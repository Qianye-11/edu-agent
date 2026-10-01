# -*- coding: utf-8 -*-
"""模块 3 · 会话级 plugin 隔离（"不能全局一次性加载全部市场 Skill"）。

规则（写进代码，不靠调用方自觉）：

1. **每个会话一份 plugin 集合**，落盘 `skill_market/sessions/<sid>.json`，
   进程内还有一层缓存；会话之间互相看不见。
2. `bind()` 必须**显式给 id**，没有"把市场里所有已发布技能都装上"的接口 ——
   从 API 形状上就杜绝了"全局一次性加载"。
3. `assert_loadable()` 是加载前的唯一入口判据，它同时要求：
   技能在该会话集合里 ∧ 全局状态是 published ∧ 文件在运行时目录 ∧ 能力开关为开。
   **草稿（draft/submitted/approved）永远返回 False**，这就是"草稿绝不能被 Agent 调用"。
4. 单会话技能数有上限（默认 8），防止某次绑定把整个市场塞进来。
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from edu_agent.skill_market import paths, store

MAX_PER_SESSION = 8
TTL_SECONDS = 6 * 3600          # 会话集合闲置 6 小时后可被 gc 回收

_lock = threading.RLock()
_cache: dict[str, dict] = {}


def _file(session_id: str) -> Path:
    safe = "".join(c for c in str(session_id) if c.isalnum() or c in "-_")[:64]
    return paths.sessions_dir() / (safe + ".json")


def _load(session_id: str) -> dict:
    key = str(session_id or "")
    if not key:
        return {"skills": [], "updated": 0.0}
    with _lock:
        hit = _cache.get(key)
        if hit is not None:
            return hit
        p = _file(key)
        row: dict = {"skills": [], "updated": 0.0}
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("skills"), list):
                    row = {"skills": [str(s) for s in data["skills"]],
                           "updated": float(data.get("updated") or 0.0)}
            except (ValueError, TypeError):
                row = {"skills": [], "updated": 0.0}
        _cache[key] = row
        return row


def _save(session_id: str, row: dict) -> None:
    paths.ensure_dirs()
    key = str(session_id or "")
    row = dict(row)
    row["updated"] = time.time()
    with _lock:
        _cache[key] = row
        p = _file(key)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)


def list_for(session_id: str) -> list[str]:
    """该会话当前绑定的技能 id（只读）。"""
    return list(_load(session_id).get("skills") or [])


def bind(session_id: str, skill_ids: list[str], *, replace_all: bool = False) -> dict:
    """给会话绑定技能集合。**必须显式给 id**；未发布/不可加载的会被剔除并说明原因。"""
    if not str(session_id or "").strip():
        return {"ok": False, "error": "session_id 不能为空（会话隔离的前提）"}
    want: list[str] = []
    rejected: list[dict] = []
    for sid in skill_ids or []:
        s = str(sid or "").strip()
        if not s or s in want:
            continue
        ok, why = store.is_loadable(s)
        if not ok:
            rejected.append({"skill": s, "reason": why})
            continue
        want.append(s)
    if len(want) > MAX_PER_SESSION:
        rejected += [{"skill": s, "reason": "超出单会话技能上限 %d" % MAX_PER_SESSION}
                     for s in want[MAX_PER_SESSION:]]
        want = want[:MAX_PER_SESSION]
    cur = [] if replace_all else list_for(session_id)
    merged = want if replace_all else list(dict.fromkeys(cur + want))
    if len(merged) > MAX_PER_SESSION:
        rejected += [{"skill": s, "reason": "超出单会话技能上限 %d" % MAX_PER_SESSION}
                     for s in merged[MAX_PER_SESSION:]]
        merged = merged[:MAX_PER_SESSION]
    _save(session_id, {"skills": merged})
    return {"ok": True, "session_id": session_id, "skills": merged,
            "rejected": rejected, "max_per_session": MAX_PER_SESSION}


def unbind(session_id: str, skill_id: str = "") -> dict:
    cur = list_for(session_id)
    if not skill_id:
        _save(session_id, {"skills": []})
        return {"ok": True, "session_id": session_id, "skills": []}
    left = [s for s in cur if s != skill_id]
    _save(session_id, {"skills": left})
    return {"ok": True, "session_id": session_id, "skills": left}


def assert_loadable(session_id: str, skill_id: str) -> tuple[bool, str]:
    """加载前唯一判据。**草稿一定过不了这一关。**"""
    if skill_id not in list_for(session_id):
        return False, "该技能没有绑定到本会话（会话级隔离：不同会话不共享 plugin 集合）"
    return store.is_loadable(skill_id)


def loadable_for(session_id: str) -> dict:
    """该会话**实际可加载**的技能（把绑定里失效的过滤掉，并说明原因）。"""
    out: list[dict] = []
    dropped: list[dict] = []
    for sid in list_for(session_id):
        ok, why = store.is_loadable(sid)
        (out if ok else dropped).append({"skill": sid, "reason": why})
    return {"session_id": session_id, "loadable": out, "dropped": dropped}


def gc(ttl: int = TTL_SECONDS) -> dict:
    """回收闲置会话集合（进程缓存 + 磁盘文件）。"""
    now = time.time()
    removed: list[str] = []
    with _lock:
        for key in list(_cache):
            if now - float(_cache[key].get("updated") or 0) > ttl:
                _cache.pop(key, None)
                removed.append(key)
    if paths.sessions_dir().exists():
        for p in paths.sessions_dir().glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if now - float(data.get("updated") or 0) > ttl:
                    p.unlink(missing_ok=True)
                    if p.stem not in removed:
                        removed.append(p.stem)
            except (ValueError, OSError, TypeError):
                continue
    return {"ok": True, "removed": removed, "count": len(removed)}


def stats() -> dict:
    files = list(paths.sessions_dir().glob("*.json")) if paths.sessions_dir().exists() else []
    return {"sessions": len(files), "cached": len(_cache), "max_per_session": MAX_PER_SESSION}
