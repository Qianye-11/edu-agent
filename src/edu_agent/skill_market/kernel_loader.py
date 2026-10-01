# -*- coding: utf-8 -*-
"""模块 3 · Semantic Kernel 动态加载（`@kernel_function` 是最终形态）。

两件事分开做，这是本文件最重要的设计决定：

* **看**（`describe_plugin_file`）：用 `ast` 静态解析 `plugin.py`，列出里面的
  `@kernel_function` 函数名/描述。**不 import、不执行** —— 所以市场详情页与审核页
  可以在"代码还没被信任"的前提下展示它到底暴露了哪些函数。
* **跑**（`load_skill_plugin`）：真正 import 并 `kernel.add_plugin(instance, plugin_name=skill_id)`。
  只有 `isolation.assert_loadable()` 放行的技能才会走到这里 —— 也就是
  该会话已绑定 ∧ 状态 published ∧ 文件在运行时目录 ∧ 能力开关为开。

顺带一个硬约束：**每个会话一个 Kernel**（`build_kernel(session_id)`），
不做"全局一个 Kernel 装完整个市场"那种事 —— 那正是技能串扰的来源。
"""
from __future__ import annotations

import ast
import asyncio
import importlib.util
import re
import sys
import time
from pathlib import Path
from typing import Any

from edu_agent.skill_market import isolation, manifest as mf, store

# Semantic Kernel 对 plugin 名有硬正则：^[0-9A-Za-z_]+$（**不允许连字符**）。
# 我们的技能 id 是 kebab-case（graph-node-question-draft），直接当 plugin_name 会炸：
#   FunctionInitializationError: Failed to create KernelFunctionMetadata
# （实测踩过）。所以这里做一次名字映射，并保留双向对照。
_SK_PLUGIN_RE = re.compile(r"^[0-9A-Za-z_]+$")


def plugin_name_for(skill_id: str) -> str:
    """技能 id → 合法的 SK plugin 名（连字符等一律换成下划线）。"""
    name = re.sub(r"[^0-9A-Za-z_]", "_", str(skill_id or ""))
    if not name:
        name = "skill"
    if name[0].isdigit():                 # SK 允许数字开头，但为了可读性统一加前缀
        name = "s_" + name
    return name


def skill_id_for(plugin_name: str, session_id: str = "") -> str:
    """反向解析：plugin 名 → 技能 id（先查本会话绑定的技能）。"""
    for sid in isolation.list_for(session_id):
        if plugin_name_for(sid) == plugin_name:
            return sid
    return plugin_name


def plugin_file(skill_id: str) -> Path | None:
    """运行时目录里该技能的插件文件（manifest 的 plugin 字段，默认 plugin.py）。"""
    from edu_agent.skill_market import paths

    d = paths.runtime_plugin_path(skill_id)
    text = d / "SKILL.md"
    rel = "plugin.py"
    if text.exists():
        man, _ = mf.from_document(text.read_text(encoding="utf-8", errors="replace"))
        rel = man.plugin or rel
    p = d / rel
    return p if p.exists() else None


def describe_plugin_file(path: Path) -> dict:
    """**静态**列出插件暴露的 kernel functions（AST，不执行代码）。"""
    p = Path(path)
    if not p.exists():
        return {"ok": False, "error": "插件文件不存在：%s" % p, "functions": []}
    try:
        tree = ast.parse(p.read_text(encoding="utf-8", errors="replace"), filename=str(p))
    except SyntaxError as e:
        return {"ok": False, "error": "插件语法错误：%s" % e, "functions": []}

    out: list[dict] = []

    def _decorated(node: ast.AST) -> tuple[bool, str, str]:
        name, desc = "", ""
        for dec in getattr(node, "decorator_list", []) or []:
            target = dec.func if isinstance(dec, ast.Call) else dec
            fname = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", "")
            if fname != "kernel_function":
                continue
            if isinstance(dec, ast.Call):
                for kw in dec.keywords:
                    if kw.arg == "name" and isinstance(kw.value, ast.Constant):
                        name = str(kw.value.value)
                    if kw.arg == "description" and isinstance(kw.value, ast.Constant):
                        desc = str(kw.value.value)
            return True, name, desc
        return False, "", ""

    for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
        for fn in [n for n in cls.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
            ok, name, desc = _decorated(fn)
            if not ok:
                continue
            doc = (ast.get_docstring(fn) or "").strip().splitlines()
            out.append({
                "class": cls.name,
                "function": name or fn.name,
                "python_name": fn.name,
                "description": desc or (doc[0] if doc else ""),
                "params": [a.arg for a in fn.args.args if a.arg != "self"],
                "async": isinstance(fn, ast.AsyncFunctionDef),
            })
    classes = [n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    return {"ok": True, "functions": out, "classes": classes,
            "kernel_function_count": len(out)}


def describe_skill(skill_id: str) -> dict:
    """市场详情页用：这个技能发布后会对 Kernel 暴露哪些函数（静态、不执行）。"""
    p = plugin_file(skill_id)
    if p is None:
        return {"ok": True, "skill": skill_id, "functions": [], "plugin": "",
                "note": "未声明 plugin（纯提示词技能），发布后不注册 kernel function"}
    got = describe_plugin_file(p)
    got.update({"skill": skill_id, "plugin": str(p)})
    return got


def _import_module(path: Path, skill_id: str):
    """按文件路径 import（模块名带技能 id，避免不同技能同名互相覆盖）。"""
    mod_name = "edu_skill_%s_%s" % (skill_id, abs(hash(str(path))) % 10 ** 8)
    spec = importlib.util.spec_from_file_location(mod_name, str(path))
    if spec is None or spec.loader is None:
        raise ImportError("无法加载插件：%s" % path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    return module


def _plugin_objects(module) -> list[Any]:
    """找出模块里带 @kernel_function 方法的类并实例化（无参构造）。"""
    objs: list[Any] = []
    for attr in dir(module):
        obj = getattr(module, attr)
        if not isinstance(obj, type) or obj.__module__ != module.__name__:
            continue
        has_kf = any(getattr(getattr(obj, m, None), "__kernel_function__", False)
                     for m in dir(obj))
        if not has_kf:
            continue
        try:
            objs.append(obj())
        except Exception:  # noqa: BLE001  需要构造参数的类跳过（不猜参数）
            continue
    return objs


def load_skill_plugin(kernel: Any, session_id: str, skill_id: str) -> dict:
    """把某个**已发布**技能加载进这个会话的 Kernel。草稿一律拒绝。"""
    ok, why = isolation.assert_loadable(session_id, skill_id)
    if not ok:
        return {"ok": False, "skill": skill_id, "error": why, "loaded": False}
    p = plugin_file(skill_id)
    if p is None:
        return {"ok": False, "skill": skill_id, "loaded": False,
                "error": "该技能没有 plugin 文件（纯提示词技能不能注册 kernel function）"}
    t0 = time.time()
    try:
        module = _import_module(p, skill_id)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "skill": skill_id, "loaded": False,
                "error": "插件导入失败：%s: %s" % (type(e).__name__, e)}
    loaded: list[str] = []
    pname = plugin_name_for(skill_id)
    for obj in _plugin_objects(module):
        try:
            kernel.add_plugin(obj, plugin_name=pname)
            loaded.append(obj.__class__.__name__)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "skill": skill_id, "loaded": False,
                    "error": "add_plugin 失败：%s: %s" % (type(e).__name__, e)}
    if not loaded:
        return {"ok": False, "skill": skill_id, "loaded": False,
                "error": "插件里没有找到带 @kernel_function 的类"}
    return {"ok": True, "skill": skill_id, "loaded": True, "plugin": str(p),
            "plugin_name": pname, "classes": loaded, "ms": int((time.time() - t0) * 1000)}


def build_kernel(session_id: str, *, kernel_factory=None,
                 skills: list[str] | None = None) -> dict:
    """为**一个会话**建 Kernel，只装该会话绑定的、可加载的技能。

    kernel_factory: 测试注入用（默认 `semantic_kernel.Kernel`）。
    """
    if kernel_factory is None:
        try:
            from semantic_kernel import Kernel
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": "semantic-kernel 未安装：%s" % e,
                    "session_id": session_id, "loaded": []}
        kernel_factory = Kernel
    kernel = kernel_factory()
    target = skills if skills is not None else isolation.list_for(session_id)
    loaded: list[dict] = []
    failed: list[dict] = []
    for sid in target:
        r = load_skill_plugin(kernel, session_id, sid)
        (loaded if r.get("ok") else failed).append(r)
    return {"ok": True, "session_id": session_id, "kernel": kernel,
            "loaded": loaded, "failed": failed,
            "note": "每个会话一个 Kernel；只装本会话绑定的技能"}


def invoke(kernel: Any, plugin_name: str, function_name: str, **kwargs) -> dict:
    """同步调用 kernel function（内部 asyncio.run；同步 def 由 FastAPI 丢线程池）。

    `plugin_name` 既接受技能 id（`graph-node-question-draft`）也接受已转换的 SK plugin 名
    —— 内部统一走 `plugin_name_for()`，调用方不必记两套名字。
    """
    t0 = time.time()
    pname = plugin_name if _SK_PLUGIN_RE.match(str(plugin_name or "")) else plugin_name_for(plugin_name)
    try:
        result = asyncio.run(kernel.invoke(plugin_name=pname,
                                           function_name=function_name, **kwargs))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e),
                "plugin": pname, "function": function_name}
    value = getattr(result, "value", result)
    return {"ok": True, "plugin": pname, "function": function_name,
            "value": value, "text": str(value), "ms": int((time.time() - t0) * 1000)}


def kernel_catalog(session_id: str) -> dict:
    """该会话 Kernel 里会有哪些 plugin/function（静态推导，不建 Kernel、不执行代码）。"""
    rows: list[dict] = []
    for sid in isolation.list_for(session_id):
        ok, why = store.is_loadable(sid)
        info = describe_skill(sid)
        rows.append({"skill": sid, "plugin_name": plugin_name_for(sid),
                     "loadable": ok, "reason": why,
                     "functions": info.get("functions", [])})
    return {"ok": True, "session_id": session_id, "plugins": rows}
