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


def is_agent_loadable(skill_id: str) -> tuple[bool, str]:
    """**会话加载**用的判据：Agent 现在到底能不能加载它。

    判据 = 已安装 ∧ 运行时文件存在 ∧ 能力开关没关（见 `store.is_loadable()`），
    **不看市场状态位**。为什么必须这样：

        从已发布技能派生新草稿继续编辑时，状态位会变回 `draft` —— 会话加载若也看状态位，
        那么"老师只是想改一版草稿"就会把这个技能从所有会话里摘掉，
        等于**改个草稿把线上打挂**。
        （实测：派生草稿后 `store.is_loadable()` 说 True、会话却加载到空列表，两处判据打架。）

    停用（`disable`）会把 `installed` 置为 False，所以停用照旧被排除 ✅。
    市场语义的"已发布"仍由 `is_published()` 提供（出团队库策略用它）。
    """
    ok, why = store.is_loadable(skill_id)
    if not ok:
        return False, why
    if not paths.is_inside_runtime(skill_id):
        return False, "文件不在运行时目录（data/skills）里"
    return True, "ok"


def published_skill_ids() -> tuple[list[str], list[dict]]:
    """**当前可被 Agent 加载**的技能 id 列表（注册表 installed 行 → 逐条核对）。

    名字保留 `published_skill_ids` 以免破坏既有调用方；语义见 `is_agent_loadable`：
    判据是"装没装 + 文件在不在 + 能力开关"，不是"市场状态恰好是 published"。
    """
    ok_ids: list[str] = []
    dropped: list[dict] = []
    for row in registry_db.list_skills():
        sid = str(row.get("skill_id") or "")
        installed = bool(row.get("installed"))
        says_published = str(row.get("status")) == "published"
        if not installed and not says_published:
            continue                       # 从没发布过（或已被停用）→ 根本不是候选，不必报
        good, why = is_agent_loadable(sid)
        if good:
            ok_ids.append(sid)
        else:
            # 装过 / 或注册表还写着 published，但现在加载不了 → **如实报出来**
            # （典型：状态 published 但运行时文件被手工删了；这种不该静默消失）
            dropped.append({"skill": sid, "reason": why})
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
        # 单用户调试：加载**当前可被 Agent 加载**的全部技能
        # （= 已安装 ∧ 文件在 ∧ 能力开关没关；不看市场状态位，见 is_agent_loadable）
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
            good, why = is_agent_loadable(s)       # 本人已安装 ∧ 真的能加载
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
