"""幻灯片渲染收口：主题 + 会话数据 → 每页 SVG（预览与导出共用**同一份**）。

为什么要有这一层（`docs/22` D13 / `docs/23` M1）：

- 以前面板用 JS 拼幻灯片 HTML、导出用 python-pptx 另写坐标 ⇒ "预览≠导出"永远修不掉；
- 现在只有**一份**布局逻辑（`ppt_svg`），调用方只有两个：
  面板预览（`POST /api/slide/render`，纯内存）与导出（M3，写 `data/tmp/ppt_jobs/<job>/svg_output/`）。

本模块**绝不写盘**：`build_deck()` 只返回字符串，落盘一律由调用方按红线去做（有单测锁）。
"""

from __future__ import annotations

import math

from . import ppt_svg
from . import ppt_tokens as tk

#: 版式 id（与 docs/22 §5 一致；显式 `layout` 只认这些，其余按位置推断）
LAYOUTS = ppt_svg.LAYOUTS


# ---------------------------------------------------------------- 主题

def themes() -> list[dict]:
    """内置主题清单（给面板的下拉用）：数值 + 出处 + 字体是否本机可用。"""
    out: list[dict] = []
    for theme_id in tk.available():
        t = tk.load(theme_id)
        problems = tk.validate(t)
        out.append({
            "id": t.get("id") or theme_id,
            "name": t.get("name") or theme_id,
            "source": t.get("source") or "",
            "canvas": t["canvas"],
            "palette": {k: v for k, v in t["palette"].items() if k != "accent_max_ratio"},
            "type": t["type"],
            "fonts_ok": not any("字体" in p for p in problems),
            "fonts_missing": [p for p in problems if "字体" in p],
        })
    return out


#: 首次打开时的默认主题顺序（docs/23 §7：默认"学术蓝"，最正式、说课/公开课都稳）
PREFERRED = ("tech-editorial", "illustration", "hand-drawn")


def default_theme_id() -> str:
    """默认主题：按 `PREFERRED` 取第一个可用的（面板会记住老师选过的那套）。"""
    ids = tk.available()
    for want in PREFERRED:
        if want in ids:
            return want
    return ids[0] if ids else ""


def theme(theme_id: str) -> dict:
    """取一套主题（空值 → 默认主题）。"""
    return tk.load(theme_id or default_theme_id())


# ---------------------------------------------------------------- 会话数据 → 槽位

def _text(v: object) -> str:
    return str(v or "").strip()


def _bullets(slide: dict) -> list[str]:
    raw = slide.get("bullets")
    if not isinstance(raw, list):
        return []
    return [_text(b) for b in raw if _text(b)]


def _blocks(slide: dict) -> list[dict]:
    """`blocks[]`（带标签）优先；否则把 `bullets[]` 当成无标签的块。

    两种写法都收，因为会话历史里是 `bullets`，而模板/手工数据里是 `blocks`。
    """
    raw = slide.get("blocks")
    if isinstance(raw, list) and raw:
        out = []
        for b in raw:
            if isinstance(b, dict):
                text = _text(b.get("text") or b.get("name"))
                if text:
                    out.append({"label": _text(b.get("label")), "text": text})
            elif _text(b):
                out.append({"label": "", "text": _text(b)})
        return out
    return [{"label": "", "text": b} for b in _bullets(slide)]


def _items(slide: dict) -> list[str]:
    """取这一页的"条目文字"（`bullets` 优先，其次 `blocks` 的正文）——用于要点页/图文页/结尾页。"""
    items = _bullets(slide)
    return items or [b["text"] for b in _blocks(slide)]


def pick_layout(slide: dict, index: int, total: int) -> str:
    """版式：显式 `layout` 优先；否则按位置推断（docs/19 §8.4）。

    ⚠️ 第 1 页**只有在没有要点内容时**才当封面：否则把"情境导入"这类真内容页排成封面，
    它的要点会被整页丢掉（这是"内容零丢失"的红线，宁可不给封面也不丢内容）。
    最后一页用结尾版式（它本来就排要点，不会丢）。
    """
    layout = _text(slide.get("layout"))
    if layout in LAYOUTS:
        return layout
    if index == 0 and not _items(slide):
        return "cover"
    if total > 1 and index == total - 1:
        return "end"
    return "bullets"


def label_for(slide: dict, index: int) -> str:
    """内容页左上角的小标签：`环节 2 · 12 分钟`（时长来自教案里的"约 12 分钟"）。"""
    label = _text(slide.get("label"))
    if label:
        return label
    minutes = _text(slide.get("minutes"))
    if not minutes:
        return ""
    return f"环节 {index + 1} · {minutes} 分钟"


def to_slots(slide: dict, index: int, total: int, doc_title: str = "") -> tuple[str, dict]:
    """把一页会话数据映射成 `(版式, 槽位)`。"""
    layout = pick_layout(slide, index, total)
    name = _text(slide.get("name") or slide.get("title"))
    slots: dict = {}

    if layout == "cover":
        # 封面：整份教案的课题当主标，第一个环节名降为副标（比拿环节名当封面好看）
        title = _text(slide.get("cover_title")) or doc_title or name
        subtitle = _text(slide.get("subtitle")) or (name if title != name else "")
        slots.update({"title": title, "subtitle": subtitle,
                      "meta": _text(slide.get("meta")), "label": _text(slide.get("label"))})
    elif layout == "section":
        slots.update({"index": _text(slide.get("index")) or str(index + 1), "title": name})
    elif layout == "two-col":
        left = slide.get("left") if isinstance(slide.get("left"), dict) else {}
        right = slide.get("right") if isinstance(slide.get("right"), dict) else {}
        slots.update({"title": name, "label": label_for(slide, index),
                      "left": {"heading": _text(left.get("heading")),
                               "bullets": [_text(x) for x in (left.get("bullets") or []) if _text(x)]},
                      "right": {"heading": _text(right.get("heading")),
                                "bullets": [_text(x) for x in (right.get("bullets") or []) if _text(x)]}})
    elif layout == "media":
        slots.update({"title": name, "label": label_for(slide, index),
                      "caption": _text(slide.get("caption")), "bullets": _items(slide)})
    elif layout == "end":
        slots.update({"title": name or "小结", "label": label_for(slide, index),
                      "bullets": _items(slide), "closing": _text(slide.get("closing"))})
    else:                                   # bullets
        slots.update({"title": name, "label": label_for(slide, index), "blocks": _blocks(slide)})
    return layout, slots


# ---------------------------------------------------------------- 整册

def build_deck(theme_id: str, slides: list[dict] | None, title: str = "") -> dict:
    """渲染整册：返回 `{ok, theme_id, pages:[svg...], layouts, warnings, stats}`。

    **不落盘、不联网、不依赖 WPS**；`pages` 就是面板要显示、导出要转换的那份 SVG。
    """
    t = theme(theme_id)
    items = [s for s in (slides or []) if isinstance(s, dict)]
    if not items:
        return {"ok": True, "theme_id": t["id"], "pages": [], "layouts": [],
                "warnings": [], "stats": {"pages": 0, "chars": 0}}

    total = len(items)
    warnings: list[str] = []
    pages: list[str] = []
    layouts: list[str] = []
    chars = 0
    for i, slide in enumerate(items):
        layout, slots = to_slots(slide, i, total, doc_title=_text(title))
        layouts.append(layout)
        pages.append(ppt_svg.render_slide(t, layout, slots, index=i, total=total, warnings=warnings))
        chars += sum(len(str(v)) for v in (slide.get("bullets") or []))
    return {"ok": True, "theme_id": t["id"], "pages": pages, "layouts": layouts,
            "warnings": warnings,
            "stats": {"pages": total, "chars": chars, "theme_name": t.get("name") or t["id"]}}


def slide_warnings(theme_id: str, slides: list[dict] | None, title: str = "") -> list[str]:
    """只跑"密度/上限"检查（面板提示用），不返回 SVG。"""
    return build_deck(theme_id, slides, title).get("warnings") or []


#: 画布尺寸（与 tokens 一致；面板的等比缩放以此为准）
CANVAS_W = 1280
CANVAS_H = 720
#: 缩放系数上下限（太小看不清、太大浪费屏幕）
SCALE_MIN, SCALE_MAX = 0.15, 2.0


def fit_scale(container_w: float) -> float:
    """面板宽度 → 预览台缩放系数（**面板里的 JS 是这段公式的镜像**）。

    预览台内部永远是 1280×720；只有这一个**均匀**缩放，所以元素之间的相对位置与导出完全一致
    （面板拖宽拖窄都不会变形）。单测锁住取值，面板检查锁住 `data-scale`。
    """
    try:
        w = float(container_w)
    except (TypeError, ValueError):
        w = 0.0
    if w <= 0:
        w = 480.0                      # 量不到宽度时的兜底（jsdom / 隐藏时就是这种）
    return max(SCALE_MIN, min(SCALE_MAX, w / CANVAS_W))


def fit_height(container_w: float) -> int:
    """缩放后预览台应有的高度（面板据此设 wrap 的高度，避免留白/裁切）。

    用 `floor(x+0.5)` 而不是 `round()`：JS 的 `Math.round(382.5)=383`，Python 的 `round(382.5)=382`，
    两边不一致就会差 1px —— 审计算出来的高度必须与面板实际设的一致。
    """
    return int(math.floor(CANVAS_H * fit_scale(container_w) + 0.5))


if __name__ == "__main__":        # 冒烟：python -m edu_agent.slide_deck
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    demo = [{"name": "3.3 幂函数"}, {"name": "情境导入", "minutes": "5", "bullets": ["投影五个实例"]},
            {"name": "小结", "minutes": "3", "bullets": ["三条结论"]}]
    for tid in tk.available():
        out = build_deck(tid, demo, title="3.3 幂函数")
        print(f"{tid}: {out['stats']['pages']} 页 · 版式 {out['layouts']} · 警告 {len(out['warnings'])}")
