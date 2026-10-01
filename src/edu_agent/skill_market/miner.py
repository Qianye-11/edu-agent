# -*- coding: utf-8 -*-
"""skill-create · M2 工作流挖掘器。

把运行痕迹（`runs/`）按**意图签名**聚合，找出"跨会话反复出现且每次都跑通"的模式，
产出**候选工作流**（`candidates/`）—— 只提议，不生成草稿。

签名（`intent_signature`）是确定性的，不用模型：

    signature = 意图 | 能力序列 | 产出形状
    例：draft-question|kg.read+question.draft|stem+answer+analysis+knowledge_id

所以"给高中学生生成一道题，要带解析"与"帮我按知识点出个题，高一，基础难度"
会落到**同一个签名**——这是"熟悉你的说话方式"，不是匹配固定话术。

R1~R6 判据见 docs/21。其中：
* R3 在挖掘阶段用**痕迹里的形状一致率**做代理，真正的"回放一致"由 `verifier` 的 L2 判定
  （它会把结果写回候选的 criteria）；
* R5 在挖掘阶段只校验"痕迹里调过的能力都能映射到已登记能力"，
  完整的静态权限扫描同样由 `verifier` 的 L1 判定。
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import time
from pathlib import Path

from edu_agent.skill_market import audit, capability_registry as caps, paths, runs

# ---------------------------------------------------------------- 意图识别
INTENT_RULES: tuple[tuple[re.Pattern, str], ...] = (
    # 出题：口语说法很多（出题 / 出个题 / 出一道 / 来一道 / 重出一道题 / 再出几道题 / 题目草稿），
    # 早先只写了「出题」，结果"帮我按知识点出个题"落到 other、与"生成一道题"分不到一组；
    # 更要命的是用户纠正时说的"重出一道题"也认不出来 —— 于是被纠正的轮次掉进别的签名组，
    # R6 就算不出纠正（实测踩过两次）。所以这里把动词与量词都放开。
    (re.compile(r"((出|生成|重出|再出|换|来一|来几)[^。；\n]{0,6}?题|题目草稿|命题|组卷|出卷)"),
     "draft-question"),
    (re.compile(r"(讲解|讲讲|解释|为什么|说明一下|怎么回事)"), "explain"),
    (re.compile(r"(教案|备课|课时|教学设计)"), "lesson-plan"),
    (re.compile(r"(导出|生成\s*(docx|word|ppt|pptx|课件))", re.I), "export-doc"),
    (re.compile(r"(整理|归纳|总结|梳理)"), "summarize"),
    (re.compile(r"(分析|诊断|体检|学情|掌握度)"), "analyze"),
    (re.compile(r"(取图|配图|找图片|插画)"), "fetch-image"),
)


# 承接语：用户第二轮往往不重述意图（"再来一道"/"继续"/"还有吗"），
# 这类轮次要**继承上一轮的意图**，否则会被归到 other、从工作流里漏掉（实测踩过）。
CONTINUATION_RE = re.compile(r"(再来|再出|继续|接着|下一个|同上|还有吗|再多|再给我|再要)")


def intent_of(text: str) -> str:
    """从触发语识别意图（第一个命中的规则胜出，都命中不了 → other）。"""
    s = str(text or "")
    for rx, name in INTENT_RULES:
        if rx.search(s):
            return name
    return "other"


# 意图 → 该意图**通常**需要的能力（历史回扫时推断用，见 packager）
INTENT_DEFAULT_CAPABILITIES: dict[str, tuple[str, ...]] = {
    "draft-question": ("kg.read", "question.draft"),
    "explain": ("kb.read",),
    "lesson-plan": ("lesson.read", "workbench.emit"),
    "export-doc": ("action:export_docx",),
    "summarize": ("session.read",),
    "analyze": ("mastery.read",),
    "fetch-image": ("skill:web-image",),
}


def capability_of_step(step: dict) -> str:
    """痕迹里的一个步骤 → 能力令牌（尽量用已登记的名字）。"""
    sid = str(step.get("id") or "").strip()
    if not sid:
        return ""
    kind = str(step.get("kind") or "skill")
    for token in ("skill:" + sid, "mcp:" + sid, sid):
        ok, _ = caps.resolve_capability(token)
        if ok:
            return sid if token == sid else sid
    return sid


def signature_for(trigger_text: str, steps: list[dict] | None, inputs: dict | None,
                  shape: list[str] | None, intent: str = "") -> str:
    """意图签名：意图 | 能力序列 | 产出形状。

    `intent` 可显式给定（承接轮继承上一轮意图时用），缺省从触发语识别。
    """
    name = intent or intent_of(trigger_text)
    cap_ids: list[str] = []
    for st in steps or []:
        if not isinstance(st, dict):
            continue
        if str(st.get("kind") or "") not in ("skill", "mcp", "tool"):
            continue
        cid = capability_of_step(st)
        if cid and cid not in cap_ids:
            cap_ids.append(cid)
    cap_str = "+".join(sorted(cap_ids)) or "no-cap"
    shape_str = "+".join(sorted({str(s) for s in (shape or []) if str(s).strip()})) or "no-shape"
    return "%s|%s|%s" % (name, cap_str, shape_str)


# ---------------------------------------------------------------- 候选存储
def _cand_path(signature: str) -> Path:
    h = hashlib.sha1(signature.encode("utf-8")).hexdigest()[:16]
    return paths.candidates_dir() / ("c-%s.json" % h)


def _read_json(p: Path) -> dict:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (ValueError, OSError):
        return {}


def _write_json(p: Path, data: dict) -> None:
    paths.ensure_dirs()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)


def list_candidates(state: str = "") -> list[dict]:
    """候选看板（按分数降序）。"""
    d = paths.candidates_dir()
    if not d.exists():
        return []
    out: list[dict] = []
    for p in sorted(d.glob("c-*.json")):
        c = _read_json(p)
        if not c or not c.get("candidate_id"):
            continue
        if state and c.get("state") != state:
            continue
        c["_file"] = str(p)
        out.append(c)
    return sorted(out, key=lambda c: float(c.get("score") or 0), reverse=True)


def get_candidate(candidate_id: str) -> dict | None:
    for c in list_candidates():
        if c.get("candidate_id") == candidate_id or c.get("signature") == candidate_id:
            return c
    return None


def save_candidate(c: dict) -> dict:
    c = dict(c)
    c.pop("_file", None)
    c["updated"] = time.time()
    _write_json(_cand_path(str(c.get("candidate_id") or c.get("signature") or "")), c)
    return c


def set_state(candidate_id: str, state: str, **fields) -> dict:
    c = get_candidate(candidate_id)
    if c is None:
        return {"ok": False, "error": "候选不存在：%s" % candidate_id}
    c.pop("_file", None)
    c["state"] = state
    c.update(fields)
    save_candidate(c)
    return {"ok": True, "candidate_id": c["candidate_id"], "state": state}


# ---------------------------------------------------------------- 否决 / 冷却
def reject(candidate_id: str, reason: str = "", actor: str = "user") -> dict:
    """否决候选：进冷却名单（只追加），冷却期内不再出现在看板顶部。"""
    c = get_candidate(candidate_id)
    if c is None:
        return {"ok": False, "error": "候选不存在：%s" % candidate_id}
    rec = audit.chain_append(paths.rejected_file(), {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "kind": "reject",
        "candidate_id": c["candidate_id"],
        "signature": c.get("signature"),
        "actor": audit.redact(str(actor)),
        "reason": audit.redact(str(reason))[:200],
    })
    set_state(c["candidate_id"], "rejected", rejected_at=time.time(), reject_reason=reason)
    return {"ok": True, "candidate_id": c["candidate_id"], "state": "rejected", "record": rec}


def rejection_until(signature: str, cfg: dict | None = None) -> float:
    """该签名被否决后的冷却截止时间（0 = 没被否决过/已过冷却）。"""
    cfg = cfg or runs.config()
    days = float(cfg.get("reject_cooldown_days") or 30)
    latest = 0.0
    p = paths.rejected_file()
    if not p.exists():
        return 0.0
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0.0
    for line in lines:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if rec.get("signature") != signature:
            continue
        try:
            ts = time.mktime(time.strptime(str(rec.get("ts"))[:19], "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            ts = 0.0
        latest = max(latest, ts)
    if not latest:
        return 0.0
    until = latest + days * 86400
    return until if until > time.time() else 0.0


# ---------------------------------------------------------------- 聚合
_PII_PATTERNS = (
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}"),
    # 身份证要带合法生日，否则 64 位十六进制哈希里偶然出现的 18 位数字会被误判（实测踩过）
    re.compile(r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"),
)

# 扫描隐私时**跳过**这些字段：它们是我们自己的摘要/链路值，不是用户数据。
_PII_EXEMPT_KEYS = {"hash", "prev_hash", "args_digest", "digest", "seq", "ts",
                    "turn", "latency_ms", "chars", "ms"}


def _ts_of(rec: dict) -> float:
    try:
        return time.mktime(time.strptime(str(rec.get("ts"))[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return 0.0


def _trace_complete(rec: dict) -> bool:
    """R2：一次运行是否留下了完整痕迹。"""
    if not rec.get("session_id") or not rec.get("intent_signature"):
        return False
    out = rec.get("output") or {}
    if not (out.get("shape") or []):
        return False
    return bool(rec.get("steps") or rec.get("inputs"))


def _has_pii(rec: dict) -> bool:
    """R4：痕迹里是否残留未脱敏隐私。

    两处防误判（都踩过）：① 跳过自家摘要/链路字段（hash/prev_hash/digest…）；
    ② 剩下的文本里再把摘要串剔掉。否则 sha256 里偶然出现的 18 位数字会被当成身份证。
    """
    def strip(o):
        if isinstance(o, dict):
            return {k: strip(v) for k, v in o.items() if str(k) not in _PII_EXEMPT_KEYS}
        if isinstance(o, list):
            return [strip(v) for v in o]
        return o

    blob = audit.strip_digests(json.dumps(strip(rec), ensure_ascii=False))
    return any(rx.search(blob) for rx in _PII_PATTERNS)


def aggregate(records: list[dict] | None = None, *, cfg: dict | None = None) -> list[dict]:
    """按签名聚合 → 候选列表（含 R1~R6 逐条结论与评分）。"""
    cfg = cfg or runs.config()
    recs = records if records is not None else runs.runs(since_days=int(cfg.get("window_days") or 30))
    corrections = runs.corrections()

    groups: dict[str, list[dict]] = {}
    for r in recs:
        sig = str(r.get("intent_signature") or "")
        if not sig:
            continue
        groups.setdefault(sig, []).append(r)

    out: list[dict] = []
    for sig, items in groups.items():
        items.sort(key=lambda r: _ts_of(r))
        successes = [r for r in items if r.get("outcome") == "success"]
        if not successes:
            continue
        sessions = sorted({str(r.get("session_id")) for r in successes})
        shapes = [tuple(r.get("output", {}).get("shape") or []) for r in successes]
        common = max(set(shapes), key=shapes.count) if shapes else ()
        consistency = (shapes.count(common) / len(shapes)) if shapes else 0.0
        last_ts = _ts_of(successes[-1])
        first_ts = _ts_of(successes[0])
        age_days = max(0.0, (time.time() - last_ts) / 86400) if last_ts else 999.0

        # R6：被纠正过的轮次（同一会话同一轮）。计划里的口径是"允许 ≤1 次且已记录"，
        # 所以 ≥2 次纠正直接不过 —— 早先写成"只要后面还有成功就算过"，等于把
        # "反复被纠正的坏流程"也放行（实测踩过）。
        corrected = sum(1 for r in successes
                        if int(r.get("turn") or 0) in corrections.get(str(r.get("session_id")), set()))
        later_success_after_correction = corrected > 0 and len(successes) > corrected
        r6_ok = corrected == 0 or (corrected == 1 and later_success_after_correction)

        # R5（挖掘阶段代理）：痕迹里调过的能力必须都能映射到已登记能力
        unmapped: list[str] = []
        for r in successes:
            for st in (r.get("steps") or []):
                if not isinstance(st, dict):
                    continue
                cid = str(st.get("id") or "")
                if not cid:
                    continue
                ok, _ = caps.resolve_capability(cid)
                ok2, _ = caps.resolve_capability("skill:" + cid)
                ok3, _ = caps.resolve_capability("mcp:" + cid)
                if not (ok or ok2 or ok3) and cid not in unmapped:
                    unmapped.append(cid)

        steps_max = max((len(r.get("steps") or []) for r in successes), default=0)
        criteria = {
            "R1": len(sessions) >= int(cfg.get("min_sessions") or 2),
            "R2": all(_trace_complete(r) for r in successes),
            "R3": consistency >= float(cfg.get("consistency_min") or 1.0),   # 代理：形状一致率
            "R4": not any(_has_pii(r) for r in items),
            "R5": not unmapped,
            "R6": r6_ok,
        }
        blocking: list[str] = []
        if not criteria["R1"]:
            blocking.append("R1 未过：只有 %d 个不同会话（需要 %d 个）"
                            % (len(sessions), int(cfg.get("min_sessions") or 2)))
        if not criteria["R2"]:
            blocking.append("R2 未过：有运行缺少完整痕迹（输入/步骤/产出形状）")
        if not criteria["R3"]:
            blocking.append("R3 未过：产出形状一致率 %.2f（需要 %.2f）"
                            % (consistency, float(cfg.get("consistency_min") or 1.0)))
        if not criteria["R4"]:
            blocking.append("R4 未过：痕迹里检出未脱敏隐私")
        if not criteria["R5"]:
            blocking.append("R5 未过：调用了未登记能力 %s" % "、".join(unmapped))
        if not criteria["R6"]:
            blocking.append("R6 未过：%d 次运行被人工纠正过（只允许 ≤1 次且之后仍成功）" % corrected)

        # 评分（全部归一到 0~1）
        s_runs = min(1.0, len(successes) / 5.0)
        s_sess = min(1.0, len(sessions) / 3.0)
        s_cons = float(consistency)
        s_recent = math.exp(-age_days / 14.0) if age_days < 900 else 0.0
        s_gain = min(1.0, (steps_max * len(successes)) / 20.0)
        score = round(0.35 * s_runs + 0.25 * s_sess + 0.20 * s_cons
                      + 0.10 * s_recent + 0.10 * s_gain, 4)

        title = _title_of(successes[-1], sig)
        intent = sig.split("|", 1)[0]
        observed_caps = sorted({capability_of_step(st)
                                for r in successes for st in (r.get("steps") or [])
                                if isinstance(st, dict) and st.get("id")})
        # 历史回扫拿不到"当时调了哪个技能"（那时还没埋点），所以能力清单只能按意图推断 ——
        # 必须如实标出来，让人知道这份清单是**推断**的、需要人工确认（实测踩过）。
        suggested = [c for c in INTENT_DEFAULT_CAPABILITIES.get(intent, ()) if c not in observed_caps]
        out.append({
            "candidate_id": sig,
            "signature": sig,
            "title": title,
            "intent": intent,
            "capabilities": observed_caps,
            "capabilities_source": "observed" if observed_caps else "inferred",
            "suggested_capabilities": suggested,
            "runs": len(successes),
            "total_runs": len(items),
            "sessions": len(sessions),
            "session_ids": sessions[:10],
            "success_rate": round(len(successes) / len(items), 4),
            "consistency": round(consistency, 4),
            "shape": list(common),
            "corrections": corrected,
            "unmapped_capabilities": unmapped,
            "first_seen": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(first_ts)) if first_ts else "",
            "last_seen": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(last_ts)) if last_ts else "",
            "score": score,
            "criteria": criteria,
            "blocking": blocking,
            "evidence": [{"session_id": r.get("session_id"), "turn": r.get("turn"),
                          "run_seq": r.get("seq"), "outcome": r.get("outcome")}
                         for r in successes[-10:]],
            "state": "candidate",
            "skill_id": "",
        })
    return out


def _title_of(rec: dict, sig: str) -> str:
    """候选标题：优先用触发语的前 24 字（已脱敏）。"""
    txt = rec.get("trigger_text")
    if isinstance(txt, str) and txt.strip():
        return " ".join(txt.split())[:24]
    return sig.split("|", 1)[0]


# ---------------------------------------------------------------- 扫描入口
def scan(*, since_days: int | None = None, cfg: dict | None = None) -> dict:
    """挖一次：读痕迹 → 聚合 → 更新候选看板（**只更新看板，不生成草稿**）。"""
    cfg = cfg or runs.config()
    window = int(since_days or cfg.get("window_days") or 30)
    recs = runs.runs(since_days=window)
    found = aggregate(recs, cfg=cfg)

    created, updated, verified, blocked, cooling = 0, 0, 0, 0, 0
    for c in found:
        old = get_candidate(c["candidate_id"])
        if old:
            keep_state = old.get("state") or "candidate"
            for k in ("state", "skill_id", "rejected_at", "reject_reason"):
                if old.get(k) not in (None, ""):
                    c[k] = old[k]
            c["state"] = keep_state
            c["first_seen"] = old.get("first_seen") or c["first_seen"]
            updated += 1
        else:
            created += 1
        c["cooling_until"] = rejection_until(c["signature"], cfg)
        if c["cooling_until"]:
            cooling += 1
        if all(c["criteria"].values()):
            verified += 1
        else:
            blocked += 1
        save_candidate(c)

    return {
        "ok": True,
        "window_days": window,
        "runs_recorded": len(recs),
        "signatures": len(found),
        "candidates": len(found),
        "created": created, "updated": updated,
        "verified": verified, "blocked": blocked, "cooling": cooling,
        "candidates_ready": [c["candidate_id"] for c in found if all(c["criteria"].values())],
        "config": cfg,
    }


def stats() -> dict:
    """挖掘观测：评估阈值是否合理（候选数、通过率、被否率）。"""
    cands = list_candidates()
    by_state: dict[str, int] = {}
    for c in cands:
        by_state[c.get("state", "?")] = by_state.get(c.get("state", "?"), 0) + 1
    ready = [c for c in cands if all((c.get("criteria") or {}).values())]
    return {"candidates": len(cands), "by_state": by_state,
            "criteria_ready": len(ready),
            "avg_score": round(sum(float(c.get("score") or 0) for c in cands) / len(cands), 4) if cands else 0.0,
            "trace": runs.stats(), "config": runs.config()}


# ---------------------------------------------------------------- 半自动提示（M8）
def prompted_file() -> Path:
    """提示记录（**只追加**）：同一个会话/候选只提示一次，不反复唠叨。"""
    return paths.candidates_dir() / "_prompts.jsonl"


def already_prompted(session_id: str, candidate_id: str) -> bool:
    p = prompted_file()
    if not p.exists():
        return False
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return False
    for line in lines:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if (str(rec.get("session_id")) == str(session_id)
                and str(rec.get("candidate_id")) == str(candidate_id)):
            return True
    return False


def mark_prompted(session_id: str, candidate_id: str, text: str = "") -> dict:
    return audit.chain_append(prompted_file(), {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "kind": "prompt",
        "session_id": str(session_id),
        "candidate_id": str(candidate_id),
        "text": audit.redact(str(text))[:200] if text else "",
    })


def prompt_for_session(session_id: str) -> dict:
    """半自动提示：这个会话里跑过的流程，有没有已经跨会话跑通、值得固化的？

    只**提议**：返回文案给 web 层去展示；不生成草稿、不发布。
    同一个 (会话, 候选) 只提示一次（`_prompts.jsonl` 只追加记录）。
    """
    sid = str(session_id or "")
    if not sid:
        return {"ok": False, "error": "缺少 session_id"}
    if not bool(runs.config().get("prompt_in_chat", True)):
        return {"ok": True, "ask": False, "reason": "prompt_in_chat=false"}
    sigs = {r.get("intent_signature") for r in runs.runs() if r.get("session_id") == sid}
    if not sigs:
        return {"ok": True, "ask": False, "reason": "这个会话还没有痕迹"}
    for c in list_candidates():
        if c.get("signature") not in sigs:
            continue
        if c.get("state") in ("rejected", "promoted", "drafted"):
            continue
        if not all((c.get("criteria") or {}).values()):
            continue
        cid = str(c.get("candidate_id"))
        if already_prompted(sid, cid):
            continue
        text = ("（这套流程已经跨 %d 个会话跑通 %d 次了，要不要固化成技能？"
                "回复「存成技能」我就把它整理成草稿待你审核。）"
                % (c.get("sessions") or 0, c.get("runs") or 0))
        mark_prompted(sid, cid, text)
        return {"ok": True, "ask": True, "candidate_id": cid,
                "title": c.get("title"), "runs": c.get("runs"),
                "sessions": c.get("sessions"), "score": c.get("score"), "text": text}
    return {"ok": True, "ask": False, "reason": "没有新的、已跑通的流程可提示"}
