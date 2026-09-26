"""版式审计（**模型内**）：按 `docs/22` §7.1 / §7.1b 的定义量我们真正导出的那份 SVG。

为什么不用浏览器（`docs/22` §7.3 有实测记录）：

- jsdom 没有排版引擎，几何全 0；
- Edge `--headless=new --dump-dom`（页内自测后抓 DOM）**不可靠**：首次成功、之后稳定返回空；
- 但我们的渲染是**确定性**的：SVG 里的坐标就是最终坐标 ⇒ 直接解析 SVG 算指标，零依赖、可单测、可造负样本。

指标分三档：`error`（不合格，`--gate` 会让命令失败）、`warn`（值得看一眼）、`info`（记录用，不判）。

> 说明：文字宽度用与排版同一套估算（`ppt_svg.char_w`），所以"碰撞/空白"量的是**同一个口径**的几何，
> 而不是另写一套标准 —— 这点比"用浏览器再量一遍"更一致（也更快）。
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from . import ppt_svg
from . import slide_deck
from . import ppt_tokens as tk

SVG_NS = "{http://www.w3.org/2000/svg}"

#: 阈值（与 docs/22 §7.1/§7.1b 一一对应；改这里就要同步改文档）
LIMITS = {
    "aspect_tol": 0.005,        # 宽高比与 16:9 的相对误差
    "overflow_tol": 0.5,        # 元素越出画布多少 px 才算越界
    "active_ratio_min": 0.74,   # 内容纵向占比下限（来自 [S11]）
    "bottom_gap_max": 170.0,    # 底部空白上限（来自 [S11]）
    "safe_line_ratio": 0.93,    # 内容安全线（来自 [S11]）
    "title_gap_min": 32.0,      # 大标题与下一块的最小间距（来自 [S11]）
    "label_gap_min": 14.0,      # 局部小标题与下一块的最小间距（来自 [S11]）
    "collision_area_min": 1.0,  # 文本两两相交超过多少 px² 才算碰撞
    "accent_ratio_max": 0.10,   # 主色面积占比上限（来自 [S1]）
    "imbalance_max": 0.12,      # 左右/上下失衡上限（来自 [S6]）
}

#: 每个版式的"安静侧"（这一侧本来就该少内容）：用于失衡指标豁免，`None` = 两侧都要平衡
QUIET_SIDE = {"cover": "right", "section": "left", "bullets": "right",
              "media": "left", "two-col": None, "end": "right"}

#: 画布背景铺满整页，不算"内容"
_BG_RATIO = 0.98


# ---------------------------------------------------------------- SVG → 盒子

def _f(v: object, default: float = 0.0) -> float:
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _text_width(s: str, size: float) -> float:
    return sum(ppt_svg.char_w(ch, size) for ch in s)


def boxes_from_svg(svg: str) -> list[dict]:
    """把一页 SVG 解析成盒子列表（`{tag, role, x, y, w, h, ink, fill, size, text}`）。

    盒子口径：矩形/直线/折线取几何；文字取"基线 − 0.8em 到 基线 + 0.2em"，
    宽度用与排版同一套字宽估算（`ppt_svg.char_w`）—— 与生成时一致，不用另立标准。

    `ink` 是**墨迹面积近似**（给"主色占比"用）：矩形 = 面积、线/折线 = 长度×线宽、
    文字 = 包围盒×0.5。用包围盒量曲线会把"一条 2px 细线"算成一大块色板，那不叫主色泛滥。
    """
    root = ET.fromstring(svg)
    canvas_w = _f(root.get("width"), 1280.0)
    canvas_h = _f(root.get("height"), 720.0)
    out: list[dict] = []
    for el in root.iter():
        tag = el.tag.replace(SVG_NS, "")
        if tag == "svg":
            continue
        fill = el.get("fill") or el.get("stroke") or "none"
        sw = _f(el.get("stroke-width"), 1.0)
        if tag == "rect":
            w, h = _f(el.get("width")), _f(el.get("height"))
            if w >= canvas_w * _BG_RATIO and h >= canvas_h * _BG_RATIO:
                continue                                  # 画布底色，不算内容
            out.append({"tag": tag, "role": "shape", "x": _f(el.get("x")), "y": _f(el.get("y")),
                        "w": w, "h": h, "ink": w * h, "fill": fill, "size": 0.0, "text": ""})
        elif tag == "line":
            x1, y1 = _f(el.get("x1")), _f(el.get("y1"))
            x2, y2 = _f(el.get("x2")), _f(el.get("y2"))
            length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
            out.append({"tag": tag, "role": "rule", "x": min(x1, x2), "y": min(y1, y2),
                        "w": max(abs(x2 - x1), 1.0), "h": max(abs(y2 - y1), 1.0),
                        "ink": length * sw, "fill": el.get("stroke") or "none",
                        "size": 0.0, "text": ""})
        elif tag == "polyline":
            pts = [p.split(",") for p in (el.get("points") or "").split() if "," in p]
            xy = [(_f(p[0]), _f(p[1])) for p in pts] or [(0.0, 0.0)]
            xs = [p[0] for p in xy]
            ys = [p[1] for p in xy]
            length = sum(((xy[i + 1][0] - xy[i][0]) ** 2 + (xy[i + 1][1] - xy[i][1]) ** 2) ** 0.5
                         for i in range(len(xy) - 1))
            out.append({"tag": tag, "role": "graphic", "x": min(xs), "y": min(ys),
                        "w": max(max(xs) - min(xs), 1.0), "h": max(max(ys) - min(ys), 1.0),
                        "ink": length * sw, "fill": el.get("stroke") or "none",
                        "size": 0.0, "text": ""})
        elif tag == "text":
            s = (el.text or "").strip()
            if not s:
                continue
            size = _f(el.get("font-size"), 16.0)
            w = _text_width(s, size)
            x = _f(el.get("x"))
            anchor = el.get("text-anchor") or "start"
            if anchor == "middle":
                x -= w / 2
            elif anchor == "end":
                x -= w
            y = _f(el.get("y")) - size * 0.8
            out.append({"tag": tag, "role": "text", "x": x, "y": y, "w": w, "h": size,
                        "ink": w * size * 0.5, "fill": fill, "size": size, "text": s})
    return out


# ---------------------------------------------------------------- 几何小工具

def _bbox(boxes: list[dict]) -> tuple[float, float, float, float]:
    x0 = min(b["x"] for b in boxes)
    y0 = min(b["y"] for b in boxes)
    x1 = max(b["x"] + b["w"] for b in boxes)
    y1 = max(b["y"] + b["h"] for b in boxes)
    return x0, y0, x1, y1


def _inter_area(a: dict, b: dict) -> float:
    w = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    h = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    return max(0.0, w) * max(0.0, h)


def largest_empty_rect(boxes: list[dict], canvas_w: float, canvas_h: float, cells: int = 64) -> float:
    """最大空白矩形占画布的比例（网格近似 + 直方图法，纯记录用、不设卡）。

    只统计"没有被任何内容盒子覆盖"的格子 —— 规范自己要求"一侧明显更安静"，
    所以这个数**大不等于难看**，它的用途是"改版式前后有没有变糟"。
    """
    ch = canvas_h / cells * (canvas_w / canvas_h)
    cw = canvas_w / cells
    rows = int(canvas_h / ch)
    grid = [[False] * cells for _ in range(rows)]
    for b in boxes:
        c0, c1 = int(max(0, b["x"]) / cw), int(min(canvas_w, b["x"] + b["w"]) / cw)
        r0, r1 = int(max(0, b["y"]) / ch), int(min(canvas_h, b["y"] + b["h"]) / ch)
        for r in range(r0, min(r1 + 1, rows)):
            for c in range(c0, min(c1 + 1, cells)):
                grid[r][c] = True
    best = 0
    heights = [0] * cells
    for r in range(rows):
        for c in range(cells):
            heights[c] = 0 if grid[r][c] else heights[c] + 1
        stack: list[tuple[int, int]] = []
        for c in range(cells + 1):
            h = heights[c] if c < cells else 0
            start = c
            while stack and stack[-1][1] >= h:
                idx, hh = stack.pop()
                best = max(best, hh * (c - idx))
                start = idx
            stack.append((start, h))
    return best * cw * ch / (canvas_w * canvas_h)


# ---------------------------------------------------------------- 单页审计

def audit_page(theme: dict, svg: str, layout: str, page_no: int = 1) -> dict:
    """审一页：返回 `{page, layout, metrics, findings}`。"""
    cv = ppt_svg.Canvas(theme)
    canvas_w, canvas_h = float(cv.w), float(cv.h)
    boxes = boxes_from_svg(svg)
    texts = [b for b in boxes if b["role"] == "text"]
    findings: list[dict] = []

    def add(level: str, code: str, detail: str, fix: str) -> None:
        findings.append({"level": level, "code": code, "page": page_no, "detail": detail, "fix": fix})

    # 1) 版式白名单
    if layout not in slide_deck.LAYOUTS:
        add("error", "whitelist", f"版式 {layout!r} 不在登记的 6 个里",
            "用 slide_deck.LAYOUTS 里的版式，别临时自创")

    # 2) 宽高比
    root = ET.fromstring(svg)
    vw, vh = _f(root.get("width"), canvas_w), _f(root.get("height"), canvas_h)
    ratio = vw / vh
    aspect_err = abs(ratio - 16 / 9) / (16 / 9)
    if aspect_err > LIMITS["aspect_tol"]:
        add("error", "aspect", f"画布 {vw:g}×{vh:g} 不是 16:9（相对误差 {aspect_err:.1%}）",
            "画布固定 1280×720；改 tokens 的 canvas")

    # 3) 越界（视觉溢出）
    out_boxes = [b for b in boxes
                 if b["x"] < -LIMITS["overflow_tol"] or b["y"] < -LIMITS["overflow_tol"]
                 or b["x"] + b["w"] > canvas_w + LIMITS["overflow_tol"]
                 or b["y"] + b["h"] > canvas_h + LIMITS["overflow_tol"]]
    for b in out_boxes[:3]:
        over = max(b["x"] + b["w"] - canvas_w, -b["x"], b["y"] + b["h"] - canvas_h, -b["y"])
        add("error", "overflow",
            f"{b['tag']}「{(b['text'] or b['role'])[:12]}」越出画布 {over:.0f}px",
            "缩短这处文字或改用更宽的版式；**不要删内容**，宁可拆页")

    # 4) 安全边距 + 内容纵向分布
    margin = float(cv.margin)
    content = [b for b in boxes if b["role"] in ("text", "shape", "graphic")]
    gaps = {"left": 0.0, "right": 0.0, "top": 0.0, "bottom": 0.0}
    active_ratio, bottom_gap, empty_ratio = 0.0, 0.0, 0.0
    if content:
        x0, y0, x1, y1 = _bbox(content)
        gaps = {"left": x0, "right": canvas_w - x1, "top": y0, "bottom": canvas_h - y1}
        active_ratio = (y1 - y0) / canvas_h
        bottom_gap = max(0.0, canvas_h - y1)
        empty_ratio = largest_empty_rect(content, canvas_w, canvas_h)
        for side in ("left", "right", "top", "bottom"):
            if gaps[side] < margin - LIMITS["overflow_tol"]:
                add("error", "margin", f"{side} 边距只剩 {gaps[side]:.0f}px（要求 ≥{margin:g}px）",
                    "把内容收进安全边距内；版心宽用 tokens 的 content_w")
        if active_ratio < LIMITS["active_ratio_min"]:
            add("warn", "active-ratio", f"内容纵向占比 {active_ratio:.0%}（建议 ≥{LIMITS['active_ratio_min']:.0%}）",
                "把块间距/字号按 tokens 调开，或补一点内容；别为了填满而加废话")
        if bottom_gap > LIMITS["bottom_gap_max"]:
            add("warn", "bottom-gap", f"底部空白 {bottom_gap:.0f}px（建议 ≤{LIMITS['bottom_gap_max']:.0f}px）",
                "抬高内容或把块间距调匀（`distribute()` 已经会均分，这里多半是页内块太少）")
        if y1 > canvas_h * LIMITS["safe_line_ratio"]:
            add("warn", "safe-line", f"内容压到 {y1:.0f}px，越过安全线 {canvas_h * LIMITS['safe_line_ratio']:.0f}px",
                "上移一点或减一行；页码/页脚线本身不算内容")

    # 5) 文字碰撞（有意叠放不算：只比文字与文字）
    collisions = []
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            a = _inter_area(texts[i], texts[j])
            if a > LIMITS["collision_area_min"]:
                collisions.append((texts[i]["text"][:10], texts[j]["text"][:10], a))
    if collisions:
        a, b, area = collisions[0]
        add("error", "collision", f"{len(collisions)} 处文字重叠（如「{a}」×「{b}」{area:.0f}px²）",
            "把块间距调开或减少同屏块数；**不要删内容**，宁可拆页")

    # 6) 失衡（面积加权；版式声明了安静侧就豁免）
    imbalance = {"horizontal": 0.0, "vertical": 0.0, "heavy": ""}
    if texts:
        mid_x, mid_y = canvas_w / 2, canvas_h / 2
        left = sum(b["w"] * b["h"] for b in texts if b["x"] + b["w"] / 2 < mid_x)
        right = sum(b["w"] * b["h"] for b in texts if b["x"] + b["w"] / 2 >= mid_x)
        top = sum(b["w"] * b["h"] for b in texts if b["y"] + b["h"] / 2 < mid_y)
        bot = sum(b["w"] * b["h"] for b in texts if b["y"] + b["h"] / 2 >= mid_y)
        total = (left + right) or 1.0
        imbalance = {"horizontal": abs(left - right) / total, "vertical": abs(top - bot) / (top + bot or 1.0),
                     "heavy": "left" if left >= right else "right"}
        quiet = QUIET_SIDE.get(layout)
        if imbalance["horizontal"] > LIMITS["imbalance_max"]:
            if quiet and imbalance["heavy"] != quiet:
                add("info", "imbalance",
                    f"左右失衡 {imbalance['horizontal']:.0%}（本版式的安静侧在{quiet}，符合预期）", "无需处理")
            else:
                add("error", "imbalance",
                    f"左右失衡 {imbalance['horizontal']:.0%}（上限 {LIMITS['imbalance_max']:.0%}）",
                    "把重的一侧分一点到另一侧（两栏/图文版式），或改用不对称但平衡的版式")

    # 7) 字号阶梯（只许取 tokens 里的档）
    allowed = {float(v) for k, v in cv.type.items() if k in ("cover", "title", "label", "body", "caption")}
    used = sorted({round(b["size"], 2) for b in texts})
    stray = [s for s in used if not any(abs(s - a) < 0.01 for a in allowed)]
    if stray:
        add("error", "font-scale", f"出现阶梯外的字号 {stray}（允许 {sorted(allowed)}）",
            "字号一律取 tokens 的 type（docs/22 §3.3）")

    # 8) 主色占比 + 标题间距
    accent = tk.rgb(cv.marker)
    accent_area = 0.0
    for b in boxes:
        try:
            same = tk.rgb(b["fill"]) == accent
        except ValueError:
            same = False
        if same:
            accent_area += b["ink"]
    accent_ratio = accent_area / (canvas_w * canvas_h)
    if accent_ratio > LIMITS["accent_ratio_max"]:
        add("error", "accent", f"主色面积 {accent_ratio:.1%}（上限 {LIMITS['accent_ratio_max']:.0%}）",
            "主色只留细线/短记号/关键词；大面积色块改用 soft/line 色")

    title_sizes = [b for b in texts if abs(b["size"] - cv.size("title")) < 0.01]
    title_gap = None
    if title_sizes:
        title = title_sizes[0]
        title_bottom = title["y"] + title["h"]
        below = [b for b in texts if b["y"] > title_bottom - 1 and b is not title]
        if below:
            nearest = min(below, key=lambda b: b["y"])
            title_gap = nearest["y"] - title_bottom
            floor = LIMITS["label_gap_min"] if nearest["size"] <= cv.size("label") + 0.01 else LIMITS["title_gap_min"]
            if title_gap < floor:
                add("warn", "title-gap", f"标题与下一块只隔 {title_gap:.0f}px（建议 ≥{floor:g}px）",
                    "把标题下的间距调开；别靠删内容解决")

    return {"page": page_no, "layout": layout, "findings": findings,
            "metrics": {"aspect": round(ratio, 4), "active_ratio": round(active_ratio, 3),
                        "bottom_gap": round(bottom_gap, 1), "empty_rect": round(empty_ratio, 3),
                        "margins": {k: round(v, 1) for k, v in gaps.items()},
                        "collisions": len(collisions), "accent_ratio": round(accent_ratio, 4),
                        "imbalance": {k: round(v, 3) if isinstance(v, float) else v
                                      for k, v in imbalance.items()},
                        "font_sizes": used, "title_gap": None if title_gap is None else round(title_gap, 1),
                        "boxes": len(boxes), "texts": len(texts)}}


# ---------------------------------------------------------------- 整册审计

def audit_pages(theme_id: str, pages: list[str], layouts: list[str] | None = None) -> dict:
    """审一组已经渲染好的 SVG（负样本就是这么喂进来的）。"""
    theme = slide_deck.theme(theme_id)
    lays = list(layouts or ["bullets"] * len(pages))
    rows = [audit_page(theme, svg, lays[i] if i < len(lays) else "bullets", i + 1)
            for i, svg in enumerate(pages)]
    errors = [f for r in rows for f in r["findings"] if f["level"] == "error"]
    warns = [f for r in rows for f in r["findings"] if f["level"] == "warn"]
    infos = [f for r in rows for f in r["findings"] if f["level"] == "info"]
    return {"ok": not errors, "theme_id": theme["id"], "theme_name": theme.get("name") or theme["id"],
            "pages": rows, "errors": errors, "warnings": warns, "infos": infos,
            "stats": {"pages": len(rows), "errors": len(errors), "warnings": len(warns), "infos": len(infos)}}


def audit_deck(theme_id: str, slides: list[dict] | None = None, title: str = "") -> dict:
    """渲染一份 deck 再审计（默认用示例教案）。"""
    slides = slides if slides is not None else slide_deck.SAMPLE_DECK
    built = slide_deck.build_deck(theme_id, slides, title=title or "3.3 幂函数")
    out = audit_pages(theme_id, built["pages"], built["layouts"])
    out["render_warnings"] = built["warnings"]
    return out


def baseline(theme_ids: list[str] | None = None) -> dict:
    """跑一遍所有主题的示例 deck，给出可直接写进文档的基线数字。"""
    ids = theme_ids or tk.available()
    out: dict[str, dict] = {}
    for tid in ids:
        rep = audit_deck(tid)
        rows = rep["pages"]
        out[tid] = {
            "name": rep["theme_name"],
            "active_ratio_min": min((r["metrics"]["active_ratio"] for r in rows), default=0.0),
            "bottom_gap_max": max((r["metrics"]["bottom_gap"] for r in rows), default=0.0),
            "margin_min": min((min(r["metrics"]["margins"].values()) for r in rows), default=0.0),
            "empty_rect_max": max((r["metrics"]["empty_rect"] for r in rows), default=0.0),
            "collisions": sum(r["metrics"]["collisions"] for r in rows),
            "accent_ratio_max": max((r["metrics"]["accent_ratio"] for r in rows), default=0.0),
            "imbalance_max": max((r["metrics"]["imbalance"]["horizontal"] for r in rows), default=0.0),
            "title_gap_min": min((r["metrics"]["title_gap"] for r in rows
                                  if r["metrics"]["title_gap"] is not None), default=0.0),
            "font_sizes": sorted({s for r in rows for s in r["metrics"]["font_sizes"]}),
            "errors": rep["stats"]["errors"], "warnings": rep["stats"]["warnings"],
        }
    return out


def write_report(rep: dict, path: Path) -> Path:
    """把原始结果写到 `data/tmp/slide_audit/<主题>.json`（临时目录，不入库）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
