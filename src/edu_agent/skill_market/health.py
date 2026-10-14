# -*- coding: utf-8 -*-
"""skill-create · M6 发布后回归（L4）：技能上线之后还灵不灵。

第一轮只做到"发布前检验"。但技能上线之后才是真正开始被用 —— 上游接口改了、
模型换了、数据格式变了，都可能让一个昨天还好的技能今天开始失败。本模块做三件事：

1. **每次调用都留一条 skill-run 记录**（只追加、脱敏、哈希链，与运行痕迹同一套）；
2. **按技能算健康度**：调用次数、成功率、延迟 p50/p95、最近一次错误、趋势；
3. **跌破阈值就提示**（`attention=true` + 原因），但**绝不自动停用** ——
   停用是 `skill_disable` 闸门后面的人的决定（与"发布权在人手里"一致）。

阈值（`skill_create.json`）：

    health_min_calls: 5        # 少于这个调用数不下结论（避免一次失败就报警）
    health_min_ok_rate: 0.8    # 成功率低于它就标 attention
    health_window_days: 30     # 只看最近多少天
"""
from __future__ import annotations

import time
from typing import Any

from edu_agent.skill_market import audit, paths, runs

_DEFAULTS = {
    "health_min_calls": 5,
    "health_min_ok_rate": 0.8,
    "health_window_days": 30,
}


def config() -> dict:
    cfg = dict(runs.config())
    out = dict(_DEFAULTS)
    for k in _DEFAULTS:
        if k in cfg:
            out[k] = cfg[k]
    return out


def _month_file(ts: float | None = None) -> "paths.Path":  # type: ignore[name-defined]
    from pathlib import Path

    return Path(paths.runs_dir()) / time.strftime("%Y-%m.jsonl", time.localtime(ts or time.time()))


def record_run(skill_id: str, *, ok: bool, ms: int = 0, session_id: str = "",
               error: str = "", args: dict | None = None,
               installed_only: bool = True) -> dict:
    """记一次**已发布技能**的调用（L4 的原始数据）。

    `installed_only=True` 时只记"装在运行时目录里"的技能 —— 也就是已发布的那些；
    草稿/市场里的技能不产生回归数据（它们还不该被调用）。
    任何异常都吞掉：埋点不能影响技能调用。
    """
    try:
        sid = str(skill_id or "")
        if not sid:
            return {}
        if installed_only and not paths.is_inside_runtime(sid):
            return {}
        rec = audit.chain_append(_month_file(), {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "kind": "skill-run",
            "skill": sid,
            "ok": bool(ok),
            "ms": int(ms or 0),
            "session_id": str(session_id or ""),
            "error": audit.redact(str(error or ""))[:200] if error else "",
            "args_digest": audit.digest(args) if args else "",
        })
        return rec
    except Exception:  # noqa: BLE001
        return {}


def _skill_runs(window_days: int | None = None) -> list[dict]:
    cfg = config()
    days = int(window_days if window_days is not None else cfg["health_window_days"])
    cutoff = time.time() - days * 86400 if days else 0
    out: list[dict] = []
    for rec in runs._iter_records():                      # noqa: SLF001  同一模块族的内部读法
        if rec.get("kind") != "skill-run":
            continue
        if cutoff:
            try:
                ts = time.mktime(time.strptime(str(rec.get("ts"))[:19], "%Y-%m-%dT%H:%M:%S"))
            except ValueError:
                ts = 0.0
            if ts < cutoff:
                continue
        out.append(rec)
    return out


def _pct(values: list[int], q: float) -> int:
    if not values:
        return 0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))
    return int(s[idx])


def skill_stats(skill_id: str = "", *, window_days: int | None = None) -> list[dict]:
    """按技能聚合健康度（skill_id 为空则返回全部）。"""
    cfg = config()
    recs = _skill_runs(window_days)
    groups: dict[str, list[dict]] = {}
    for r in recs:
        groups.setdefault(str(r.get("skill") or ""), []).append(r)

    out: list[dict] = []
    for sid, items in groups.items():
        if skill_id and sid != skill_id:
            continue
        items.sort(key=lambda r: str(r.get("ts") or ""))
        ok_n = sum(1 for r in items if r.get("ok"))
        lat = [int(r.get("ms") or 0) for r in items if r.get("ms")]
        last_err = ""
        for r in reversed(items):
            if not r.get("ok"):
                last_err = str(r.get("error") or "")
                break
        # 趋势：后半段成功率 - 前半段成功率
        half = max(1, len(items) // 2)
        first, second = items[:half], items[half:]
        r1 = (sum(1 for r in first if r.get("ok")) / len(first)) if first else 0.0
        r2 = (sum(1 for r in second if r.get("ok")) / len(second)) if second else 0.0
        ok_rate = round(ok_n / len(items), 4) if items else 0.0
        recent = items[-3:]
        recent_rate = (sum(1 for r in recent if r.get("ok")) / len(recent)) if recent else 1.0
        attention, why = False, ""
        if len(items) < int(cfg["health_min_calls"]):
            why = "调用样本还少（%d 次，需 %d 次才下结论）" % (len(items), int(cfg["health_min_calls"]))
        elif ok_rate < float(cfg["health_min_ok_rate"]):
            attention = True
            why = "成功率 %.0f%% 低于阈值 %.0f%%" % (ok_rate * 100, float(cfg["health_min_ok_rate"]) * 100)
        elif len(items) >= 6 and recent_rate <= 0.34:
            # 总成功率还达标、但**最近几次**基本都失败 → 刚坏，值得看一眼。
            # （早先写成"前后半段成功率差 ≥0.5"，那个条件在默认阈值下永远被上一条先命中，
            #   等于死代码 —— 改成看最近 3 次。）
            attention = True
            why = "最近 %d 次里成功率只有 %.0f%%（趋势下滑）" % (len(recent), recent_rate * 100)
        out.append({
            "skill": sid,
            "calls": len(items),
            "ok": ok_n,
            "failed": len(items) - ok_n,
            "ok_rate": ok_rate,
            "latency_p50_ms": _pct(lat, 0.5),
            "latency_p95_ms": _pct(lat, 0.95),
            "last_error": last_err,
            "last_call": str(items[-1].get("ts") or "") if items else "",
            "trend": round(r2 - r1, 4),
            "attention": attention,
            "reason": why,
            "installed": paths.is_inside_runtime(sid),
        })
    return sorted(out, key=lambda r: (not r["attention"], -r["calls"]))


def health(window_days: int | None = None) -> dict:
    """L4 总览：所有已发布技能的健康度 + 需要人看一眼的清单。"""
    stats = skill_stats(window_days=window_days)
    attention = [s for s in stats if s["attention"]]
    return {
        "ok": True,
        "skills": stats,
        "attention": attention,
        "attention_count": len(attention),
        "config": config(),
        "hint": ("有 %d 个技能成功率偏低：建议在「权限」里停用或回滚，"
                 "系统不会自动停用它们" % len(attention)) if attention else "所有已发布技能运行正常",
    }


def verify() -> dict:
    """skill-run 记录也在运行痕迹文件里 → 沿用同一套哈希链校验。"""
    return runs.verify()
