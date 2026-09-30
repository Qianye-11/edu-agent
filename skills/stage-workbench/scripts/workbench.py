#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""个性化工作台技能入口（课本教练 skill：stage-workbench）。

链路：**参数 -> Kernel 调用 KernelFunction -> 结构化 JSON**

    python workbench.py --stage 初中 --json          # 主用法：拿该学段的工作台配置
    python workbench.py --list-stages                # 支持的学段
    python workbench.py --stage 初中 --module daily-practice
    python workbench.py --stage 高中 --summary
    python workbench.py --diff                       # 初中/高中差异
    python workbench.py --describe                   # 插件自描述（函数 + description）
    python workbench.py --selftest                   # 自检（两个学段都跑一遍）
    python workbench.py --engine direct --stage 初中  # 不用 SK，直接调插件方法

设计要点：
  * **模块化**：数据在 stage_config.py，能力在 workbench_plugin.py，编排与输出在本文件；
  * **通过 Kernel 调用**：默认走 `Kernel.invoke(plugin_name, function_name, **kwargs)`，
    `--engine direct` 则直接调方法 —— 两条路径返回同一份 JSON，便于对照排查；
  * **机器可读**：`--json` 时强制 stdout 为 UTF-8（Windows 管道默认 GBK，会让中文变乱码）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stage_config as sc            # noqa: E402
from workbench_plugin import SK_AVAILABLE, WorkbenchPlugin  # noqa: E402

PLUGIN_NAME = "stage_workbench"
SKILL_ID = "stage-workbench"
DEFAULT_STAGE = "高中"


def _console_safe() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:                                     # pragma: no cover
            pass


def _force_utf8_stdout() -> None:
    """JSON 输出必须 UTF-8：Windows 上管道里的 stdout 默认是 GBK。"""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                                         # pragma: no cover
        pass


def build_kernel(engine: str = "auto"):
    """构建 Kernel 并注册插件。

    返回 (kernel | None, engine_label)。engine="direct" 或 SK 不可用时返回 None，
    由调用方直接调插件方法 —— 保证「没有 SK 也能用」，只是少了 Kernel 那一层编排。
    """
    if engine == "direct":
        return None, "direct"
    try:
        import semantic_kernel
        from semantic_kernel import Kernel

        kernel = Kernel()
        kernel.add_plugin(WorkbenchPlugin(), plugin_name=PLUGIN_NAME)
        return kernel, "semantic-kernel " + getattr(semantic_kernel, "__version__", "?")
    except Exception as exc:                                  # noqa: BLE001
        if engine == "sk":
            raise
        print("[workbench] Semantic Kernel 不可用（%s），改用 direct 模式：%s"
              % (type(exc).__name__, exc), file=sys.stderr)
        return None, "direct"


def invoke(kernel, function_name: str, **kwargs) -> dict:
    """调用 KernelFunction（走 Kernel 或直接调方法），统一返回解析后的 dict。"""
    if kernel is not None:
        result = asyncio.run(kernel.invoke(plugin_name=PLUGIN_NAME,
                                           function_name=function_name, **kwargs))
        return json.loads(str(result))
    return json.loads(getattr(WorkbenchPlugin(), function_name)(**kwargs))


def _pick_function(args) -> tuple[str, dict]:
    """按命令行参数决定调用哪个 KernelFunction，以及它的参数。"""
    if args.list_stages:
        return "list_stages", {}
    if args.diff:
        return "diff_stages", {}
    if args.describe:
        return "describe_plugin", {}
    if args.module:
        return "get_module", {"stage": args.stage, "module_id": args.module}
    if args.summary:
        return "stage_summary", {"stage": args.stage}
    if args.compose:
        return "compose_workbench", {"stage": args.stage}
    return "get_workbench_config", {"stage": args.stage}


def mode_selftest(args) -> int:
    print("=== stage-workbench 自检 ===")
    print("1) 解释器      : %s" % sys.version.split()[0])
    print("2) 技能目录    : %s" % os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    print("3) SK 可导入   : %s" % ("是" if SK_AVAILABLE else "否（会走 direct 模式）"))
    kernel, label = build_kernel(args.engine)
    print("4) 引擎        : %s" % label)
    if kernel is not None:
        fns = [f"{pid}.{fid}" for pid, plug in kernel.plugins.items() for fid in plug.functions]
        print("5) Kernel 函数 : %s" % ", ".join(sorted(fns)))
        print("   函数描述示例: %s" % (
            kernel.plugins[PLUGIN_NAME].functions["get_workbench_config"].description[:60] + "…"))

    errs = sc.validate()
    print("6) 数据表校验  : %s" % ("通过" if not errs else "发现问题 " + "; ".join(errs[:3])))

    print("7) 两个学段分别取配置：")
    seen = {}
    for stage in sc.STAGE_ORDER:
        res = invoke(kernel, "get_workbench_config", stage=stage)
        cfg = res.get("config") or {}
        seen[stage] = {m["id"] for m in cfg.get("modules", [])}
        print("   %s -> %d 个模块  主题=%s  问候=%s"
              % (stage, cfg.get("module_count", 0), cfg.get("theme", {}).get("tone"),
                 (cfg.get("greeting") or "")[:18]))
    same = seen.get("初中") == seen.get("高中")
    print("   两学段模块是否相同: %s（应为 False）" % same)

    print("8) 辅助函数    :", end=" ")
    for fn, kw in (("list_stages", {}), ("diff_stages", {}),
                   ("get_module", {"stage": "初中", "module_id": "daily-practice"}),
                   ("stage_summary", {"stage": "高中"}), ("describe_plugin", {})):
        ok = bool(invoke(kernel, fn, **kw).get("ok"))
        print("%s=%s" % (fn, "OK" if ok else "!!"), end=" ")
    print()

    bad = bool(errs) or same
    print("=== %s ===" % ("自检未通过 [!!]" if bad else "自检通过 [OK]"))
    return 1 if bad else 0


def main(argv=None) -> int:
    _console_safe()
    p = argparse.ArgumentParser(
        prog="workbench.py",
        description="个性化工作台：按学段（初中/高中）返回对应的工作台模块配置（Semantic Kernel 实现）")
    p.add_argument("--stage", default=DEFAULT_STAGE, help="学段：初中/高中（也接受初一/高一等写法）")
    p.add_argument("--module", help="取单个模块详情（模块 id 或中文名）")
    p.add_argument("--list-stages", action="store_true", help="列出支持的学段")
    p.add_argument("--summary", action="store_true", help="只要一句话概述")
    p.add_argument("--diff", action="store_true", help="初中/高中差异对比")
    p.add_argument("--compose", action="store_true", help="配置 + 概述一起返回")
    p.add_argument("--describe", action="store_true", help="插件自描述（函数与 description）")
    p.add_argument("--engine", choices=("auto", "sk", "direct"), default="auto",
                   help="执行引擎：auto（有 SK 就用）/ sk（强制）/ direct（不用 SK）")
    p.add_argument("--json", action="store_true", help="以 JSON 输出（平台调用时使用）")
    p.add_argument("--selftest", action="store_true", help="自检")
    args = p.parse_args(argv)

    if args.json:
        _force_utf8_stdout()

    if args.selftest:
        return mode_selftest(args)

    kernel, label = build_kernel(args.engine)
    fn_name, kwargs = _pick_function(args)
    try:
        data = invoke(kernel, fn_name, **kwargs)
    except Exception as exc:                                  # noqa: BLE001
        out = {"ok": False, "skill": SKILL_ID, "function": fn_name, "engine": label,
               "error": "%s: %s" % (type(exc).__name__, exc)}
        print(json.dumps(out, ensure_ascii=False) if args.json
              else "[workbench] 出错：%s" % out["error"])
        return 1

    data.setdefault("ok", True)
    data["skill"] = SKILL_ID
    data["function"] = fn_name
    data["engine"] = label
    data["requested_stage"] = args.stage

    if args.json or not sys.stdout.isatty():
        print(json.dumps(data, ensure_ascii=False))
        return 0 if data.get("ok") else 1

    # 交互/人看：先给个摘要，再给完整 JSON
    cfg = data.get("config") or {}
    if cfg:
        print("%s 工作台（%s）· 引擎 %s" % (cfg.get("display_name"), cfg.get("grade_hint"), label))
        print("风格：%s  模块 %d 个" % (cfg.get("theme", {}).get("tone"), cfg.get("module_count", 0)))
        for m in cfg.get("modules", []):
            print("  [%s] %-10s %s" % (m.get("icon"), m.get("title"), m.get("desc")[:40]))
        print()
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0 if data.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
