# -*- coding: utf-8 -*-
"""课件生成（PPT Master 引擎）的 Windows 侧转发器。

定位：把「课件页面数据 → 原生可编辑 .pptx」接进教育 agent 的能力面。
引擎是**仓库外的第三方 skill 包** ppt-master（默认 D:\\hermes\\ppt-master），
与 capabilities.py 里 wps-office「第三方装在仓库外」的惯例一致；本模块只做编排：
  1) 解析引擎/工作区位置（环境变量 → 默认）
  2) Windows 路径 ↔ /mnt/x 路径换算（直接复用 wsl_docx 的实现，不重复造）
  3) 起 WSL 里的 scripts/wsl_ppt_bridge.py，喂 JSON、收 JSON

为什么要走 WSL（与 wsl_docx.py 同因）：本机 SAC 拦 lxml → python-pptx / ppt-master 的
导出链路在 Windows 侧跑不起来；SAC 管不到 WSL。

用法：
    from edu_agent import ppt_master
    ppt_master.available()                     # -> (bool, 说明)
    ppt_master.generate(pages=[...], name="牛顿第一定律")   # -> {"ok":True,"pptx":"D:\\...pptx",...}
    ppt_master.verify("D:\\...pptx")            # -> 原生性自检（形状数/图片数/字体）

pages 结构（每页一个 dict）：
    {"layout": "cover",   "title": ..., "subtitle": ...}
    {"layout": "section", "title": ..., "subtitle": ...}
    {"layout": "cards",   "title": ..., "items": [{"heading":..., "body":[行, 行]}]}   # 最多 3 张卡
    {"layout": "bullets", "title": ..., "items": [行, 行, ...]}                        # 最多 6 条
预留但未验证的扩展（写法见 D:\\hermes 文档）：布局标签与配色可由 style 覆盖。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

from edu_agent.wsl_docx import native_ok, to_win, to_wsl

REPO = Path(__file__).resolve().parents[2]
SHIM = REPO / "scripts" / "wsl_ppt_bridge.py"
DEFAULT_ENGINE = os.environ.get("EDU_PPT_MASTER_ROOT", r"D:\hermes\ppt-master")
DEFAULT_DISTRO = os.environ.get("EDU_WSL_DISTRO", "Ubuntu")
DEFAULT_PY = os.environ.get("EDU_WSL_PYTHON", "python3")
TIMEOUT = float(os.environ.get("EDU_PPT_TIMEOUT", "900"))     # 一次生成含质量门，给足
CANVAS = ("ppt169", "ppt43")

_FIX = None


def _fix_paths(value):
    """把 WSL 返回结果里的 /mnt/x/... 换回 Windows 路径（复用 wsl_docx 的递归实现）。"""
    global _FIX
    if _FIX is None:
        from edu_agent import wsl_docx

        _FIX = wsl_docx._fix_paths            # noqa: SLF001 同仓复用，避免两份换算逻辑
    return _FIX(value)


def engine_root() -> Path:
    """ppt-master 引擎根目录（仓库外，含 skills/ppt-master）。"""
    return Path(DEFAULT_ENGINE)


def workspace() -> Path:
    """课件项目的落地目录；默认放仓库 data/ 下（不进 git）。"""
    env = (os.environ.get("EDU_PPT_WORKSPACE") or "").strip()
    if env:
        return Path(env)
    try:
        from edu_agent.config import load_settings

        return Path(load_settings().data_dir) / "ppt_courses"
    except Exception:  # noqa: BLE001  配置异常不能阻断生成
        return Path(r"D:\hermes\education-ppt")


def backend() -> str:
    """用哪个后端：'wsl'（默认）或 'native'（引擎与依赖都装在 Windows 侧时的后路）。

    2026-09-26 实测教训：**不要**按 native_ok() 自动切 native —— native_ok() 只看本进程能否
    import pptx，而引擎脚本要用的是**同一个解释器环境**里的 pptx。本机 .venv 里能 import 而
    系统 python（3.14.6）不能，自动切换会把「能跑」误判成「跑不了」。默认固定走 WSL，
    想用本机跑就显式设 EDU_PPT_BACKEND=native（那时用 sys.executable 解释器）。
    """
    forced = (os.environ.get("EDU_PPT_BACKEND") or "").strip().lower()
    return forced if forced in ("native", "wsl") else "wsl"


def _call(op: str, args: dict) -> dict:
    """起 WSL 跑一个 op。返回 {"ok": True, "value": ...} / {"ok": False, "error": "..."}。"""
    if not shutil.which("wsl"):
        return {"ok": False, "error": "找不到 wsl.exe（本机没装 WSL？）"}
    if not SHIM.exists():
        return {"ok": False, "error": f"缺少桥接脚本 {SHIM}"}
    req = {"op": op, "args": args}
    cmd = ["wsl.exe", "-d", DEFAULT_DISTRO, "--", DEFAULT_PY, str(to_wsl(SHIM))]
    try:
        r = subprocess.run(cmd, input=json.dumps(req, ensure_ascii=False), capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"WSL 侧超过 {TIMEOUT:.0f}s 未返回"}
    for line in reversed((r.stdout or "").strip().splitlines()):     # 只认最后一行 JSON
        line = line.strip()
        if line.startswith("{"):
            try:
                resp = json.loads(line)
            except json.JSONDecodeError:
                continue
            if resp.get("ok"):
                return {"ok": True, "value": _fix_paths(resp.get("value"))}
            return {"ok": False, "error": resp.get("error") or "WSL 侧返回 ok=false",
                    "value": _fix_paths(resp.get("value") or {})}
    err = (r.stderr or "").strip()[-400:] or (r.stdout or "").strip()[-400:]
    return {"ok": False, "error": f"WSL 侧无有效输出（exit={r.returncode}）: {err}"}


def _native(op: str, args: dict) -> dict:
    """原生后端：直接跑本机引擎脚本（仅当引擎与依赖都装在 Windows 侧时可用）。"""
    if not native_ok():
        return {"ok": False, "error": "本机解释器 import pptx 失败（SAC 拦 lxml？），"
                                      "课件生成请用默认的 WSL 后端（EDU_PPT_BACKEND=wsl）"}
    skill = engine_root() / "skills" / "ppt-master" / "scripts"
    bridge = REPO / "scripts" / "wsl_ppt_bridge.py"
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    req = dict(args)
    req.setdefault("engine", str(engine_root()))
    try:
        # 用当前解释器（sys.executable），不要用 which("python")：本机 PATH 上的 python 可能
        # 与跑本模块的不是同一个环境（实测 which("python")=3.14.6、.venv=3.11）。
        r = subprocess.run([sys.executable, str(bridge)],
                           input=json.dumps({"op": op, "args": req}, ensure_ascii=False),
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           env=env, timeout=TIMEOUT, cwd=str(skill))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    for line in reversed((r.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                resp = json.loads(line)
            except json.JSONDecodeError:
                continue
            return {"ok": bool(resp.get("ok")), "value": resp.get("value"),
                    "error": resp.get("error")}
    return {"ok": False, "error": (r.stderr or "")[-400:] or "无输出"}


def call(op: str, args: dict) -> dict:
    """按 backend() 分派一次桥接调用。"""
    if backend() == "native":
        args = dict(args)
        args.setdefault("engine", str(engine_root()))
        args.setdefault("workspace", str(workspace()))
        return _native(op, args)
    args = dict(args)
    args.setdefault("engine", to_wsl(engine_root()))
    args.setdefault("workspace", to_wsl(workspace()))
    return _call(op, args)


@lru_cache(maxsize=1)
def available() -> tuple[bool, str]:
    """引擎 + WSL 是否就绪（探一次并缓存）。"""
    if not SHIM.exists():
        return False, f"缺少桥接脚本 {SHIM}"
    resp = call("ping", {})
    if not resp.get("ok"):
        return False, str(resp.get("error"))
    v = resp.get("value") or {}
    if not v.get("engine_exists"):
        return False, f"引擎缺 SKILL.md：{v.get('engine')}（设 EDU_PPT_MASTER_ROOT 指到 ppt-master clone）"
    return True, f"就绪（python {v.get('python')} / python-pptx {v.get('pptx_version')}）"


def generate(pages: list[dict], name: str = "courseware", canvas: str = "ppt169",
             style: dict | None = None, out_dir: str | Path | None = None,
             template_id: str = "") -> dict:
    """课件页面数据 → 原生可编辑 .pptx。

    返回 {"ok": True, "pptx": "D:\\...", "project": ..., "pages": n, "gate": {...}}
    或   {"ok": False, "error": ..., "gate": {...}（质量门未过时）}
    """
    if not pages:
        return {"ok": False, "error": "pages 为空"}
    if canvas not in CANVAS:
        return {"ok": False, "error": f"未支持的 canvas：{canvas}（可选 {', '.join(CANVAS)}）"}
    args = {"pages": pages, "name": name, "canvas": canvas, "style": style or {},
            "template_id": str(template_id or "").strip()}
    if out_dir:
        args["workspace"] = to_wsl(out_dir)
    resp = call("ppt_generate", args)
    if not resp.get("ok"):
        return {"ok": False, "error": resp.get("error"), "gate": (resp.get("value") or {}).get("gate")}
    v = resp.get("value") or {}
    return {"ok": True, "pptx": v.get("pptx"), "project": v.get("project"),
            "pages": v.get("pages"), "gate": v.get("gate"),
            "dropped_items": v.get("dropped_items", 0), "backend": backend(),
            "template_id": v.get("template_id"), "template_name": v.get("template_name")}


def compile_project(project: str | Path) -> dict:
    """对已有项目目录（手写 SVG / 深改过的页面）走同一条质量门 + 导出。"""
    resp = call("ppt_compile", {"project": to_wsl(project)})
    if not resp.get("ok"):
        return {"ok": False, "error": resp.get("error"), "gate": (resp.get("value") or {}).get("gate")}
    v = resp.get("value") or {}
    return {"ok": True, "pptx": v.get("pptx"), "project": v.get("project"),
            "gate": v.get("gate"), "backend": backend()}


def verify(pptx: str | Path) -> dict:
    """原生性自检：形状数 / 图片数 / 中文字体 / 嵌入媒体。"""
    resp = call("ppt_verify", {"pptx": to_wsl(pptx)})
    if not resp.get("ok"):
        return {"ok": False, "error": resp.get("error")}
    return resp.get("value") or {}
