# -*- coding: utf-8 -*-
"""skill-create · M5 对外接口（纯数据面，无前端页面）。

    GET  /api/skill-create/candidates        候选看板（评分、证据、差哪条判据）
    POST /api/skill-create/scan              挖一次（可先回扫历史会话）
    POST /api/skill-create/sweep             只回扫历史会话（幂等）
    POST /api/skill-create/verify            L1+L2 检验
    POST /api/skill-create/draft             候选 → 草稿（过 permissions 闸门）
    POST /api/skill-create/submit            提交审核（要求已检验）
    POST /api/skill-create/reject            否决候选（进冷却名单）
    GET  /api/skill-create/runs              运行痕迹查询（只读）
    GET  /api/skill-create/suggest           半自动提示：这个会话本身够不够格封装
    POST /api/skill-create/trace             手工写一条痕迹（测试/演示/补录）
    GET  /api/skill-create/stats             观测：候选数、通过率、被否率
    GET  /api/skill-create/verify-chain      运行痕迹哈希链校验
    GET  /api/skill-create/config            阈值配置
    POST /api/skill-create/config            改阈值（不用改代码）
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from edu_agent.skill_market import (health, lifecycle, miner, packager, runs,
                                   safe_import, scheduler, shadow, verifier)

router = APIRouter(prefix="/api/skill-create", tags=["skill-create"])


def _err(e: Exception) -> dict:
    return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


@router.get("/candidates")
def api_candidates(state: str = "", limit: int = 50):
    """候选看板（按分数降序）。冷却中的候选带 `cooling_until`，前端可折叠展示。"""
    try:
        rows = miner.list_candidates(state=state)[:max(1, int(limit))]
        for r in rows:
            r.pop("_file", None)
            if not r.get("cooling_until"):
                r["cooling_until"] = miner.rejection_until(str(r.get("signature") or ""))
        return {"ok": True, "candidates": rows, "count": len(rows)}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/scan")
def api_scan(body: dict):
    """挖一次。默认先回扫历史会话（`sweep=true`），所以第一次调用就能看到历史候选。"""
    try:
        swept = None
        if body.get("sweep", True):
            swept = runs.sweep(limit_sessions=int(body.get("limit_sessions") or 30))
        r = miner.scan(since_days=int(body["since_days"]) if body.get("since_days") else None)
        r["sweep"] = swept
        return r
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/sweep")
def api_sweep(body: dict | None = None):
    """只回扫历史会话（幂等，可随时重跑）。"""
    try:
        return runs.sweep(limit_sessions=int((body or {}).get("limit_sessions") or 30))
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/verify")
def api_verify(body: dict):
    """L1 静态权限扫描 + L2 声明式回放。`candidate_id` 或 `skill_id` 都行。"""
    try:
        target = str(body.get("candidate_id") or body.get("skill_id") or "")
        return verifier.verify(target, actor=str(body.get("actor") or "E"))
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/import")
def api_import(body: dict):
    """把手写的 SKILL.md（YAML 块状 front-matter 也认）导入市场草稿区。

    body: {"text": "<SKILL.md 全文>"} 或 {"path": "<导入根内的路径>"}

    doc 34 §5.5：**不接受任意本机绝对路径** —— `path` 必须落在服务端配置的导入根之内
    （`EDU_SKILL_IMPORT_ROOTS`，默认 `<repo>/skill-src` 与 `<data_dir>/skill_inbox`），
    并做体积上限检查。相对路径按导入根解析。
    """
    try:
        text = str(body.get("text") or "")
        if not text and body.get("path"):
            got = safe_import.resolve_import_path(str(body["path"]))
            if not got.get("ok"):
                return {"ok": False, "gate": "path_whitelist", "error": got["error"],
                        "roots": got.get("roots") or safe_import.describe()["import_roots"]}
            p = got["path"]
            ok, why = safe_import.check_size(p, safe_import.MAX_IMPORT_BYTES)
            if not ok:
                return {"ok": False, "gate": "size", "error": why, "path": p}
            text = Path(p).read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            return {"ok": False, "error": "缺少 text 或 path"}
        r = packager.import_draft(text, actor=str(body.get("actor") or "E"),
                                  confirm=bool(body.get("confirm")),
                                  description=str(body.get("description") or ""))
        r.pop("manifest", None)
        return r
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/draft")
def api_draft(body: dict):
    """候选 → 草稿。要求 R1~R6 全过；会过 `permissions.guard("skill_create_draft")`。"""
    try:
        r = packager.make_draft(str(body.get("candidate_id") or ""),
                                actor=str(body.get("actor") or "E"),
                                confirm=bool(body.get("confirm")),
                                force=bool(body.get("force")))
        r.pop("manifest", None)
        return r
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/submit")
def api_submit(body: dict):
    """提交审核。**必须先检验过**（且检验后草稿没被改过）——这是"必须检验"的闸门。"""
    try:
        target = str(body.get("candidate_id") or body.get("skill_id") or "")
        st = verifier.verification_state(target)
        if not st.get("ok"):
            return st
        r = lifecycle.submit(str(st.get("skill_id") or ""),
                             str(body.get("actor") or "E"),
                             session_id=str(body.get("session_id") or ""))
        if r.get("ok") and st.get("candidate_id"):
            miner.set_state(str(st["candidate_id"]), "promoted", skill_id=st.get("skill_id"),
                            submitted_at=__import__("time").time())
        return r
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/reject")
def api_reject(body: dict):
    """否决候选：进冷却名单（只追加），冷却期内不再出草稿。"""
    try:
        return miner.reject(str(body.get("candidate_id") or ""),
                            reason=str(body.get("reason") or ""),
                            actor=str(body.get("actor") or "user"))
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/runs")
def api_runs(signature: str = "", session_id: str = "", limit: int = 50):
    """运行痕迹查询（只读）。"""
    try:
        return {"ok": True, "runs": runs.query(session_id=session_id, signature=signature,
                                               limit=max(1, min(limit, 500)))}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/suggest")
def api_suggest(session_id: str = ""):
    """半自动提示：这个会话**自身**是否已经够格封装（不自动出草稿）。"""
    try:
        sid = str(session_id or "")
        if not sid:
            return {"ok": False, "error": "缺少 session_id"}
        mine = [r for r in runs.runs() if r.get("session_id") == sid]
        sigs = {r.get("intent_signature") for r in mine if r.get("intent_signature")}
        hits = [c for c in miner.list_candidates()
                if c.get("signature") in sigs and all((c.get("criteria") or {}).values())]
        return {"ok": True, "session_id": sid, "traced_runs": len(mine),
                "signatures": sorted(s for s in sigs if s),
                "ready_candidates": [{"candidate_id": c["candidate_id"], "title": c.get("title"),
                                      "runs": c.get("runs"), "sessions": c.get("sessions"),
                                      "score": c.get("score")} for c in hits],
                "ask_user": bool(hits),
                "prompt": ("这套流程已经跨会话跑通 %d 次了，要不要固化成技能？"
                           % (hits[0].get("runs") if hits else 0)) if hits else ""}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/trace")
def api_trace(body: dict):
    """手工写一条运行痕迹（测试/演示/补录用）。正常情况由 web_server 自动埋点。"""
    try:
        rec = runs.record_turn(str(body.get("session_id") or ""),
                               int(body.get("turn") or 1),
                               str(body.get("user_text") or ""),
                               str(body.get("assistant_text") or ""),
                               inputs=body.get("inputs") or None,
                               steps=body.get("steps") or None,
                               latency_ms=int(body.get("latency_ms") or 0))
        return {"ok": bool(rec), "run": rec}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/stats")
def api_stats():
    """观测：评估阈值是否合理（候选数、判据通过率、被否数、痕迹分布）。"""
    try:
        return {"ok": True, **miner.stats()}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/verify-chain")
def api_verify_chain():
    """运行痕迹的哈希链校验（防篡改）。"""
    try:
        return {"ok": True, **runs.verify()}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/config")
def api_get_config():
    try:
        return {"ok": True, "config": runs.config()}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/config")
def api_set_config(body: dict):
    """改阈值（min_sessions / window_days / consistency_min / score_min /
    reject_cooldown_days / dedupe_threshold / trace_enabled /
    require_shadow / scan_interval_hours / prompt_in_chat /
    health_min_calls / health_min_ok_rate / health_window_days）。"""
    try:
        return {"ok": True, "config": runs.set_config(**(body or {}))}
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ---------------------------------------------------------------- 第二轮：L3 / L4 / 定时
@router.post("/shadow")
def api_shadow(body: dict):
    """L3 影子运行：把草稿里的 plugin.py 放进子进程真跑一次，出站调用全被记录桩拦下。

    `candidate_id` 或 `skill_id` 都行（人工导入的草稿没有候选 id）。
    """
    try:
        target = str(body.get("candidate_id") or body.get("skill_id") or "")
        return shadow.shadow_run(target, actor=str(body.get("actor") or "E"),
                                 timeout=int(body.get("timeout") or 60))
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/health")
def api_health(window_days: int = 0):
    """L4 发布后回归：每个已发布技能的调用次数/成功率/延迟/最近错误。

    跌破阈值的会带 `attention=true` 与原因 —— **只提示，不自动停用**。
    """
    try:
        return health.health(window_days=window_days or None)
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/health/{skill_id}")
def api_health_one(skill_id: str):
    try:
        return {"ok": True, "skills": health.skill_stats(skill_id)}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/schedule")
def api_schedule():
    """定时扫描状态（间隔、上次/下次、上次结果）。"""
    try:
        return scheduler.status()
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/schedule/run")
def api_schedule_run(body: dict | None = None):
    """立刻扫一次（等于 /scan，但走定时任务同一入口，结果会记进调度状态）。"""
    try:
        b = body or {}
        return scheduler.run_once(sweep=bool(b.get("sweep", True)),
                                  limit_sessions=int(b.get("limit_sessions") or 30))
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/schedule/start")
def api_schedule_start():
    try:
        return scheduler.start()
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/schedule/stop")
def api_schedule_stop():
    try:
        return scheduler.stop()
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/prompt")
def api_prompt(session_id: str = ""):
    """半自动提示：这个会话里跑过的流程值不值得固化（只提议，且同一会话只提一次）。"""
    try:
        return miner.prompt_for_session(str(session_id or ""))
    except Exception as e:  # noqa: BLE001
        return _err(e)
