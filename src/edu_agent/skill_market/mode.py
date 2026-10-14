# -*- coding: utf-8 -*-
"""单用户调试模式 / 多用户正式模式 的**全局开关**。

    SINGLE_USER_MODE = True   当前单用户自用调试（默认）
    SINGLE_USER_MODE = False  后期多教师正式模式

开关决定两件事（**只影响分支，不影响任何既有能力**）：

| | True（单用户调试） | False（多教师正式） |
|---|---|---|
| 会话加载 | 自动加载**全部 status=published** 的 skill | 调 MCP 用户服务取该教师**已安装**列表，只加载「本人已安装 ∧ published」 |
| 市场可见 | 全部草稿 + 已发布（谁都能看，因为只有你一个人） | 已发布 + **本人创建的**草稿 |
| 教师安装逻辑 | 不用 | 用（`skill_installs` 表） |
| MCP 用户服务 | **不调用** | 调用 |

取值优先级（高 → 低）：

    1. 环境变量 EDU_SINGLE_USER_MODE（部署时用，`1/true/yes/on` 为真，`0/false/no/off` 为假）
    2. <data_dir>/skill_market/mode.json 的 single_user_mode
    3. 默认 True（**单用户调试是默认**，避免误开多用户把加载面放大）

改开关不需要重启：每次判断都现读（与技能目录同一哲学）。
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from edu_agent.skill_market import paths

ENV_KEY = "EDU_SINGLE_USER_MODE"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}

_DEFAULTS: dict = {
    "single_user_mode": True,
    # 单用户模式下"我"是谁（写进 create_teacher_id，字段必须留、值可以是占位）
    "debug_teacher_id": "local-teacher",
    # MCP 用户服务：代码完整保留，现阶段未接线 → 多用户模式下会明确报"未连接"
    "mcp_user_service": {
        "enabled": False,
        "transport": "http",          # http | mcp
        "base_url": "",               # 例如 http://127.0.0.1:5190
        "mcp_server_id": "user-service",
        "timeout": 8.0,
    },
}


def config_file() -> Path:
    return paths.market_root() / "mode.json"


def config() -> dict:
    """完整配置（文件缺失/损坏就回默认，绝不让开关层把应用卡死）。"""
    cfg = json.loads(json.dumps(_DEFAULTS))       # deep copy
    p = config_file()
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k, v in data.items():
                    if k == "mcp_user_service" and isinstance(v, dict):
                        cfg["mcp_user_service"].update(v)
                    elif k in cfg:
                        cfg[k] = v
        except (ValueError, OSError):
            pass
    return cfg


def _write(cfg: dict) -> dict:
    paths.ensure_dirs()
    p = config_file()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    return cfg


def single_user_mode() -> bool:
    """当前是不是单用户调试模式（环境变量优先）。"""
    env = (os.getenv(ENV_KEY) or "").strip().lower()
    if env in _TRUE:
        return True
    if env in _FALSE:
        return False
    return bool(config().get("single_user_mode", True))


def mode_source() -> str:
    """这个判断是从哪来的（排查用）。"""
    env = (os.getenv(ENV_KEY) or "").strip().lower()
    if env in _TRUE or env in _FALSE:
        return "env:%s" % ENV_KEY
    return "file:%s" % config_file()


def set_single_user_mode(on: bool) -> dict:
    """切换模式（写文件；环境变量若已设则环境变量仍然优先，会在返回值里提醒）。"""
    cfg = config()
    cfg["single_user_mode"] = bool(on)
    _write(cfg)
    env_set = (os.getenv(ENV_KEY) or "").strip().lower() in (_TRUE | _FALSE)
    return {"ok": True, "single_user_mode": single_user_mode(),
            "saved": bool(on), "source": mode_source(),
            "note": ("环境变量 %s 已设置，它优先于文件；要按文件生效请先取消该环境变量"
                     % ENV_KEY) if env_set and single_user_mode() != bool(on) else ""}


def teacher_id(explicit: str = "") -> str:
    """当前身份。

    * 单用户模式：返回 `debug_teacher_id`（默认 `local-teacher`），**仅用于填字段**，
      业务逻辑不依赖它（约束：现阶段可以不使用 teacher_id，但字段必须留）。
    * 多用户模式：必须由会话显式传入；传空则返回空串，调用方应当拒绝而不是放开。
    """
    if explicit:
        return str(explicit).strip()
    if single_user_mode():
        return str(config().get("debug_teacher_id") or "local-teacher")
    return ""


def describe() -> dict:
    """给接口/自检看的快照（**不含前端**）。"""
    return {
        "ok": True,
        "single_user_mode": single_user_mode(),
        "source": mode_source(),
        "env_key": ENV_KEY,
        "debug_teacher_id": config().get("debug_teacher_id"),
        "mcp_user_service": config().get("mcp_user_service"),
        "behavior": {
            "session_load": ("自动加载全部 published" if single_user_mode()
                             else "按 MCP 用户服务的已安装列表加载（仅本人 ∧ published）"),
            "market_visibility": ("全部草稿 + 已发布" if single_user_mode()
                                  else "已发布 + 本人创建的草稿"),
            "install_logic": "不使用" if single_user_mode() else "使用（skill_installs）",
            "mcp_user_service": "不调用" if single_user_mode() else "调用",
        },
        "read_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
