"""槽位 + tokens → SVG（预览与导出的**同一个源**）。

规格见 `docs/22-预览版式与导出规格.md`：§3 数值、§5 六版式与槽位、§6 导出（同一份 SVG 交给
`.deps/ppt-master` 的 `svg_to_pptx.py` 出原生可编辑 pptx）。

硬约束（来自上游 style-system 与 ppt-master 集成说明，见 docs/22 §11 的 S1–S3、S7）：

- `viewBox="0 0 1280 720"`，坐标系 = 画布 px，所有文字用真 `<text>`（**不是**位图）；
- 只用 `rect` / `line` / `polyline` / `text`，**显式** fill / stroke；不用 filter / 阴影 / 发光 / 渐变 / 外链图；
- 一页只用一个主色做强调，其余走灰阶；强调面积远小于 10% 画布；
- **绝不截断文字**：装不下就换行（`wrap`）；模块数超上限只提示"拆页"，不改写老师的话。

排版约定：画布底色铺满；正文从安全边距（左）起排，版心宽取 tokens 的 `content_w` 上限，
右侧因此更安静（上游要求不对称、留一侧更安静）；页码 `x/N` 画在安全边距**内**（封面不画）。
"""

from __future__ import annotations

import re

from . import ppt_tokens as tk

#: 版式 id（与 docs/22 §5、docs/19 §8.4 一致）
LAYOUTS = ("cover", "section", "bullets", "two-col", "media", "end")


# ---------------------------------------------------------------- 画布与图元

class Canvas:
    """1280×720 设计坐标上的定位工具：安全边距、12 栏网格、页脚。

    `boxes`：**可编辑盒子**收集器（R10）。给 `render_slide(..., boxes=[])` 传一个列表，
    渲染时每个"文字槽"就把自己的可编辑区域（设计像素）记进去 —— 前端拿它做页内编辑的叠加层，
    不用自己去量像素（jsdom / SVG 量尺寸都不可靠）。
    """

    def __init__(self, t: dict, boxes: list[dict] | None = None,
                 overrides: dict | None = None):
        c = t["canvas"]
        self.t = t
        self.w = int(c["w"])
        self.h = int(c["h"])
        self.margin = float(c["margin"])
        self.bg = c["bg"]
        self.content_w = float(c["content_w"][1])
        self.cols = int(c.get("cols") or 12)
        self.gutter = float(c.get("gutter") or 24)
        self.col_w = (self.content_w - (self.cols - 1) * self.gutter) / self.cols
        self.type = t["type"]
        self.weights = t.get("weights") or {}
        self.palette = t["palette"]
        self.family = ", ".join(t["fonts"]["stack"])
        self.ls = float(self.type.get("letter_spacing") or 0)
        self.right = self.margin + self.content_w
        self.boxes = boxes
        self.overrides = overrides or {}

    def ov(self, slot: str) -> dict:
        """这个槽的"老师改过的样子"（拖到哪儿、多大、什么字号颜色…）；没改过就是空 dict。"""
        return (self.overrides or {}).get(slot) or {}

    def zof(self, slot: str) -> int:
        """这一格的层级（老师用「上移一层/下移一层」调过才有；没调过 = 0）。"""
        try:
            return int((self.ov(slot) or {}).get("z") or 0)
        except (TypeError, ValueError):
            return 0

    @property
    def has_z(self) -> bool:
        """这一页有没有人调过层级。**没有就别碰出图顺序**（导出要逐字节一致）。"""
        return any(self.zof(s) for s in (self.overrides or {}))

    def apply(self, slot: str, x: float, y: float, size: float,
              fill: str | None, anchor: str) -> tuple[float, float, float, str | None, str, bool]:
        """把覆盖套到一次文字发射上：返回 (x, y, size, fill, anchor, 是否要画)。

        `hidden` 用来"删掉这一处文字"（老师的删除就是清空 + 不再画）。对齐按**版心宽/content_w**
        作为参照居中或右对齐（单行槽在发射时还不知道自己的框宽，用版心是最不容易出错的近似）。
        """
        o = self.ov(slot) if slot else {}
        if not o:
            return x, y, size, fill, anchor, True
        if o.get("hidden"):
            return x, y, size, fill, anchor, False
        al = o.get("align")
        if al in ("start", "middle", "end") and al != anchor:
            ref = float(o.get("dw") or 0) or self.content_w
            if anchor == "start":                 # 原来按左边界给的 x → 换算成新的锚点
                if al == "middle":
                    x, anchor = x + ref / 2, "middle"
                elif al == "end":
                    x, anchor = x + ref, "end"
        return (x + float(o.get("dx") or 0), y + float(o.get("dy") or 0),
                float(o.get("size") or size), o.get("color") or fill, anchor, True)

    def box(self, slot: str, x: float, y: float, w: float, h: float, size: float,
            *, kind: str = "text", align: str = "start") -> None:
        """记一个可编辑盒子（`slot` 为空或没开 annotate 就什么都不做）。

        **同一个槽只留一个盒子**（保留面积更大的那个）：`text()` 会自动给单行做个小盒子，
        而版式里往往随后又显式给一个"整行/整块"的大盒子（点击区更友好）—— 两个都留会让前端
        画出两块重叠的点击区（踩过：`title` 出现两次）。
        **覆盖要在这里套上**（拖动/改大小长在盒子上），这样前端画的框与服务端画的字永远一致。
        """
        if self.boxes is None or not slot:
            return
        o = self.ov(slot)
        if o:
            if o.get("hidden"):
                return
            x += float(o.get("dx") or 0)
            y += float(o.get("dy") or 0)
            w = max(40.0, w + float(o.get("dw") or 0))
            h = max(24.0, h + float(o.get("dh") or 0))
            size = float(o.get("size") or size)
            if o.get("align") in ("start", "middle", "end"):
                align = o["align"]
        area = float(w) * float(h)
        entry = {"slot": slot, "kind": kind, "align": align,
                 "x": round(float(x), 1), "y": round(float(y), 1),
                 "w": round(float(w), 1), "h": round(float(h), 1),
                 "size": round(float(size), 1)}
        z = int(o.get("z") or 0) if o else 0
        if z:
            entry["z"] = z                            # 层级只在老师调过时才给（老数据形状不变）
        for i, b in enumerate(self.boxes):
            if b.get("slot") != slot:
                continue
            if area > float(b.get("w") or 0) * float(b.get("h") or 0):
                self.boxes[i] = entry
            return
        self.boxes.append(entry)

    # -- 字号 / 颜色 / 字重
    def size(self, key: str) -> float:
        return float(self.type[key])

    def weight(self, key: str, default: int = 400) -> int:
        return int(self.weights.get(key, default))

    def color(self, key: str) -> str:
        return self.palette[key]

    @property
    def marker(self) -> str:
        """细小记号/标签用的颜色：默认 accent；某个主题的 accent 太浅时可指到 accent2。
        （插画暖那套的 accent 是浅蓝 #A7C7E7，做标题下划线好看、做标签读不清。）"""
        return self.color(self.t.get("marker") or "accent")

    # -- 几何
    def col_x(self, i: int) -> float:
        """第 i 栏左边界（0 起）。"""
        return self.margin + i * (self.col_w + self.gutter)

    def span_w(self, span: int) -> float:
        """跨 span 栏的宽度（含中间 gutter）。"""
        return span * self.col_w + (span - 1) * self.gutter

    @property
    def footer_y(self) -> float:
        """页码基线（画在安全边距内）。"""
        return self.h - self.margin - 6


def esc(s: object) -> str:
    """XML 转义（老师的话里什么都可能有）。"""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def text(cv: Canvas, x: float, y: float, s: object, *, size: float, weight: int | None = None,
         fill: str | None = None, anchor: str = "start", slot: str = "", zkey: str = "") -> str:
    """一行文字（给的是**基线** y；不用 dominant-baseline，转 pptx 时口径一致）。

    `slot` 给了就顺手记一个可编辑盒子（高 = 一行；宽 = 这行文字的实际宽度），
    并给元素打上 `data-slot` 方便前端"编辑时隐藏这一槽"。

    `zkey` 是**层级归属**：多行文字的后续行 `slot` 是空的（盒子只记一次），但它们必须跟着
    第一行一起换层，所以层级记号单独用 `zkey` 传（渲染完由 `order_z` 洗掉）。
    """
    if s is None or str(s) == "":
        return ""
    x, y, size, fill, anchor, draw = cv.apply(slot, x, y, size, fill, anchor)
    if not draw:
        return ""
    o = cv.ov(slot) if slot else {}
    weight = 700 if o.get("bold") else int(weight if weight is not None else 400)
    attrs = [
        f'x="{round(x, 2)}"', f'y="{round(y, 2)}"',
        f'font-family="{esc(o.get("font") or cv.family)}"', f'font-size="{round(size, 2)}"',
        f'font-weight="{weight}"',
        f'fill="{fill or cv.color("text")}"',
    ]
    if o.get("italic"):
        attrs.append('font-style="italic"')
    if anchor != "start":
        attrs.append(f'text-anchor="{anchor}"')
    if cv.ls:
        attrs.append(f'letter-spacing="{cv.ls}em"')
    zk = zkey or slot
    if zk and cv.has_z and cv.zof(zk):
        attrs.append(f'data-zslot="{esc(zk)}"')     # 层级记号：order_z 会读它、然后洗掉
    if slot and cv.boxes is not None:               # ⚠️ 只在 annotate 时打点：导出要逐字节一致
        attrs.append(f'data-slot="{esc(slot)}"')
        w = sum(char_w(ch, size) for ch in str(s))
        bx = x - (w / 2 if anchor == "middle" else (w if anchor == "end" else 0))
        cv.box(slot, bx, y - size * 0.86, max(w, size), size * 1.26, size, align=anchor)
    return f'<text {" ".join(attrs)}>{esc(s)}</text>'


def line(x1: float, y1: float, x2: float, y2: float, color: str, w: float = 1) -> str:
    return (f'<line x1="{round(x1, 2)}" y1="{round(y1, 2)}" x2="{round(x2, 2)}" y2="{round(y2, 2)}" '
            f'stroke="{color}" stroke-width="{round(w, 2)}"/>')


def rect(x: float, y: float, w: float, h: float, *, fill: str = "none", stroke: str | None = None,
         sw: float = 1, r: float = 0) -> str:
    attrs = [f'x="{round(x, 2)}"', f'y="{round(y, 2)}"', f'width="{round(w, 2)}"',
             f'height="{round(h, 2)}"', f'fill="{fill}"']
    if stroke:
        attrs += [f'stroke="{stroke}"', f'stroke-width="{round(sw, 2)}"']
    if r:
        attrs.append(f'rx="{round(r, 2)}"')
    return f'<rect {" ".join(attrs)}/>'


def polyline(points: list[tuple[float, float]], color: str, w: float = 2) -> str:
    pts = " ".join(f"{round(x, 2)},{round(y, 2)}" for x, y in points)
    return f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="{round(w, 2)}"/>'


# ---------------------------------------------------------------- 文字换行

def char_w(ch: str, size: float) -> float:
    """估算字宽：CJK/全角按 1 字宽，西文 0.55，空格 0.3（够用，且不截断文字）。"""
    if ord(ch) > 0x2E80:
        return size
    if ch == " ":
        return size * 0.30
    return size * 0.55


def wrap(s: object, max_w: float, size: float) -> list[str]:
    """按宽度换行；**该换几行就换几行**（排不下就往下长，交给审计与密度提示去报）。

    ⚠️ 曾经写成"到 max_lines 就把余下内容全并进最后一行"—— 结果 400 字的块变成
    **一个 6456px 宽的文字元素**（横向溢出 404%），转换器直接拒绝、导出静默回退旧版式（踩过）。
    "绝不截断"说的是**不删字**，不是"不许换行"：所以现在一律按宽度排满。
    """
    txt = "" if s is None else str(s)
    if not txt:
        return []
    lines: list[str] = []
    cur, cur_w = "", 0.0
    for ch in txt:
        w = char_w(ch, size)
        if cur and cur_w + w > max_w:
            lines.append(cur)
            cur, cur_w = "", 0.0
        cur += ch
        cur_w += w
    if cur:
        lines.append(cur)
    return lines


def paragraph(cv: Canvas, x: float, y: float, s: object, *, size: float, max_w: float, lh: float,
              weight: int | None = None, fill: str | None = None, slot: str = "") -> tuple[str, float]:
    """多行文字；返回 (SVG 片段, 下一次可用的基线 y)。

    `slot` 给了就记**一个盒子盖住整块**（宽 = 版心宽，高 = 行数 × 行距）——
    点哪儿都能进编辑，比逐行小盒子好用（R10）。
    **覆盖（拖动/改宽/字号色/对齐）在每一行上生效**：行内对齐按块的宽度算，所以居中/右对齐不会跑偏。
    """
    o = cv.ov(slot) if slot else {}
    if o.get("hidden"):
        return "", y
    size = float(o.get("size") or size)
    fill = o.get("color") or fill
    max_w = max(40.0, max_w + float(o.get("dw") or 0))
    dx, dy = float(o.get("dx") or 0), float(o.get("dy") or 0)
    lines = wrap(s, max_w, size)
    step = size * lh
    out = []
    for i, ln in enumerate(lines):
        lx = x + dx
        anchor = "start"
        if o.get("align") == "middle":
            lx, anchor = lx + max_w / 2, "middle"
        elif o.get("align") == "end":
            lx, anchor = lx + max_w, "end"
        out.append(text(cv, lx, y + dy + i * step, ln, size=size, weight=weight, fill=fill,
                        anchor=anchor, slot=(slot if i == 0 else ""), zkey=slot))
    svg = "".join(out)
    if slot and lines:
        cv.box(slot, x, y - size * 0.86, max_w, max(1, len(lines)) * step + size * 0.2, size)
    return svg, y + max(1, len(lines)) * step


# ---------------------------------------------------------------- 版面零件

def title_block(cv: Canvas, slots: dict) -> tuple[str, float]:
    """页标题区：可选小节标签 + 标题 + 主色短记号 + 细线。返回 (片段, 正文起始 y)。"""
    out = []
    y = cv.margin
    if slots.get("label"):
        out.append(text(cv, cv.margin, y + cv.size("label") * 0.9, slots["label"],
                        size=cv.size("label"), weight=cv.weight("label"), fill=cv.color("sub"),
                        slot="label"))
        y += cv.size("label") * 1.9
    y += cv.size("title") * 0.82
    out.append(text(cv, cv.margin, y, slots.get("title") or "", size=cv.size("title"),
                    weight=cv.weight("title"), slot="title"))
    cv.box("title", cv.margin, cv.margin, cv.content_w, y - cv.margin + cv.size("title") * 0.4,
           cv.size("title"))                       # 标题的点击区给整行（文字可能短，别只框住那几个字）
    rule_y = y + cv.size("title") * 0.6
    out.append(rect(cv.margin, rule_y - 2, 44, 2, fill=cv.marker))
    out.append(line(cv.margin + 60, rule_y, cv.right, rule_y, cv.color("line"), 1))
    return "".join(out), rule_y + cv.size("body") * 2.2


def footer(cv: Canvas, index: int, total: int) -> str:
    """页脚细线 + 页码 x/N（画在安全边距内）。页码**只读**（不给编辑盒子）。"""
    return (line(cv.margin, cv.footer_y - 22, cv.right, cv.footer_y - 22, cv.color("line"), 1) +
            text(cv, cv.right, cv.footer_y, f"{index + 1} / {total}", size=cv.size("caption"),
                 fill=cv.color("sub"), anchor="end"))


def blocks_of(slots: dict) -> list[dict]:
    """把 `bullets[]`（纯字符串）与 `blocks[]`（带 label）统一成块列表。"""
    raw = slots.get("blocks")
    if raw is None:
        raw = [{"text": b} for b in (slots.get("bullets") or [])]
    return [b if isinstance(b, dict) else {"text": b} for b in raw]


def distribute(y_top: float, y_bottom: float, heights: list[float], base_gap: float,
               *, gap_max_ratio: float = 2.4, bottom_room: float = 24.0) -> list[float]:
    """把若干块垂直铺在 [y_top, y_bottom] 里，返回每块的起始 y。

    块少时不要全部挤在顶上（那会让页面显得头重、空白率超标）：间距按剩余空间放大
    （上限 `gap_max_ratio` 倍基准间距），余量对半分在上下 —— 也就是"垂直方向也留白"。
    """
    n = len(heights)
    if n == 0:
        return []
    slack = max(0.0, (y_bottom - bottom_room - y_top) - sum(heights))
    if n > 1:
        gap = min(base_gap * gap_max_ratio, max(base_gap, slack / (n + 1)))
    else:
        gap = base_gap
    lead = max(0.0, (slack - gap * (n - 1)) / 2)
    out: list[float] = []
    y = y_top + lead
    for h in heights:
        out.append(y)
        y += h + gap
    return out


def apply_density(cv: Canvas, items: list, warnings: list[str], where: str) -> list:
    """每页模块数超上限 → 只排前 N 个并提示拆页（**绝不截断单块文字**）。"""
    limit = int(cv.t["density"]["blocks_max"])
    if len(items) > limit:
        warnings.append(f"{where}：模块 {len(items)} 个 > 上限 {limit} → 建议拆页"
                        f"（本页只排了前 {limit} 个，其余请另起一页）")
        return items[:limit]
    return items


# ---------------------------------------------------------------- 六个版式

def _cover(cv: Canvas, slots: dict, index: int, total: int, warnings: list[str]) -> str:
    del warnings                                    # 封面不涉及密度上限
    out = []
    if slots.get("label"):
        out.append(text(cv, cv.margin, cv.margin + cv.size("label") * 0.9, slots["label"],
                        size=cv.size("label"), weight=cv.weight("label"), fill=cv.color("sub"),
                        slot="label"))
    y = cv.h * 0.42
    out.append(text(cv, cv.margin, y, slots.get("title") or "", size=cv.size("cover"),
                    weight=cv.weight("cover"), slot="title"))
    cv.box("title", cv.margin, y - cv.size("cover"), cv.content_w, cv.size("cover") * 1.6,
           cv.size("cover"))
    out.append(rect(cv.margin, y + cv.size("cover") * 0.56, 96, 3, fill=cv.marker))
    if slots.get("subtitle"):
        out.append(text(cv, cv.margin, y + cv.size("cover") * 1.2, slots["subtitle"],
                        size=cv.size("body"), fill=cv.color("sub"), slot="subtitle"))
    if slots.get("meta"):
        out.append(line(cv.margin, cv.footer_y - 34, cv.right, cv.footer_y - 34, cv.color("line"), 1))
        out.append(text(cv, cv.margin, cv.footer_y, slots["meta"], size=cv.size("label"),
                        fill=cv.color("sub"), slot="meta"))
    return "".join(out)


def _section(cv: Canvas, slots: dict, index: int, total: int, warnings: list[str]) -> str:
    """章节过渡：大号序号 + 环节名 + 整页大留白。"""
    del warnings                                    # 章节页不涉及密度上限
    size = cv.size("cover")
    y = cv.h * 0.46
    out = [rect(cv.margin, y - size * 0.86, 2, size * 1.42, fill=cv.marker)]
    x = cv.margin + 32
    out.append(text(cv, x, y, slots.get("index") or "", size=size, weight=cv.weight("cover"),
                    fill=cv.marker))
    out.append(text(cv, x, y + size, slots.get("title") or "", size=cv.size("title"),
                    weight=cv.weight("title"), slot="title"))
    cv.box("title", x, y + size - cv.size("title"), cv.right - x, cv.size("title") * 1.5,
           cv.size("title"))
    return "".join(out)


def _bullets(cv: Canvas, slots: dict, index: int, total: int, warnings: list[str]) -> str:
    """要点页：带标签的块（上游明确"避免密集列点 → labeled blocks"）。"""
    head, y_top = title_block(cv, slots)
    out = [head]
    size, lh = cv.size("body"), float(cv.type["lh_body"])
    lab = cv.size("label")
    items = apply_density(cv, blocks_of(slots), warnings, f"第 {index + 1} 页")
    max_w = cv.content_w - 24
    heights, drawn = [], []
    for it in items:
        lines = wrap(it.get("text"), max_w, size)
        h = (lab * 1.7 if it.get("label") else 0) + max(1, len(lines)) * size * lh
        heights.append(h)
        drawn.append(lines)
    tops = distribute(y_top, cv.footer_y, heights, size * float(cv.type.get("para_gap_ratio") or 0.6))
    for bi, (it, lines, y) in enumerate(zip(items, drawn, tops)):
        out.append(rect(cv.margin, y, 2, 14, fill=cv.marker))      # 主色短竖线（不套卡片）
        ty = y
        if it.get("label"):
            out.append(text(cv, cv.margin + 24, ty + lab * 0.9, it["label"], size=lab,
                            weight=cv.weight("label"), fill=cv.marker, slot=f"block:{bi}:label"))
            ty += lab * 1.7
        for i, ln in enumerate(lines):
            out.append(text(cv, cv.margin + 24, ty + size * 0.82 + i * size * lh, ln, size=size,
                            slot=(f"block:{bi}" if i == 0 else "")))
        cv.box(f"block:{bi}", cv.margin + 24, ty - size * 0.86, max_w,
               max(1, len(lines)) * size * lh + size * 0.2, size)
    return "".join(out)


def _two_col(cv: Canvas, slots: dict, index: int, total: int, warnings: list[str]) -> str:
    """两栏：对比 / 并列；中间一条细线做结构。"""
    head, y_top = title_block(cv, slots)
    out = [head]
    col_w = (cv.content_w - cv.gutter * 2) / 2
    mid_x = cv.margin + col_w + cv.gutter
    out.append(line(mid_x - cv.gutter / 2, y_top - cv.size("body"), mid_x - cv.gutter / 2,
                    cv.footer_y - 40, cv.color("line"), 1))
    size, lh = cv.size("body"), float(cv.type["lh_body"])
    for i, key in enumerate(("left", "right")):
        col = slots.get(key) or {}
        x = cv.margin + i * (col_w + cv.gutter * 2)
        items = apply_density(cv, col.get("bullets") or [], warnings, f"第 {index + 1} 页·{key}")
        heights, lines_list = [], []
        for b in items:
            ls = wrap(b, col_w, size)
            heights.append(max(1, len(ls)) * size * lh)
            lines_list.append(ls)
        head_h = size * 1.6 if col.get("heading") else 0.0
        if col.get("heading"):
            out.append(text(cv, x, y_top, col["heading"], size=size, weight=700,
                            slot=f"{key}:heading"))
        for j, (ls, yy) in enumerate(zip(lines_list, distribute(y_top + head_h, cv.footer_y,
                                                                heights, size * 0.5))):
            for k, ln in enumerate(ls):
                out.append(text(cv, x, yy + size * 0.82 + k * size * lh, ln, size=size,
                                slot=(f"{key}:bullet:{j}" if k == 0 else "")))
            cv.box(f"{key}:bullet:{j}", x, yy - size * 0.86, col_w,
                   max(1, len(ls)) * size * lh + size * 0.2, size)
    return "".join(out)


def _media(cv: Canvas, slots: dict, index: int, total: int, warnings: list[str]) -> str:
    """图文：左边图示（纯矢量），右边注释 + 要点。"""
    head, y = title_block(cv, slots)
    out = [head]
    size = cv.size("body")
    box_w = cv.span_w(7)
    box_h = min(340.0, cv.footer_y - 40 - y)
    out.append(rect(cv.margin, y, box_w, box_h, fill=cv.color("soft"), stroke=cv.color("line"), sw=1, r=2))
    out.append(_chart(cv, cv.margin + 36, y + 36, box_w - 72, box_h - 72))
    x = cv.margin + box_w + cv.gutter * 1.5
    max_w = cv.right - x
    yy = y + size * 0.9
    if slots.get("caption"):
        svg, yy = paragraph(cv, x, yy, slots["caption"], size=cv.size("caption"), max_w=max_w,
                            lh=1.45, fill=cv.color("sub"), slot="caption")
        out.append(svg)
        yy += size * 0.9
    for bi, b in enumerate(apply_density(cv, slots.get("bullets") or [], warnings,
                                         f"第 {index + 1} 页")):
        out.append(text(cv, x, yy, "—", size=size, fill=cv.marker))
        svg, yy = paragraph(cv, x + size * 0.9, yy, b, size=size, max_w=max_w - size * 0.9,
                            lh=float(cv.type["lh_body"]), slot=f"block:{bi}")
        out.append(svg)
        yy += size * 0.55
    return "".join(out)


def _chart(cv: Canvas, x: float, y: float, w: float, h: float) -> str:
    """幂函数 y = x² 示意：坐标轴 + 曲线 + 少量刻度（纯矢量，无图片、无渐变、无阴影）。"""
    out = []
    axis, sub = cv.color("line"), cv.color("sub")
    label = cv.size("label")
    x0, y0 = x + w * 0.38, y + h * 0.88          # 原点（左短右长：y=x² 的右半支更高，要留地方）
    xl, xr, yt = w * 0.32, w * 0.58, h * 0.74
    out.append(line(x0 - xl, y0, x0 + xr, y0, axis, 1))
    out.append(line(x0, y0, x0, y0 - yt, axis, 1))
    span, ymax = 1.5, 2.25
    pts = []
    for i in range(61):
        vx = -span + 2 * span * i / 60
        pts.append((x0 + vx / span * (xl if vx < 0 else xr), y0 - min(vx * vx, ymax) / ymax * yt))
    out.append(polyline(pts, cv.color("accent"), 2))
    for t in (-1, 0, 1):
        tx = x0 + t / span * (xl if t < 0 else xr)
        out.append(line(tx, y0, tx, y0 + 5, axis, 1))
        out.append(text(cv, tx, y0 + 5 + label * 1.2, str(t), size=label, fill=sub, anchor="middle"))
    for t in (1, 2):
        ty = y0 - t / ymax * yt
        out.append(line(x0 - 5, ty, x0, ty, axis, 1))
        out.append(text(cv, x0 - 9, ty + label * 0.36, str(t), size=label, fill=sub, anchor="end"))
    out.append(text(cv, x0 + xr, y0 + 5 + label * 1.2, "x", size=label, fill=sub, anchor="middle"))
    out.append(text(cv, x0 - 9, y0 - yt - label * 0.4, "y", size=label, fill=sub, anchor="end"))
    return "".join(out)


def _end(cv: Canvas, slots: dict, index: int, total: int, warnings: list[str]) -> str:
    """结尾：编号小结（≤3 条）+ 一句话收束。"""
    head, y = title_block(cv, slots)
    out = [head]
    size = cv.size("body")
    for i, b in enumerate(apply_density(cv, slots.get("bullets") or [], warnings, f"第 {index + 1} 页")):
        out.append(text(cv, cv.margin, y + i * size * 2.1, str(i + 1), size=size * 0.9,
                        weight=cv.weight("label"), fill=cv.marker))
        out.append(text(cv, cv.margin + 40, y + i * size * 2.1, b, size=size, slot=f"block:{i}"))
        cv.box(f"block:{i}", cv.margin + 40, y + i * size * 2.1 - size * 0.86,
               cv.content_w - 40, size * 1.3, size)
    if slots.get("closing"):
        cy = cv.footer_y - 70
        out.append(line(cv.margin, cy - 34, cv.right, cy - 34, cv.color("line"), 1))
        out.append(text(cv, cv.margin, cy, slots["closing"], size=cv.size("caption"),
                        fill=cv.marker, slot="closing"))
    return "".join(out)


_RENDERERS = {"cover": _cover, "section": _section, "bullets": _bullets,
              "two-col": _two_col, "media": _media, "end": _end}


# ---------------------------------------------------------------- 对外接口

#: 出图元素（够用的最小集合：正文只可能是这四种）
_EL_RE = re.compile(r"<(?P<tag>text|rect|line|polyline)\b[^>]*?(?:/>|>.*?</(?P=tag)>)", re.S)
_ZATTR_RE = re.compile(r'\s+data-zslot="[^"]*"')


def order_z(cv: Canvas, body: str) -> str:
    """按「层级」重排出图顺序，然后把层级记号洗掉（R14）。

    做法（为什么不改六个版式函数）：发射时给"老师调过层级"的元素打一个 `data-zslot` 记号，
    这里把**所有文字元素**按 `(层级, 原来的先后)` 稳定排序后填回**文字元素占的那些位置** ——
    静态图元（底纹/短横线/圆点）不动，只有文字之间换层，正是老师"把这块盖到那块上面"的意思。
    **没有 z 覆盖时一个字节都不碰**（`cv.has_z` 为假直接返回），导出与老版本逐字节一致。

    ⚠️ 元素正则在正文里找 `<text|rect|line|polyline>`：文字内容都过了 `esc()`，
    正文里不可能再出现 `<`，所以这样切是安全的（也正因如此才不引入 XML 解析器）。
    """
    if not cv.has_z:
        return body
    spans = list(_EL_RE.finditer(body))
    texts = [i for i, m in enumerate(spans) if m.group("tag") == "text"]
    if len(texts) < 2:
        return _ZATTR_RE.sub("", body) if "data-zslot=" in body else body
    zs: dict[str, int] = {}
    items: list[tuple[int, int, str]] = []
    for order, i in enumerate(texts):
        el = spans[i].group(0)
        key = re.search(r'data-zslot="([^"]*)"', el)
        z = 0
        if key:
            if key.group(1) not in zs:
                zs[key.group(1)] = cv.zof(key.group(1))
            z = zs[key.group(1)]
        items.append((z, order, el))
    items.sort(key=lambda t: (t[0], t[1]))           # 稳定：同层保持原来的先后
    els = [m.group(0) for m in spans]
    for i, (_, _, el) in zip(texts, items, strict=False):
        els[i] = el
    pieces: list[str] = []
    prev = 0
    for m, el in zip(spans, els, strict=False):
        pieces.append(body[prev:m.start()])
        pieces.append(el)
        prev = m.end()
    pieces.append(body[prev:])
    return _ZATTR_RE.sub("", "".join(pieces))


def render_slide(t: dict, layout: str, slots: dict | None = None, *, index: int = 0, total: int = 1,
                 warnings: list[str] | None = None, boxes: list[dict] | None = None,
                 overrides: dict | None = None) -> str:
    """渲染一页，返回完整 SVG 文本（画布底色 + 版式 + 页脚；封面不画页码）。

    `boxes` 给了一个列表（`annotate`）时：给每个文字槽打 `data-slot` 并收集**可编辑盒子**
    （设计像素 1280×720）。**导出路径不传它** —— 导出用的 SVG 与以前逐字节一致（`docs/25` D8）。

    `overrides` 是老师改过的样子（`slides[i].slots`）：`{槽位: {dx,dy,dw,dh,size,color,align,lh,z,hidden}}`，
    在发射时套上 —— **预览与导出走同一条路**，所以"看到什么样，导出就是什么样"。
    """
    if layout not in _RENDERERS:
        raise ValueError(f"没有这个版式：{layout}（可选：{', '.join(LAYOUTS)}）")
    cv = Canvas(t, boxes, overrides)
    warn = warnings if warnings is not None else []
    sl = slots or {}
    body = order_z(cv, _RENDERERS[layout](cv, sl, index, total, warn))
    tail = "" if layout == "cover" else footer(cv, index, total)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{cv.w}" height="{cv.h}" '
            f'viewBox="0 0 {cv.w} {cv.h}">' + rect(0, 0, cv.w, cv.h, fill=cv.bg) + body + tail + "</svg>")


def render_deck(t: dict, slides: list[dict], warnings: list[str] | None = None) -> list[str]:
    """渲染整册：`slides = [{"layout": "cover", "title": ...}, ...]`，一页一个 SVG。"""
    warn = warnings if warnings is not None else []
    total = len(slides)
    return [render_slide(t, s.get("layout") or "bullets", s, index=i, total=total, warnings=warn)
            for i, s in enumerate(slides)]


def slide_warnings(t: dict, slides: list[dict]) -> list[str]:
    """只跑密度/上限检查（面板提示用），不返回 SVG。"""
    warn: list[str] = []
    render_deck(t, slides, warn)
    return warn


if __name__ == "__main__":       # 手工冒烟：python -m edu_agent.ppt_svg [主题]
    import sys

    theme = sys.argv[1] if len(sys.argv) > 1 else (tk.available() or ["tech-editorial"])[0]
    data = tk.load(theme)
    pages = render_deck(data, [{"layout": "cover", "title": "冒烟"},
                               {"layout": "bullets", "title": "要点", "bullets": ["一", "二"]}])
    print(f"{theme}: {len(pages)} 页 · 首页 {len(pages[0])} 字符")
