# -*- coding: utf-8 -*-
"""版式数据化（R27b）：**新加版式走数据，渲染器只认数据**。

为什么这么切（`docs/29` §2.4）：
* 现有 6 个核心版式（cover/section/bullets/two-col/media/end）是**内容驱动**的排版逻辑
  （块数、换行、密度、留白都随内容算），把它们硬掰成静态数据会破坏"预览=导出"与已验收的观感；
* 而老师要的"不固化"要的是：**agent 能选注册表里那 86 个版式、甚至自定义版式**，不用我们改代码。
  ⇒ 所以：**核心 6 个保持代码不动（逐字节不变）**，**其余版式全部走数据**（`layout_data` 读注册表，
  用画布原语 rect/line/path/text 画装饰 + 往槽位里填字）。

数据从哪来：`content/ppt_templates/_design/layouts.json`（R27a 从 ppt-master 编译而来，含
`decorations`（layer=master/layout 的图元）与 `slots`（`data-pptx-placeholder` + `bounds` + 载体字号/字色））。

约束（锁死清单）：画布 1280×720；字号必须落在主题阶梯（越界**夹到最近的档**并记 warn）；
颜色一律取自主题 tokens（装饰里原版的硬编码色会被换成主题色，避免"别的主题配着别人的配色"）。
"""

from __future__ import annotations

import math
import re
from functools import lru_cache

from edu_agent import design_spec

#: 主题字号阶梯的键 → 画布 px（从 tokens 的 type 里取；这里是兜底）
_TYPE_FALLBACK = {"cover": 72, "title": 44, "label": 12, "body": 24, "caption": 15}

#: 注册表版式 id 的前缀（写进 slides 时用 `reg:` 前缀最稳；不带前缀也认，只要不在核心 6 个里）
PREFIX = "reg:"


@lru_cache(maxsize=1)
def registry() -> dict:
    """{版式 id: {family, id, name, canvas, slots, decorations}}。

    ⚠️ 两条实测过的坑：
    1. **版式 id 跨族重名**（`cover` / `title_content` …）⇒ 去重时**优先 16:9 主力族 `presentation_core`**，
       再按族名顺序；否则 `cover` 会命中 1:1 的 `moments_square`（画布不是 1280×720）。
    2. **只有 16:9 的族**（`presentation_core` / `presentation_core_43` / `report_core` /
       `editorial_bleed`）能直接画进我们的画布；其它族是 1:1 / 竖版 / 3:4（小红书），
       它们的槽位坐标会超出 720 高 —— 遇到这种版式**明确报错并指路**，别硬画出一张"半张纸"。
    """
    out: dict[str, dict] = {}
    fams = sorted(design_spec.layout_families(),
                  key=lambda f: (f["id"] != "presentation_core", f["id"]))
    for f in fams:
        for l in f["layouts"]:
            out.setdefault(l["id"], {"family": f["id"], **l})
    return out


def is_wide(canvas: list | tuple | None) -> bool:
    """是不是 16:9 画布（只有这种能直接进我们的 1280×720 页面）。"""
    if not canvas or len(canvas) != 2:
        return False
    try:
        w, h = float(canvas[0]), float(canvas[1])
    except (TypeError, ValueError):
        return False
    return h > 0 and abs(w / h - 16 / 9) < 0.02 and w >= 1200


def ids(*, wide_only: bool = True) -> tuple[str, ...]:
    """可用版式 id。

    `wide_only=True`（默认、也是我们支持的）：只列 **16:9** 的族 —— 我们的画布固定 1280×720，
    1:1 / 竖版 / 3:4 的族画进去会溢出（见 `registry()` 的说明）。
    """
    if not wide_only:
        return tuple(sorted(registry().keys()))
    return tuple(sorted(k for k, v in registry().items() if is_wide(v.get("canvas"))))


def is_registry_layout(layout: str) -> bool:
    lay = _strip_prefix(layout)
    spec = registry().get(lay)
    return bool(spec) and is_wide(spec.get("canvas"))


def _strip_prefix(layout: str) -> str:
    lay = str(layout or "")
    return lay[len(PREFIX):] if lay.startswith(PREFIX) else lay


def _num(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _clamp_size(tokens: dict, want: float | None) -> tuple[float, bool]:
    """把字号夹到主题阶梯里最接近的一档（越界就记 warn，不硬用小字）。"""
    steps = [float(v) for k, v in (tokens.get("type") or {}).items()
             if k in ("cover", "title", "label", "body", "caption") and isinstance(v, (int, float))]
    if not steps:
        steps = list(_TYPE_FALLBACK.values())
    if want is None:
        return float(tokens["type"]["body"]), False
    near = min(steps, key=lambda s: abs(s - want))
    return near, abs(near - want) > 0.5


def _wrap(text: str, width: float, size: float) -> list[str]:
    """按估算宽度折行（中文按 1 字宽、西文按 0.55 字宽估）——够用，且与核心版式同一套估法。"""
    if not text:
        return []
    out: list[str] = []
    for para in str(text).splitlines() or [""]:
        buf, w = "", 0.0
        for ch in para:
            cw = size * (1.0 if ord(ch) > 0x2E80 else 0.55)
            if w + cw > width and buf:
                out.append(buf)
                buf, w = "", 0.0
            buf += ch
            w += cw
        out.append(buf)
    return out


def _slot_text(slide: dict, placeholder: str, idx: int) -> str:
    """槽位 → 文字。`title` 类取标题，`object`/`body` 类取要点（多槽按顺序分配）。"""
    title = str(slide.get("cover_title") or slide.get("title") or slide.get("name") or "").strip()
    label = str(slide.get("label") or "").strip()
    blocks = [str(b.get("text") or b.get("name") or "").strip()
              for b in (slide.get("blocks") or []) if isinstance(b, dict)]
    bullets = [str(b).strip() for b in (slide.get("bullets") or []) if str(b).strip()]
    items = [t for t in (blocks or bullets) if t]
    cap = str(slide.get("caption") or "").strip()
    closing = str(slide.get("closing") or "").strip()
    ph = (placeholder or "").lower()
    if ph in ("title", "ctrtitle", "ctrTitle".lower()):
        return title or label
    if ph in ("subtitle", "subTitle".lower()):
        return str(slide.get("subtitle") or "").strip()
    if "caption" in ph:
        return cap or (items[idx] if idx < len(items) else "")
    if ph in ("object", "body", "content", "text", "placeholder"):
        return items[idx] if idx < len(items) else (cap if not idx else "")
    if "label" in ph or "kicker" in ph:
        return label
    return items[idx] if idx < len(items) else ""


def _content_bounds(spec: dict) -> tuple[float, float, float, float] | None:
    """源版式的"内容包围盒"（装饰 + 槽位），用来把坐标映射进我们的安全区。

    只算**有意义的**图元：整幅背景（x=0,y=0,w=画布宽,h=画布高）不算 —— 否则包围盒永远是整页，
    映射就成了恒等变换，白折腾。
    """
    xs: list[float] = []
    ys: list[float] = []
    canvas = [_num(v) for v in (spec.get("canvas") or [1280, 720])]
    cw, ch = (canvas + [1280, 720])[:2]
    for d in spec.get("decorations") or []:
        a = d.get("attrs") or {}
        tag = d.get("tag")
        if tag == "rect":
            x, y = _num(a.get("x")), _num(a.get("y"))
            w, h = _num(a.get("width")), _num(a.get("height"))
            if w >= cw * 0.98 and h >= ch * 0.98:                 # 整幅背景，跳过
                continue
            if w <= 0 or h <= 0:
                continue
            xs += [x, x + w]
            ys += [y, y + h]
        elif tag == "line":
            xs += [_num(a.get("x1")), _num(a.get("x2"))]
            ys += [_num(a.get("y1")), _num(a.get("y2"))]
    for s in spec.get("slots") or []:
        b = [_num(v) for v in (s.get("bounds") or [])]
        if len(b) == 4:
            xs += [b[0], b[0] + b[2]]
            ys += [b[1], b[1] + b[3]]
    if not xs or not ys:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def render_body(tokens: dict, slide: dict, layout: str, *, index: int = 0, total: int = 1,
                warnings: list[str] | None = None, boxes: list[dict] | None = None) -> str:
    """按注册表的版式数据渲染**一整页**（画布底色 + 装饰 + 槽位文字），返回 SVG 文本。

    与核心版式同一套口径：画布 1280×720、颜色只来自 tokens、`boxes` 给了就给文字打 `data-slot`
    （面板可编辑盒子；**导出路径不传 boxes**）。
    """
    warn = warnings if warnings is not None else []
    lid = _strip_prefix(layout)
    spec = registry().get(lid)
    if not spec:
        raise ValueError(f"注册表里没有这个版式：{layout}")
    if not is_wide(spec.get("canvas")):
        raise ValueError(
            f"版式 {lid} 属于 {spec.get('family')} 族（画布 {spec.get('canvas')}），不是 16:9 —— "
            f"我们的页面固定 1280×720，画不了这种版式（可选版式见 GET /api/slide/design/catalog）")

    cv = _num((tokens.get("canvas") or {}).get("w"), 1280), _num((tokens.get("canvas") or {}).get("h"), 720)
    W, H = cv
    palette = tokens.get("palette") or {}
    bg = str(palette.get("bg") or (tokens.get("canvas") or {}).get("bg") or "#FFFFFF")
    text_c = str(palette.get("text") or "#1A1A1A")
    sub_c = str(palette.get("sub") or "#6B6B6B")
    line_c = str(palette.get("line") or "#E6EAF2")
    soft_c = str(palette.get("soft") or "#F7F9FC")
    accent = str(palette.get("accent") or "#2F6BFF")
    font = ((tokens.get("fonts") or {}).get("stack") or ["微软雅黑"])
    font_family = ", ".join(str(f) for f in font) if isinstance(font, list) else str(font)
    margin = _num((tokens.get("canvas") or {}).get("margin"), 72)
    #: 安全边距与内容安全线（审计同口径）。核心版式的内容底边约在 `footer_y - 34 = 608`，
    #: 这里取 `H - margin - 30 = 618`，留出页码那一行，既不撞底边距也不越安全线。
    safe = (margin, margin, W - margin, H - margin - 30)
    footer_y = H - margin - 6                      # 与核心版式同一个页码基线

    parts: list[str] = [f'<rect x="0" y="0" width="{W:g}" height="{H:g}" fill="{bg}"/>']
    # ① 把源版式的坐标**归一化**到我们的安全盒子里：源版式各有各的边距（常见 64px，我们要求 ≥72px），
    #    直接照搬会被审计判 error（实测：left/right 只剩 64px、内容越过安全线）。做法是按"源内容包围盒"
    #    等比映射 —— 16:9 同比例，所以不会变形。
    bounds = _content_bounds(spec)
    sx = sy = 1.0
    ox = oy = 0.0
    if bounds:
        bx0, by0, bx1, by1 = bounds
        sw, sh = max(bx1 - bx0, 1e-6), max(by1 - by0, 1e-6)
        tw, th = safe[2] - safe[0], safe[3] - safe[1]
        s = min(tw / sw, th / sh)
        sx = sy = s
        ox = safe[0] - bx0 * s + (tw - sw * s) / 2          # 横向居中
        oy = safe[1] - by0 * s                              # **贴顶**：竖着居中会让"内容纵向占比"掉到 74% 以下（实测 66%~70%）

    def X(v: float) -> float:
        return v * sx + ox

    def Y(v: float) -> float:
        return v * sy + oy

    # ① 装饰图元：颜色一律换成主题色（原版硬编码色会跟主题打架）
    for d in spec.get("decorations") or []:
        a = d.get("attrs") or {}
        tag = d.get("tag") or "rect"
        if tag == "rect":
            sw, sh = _num(a.get("width")), _num(a.get("height"))
            src_canvas = [_num(v) for v in (spec.get("canvas") or [W, H])]
            scw, sch = (src_canvas + [W, H])[:2]
            if sw >= scw * 0.98 and sh >= sch * 0.98:
                continue                                      # 源版式的整幅背景：跳过（底色用主题的）
            fill = "none" if a.get("fill") == "none" else (soft_c if a.get("fill") else "none")
            stroke = f' stroke="{line_c}" stroke-width="{a.get("stroke-width", 1)}"' if a.get("stroke") else ""
            rx = f' rx="{a["rx"]}"' if a.get("rx") else ""
            x, y = X(_num(a.get("x"))), Y(_num(a.get("y")))
            w, h = sw * sx, sh * sy
            parts.append(f'<rect x="{x:g}" y="{y:g}" width="{w:g}" '
                         f'height="{h:g}"{rx} fill="{fill}"{stroke}/>')
        elif tag == "line":
            parts.append(f'<line x1="{X(_num(a.get("x1"))):g}" y1="{Y(_num(a.get("y1"))):g}" '
                         f'x2="{X(_num(a.get("x2"))):g}" y2="{Y(_num(a.get("y2"))):g}" '
                         f'stroke="{line_c}" stroke-width="{a.get("stroke-width", 1)}"/>')
        elif tag == "path":
            d_attr = a.get("d", "")
            if d_attr:
                parts.append(f'<path d="{d_attr}" fill="none" stroke="{line_c}" '
                             f'stroke-width="{a.get("stroke-width", 1)}"/>')
        # circle / ellipse / polygon / polyline 极少出现在 master/layout 层，略过不画（宁缺勿错）

    # ② 槽位文字：字号夹到主题阶梯；容量不够就折行（不截断内容，超出记 warn）
    safe_bottom = safe[3]
    for i, s in enumerate(spec.get("slots") or []):
        b = [_num(x) for x in (s.get("bounds") or [])]
        if len(b) != 4:
            continue
        x, y = X(b[0]), Y(b[1])
        w, h = b[2] * sx, b[3] * sy
        raw = _slot_text(slide, s.get("placeholder") or "", i)
        if not raw:
            continue
        size, off = _clamp_size(tokens, s.get("size"))
        if off:
            warn.append(f"版式 {lid} 第 {i + 1} 个槽的原字号 {s.get('size')} 不在主题阶梯里，已用 {size:g}px")
        is_title = (s.get("placeholder") or "").lower() in ("title", "ctrtitle", "subtitle")
        color = text_c if is_title else sub_c
        weight = str(s.get("weight") or ("700" if is_title else "400"))
        anchor = {"middle": "middle", "end": "end"}.get(str(s.get("align") or ""), "start")
        tx = x + (w / 2 if anchor == "middle" else (w if anchor == "end" else 0))
        lines = _wrap(raw, max(w, size * 2), size)
        lh = float((tokens.get("type") or {}).get("lh_body") or 1.35)
        if is_title:
            lh = float((tokens.get("type") or {}).get("lh_title") or 1.08)
        step = size * lh
        base_y = y + size * (1.15 if is_title else 1.0)             # 与 bounds 顶对齐（近似原版基线）
        max_lines = max(1, int((min(y + h, safe_bottom) - base_y) // step) + 1)
        shown = lines[:max_lines]
        if len(lines) > max_lines:
            warn.append(f"版式 {lid} 第 {i + 1} 个槽放不下（{len(lines)} 行 > {max_lines} 行），"
                        f"已排前 {max_lines} 行 —— 内容建议拆分或换版式")
        attrs = (f'data-slot="{s.get("placeholder")}"' if boxes is not None else "")
        if boxes is not None:
            boxes.append({"slot": str(s.get("placeholder") or f"slot:{i}"), "kind": "text",
                          "x": x, "y": base_y - size, "w": w, "h": min(h, max(step, step * len(shown))),
                          "size": size, "align": anchor})
        # ⚠️ **一行一个 `<text>`**，不要用 `<tspan>` 装多行：审计与面板都按 `<text>` 的直接文本
        # 量墨迹（`el.text` 不含子节点）—— 用 tspan 会让审计"看不见这些文字"，
        # 于是失衡被判 100%、内容占比也算错（踩过）。
        for j, ln in enumerate(shown):
            parts.append(f'<text {attrs}x="{tx:g}" y="{base_y + j * step:g}" fill="{color}" '
                         f'font-size="{size:g}" font-weight="{weight}" text-anchor="{anchor}" '
                         f'font-family="{font_family}" xml:space="preserve">{_esc(ln)}</text>')

    # ③ 页脚（页码）：与核心版式同一个位置口径 —— 画在**安全边距内**、不碰安全线
    if total and not str(spec.get("id", "")).startswith("cover"):
        parts.append(f'<line x1="{margin:g}" y1="{footer_y - 22:g}" x2="{W - margin:g}" '
                     f'y2="{footer_y - 22:g}" stroke="{line_c}" stroke-width="1"/>')
        parts.append(f'<text x="{W - margin:g}" y="{footer_y:g}" fill="{sub_c}" '
                     f'font-size="15" text-anchor="end" font-family="{font_family}" '
                     f'xml:space="preserve">{index + 1} / {total}</text>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W:g}" height="{H:g}" '
            f'viewBox="0 0 {W:g} {H:g}">' + "".join(parts) + "</svg>")


def _esc(s: str) -> str:
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def summary() -> dict:
    """给日志/接口用的小结。"""
    fams = design_spec.layout_families()
    return {"layouts": len(ids()), "families": len(fams),
            "core": list(_CORE), "example": ids()[:5]}


def _core() -> tuple[str, ...]:
    from edu_agent import ppt_svg
    return tuple(ppt_svg.LAYOUTS)


_CORE = ("cover", "section", "bullets", "two-col", "media", "end")


def is_blank_ok(svg: str) -> bool:
    """粗查一页是否"画出来了"（渲染冒烟用）。"""
    return bool(re.search(r"<text\b", svg)) and "viewBox=" in svg
