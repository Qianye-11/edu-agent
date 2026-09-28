# -*- coding: utf-8 -*-
"""设计规格注册表（R27a）：读编译产物 → 给 agent 一份"可调什么"的目录，并把一份 `design` 解析成具体数值。

为什么单独一个模块（`docs/29` §2.4）：老师要的是"**通识 agent 收到要求后能去调控预览里的 PPT，
而不是固化成固定的东西**"。所以：

* **注册表**（`content/ppt_templates/_design/*.json`，`tools/compile_design_registry.py` 编译）
  = 视觉风格 18（纪律 + 我们给的默认数值）· 沟通模式 5 · 品牌 21（色板）· 版式 7 族 86 个（槽位 + 装饰）；
* **解析器**（本模块）= 把 `{visual_style, mode, brand, overrides, per_page}` 解析成**一份 tokens 口径的数值**
  （palette / type / geometry / density / canvas）+ 版式选择，**越界夹回并记 warn**；
* **锁死清单**（`_meta.json` 的 `locked`）= 无论怎么调都不许破的底线（画布 / 安全边距 / 元素白名单 /
  预览=导出 / 审计 error 0 / 落盘红线）。

注册表是**自包含**的：两份 skill（`.deps/`）不在场时，本模块与目录接口照常工作。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DESIGN_DIR = ROOT / "content" / "ppt_templates" / "_design"
BUILTIN_DIR = ROOT / "content" / "ppt_templates" / "_builtin"

#: 十六进制颜色（3 位或 6 位）
_HEX_RE = re.compile(r"^#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$")

#: 允许 agent 覆盖的数值键（其余一律忽略 —— 与 `clean_slides` 一个思路：不认识的不乱写）
OVERRIDE_KEYS = {
    "palette": ("bg", "text", "accent", "sub", "line", "soft"),
    "geometry": ("radius", "line_w"),
    "density": ("blocks_max", "chars_per_block_max"),
    "type": ("cover", "title", "label", "body", "caption", "lh_title", "lh_body"),
}

#: 硬边界（越界夹回；"不固化 ≠ 不设底线"）
LIMITS = {
    "palette": {},
    "geometry": {"radius": (0, 16), "line_w": (0.5, 2.0)},
    "density": {"blocks_max": (1, 8), "chars_per_block_max": (6, 40)},
    "type": {"cover": (40, 96), "title": (28, 56), "label": (9, 16),
             "body": (16, 30), "caption": (10, 18), "lh_title": (0.95, 1.6), "lh_body": (1.1, 2.0)},
}


def _read_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def visual_styles() -> list[dict]:
    return _read_json(DESIGN_DIR / "visual_styles.json")["styles"]


@lru_cache(maxsize=1)
def modes() -> list[dict]:
    return _read_json(DESIGN_DIR / "modes.json")["modes"]


@lru_cache(maxsize=1)
def brands() -> list[dict]:
    return _read_json(DESIGN_DIR / "brands.json")["brands"]


@lru_cache(maxsize=1)
def layout_families() -> list[dict]:
    return _read_json(DESIGN_DIR / "layouts.json")["families"]


@lru_cache(maxsize=1)
def locked() -> dict:
    return _read_json(DESIGN_DIR / "_meta.json").get("locked", {})


@lru_cache(maxsize=1)
def meta() -> dict:
    return _read_json(DESIGN_DIR / "_meta.json")


def base_tokens() -> dict:
    """基准 tokens（内置主题里的第一套已验收数值）。"""
    return _read_json(BUILTIN_DIR / "tech-editorial.json")


def find_style(sid: str) -> dict | None:
    sid = str(sid or "").strip()
    for s in visual_styles():
        if s["id"] == sid:
            return s
    return None


def find_brand(bid: str) -> dict | None:
    bid = str(bid or "").strip().lower()
    for b in brands():
        if b["id"].lower() == bid or b["name"].lower() == bid:
            return b
    return None


def find_layout(family_id: str, layout_id: str) -> dict | None:
    """按 id 找版式。

    ⚠️ 版式 id **会跨族重名**（`title_content` / `hero_statement` 在 `presentation_core` 与
    `presentation_core_43` 里都有）。没指定族时**优先 16:9 主力族 `presentation_core`**，
    再按族名顺序找 —— 结果可预期，并且把命中的族一起返回（踩过：随手取第一个会拿到另一个族）。
    """
    fams = layout_families()
    order = sorted(fams, key=lambda f: (f["id"] != "presentation_core", f["id"]))
    for f in order:
        if family_id and f["id"] != family_id:
            continue
        for l in f["layouts"]:
            if l["id"] == layout_id:
                return {"family": f["id"], **l}
    return None


def catalog(*, full: bool = False) -> dict:
    """给 agent 的"可调什么"目录。

    `full=False`（默认）只列 id / 一句话 / 默认值摘要 —— 目录接口别把 200KB 的版式几何一次吐出去；
    `full=True` 才带上规则全文与版式槽位（供面板或深度调用）。
    """
    styles = []
    for s in visual_styles():
        item: dict[str, Any] = {
            "id": s["id"], "group": s["group"], "character": s["character"],
            "paired_rendering": s["paired_rendering"], "illustration": s["illustration"],
            "defaults": s["defaults"], "tuned": s["tuned"], "note": s["note"],
        }
        if full:
            item["rules"] = s["rules"]
            item["must"] = s["must"]
        styles.append(item)
    ms = [{"id": m["id"], "skeleton": m["skeleton"], "context": m["context"],
           **({"rules": m["rules"], "must": m["must"]} if full else {})} for m in modes()]
    bs = [{"id": b["id"], "name": b["name"], "summary": b["summary"], "tone": b["tone"],
           "palette": b["palette"],
           **({"source": b["source"]} if full else {})} for b in brands()]
    fam = []
    for f in layout_families():
        item: dict[str, Any] = {"id": f["id"], "summary": f["summary"],
                                "canvas_format": f["canvas_format"], "count": len(f["layouts"]),
                                "layouts": [{"id": l["id"], "name": l["name"],
                                             "slots": [s["placeholder"] for s in l["slots"]]}
                                            for l in f["layouts"]]}
        if full:
            item["layouts"] = f["layouts"]
        fam.append(item)
    base = base_tokens()
    return {
        "ok": True,
        "schema": "edu-agent.design-catalog.v1",
        "source": {"skill": meta().get("source_skill"), "commit": meta().get("source_commit"),
                   "license": meta().get("source_license"),
                   "compiled_by": meta().get("compiled_by")},
        "counts": meta().get("counts"),
        "visual_styles": styles,
        "modes": ms,
        "brands": bs,
        "layout_families": fam,
        "tokens_base": {"canvas": base["canvas"], "type": base["type"],
                        "ranges": base["ranges"], "weights": base["weights"],
                        "fonts": base["fonts"], "geometry": base["geometry"]},
        "overridable": {k: list(v) for k, v in OVERRIDE_KEYS.items()},
        "locked": locked(),
        "hints": [
            "选一个 visual_style（看 character 与 defaults）；mode 与 style 相互独立，任意搭配。",
            "想换主色就给 overrides.palette.accent；想更松/更密给 overrides.density。",
            "越界的数值会被夹回并在 warnings 里说明 —— 不会报错，但也不会生效。",
            "per_page 可以逐页指定版式（id 见 layout_families）。",
            "锁死项（locked）不许破：画布 1280×720、安全边距、元素白名单、预览=导出、审计 error 0。",
        ],
    }


@dataclass
class Resolved:
    """一份 `design` 解析出来的东西：数值 + 版式 + 说明。"""

    palette: dict
    type: dict
    geometry: dict
    density: dict
    canvas: dict
    fonts: dict
    weights: dict
    ranges: dict
    visual_style: str = ""
    mode: str = ""
    brand: str = ""
    per_page: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    applied: list = field(default_factory=list)

    def as_tokens(self, theme_id: str = "", name: str = "") -> dict:
        """合成一份**与内置主题同构**的 tokens —— 后面的渲染/审计全都按同一口径读它。"""
        base = base_tokens()
        out = dict(base)
        out.update({
            "id": theme_id or ("design-" + (self.visual_style or "custom")),
            "name": name or (self.visual_style or "自定义设计"),
            "canvas": dict(self.canvas),
            "palette": dict(self.palette),
            "type": dict(self.type),
            "geometry": dict(self.geometry),
            "density": dict(self.density),
            "fonts": dict(self.fonts),
            "weights": dict(self.weights),
            "ranges": dict(self.ranges),
            "source": "design_spec:" + (self.visual_style or "custom"),
            "notes": ["由 R27a 的设计规格解析器合成（视觉风格 = " + (self.visual_style or "未指定") + "）"],
        })
        return out

    def summary(self) -> dict:
        return {"visual_style": self.visual_style, "mode": self.mode, "brand": self.brand,
                "palette": self.palette, "density": self.density, "geometry": self.geometry,
                "per_page": self.per_page, "applied": self.applied, "warnings": self.warnings}


def _clamp(group: str, key: str, value: Any, warn: list, applied: list) -> Any:
    lo_hi = LIMITS.get(group, {}).get(key)
    if lo_hi is None:
        return value
    lo, hi = lo_hi
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        fixed = min(max(float(value), lo), hi)
        if abs(fixed - float(value)) > 1e-9:
            warn.append(f"{group}.{key}={value} 越界，已夹到 {fixed}（范围 {lo}~{hi}）")
        return int(fixed) if float(fixed).is_integer() and isinstance(lo, int) else fixed
    return value


def resolve(design: dict | None, *, theme_id: str = "") -> Resolved:
    """把一份 `design` 解析成具体数值。

    `design` 形状（都是可选的，缺省 = 基准 tokens）::

        {"visual_style": "ink-wash", "mode": "instructional", "brand": "huawei",
         "overrides": {"palette": {"accent": "#9E2B25"}, "density": {"blocks_max": 4}},
         "per_page": [{"layout": "section_header"}, {"layout": "title_content"}]}
    """
    base = base_tokens()
    warn: list[str] = []
    applied: list[str] = []
    design = design if isinstance(design, dict) else {}

    style_id = str(design.get("visual_style") or "").strip()
    style = find_style(style_id) if style_id else None
    if style_id and not style:
        warn.append(f"没有这套视觉风格：{style_id}（可用 id 见 visual_styles）—— 已按基准数值渲染")

    mode_id = str(design.get("mode") or "").strip()
    if mode_id and not any(m["id"] == mode_id for m in modes()):
        warn.append(f"没有这个沟通模式：{mode_id}")
    brand_id = str(design.get("brand") or "").strip()
    brand = find_brand(brand_id) if brand_id else None
    if brand_id and not brand:
        warn.append(f"没有这个品牌：{brand_id}")

    # ① 起点：基准 tokens；② 叠视觉风格的默认值；③ 叠品牌色板；④ 叠 agent 的 overrides
    # ⚠️ 基准 tokens 的 palette 里没有 bg（底色在 canvas.bg）—— 这里补齐，否则合成的 tokens 缺底色（踩过）。
    palette = {**base["palette"], "bg": base["canvas"]["bg"]}
    geometry = {k: base["geometry"][k] for k in ("line_w", "radius", "shadow", "glow", "gradient", "texture")}
    if isinstance(geometry.get("radius"), list):                     # 基准里 radius 是区间
        geometry["radius"] = geometry["radius"][0]
    density = dict(base["density"])
    if style:
        palette.update({k: v for k, v in (style["defaults"].get("palette") or {}).items() if v})
        geometry.update({k: v for k, v in (style["defaults"].get("geometry") or {}).items() if v is not None})
        density.update({k: v for k, v in (style["defaults"].get("density") or {}).items() if v})
        applied.append("visual_style:" + style["id"])
    if brand:
        bp = brand.get("palette") or {}
        if bp.get("primary"):
            palette["accent"] = bp["primary"]
        if bp.get("bg"):
            palette["bg"] = bp["bg"]
        if bp.get("text"):
            palette["text"] = bp["text"]
        if bp.get("muted-text"):
            palette["sub"] = bp["muted-text"]
        if bp.get("border"):
            palette["line"] = bp["border"]
        if bp.get("surface"):
            palette["soft"] = bp["surface"]
        applied.append("brand:" + brand["id"])

    ov = design.get("overrides") if isinstance(design.get("overrides"), dict) else {}
    for group, keys in OVERRIDE_KEYS.items():
        src = ov.get(group) if isinstance(ov.get(group), dict) else {}
        for k in keys:
            if k not in src:
                continue
            v = src[k]
            if v in ("", None):
                continue
            if group == "palette":
                if isinstance(v, str) and _HEX_RE.match(v.strip()):
                    palette[k] = v.strip().upper() if len(v.strip()) == 7 else v.strip()
                    applied.append(f"override.palette.{k}")
                else:
                    warn.append(f"overrides.palette.{k}={v!r} 不是 #RRGGBB / #RGB，已忽略")
                continue
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                warn.append(f"overrides.{group}.{k}={v!r} 不是数字，已忽略")
                continue
            if group == "type":
                continue                                 # type 的覆盖在下面统一处理（这里夹一次会重复告警）
            v = _clamp(group, k, v, warn, applied)
            if group == "geometry":
                geometry[k] = v
            elif group == "density":
                density[k] = int(v)
            applied.append(f"override.{group}.{k}")

    typ = dict(base["type"])
    tov = ov.get("type") if isinstance(ov.get("type"), dict) else {}
    for k in OVERRIDE_KEYS["type"]:
        if k in tov and isinstance(tov[k], (int, float)) and not isinstance(tov[k], bool):
            typ[k] = _clamp("type", k, tov[k], warn, applied)
            applied.append(f"override.type.{k}")

    per_page = []
    for item in (design.get("per_page") or []):
        if isinstance(item, dict) and item.get("layout"):
            lay = str(item["layout"]).strip()
            fam = str(item.get("family") or "").strip()
            hit = find_layout(fam, lay)
            if hit:
                per_page.append({"layout": lay, "family": hit["family"]})
            else:
                per_page.append({"layout": lay, "family": fam})
                warn.append(f"per_page 里的版式 {lay!r} 不在注册表里（先用兜底版式渲染）")

    return Resolved(
        palette=palette, type=typ, geometry=geometry, density=density,
        canvas=dict(base["canvas"]), fonts=dict(base["fonts"]), weights=dict(base["weights"]),
        ranges=dict(base["ranges"]), visual_style=(style["id"] if style else ""),
        mode=mode_id, brand=(brand["id"] if brand else ""), per_page=per_page,
        warnings=warn, applied=applied,
    )
