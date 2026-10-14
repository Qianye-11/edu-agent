# -*- coding: utf-8 -*-
"""单用户/多用户模式、注册表、会话加载分支的**数据接口**（不含任何前端页面）。

    GET  /api/skill/mode                      当前模式 + 行为差异说明
    POST /api/skill/mode                      切换 SINGLE_USER_MODE
    GET  /api/skill/bootstrap                 模式/注册表/MCP 用户服务总览
    GET  /api/skill/registry                  注册表结构（含四个必留字段）+ 统计
    GET  /api/skill/registry/skills           注册表行（可按 status / teacher_id 过滤）
    POST /api/skill/registry/migrate          把已有 state.json / 四区目录补登记（幂等）
    GET  /api/skill/visible                   市场可见性（单用户=全部；多用户=已发布+本人草稿）
    GET  /api/skill/session/{sid}/init        会话加载分支：按模式算出该会话该加载哪些技能
    POST /api/skill/session/{sid}/init        同上（可 force 重新绑定 / 传 teacher_id）
    GET  /api/skill/mcp-user-service          MCP 用户服务状态（未接线也如实报）
    POST /api/skill/mcp-user-service          接线配置（enabled/base_url/transport/…）
"""
from __future__ import annotations

from fastapi import APIRouter

from edu_agent.skill_market import (mcp_user_service, mode, registry_db,
                                   session_bootstrap)

router = APIRouter(prefix="/api/skill", tags=["skill-mode"])


def _err(e: Exception) -> dict:
    return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


@router.get("/mode")
def api_mode():
    """当前是单用户调试还是多教师正式，以及这个开关具体改变了什么行为。"""
    try:
        return mode.describe()
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/mode")
def api_set_mode(body: dict):
    """切换 SINGLE_USER_MODE。body: {"single_user_mode": true|false}

    注意：环境变量 `EDU_SINGLE_USER_MODE` 优先级高于本文件；若两者冲突，返回值里的
    `note` 会说明（避免"我改了却没生效"这种排查成本）。
    """
    try:
        if "single_user_mode" not in (body or {}):
            return {"ok": False, "error": "缺少 single_user_mode"}
        r = mode.set_single_user_mode(bool(body.get("single_user_mode")))
        r["describe"] = mode.describe()
        return r
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/bootstrap")
def api_bootstrap():
    """模式 / 注册表 / MCP 用户服务 / 可加载技能 的总览（运维一眼看全）。"""
    try:
        return session_bootstrap.describe()
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/registry")
def api_registry():
    """注册表结构快照：**确认四个必留字段都在**（skill_id/status/create_teacher_id/file_path）。"""
    try:
        st = registry_db.stats()
        st["ok"] = bool(st.get("schema", {}).get("ok"))
        return st
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/registry/skills")
def api_registry_skills(status: str = "", teacher_id: str = "", area: str = ""):
    try:
        rows = registry_db.list_skills(status=status, teacher_id=teacher_id, area=area)
        return {"ok": True, "count": len(rows), "skills": rows}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/registry/migrate")
def api_registry_migrate():
    """把已有的 state.json 与市场四区目录补登记进注册表（幂等，可随时重跑）。"""
    try:
        a = registry_db.migrate_from_state_file()
        b = registry_db.migrate_from_dirs()
        return {"ok": True, "from_state": a, "from_dirs": b,
                "stats": registry_db.stats()}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/registry/reconcile")
def api_registry_reconcile():
    """注册表对账：同步 installed 标志，列出"状态 published 但文件不在"的陈旧行。

    **不改 status**（状态是生命周期事实，磁盘缺失是运维事实，分开记）。
    """
    try:
        return registry_db.reconcile()
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/visible")
def api_visible(teacher_id: str = ""):
    """市场可见性分支：单用户=全部草稿+已发布；多用户=已发布+**本人创建的**草稿。"""
    try:
        return session_bootstrap.visible_skills(teacher_id)
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/session/{sid}/init")
def api_session_init(sid: str, teacher_id: str = "", force: int = 0):
    """会话加载分支：按当前模式算出该会话该加载哪些技能并绑定（幂等）。"""
    try:
        return session_bootstrap.ensure_session(sid, teacher_id, force=bool(force))
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/session/{sid}/init")
def api_session_init_post(sid: str, body: dict | None = None):
    try:
        b = body or {}
        return session_bootstrap.ensure_session(
            sid, str(b.get("teacher_id") or ""), force=bool(b.get("force")))
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/mcp-user-service")
def api_mcp_user_service():
    """MCP 用户服务状态。**代码完整保留**，是否调用由 SINGLE_USER_MODE 决定；
    现阶段未接线 → `connected=false` + 人话原因（页面保持未连接）。"""
    try:
        return mcp_user_service.health()
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/mcp-user-service")
def api_set_mcp_user_service(body: dict):
    """接线配置。body 例：{"enabled": true, "transport": "http",
    "base_url": "http://127.0.0.1:5190"} 或 {"transport": "mcp", "mcp_server_id": "user-service"}"""
    try:
        r = mcp_user_service.set_config(**(body or {}))
        r["health"] = mcp_user_service.health()
        return r
    except Exception as e:  # noqa: BLE001
        return _err(e)
