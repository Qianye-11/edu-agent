# -*- coding: utf-8 -*-
"""skill-create · M1 运行痕迹（run trace）。

**为什么先做它**：没有痕迹，"跑通"就只能靠嘴说。痕迹是可复核的 ——
谁、什么时候、拿什么输入、调了哪些能力、产出什么形状、有没有被人工纠正。

三条硬规矩（与审计同一套）：

1. **只追加**：`runs/<yyyy-mm>.jsonl`，复用 `audit.chain_append()` 的哈希链，无删改接口；
2. **脱敏**：复用 `audit.redact()`；学生姓名/学号/作答只留摘要与长度；
3. **失败静默**：埋点异常绝不影响回答（调用方只需要 `try/except` 包一下，本模块内部也全包）。

埋点三处（都在 web_server 侧接线，本模块只提供函数）：

    runs.begin_turn(session_id)                     # 一轮开始（清空本轮的步骤缓冲）
    runs.record_skill_call("ollama-qa", ok=True)    # 技能调用时（可多次）
    runs.record_turn(session_id, turn, user, reply) # 一轮结束（写一行痕迹）

人工纠正检测：下一轮用户消息里出现「不对 / 不是 / 重来 / 你搞错了」这类否定时，
给**上一轮**补写一条 `run.correction` 记录（也是追加，不改历史）——R6 判据靠它。
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any, Iterator

from edu_agent.skill_market import audit, paths

# ---------------------------------------------------------------- 配置
_DEFAULTS = {
    "trace_enabled": True,       # 是否埋点（关掉就完全不写）
    "min_sessions": 2,           # R1：至少几个不同会话
    "min_runs": 2,               # 至少几次成功运行
    "window_days": 30,           # 只看最近多少天
    "consistency_min": 1.0,      # R3：产出形状一致率下限
    "score_min": 0.6,            # 候选入板的最低分
    "reject_cooldown_days": 30,  # 否决后的冷却期
    "dedupe_threshold": 0.6,     # 与已有技能的相似度阈值（超过就抑制）
    # ---- 第二轮 ----
    "require_shadow": True,      # 草稿声明了 plugin.py 就必须过 L3 影子运行
    "scan_interval_hours": 24,   # 自动挖掘间隔（0 = 关掉定时扫描）
    "prompt_in_chat": True,      # 跑通的流程是否在对话里提示一句
    "health_min_calls": 5,       # L4：少于这个调用数不下结论
    "health_min_ok_rate": 0.8,   # L4：成功率低于它就标 attention
    "health_window_days": 30,    # L4：只看最近多少天
}

#: 阈值可由环境变量覆盖（doc 34 §7.1 的 `.env` 配置项）；**显式 set_config 过的值优先于环境变量**
_ENV_KEYS = {
    "min_sessions": "SKILL_CANDIDATE_MIN_SESSIONS",
    "window_days": "SKILL_CANDIDATE_WINDOW_DAYS",
    "reject_cooldown_days": "SKILL_CANDIDATE_COOLDOWN_DAYS",
}

_lock = threading.RLock()
_steps = threading.local()
_last_turn: dict[str, dict] = {}          # session_id -> 上一轮信息（用于纠正检测）


def config() -> dict:
    """阈值配置（`<market>/skill_create.json`，缺失或损坏就回默认）。

    优先级：**显式 `set_config()` 写进文件的值 > 环境变量（`.env`）> 内置默认**。
    环境变量只在文件里没写过该键时生效 —— 这样"部署时用 .env 定阈值"和
    "运行/测试时显式改阈值"不会互相打架。
    """
    import json
    import os

    p = paths.config_file()
    cfg = dict(_DEFAULTS)
    stored: dict = {}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                stored = {k: v for k, v in data.items() if k in _DEFAULTS}
                cfg.update(stored)
        except ValueError:
            pass
    for key, env in _ENV_KEYS.items():
        if key in stored:
            continue                      # 显式设置过 → 环境变量不覆盖
        raw = (os.getenv(env) or "").strip()
        if raw:
            try:
                cfg[key] = int(raw)
            except ValueError:
                pass
    return cfg


def set_config(**changes) -> dict:
    """改阈值（只允许已知键）。"""
    import json

    cfg = config()
    cfg.update({k: v for k, v in changes.items() if k in _DEFAULTS})
    paths.ensure_dirs()
    p = paths.config_file()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    return cfg


# ---------------------------------------------------------------- 抽取辅助
_FAIL_RE = re.compile(r"(失败|报错|错误[:：]|异常|Traceback|无法完成|不能完成|"
                      r"抱歉[，,]?\s*我(无法|不能)|Error:|Exception|超时)", re.I)
# 产出字段标签 → 规范名（让"形状"可比较）
_FIELD_MAP = {
    "题干": "stem", "题目": "stem", "答案": "answer", "解析": "analysis",
    "知识点": "knowledge_point", "考点": "knowledge_point", "难度": "difficulty",
    "学段": "stage", "学科": "subject", "题型": "question_type",
    "选项": "options", "思路": "approach", "小结": "summary",
}
# 这些标签不是产出字段（是过程/元信息/引用），别算进形状
_FIELD_NOISE = {"步骤", "输出", "输入", "注", "说明", "来源", "触发场景", "依赖能力",
                "边界", "待人工编辑", "溯源", "抽取质量提示", "已完成", "如下",
                # 引用/统计元信息：A 链答复里带的 citations 块会被正则捞进来，
                # 那是**检索元数据**不是这次流程的产出（实测踩过）
                "citations", "confidence", "coverage", "index", "page", "pages",
                "quote", "section", "heading", "chars", "digest", "ms", "model",
                "usage", "tokens", "mode", "rid", "session_id"}
_LABEL_RE = re.compile(
    r"^[\s\-*•\d.、)）]*(?:\*\*)?([\u4e00-\u9fa5A-Za-z_][\u4e00-\u9fa5A-Za-z0-9_ ]{0,11}?)"
    r"(?:\*\*)?\s*[:：]", re.M)
# "第 2 段：" 这类分段标记也不是产出字段
_SEGMENT_RE = re.compile(r"^第\s*[\d一二三四五六七八九十]+\s*[段页章节部分条]?$")
_KNOWLEDGE_RE = re.compile(r"\b([A-Z]{1,3}-?\d{1,6}|node[-_]\w{2,32}|kp[-_]\w{2,32})\b")
_STAGE_RE = re.compile(r"(初中|高中|小学|高一|高二|高三|初一|初二|初三)")
_SUBJECTS = ("数学", "语文", "英语", "物理", "化学", "生物", "历史", "地理", "政治")
_DIFF_RE = re.compile(r"(基础|提高|拔高|简单|中等|困难|易|中|难)")


def output_shape(text: str) -> list[str]:
    """从助手答复里抽"产出形状"（字段集合）。抽不到 = 这次没有结构化产出。"""
    fields: list[str] = []
    for m in _LABEL_RE.finditer(str(text or "")):
        label = " ".join(m.group(1).split())
        if not label or label in _FIELD_NOISE or _SEGMENT_RE.match(label):
            continue
        name = _FIELD_MAP.get(label, label)
        if name in _FIELD_NOISE:
            continue
        if name not in fields:
            fields.append(name)
    # 也认 JSON 风格的键（同样过噪声表）
    for m in re.finditer(r'"([a-z_]{3,24})"\s*:', str(text or "")):
        k = m.group(1)
        if k in _FIELD_NOISE or k in fields:
            continue
        fields.append(k)
    return sorted(fields)


def extract_inputs(text: str) -> dict:
    """抽具体输入标识（只留标识，不留正文）。"""
    s = str(text or "")
    out: dict[str, Any] = {}
    refs = _KNOWLEDGE_RE.findall(s)
    if refs:
        out["knowledge_id"] = refs[0]
    stage = _STAGE_RE.search(s)
    if stage:
        out["stage"] = stage.group(1)
    for sub in _SUBJECTS:
        if sub in s:
            out["subject"] = sub
            break
    diff = _DIFF_RE.search(s)
    if diff:
        out["difficulty"] = diff.group(1)
    return out


def classify_outcome(assistant_text: str, *, shape: list[str] | None = None) -> str:
    """success / failed / abandoned。"""
    a = str(assistant_text or "").strip()
    if not a:
        return "abandoned"
    if _FAIL_RE.search(a[:400]):
        return "failed"
    fields = shape if shape is not None else output_shape(a)
    return "success" if fields else "abandoned"


# ---------------------------------------------------------------- 本轮步骤缓冲
def begin_turn(session_id: str) -> None:
    """一轮开始：清空本轮的步骤缓冲。"""
    _steps.buffer = []
    _steps.session_id = str(session_id or "")


def record_skill_call(skill_id: str, *, ok: bool = True, ms: int = 0,
                      args: dict | None = None, kind: str = "skill") -> None:
    """技能/工具调用时调一次（累进本轮缓冲，等 record_turn 一起落盘）。"""
    buf = getattr(_steps, "buffer", None)
    if buf is None:
        buf = []
        _steps.buffer = buf
    buf.append({
        "kind": kind,
        "id": str(skill_id),
        "ok": bool(ok),
        "ms": int(ms or 0),
        "args_digest": audit.digest(args) if args else "",
    })


def _drain_steps() -> list[dict]:
    buf = list(getattr(_steps, "buffer", []) or [])
    _steps.buffer = []
    return buf


# ---------------------------------------------------------------- 写痕迹
def _month_file(ts: float | None = None) -> Path:
    return paths.runs_dir() / time.strftime("%Y-%m.jsonl", time.localtime(ts or time.time()))


_CORRECTION_RE = re.compile(
    r"(不对|不是这样|不是这个|错了|搞错|弄错|重来|再来一次|重新(生成|做|来)|"
    r"我说的是|你理解错|没按|不符合|改一下|重新来)")


def detect_correction(next_user_text: str) -> bool:
    """下一轮用户消息是否在纠正上一轮。"""
    return bool(_CORRECTION_RE.search(str(next_user_text or "")))


def record_correction(session_id: str, turn: int, reason: str = "") -> dict:
    """给**上一轮**补写一条纠正记录（追加，不改历史）。"""
    return audit.chain_append(_month_file(), {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "kind": "correction",
        "session_id": str(session_id),
        "turn": int(turn or 0),
        "reason": audit.redact(reason)[:200] if isinstance(audit.redact(reason), str) else "",
    })


def record_turn(session_id: str, turn: int, user_text: str, assistant_text: str, *,
                inputs: dict | None = None, steps: list[dict] | None = None,
                latency_ms: int = 0, signature: str = "", trace_enabled: bool | None = None) -> dict:
    """一轮结束：写一行运行痕迹。**任何异常都吞掉**（埋点不能影响回答）。"""
    try:
        if trace_enabled is None:
            trace_enabled = bool(config().get("trace_enabled", True))
        if not trace_enabled:
            return {}
        sid = str(session_id or "")
        shape = output_shape(assistant_text)
        outcome = classify_outcome(assistant_text, shape=shape)
        got_inputs = dict(inputs or {}) or extract_inputs(str(user_text or "") + "\n" + str(assistant_text or ""))
        got_steps = list(steps if steps is not None else _drain_steps())

        # 纠正检测：这一轮在纠正上一轮 → 给上一轮补记
        with _lock:
            prev = _last_turn.get(sid)
        if prev and detect_correction(user_text):
            try:
                record_correction(sid, int(prev.get("turn") or 0), reason=str(user_text)[:120])
            except Exception:  # noqa: BLE001
                pass

        sig = signature
        inherited = ""
        if not sig:
            try:
                from edu_agent.skill_market import miner

                # 承接轮（"再来一道"/"继续"）继承上一轮意图，否则会被算成 other 而漏出工作流
                if prev and miner.CONTINUATION_RE.search(str(user_text or "")):
                    inherited = str(prev.get("intent") or "")
                sig = miner.signature_for(user_text, got_steps, got_inputs, shape,
                                          intent=inherited)
                if not inherited:
                    inherited = miner.intent_of(str(user_text or ""))
            except Exception:  # noqa: BLE001
                sig = ""

        rec = audit.chain_append(_month_file(), {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "kind": "run",
            "session_id": sid,
            "turn": int(turn or 0),
            "intent_signature": sig,
            "trigger_text": audit.redact(" ".join(str(user_text or "").split())[:200]),
            "inputs": audit.redact(got_inputs),
            "steps": audit.redact(got_steps),
            "output": {"shape": shape,
                       "digest": audit.digest(assistant_text),
                       "chars": len(str(assistant_text or ""))},
            "outcome": outcome,
            "latency_ms": int(latency_ms or 0),
        })
        with _lock:
            _last_turn[sid] = {"turn": int(turn or 0), "seq": rec.get("seq"),
                               "outcome": outcome, "intent": inherited}
        return rec
    except Exception:  # noqa: BLE001
        return {}


# ---------------------------------------------------------------- 读痕迹
def _iter_records() -> Iterator[dict]:
    import json

    d = paths.runs_dir()
    if not d.exists():
        return
    for path in sorted(d.glob("*.jsonl")):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue


def runs(since_days: int | None = None) -> list[dict]:
    """全部运行痕迹（只读）。since_days 给定时只返回窗口内的。"""
    out: list[dict] = []
    cutoff = None
    if since_days:
        cutoff = time.time() - float(since_days) * 86400
    for rec in _iter_records():
        if rec.get("kind") != "run":
            continue
        if cutoff is not None:
            try:
                ts = time.mktime(time.strptime(str(rec.get("ts"))[:19], "%Y-%m-%dT%H:%M:%S"))
            except ValueError:
                ts = 0.0
            if ts < cutoff:
                continue
        out.append(rec)
    return out


def corrections() -> dict[str, set[int]]:
    """被纠正过的轮次：{session_id: {turn, ...}}。"""
    out: dict[str, set[int]] = {}
    for rec in _iter_records():
        if rec.get("kind") != "correction":
            continue
        out.setdefault(str(rec.get("session_id") or ""), set()).add(int(rec.get("turn") or 0))
    return out


def query(session_id: str = "", signature: str = "", limit: int = 50) -> list[dict]:
    """按会话/签名查痕迹（新的在前）。"""
    out = [r for r in runs() if
           (not session_id or r.get("session_id") == session_id)
           and (not signature or r.get("intent_signature") == signature)]
    out.sort(key=lambda r: int(r.get("seq") or 0), reverse=True)
    return out[:max(1, int(limit))] if limit else out


def verify() -> dict:
    """校验运行痕迹的哈希链（与审计同一套）。"""
    broken: list[dict] = []
    files = 0
    records = 0
    d = paths.runs_dir()
    if d.exists():
        for path in sorted(d.glob("*.jsonl")):
            files += 1
            records += sum(1 for line in path.read_text(encoding="utf-8", errors="replace")
                           .splitlines() if line.strip())
            broken.extend(audit.verify_chain_in(path))
    return {"ok": not broken, "files": files, "records": records, "broken": broken}


def stats() -> dict:
    all_recs = list(_iter_records())
    rs = [r for r in all_recs if r.get("kind") == "run"]
    cs = [r for r in all_recs if r.get("kind") == "correction"]
    by_outcome: dict[str, int] = {}
    for r in rs:
        k = str(r.get("outcome") or "?")
        by_outcome[k] = by_outcome.get(k, 0) + 1
    return {"runs": len(rs), "corrections": len(cs),
            "sessions": len({r.get("session_id") for r in rs}),
            "signatures": len({r.get("intent_signature") for r in rs if r.get("intent_signature")}),
            "by_outcome": by_outcome, "trace_enabled": bool(config().get("trace_enabled", True))}


# ---------------------------------------------------------------- 历史回扫
def traced_turns() -> set[tuple[str, int]]:
    """已经记过的 (session_id, turn)，用于回扫去重（回扫必须幂等）。"""
    out: set[tuple[str, int]] = set()
    for rec in _iter_records():
        if rec.get("kind") != "run":
            continue
        try:
            out.add((str(rec.get("session_id") or ""), int(rec.get("turn") or 0)))
        except (TypeError, ValueError):
            continue
    return out


def _pairs(messages: list[dict]) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    last_user = ""
    for m in messages or []:
        role = str(m.get("role") or "")
        text = str(m.get("content") or "")
        if role == "user":
            last_user = text
        elif role == "assistant" and last_user:
            out.append((last_user, text))
            last_user = ""
    return out


def sweep(*, limit_sessions: int = 30, session_reader=None,
          sessions: list[dict] | None = None) -> dict:
    """**回扫历史会话**：把还没记过的轮次补成运行痕迹。

    为什么需要它：实时埋点只覆盖"从今天起"的对话，而"熟悉我"必须能看历史。
    回扫是**幂等**的（按 session_id+turn 去重），所以可以随时重跑、可以每日跑。

    注意：回扫拿不到当时的技能调用序列（那时没埋点），所以 `steps` 为空、
    靠 `inputs` 满足 R2 —— 这也是为什么实时埋点仍然要接（有 steps 才能做 L2 的能力核对）。

    `sessions` 可注入（测试用）；缺省从会话库读最近 limit_sessions 个。
    """
    if session_reader is None:
        session_reader = _default_session_reader
    known = traced_turns()
    scanned = recorded = skipped = 0
    errors: list[str] = []
    if sessions is None:
        try:
            sessions = _list_sessions(limit_sessions)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}

    for row in sessions:
        sid = str(row.get("id") or "")
        if not sid:
            continue
        try:
            sess = session_reader(sid)
        except Exception:  # noqa: BLE001
            sess = None
        if not sess:
            continue
        scanned += 1
        for idx, (user_text, assistant_text) in enumerate(_pairs(sess.get("messages") or []), start=1):
            if (sid, idx) in known:
                skipped += 1
                continue
            rec = record_turn(sid, idx, user_text, assistant_text)
            if rec:
                recorded += 1
                known.add((sid, idx))
    return {"ok": True, "scanned_sessions": scanned, "recorded": recorded,
            "skipped": skipped, "errors": errors}


def _default_session_reader(session_id: str) -> dict | None:
    from edu_agent.config import load_settings
    from edu_agent.host.store import SessionStore

    st = SessionStore(path=load_settings().sessions_db)
    try:
        return st.get(session_id)
    finally:
        st.close()


def _list_sessions(limit: int) -> list[dict]:
    from edu_agent.config import load_settings
    from edu_agent.host.store import SessionStore

    st = SessionStore(path=load_settings().sessions_db)
    try:
        rows = st.list_sessions()
        return list(reversed(rows))[:max(1, int(limit))]
    finally:
        st.close()


def sweep_session(session_id: str, *, session_reader=None, use_buffer: bool = True) -> dict:
    """补记**单个会话**里还没记过的轮次（幂等）。

    这是 web 侧实时埋点的入口：每次 `/api/chat` 开头调一次 —— 上一轮的回答此刻已经
    落在会话库里，于是"上一轮"就被补成一条痕迹；本轮技能调用记进缓冲，等下一轮再落盘。
    这样不必在 `/api/chat` 的每条 return 路径上都埋点（它有 5 条以上出口）。
    """
    sid = str(session_id or "")
    if not sid:
        return {"ok": False, "error": "缺少 session_id"}
    reader = session_reader or _default_session_reader
    try:
        sess = reader(sid)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    if not sess:
        return {"ok": False, "error": "会话不存在：%s" % sid}

    known = traced_turns()
    pairs = _pairs(sess.get("messages") or [])
    recorded = skipped = 0
    for idx, (user_text, assistant_text) in enumerate(pairs, start=1):
        if (sid, idx) in known:
            skipped += 1
            continue
        steps = None
        if use_buffer and idx == len(pairs):
            steps = _drain_steps()          # 只把缓冲给"最新一轮"（其余轮次没有步骤，属正常）
        if steps is None:
            steps = []
        rec = record_turn(sid, idx, user_text, assistant_text, steps=steps)
        if rec:
            recorded += 1
            known.add((sid, idx))
    return {"ok": True, "session_id": sid, "turns": len(pairs),
            "recorded": recorded, "skipped": skipped}
