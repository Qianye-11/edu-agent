#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WSL 侧桥接：把「课件数据 → 原生可编辑 PPTX」在 Linux 里跑完。

为什么要它：George 这台机器的 Windows「智能应用控制」(SAC) 会拦 lxml 的 etree.pyd，
而 ppt-master 的核心导出链路（python-pptx / svg_quality / svg_to_pptx）都依赖它，
于是 Windows 侧生成 .pptx 直接失败。SAC 管不到 WSL，所以：**Windows 只做编排
（拼页面数据、传参、收结果），真正的 .pptx 渲染丢进 WSL**。与 scripts/wsl_docx_bridge.py
同一形状、同一协议（stdio，一行一个 JSON）。

引擎：ppt-master（第三方 skill 包，装在仓库外，默认 /mnt/d/hermes/ppt-master）。
  - 只借它的「编译器层」：finalize_svg → svg_quality_checker（质量门）→ svg_to_pptx（原生 DrawingML）
  - 不搬它的提示词层；页面 SVG 由本脚本用固定版式确定性渲染（不靠模型手写 SVG）

协议（stdio，一行一个 JSON）：
  请求  {"op": "ping"|"ppt_generate"|"ppt_compile"|"ppt_verify", "args": {...}}
  响应  {"ok": true, "value": {...}}  |  {"ok": false, "error": "..."}

op:
  ping        {}                                   → {ready, python, engine, pptx_version, ...}
  ppt_generate{name, pages:[...], workspace?, canvas?, style?, engine?}
              → {pptx, project, pages, gate:{errors,warnings,report}, log}
  ppt_compile {project, engine?}                   → 同上（对已有项目里的手写 SVG 走同一条门）
  ppt_verify  {pptx}                               → 原生性自检（形状数/图片数/字体/嵌入媒体）

路径约定：Windows 侧负责换算（`D:\\a\\b` ↔ `/mnt/d/a/b`），本脚本只认 POSIX 路径。
自测（在 WSL 里）：
  echo '{"op":"ping","args":{}}' | python3 /mnt/d/教育agent/scripts/wsl_ppt_bridge.py
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

DEFAULT_ENGINE = os.environ.get("EDU_PPT_ENGINE", "/mnt/d/hermes/ppt-master")
DEFAULT_WORKSPACE = os.environ.get("EDU_PPT_WORKSPACE", "/mnt/d/hermes/education-ppt")
STEP_TIMEOUT = float(os.environ.get("EDU_PPT_STEP_TIMEOUT", "600"))

# 画布：viewBox 与逻辑尺寸（与 ppt-master references/canvas-formats.md 对齐）
CANVAS = {
    "ppt169": (1280, 720),
    "ppt43": (1024, 768),
}

# 字号角色：**每个用到的字号都必须在此登记**，否则质量门会报
# 「undeclared font-size N (k occurrences) exceeds the sparse-display limit」。
SIZES = {
    "hero": 54,
    "section": 44,
    "title": 36,
    "card_title": 26,
    "body": 19,
    "caption": 16,
}

DEFAULT_PALETTE = {
    "background": "#FFFFFF",
    "surface": "#F1F5F9",
    "primary": "#1E293B",
    "secondary_text": "#475569",
    "accent": "#2563EB",
    "divider": "#CBD5E1",
}

# 这两套是 ppt-master 自带的真实 Layout 工作区，不是教育 agent 自己画的主题皮肤。
# 导出的页面保留 data-pptx-master / data-pptx-layout，因此 PowerPoint 里能看到可复用母版。
MASTER_TEMPLATES = {
    "presentation_core": {"folder": "presentation_core", "canvas": "ppt169", "label": "PPT Master 通用 16:9"},
    "presentation_core_43": {"folder": "presentation_core_43", "canvas": "ppt43", "label": "PPT Master 课堂 4:3"},
}

MASTER_LAYOUT_NAMES = {
    "01_title_slide.svg": ("title_slide", "Title Slide"),
    "02_title_content.svg": ("title_content", "Title and Content"),
    "11_three_card.svg": ("three_card", "Three-Card Synthesis"),
    "13_process_timeline.svg": ("process_timeline", "Process Timeline"),
    "12_three_card.svg": ("three_card", "Three-Card Synthesis"),
    "14_process_timeline.svg": ("process_timeline", "Process Timeline"),
}

FONT = "Microsoft YaHei"          # 本机 WSL 里已注册（Windows 字体拷入 ~/.local/share/fonts）

FORBIDDEN_ROWS = [
    "`mask`, `<style>`, `class`, external CSS, `<foreignObject>`, `textPath`, `@font-face`, "
    "`<animate*>`, `<set>`, `<script>` / event attributes, `<iframe>`",
    "HTML named entities in text; write typography as raw Unicode and escape XML reserved characters",
]


# ---------------------------------------------------------------- 工具
def _esc(s: object) -> str:
    """XML 文本转义（质量门禁止 HTML 实体，只允许 &amp; &lt; &gt; &quot; &apos;）。"""
    return (
        str(s if s is not None else "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _engine_root(raw: str | None) -> Path:
    """返回 ppt-master 的 clone 根（内含 skills/ppt-master）。"""
    root = Path(raw or DEFAULT_ENGINE).expanduser()
    if (root / "skills" / "ppt-master" / "SKILL.md").exists():
        return root
    if (root / "SKILL.md").exists():                 # 直接指到 skill 目录也认
        return root.parent.parent
    raise FileNotFoundError(f"找不到 ppt-master 引擎（SKILL.md）：{root}")


def _skill_dir(engine: Path) -> Path:
    d = engine / "skills" / "ppt-master"
    return d if d.exists() else engine


def _run(cmd: list[str], cwd: Path) -> tuple[int, str]:
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1")
    try:
        r = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=env, timeout=STEP_TIMEOUT)
    except subprocess.TimeoutExpired:
        return 124, f"超时（>{STEP_TIMEOUT:.0f}s）: {' '.join(cmd)}"
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


# ---------------------------------------------------------------- 版式渲染（确定性）
def _page_frame(w: int, h: int, page_role: str) -> list[str]:
    """根级静态页框：一条带 data-pptx-role=background 的背景矩形（页框不算内容单元）。"""
    return [f'  <rect id="page-background" data-pptx-role="background" x="0" y="0" '
            f'width="{w}" height="{h}" fill="{{background}}"/>']


def _svg(w: int, h: int, page_role: str, body: list[str], palette: dict) -> str:
    head = (f'<?xml version="1.0" encoding="UTF-8"?>\n'
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" '
            f'viewBox="0 0 {w} {h}" font-family="{FONT}" data-pptx-page-role="{page_role}">')
    text = "\n".join(body + ["</svg>"])
    for key, val in palette.items():
        text = text.replace("{" + key + "}", val)
    return head + "\n" + text + "\n"


def _template_tspans(lines: list[str], x: str, y: str, step: int = 42) -> str:
    """把多条课堂要点写进模板既有的 text carrier，保持它仍是一个原生可编辑占位符。"""
    try:
        y0 = float(y)
    except ValueError:
        y0 = 0
    out = []
    for i, line in enumerate(lines[:6]):
        text = _esc(line)
        out.append(f'<tspan x="{x}" y="{int(y0 + i * step)}">{text}</tspan>')
    return "".join(out) or "<tspan> </tspan>"


def _replace_template_body(svg: str, token: str, lines: list[str]) -> str:
    """仅替换模板里的文字 carrier 内容，不改它的 slot / master / layout 元数据。"""
    pattern = (r'(<text\\b[^>]*\\bx="(?P<x>[^"]+)"[^>]*\\by="(?P<y>[^"]+)"[^>]*>)'
               + re.escape(token) + r'(</text>)')
    return re.sub(pattern, lambda m: m.group(1) + _template_tspans(lines, m.group("x"), m.group("y")) + m.group(4), svg,
                  count=1, flags=re.S)


def _template_items(page: dict) -> list[str]:
    raw = page.get("items") or page.get("bullets") or page.get("blocks") or []
    out = []
    for item in raw:
        if isinstance(item, dict):
            label = str(item.get("label") or item.get("heading") or "").strip()
            text = item.get("text") or item.get("body") or ""
            if isinstance(text, (list, tuple)):
                text = " ".join(str(x) for x in text if str(x).strip())
            value = (label + ("：" if label and str(text).strip() else "") + str(text)).strip()
        else:
            value = str(item).strip()
        if value:
            out.append(value)
    return out[:6]


def _render_master_template(engine: Path, template_id: str, page: dict) -> tuple[str, str]:
    """用 ppt-master 的原生 Layout 原型填充一页课堂内容。

    模板固定视觉与结构仍由原 SVG 拥有；这里只写入其公开 placeholder。
    """
    meta = MASTER_TEMPLATES.get(template_id)
    if not meta:
        raise ValueError(f"未知 PPT Master 模板：{template_id}")
    root = engine / "skills" / "ppt-master" / "templates" / "layouts" / meta["folder"] / "templates"
    if not root.is_dir():
        raise FileNotFoundError(f"PPT Master 模板目录不存在：{root}")
    layout = str(page.get("layout") or "bullets")
    title = str(page.get("title") or page.get("name") or "课堂内容").strip()
    subtitle = str(page.get("subtitle") or page.get("label") or "课堂课件").strip()
    items = _template_items(page)
    if layout in {"cover", "title"}:
        source = "01_title_slide.svg"
        replacements = {"{{TITLE}}": title, "{{SUBTITLE}}": subtitle}
    elif layout == "cards" and len(items) >= 2:
        source = "11_three_card.svg" if template_id.endswith("_43") else "12_three_card.svg"
        replacements = {"{{PAGE_TITLE}}": title, "{{CARD_1}}": items[0] if items else "",
                        "{{CARD_2}}": items[1] if len(items) > 1 else "",
                        "{{CARD_3}}": items[2] if len(items) > 2 else ""}
    # 时间轴的每个 slot 仅容约 9 个中文字符；教学过程常有完整句，不能为追求版式
    # 而把文字塞溢出。短标签才用它，其他过程页仍使用该母版的正文 layout。
    elif ("过程" in title or "流程" in title) and 2 <= len(items) <= 4 and all(len(item) <= 9 for item in items):
        source = "13_process_timeline.svg" if template_id.endswith("_43") else "14_process_timeline.svg"
        replacements = {"{{PAGE_TITLE}}": title, "{{KEY_MESSAGE}}": subtitle}
        for i in range(4):
            replacements[f"{{{{STEP_{i + 1}}}}}"] = items[i] if i < len(items) else ""
    else:
        source = "02_title_content.svg"
        replacements = {"{{PAGE_TITLE}}": title}
    svg = (root / source).read_text(encoding="utf-8")
    if source == "02_title_content.svg":
        svg = _replace_template_body(svg, "{{CONTENT_AREA}}", items or [subtitle])
    for token, value in replacements.items():
        svg = svg.replace(token, _esc(value))
    return source, svg


def render_cover(page: dict, w: int, h: int, c: dict) -> str:
    """封面：主标题 + 副标题 + 左侧强调条。"""
    title = _esc(page.get("title") or "未命名课件")
    sub = _esc(page.get("subtitle") or "")
    body = [
        f'  <rect id="cover-accent" data-pptx-role="decoration" x="{int(w*0.05)}" y="{int(h*0.30)}" '
        f'width="8" height="{int(h*0.267)}" rx="4" fill="{{accent}}"/>',
        f'  <g id="cover-title-block" data-pptx-bounds="{int(w*0.0875)} 216 '
        f'{w - 2*int(w*0.0875)} 200">',
        f'    <text id="cover-title" x="{int(w*0.0875)}" y="{int(h*0.406)}" fill="{{primary}}" '
        f'font-size="{SIZES["hero"]}" font-weight="700">{title}</text>',
    ]
    if sub:
        body.append(f'    <text id="cover-subtitle" x="{int(w*0.0875)}" y="{int(h*0.546)}" '
                    f'fill="{{secondary_text}}" font-size="{SIZES["card_title"]}">{sub}</text>')
    body.append("  </g>")
    return _svg(w, h, "cover", body, c)


def render_section(page: dict, w: int, h: int, c: dict) -> str:
    """章节页：大标题 + 可选说明。"""
    title = _esc(page.get("title") or "")
    sub = _esc(page.get("subtitle") or "")
    body = [
        f'  <rect id="section-rule" data-pptx-role="decoration" x="{int(w*0.075)}" y="{int(h*0.41)}" '
        f'width="96" height="4" rx="2" fill="{{divider}}"/>',
        f'  <g id="section-title-block" data-pptx-bounds="{int(w*0.075)} {int(h*0.41)} {w-int(w*0.075)*2} 180">',
        f'    <text id="section-title" x="{int(w*0.075)}" y="{int(h*0.5)}" fill="{{primary}}" '
        f'font-size="{SIZES["section"]}" font-weight="700">{title}</text>',
    ]
    if sub:
        body.append(f'    <text id="section-subtitle" x="{int(w*0.075)}" y="{int(h*0.572)}" '
                    f'fill="{{secondary_text}}" font-size="{SIZES["body"]}">{sub}</text>')
    body.append("  </g>")
    return _svg(w, h, "section", body, c)


def _content_head(page: dict, w: int, c: dict) -> list[str]:
    title = _esc(page.get("title") or "")
    return [
        f'  <rect id="title-rule" data-pptx-role="decoration" x="{int(w*0.05)}" y="120" '
        f'width="96" height="4" rx="2" fill="{{divider}}"/>',
        f'  <g id="page-title-block" data-pptx-bounds="{int(w*0.05)} 40 {w-2*int(w*0.05)} 72">',
        f'    <text id="page-title" x="{int(w*0.05)}" y="90" fill="{{primary}}" '
        f'font-size="{SIZES["title"]}" font-weight="700">{title}</text>',
        f'  </g>',
    ]


def render_cards(page: dict, w: int, h: int, c: dict) -> str:
    """卡片页：1~3 张卡片，每张一个标题 + 最多 3 行正文。

    垂直排布按**实际正文行数**算卡片高度，再把整块居中在内容带里
    （2026-09-26 视觉核查发现固定高 380 会让卡片下半部大片留白）。"""
    items = [i for i in (page.get("items") or []) if isinstance(i, dict)][:3]
    body = _content_head(page, w, c)
    n = max(len(items), 1)
    margin = int(w * 0.05)
    gap = 24
    cw = int((w - 2 * margin - gap * (n - 1)) / n)
    band_top, band_h = 176, 456
    lines_max = 0
    for it in items:
        bl = it.get("body") or []
        if isinstance(bl, str):
            bl = [bl]
        lines_max = max(lines_max, min(len([l for l in bl if str(l).strip()]), 3))
    inner = SIZES["card_title"] + 8 + 52 + lines_max * 36      # 标题块 + 间距 + 正文行
    ch = int(min(380, max(280, inner + 84)))                   # 上下各留 ~42 的呼吸
    top = band_top + int((band_h - ch) / 2)
    for idx, it in enumerate(items, start=1):
        x = margin + (idx - 1) * (cw + gap)
        lines = it.get("body") or []
        if isinstance(lines, str):
            lines = [lines]
        lines = [l for l in lines if str(l).strip()][:3]
        pad = max(30, int((ch - inner) / 2))
        head_y = top + pad + SIZES["card_title"] + 8
        body += [
            f'  <g id="card-{idx}" data-pptx-bounds="{x} {top} {cw} {ch}">',
            f'    <rect id="card-{idx}-bg" x="{x}" y="{top}" width="{cw}" height="{ch}" rx="12" '
            f'fill="{{surface}}" stroke="{{divider}}" stroke-width="1"/>',
            f'    <text id="card-{idx}-heading" x="{x+32}" y="{head_y}" fill="{{primary}}" '
            f'font-size="{SIZES["card_title"]}" font-weight="700">{_esc(it.get("heading") or "")}</text>',
        ]
        for li, line in enumerate(lines):
            body.append(f'    <text id="card-{idx}-body-{li+1}" x="{x+32}" y="{head_y+52+li*36}" '
                        f'fill="{{secondary_text}}" font-size="{SIZES["body"]}">{_esc(line)}</text>')
        body.append("  </g>")
    return _svg(w, h, "content", body, c)


def render_bullets(page: dict, w: int, h: int, c: dict) -> str:
    """要点页：最多 6 条，每条一个圆点 + 一行文字（第 2 行缩进续行）。"""
    raw = page.get("items") or []
    flat: list[str] = []
    for it in raw:
        if isinstance(it, dict):
            head = str(it.get("heading") or "").strip()
            tail = it.get("body") or ""
            if isinstance(tail, (list, tuple)):
                tail = " ".join(str(t) for t in tail)
            flat.append((head + ("：" if head and str(tail).strip() else "") + str(tail)).strip())
        else:
            flat.append(str(it).strip())
    flat = [t for t in flat if t][:6]
    body = _content_head(page, w, c)
    x0, step = int(w * 0.07), 64
    band_top, band_h = 176, 456
    block = (max(len(flat), 1) - 1) * step + 40      # 条目少时整块居中，别全挤在上半部
    y0 = band_top + int((band_h - block) / 2) + 20
    body.append(f'  <g id="bullet-list" data-pptx-bounds="{x0-20} {y0-44} '
                f'{w-2*(x0-20)} {block + 30}">')
    for i, text in enumerate(flat, start=1):
        y = y0 + (i - 1) * step
        body += [
            f'    <circle id="bullet-dot-{i}" x="0" y="0" cx="{x0}" cy="{y-6}" r="5" fill="{{accent}}"/>',
            f'    <text id="bullet-text-{i}" x="{x0+28}" y="{y}" fill="{{secondary_text}}" '
            f'font-size="{SIZES["body"]}">{_esc(text)}</text>',
        ]
    body.append("  </g>")
    return _svg(w, h, "content", body, c)


LAYOUTS = {
    "cover": render_cover,
    "title": render_cover,        # 别名
    "section": render_section,
    "cards": render_cards,
    "bullets": render_bullets,
}


# ---------------------------------------------------------------- spec_lock / 项目骨架
def _lock_md(canvas: str, palette: dict, pages: list[dict]) -> str:
    w, h = CANVAS[canvas]
    roles = "\n".join(f"- {k}: {v}" for k, v in palette.items())
    sizes = "\n".join(f"- {k}: {v}" for k, v in SIZES.items())
    rhythm = "\n".join(
        f"- P{i:02d}: {'anchor' if (p.get('layout') in ('cover', 'title', 'section')) else 'dense'}"
        for i, p in enumerate(pages, start=1)
    )
    return f"""<!-- ppt-master-schema: spec-lock/v1 -->
# Execution Lock

## canvas
- viewBox: 0 0 {w} {h}
- format: {canvas}

## communication
- primary_language: zh-Hans-CN
- audience: 高中生与任课教师
- objective: 把教材内容呈现为可直接编辑的课堂课件
- core_message: 由页面标题承载

## mode
- mode: instructional

## visual_style
- visual_style: soft-rounded

## colors
{roles}

## typography
- font_family: {FONT}
- title_family: {FONT}
- body_family: {FONT}
{sizes}

## icons
- library: none
- inventory: none

## page_rhythm
{rhythm}

## pptx_structure
- mode: flat

## forbidden
""" + "\n".join(f"- {row}" for row in FORBIDDEN_ROWS) + "\n"


def _master_lock(canvas: str, palette: dict, pages: list[dict], template_id: str,
                 written: list[tuple[str, str]]) -> str:
    """为 ppt-master layout 工作区写完整的 structured 导出合同。

    这里的 role 数值与选中 SVG 的 carrier 一致；否则质量门会把模板的原生字号
    当成未声明的重复样式。template 原件也会复制进项目，供 exporter 注册 Layout。
    """
    meta = MASTER_TEMPLATES[template_id]
    master_key = f"{meta['folder']}_master"
    lock = _lock_md(canvas, palette, pages)
    old_type = "\n".join(f"- {k}: {v}" for k, v in SIZES.items())
    native_type = ("\n".join(["- cover_title: 46", "- subtitle: 22", "- title: 32",
                                "- body: 20", "- takeaway: 18", "- card_title: 20"])
                   if template_id.endswith("_43") else
                   "\n".join(["- cover_title: 54", "- subtitle: 26", "- title: 36",
                               "- body: 22", "- takeaway: 20", "- card_title: 22"]))
    lock = lock.replace(old_type, native_type)
    lock = lock.replace("- mode: flat", "- mode: structured\n- template_reuse_scope: layout\n- template_adherence: strict")
    master_name = "Presentation Core 4:3" if template_id.endswith("_43") else "Presentation Core"
    layouts: dict[str, tuple[str, str]] = {}
    for _, source in written:
        key, label = MASTER_LAYOUT_NAMES[source]
        layouts[key] = (label, source)
    lock += f"\n## pptx_masters\n- {master_key}: {master_name}\n\n## pptx_layouts\n"
    lock += "\n".join(f"- {key}: {master_key} | {label} | template:{Path(source).stem}"
                      for key, (label, source) in layouts.items()) + "\n"
    lock += "\n## page_pptx_layouts\n" + "\n".join(
        f"- P{i:02d}: {MASTER_LAYOUT_NAMES[source][0]}" for i, (_, source) in enumerate(written, 1)) + "\n"
    lock += "\n## page_layouts\n" + "\n".join(
        f"- P{i:02d}: {Path(source).stem}" for i, (_, source) in enumerate(written, 1)) + "\n"
    return lock


def _scaffold(project: Path) -> None:
    for d in ("sources", "svg_output", "svg_final", "exports", "images", "icons",
              "notes", "analysis", "live_preview", "templates", "validation"):
        (project / d).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- 三个步骤
def _gate(project: Path, skill: Path) -> dict:
    code, out = _run([sys.executable, str(skill / "scripts" / "svg_quality_checker.py"),
                      str(project), "--canonical-authoring", "--stage", "final", "--json"], skill)
    report = project / "validation" / "svg_quality_report.json"
    summary, files = {}, []
    if report.exists():
        try:
            data = json.loads(report.read_text(encoding="utf-8"))
            summary = data.get("summary") or {}
            for f in data.get("files") or []:
                files += list(f.get("errors") or [])[:3]
        except Exception:  # noqa: BLE001
            pass
    return {"exit": code, "errors": int(summary.get("errors") or 0),
            "warnings": int(summary.get("warnings") or 0),
            "report": str(report) if report.exists() else "",
            "details": files[:4], "tail": out[-600:]}


def _compile(project: Path, skill: Path) -> dict:
    steps = {}
    code, out = _run([sys.executable, str(skill / "scripts" / "finalize_svg.py"), str(project)], skill)
    steps["finalize"] = {"exit": code, "tail": out[-400:]}
    gate = _gate(project, skill)
    steps["gate"] = gate
    if gate["errors"]:
        return {"ok": False, "stage": "gate", "gate": gate,
                "error": "质量门未通过：" + ("；".join(gate["details"]) or f"{gate['errors']} 个错误")}
    code, out = _run([sys.executable, str(skill / "scripts" / "svg_to_pptx.py"), str(project)], skill)
    steps["export"] = {"exit": code, "tail": out[-800:]}
    if code != 0:
        return {"ok": False, "stage": "export", "gate": gate, "error": out[-600:], "steps": steps}
    exports = sorted((project / "exports").glob("*.pptx"), key=lambda p: p.stat().st_mtime)
    if not exports:
        return {"ok": False, "stage": "export", "gate": gate,
                "error": "导出过程没有产出 .pptx", "steps": steps}
    return {"ok": True, "pptx": str(exports[-1]), "gate": gate, "steps": steps,
            "log": steps["export"]["tail"]}


# ---------------------------------------------------------------- ops
def op_ping(args: dict) -> dict:
    engine = _engine_root(args.get("engine"))
    skill = _skill_dir(engine)
    try:
        import pptx  # noqa: F401
        pptx_v = pptx.__version__
    except Exception as exc:  # noqa: BLE001
        pptx_v = f"不可用（{exc}）"
    return {"ready": True, "python": sys.version.split()[0], "pptx_version": pptx_v,
            "engine": str(engine), "skill": str(skill),
            "engine_exists": (skill / "SKILL.md").exists(),
            "workspace": str(Path(args.get("workspace") or DEFAULT_WORKSPACE).expanduser())}


def op_ppt_generate(args: dict) -> dict:
    engine = _engine_root(args.get("engine"))
    skill = _skill_dir(engine)
    pages = [p for p in (args.get("pages") or []) if isinstance(p, dict)]
    if not pages:
        return {"ok": False, "error": "pages 为空"}
    template_id = str(args.get("template_id") or "").strip()
    template_meta = MASTER_TEMPLATES.get(template_id)
    canvas = template_meta["canvas"] if template_meta else str(args.get("canvas") or "ppt169")
    if canvas not in CANVAS:
        return {"ok": False, "error": f"未支持的 canvas: {canvas}（可选 {', '.join(CANVAS)}）"}
    w, h = CANVAS[canvas]
    palette = dict(DEFAULT_PALETTE)
    palette.update({k: v for k, v in (args.get("style") or {}).items() if k in DEFAULT_PALETTE})

    # 工程目录名会出现在下载文件名里 —— **允许中文**（中文路径在本机 WSL/Windows 两侧都验过：
    # 仓库本身就在 /mnt/d/教育agent）。只清掉路径分隔符/控制字符这类危险字符，并把连续横线压成一个
    # （2026-09-26 实测：早期 ASCII-only 清洗会把「3.3 幂函数」变成 `3-3----`，文件名很难看）。
    name = re.sub(r"[^\w-]+", "-", str(args.get("name") or "courseware"), flags=re.UNICODE)
    name = re.sub(r"-{2,}", "-", name).strip("-") or "courseware"
    workspace = Path(args.get("workspace") or DEFAULT_WORKSPACE).expanduser()
    workspace.mkdir(parents=True, exist_ok=True)
    # 每次生成独立工程。旧实现仅按日期命名，重复导出会让上一版 SVG 残留并混入新 PPT。
    stamp = _dt.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    project = workspace / f"{name}_{canvas}_{stamp}"
    _scaffold(project)

    written, dropped = [], 0
    for i, page in enumerate(pages, start=1):
        layout = str(page.get("layout") or "cards")
        if template_meta:
            source, svg = _render_master_template(engine, template_id, page)
            filename = f"P{i:02d}_{Path(source).stem}.svg"
            # structured exporter 从项目 templates/ 读取原型来建立真实 Master/Layout；
            # svg_output 则是填过内容的 Slide 实例。二者同源，不能用我们自己的简化版替代。
            template_src = (engine / "skills" / "ppt-master" / "templates" / "layouts"
                            / template_meta["folder"] / "templates" / source)
            (project / "templates" / source).write_text(template_src.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            fn = LAYOUTS.get(layout)
            if fn is None:
                return {"ok": False, "error": f"未知版式 {layout!r}（可选 {', '.join(sorted(LAYOUTS))}）"}
            if layout in ("cards",) and len(page.get("items") or []) > 3:
                dropped += len(page["items"]) - 3
            svg, filename = fn(page, w, h, palette), f"P{i:02d}_{layout}.svg"
        (project / "svg_output" / filename).write_text(svg, encoding="utf-8")
        written.append((filename, source) if template_meta else filename)
    lock = (_master_lock(canvas, palette, pages, template_id, written) if template_meta
            else _lock_md(canvas, palette, pages))
    (project / "spec_lock.md").write_text(lock, encoding="utf-8")
    (project / "README.md").write_text(
        f"# {name}\n\n- 由教育 agent 的 ppt_master 桥接生成（版式确定性渲染，非模型手写 SVG）\n"
        f"- 画布：{canvas}（{w}x{h}）\n- 页数：{len(pages)}\n", encoding="utf-8")

    out = _compile(project, skill)
    out.update({"project": str(project), "pages": len(written),
                "svg": [item[0] if template_meta else item for item in written],
                "dropped_items": dropped, "template_id": template_id or None,
                "template_name": template_meta["label"] if template_meta else None})
    return out


def op_ppt_compile(args: dict) -> dict:
    engine = _engine_root(args.get("engine"))
    skill = _skill_dir(engine)
    project = Path(str(args.get("project") or ""))
    if not project.is_dir():
        return {"ok": False, "error": f"项目目录不存在：{project}"}
    out = _compile(project, skill)
    out.update({"project": str(project)})
    return out


def op_ppt_verify(args: dict) -> dict:
    """原生性自检：.pptx 里到底是「可编辑形状」还是「整页图片」。"""
    import zipfile

    p = Path(str(args.get("pptx") or ""))
    if not p.is_file():
        return {"ok": False, "error": f"找不到文件：{p}"}
    z = zipfile.ZipFile(p)
    names = z.namelist()
    slides = [n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
    sp = pic = txt = prst = 0
    for s in slides:
        x = z.read(s).decode("utf-8", "replace")
        sp += x.count("<p:sp>")
        pic += x.count("<p:pic")
        txt += len(re.findall(r"<a:t>", x))
        prst += x.count("<a:prstGeom")
    media = [n for n in names if n.startswith("ppt/media/")]
    theme = ""
    if "ppt/theme/theme1.xml" in names:
        m = re.search(r'<a:majorFont>.*?<a:ea typeface="([^"]*)"', z.read("ppt/theme/theme1.xml").decode("utf-8", "replace"), re.S)
        theme = m.group(1) if m else ""
    return {"ok": True, "pptx": str(p), "bytes": p.stat().st_size,
            "slides": len(slides), "shapes": sp, "pictures": pic, "text_runs": txt,
            "preset_geometries": prst, "media_files": len(media), "east_asian_font": theme,
            "native_editable": bool(sp and not pic and not media)}


OPS = {
    "ping": op_ping,
    "ppt_generate": op_ppt_generate,
    "ppt_compile": op_ppt_compile,
    "ppt_verify": op_ppt_verify,
}


def main() -> int:
    raw = sys.stdin.read().strip()
    if not raw:
        print(json.dumps({"ok": False, "error": "空请求（stdin 没有 JSON）"}, ensure_ascii=False))
        return 0
    try:
        req = json.loads(raw.splitlines()[-1])
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"请求不是 JSON: {exc}"}, ensure_ascii=False))
        return 0
    op = str(req.get("op") or "")
    fn = OPS.get(op)
    if fn is None:
        print(json.dumps({"ok": False, "error": f"未知 op: {op!r}（可选 {', '.join(sorted(OPS))}）"},
                         ensure_ascii=False))
        return 0
    try:
        value = fn(req.get("args") or {})
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 0
    if isinstance(value, dict) and value.get("ok") is False:
        print(json.dumps({"ok": False, "error": value.get("error") or "生成失败",
                          "value": value}, ensure_ascii=False))
        return 0
    print(json.dumps({"ok": True, "value": value}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
