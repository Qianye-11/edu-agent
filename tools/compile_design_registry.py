# -*- coding: utf-8 -*-
"""把两份 skill 的**设计规格**编译成我们自己的注册表（R27a）。

为什么要有这一步（`docs/29` §1.2 红线）：两份 skill 都**不入库**（`.deps/`），而我们又不能让
"agent 可调控"这条通道依赖它们在场 —— 所以把它们的**规则与数值**读一遍，编译成**自包含**的 JSON
进我们仓库；`.deps` 只用于"重新编译"。

来源（只读，绝不修改）：
  · ppt-master（MIT，pin 481e057）
      references/visual-styles/_index.md + 每个风格文件 → 视觉风格（18 套）的纪律与配对图像
      references/modes/_index.md + 每个模式文件        → 沟通模式（5 种）
      templates/brands/<家>/templates/design_spec.md   → 品牌色板（21 家，色值照抄并标 provenance）
      templates/layouts/<族>/templates/*.svg           → 版式（7 族 86 个）：槽位 + 装饰图元
  · guizang-ppt-skill（AGPL，pin c91369c）：**不参与编译**，只在文档里核对数值（见 docs/29 §1.2）

产出（入库，自包含）：
  content/ppt_templates/_design/{visual_styles,modes,brands,layouts}.json + _meta.json

⚠️ 关于"默认数值"：visual style 原文**不含 HEX**（它只管纪律）。所以每套风格的 `defaults`
是我们**自己给的默认值**（色板/几何/密度），并标 `tuned`：
  tuned=true  —— 人工调过（先做 ink-wash / swiss-minimal / editorial 三套）；
  tuned=false —— 由基准 tokens + 风格的硬纪律机械派生（等样张评审后再逐套人工调）。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEPS = ROOT / ".deps"
PPT_MASTER = DEPS / "ppt-master"
SKILL = PPT_MASTER / "skills" / "ppt-master"
OUT = ROOT / "content" / "ppt_templates" / "_design"
BASE_TOKENS = ROOT / "content" / "ppt_templates" / "_builtin" / "tech-editorial.json"

#: 视觉风格 → 我们给的默认值（色板/几何/密度）。只有这三套先人工调，其余按纪律派生。
TUNED_DEFAULTS: dict[str, dict] = {
    "swiss-minimal": {
        "palette": {"bg": "#FAFAF8", "text": "#0A0A0A", "accent": "#002FA7",
                    "sub": "#737373", "line": "#D4D4D2", "soft": "#F0F0EE"},
        "geometry": {"radius": 0, "shadow": False, "gradient": False, "line_w": 1},
        "density": {"blocks_max": 5, "chars_per_block_max": 22},
        "note": "照 Swiss 硬规则：直角、纯色、不透明、单一高饱和锚点；灰阶跨主题统一（guizang themes-swiss 实测值）",
    },
    "ink-wash": {
        "palette": {"bg": "#F8F6F2", "text": "#2B2B2B", "accent": "#9E2B25",
                    "sub": "#797877", "line": "#EAE7E1", "soft": "#F1EEE9"},
        "geometry": {"radius": 0, "shadow": False, "gradient": False, "line_w": 1},
        "density": {"blocks_max": 4, "chars_per_block_max": 20},
        "note": "宣纸底 + 墨黑字 + **单一朱红印色**（accent 只许出现一处）；大片留白，密度比默认更松",
    },
    "editorial": {
        "palette": {"bg": "#FFFFFF", "text": "#1A1A1A", "accent": "#B3261E",
                    "sub": "#6B6B6B", "line": "#DCDCDC", "soft": "#F5F5F5"},
        "geometry": {"radius": 0, "shadow": False, "gradient": False, "line_w": 1},
        "density": {"blocks_max": 6, "chars_per_block_max": 26},
        "note": "杂志层级：细分隔线 + 分栏 + 衬线/无衬线对照；允许比默认略密",
    },
}

#: 机械派生规则：按风格 id 给几何/密度/明暗的硬纪律覆盖（不动色板时用基准色）
DERIVED_RULES: dict[str, dict] = {
    "dark-tech": {"dark": True, "geometry": {"radius": 2, "shadow": False, "gradient": False}},
    "blueprint": {"dark": True, "geometry": {"radius": 0, "shadow": False, "gradient": False}},
    "chalkboard": {"dark": True, "geometry": {"radius": 0, "shadow": False, "gradient": False}},
    "pixel-art": {"geometry": {"radius": 0, "shadow": False, "gradient": False}},
    "brutalist": {"geometry": {"radius": 0, "shadow": False, "gradient": False}},
    "zine": {"geometry": {"radius": 0, "shadow": False, "gradient": False}},
    "vintage-poster": {"geometry": {"radius": 0, "shadow": False, "gradient": False}},
    "data-journalism": {"density": {"blocks_max": 6, "chars_per_block_max": 26}},
    "photo-editorial": {"density": {"blocks_max": 3, "chars_per_block_max": 18}},
    "soft-rounded": {"geometry": {"radius": 8, "shadow": True, "gradient": False}},
    "glassmorphism": {"geometry": {"radius": 10, "shadow": True, "gradient": True}},
    "paper-cut": {"geometry": {"radius": 2, "shadow": True, "gradient": False}},
    "sketch-notes": {"geometry": {"radius": 4, "shadow": False, "gradient": False}},
    "ink-notes": {"geometry": {"radius": 0, "shadow": False, "gradient": False}},
    "memphis": {"geometry": {"radius": 2, "shadow": False, "gradient": False}},
}

DARK_PALETTE = {"bg": "#0F1212", "text": "#F2F2F0", "accent": "#4CC2FF",
                "sub": "#9AA0A6", "line": "#2A2F33", "soft": "#171B1E"}


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")


def _git_commit(path: Path) -> str:
    try:
        out = subprocess.run(["git", "-C", str(path), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=30)
        return out.stdout.strip() or "?"
    except Exception:                                    # noqa: BLE001
        return "?"


def _sections(md: str) -> dict[str, str]:
    """把 `## x. Title` 切成一节一节的正文（标题 → 正文）。"""
    out: dict[str, str] = {}
    cur = ""
    buf: list[str] = []
    for line in md.splitlines():
        m = re.match(r"^##\s+(.*)$", line)
        if m:
            if cur:
                out[cur] = "\n".join(buf).strip()
            cur = m.group(1).strip()
            buf = []
        elif cur:
            buf.append(line)
    if cur:
        out[cur] = "\n".join(buf).strip()
    return out


def _table_rows(md: str, must_have: str) -> list[list[str]]:
    """取包含 `must_have` 的那张表的数据行（返回单元格列表，已去反引号与链接）。"""
    rows: list[list[str]] = []
    for line in md.splitlines():
        s = line.strip()
        if not s.startswith("|") or must_have not in line:
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 2 or set(cells[0]) <= set("-: "):
            continue
        clean = [re.sub(r"\[`?([^`\]]+)`?\]\([^)]*\)", r"\1", c).strip("` ") for c in cells]
        rows.append(clean)
    return rows


def _bullets(text: str) -> list[str]:
    return [re.sub(r"^[-*]\s+", "", ln).strip() for ln in text.splitlines()
            if re.match(r"^\s*[-*]\s+\S", ln)]


def compile_visual_styles() -> dict:
    d = SKILL / "references" / "visual-styles"
    index = _read(d / "_index.md")
    rows = _table_rows(index, ".md)")
    styles = []
    group = ""
    for line in index.splitlines():
        g = re.match(r"^###\s+([\d.]+)\s+(.*)$", line.strip())
        if g:
            group = g.group(2).strip()
            continue
        m = re.match(r"^\|\s*\[`([^`]+)`\]", line.strip())
        if not m:
            continue
        cells = [re.sub(r"\[`?([^`\]]+)`?\]\([^)]*\)", r"\1", c).strip("` ")
                 for c in line.strip().strip("|").split("|")]
        sid = m.group(1)
        f = d / f"{sid}.md"
        secs = _sections(_read(f)) if f.is_file() else {}
        key = {k.split(". ", 1)[-1].lower(): v for k, v in secs.items()}
        base = json.loads(_read(BASE_TOKENS))
        # ⚠️ 基准 tokens 的 palette 里**没有 bg**（底色在 canvas.bg 里），且 geometry.line_w/radius 是**区间**。
        # 派生默认值时必须补齐/折成单值，否则解析器合出来的 tokens 缺 bg、几何带区间（踩过）。
        base_palette = {**base["palette"], "bg": base["canvas"]["bg"]}
        base_geometry = {
            "line_w": (base["geometry"]["line_w"][0] if isinstance(base["geometry"]["line_w"], list)
                       else base["geometry"]["line_w"]),
            "radius": (base["geometry"]["radius"][0] if isinstance(base["geometry"]["radius"], list)
                       else base["geometry"]["radius"]),
            "shadow": base["geometry"]["shadow"],
            "gradient": base["geometry"]["gradient"],
        }
        tuned = TUNED_DEFAULTS.get(sid)
        rule = DERIVED_RULES.get(sid, {})
        defaults = {
            "palette": dict(tuned["palette"] if tuned else
                            (DARK_PALETTE if rule.get("dark") else base_palette)),
            "geometry": dict(tuned["geometry"] if tuned else
                             {**base_geometry, **rule.get("geometry", {})}),
            "density": dict(tuned["density"] if tuned else
                            {**base["density"], **rule.get("density", {})}),
        }
        defaults["geometry"]["radius"] = int(defaults["geometry"]["radius"] or 0)
        styles.append({
            "id": sid,
            "group": group,
            "character": cells[1] if len(cells) > 1 else "",
            "paired_rendering": cells[2] if len(cells) > 2 else "",
            "illustration": cells[3] if len(cells) > 3 else "",
            "rules": {
                "shape": key.get("shape & decoration", ""),
                "typography": key.get("typography character", ""),
                "colors": key.get("using the deck's colors", ""),
                "texture": key.get("texture / elevation", ""),
                "paired": key.get("paired image-rendering", ""),
                "illustration_propensity": key.get("illustration propensity", ""),
            },
            "must": _bullets(key.get("shape & decoration", ""))[:4],
            "defaults": defaults,
            "tuned": bool(tuned),
            "note": (tuned or {}).get("note", "" if tuned else "派生默认值（等样张评审后逐套人工调）"),
            "source": f"ppt-master references/visual-styles/{sid}.md",
        })
    return {"styles": styles}


def compile_modes() -> dict:
    d = SKILL / "references" / "modes"
    index = _read(d / "_index.md")
    modes = []
    for line in index.splitlines():
        m = re.match(r"^\|\s*\[`([^`]+)`\]", line.strip())
        if not m:
            continue
        cells = [re.sub(r"\[`?([^`\]]+)`?\]\([^)]*\)", r"\1", c).strip("` ")
                 for c in line.strip().strip("|").split("|")]
        mid = m.group(1)
        f = d / f"{mid}.md"
        secs = _sections(_read(f)) if f.is_file() else {}
        modes.append({
            "id": mid,
            "skeleton": cells[1] if len(cells) > 1 else "",
            "context": cells[2] if len(cells) > 2 else "",
            "rules": {k: v for k, v in secs.items()},
            "must": [b for v in secs.values() for b in _bullets(v)][:6],
            "source": f"ppt-master references/modes/{mid}.md",
        })
    return {"modes": modes}


def compile_brands() -> dict:
    d = SKILL / "templates" / "brands"
    brands = []
    for sub in sorted(p for p in d.iterdir() if p.is_dir()):
        spec = sub / "templates" / "design_spec.md"
        if not spec.is_file():
            continue
        md = _read(spec)
        head = re.match(r"^---\n(.*?)\n---", md, re.S)
        fm: dict[str, str] = {}
        if head:
            for ln in head.group(1).splitlines():
                kv = re.match(r"^([a-z_]+):\s*(.*)$", ln.strip())
                if kv:
                    fm[kv.group(1)] = kv.group(2).strip().strip('"')
        palette: dict[str, str] = {}
        for cells in _table_rows(md, "#"):
            if len(cells) >= 2 and re.fullmatch(r"#[0-9A-Fa-f]{6}", cells[1]):
                palette.setdefault(cells[0].lower(), cells[1].upper())
        tone = ""
        for cells in _table_rows(md, "Tone"):
            if len(cells) >= 2 and cells[0].lower() == "tone":
                tone = cells[1]
        brands.append({
            "id": fm.get("brand_id") or sub.name,
            "name": sub.name,
            "summary": fm.get("summary", ""),
            "primary_color": fm.get("primary_color", ""),
            "tone": tone,
            "palette": palette,
            "source": f"ppt-master templates/brands/{sub.name}/templates/design_spec.md",
        })
    return {"brands": brands}


SVG_TAGS = ("rect", "line", "path", "circle", "ellipse", "polygon", "polyline")


def compile_layouts() -> dict:
    root = SKILL / "templates" / "layouts"
    index = {}
    idx_file = root / "layouts_index.json"
    if idx_file.is_file():
        index = json.loads(_read(idx_file))
    families = []
    total = 0
    for fam in sorted(p for p in root.iterdir() if p.is_dir()):
        tdir = fam / "templates"
        if not tdir.is_dir():
            continue
        layouts = []
        for svg in sorted(tdir.glob("*.svg")):
            txt = _read(svg)
            root_tag = re.search(r"<svg\b[^>]*>", txt)
            head = root_tag.group(0) if root_tag else ""
            canvas = re.search(r'viewBox="([^"]+)"', head)
            w = re.search(r'width="([\d.]+)"', head)
            h = re.search(r'height="([\d.]+)"', head)
            slots = []
            for m in re.finditer(r'<g\b[^>]*data-pptx-placeholder="([^"]+)"[^>]*>(.*?)</g>', txt, re.S):
                block, inner = m.group(0), m.group(2)
                bounds = re.search(r'data-pptx-bounds="([^"]+)"', block)
                carrier = re.search(r"<text\b[^>]*>", inner)
                c = carrier.group(0) if carrier else ""
                slots.append({
                    "placeholder": m.group(1),
                    "bounds": [float(x) for x in (bounds.group(1).split() if bounds else [])],
                    "size": float(re.search(r'font-size="([\d.]+)"', c).group(1)) if re.search(r'font-size="([\d.]+)"', c) else None,
                    "fill": (re.search(r'fill="([^"]+)"', c).group(1) if re.search(r'fill="([^"]+)"', c) else None),
                    "align": (re.search(r'text-anchor="([^"]+)"', c).group(1) if re.search(r'text-anchor="([^"]+)"', c) else None),
                    "weight": (re.search(r'font-weight="([^"]+)"', c).group(1) if re.search(r'font-weight="([^"]+)"', c) else None),
                })
            decor = []
            for tag in SVG_TAGS:
                for m in re.finditer(rf"<{tag}\b[^>]*>", txt):
                    one = m.group(0)
                    if 'data-pptx-placeholder' in one:
                        continue
                    layer = re.search(r'data-pptx-layer="([^"]+)"', one)
                    if not layer or layer.group(1) not in ("master", "layout"):
                        continue
                    decor.append({"tag": tag, "attrs": dict(re.findall(r'([a-zA-Z-]+)="([^"]*)"', one))})
            layouts.append({
                "id": (re.search(r'data-pptx-layout="([^"]+)"', head).group(1)
                       if re.search(r'data-pptx-layout="([^"]+)"', head) else svg.stem),
                "name": (re.search(r'data-pptx-layout-name="([^"]+)"', head).group(1)
                         if re.search(r'data-pptx-layout-name="([^"]+)"', head) else svg.stem),
                "file": svg.name,
                "canvas": [float(w.group(1)) if w else 1280.0, float(h.group(1)) if h else 720.0],
                "viewBox": canvas.group(1) if canvas else "",
                "slots": slots,
                "decorations": decor,
            })
        total += len(layouts)
        families.append({
            "id": fam.name,
            "summary": (index.get(fam.name) or {}).get("summary", ""),
            "canvas_format": (index.get(fam.name) or {}).get("canvas_format", ""),
            "layouts": layouts,
        })
    return {"families": families, "layout_total": total}


#: 锁死清单（"不固化 ≠ 不设底线"）—— 写进目录，agent 读得到
LOCKED = {
    "canvas": {"w": 1280, "h": 720, "note": "16:9，设计像素；导出 1px = 0.75pt"},
    "safe_margin_min": 72,
    "type_steps_note": "字号必须取自主题 tokens 的 5 档（偏离只记 warn，不阻断）",
    "elements": ["rect", "line", "path", "circle", "text", "polyline", "ellipse", "polygon"],
    "forbidden": ["filter/阴影", "渐变（部分风格允许，导出前会被审计判 warn）", "外链图片", "非预设自由形状（导出走预设形状目录）"],
    "preview_equals_export": True,
    "audit": "tools/slide_audit.py --gate 必须 error 0",
    "no_write": ["content/plans/", "模板原稿", ".deps/ppt-master"],
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="只比对，不写文件（CI 用）")
    args = ap.parse_args()
    if not (SKILL / "SKILL.md").is_file():
        print("找不到 ppt-master（.deps/ppt-master）。先跑 deploy/setup_skills.cmd")
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    vs = compile_visual_styles()
    md = compile_modes()
    br = compile_brands()
    ly = compile_layouts()
    payloads = {
        "visual_styles.json": vs,
        "modes.json": md,
        "brands.json": br,
        "layouts.json": ly,
        "_meta.json": {
            "compiled_by": "tools/compile_design_registry.py",
            "source_skill": "ppt-master",
            "source_commit": _git_commit(PPT_MASTER),
            "source_license": "MIT",
            "counts": {"visual_styles": len(vs["styles"]), "modes": len(md["modes"]),
                       "brands": len(br["brands"]), "layout_families": len(ly["families"]),
                       "layouts": ly["layout_total"]},
            "note": ("自包含注册表：两份 skill 不在场也能跑（docs/29 §1.2 红线）。"
                     "guizang-ppt-skill（AGPL）不参与编译，只在文档里核对数值。"),
            "locked": LOCKED,
        },
    }
    for name, obj in payloads.items():
        text = json.dumps(obj, ensure_ascii=False, indent=1) + "\n"
        target = OUT / name
        if args.check:
            same = target.is_file() and target.read_text(encoding="utf-8") == text
            print(("OK  " if same else "DIFF") + "  " + name)
            continue
        target.write_text(text, encoding="utf-8")
        print(f"写出 {name}  {len(text) // 1024} KB")
    if not args.check:
        c = payloads["_meta.json"]["counts"]
        print(f"盘点：视觉风格 {c['visual_styles']} · 模式 {c['modes']} · 品牌 {c['brands']} · "
              f"版式族 {c['layout_families']} · 版式 {c['layouts']}")
        print(f"来源 commit：{payloads['_meta.json']['source_commit']}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
