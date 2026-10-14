"""把「已安装的技能」接进聊天工具链（Agent A 的 ReAct 工具 + 确定性快路）。

为什么需要
----------
`/api/capabilities` 只扫 `<repo>/skills` 的平台自带能力，聊天 agent 原先也只挂了
`ask_textbook` 一个工具 —— 老师审核发布安装的技能在对话里**完全用不上**。
本模块把它接上，两条路：

1. **通用工具**：已安装 ∧ 可加载 的技能 → 按「技能 × 函数」生成 LangChain 工具，
   参数与说明取自 `manifest.inputs_spec` 与插件 AST（`kernel_loader.describe_skill`），
   并给系统提示追加「可用技能」段，让模型知道什么时候该调。

2. **确定性快路**（`match_intent`）：技能可在 SKILL.md 的 front-matter 里声明
   `intents:`（触发词）。命中就**直接调技能**，不经过模型 ——
   因为本机聊天模型是 `qwen2.5:3b`，小模型的 function-calling 不可靠，
   "说一句话就出图"不能赌在它身上。快路命中时同样如实标注是哪个技能出的结果。

安全
----
真正调用走的是与 `/api/skill-market/kernel/invoke` **同一条路**
（`isolation.assert_loadable` → `kernel_loader.build_kernel` → `invoke`），不绕过任何闸门；
草稿 / 待审 / 停用 / 批准失效的技能**不会**出现在工具列表里（判据同 `store.is_loadable`）。
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

#: 会话 id：聊天工具用的固定会话（单用户模式会自动加载全部已安装技能）
CHAT_SESSION = "chat"

_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7,
           "八": 8, "九": 9, "十": 10}


def _sanitize(name: str) -> str:
    """工具名只允许 [0-9A-Za-z_]（函数调用协议的要求），其余字符换下划线。"""
    return re.sub(r"[^0-9A-Za-z_]", "_", str(name or "")).strip("_")[:40] or "skill"


def _cn_to_int(text: str) -> int | None:
    """中文数字 → 整数（支持 一~二十；'十一'/'二十' 这类）。"""
    t = str(text or "").strip()
    if not t:
        return None
    if t.isdigit():
        return int(t)
    if t.startswith("十") and len(t) == 1:
        return 10
    if t.startswith("十"):                       # 十一 ~ 十九
        return 10 + (_CN_NUM.get(t[1], 0) or 0)
    if len(t) == 2 and t[0] == "二" and t[1] == "十":
        return 20
    if len(t) >= 2 and t[1] == "十":             # 二十 ~ 九十九
        return (_CN_NUM.get(t[0], 0) or 0) * 10 + (_CN_NUM.get(t[2], 0) if len(t) > 2 else 0)
    if len(t) == 1:
        return _CN_NUM.get(t)
    return None


def _intents_of(man: Any) -> list[str]:
    """技能声明的触发词（SKILL.md front-matter 的 `intents:`）。

    manifest 没有 `intents` 字段，但**未知字段会原样进 `extra`**（前向兼容机制），
    所以技能作者直接在 SKILL.md 里写 `intents: 思维导图, 导图` 就能生效，
    不需要改 manifest 模型；值支持 逗号/顿号/空格分隔、`[...]` 与引号包裹。
    """
    raw = getattr(man, "intents", None)
    if raw is None:
        for k, v in (getattr(man, "extra", None) or ()):
            if str(k).strip().lower() in ("intents", "intent", "触发词"):
                raw = v
                break
    if raw is None:
        return []
    if isinstance(raw, str):
        cleaned = raw.strip().strip("[]").replace('"', "").replace("'", "")
        return [x.strip() for x in re.split(r"[,，、\s]+", cleaned) if x.strip()]
    if isinstance(raw, (list, tuple)):
        return [str(x).strip().strip('"\'') for x in raw if str(x).strip()]
    return []


def loadable_skills() -> list[dict]:
    """已安装 ∧ 可加载 的技能 → 给 LLM / 快路用的结构化清单。"""
    from edu_agent.skill_market import kernel_loader, registry_db, store

    out: list[dict] = []
    for row in registry_db.list_skills():
        sid = str(row.get("skill_id") or "")
        if not sid or not row.get("installed"):
            continue
        ok, _why = store.is_loadable(sid)
        if not ok:
            continue
        found = store.find_skill(sid)
        man = found.get("manifest") if found else None
        if man is None:
            continue
        spec: dict[str, dict] = {}
        for s in (getattr(man, "inputs_spec", None) or []):
            if isinstance(s, dict) and s.get("name"):
                spec[str(s["name"])] = s
        try:
            funcs = kernel_loader.describe_skill(sid).get("functions", []) or []
        except Exception:                                     # noqa: BLE001
            funcs = []
        fns: list[dict] = []
        for f in funcs:
            name = str(f.get("function") or "")
            if not name:
                continue
            params = []
            for p in (f.get("params") or []):
                meta = spec.get(str(p), {})
                ptype = str(meta.get("type") or "").strip().lower()
                if not ptype:
                    # 技能没写 inputs_spec（或写了块状 YAML 而解析器只认行内形式）时兜底：
                    # 名字像计数的按整数处理，免得表单把章节号当字符串、也让模型少犯错。
                    ptype = "integer" if str(p).lower().endswith(
                        ("_no", "_num", "_count", "_index")) else "string"
                params.append({
                    "name": str(p),
                    "type": ptype,
                    "description": str(meta.get("description") or ""),
                })
            fns.append({
                "name": name,
                "description": str(f.get("description") or ""),
                "params": params,
                "tool_name": "%s__%s" % (_sanitize(sid), _sanitize(name)),
            })
        if not fns:
            continue                                          # 纯提示词技能：没有可调函数
        out.append({
            "skill": sid,
            "title": str(getattr(man, "title", "") or sid),
            "description": str(getattr(man, "description", "") or ""),
            "capabilities": list(getattr(man, "allowed_capabilities", None) or []),
            "functions": fns,
            "intents": _intents_of(man),
        })
    return out


def invoke(skill: str, function: str, args: dict | None = None,
           session_id: str = CHAT_SESSION) -> dict:
    """调用技能函数（与 /api/skill-market/kernel/invoke 同一条路，不绕闸门）。"""
    from edu_agent.skill_market import isolation, kernel_loader, session_bootstrap

    args = {k: v for k, v in (args or {}).items() if v is not None and v != ""}
    ok, why = isolation.assert_loadable(session_id, skill)
    if not ok:
        # 首次进聊天时该会话可能还没绑定过 → 按当前模式补绑一次（幂等）
        session_bootstrap.ensure_session(session_id, force=True)
        ok, why = isolation.assert_loadable(session_id, skill)
    if not ok:
        return {"ok": False, "gate": "isolation", "skill": skill, "error": why}
    built = kernel_loader.build_kernel(session_id, skills=[skill])
    if not built.get("ok"):
        return built
    return kernel_loader.invoke(built["kernel"], skill, str(function or ""), **args)


def result_text(result: Any) -> str:
    """把内核返回转成给模型/用户看的文本（markdown 原样给出，其它转 JSON）。"""
    if not isinstance(result, dict):
        return str(result)
    if result.get("ok") is False:
        return "技能调用失败：" + str(result.get("error") or result.get("gate") or "未知原因")
    value = result.get("value")
    value = getattr(value, "value", value)          # Semantic Kernel 的包装
    if isinstance(value, dict):
        if isinstance(value.get("markdown"), str):
            return value["markdown"]
        return json.dumps(value, ensure_ascii=False)[:4000]
    return str(value)


# ---------------------------------------------------------------- 通用工具（给能调工具的模型）
def build_tools(session_id: str = CHAT_SESSION) -> list:
    """按「技能 × 函数」生成 LangChain 工具；参数全部可选（缺省由技能自己兜底）。"""
    from langchain_core.tools import StructuredTool
    from pydantic import Field, create_model

    tools: list = []
    for sk in loadable_skills():
        for fn in sk["functions"]:
            fields: dict[str, Any] = {}
            for p in fn["params"]:
                t = str(p["type"]).lower()
                ann = int if t in ("integer", "int") else (
                    float if t in ("number", "float") else str)
                fields[p["name"]] = (Optional[ann],
                                     Field(None, description=p["description"] or p["name"]))
            if not fields:
                fields["note"] = (Optional[str], Field(None, description="该函数无需参数"))
            model = create_model(_sanitize(fn["tool_name"]) + "_args", **fields)

            def _make(skill_id: str, fname: str):
                def _run(**kw):
                    args = {k: v for k, v in kw.items() if v is not None and k != "note"}
                    return result_text(invoke(skill_id, fname, args, session_id))
                return _run

            desc = "%s%s" % (sk["title"], ("：" + sk["description"]) if sk["description"] else "")
            if fn["description"] and fn["description"] not in desc:
                desc += "（" + fn["description"] + "）"
            if fn["params"]:
                desc += "　参数：" + "、".join(
                    "%s(%s%s)" % (p["name"], p["type"],
                                  "，" + p["description"] if p["description"] else "")
                    for p in fn["params"])
            tools.append(StructuredTool.from_function(
                func=_make(sk["skill"], fn["name"]),
                name=fn["tool_name"], description=desc, args_schema=model))
    return tools


def prompt_block(skills: list[dict] | None = None) -> str:
    """追加到系统提示的「可用技能」说明。"""
    skills = loadable_skills() if skills is None else skills
    if not skills:
        return ""
    lines = ["", "可用技能（老师审核通过并已安装，需要时直接调用对应工具）："]
    for sk in skills:
        lines.append("- %s（%s）：%s" % (sk["skill"], sk["title"], sk["description"]))
        for fn in sk["functions"]:
            args = "、".join("%s: %s%s" % (p["name"], p["type"],
                                         "（" + p["description"] + "）" if p["description"] else "")
                            for p in fn["params"])
            lines.append("    工具 `%s`　参数：%s" % (fn["tool_name"], args or "无"))
    lines += [
        "",
        "补充规则：",
        "5. 用户要求**生成类**产出（思维导图、导图、梳理章节结构等）时，调用对应技能工具，"
        "不要自己编造章节结构；教材知识问答仍走 ask_textbook。",
        "6. 参数不全时按上下文补全（例如只说“第二章”就传 chapter_no=2；book_id 可省略，"
        "教材库会取默认课本）；实在补不了就问用户一句。",
        "7. 技能返回的 markdown 要**完整呈现**给用户，不要改写章节名与页码。",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------- 确定性快路（不依赖模型）
def extract_args(text: str, params: list[dict]) -> dict:
    """从用户话里抽取技能参数（目前支持章节号）。"""
    args: dict[str, Any] = {}
    t = str(text or "")
    for p in params:
        name = str(p["name"])
        if name in ("chapter_no", "chapter", "chapter_num"):
            m = re.search(r"第\s*([0-9]{1,3}|[一二两三四五六七八九十]{1,3})\s*[章节讲课]", t)
            if m:
                n = _cn_to_int(m.group(1))
                if n:
                    args[name] = n
            elif re.search(r"(导图|思维导图)", t):
                m2 = re.search(r"([0-9]{1,3})\s*[章节]", t)
                if m2:
                    args[name] = int(m2.group(1))
    return args


def chapter_guard(args: dict) -> str:
    """章节号越界检查 —— 越界就**别调技能**，返回一句人能看懂的话（空串 = 没问题）。

    为什么必须拦：教材库对"不存在的章节"会**静默回退到第一章**
    （实测：说"第十二章"，拿回来的是第一章的导图，看着像成功、其实是错的）。
    这种"看着对、其实错"的结果比报错更危险 —— 宁可不调，直接告诉用户这册有哪几章。
    """
    n = args.get("chapter_no")
    if not isinstance(n, int):
        return ""
    try:
        from edu_agent import textbook_lib

        data = textbook_lib.chapters() or {}
        items = data.get("chapters") if isinstance(data, dict) else data
    except Exception:                                        # noqa: BLE001  查不到就不拦
        return ""
    total = len(items or [])
    if not total or 1 <= n <= total:
        return ""
    # 列表按**真实章号**排（`chapters()` 是按文件名排的，直接列会得到
    # "1.第一章 / 2.第三章 / 3.第二章" 这种让人看不懂的顺序）
    rows: list[tuple[int, str]] = []
    for idx, c in enumerate((items or []), 1):
        if not isinstance(c, dict):
            rows.append((idx, str(c)))
            continue
        label = "%s %s" % (str(c.get("chapter") or "").strip(),
                           str(c.get("title") or "").strip())
        no = idx
        try:
            from edu_agent import textbook_lib as _tl

            no = int(_tl.chapter_no_of(str(c.get("chapter") or ""), idx) or idx)
        except Exception:                                    # noqa: BLE001
            no = idx
        rows.append((no, label.strip()))
    rows.sort(key=lambda r: r[0])
    listing = "\n".join("第 %d 章　%s" % (no, lab) for no, lab in rows[:15])
    return ("这册教材只有 %d 章，没有第 %d 章 —— 我没有调用技能"
            "（否则会静默给你第一章的导图）。\n\n可选章节：\n%s\n\n告诉我第几章，我再做。"
            % (total, n, listing))


def match_intent(text: str, session_id: str = CHAT_SESSION) -> dict | None:
    """命中技能声明的触发词 → 返回 {skill, function, args, result}；否则 None。

    两条命中口径（都要求**参数能从话里抽到**，抽不到就不猜、交给模型问用户）：
      ① 技能在 SKILL.md 里声明的 `intents:` 触发词命中；
      ② 退一步的通用规则：话里同时出现**章节引用**（第N章/第十二章）与**生成类动词**
         （梳理/画/做/生成/整理/结构/导图）—— 这样"第3章梳理一下"这类说法也能命中，
         而不必要求技能作者把每种说法都列进 intents。

    只对**已安装且可加载**、且有可调用函数的技能生效。
    """
    t = str(text or "")
    if not t:
        return None
    chapter_ref = re.search(r"第\s*([0-9]{1,3}|[一二两三四五六七八九十]{1,3})\s*章", t)
    verbs = ("梳理", "画", "做", "生成", "整理", "结构", "导图")
    verb_hit = any(v in t for v in verbs)
    for sk in loadable_skills():
        words = [w for w in sk.get("intents") or [] if w]
        by_word = bool(words) and any(w in t for w in words)
        by_rule = bool(chapter_ref) and verb_hit
        if not (by_word or by_rule):
            continue
        fn = sk["functions"][0]
        params = fn["params"]
        args = extract_args(t, params)
        missing = [p["name"] for p in params
                   if p["name"] not in args and p["name"] not in ("book_id", "note")]
        if missing:
            continue                     # 缺关键参数（如章节号）就不猜
        warn = chapter_guard(args)
        if warn:
            # 章节号越界：**不调技能**，直接把"这册只有 N 章"讲清楚
            return {"skill": sk["skill"], "title": sk["title"], "function": fn["name"],
                    "args": args, "result": None, "text": warn, "blocked": "chapter_range"}
        res = invoke(sk["skill"], fn["name"], args, session_id)
        return {"skill": sk["skill"], "title": sk["title"], "function": fn["name"],
                "args": args, "result": res, "text": result_text(res),
                "matched_by": "intent_word" if by_word else "chapter_plus_verb"}
    return None
