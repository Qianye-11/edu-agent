# -*- coding: utf-8 -*-
"""MCP 用户服务客户端 —— **多教师正式模式**才用得到。

> ⚠️ 本模块的代码**完整保留、不许注释删除**（约束 5）。是否调用由 `SINGLE_USER_MODE` 决定：
>
> * `SINGLE_USER_MODE=True`（当前单用户调试）：**绝不调用** —— 会话加载直接取全部 published，
>   市场可见全部草稿+已发布，不涉及"教师安装"这套逻辑。
> * `SINGLE_USER_MODE=False`（后期多教师正式）：调用它拿"该教师**已安装**的 skill 列表"，
>   再与 published 取交集后加载；草稿只对创建者可见。
>
> 现阶段**未接线**（`enabled=false`、`base_url` 为空）→ 所有函数返回 `connected=False`。
> 调用方（`session_bootstrap`）按"未连接"**降级为拒绝加载并如实报错**，
> 而不是放开全部技能 —— 安全默认：连不上用户服务时宁可少加载，也不要越权多加载。

两条实现路径都留着，切换只改 `transport`：

| transport | 走法 |
|---|---|
| `mcp` | `mcp_tools.call_tool(mcp_server_id, "list_installed_skills", {...})`（MCP 工具） |
| `http` | `POST {base_url}/api/teacher/{teacher_id}/skills`（普通 HTTP） |
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request

from edu_agent.skill_market import mode


def config() -> dict:
    return dict(mode.config().get("mcp_user_service") or {})


def set_config(**changes) -> dict:
    """改 MCP 用户服务配置（接线时用：enabled/base_url/transport/mcp_server_id/timeout）。"""
    cfg = mode.config()
    svc = dict(cfg.get("mcp_user_service") or {})
    svc.update({k: v for k, v in changes.items() if v is not None})
    cfg["mcp_user_service"] = svc
    mode._write(cfg)          # noqa: SLF001  同模块族的内部写入口
    return {"ok": True, "mcp_user_service": svc}


def connected() -> tuple[bool, str]:
    """是否已接线。返回 (是否连接, 原因)。**未接线时给出人话原因**。"""
    cfg = config()
    if not cfg.get("enabled"):
        return False, "MCP 用户服务未启用（mcp_user_service.enabled=false，页面保持未连接）"
    if str(cfg.get("transport") or "http") == "http" and not str(cfg.get("base_url") or "").strip():
        return False, "MCP 用户服务未配置地址（mcp_user_service.base_url 为空）"
    return True, "ok"


# ---------------------------------------------------------------- 两条实现路径
def _call_mcp_tool(server_id: str, tool: str, args: dict, timeout: float) -> dict:
    """MCP 工具路径（走平台既有的 mcp_tools 客户端）。"""
    try:
        from edu_agent import mcp_tools

        raw = mcp_tools.call_tool(server_id, tool, args, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    if raw is None:
        return {"ok": False, "error": "MCP 工具无返回（服务未启动或工具名不对）"}
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
        return data if isinstance(data, dict) else {"ok": True, "data": data}
    except ValueError:
        return {"ok": False, "error": "MCP 工具返回不是 JSON", "raw": str(raw)[:300]}


def _call_http(path: str, payload: dict, timeout: float) -> dict:
    """HTTP 路径。"""
    cfg = config()
    base = str(cfg.get("base_url") or "").rstrip("/")
    if not base:
        return {"ok": False, "error": "未配置 base_url"}
    req = urllib.request.Request(
        base + path, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": "HTTP %s" % e.code,
                "body": e.read().decode("utf-8", "replace")[:300]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


# ---------------------------------------------------------------- 对外接口
def installed_skills(teacher_id: str) -> dict:
    """取某教师**已安装**的 skill 列表。

    返回：{ok, connected, teacher_id, skills:[...], source, error}
    **未接线时 ok=False、connected=False**，调用方必须按"拿不到"处理。
    """
    tid = str(teacher_id or "").strip()
    if not tid:
        return {"ok": False, "connected": False, "skills": [],
                "error": "多教师模式必须提供 teacher_id"}
    ok, why = connected()
    if not ok:
        return {"ok": False, "connected": False, "teacher_id": tid, "skills": [],
                "source": "unwired", "error": why,
                "hint": "接线方式：POST /api/skill/mode/mcp-user-service "
                        "{enabled:true, base_url:'http://…'} 或用 transport='mcp'"}
    cfg = config()
    timeout = float(cfg.get("timeout") or 8.0)
    if str(cfg.get("transport") or "http") == "mcp":
        got = _call_mcp_tool(str(cfg.get("mcp_server_id") or "user-service"),
                             "list_installed_skills", {"teacher_id": tid}, timeout)
        src = "mcp"
    else:
        got = _call_http("/api/teacher/%s/skills" % tid, {"teacher_id": tid}, timeout)
        src = "http"
    skills = got.get("skills") if isinstance(got, dict) else None
    if not isinstance(skills, list):
        return {"ok": False, "connected": True, "teacher_id": tid, "skills": [],
                "source": src, "error": "用户服务返回里没有 skills 列表",
                "raw": str(got)[:300]}
    return {"ok": True, "connected": True, "teacher_id": tid,
            "skills": [str(s) for s in skills], "source": src,
            "at": time.strftime("%Y-%m-%dT%H:%M:%S")}


def can_see_draft(teacher_id: str, row: dict) -> bool:
    """草稿可见性（多用户模式）：只有创建者本人能看见自己的草稿。"""
    if mode.single_user_mode():
        return True                     # 单用户调试：全部可见
    tid = str(teacher_id or "").strip()
    owner = str((row or {}).get("create_teacher_id") or "")
    return bool(tid) and bool(owner) and tid == owner


def health() -> dict:
    """给运维/自检看的状态（**不做任何调用**，避免每次探活都打网络）。"""
    ok, why = connected()
    return {"ok": True, "connected": ok, "reason": why, "config": config(),
            "single_user_mode": mode.single_user_mode(),
            "will_be_called": (not mode.single_user_mode()),
            "note": ("单用户调试模式：本服务不会被调用" if mode.single_user_mode()
                     else "多教师模式：会话初始化时会调用本服务取已安装列表")}
