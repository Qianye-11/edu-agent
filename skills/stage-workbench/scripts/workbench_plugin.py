# -*- coding: utf-8 -*-
"""个性化工作台的 **Semantic Kernel 插件层**。

把「数据层」（stage_config.py）的能力包装成 KernelFunction —— **每个函数都带 name 与
description**，这样：
  * Kernel 内的函数目录（`kernel.plugins`）能自描述，未来交给模型做函数选择时无需额外文档；
  * 平台/CLI 都能按函数名调用，不用关心实现细节。

设计取舍：插件方法只返回**字符串**（JSON 文本）。这是 KernelFunction 最通用的返回形态，
也让「通过 Kernel 调用」与「SK 不可用时直接调用」两条路径行为一致。

依赖降级：Semantic Kernel 没装时，本模块仍可导入（用一个保留 name/description 的降级装饰器），
数据层与 CLI 的 `--engine direct` 路径照常可用 —— 不让「缺一个可选依赖」拖垮整个技能。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stage_config as sc  # noqa: E402

try:                                                    # pragma: no cover
    from semantic_kernel.functions import kernel_function
    SK_AVAILABLE = True
except Exception:                                       # noqa: BLE001
    SK_AVAILABLE = False

    def kernel_function(**meta):                        # type: ignore[misc]
        """SK 不可用时的降级装饰器：把元数据挂在函数对象上，行为与 SK 版本等价。"""
        def deco(fn):
            fn.__kernel_function_name__ = meta.get("name", fn.__name__)
            fn.__kernel_function_description__ = meta.get("description", "")
            return fn
        return deco


def _dumps(obj) -> str:
    """统一出口：所有 KernelFunction 都返回 JSON 字符串（UTF-8 中文不转义）。"""
    return json.dumps(obj, ensure_ascii=False)


class WorkbenchPlugin:
    """个性化工作台插件（按学段动态返回工作台模块配置）。

    函数一览（名字就是给模型/调用方看的接口）：
      * get_workbench_config(stage)  学段 -> 完整工作台配置（主入口）
      * list_stages()                支持的学段清单
      * get_module(stage, module_id) 单个模块详情
      * stage_summary(stage)         一句话概述该学段重点
      * diff_stages()                初中/高中差异摘要
      * compose_workbench(stage)     组合：配置 + 概述一次返回
    """

    # ---------------------------------------------------------- 主入口

    @kernel_function(
        name="get_workbench_config",
        description=(
            "按学段返回个性化工作台的完整模块配置：传入学段（初中/高中，"
            "也接受初一/高一/七年级等写法），返回该学段专属的模块列表、主题、侧边栏、"
            "快捷动作与布局建议（结构化 JSON，可直接交前端渲染）。"
        ),
    )
    def get_workbench_config(self, stage: str = "高中") -> str:
        """学段 -> 该学段专属的工作台界面配置。"""
        cfg = sc.get_config(stage)
        return _dumps({"ok": True, "action": "get_workbench_config",
                       "stage": cfg["stage"], "config": cfg})

    # ---------------------------------------------------------- 辅助函数

    @kernel_function(
        name="list_stages",
        description="列出本工作台支持的学段（初中/高中）及其年级范围、模块数量、界面风格与可识别别名。",
    )
    def list_stages(self) -> str:
        return _dumps({"ok": True, "action": "list_stages",
                       "stages": sc.list_stages(), "default": "高中"})

    @kernel_function(
        name="get_module",
        description=(
            "按学段与模块 id（或模块中文名）返回单个工作台模块的详情，"
            "例如 stage=初中、module_id=daily-practice。找不到时返回 found=false。"
        ),
    )
    def get_module(self, stage: str = "高中", module_id: str = "") -> str:
        hit = sc.get_module(stage, module_id)
        if not hit:
            return _dumps({"ok": False, "action": "get_module",
                           "reason": "该学段下没有这个模块",
                           "stage": sc.normalize_stage(stage), "module_id": module_id,
                           "available": [m["id"] for m in sc.get_config(stage)["modules"]]})
        return _dumps({"ok": True, "action": "get_module", "found": True, **hit})

    @kernel_function(
        name="stage_summary",
        description="用一句话概述某个学段工作台的重点模块与界面风格（适合做页面副标题或给模型的上下文）。",
    )
    def stage_summary(self, stage: str = "高中") -> str:
        return _dumps({"ok": True, "action": "stage_summary",
                       "stage": sc.normalize_stage(stage), "summary": sc.stage_summary(stage)})

    @kernel_function(
        name="diff_stages",
        description="对比初中与高中工作台：各自有哪些模块、界面风格差异、以及各自独有的模块 id。",
    )
    def diff_stages(self) -> str:
        return _dumps({"ok": True, "action": "diff_stages", **sc.diff_stages()})

    @kernel_function(
        name="compose_workbench",
        description=(
            "组合返回：该学段的完整配置 + 一句话概述（内部先取配置再取概述），"
            "适合一次性拿到前端渲染所需的全部内容。"
        ),
    )
    def compose_workbench(self, stage: str = "高中") -> str:
        cfg = json.loads(self.get_workbench_config(stage))
        cfg["summary"] = sc.stage_summary(stage)
        cfg["action"] = "compose_workbench"
        return _dumps(cfg)

    # ---------------------------------------------------------- 插件自描述

    @kernel_function(
        name="describe_plugin",
        description="返回本插件自身的函数清单与各自 description（自描述，便于排查与文档生成）。",
    )
    def describe_plugin(self) -> str:
        fns = []
        for name in dir(self):
            if name.startswith("_"):
                continue
            fn = getattr(self, name)
            desc = getattr(fn, "__kernel_function_description__", "")
            kname = getattr(fn, "__kernel_function_name__", name)
            if desc:
                fns.append({"name": kname, "method": name, "description": desc})
        return _dumps({"ok": True, "action": "describe_plugin",
                       "plugin": "stage_workbench", "functions": fns})
