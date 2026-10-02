# -*- coding: utf-8 -*-
"""会话初始化：**决定这个会话加载哪些技能**。

> 约束 2：`isolation.bind()` **只在本模块出现** —— bind 是"某个会话这次加载哪些技能"，
> 属于会话初始化内部动作；`publish` 只管把技能落盘到全局 `data/skills` 并把状态改成 published，
> 它**不许**碰任何会话绑定（`tests/test_skill_mode.py` 有回归测试盯着这条）。

分支（约束 4）：

    SINGLE_USER_MODE=True   会话启动 → 自动加载**全部 status=published** 的 skill
                            （不查教师安装、不调 MCP 用户服务）
    SINGLE_USER_MODE=False  会话启动 → 调 MCP 用户服务取该教师**已安装**列表
                            → 只加载「本人已安装 ∧ status=published」的 skill

加载前还会核对"文件真的在运行时目录里"（`paths.is_inside_runtime`）：
注册表说 published、但磁盘上没有的技能会被**剔除并说明原因**，不做无声失败。
"""
from __future__ import annotations

import time

from edu_agent.skill_market import (isolation, mcp_user_service, mode, paths,
                                   registry_db, store)


def is_published(skill_id: str) -> tuple[bool, str]:
    """注册表状态 + 磁盘事实双重核对。"""
    row = registry_db.get(skill_id)
    if row is None:
        return False, "注册表里没有这个技能"
    if str(row.get("status")) != "published":
        return False, "状态是 %s（只有 published 才能加载）" % row.get("status")
    if not paths.is_inside_runtime(skill_id):
        return False, "文件不在运行时目录（data/skills）里"
    return True, "ok"


def published_skill_ids() -> tuple[list[str], list[dict]]:
    """全部 published（注册表）→ 逐条核对磁盘 → (可用, 被剔除)。"""
    ok_ids: list[str] = []
    dropped: list[dict] = []
    for row in registry_db.published():
        sid = str(row.get("skill_id") or "")
        good, why = is_published(sid)
        (ok_ids if good else dropped).append(
            sid if good else {"skill": sid, "reason": why})
    return ok_ids, dropped


def ensure_session(session_id: str, teacher_id: str = "", *,
                   force: bool = False) -> dict:
    """会话初始化时调一次：按当前模式把该会话该加载的技能绑好（幂等）。

    * 单用户调试：绑定**全部 published**；集合没变就跳过（不重复写盘）。
    * 多教师正式：先问 MCP 用户服务拿"该教师已安装"，与 published 取交集后绑定；
      **拿不到就拒绝加载并报错**（安全默认：宁可少加载，不越权多加载）。
    """
    sid = str(session_id or "").strip()
    if not sid:
        return {"ok": False, "error": "缺少 session_id"}
    single = mode.single_user_mode()
    mcp_info: dict = {"called": False}

    if single:
        ids, dropped = published_skill_ids()
        source = "single_user:all_published"
        tid = mode.teacher_id(teacher_id)          # 仅用于填字段，业务不依赖
        mcp_info = {"called": False, "reason": "单用户调试模式：不调用 MCP 用户服务"}
    else:
        tid = mode.teacher_id(teacher_id)
        if not tid:
            return {"ok": False, "gate": "teacher_id", "single_user_mode": False,
                    "error": "多教师模式必须提供 teacher_id（会话里带上）"}
        mcp_info = mcp_user_service.installed_skills(tid)
        mcp_info["called"] = True
        if not mcp_info.get("ok"):
            # 关键的安全默认：用户服务拿不到 → 不加载任何技能，并如实说明
            return {"ok": False, "gate": "mcp_user_service", "single_user_mode": False,
                    "teacher_id": tid, "loaded": [], "mcp": mcp_info,
                    "error": "拿不到该教师的已安装列表：%s" % mcp_info.get("error")}
        installed = set(mcp_info.get("skills") or [])
        ids, dropped = [], []
        for s in sorted(installed):
            good, why = is_published(s)
            if good:
                ids.append(s)
            else:
                dropped.append({"skill": s, "reason": why})
        source = "multi_user:mcp_installed"

    current = isolation.list_for(sid)
    if not force and current == ids:
        return {"ok": True, "already": True, "session_id": sid, "single_user_mode": single,
                "teacher_id": tid, "source": source, "loaded": ids, "dropped": dropped,
                "mcp": mcp_info}

    bound = isolation.bind(sid, ids, replace_all=True)   # ← bind 只在这里出现
    return {"ok": True, "already": False, "session_id": sid, "single_user_mode": single,
            "teacher_id": tid, "source": source,
            "loaded": bound.get("skills") or [], "rejected": bound.get("rejected") or [],
            "dropped": dropped, "mcp": mcp_info,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S")}


def visible_skills(teacher_id: str = "") -> dict:
    """市场可见性分支（约束 4）。**不做前端**，只给数据。"""
    single = mode.single_user_mode()
    tid = mode.teacher_id(teacher_id)
    rows = registry_db.visible_for(tid, single_user=single)
    # 补上磁盘上的实时信息（安装状态/可加载性），方便市场页直接渲染
    out = []
    for r in rows:
        sid = str(r.get("skill_id") or "")
        loadable, why = store.is_loadable(sid) if str(r.get("status")) == "published" \
            else (False, "非 published 状态")
        out.append({**r, "installed": bool(paths.is_inside_runtime(sid)),
                    "loadable": loadable, "loadable_reason": why})
    return {"ok": True, "single_user_mode": single, "teacher_id": tid,
            "scope": ("全部草稿 + 已发布" if single else "已发布 + 本人创建的草稿"),
            "count": len(out), "skills": out}


def describe() -> dict:
    """给接口/自检看的分支快照。"""
    ids, dropped = published_skill_ids()
    return {"ok": True, "mode": mode.describe(),
            "loadable_published": ids, "dropped_published": dropped,
            "mcp_user_service": mcp_user_service.health(),
            "registry": registry_db.stats()}
