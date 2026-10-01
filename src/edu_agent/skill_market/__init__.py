# -*- coding: utf-8 -*-
"""模块 3 · Skill 市场与 Agent 自动封装（负责人 E；协作 G 安全/运维、B 接口与审计、F 市场 UI）。

一句话职责：**把"一次成功会话"变成"一个可审核、可发布、可回滚、且默认谁都装不上的 Skill"**。

本包只做后端业务逻辑与接口契约，**不含任何前端页面**（市场页由 F 实现，
数据由 `api.py` 的路由提供）。

公开入口（其余都是内部实现）：

    from edu_agent.skill_market import (manifest, draft, perm_scan, lifecycle,
                                        store, isolation, kernel_loader, audit,
                                        contract, capability_registry, paths)
    from edu_agent.skill_market.api import router        # FastAPI 路由（挂进 web_server）

各文件对应交付项：

    manifest.py            SKILL-01  front-matter 扩展（version/status/inputs/
                                    allowed_capabilities/data_scope/tests/owner…）
    draft.py               SKILL-02  成功会话 → 可编辑 SKILL.md 草稿（**不产 .py**）
    perm_scan.py           SKILL-03  静态权限扫描（未声明的网络/文件/执行 → 拒绝审核）
    audit.py               SKILL-04  追加式、脱敏、带哈希链的审计日志
    lifecycle.py           SKILL-05  草稿→提交→审核→发布/停用/回滚 状态机
    store.py / paths.py              四区存储（草稿/待审/已发布/归档）+ 安装卸载
    isolation.py                      **会话级 plugin 隔离**（每会话独立集合）
    kernel_loader.py                  Semantic Kernel `@kernel_function` 动态加载
    contract.py                       跨模块契约（knowledge_id/teaching_context/…）
    capability_registry.py            能力令牌白名单（复用 capabilities/permissions）
    api.py                            对外接口契约（SKILL-06 的数据面，UI 由 F 做）
"""
from __future__ import annotations

from edu_agent.skill_market import (  # noqa: F401
    audit,
    capability_registry,
    contract,
    create_api,
    draft,
    fixture,
    health,
    isolation,
    kernel_loader,
    lifecycle,
    manifest,
    miner,
    packager,
    paths,
    perm_scan,
    runs,
    scheduler,
    shadow,
    store,
    verifier,
)

__all__ = [
    "audit", "capability_registry", "contract", "create_api", "draft", "fixture",
    "health", "isolation", "kernel_loader", "lifecycle", "manifest", "miner",
    "packager", "paths", "perm_scan", "runs", "scheduler", "shadow", "store",
    "verifier",
]

# 版本号跟模块一起走（SKILL-01 的 version 是技能版本，这里是模块版本）
MODULE_VERSION = "3.0.0"
