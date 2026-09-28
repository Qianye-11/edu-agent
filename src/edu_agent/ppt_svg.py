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
from . import slide_image

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
            *, kind: str = "text", align: str = "start", obj: dict | None = None) -> None:
        """记一个可编辑盒子（`slot` 为空或没开 annotate 就什么都不做）。

        **同一个槽只留一个盒子**（保留面积更大的那个）：`text()` 会自动给单行做个小盒子，
        而版式里往往随后又显式给一个"整行/整块"的大盒子（点击区更友好）—— 两个都留会让前端
        画出两块重叠的点击区（踩过：`title` 出现两次）。
        **覆盖要在这里套上**（拖动/改大小长在盒子上），这样前端画的框与服务端画的字永远一致。

        `kind`：`text`（版式槽位）/ `obj-text` / `obj-shape`（R21 自由层）。
        `obj`：自由层对象自己的信息（形状名等），前端据此画不同的框与右栏。
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
        if obj:
            entry["obj"] = {k: v for k, v in obj.items() if v not in (None, "")}
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

    def size_of(self, value, default: float) -> float:
        """给自由层用的字号：老师没给就用版式正文的档位（不写死数字）。"""
        try:
            v = float(value)
        except (TypeError, ValueError):
            v = 0.0
        return v if v >= 8 else float(default)

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
         fill: str | None = None, anchor: str = "start", slot: str = "", zkey: str = "",
         ov: dict | None = None) -> str:
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
    o = ov if ov is not None else (cv.ov(slot) if slot else {})
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
    if o.get("underline"):
        attrs.append('text-decoration="underline"')
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


def image(x: float, y: float, w: float, h: float, href: str, *, fit: str = "cover") -> str:
    """一张图（`href` 是 `data:` URI，由服务端读 `data/uploads/` 现算 —— 预览不写盘）。

    · `preserveAspectRatio="xMidYMid slice"` = 铺满并居中裁切（WPS 里"填充"那种），
      比留白更常见；图片比例与框不一致时不会变形；
    · **只认 data: URI**：外链在导出（离线转换）与预览里都可能取不到，会静默破图（R7 踩过）；
    · 用 SVG2 的 `href`（不用 `xlink:href`）：这样不必给根节点加 `xmlns:xlink`，
      没图的那些页仍然逐字节不变（有单测钉住）。
    """
    if not href or not str(href).startswith("data:"):
        return ""
    par = "xMidYMid slice" if fit == "cover" else "xMidYMid meet"
    return (f'<image x="{round(x, 2)}" y="{round(y, 2)}" width="{round(w, 2)}" '
            f'height="{round(h, 2)}" preserveAspectRatio="{par}" href="{esc(href)}"/>')


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
              weight: int | None = None, fill: str | None = None, slot: str = "",
              ov: dict | None = None, align: str | None = None,
              box_slot: str = "") -> tuple[str, float]:
    """多行文字；返回 (SVG 片段, 下一次可用的基线 y)。

    `slot` 给了就记**一个盒子盖住整块**（宽 = 版心宽，高 = 行数 × 行距）——
    点哪儿都能进编辑，比逐行小盒子好用（R10）。
    **覆盖（拖动/改宽/字号色/对齐）在每一行上生效**：行内对齐按块的宽度算，所以居中/右对齐不会跑偏。

    `ov` / `align`（R21）：自由层对象的样式**长在对象自己身上**（不进 `slides[i].slots`），
    所以允许直接把那套样式传进来，而不是只能从 `cv.ov(slot)` 取。
    `box_slot`：盒子记到哪个槽名下（形状上叠字时：文字槽与形状槽要分开，否则会互相顶掉）。
    """
    o = ov if ov is not None else (cv.ov(slot) if slot else {})
    if o.get("hidden"):
        return "", y
    size = float(o.get("size") or size)
    fill = o.get("color") or fill
    max_w = max(40.0, max_w + float(o.get("dw") or 0))
    dx, dy = float(o.get("dx") or 0), float(o.get("dy") or 0)
    al = align if align in ("start", "middle", "end") else o.get("align")
    lines = wrap(s, max_w, size)
    step = size * lh
    out = []
    for i, ln in enumerate(lines):
        lx = x + dx
        anchor = "start"
        if al == "middle":
            lx, anchor = lx + max_w / 2, "middle"
        elif al == "end":
            lx, anchor = lx + max_w, "end"
        out.append(text(cv, lx, y + dy + i * step, ln, size=size, weight=weight, fill=fill,
                        anchor=anchor, slot=(slot if i == 0 else ""), zkey=slot, ov=o))
    svg = "".join(out)
    if (slot or box_slot) and lines:
        cv.box(box_slot or slot, x, y - size * 0.86, max_w, max(1, len(lines)) * step + size * 0.2, size)
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
    """图文：左边图示（**有图就放老师的图**，没图就用纯矢量示意），右边注释 + 要点。"""
    head, y = title_block(cv, slots)
    out = [head]
    size = cv.size("body")
    box_w = cv.span_w(7)
    box_h = min(340.0, cv.footer_y - 40 - y)
    out.append(rect(cv.margin, y, box_w, box_h, fill=cv.color("soft"), stroke=cv.color("line"), sw=1, r=2))
    pic = image(cv.margin, y, box_w, box_h, str(slots.get("image") or ""))
    if pic:
        out.append(pic)                       # 图直接铺满那个软色框（不外链、不变形）
        credit = str(slots.get("image_credit") or "").strip()
        if credit:                            # 出处：老师给的图也要署上（CC 图源的硬要求）
            csize = max(10.0, cv.size("caption") * 0.8)
            cw = min(box_w - 16, sum(char_w(ch, csize) for ch in credit[:40]) + 14)
            cy = y + box_h - csize * 2.2
            out.append(rect(cv.margin + 6, cy, cw, csize * 1.6, fill=cv.color("text"), r=2))
            out.append(text(cv, cv.margin + 13, cy + csize * 1.15, credit[:40],
                            size=csize, fill=cv.bg))
    else:
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


# ---------------------------------------------------------------- 自由层（R21）
#
# 老师在页面上**自己插入**的文本框与形状。两条硬要求：
#
# 1. **导出必须还是原生可编辑形状**：形状发的是 ppt-master 认的规范预设组
#    （`<g data-pptx-authoring="preset" data-pptx-object="shape" data-pptx-prst="rect"
#      data-pptx-frame="x y w h" fill stroke>` + `<path>`），它的 `svg_to_pptx.py`
#    照着这组属性直接产出 DrawingML 预设形状；我们**不发明第二种画法**。
# 2. **几何不能自己编**：下面每个预设的路径都来自 `.deps/ppt-master` 的
#    `preset_shape_svg.py render`（单位框 1000×1000 的实测输出，圆角半径 = min(w,h)/6），
#    `tests/test_slide_objects.py` 里有一条**黄金测试**逐预设逐尺寸与那个脚本对拍 ——
#    对不上就红，免得哪天悄悄漂开（"预览=导出"就是这么守住的）。
#
# 为什么不每次渲染都去调那个脚本：单次调用 ~0.42 秒，一页三个形状就要一秒多，而这是**每次编辑**
# 都要走的路。脚本只在"对拍测试"里用。
SHAPE_PRESETS = ("rect", "roundRect", "ellipse", "triangle", "parallelogram", "chevron",
                 "rightArrow", "line")


def _fmt(v: float) -> str:
    s = f"{float(v):.2f}".rstrip("0").rstrip(".")
    return s if s not in ("-0", "") else "0"


def preset_path(preset: str, x: float, y: float, w: float, h: float) -> str:
    """按规范公式算出预设形状的 `d`（与 `preset_shape_svg.py` 的输出一致，见上）。"""
    x2, y2 = x + w, y + h
    if preset == "rect":
        return f"M {_fmt(x)} {_fmt(y)} L {_fmt(x2)} {_fmt(y)} L {_fmt(x2)} {_fmt(y2)} L {_fmt(x)} {_fmt(y2)} Z"
    if preset == "triangle":
        return f"M {_fmt(x)} {_fmt(y2)} L {_fmt(x + w / 2)} {_fmt(y)} L {_fmt(x2)} {_fmt(y2)} Z"
    if preset == "parallelogram":
        sk = h / 4                                  # 实测：斜边偏移按**高**算（不是宽）
        return (f"M {_fmt(x)} {_fmt(y2)} L {_fmt(x + sk)} {_fmt(y)} L {_fmt(x2)} {_fmt(y)} "
                f"L {_fmt(x2 - sk)} {_fmt(y2)} Z")
    if preset == "chevron":
        k = h / 2                                   # 实测：尖角伸出的距离 = 高的一半
        return (f"M {_fmt(x)} {_fmt(y)} L {_fmt(x2 - k)} {_fmt(y)} L {_fmt(x2)} {_fmt(y + h / 2)} "
                f"L {_fmt(x2 - k)} {_fmt(y2)} L {_fmt(x)} {_fmt(y2)} L {_fmt(x + k)} {_fmt(y + h / 2)} Z")
    if preset == "rightArrow":
        hx, sx = h / 4, x2 - h / 2                  # 实测：杆高 = h/2（上下各留 h/4），箭头宽 = h/2
        return (f"M {_fmt(x)} {_fmt(y + hx)} L {_fmt(sx)} {_fmt(y + hx)} L {_fmt(sx)} {_fmt(y)} "
                f"L {_fmt(x2)} {_fmt(y + h / 2)} L {_fmt(sx)} {_fmt(y2)} L {_fmt(sx)} {_fmt(y2 - hx)} "
                f"L {_fmt(x)} {_fmt(y2 - hx)} Z")
    if preset == "ellipse":
        rx, ry, cx, cy = w / 2, h / 2, x + w / 2, y + h / 2
        return (f"M {_fmt(x)} {_fmt(cy)} A {_fmt(rx)} {_fmt(ry)} 0 0 1 {_fmt(cx)} {_fmt(y)} "
                f"A {_fmt(rx)} {_fmt(ry)} 0 0 1 {_fmt(x2)} {_fmt(cy)} "
                f"A {_fmt(rx)} {_fmt(ry)} 0 0 1 {_fmt(cx)} {_fmt(y2)} "
                f"A {_fmt(rx)} {_fmt(ry)} 0 0 1 {_fmt(x)} {_fmt(cy)} Z")
    if preset == "roundRect":
        r = min(w, h) / 6                          # 实测：单位框 166.67 = 1000/6；300×120 → 20 = 120/6
        return (f"M {_fmt(x)} {_fmt(y + r)} A {_fmt(r)} {_fmt(r)} 0 0 1 {_fmt(x + r)} {_fmt(y)} "
                f"L {_fmt(x2 - r)} {_fmt(y)} A {_fmt(r)} {_fmt(r)} 0 0 1 {_fmt(x2)} {_fmt(y + r)} "
                f"L {_fmt(x2)} {_fmt(y2 - r)} A {_fmt(r)} {_fmt(r)} 0 0 1 {_fmt(x2 - r)} {_fmt(y2)} "
                f"L {_fmt(x + r)} {_fmt(y2)} A {_fmt(r)} {_fmt(r)} 0 0 1 {_fmt(x)} {_fmt(y2 - r)} Z")
    if preset == "line":
        return f"M {_fmt(x)} {_fmt(y)} L {_fmt(x + w)} {_fmt(y + h)}"
    return ""


def preset_group(obj: dict) -> str:
    """一个自由层形状 → 规范预设组（ptt-master 直接认）。颜色/描边都显式给，绝不靠默认值。

    ⚠️ 两条是 ppt-master 的**硬要求**（它自己的质量检查会判 error，导出直接失败 —— 实测踩过）：
    1. `<g>` 必须带一个**稳定的 `id`**（"Authored preset logical group requires a stable id"）；
    2. 只要 `stroke` 不是 `none`，就必须给 `stroke-width`（"visible stroke requires stroke-width"）。
    """
    preset = str(obj.get("shape") or "rect")
    if preset not in SHAPE_PRESETS:
        preset = "rect"
    x, y = float(obj.get("x") or 0), float(obj.get("y") or 0)
    w, h = max(1.0, float(obj.get("w") or 1)), max(1.0, float(obj.get("h") or 1))
    fill = str(obj.get("fill") or "")
    stroke = str(obj.get("stroke") or "")
    if preset == "line":
        fill, stroke = "none", (stroke or str(obj.get("color") or "#1A1A1A"))
    else:
        fill = fill or "#DCE6F5"
        stroke = stroke or "none"
    kind = "connector" if preset == "line" else "shape"
    sw = f' stroke-width="{_fmt(obj.get("stroke_w") or 1)}"' if stroke != "none" else ""
    # ⚠️ 子元素**只能**是 `<path>`：实测把矩形写成 `<rect>` 会被判
    # "Compact authored preset is atomic and may contain only direct SVG paths"（导出直接失败）。
    return (f'<g id="{esc(obj.get("id") or "obj")}" data-pptx-authoring="preset" '
            f'data-pptx-object="{kind}" data-pptx-prst="{esc(preset)}" '
            f'data-pptx-frame="{_fmt(x)} {_fmt(y)} {_fmt(w)} {_fmt(h)}" '
            f'fill="{esc(fill)}" stroke="{esc(stroke)}"{sw}>'
            f'<path d="{preset_path(preset, x, y, w, h)}"/></g>')


def draw_objects(cv: Canvas, objects: list[dict] | None) -> str:
    """把自由层的对象画出来（形状 → 规范预设组；文本框 → 框内文字，可编辑）。

    顺序 = 数组顺序（后画的在上面）；`上移一层/下移一层` 就是换数组位置 —— 不再引入第二套 z，
    免得和版式槽位的 `z` 覆盖打架（那套是"版式内部谁压谁"）。
    枚举时给可编辑盒子打 `data-slot="obj:<id>"`，前端就能用同一套选中/拖动/改大小。
    """
    out: list[str] = []
    for i, o in enumerate(objects or []):
        if not isinstance(o, dict):
            continue
        oid = str(o.get("id") or f"o{i + 1}")
        slot = f"obj:{oid}"
        x, y = float(o.get("x") or 0), float(o.get("y") or 0)
        w, h = max(1.0, float(o.get("w") or 1)), max(1.0, float(o.get("h") or 1))
        if o.get("kind") == "shape":
            out.append(preset_group(o))
            if cv.boxes is not None:
                cv.box(slot, x, y, w, h, float(o.get("size") or 0), align="start",
                       kind="obj-shape", obj={"shape": o.get("shape"), "name": o.get("name") or ""})
            if str(o.get("text") or "").strip():
                out.append(_object_text(cv, o, x, y, w, h, slot))
            continue
        if o.get("kind") == "image":
            # R22：老师上传的图。**内联成 data: URI**（预览与导出都不依赖外部文件），
            # `fit` 决定铺满裁切（cover）还是完整收进框里（contain）。
            href = slide_image.data_uri(str(o.get("src") or ""))
            if href:
                out.append(image(x, y, w, h, href, fit=str(o.get("fit") or "cover")))
            if cv.boxes is not None:
                cv.box(slot, x, y, w, h, 0.0, align="start", kind="obj-image",
                       obj={"shape": "image", "src": str(o.get("src") or "")})
            if str(o.get("text") or "").strip():          # 图上也能配一行说明
                out.append(_object_text(cv, o, x, y, w, h, slot))
            continue
        # 文本框：先描一个软色底（"有框"的观感），再放字
        if o.get("fill"):
            out.append(rect(x, y, w, h, fill=str(o["fill"]), stroke=str(o.get("stroke") or "none"),
                            sw=float(o.get("stroke_w") or 1), r=float(o.get("radius") or 0)))
        if cv.boxes is not None:
            cv.box(slot, x, y, w, h, float(o.get("size") or 0), align=str(o.get("align") or "start"),
                   kind="obj-text", obj={"shape": "text"})
        out.append(_object_text(cv, o, x, y, w, h, slot))
    return "".join(out)


def _object_text(cv: Canvas, o: dict, x: float, y: float, w: float, h: float, slot: str) -> str:
    """自由层里的一段文字（文本框本体，或形状上叠的字）。**一样会换行、一样不截断。**

    盒子的槽名：文本框用 `obj:<id>`；形状上叠的字用 `obj:<id>:text` —— 否则文字盒子会把
    形状盒子顶掉（`Canvas.box` 同槽只留一个），那就点不到形状、也拖不动它了。
    """
    text = str(o.get("text") or "")
    if not text.strip():
        return ""
    size = cv.size_of(o.get("size"), 24.0)
    align = str(o.get("align") or "start")
    pad = size * 0.5
    box_slot = slot if o.get("kind") == "text" else (slot + ":text")
    svg, _y = paragraph(
        cv, x + pad, y + size * 0.95, text, size=size, max_w=max(24.0, w - pad * 2),
        lh=float(o.get("lh") or 1.3), fill=str(o.get("color") or ""), align=align,
        slot=slot if o.get("kind") != "shape" else "", box_slot=box_slot,
        ov={"size": size, "bold": o.get("bold"), "italic": o.get("italic"),
            "underline": o.get("underline"), "font": o.get("font")})
    return svg


def render_slide(t: dict, layout: str, slots: dict | None = None, *, index: int = 0, total: int = 1,
                 warnings: list[str] | None = None, boxes: list[dict] | None = None,
                 overrides: dict | None = None, objects: list[dict] | None = None) -> str:
    """渲染一页，返回完整 SVG 文本（画布底色 + 版式 + 自由层 + 页脚；封面不画页码）。

    `boxes` 给了一个列表（`annotate`）时：给每个文字槽打 `data-slot` 并收集**可编辑盒子**
    （设计像素 1280×720）。**导出路径不传它** —— 导出用的 SVG 与以前逐字节一致（`docs/25` D8）。

    `overrides` 是老师改过的样子（`slides[i].slots`）：`{槽位: {dx,dy,dw,dh,size,color,align,lh,z,hidden}}`，
    在发射时套上 —— **预览与导出走同一条路**，所以"看到什么样，导出就是什么样"。

    `objects`（R21）是老师**自己插入**的文本框与形状（自由层）：画在版式内容之后、页脚之前，
    组内按数组顺序叠放（`上移一层/下移一层` = 换数组位置）。形状发的是 ppt-master 认的
    **规范预设组**（`data-pptx-authoring="preset"` …），所以导出会变成原生可编辑形状。
    """
    if layout not in _RENDERERS:
        raise ValueError(f"没有这个版式：{layout}（可选：{', '.join(LAYOUTS)}）")
    cv = Canvas(t, boxes, overrides)
    warn = warnings if warnings is not None else []
    sl = slots or {}
    body = order_z(cv, _RENDERERS[layout](cv, sl, index, total, warn))
    free = draw_objects(cv, objects or [])
    tail = "" if layout == "cover" else footer(cv, index, total)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{cv.w}" height="{cv.h}" '
            f'viewBox="0 0 {cv.w} {cv.h}">' + rect(0, 0, cv.w, cv.h, fill=cv.bg) + body + free +
            tail + "</svg>")


def render_deck(t: dict, slides: list[dict], warnings: list[str] | None = None) -> list[str]:
    """渲染整册：`slides = [{"layout": "cover", "title": ...}, ...]`，一页一个 SVG。

    注意：这里**不传** `boxes`/`objects` 之外的注解，导出路径与预览共用同一份 SVG 生成函数
    （`slide_deck.build_deck` 才是带注解的那条路）。
    """
    warn = warnings if warnings is not None else []
    total = len(slides)
    return [render_slide(t, s.get("layout") or "bullets", s, index=i, total=total, warnings=warn,
                         objects=s.get("objects") or [])
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
