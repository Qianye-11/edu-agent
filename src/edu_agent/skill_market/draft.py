# -*- coding: utf-8 -*-
"""SKILL-02 · 自动封装草稿：**选中成功会话 → 抽取 → 生成可编辑的 SKILL.md 草稿**。

明确边界（写死在代码里，也写进文档）：

* 产出物**只有** `SKILL.md` + 匿名 `fixture.json`。
* **不生成任何可运行的 .py**。`store.write_draft()` 会拒绝 `.py/.cmd/.ps1/...` 后缀
  （`DRAFT_BANNED_SUFFIX`），所以"草稿被直接加载执行"这件事在磁盘层就不可能发生。
* 插件代码（Semantic Kernel 的 `@kernel_function` 类）由**人工**依据
  `skill_market/templates/plugin_template.py` 编写，提交审核时随技能一起提交。

抽取逻辑是**纯函数**（`extract_from_session`），不碰 IO，便于单测；落盘部分在
`create_draft_from_session()`。会话读取通过 `session_reader` 注入，测试里给个假会话即可。
"""
from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

from edu_agent.skill_market import capability_registry as caps
from edu_agent.skill_market import manifest as mf
from edu_agent.skill_market import perm_scan, store
from edu_agent.skill_market.paths import valid_id

# 会话"成功"的判据：助手答复非空、没有失败标记、且带结构化产出
FAIL_MARKERS = re.compile(
    r"(失败|报错|错误[:：]|异常|Traceback|无法完成|不能完成|抱歉[，,]?\s*我(无法|不能)|"
    r"Error:|Exception|超时|请稍后重试)", re.I)
SUCCESS_MARKERS = re.compile(
    r"(已完成|生成完成|如下|步骤|解析|答案|题目|草稿|示例|示例输出|##|\d+\.\s)", re.I)

# 步骤抽取：优先有序列表，其次标题，其次"首先/然后/接着/最后"
_STEP_NUM_RE = re.compile(r"^\s*(?:\d+[.、)]|[-*•])\s+(.{4,200})$", re.M)
_STEP_HEAD_RE = re.compile(r"^#{2,4}\s+(.{2,60})$", re.M)
_STEP_FLOW_RE = re.compile(r"(首先|然后|接着|再|最后|下一步)[，,]?\s*([^。；\n]{4,120})")

# 输入抽取：常见教学标识与槽位
_INPUT_HINTS: tuple[tuple[str, str], ...] = (
    ("knowledge_id", r"(knowledge_id|知识点\s*id|节点\s*id|图谱节点)"),
    ("teaching_context", r"(teaching_context|学段|年级|学科|章节|课时)"),
    ("mastery_snapshot_id", r"(mastery_snapshot_id|学情|掌握度|薄弱)"),
    ("difficulty", r"(难度|基础|提高|拔高|易|中|难)"),
    ("question_count", r"(几道|道题|题量|数量)"),
    ("question_type", r"(选择|填空|解答|判断|简答|计算)题?"),
)
_KNOWLEDGE_REF_RE = re.compile(r"\b(?:knowledge_id\s*[=:]\s*)?([A-Z]{1,3}-?\d{1,6}|node[-_]\w{2,32}|kp[-_]\w{2,32})\b")

# 匿名化
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")
# 身份证：带合法生日的精确口径。宽口径（18 位数字）会把 sha256 摘要误当身份证擦掉（实测踩过）。
_IDCARD_RE = re.compile(
    r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)")
# 姓名/学号：**必须带冒号**才算字段。早先写成冒号可选，结果把"给高中学生生成一道题"
# 里的"学生生成"当成姓名擦掉了（实测踩过），所以这里强制要求分隔符。
_NAME_RE = re.compile(r"(?:学生姓名|学生名|姓名|学生)\s*[:：]\s*([\u4e00-\u9fa5]{2,4})")
_STU_ID_RE = re.compile(r"(?:学号|考号)\s*[:：]\s*([A-Za-z0-9]{4,20})")


def anonymize(text: str) -> str:
    """匿名化：手机/邮箱/身份证/学生姓名/学号 → 占位符。"""
    s = str(text or "")
    s = _PHONE_RE.sub("1**********", s)
    s = _EMAIL_RE.sub("***@***", s)
    s = _IDCARD_RE.sub("******************", s)
    s = _NAME_RE.sub("学生姓名:学生A", s)
    s = _STU_ID_RE.sub("学号:S000", s)
    return s


def _pair_messages(messages: list[dict]) -> list[tuple[str, str]]:
    """把会话消息折成 (用户, 助手) 对。"""
    pairs: list[tuple[str, str]] = []
    last_user = ""
    for m in messages or []:
        role = str(m.get("role") or "")
        text = str(m.get("content") or "")
        if role == "user":
            last_user = text
        elif role == "assistant" and last_user:
            pairs.append((last_user, text))
            last_user = ""
    return pairs


def is_successful(user_text: str, assistant_text: str) -> tuple[bool, str]:
    """判定这一轮是否"成功"（SKILL-02 只从成功会话抽取）。"""
    a = str(assistant_text or "").strip()
    if len(a) < 40:
        return False, "助手答复过短（<40 字），不像一次可复用的成功产出"
    if FAIL_MARKERS.search(a[:400]):
        return False, "助手答复里出现失败/报错标记"
    if not SUCCESS_MARKERS.search(a):
        return False, "助手答复没有可识别的结构化产出（步骤/解析/题目/示例）"
    if not str(user_text or "").strip():
        return False, "缺少对应的用户提问，无法定位触发场景"
    return True, "ok"


def _steps_from(text: str) -> list[str]:
    """从助手答复里抽步骤（有序列表 → 标题 → 连接词），最多 12 条。"""
    steps = [" ".join(m.group(1).split()) for m in _STEP_NUM_RE.finditer(text or "")]
    if len(steps) < 2:
        heads = [" ".join(m.group(1).split()) for m in _STEP_HEAD_RE.finditer(text or "")]
        if len(heads) >= 2:
            steps = heads
    if len(steps) < 2:
        steps = [" ".join((" ".join(g).split())) for g in _STEP_FLOW_RE.findall(text or "")]
    seen: list[str] = []
    for s in steps:
        s = s.strip(" -—:：")
        if s and s not in seen and len(s) >= 3:
            seen.append(s[:160])
    return seen[:12]


def _inputs_from(user_text: str, assistant_text: str, body_hint: str = "") -> list[str]:
    blob = "\n".join([str(user_text or ""), str(assistant_text or ""), str(body_hint or "")])
    out: list[str] = []
    for token, rx in _INPUT_HINTS:
        if re.search(rx, blob, re.I):
            out.append(token)
    if not out:
        out = ["teaching_context"]
    return out


def _knowledge_refs(text: str, limit: int = 5) -> list[str]:
    out: list[str] = []
    for m in _KNOWLEDGE_REF_RE.finditer(str(text or "")):
        v = m.group(1)
        if v not in out and not v.isdigit():
            out.append(v)
        if len(out) >= limit:
            break
    return out


def _slug(text: str, fallback: str = "skill") -> str:
    """技能 id：ASCII 走 slug，含中文走哈希（与 skills_local._slug 同思路）。"""
    raw = " ".join(str(text or "").split())
    if raw and re.fullmatch(r"[A-Za-z0-9 _.-]+", raw):
        s = re.sub(r"[^a-zA-Z0-9]+", "-", raw).strip("-").lower()[:48]
        if valid_id(s):
            return s
    seed = raw or fallback
    return "skill-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:10]


def extract_from_session(session: dict, *, name_hint: str = "",
                         owner: str = "E") -> dict:
    """**纯函数**：会话 → 草稿素材。不碰磁盘，方便单测与复算。"""
    messages = list((session or {}).get("messages") or [])
    pairs = _pair_messages(messages)
    if not pairs:
        return {"ok": False, "error": "该会话没有可用的「用户提问 + 助手答复」对"}

    picked: tuple[str, str] | None = None
    reasons: list[str] = []
    for user_text, assistant_text in reversed(pairs):
        good, why = is_successful(user_text, assistant_text)
        if good:
            picked = (user_text, assistant_text)
            break
        reasons.append(why)
    if picked is None:
        return {"ok": False,
                "error": "该会话里没有找到成功产出（最近一轮：%s）" % (reasons[0] if reasons else "无"),
                "checked": len(pairs)}

    user_text, assistant_text = picked
    title = " ".join(str(name_hint or "").split()) or " ".join(user_text.split())[:24] or "会话技能草稿"
    steps = _steps_from(assistant_text)
    inputs = _inputs_from(user_text, assistant_text)
    refs = _knowledge_refs(user_text + "\n" + assistant_text)
    stage = "高中" if "高中" in (user_text + assistant_text) else ("初中" if "初中" in (user_text + assistant_text) else "")

    warnings: list[str] = []
    if len(steps) < 2:
        warnings.append("没抽到清晰步骤（<2 条），正文里的步骤需要人工补写")
    if not refs:
        warnings.append("没识别到 knowledge_id 之类的标识，inputs/fixture 需要人工补")
    if not stage:
        warnings.append("没识别到学段，teaching_context 需要人工确认")

    fixture = {
        "skill": _slug(title, fallback=user_text[:32]),
        "source": {"session_id": str((session or {}).get("id") or ""),
                   "captured_at": time.strftime("%Y-%m-%d %H:%M")},
        "note": "匿名 fixture：只保留标识与结构化槽位，不含学生姓名/学号/作答明文",
        "input": {
            "teaching_context": {"stage": stage or "（待人工确认）",
                                 "subject": "数学",
                                 "chapter": "（待人工确认）"},
            "knowledge_id": refs[0] if refs else "（待人工确认）",
            "trigger_text": anonymize(" ".join(user_text.split())[:200]),
        },
        "expected_output_shape": {
            "kind": "question_draft",
            "fields": ["stem", "answer", "analysis", "knowledge_id", "difficulty"],
        },
        "expected_digest": "sha256:" + hashlib.sha256(
            anonymize(assistant_text).encode("utf-8")).hexdigest()[:12],
        "assertions": [
            "输出必须含 knowledge_id 且与输入一致",
            "输出必须含答案与解析（非空）",
            "不得包含学生姓名/学号/作答明文",
        ],
    }

    return {
        "ok": True,
        "skill_id": _slug(title, fallback=user_text[:32]),
        "title": title,
        "trigger": " ".join(user_text.split())[:300],
        "steps": steps,
        "inputs": inputs,
        "outputs": ["question_draft", "answer_outline", "knowledge_id"],
        "knowledge_refs": refs,
        "stage": stage,
        "fixture": fixture,
        "warnings": warnings,
        "owner": owner,
        "session_id": str((session or {}).get("id") or ""),
        "source_chars": len(assistant_text),
        "output_sample": anonymize(assistant_text.strip())[:1200],
    }


def build_body(ex: dict, *, allowed: tuple[str, ...], data_scope: tuple[str, ...]) -> str:
    """把抽取结果拼成 SKILL.md 正文。

    **结构固定为 doc 34 §5.3 规定的最小结构**（七段，顺序不变）：

        ## 适用场景  ## 必要输入  ## 工作流程  ## 输出格式
        ## 教师确认点  ## 质量与隐私约束  ## 版本来源

    为什么固定：教师不需要手写 Markdown（doc §8.3 完成定义第一条），
    审查页也才能按固定段位做 diff；`skill_studio.validate_draft()` 会校验这七段是否齐。
    """
    steps = ex.get("steps") or []
    step_md = "\n".join("%d. %s" % (i + 1, s) for i, s in enumerate(steps)) or \
        "1. （未抽取到步骤，请人工补写：从触发到产出的完整流程）"
    warn = ("\n".join("- " + w for w in ex.get("warnings") or [])) or "- （无）"
    inputs = "\n".join("- `%s`" % t for t in (ex.get("inputs") or [])) or "- （待人工确认）"
    outputs = "\n".join("- `%s`" % t for t in (ex.get("outputs") or [])) or "- （待人工确认）"
    caps_md = "\n".join("- `%s` — %s" % (t, caps.resolve_capability(t)[1])
                        for t in allowed) or "- （待人工确认）"
    return f"""# {ex['title']}

> **模块 3（SKILL-02）自动生成的草稿，尚未审核，Agent 不会加载它。**
> 状态 `draft` · 负责人 `{ex.get('owner') or 'E'}` · 生成于 {time.strftime('%Y-%m-%d %H:%M')}

## 适用场景

用户说：{ex.get('trigger') or '（未记录）'}

一次性任务不要写进这里 —— 只沉淀**反复出现且已证明有效**的流程。

## 必要输入

{inputs}

只接受**标识**，不接受学生作答明文：`{', '.join(data_scope) or 'knowledge_id, teaching_context'}`。

## 工作流程

{step_md}

## 输出格式

{outputs}

**只产草稿**：不写题库、不改学情，入库与审核由模块 2 负责。

## 教师确认点

提交审核前必须逐条完成：

- [ ] 校对上文步骤与输入，删掉会话里偶发的、不可复用的部分
- [ ] 确认 `allowed_capabilities`：多了要去掉（最小权限），少了审核会拒
- [ ] 依据 `skill_market/templates/plugin_template.py` 人工编写插件
      （Semantic Kernel `@kernel_function`），并在 front-matter 里填 `plugin: <文件名>.py`
- [ ] 补用例（`tests:` 字段要指向真实存在的文件或写 inline tests 块）
- [ ] 用匿名 fixture 跑一遍：`POST /api/skill-market/fixture/run`
- [ ] 跑静态权限扫描：`POST /api/skill-market/scan`，确认 `verdict=pass`
- [ ] 然后才提交审核：`POST /api/skill-market/submit`（内部强制执行 L1+L2+L3）

## 质量与隐私约束

抽取质量提示：

{warn}

依赖能力（高风险动作经 `permissions.py` 闸门，未声明即审核不通过）：

{caps_md}

边界：

- 不接触学情/题库内部库表，只调用对外 API（传 `knowledge_id` / `teaching_context` / `mastery_snapshot_id`）。
- 不保存学生隐私明文；审计日志全程脱敏。
- 不把未经核验的教材原文、课程标准、真题或推断写为事实。
- 草稿区**不允许放可执行文件**：插件代码由人工编写并显式声明。

## 版本来源

- 来源：`conversation_candidate`（Agent 从对话候选提炼）
- session_id：`{ex.get('session_id') or '（未记录）'}`
- 原始产出摘要（已脱敏）：`{ex.get('fixture', {}).get('expected_digest', '')}`
- 原文字数：{ex.get('source_chars', 0)}
"""


#: doc 34 §5.3 规定的最小结构（七段，顺序固定）
REQUIRED_SECTIONS: tuple[str, ...] = (
    "适用场景", "必要输入", "工作流程", "输出格式",
    "教师确认点", "质量与隐私约束", "版本来源",
)

#: 视为「Agent 自动生成」的 source 取值（结构校验只对这类草稿强制）
GENERATED_SOURCES: frozenset[str] = frozenset({
    "conversation_candidate", "skill-create", "session",
})


def missing_sections(body: str) -> list[str]:
    """检查正文缺哪些必需段（返回缺失的段名，空列表 = 结构合规）。"""
    text = str(body or "")
    return [s for s in REQUIRED_SECTIONS if ("## " + s) not in text]


def create_draft_from_session(session_id: str, *, name_hint: str = "", owner: str = "E",
                              description: str = "",
                              allowed_capabilities: tuple[str, ...] = ("kg.read", "question.draft"),
                              data_scope: tuple[str, ...] = ("knowledge_id", "teaching_context"),
                              session_reader=None, write: bool = True) -> dict:
    """SKILL-02 主入口：会话 → SKILL.md 草稿（+ 匿名 fixture）+ 权限扫描结论。"""
    reader = session_reader or default_session_reader
    session = reader(session_id)
    if not session:
        return {"ok": False, "error": "找不到会话：%s" % session_id}
    ex = extract_from_session(session, name_hint=name_hint, owner=owner)
    if not ex.get("ok"):
        return {"ok": False, "error": ex.get("error"), "detail": ex}

    skill_id = ex["skill_id"]
    man = mf.SkillManifest(
        name=skill_id,
        title=ex["title"],
        description=description or ("从成功会话封装：%s" % ex["trigger"][:80]),
        version="0.1.0",
        status="draft",
        owner=owner,
        category="教学",
        icon="G",
        entry="",                      # 人工填（发布后平台据此调用）
        plugin="",                     # 人工填（@kernel_function 插件文件）
        inputs=tuple(ex["inputs"]),
        outputs=tuple(ex["outputs"]),
        allowed_capabilities=tuple(allowed_capabilities),
        data_scope=tuple(data_scope),
        tests=("fixture.json",),       # 验收要求：至少要有匿名 fixture 用例
        source="session",
        session_id=session_id,
        fixture="fixture.json",
        tags=("session-draft", "待人工编辑"),
    )
    body = build_body(ex, allowed=man.allowed_capabilities, data_scope=man.data_scope)
    fixture_txt = __import__("json").dumps(ex["fixture"], ensure_ascii=False, indent=2)

    out = {"ok": True, "id": skill_id, "manifest": man, "body": body,
           "fixture": ex["fixture"], "warnings": ex["warnings"],
           "next_steps": [
               "人工校对步骤与 allowed_capabilities",
               "依据 templates/plugin_template.py 编写 plugin.py（不自动生成）",
               "补 tests 文件并跑 fixture",
               "跑 /api/skill-market/scan 拿到 pass",
               "提交审核 /api/skill-market/submit",
           ]}
    if not write:
        rep = perm_scan.scan(man, body, fixture_text=fixture_txt)
        out["scan"] = rep.to_dict()
        out["dir"] = ""
        return out

    d = store.write_draft(man, body, files={"fixture.json": fixture_txt})
    store.write_fixture(skill_id, ex["fixture"])
    rep = perm_scan.scan(man, body, fixture_text=fixture_txt,
                         fixture_path=Path(d) / "fixture.json", skill_dir=d)
    out["dir"] = str(d)
    out["scan"] = rep.to_dict()
    return out


def default_session_reader(session_id: str) -> dict | None:
    """默认会话读取：直接读平台的会话库（只读，复用 host.store，不重写存储）。"""
    from edu_agent.config import load_settings
    from edu_agent.host.store import SessionStore

    st = SessionStore(path=load_settings().sessions_db)
    try:
        return st.get(session_id)
    finally:
        st.close()


def list_success_sessions(limit: int = 10, *, session_reader=None) -> list[dict]:
    """给"从会话生成草稿"的界面用：列出最近的成功会话候选（数据面，UI 由他人做）。"""
    from edu_agent.config import load_settings
    from edu_agent.host.store import SessionStore

    st = SessionStore(path=load_settings().sessions_db)
    try:
        rows = st.list_sessions()
        out: list[dict] = []
        for row in reversed(rows):
            sess = st.get(row["id"])
            if not sess:
                continue
            pairs = _pair_messages(sess.get("messages") or [])
            if not pairs:
                continue
            u, a = pairs[-1]
            good, why = is_successful(u, a)
            out.append({"session_id": row["id"], "title": sess.get("title"),
                        "turns": len(pairs), "last_ok": good, "why": why,
                        "trigger": " ".join(u.split())[:80]})
            if len(out) >= limit:
                break
        return out
    finally:
        st.close()
