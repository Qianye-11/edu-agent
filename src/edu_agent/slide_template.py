"""演示模板库（docs/19 §9）：**模板决定风格，内容由会话给**。

两件事：
1. **内置模板** = `content/ppt_templates/_builtin/*.json`（纯数值，见 `docs/22` §3）；
2. **导入模板** = 老师/学校给的一份 `.pptx` → 抽出**配色 / 字体 / 字号层级 / 文本槽**，
   存成 `<id>/theme.json` + `<id>/slots.json` + 原稿 `template.pptx`（**只读，永不改写**）。

落盘红线：只写 `content/ppt_templates/` 底下（原稿、清单、软删），**别处一个字不写**（有单测锁）。

抽取失败的退化（docs/19 §9.2）：**只借它的配色/字体**，页面仍用我们的 6 个版式，并把原因写进 `degraded`。
"""

from __future__ import annotations

import json
import re
import shutil
import time
import zipfile
from pathlib import Path

from . import ppt_tokens as tk

#: 导入模板里"页数/角色"这些靠名字猜：命中这些词就算封面/结尾/章节
_ROLE_HINTS = {
    "cover": ("封面", "cover", "title slide", "首页"),
    "end": ("结尾", "谢谢", "thank", "ending", "the end", "小结页"),
    "section": ("章节", "目录", "section", "agenda", "过渡"),
}
#: 目录名里不能出现的字符（**中文字照留**：老师起的名字要能在面板上看懂、也不会互相撞车）
_SLUG_RE = re.compile(r'[<>:"/\\|?*\s]+')


# ---------------------------------------------------------------- 路径与清单

def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def root() -> Path:
    """模板库根目录（`content/ppt_templates/`；环境变量语义见 `ppt_tokens.templates_root`）。"""
    return tk.templates_root()


def builtin_dir() -> Path:
    return root() / "_builtin"


def trash_dir() -> Path:
    return root() / "_trash"


def index_path() -> Path:
    return root() / "index.json"


def _slug(text: str, fallback: str = "template") -> str:
    """模板 id = 目录名：保留中文（可读），只去掉文件系统不认的字符与空格。"""
    s = _SLUG_RE.sub("-", str(text or "").strip()).strip("-.")
    return (s or fallback)[:40]


def list_templates() -> list[dict]:
    """全部可用模板：内置（json）+ 导入（目录里有 theme.json）。"""
    out: list[dict] = []
    for tid in tk.available():                       # 内置：读数值文件
        t = tk.load(tid)
        out.append({"id": tid, "name": t.get("name") or tid, "kind": "json",
                    "source": t.get("source") or "", "pages": 0, "layouts": [],
                    "palette": t["palette"], "fonts": t["fonts"], "ok": True, "degraded": []})
    for d in sorted(p for p in root().glob("*") if p.is_dir() and not p.name.startswith("_")):
        theme_file = d / "theme.json"
        if not theme_file.is_file():
            continue
        try:
            theme = json.loads(theme_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        slots = _read_json(d / "slots.json") or {}
        out.append({"id": d.name, "name": theme.get("name") or d.name, "kind": "pptx",
                    "source": theme.get("source") or "", "pages": len(slots.get("slides") or []),
                    "layouts": sorted({s.get("layout") or "" for s in (slots.get("slides") or [])} - {""}),
                    "palette": (theme.get("tokens") or {}).get("palette") or {},
                    "fonts": (theme.get("tokens") or {}).get("fonts") or {},
                    "ok": bool(theme.get("tokens")), "degraded": theme.get("degraded") or []})
    return out


def get(template_id: str) -> dict | None:
    for t in list_templates():
        if t["id"] == template_id:
            return t
    return None


def _read_json(p: Path) -> dict:
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_index() -> Path:
    """刷新 `index.json`（清单是派生的，随时可由目录重建）。"""
    data = {"templates": list_templates(), "updated": time.strftime("%Y-%m-%d %H:%M:%S")}
    path = index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


# ---------------------------------------------------------------- 从 .pptx 抽东西

def _theme_xml(path: Path) -> str:
    """读主题 XML；坏包/加密包一律当成"没读到"（宁可退化，不要炸）。"""
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            for name in ("ppt/theme/theme1.xml", *[n for n in names if n.startswith("ppt/theme/")]):
                if name in names:
                    return z.read(name).decode("utf-8", "replace")
    except (zipfile.BadZipFile, OSError):
        return ""
    return ""


def _scheme(xml: str) -> dict:
    """主题配色：`<a:clrScheme>` 里的 dk1/lt1/dk2/lt2/accent1…（srgbClr 或 sysClr.lastClr）。"""
    out: dict[str, str] = {}
    block = re.search(r"<a:clrScheme.*?</a:clrScheme>", xml or "", re.S)
    if not block:
        return out
    for m in re.finditer(r'<a:(\w+)>\s*<a:(srgbClr val="([0-9A-Fa-f]{6})"|sysClr[^>]*lastClr="([0-9A-Fa-f]{6})")',
                         block.group(0)):
        key, _, srgb, sys = m.group(1), m.group(2), m.group(3), m.group(4)
        out[key] = "#" + (srgb or sys).upper()
    return out


def _font_scheme(xml: str) -> dict:
    """主题字体：majorFont/minorFont 的 latin 与 ea（东亚）字体名。"""
    out = {"major_latin": "", "major_ea": "", "minor_latin": "", "minor_ea": ""}
    for which in ("majorFont", "minorFont"):
        block = re.search(rf"<a:{which}>.*?</a:{which}>", xml or "", re.S)
        if not block:
            continue
        lat = re.search(r'<a:latin typeface="([^"]*)"', block.group(0))
        ea = re.search(r'<a:ea typeface="([^"]*)"', block.group(0))
        out[f"{'major' if which == 'majorFont' else 'minor'}_latin"] = lat.group(1) if lat else ""
        out[f"{'major' if which == 'majorFont' else 'minor'}_ea"] = ea.group(1) if ea else ""
    return out


def _sizes_px(path: Path) -> list[float]:
    """从幻灯片里收集字号（pt → 画布 px，1px = 0.75pt），降序去重；坏包返回空。"""
    sizes: set[float] = set()
    try:
        with zipfile.ZipFile(path) as z:
            slides = [n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)]
            for name in slides[:40]:                       # 只看前 40 页，够用且快
                xml = z.read(name).decode("utf-8", "replace")
                for m in re.finditer(r'sz="(\d{3,4})"', xml):
                    pt = int(m.group(1)) / 100
                    if 6 <= pt <= 96:
                        sizes.add(round(pt / tk.PT_PER_PX, 1))
    except (zipfile.BadZipFile, OSError):
        return []
    return sorted(sizes, reverse=True)


def _base_tokens() -> dict:
    """抽不出来时的基底：用内置里最"中性"的那套（学术蓝）。"""
    return tk.load("tech-editorial")


def _bg_from_pptx(pptx: Path) -> tuple[str, str]:
    """从模板首页的**整页底图/色块**推一个近似底色（给预览用；填充产物里仍是原图）。

    为什么需要：设计稿的"背景"往往是一张整页图片，而不是主题色（`clrScheme` 里只有白）——
    于是老师选了模板却在预览里"看不出背景变化"。这里取整页图的**中位色**当底色近似。
    """
    try:
        from pptx import Presentation
        from pptx.util import Emu

        prs = Presentation(str(pptx))
        if not len(prs.slides._sldIdLst):                       # noqa: SLF001
            return "", ""
        slide = prs.slides[0]
        pic = None
        solid = ""
        for sh in slide.shapes:
            w = float(Emu(int(sh.width or 0)).inches) * 96
            h = float(Emu(int(sh.height or 0)).inches) * 96
            if w < 1280 * 0.6 or h < 720 * 0.6:                 # 只认"盖住大半页"的
                continue
            if "PICTURE" in str(sh.shape_type):
                pic = pic or sh
            elif not solid:
                try:
                    if sh.fill.type == 1 and sh.fill.fore_color.rgb:
                        solid = "#" + str(sh.fill.fore_color.rgb).upper()
                except Exception:  # noqa: BLE001
                    pass
        if solid:
            return solid, f"底色取自模板首页的整页色块 {solid}"
        if pic is not None:
            import io

            from PIL import Image

            im = Image.open(io.BytesIO(pic.image.blob)).convert("RGB").resize((24, 24))
            px = list(im.getdata())
            n = len(px) or 1
            med = tuple(sorted(p[i] for p in px)[n // 2] for i in range(3))
            hexc = "#%02X%02X%02X" % med
            return hexc, (f"模板首页是整页底图 → 预览用它的中位色 {hexc} 近似"
                          f"（填充产物里是原图，不会丢）")
    except Exception:  # noqa: BLE001 取不到就算了，别影响导入
        pass
    return "", ""


def tokens_from_pptx(pptx: Path, *, name: str = "") -> tuple[dict, list[str]]:
    """一份 .pptx → 我们那套 tokens（配色/字体/字号取自模板，画布与版式仍是我们的）。

    返回 `(tokens, degraded)`；`degraded` 里逐条写清"哪一项没抽到、退化成了什么"。
    """
    degraded: list[str] = []
    base = _base_tokens()
    xml = _theme_xml(pptx)
    scheme = _scheme(xml)
    fonts = _font_scheme(xml)

    bg = scheme.get("lt1") or base["canvas"]["bg"]
    bg2, bg_note = _bg_from_pptx(pptx)              # 整页底图/色块优先（否则预览看不出背景变化）
    if bg2:
        bg = bg2
        degraded.append(bg_note)
    text = scheme.get("dk1") or base["palette"]["text"]
    accent = scheme.get("accent1") or base["palette"]["accent"]
    if not scheme:
        degraded.append("没读到主题配色（theme1.xml 里没有 clrScheme）→ 配色沿用内置学术蓝")

    palette = {
        "text": text,
        "accent": accent,
        "accent2": scheme.get("accent2") or accent,
        "sub": tk.blend(text, bg, 0.62),               # 与内置主题同一条派生规则
        "line": tk.blend(text, bg, 0.14),
        "soft": tk.blend(accent, bg, 0.12),
        "accent_max_ratio": base["palette"].get("accent_max_ratio", 0.10),
    }

    latin = (fonts.get("major_latin") or fonts.get("minor_latin") or "").strip() or "Arial"
    ea = (fonts.get("major_ea") or fonts.get("minor_ea") or "").strip() or "微软雅黑"
    if not tk.font_installed(latin):
        degraded.append(f"模板西文字体《{latin}》本机没装 → 用 Arial")
        latin = "Arial"
    if not tk.font_installed(ea):
        degraded.append(f"模板中文字体《{ea}》本机没装 → 用 微软雅黑")
        ea = "微软雅黑"
    if not fonts.get("major_latin") and not fonts.get("minor_latin"):
        degraded.append("没读到主题字体 → 西文 Arial / 中文 微软雅黑")

    # 字号：从模板里取（降序），缺的档次用基底补；再夹进内置区间，保证阶梯合法
    sizes = _sizes_px(pptx)
    picks = {"cover": sizes[0] if sizes else 0, "title": sizes[1] if len(sizes) > 1 else 0,
             "body": sizes[3] if len(sizes) > 3 else (sizes[2] if len(sizes) > 2 else 0),
             "caption": sizes[5] if len(sizes) > 5 else (sizes[4] if len(sizes) > 4 else 0)}
    if not sizes:
        degraded.append("没读到字号 → 字号沿用内置学术蓝")
    type_out = dict(base["type"])
    for key, want in picks.items():
        lo, hi = base["ranges"][key]
        val = float(want or base["type"][key])
        clamped = max(float(lo), min(float(hi), val))
        if want and abs(clamped - val) > 0.01:
            degraded.append(f"{key} 字号 {val:g}px 超出规范区间 [{lo},{hi}] → 夹到 {clamped:g}px")
        type_out[key] = clamped
    type_out["cover"] = max(type_out["cover"], type_out["title"] + 4)   # 封面得比页标题大
    type_out["cover"] = min(float(base["ranges"]["cover"][1]), type_out["cover"])

    tokens = dict(base)
    tokens.update({
        "id": name or pptx.parent.name or "imported",
        "name": name or pptx.parent.name or "导入模板",
        "source": f"{pptx.name}（导入的演示模板）",
        "canvas": dict(base["canvas"], bg=bg),
        "palette": palette,
        "type": type_out,
        "fonts": {"latin": latin, "ea": ea, "stack": [latin, ea, "sans-serif"]},
        "derived": {"sub": {"from": "text", "over": "canvas.bg", "alpha": 0.62}},
        "notes": [f"配色取自模板主题：text={text} accent={accent} bg={bg}",
                  "画布/版式仍用本项目的 1280×720 与 6 个版式（docs/19 §9.2）"],
    })
    tokens.pop("marker", None)
    problems = tk.validate(tokens)
    if problems:                                       # 再有毛病就整体退化回基底（只留配色字体）
        degraded.append("模板数值有不合规处 → 只保留配色/字体，其余用内置兜底：" + "；".join(problems[:2]))
        tokens = dict(base, palette=palette,
                      fonts={"latin": latin, "ea": ea, "stack": [latin, ea, "sans-serif"]})
        tokens["id"], tokens["name"], tokens["source"] = name or "imported", name or "导入模板", f"{pptx.name}（导入）"
    return tokens, degraded


#: 小于这个容量（字）的槽位当成"标签/装饰"，不往里写正文（见 slide_fill 的说明）
MIN_FILLABLE_CAPACITY = 4
#: 中文设计标签的判据（又小又短才算）
_LABEL_MAX_CHARS = 4
_LABEL_MAX_W = 200.0
_LABEL_MAX_H = 60.0


def _is_design_label(text: str, geo: dict) -> bool:
    """设计稿里的固定小标签（「快乐成长」「PART 01」「01」「小小探险家」）—— **绝不替换、绝不清空**。

    判据（踩过一轮才定下来）：
      · 纯西文/数字的短文本（`PART 01`、`01`、`ABC`）基本都是标签 —— 不管框多大；
      · 中文：只有**又小又短**（≤4 字且框 ≤200×60）才算标签，
        否则「写作魔法学院」这种目录条目会被误判成标签。
    注意"字号最大"**不能**用来找标题：标签往往显式设了字号，内容框反而没设（踩过）。
    """
    t = (text or "").strip()
    if not t:
        return False
    has_cjk = any(ord(ch) > 0x2E80 for ch in t)
    if not has_cjk:
        return len(t) <= 12
    small = float(geo.get("w") or 0) <= _LABEL_MAX_W and float(geo.get("h") or 0) <= _LABEL_MAX_H
    return small and len(t) <= _LABEL_MAX_CHARS


def _slot_capacity(shape, size_px: float, geo: dict | None = None) -> int:
    """一个文本框大概能装多少字：行数 × 每行字数（用我们自己的字宽估算）。

    **量不出来就返回 0**（"未知"），不要返回 1 —— 否则每个槽都会被判成"超容量"，
    提示就变成了噪音（踩过：有形状的宽高继承自版式，自己的 xfrm 是空的）。
    """
    from . import ppt_svg

    geo = geo if geo is not None else _geo_px(shape)
    w, h = float(geo.get("w") or 0), float(geo.get("h") or 0)
    if w < 8 or h < 8:
        return 0
    size_px = float(size_px or 24.0)
    char_w = ppt_svg.char_w("字", size_px) or size_px
    per_line = max(1, int(w // max(1.0, char_w)))
    lines = max(1, int(h // max(1.0, size_px * 1.35)))
    return int(per_line * lines)


def _geo_px(shape) -> dict:
    """形状几何（EMU → 画布 px；13.333in 宽 = 1280px）。

    自己没带几何（宽高继承自版式的占位符）时，**回退到版式里同 idx 的占位符**；
    再取不到就返回 0（上层按"容量未知"处理，不乱报警）。
    """
    from pptx.util import Emu

    def px(v) -> float:
        return round(float(Emu(int(v or 0)).inches) * 96, 1)

    geo = {"x": px(shape.left), "y": px(shape.top), "w": px(shape.width), "h": px(shape.height)}
    if geo["w"] >= 8 and geo["h"] >= 8:
        return geo
    try:                                            # 回退：版式里的占位符几何
        idx = shape.placeholder_format.idx
        for ph in shape.part.slide.slide_layout.placeholders:
            if ph.placeholder_format.idx == idx:
                inherited = {"x": px(ph.left), "y": px(ph.top), "w": px(ph.width), "h": px(ph.height)}
                if inherited["w"] >= 8 and inherited["h"] >= 8:
                    return inherited
    except (AttributeError, ValueError, KeyError):
        pass
    return geo


def _role_of(shape, index: int, layout_name: str) -> str:
    """猜角色：优先看占位符类型，其次看版式名与顺序。"""
    try:
        ph = shape.placeholder_format
        if ph is not None and ph.idx == 0:
            return "title"
        if ph is not None and ph.type is not None:
            return "body"
    except (AttributeError, ValueError):
        pass
    low = (layout_name or "").lower()
    for role, hints in _ROLE_HINTS.items():
        if any(h in low for h in hints):
            return role
    return "title" if index == 0 else "body"


def slots_from_pptx(pptx: Path) -> dict:
    """把模板每页的文本框分成 **标题 / 正文 / 装饰** 三类（设计稿里没有"占位符"时的正解）。

    为什么不能"按顺序塞"（踩过）：老师的模板是**设计好的普通文本框**（没有占位符），
    第一个框往往是「快乐成长」这种装饰标签、真正的标题框反而是个空的大框 ——
    按顺序写就会把正文写进标签、模板自己的示例文字还都留着，成品看着就是"乱填"。

    规则（确定性、可复核）：

    1. 容量 < `MIN_FILLABLE_CAPACITY` 的小框 → 装饰；
    2. 短文本（≤6 字）+ 小面积（≤200×60）→ 设计标签 → 装饰；
    3. 其余算**内容槽**：字号最大（并列取面积最大）的当 `title`，其余按阅读顺序（上→下、左→右）当 `body`；
    4. 每个槽记下 `shape_index`（在 `slide.shapes` 里的位置）与 `sample`（当时的文字），
       填充时按它精确找回那个框，并核对 `sample` 没过期。

    读不出来就返回 `{"slides": [], "error": ...}` —— 调用方据此退化成"只用配色"。
    """
    try:
        from pptx import Presentation
        from pptx.util import Pt
    except Exception as exc:  # noqa: BLE001 python-pptx 不可用
        return {"slides": [], "error": f"python-pptx 不可用：{exc}"}

    out: list[dict] = []
    try:
        prs = Presentation(str(pptx))
        for i, slide in enumerate(prs.slides):
            layout_name = slide.slide_layout.name or ""
            rows: list[dict] = []
            for si, shp in enumerate(slide.shapes):
                if not getattr(shp, "has_text_frame", False):
                    continue
                geo = _geo_px(shp)
                text = (shp.text_frame.text or "").strip()
                size_px = 0.0
                for para in shp.text_frame.paragraphs:
                    for run in para.runs:
                        if run.font.size:
                            size_px = float(Pt(run.font.size.pt).pt) / tk.PT_PER_PX
                            break
                    if size_px:
                        break
                rows.append({"shape_index": si, "text": text, "geo": geo,
                             "size_px": round(size_px, 1),
                             "capacity": _slot_capacity(shp, size_px, geo),
                             "placeholder": _placeholder_kind(shp)})
            content = [r for r in rows
                       if r["capacity"] >= MIN_FILLABLE_CAPACITY and not _is_design_label(r["text"], r["geo"])]
            # 标题 = **面积最大**的内容槽（不能用字号：标签常显式设字号、内容框反而没设 —— 踩过）
            content.sort(key=lambda r: -(r["geo"]["w"] * r["geo"]["h"]))
            title_row = content[0] if content else None
            body_rows = sorted([r for r in content[1:]], key=lambda r: (r["geo"]["y"], r["geo"]["x"]))
            slots = []
            for r in rows:
                role = ("title" if r is title_row else ("body" if r in body_rows else "decoration"))
                slots.append({"role": role, "shape_index": r["shape_index"],
                              "capacity": r["capacity"], "size_px": r["size_px"], "geo": r["geo"],
                              "sample": r["text"][:40], "placeholder": r["placeholder"]})
            out.append({"index": i, "layout": layout_name, "slots": slots})
    except Exception as exc:  # noqa: BLE001 坏包/加密包
        return {"slides": [], "error": f"读 .pptx 失败：{type(exc).__name__}: {exc}"}
    return {"slides": out, "pages": len(out)}


def _placeholder_kind(shape) -> str:
    """占位符类型串（有占位符的模板优先用它的语义；没有就给空串）。"""
    try:
        ph = shape.placeholder_format
        if ph is None:
            return ""
        return f"{ph.type}:{ph.idx}"
    except (AttributeError, ValueError):
        return ""


# ---------------------------------------------------------------- 导入 / 软删

def import_pptx(src: Path, *, name: str = "", template_id: str = "") -> dict:
    """把一份 `.pptx` 收进模板库（原稿只读，抽取失败也要能退化使用）。"""
    src = Path(src)
    if not src.is_file():
        return {"ok": False, "error": f"文件不存在：{src}"}
    tid = _slug(template_id or name or src.stem)
    dest = root() / tid
    if dest.exists():
        tid = f"{tid}-{time.strftime('%H%M%S')}"
        dest = root() / tid
    dest.mkdir(parents=True, exist_ok=True)

    original = dest / "template.pptx"
    shutil.copyfile(src, original)                     # 收一份副本；原文件一个字节都不动
    tokens, degraded = tokens_from_pptx(original, name=name or src.stem)
    slots = slots_from_pptx(original)
    if slots.get("error"):
        degraded.append(f"文本槽没抽出来（{slots['error']}）→ 只借这份模板的配色/字体，页面用我们的 6 个版式")

    (dest / "theme.json").write_text(json.dumps(
        {"id": tid, "name": name or src.stem, "source": src.name, "kind": "pptx",
         "tokens": tokens, "degraded": degraded,
         "imported": time.strftime("%Y-%m-%d %H:%M:%S")},
        ensure_ascii=False, indent=2), encoding="utf-8")
    (dest / "slots.json").write_text(json.dumps(slots, ensure_ascii=False, indent=2), encoding="utf-8")
    write_index()
    return {"ok": True, "id": tid, "name": name or src.stem, "dir": str(dest),
            "pages": slots.get("pages") or 0, "slots_ok": not slots.get("error"),
            "degraded": degraded, "theme": tokens}


def delete_template(template_id: str, *, soft: bool = True) -> dict:
    """删模板：默认**软删**到 `_trash/`（老师的东西不直接抹掉）。"""
    tid = _slug(template_id)
    dest = root() / tid
    if not dest.is_dir():
        return {"ok": False, "error": f"没有这个导入模板：{tid}（内置模板不能删）"}
    target = trash_dir() / f"{tid}-{time.strftime('%Y%m%d-%H%M%S')}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(dest), str(target))
    write_index()
    return {"ok": True, "id": tid, "moved_to": str(target), "soft": soft}


# ---------------------------------------------------------------- 给渲染用

def theme_for(template_id: str) -> dict | None:
    """把模板转成渲染用的 tokens（导入模板读 `theme.json`，内置直接读 json）。"""
    tid = str(template_id or "").strip()
    if not tid:
        return None
    if tid in tk.available():
        return tk.load(tid)
    tpl_dir = root() / _slug(tid)
    theme = _read_json(tpl_dir / "theme.json")
    pptx = tpl_dir / "template.pptx"
    tokens = None
    if pptx.is_file():                                 # 现场重算：抽取规则改进了，老导入自动跟上
        try:
            fresh, _ = tokens_from_pptx(pptx, name=theme.get("name") or tid)
            tokens = fresh if not tk.validate(fresh) else None
        except Exception:  # noqa: BLE001 重算失败就用缓存
            tokens = None
    if tokens is None:
        cached = theme.get("tokens")
        tokens = cached if isinstance(cached, dict) else None
    if not isinstance(tokens, dict):
        return None
    problems = tk.validate(tokens)
    if problems:                                       # 兜底：只借配色字体，其余用基底
        base = _base_tokens()
        tokens = dict(base, palette=tokens.get("palette") or base["palette"],
                      fonts=tokens.get("fonts") or base["fonts"])
        tokens["id"], tokens["name"] = tid, theme.get("name") or tid
    return tokens


if __name__ == "__main__":        # 冒烟：python -m edu_agent.slide_template
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    for t in list_templates():
        print(f"{t['kind']:5s} {t['id']:20s} {t['name']:12s} 页 {t['pages']} 退化 {len(t['degraded'])}")
