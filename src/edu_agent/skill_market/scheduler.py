# -*- coding: utf-8 -*-
"""skill-create · M9 定时扫描：不用你手动点，它自己每天挖一次。

一个守护线程，每 `scan_interval_hours`（默认 24 小时，0 = 关掉）做两件事：

    1. runs.sweep()   回扫历史会话（幂等，只补没记过的轮次）
    2. miner.scan()   重新聚合候选看板

**只更新看板，绝不生成草稿、绝不发布** —— 定时任务只做"发现"，不做"决定"。
线程里全部 try/except：扫描失败不影响网关，也不影响对话。
"""
from __future__ import annotations

import threading
import time

from edu_agent.skill_market import miner, runs

_lock = threading.Lock()
_state: dict = {"thread": None, "stop": False, "last_run": 0.0,
                "last_result": {}, "runs": 0, "last_error": ""}
_TICK = 60.0          # 检查间隔（秒）


def interval_hours() -> float:
    try:
        return float(runs.config().get("scan_interval_hours") or 0)
    except (TypeError, ValueError):
        return 0.0


def run_once(*, sweep: bool = True, limit_sessions: int = 30) -> dict:
    """立刻扫一次（定时线程与手动接口共用）。"""
    out: dict = {"ok": True, "at": time.time()}
    try:
        if sweep:
            out["sweep"] = runs.sweep(limit_sessions=limit_sessions)
        out["scan"] = miner.scan()
        out["ok"] = True
    except Exception as e:  # noqa: BLE001
        out = {"ok": False, "at": time.time(), "error": "%s: %s" % (type(e).__name__, e)}
    with _lock:
        _state["last_run"] = time.time()
        _state["last_result"] = out
        _state["runs"] = int(_state.get("runs") or 0) + 1
        _state["last_error"] = "" if out.get("ok") else str(out.get("error") or "")
    return out


def _loop() -> None:
    # 启动后先等一小会儿再扫（别和网关启动抢资源）
    time.sleep(20.0)
    while not _state.get("stop"):
        try:
            hours = interval_hours()
            if hours > 0:
                due = _state["last_run"] + hours * 3600
                if time.time() >= due:
                    run_once()
        except Exception:  # noqa: BLE001
            pass
        for _ in range(int(_TICK)):
            if _state.get("stop"):
                return
            time.sleep(1.0)


def start() -> dict:
    """启动定时扫描（幂等）。interval=0 时不启动。"""
    with _lock:
        if _state.get("thread") is not None and _state["thread"].is_alive():
            return {"ok": True, "already": True, **status()}
        if interval_hours() <= 0:
            return {"ok": True, "disabled": True,
                    "hint": "scan_interval_hours=0，定时扫描关闭（可 POST /api/skill-create/config 打开）"}
        _state["stop"] = False
        th = threading.Thread(target=_loop, name="skill-create-scan", daemon=True)
        _state["thread"] = th
        th.start()
    return {"ok": True, "started": True, **status()}


def stop() -> dict:
    with _lock:
        _state["stop"] = True
    return {"ok": True, **status()}


def reset() -> dict:
    """**清空调度器进程级状态**（停线程 + 计数器归零）——测试隔离用。

    为什么需要：`_state` 是模块级全局，而"import web_server"曾经会顺手拉起这个线程，
    于是同一个测试进程里先跑的文件会影响后跑的调度器测试（实测：全量 920 用例时
    `test_scheduler_*` 两条失败、单独跑全绿）。web_server 那边已改成启动时才拉起，
    这里再补一个显式重置入口，让测试不依赖"谁先谁后"。
    """
    with _lock:
        _state["stop"] = True
        _state["thread"] = None
        _state["runs"] = 0
        _state["last_run"] = 0.0
        _state["last_result"] = {}
        _state["last_error"] = ""
    return {"ok": True, **status()}


def status() -> dict:
    hours = interval_hours()
    last = float(_state.get("last_run") or 0)
    nxt = (last + hours * 3600) if (last and hours > 0) else 0
    th = _state.get("thread")
    return {
        "ok": True,
        "interval_hours": hours,
        "running": bool(th is not None and th.is_alive()),
        "runs": int(_state.get("runs") or 0),
        "last_run": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(last)) if last else "",
        "next_run": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(nxt)) if nxt else "",
        "last_result": _state.get("last_result") or {},
        "last_error": _state.get("last_error") or "",
    }
