"""幻灯片渲染收口：主题 + 会话数据 → 每页 SVG（预览与导出共用**同一份**）。

为什么要有这一层（`docs/22` D13 / `docs/23` M1）：

- 以前面板用 JS 拼幻灯片 HTML、导出用 python-pptx 另写坐标 ⇒ "预览≠导出"永远修不掉；
- 现在只有**一份**布局逻辑（`ppt_svg`），调用方只有两个：
  面板预览（`POST /api/slide/render`，纯内存）与导出（M3，写 `data/tmp/ppt_jobs/<job>/svg_output/`）。

本模块**绝不写盘**：`build_deck()` 只返回字符串，落盘一律由调用方按红线去做（有单测锁）。
"""

from __future__ import annotations

import html
import json
import math
import os
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from . import ppt_svg
from . import ppt_tokens as tk

#: 版式 id（与 docs/22 §5 一致；显式 `layout` 只认这些，其余按位置推断）
LAYOUTS = ppt_svg.LAYOUTS

#: 演示用的示例教案（样张工具与审计工具共用一份，避免两处各写一套）
SAMPLE_DECK: list[dict] = [
    {"layout": "cover", "name": "3.3 幂函数", "cover_title": "3.3 幂函数",
     "subtitle": "数学 · 高一 · 新授课", "label": "人教A版 · 必修第一册",
     "meta": "1 课时 · 情境导入 → 概念建构 → 图象探究 → 小结"},
    {"layout": "bullets", "name": "幂函数的概念", "minutes": "12", "label": "环节 2 · 12 分钟",
     "blocks": [{"label": "定义", "text": "形如 y=x^α 的函数，α 为常数"},
                {"label": "关键", "text": "底数是自变量，指数是常数"},
                {"label": "定义域", "text": "随 α 的取值变化，先看指数再定"},
                {"label": "五个常见", "text": "y=x、y=x²、y=x³、y=√x、y=x⁻¹"}]},
    {"layout": "media", "name": "图象与性质", "minutes": "15", "label": "环节 3 · 15 分钟",
     "caption": "在同一坐标系里画出 y=x²，观察它的单调性与奇偶性。",
     "bullets": ["关于 y 轴对称，是偶函数", "在 [0,+∞) 上单调递增"]},
]


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


# ---------------------------------------------------------------- 导出（docs/23 M3）

#: 导出后端开关：不设/`native` = 走 ppt-master（新，原生可编辑）；`legacy` = 回退旧版式导出
ENV_EXPORT = "EDU_PPT_EXPORT"
#: 允许作为中文字体的族名（本机已装：微软雅黑 / Noto Sans SC）
EA_OK = ("微软雅黑", "Microsoft YaHei", "Noto Sans SC", "Source Han Sans", "思源黑体")


def export_backend() -> str:
    """当前导出后端：`native`（默认）或 `legacy`（一键回退，见 docs/22 §6.3）。"""
    v = (os.environ.get(ENV_EXPORT) or "").strip().lower()
    return "legacy" if v in ("legacy", "old", "0", "off") else "native"


def safe_name(s: str, fallback: str = "教案") -> str:
    """文件名安全化（老师给的课题里什么都可能有）。"""
    bad = '<>:"/\\|?*'
    out = "".join("_" if ch in bad else ch for ch in str(s or "")).strip().strip(".")
    return (out or fallback)[:40]


def svg_texts(svg: str) -> list[str]:
    """抠出一页 SVG 里的可见文字（导出后比对内容没丢）。"""
    return [html.unescape(t) for t in re.findall(r"<text[^>]*>(.*?)</text>", svg, re.S) if t.strip()]


def _has_cjk(s: str) -> bool:
    return any(ord(ch) > 0x2E80 for ch in s)


def pptx_font_audit(slides_xml: list[str], theme_xml: str = "") -> tuple[bool, str]:
    """查"中文字体有没有掉回宋体"：**只看含中文的 run**（纯西文 run 的 ea 无害）。

    转换器会给纯西文 run 写 `<a:ea typeface="Arial">`，这不是问题；
    真会出事的是含中文的 run 没写 ea —— 那时渲染器按主题缺省走，中文 Windows 上就是宋体。
    """
    total = explicit = 0
    bad: list[str] = []
    eas: set[str] = set()
    latins: set[str] = set()
    for xml in slides_xml:
        for run in re.findall(r"<a:r>(.*?)</a:r>", xml, re.S):
            txt = html.unescape("".join(re.findall(r"<a:t>([^<]*)</a:t>", run)))
            lat = re.search(r'<a:latin typeface="([^"]*)"', run)
            ea = re.search(r'<a:ea typeface="([^"]*)"', run)
            if lat and lat.group(1):
                latins.add(lat.group(1))
            if not _has_cjk(txt):
                continue
            total += 1
            if ea and ea.group(1):
                explicit += 1
                eas.add(ea.group(1))
                if ea.group(1) not in EA_OK:
                    bad.append(f"「{txt[:8]}」→ ea={ea.group(1)}")
            else:
                bad.append(f"「{txt[:8]}」→ 没写 ea（会跟主题缺省，中文环境多半掉宋体）")
    theme_ea = ""
    if theme_xml:
        m = re.search(r"<a:minorFont>.*?<a:ea typeface=\"([^\"]*)\"", theme_xml, re.S)
        theme_ea = (m.group(1) if m else "") or "（空 = 跟随系统默认）"
    ok = total > 0 and not bad
    msg = (f"含中文 run {total} 个，显式 ea {explicit} 个 → {sorted(eas) or '—'}"
           f"；主题缺省 ea={theme_ea}；西文 latin={sorted(latins) or '—'}")
    if bad:
        msg += "；问题：" + "；".join(bad[:3])
    return ok, msg


def verify_pptx(pptx: Path, pages: list[str]) -> dict:
    """`docs/22` §6.2 的四条硬验收：页数 / 原生文本 / 不嵌字体+中文字体 / 内容零丢失。

    返回 `{ok, checks:[{name, ok, detail}], detail}`；**只看报告与包内容，不看退出码**
    （`svg_to_pptx.py` 在 `passed-with-warnings` 时退出码仍是 1）。
    """
    checks: list[dict] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    if not pptx.is_file():
        add("产物存在", False, str(pptx))
        return {"ok": False, "checks": checks, "detail": "没有产出 pptx"}

    z = zipfile.ZipFile(pptx)
    parts = z.namelist()
    slides = sorted(n for n in parts if re.fullmatch(r"ppt/slides/slide\d+\.xml", n))
    per_slide, shapes, slides_xml = [], 0, []
    for name in slides:
        xml = z.read(name).decode("utf-8")
        slides_xml.append(xml)
        per_slide.append([html.unescape(t) for t in re.findall(r"<a:t>([^<]*)</a:t>", xml)])
        shapes += len(re.findall(r"<p:sp>", xml))
    theme_xml = ""
    for name in parts:
        if re.fullmatch(r"ppt/theme/theme\d+\.xml", name):
            theme_xml = z.read(name).decode("utf-8")
            break
    embedded = [n for n in parts if "fntdata" in n]
    want = [svg_texts(p) for p in pages]

    font_ok, font_msg = pptx_font_audit(slides_xml, theme_xml)
    add("页数一致", len(slides) == len(pages), f"pptx {len(slides)} 页 / SVG {len(pages)} 页")
    add("原生文本（不是图片）", bool(shapes) and all(per_slide),
        f"{shapes} 个形状，每页文字块 {[len(p) for p in per_slide]}")
    add("不嵌字体（WPS 不读内嵌裸 TTF）", not embedded, f"包内 fntdata {len(embedded)} 个")
    add("中文字体显式指定", font_ok, font_msg)
    missing = []
    for i, (w, got) in enumerate(zip(want, per_slide), 1):
        lack = [t for t in w if t.strip() and t.strip() not in [g.strip() for g in got]]
        if lack:
            missing.append(f"第 {i} 页缺 {lack[:3]}")
    add("内容零丢失", not missing and len(want) == len(per_slide),
        "逐页逐串比对通过" if not missing else "；".join(missing))

    ok = all(c["ok"] for c in checks)
    return {"ok": ok, "checks": checks,
            "detail": "；".join(f"{'✅' if c['ok'] else '❌'} {c['name']}" for c in checks)}


def export_deck_pptx(theme_id: str, slides: list[dict] | None, title: str = "", *,
                     out_dir: str | Path | None = None) -> dict:
    """把幻灯片导出成**原生可编辑 pptx**（同一份 SVG 交给 ppt-master 转换）。

    落盘规则（`docs/23` §5）：中间件进 `data/tmp/ppt_jobs/<job>/`（24h 自清），
    产物进 `data/ppt_out/`（或老师选的目录）；**`content/plans/` 一个字不写**。
    """
    from . import ppt_polish                       # 复用它的 home/scripts/workroot/outdir

    built = build_deck(theme_id, slides, title=title)
    pages = built["pages"]
    if not pages:
        return {"ok": False, "error": "没有可导出的页（slides 为空？）", "backend": "native"}

    scripts = ppt_polish.scripts_dir()
    if not (scripts / "svg_to_pptx.py").is_file():
        return {"ok": False, "backend": "native", "gate": "install",
                "error": f"ppt-master 未就绪：{scripts}（跑 deploy\\setup_ppt_master.cmd）"}

    try:
        ppt_polish.cleanup_stale()
    except Exception:  # noqa: BLE001 清理失败不该挡住导出
        pass

    stamp = time.strftime("%Y%m%d-%H%M%S")
    job = ppt_polish.workroot() / f"deck-{stamp}"
    svg_out = job / "svg_output"
    svg_out.mkdir(parents=True, exist_ok=True)
    for i, svg in enumerate(pages, 1):
        (svg_out / f"{i:02d}_{built['layouts'][i - 1]}.svg").write_text(svg, encoding="utf-8")

    target_dir = Path(out_dir) if out_dir else ppt_polish.outdir()
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"教案_{safe_name(title or '未命名')}_{stamp}.pptx"

    logs: list[str] = []
    steps = (
        ([sys.executable, str(scripts / "svg_quality_checker.py"), str(job),
          "--canonical-authoring", "--stage", "final", "--quick-generate", "--json"], "质量检查"),
        ([sys.executable, str(scripts / "svg_to_pptx.py"), str(job),
          "--quick-generate", "-o", str(target)], "转换"),
    )
    for cmd, tag in steps:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=600, cwd=str(job))
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "backend": "native", "job": str(job),
                    "error": f"{tag}没跑起来：{exc}"}
        tail = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
        logs.append(f"{tag}：{(tail[-1] if tail else '')[:120]}")
        if tag == "转换" and not target.is_file():
            return {"ok": False, "backend": "native", "job": str(job), "logs": logs,
                    "error": f"转换未产出文件（exit={proc.returncode}）"}

    report = job / "validation" / f"{target.stem}.report.json"
    status = ""
    if report.is_file():
        try:
            status = json.loads(report.read_text(encoding="utf-8")).get("status", "")
        except Exception:  # noqa: BLE001 报告坏了不当失败
            status = ""
    verify = verify_pptx(target, pages)
    ok = verify["ok"] and (not status or status.startswith("passed"))
    return {"ok": ok, "backend": "native", "file": str(target), "filename": target.name,
            "outDir": str(target_dir), "job": str(job), "status": status,
            "pages": len(pages), "theme_id": built["theme_id"], "layouts": built["layouts"],
            "verify": verify, "logs": logs,
            "error": "" if ok else (f"转换报告 status={status or '（缺失）'}；" + verify["detail"])}


if __name__ == "__main__":        # 冒烟：python -m edu_agent.slide_deck
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    demo = [{"name": "3.3 幂函数"}, {"name": "情境导入", "minutes": "5", "bullets": ["投影五个实例"]},
            {"name": "小结", "minutes": "3", "bullets": ["三条结论"]}]
    for tid in tk.available():
        out = build_deck(tid, demo, title="3.3 幂函数")
        print(f"{tid}: {out['stats']['pages']} 页 · 版式 {out['layouts']} · 警告 {len(out['warnings'])}")
