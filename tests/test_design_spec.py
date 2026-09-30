# -*- coding: utf-8 -*-
"""设计规格注册表与解析器（R27a）的单测。

管什么：
* 注册表**自包含**：只读 `content/ppt_templates/_design/*.json`，两份 skill 不在场也能用；
* 目录（catalog）的形状与计数（18 风格 / 5 模式 / 21 品牌 / 7 族 86 版式）；
* `resolve()` 的叠加顺序（基准 → 视觉风格 → 品牌 → overrides）、**越界夹回 + warn**、
  不认识的风格/版式如实报错、`as_tokens()` 与内置主题**同构**（渲染与审计读同一口径）；
* 锁死清单在目录里（"不固化 ≠ 不设底线"）。
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

# 与本仓库其它测试一致的引导：把 src/ 加进来（`unittest discover` 时 cwd 是仓库根，但不保证 PYTHONPATH）
_PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJ / "src"))

from edu_agent import design_spec as ds          # noqa: E402

ROOT = _PROJ


class RegistryTest(unittest.TestCase):
    def test_registry_files_are_committed_and_selfcontained(self):
        d = ROOT / "content" / "ppt_templates" / "_design"
        for name in ("visual_styles.json", "modes.json", "brands.json", "layouts.json", "_meta.json"):
            self.assertTrue((d / name).is_file(), f"缺注册表文件：{name}")
        meta = json.loads((d / "_meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["source_skill"], "ppt-master")
        self.assertEqual(meta["source_license"], "MIT")
        # 自包含：注册表里不许出现 .deps 路径（否则换机器就断）
        blob = "".join((d / n).read_text(encoding="utf-8") for n in
                       ("visual_styles.json", "modes.json", "brands.json", "layouts.json"))
        self.assertNotIn(".deps", blob)

    def test_counts(self):
        c = ds.catalog()
        self.assertEqual(c["ok"], True)
        self.assertEqual(c["schema"], "edu-agent.design-catalog.v1")
        self.assertEqual(c["counts"]["visual_styles"], 18)
        self.assertEqual(c["counts"]["modes"], 5)
        self.assertEqual(c["counts"]["brands"], 21)
        self.assertEqual(c["counts"]["layout_families"], 7)
        self.assertEqual(c["counts"]["layouts"], 86)
        self.assertEqual(len(c["visual_styles"]), 18)
        self.assertEqual(sum(f["count"] for f in c["layout_families"]), 86)

    def test_every_style_has_rules_and_defaults(self):
        for s in ds.visual_styles():
            with self.subTest(style=s["id"]):
                self.assertTrue(s["character"], "缺一句话说明")
                self.assertTrue(s["rules"]["shape"], "缺形状/装饰纪律")
                self.assertTrue(s["rules"]["colors"], "缺用色纪律")
                pal = s["defaults"]["palette"]
                for k in ("bg", "text", "accent"):
                    self.assertRegex(pal[k], r"^#[0-9A-Fa-f]{6}$", f"{k} 不是 #RRGGBB")
                self.assertIn("blocks_max", s["defaults"]["density"])
                self.assertIsInstance(s["tuned"], bool)

    def test_hand_tuned_styles_are_marked(self):
        tuned = {s["id"] for s in ds.visual_styles() if s["tuned"]}
        self.assertLessEqual({"swiss-minimal", "ink-wash", "editorial"}, tuned,
                             "先人工调的三套要标 tuned=true")
        ink = ds.find_style("ink-wash")
        self.assertEqual(ink["defaults"]["palette"]["accent"].upper(), "#9E2B25")  # 单一朱红印色
        swiss = ds.find_style("swiss-minimal")
        self.assertEqual(swiss["defaults"]["geometry"]["radius"], 0)               # 直角
        self.assertFalse(swiss["defaults"]["geometry"]["shadow"])

    def test_brands_have_palette(self):
        for b in ds.brands():
            with self.subTest(brand=b["id"]):
                self.assertTrue(b["palette"].get("primary"), "缺主色")
                self.assertRegex(b["palette"]["primary"], r"^#[0-9A-Fa-f]{6}$")

    def test_layouts_have_slots_and_decorations(self):
        fams = {f["id"]: f for f in ds.layout_families()}
        self.assertIn("presentation_core", fams)
        lc = {l["id"]: l for l in fams["presentation_core"]["layouts"]}
        self.assertIn("title_content", lc)
        slots = {s["placeholder"]: s for s in lc["title_content"]["slots"]}
        self.assertIn("title", slots)
        self.assertEqual(slots["title"]["bounds"], [64.0, 40.0, 1152.0, 72.0])
        self.assertGreater(len(lc["title_content"]["decorations"]), 0, "装饰图元没编译进来")

    def test_locked_baseline_is_published(self):
        lk = ds.locked()
        self.assertEqual(lk["canvas"]["w"], 1280)
        self.assertEqual(lk["canvas"]["h"], 720)
        self.assertEqual(lk["safe_margin_min"], 72)
        self.assertIn("rect", lk["elements"])
        self.assertTrue(lk["preview_equals_export"])
        self.assertIn("content/plans/", lk["no_write"])


class ResolveTest(unittest.TestCase):
    def test_defaults_match_base_tokens_when_nothing_given(self):
        r = ds.resolve(None)
        base = ds.base_tokens()
        # 基准 tokens 的 palette 里没有 bg（底色在 canvas.bg）；解析器要把它补齐 —— 这是踩过的坑
        self.assertEqual(r.palette["bg"], base["canvas"]["bg"])
        self.assertEqual(r.palette["accent"], base["palette"]["accent"])
        self.assertEqual(r.density, base["density"])
        self.assertEqual(r.warnings, [])
        self.assertEqual(r.visual_style, "")

    def test_style_overrides_base_then_brand_then_agent(self):
        r1 = ds.resolve({"visual_style": "ink-wash"})
        self.assertEqual(r1.palette["bg"], "#F8F6F2")
        r2 = ds.resolve({"visual_style": "ink-wash", "brand": "huawei"})
        self.assertEqual(r2.palette["accent"], "#CF0A2C", "品牌主色应盖过风格的 accent")
        self.assertEqual(r2.palette["bg"], "#FFFFFF", "品牌底色应盖过风格")
        r3 = ds.resolve({"visual_style": "ink-wash", "brand": "huawei",
                         "overrides": {"palette": {"accent": "#9E2B25"}}})
        self.assertEqual(r3.palette["accent"], "#9E2B25", "agent 的 overrides 最高优先")
        self.assertIn("override.palette.accent", r3.applied)

    def test_out_of_range_is_clamped_with_warning(self):
        r = ds.resolve({"visual_style": "swiss-minimal",
                        "overrides": {"geometry": {"radius": 99}, "density": {"blocks_max": 50},
                                      "type": {"body": 4}}})
        self.assertEqual(r.geometry["radius"], 16)
        self.assertEqual(r.density["blocks_max"], 8)
        self.assertEqual(r.type["body"], 16)
        self.assertEqual(len([w for w in r.warnings if "越界" in w]), 3)

    def test_bad_values_are_ignored_not_written(self):
        r = ds.resolve({"overrides": {"palette": {"accent": "红色"}, "density": {"blocks_max": "很多"}}})
        self.assertNotEqual(r.palette["accent"], "红色")
        self.assertIsInstance(r.density["blocks_max"], int)
        self.assertEqual(len([w for w in r.warnings if "忽略" in w]), 2)

    def test_unknown_style_and_layout_are_reported(self):
        r = ds.resolve({"visual_style": "没有这套", "mode": "也不存在",
                        "per_page": [{"layout": "definitely_not_a_layout"}]})
        self.assertEqual(r.visual_style, "")
        self.assertTrue(any("没有这套视觉风格" in w for w in r.warnings))
        self.assertTrue(any("没有这个沟通模式" in w for w in r.warnings))
        self.assertTrue(any("不在注册表里" in w for w in r.warnings))

    def test_per_page_resolves_family(self):
        r = ds.resolve({"per_page": [{"layout": "title_content"}, {"layout": "hero_statement"}]})
        self.assertEqual([p["layout"] for p in r.per_page], ["title_content", "hero_statement"])
        self.assertTrue(all(p["family"] == "presentation_core" for p in r.per_page))

    def test_as_tokens_is_isomorphic_with_builtin(self):
        base = ds.base_tokens()
        t = ds.resolve({"visual_style": "editorial"}).as_tokens()
        for key in ("canvas", "palette", "type", "geometry", "density", "fonts", "weights", "ranges"):
            self.assertIn(key, t, f"合成 tokens 缺 {key}")
            self.assertIsInstance(t[key], dict)
        self.assertEqual(t["canvas"]["w"], base["canvas"]["w"])
        self.assertEqual(t["canvas"]["h"], base["canvas"]["h"])
        self.assertTrue(t["id"].startswith("design-"))

    def test_catalog_marks_which_layouts_fit_our_canvas(self):
        """我们固定 1280×720：1:1 / 竖版 / 3:4 的族要**明确标 unusable**，别让 agent 选到画不出的版式。"""
        c = ds.catalog()
        fams = {f["id"]: f for f in c["layout_families"]}
        self.assertTrue(fams["presentation_core"]["usable"])
        self.assertTrue(fams["report_core"]["usable"])
        for bad in ("moments_square", "story_vertical", "xiaohongshu_post"):
            self.assertFalse(fams[bad]["usable"], f"{bad} 不是 16:9，应标 unusable")
        usable = [l for f in c["layout_families"] for l in f["layouts"] if l["usable"]]
        self.assertGreaterEqual(len(usable), 30)
        self.assertTrue(all("usable" in l for f in c["layout_families"] for l in f["layouts"]))
        self.assertTrue(any("usable=true" in h for h in c["hints"]))

    def test_catalog_full_includes_rules_and_geometry(self):
        lean = ds.catalog()
        full = ds.catalog(full=True)
        self.assertNotIn("rules", lean["visual_styles"][0])
        self.assertIn("rules", full["visual_styles"][0])
        self.assertIn("decorations", full["layout_families"][0]["layouts"][0])
        self.assertNotIn("decorations", lean["layout_families"][0]["layouts"][0])


class EndpointTest(unittest.TestCase):
    """接口层：目录接口能返回，且 **skill 不在场**时也照常（自包含）。"""

    def test_endpoint_returns_catalog(self):
        from fastapi.testclient import TestClient

        from edu_agent import web_server

        client = TestClient(web_server.app)
        r = client.get("/api/slide/design/catalog")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["counts"]["visual_styles"], 18)
        self.assertEqual(len(body["hints"]), 5)
        r2 = client.get("/api/slide/design/catalog", params={"full": "true"})
        self.assertIn("rules", r2.json()["visual_styles"][0])


if __name__ == "__main__":
    unittest.main()
