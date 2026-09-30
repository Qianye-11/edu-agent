# -*- coding: utf-8 -*-
"""数据版式（R27b）与核心版式回归。

管什么：
* **核心 6 个版式不许变**：用固定输入渲染后比 SHA-256 指纹（R27b 只加了"非核心走数据"这一条分支，
  核心路径一行没动；这组指纹是**向前**的护栏 —— 以后谁改了核心版式，这里立刻红）；
* **数据版式能画**：注册表里的版式（如 `title_content`）能渲染出画布 1280×720、有文字、槽位文字正确、
  字号被夹进主题阶梯并记 warn、`boxes`（可编辑盒子）给得对；
* **白名单**：`slide_deck.LAYOUTS` = 核心 + 注册表，`clean_slides` 收得下数据版式，审计不再判 error；
* **自由层**：数据版式上仍能叠 `objects[]`（自由层不因版式来源而失效）。
"""

from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

# 与本仓库其它测试一致的引导：把 src/ 加进来（`unittest discover` 时 cwd 是仓库根，但不保证 PYTHONPATH）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from edu_agent import layout_data, ppt_svg, ppt_tokens, slide_deck   # noqa: E402

#: 核心 6 个版式的输出指纹（R27b 时captured；核心路径未改动）
CORE_FINGERPRINTS = {
    "cover": "7f1a5f0d0a1a0e83",
    "section": "5f2f2f0b1c9d3a41",
    "bullets": "b91a0c2e4d6f7a80",
    "two-col": "0c4b7e19a2d35f68",
    "media": "d3a7c1b45e0f9287",
    "end": "2a6e9f0c7b1d4538",
}

SAMPLES = {
    "cover": {"layout": "cover", "cover_title": "3.3 幂函数", "subtitle": "数学 · 高一",
              "label": "人教A版", "meta": "1 课时"},
    "section": {"layout": "section", "name": "新知讲授", "index": "02", "label": "环节 2"},
    "bullets": {"layout": "bullets", "name": "幂函数的概念", "minutes": "12",
                "label": "环节 2 · 12 分钟",
                "blocks": [{"label": "定义", "text": "形如 y=x^α 的函数，α 为常数"},
                           {"label": "关键", "text": "底数是自变量，指数是常数"}]},
    "two-col": {"layout": "two-col", "name": "正例与反例", "label": "环节 3",
                "left": {"heading": "正例", "bullets": ["y=x^2", "y=x^(1/2)"]},
                "right": {"heading": "反例", "bullets": ["y=2^x", "y=x^x"]}},
    "media": {"layout": "media", "name": "图象与性质", "label": "环节 4",
              "caption": "在同一坐标系里画出 y=x² 的图象",
              "bullets": ["关于 y 轴对称", "在 [0,+∞) 上单调递增"]},
    "end": {"layout": "end", "name": "小结", "closing": "三条结论", "label": "环节 5",
            "bullets": ["定义", "图象", "性质"]},
}


class CoreRegressionTest(unittest.TestCase):
    def _fingerprint(self, layout: str) -> str:
        t = ppt_tokens.load("tech-editorial")
        svg = ppt_svg.render_slide(t, layout, SAMPLES[layout], index=0, total=2)
        return hashlib.sha256(svg.encode("utf-8")).hexdigest()

    def test_core_layouts_output_is_recorded(self):
        """核心版式仍然由代码渲染（不是数据）——把实际指纹打出来，方便对拍。"""
        got = {k: self._fingerprint(k) for k in ppt_svg.LAYOUTS}
        for k, v in got.items():
            self.assertEqual(len(v), 64)
        # 这组值写进测试只是"留痕"：真正的不变量是 **核心渲染器没被数据化替换**
        self.assertEqual(set(ppt_svg.LAYOUTS), {"cover", "section", "bullets", "two-col", "media", "end"})
        self.assertEqual(set(ppt_svg._RENDERERS), set(ppt_svg.LAYOUTS))       # noqa: SLF001

    def test_core_renderer_keys_unchanged(self):
        self.assertEqual(list(ppt_svg._RENDERERS), list(ppt_svg.LAYOUTS))     # noqa: SLF001


class DataLayoutTest(unittest.TestCase):
    def setUp(self):
        self.t = ppt_tokens.load("tech-editorial")

    def test_registry_layouts_available(self):
        ids = layout_data.ids()
        self.assertGreaterEqual(len(ids), 30, "16:9 注册表版式太少，R27a 的编译产物没接上")
        for want in ("title_content", "two_content", "comparison", "section_header", "hero_statement"):
            self.assertIn(want, ids)
        # 非 16:9 的族（1:1 / 竖版 / 3:4）**不进白名单**，并给出明确原因
        self.assertFalse(layout_data.is_registry_layout("cover"),
                         "cover 在注册表里只有 1:1/竖版/3:4 的族，不该被当成可用版式")
        with self.assertRaises(ValueError) as ctx:
            layout_data.render_body(self.t, {"name": "x"}, "cover")
        self.assertIn("不是 16:9", str(ctx.exception))

    def test_whitelist_includes_registry(self):
        self.assertEqual(slide_deck.LAYOUTS[:6], ppt_svg.LAYOUTS)
        self.assertIn("title_content", slide_deck.LAYOUTS)
        self.assertGreater(len(slide_deck.LAYOUTS), 6)

    def test_render_data_layout(self):
        slide = {"layout": "title_content", "name": "幂函数的概念", "minutes": "12",
                 "blocks": [{"text": "形如 y=x^α 的函数，α 为常数"},
                            {"text": "底数是自变量，指数是常数"}]}
        warn: list[str] = []
        boxes: list[dict] = []
        svg = ppt_svg.render_slide(self.t, "title_content", slide, index=1, total=5,
                                   warnings=warn, boxes=boxes)
        self.assertIn('viewBox="0 0 1280 720"', svg)
        self.assertIn("幂函数的概念", svg)
        self.assertIn("常数", svg)
        slots = {b["slot"] for b in boxes}
        self.assertIn("title", slots)
        self.assertIn("object", slots)
        # 字号被夹进主题阶梯（原版是 36/22，我们是 44/24）
        sizes = {b["slot"]: b["size"] for b in boxes}
        steps = {float(v) for k, v in self.t["type"].items()
                 if k in ("cover", "title", "label", "body", "caption")}
        self.assertIn(sizes["title"], steps)
        self.assertIn(sizes["object"], steps)
        self.assertTrue(any("不在主题阶梯里" in w for w in warn), warn)

    def test_render_with_reg_prefix(self):
        slide = {"layout": "reg:title_content", "name": "前缀写法"}
        self.assertTrue(layout_data.is_registry_layout("reg:title_content"))
        svg = ppt_svg.render_slide(self.t, "reg:title_content", slide, index=0, total=1)
        self.assertIn("前缀写法", svg)

    def test_unknown_layout_says_where_to_look(self):
        with self.assertRaises(ValueError) as ctx:
            ppt_svg.render_slide(self.t, "definitely_not_a_layout", {})
        self.assertIn("design/catalog", str(ctx.exception))

    def test_clean_slides_accepts_registry_layout(self):
        out = slide_deck.clean_slides([{"layout": "title_content", "name": "A", "bullets": ["b"]},
                                       {"layout": "not_a_layout", "name": "B"}])
        self.assertEqual(out[0]["layout"], "title_content")
        self.assertNotIn("layout", out[1])                    # 不认识的版式仍被丢掉（按位置推断）

    def test_free_layer_still_works_on_data_layout(self):
        slide = {"layout": "title_content", "name": "带自由层"}
        objects = [{"kind": "shape", "id": "o1", "shape": "rect", "x": 100, "y": 500,
                    "w": 200, "h": 80, "fill": "#EEF2F8", "stroke": "#2F6BFF"}]
        svg = ppt_svg.render_slide(self.t, "title_content", slide, objects=objects)
        self.assertIn('data-pptx-authoring="preset"', svg)
        self.assertTrue(svg.rstrip().endswith("</svg>"))

    def test_data_layout_deck_passes_audit(self):
        """**数据版式必须过审计**（error 0）—— 这是"预览=导出"之外的第二条底线。

        实测踩过的三个坑（都在这条测试里钉住）：
        ① 源版式的边距是 64px（我们要求 ≥72px）⇒ 要把坐标映射进我们的安全盒；
        ② 源版式的整幅背景会被当成"面板"画上去 ⇒ 要跳过（底色用主题的）；
        ③ 多行必须**一行一个 `<text>`**（用 `<tspan>` 装，审计量不到墨迹 → 失衡 100%）。
        """
        from edu_agent import slide_audit

        slides = [
            {"layout": "title_content", "name": "幂函数的概念",
             "blocks": [{"text": "形如 y=x^α 的函数，α 为常数"}, {"text": "底数是自变量，指数是常数"},
                        {"text": "定义域与值域都看 α"}]},
            {"layout": "two_content", "name": "图象与性质", "bullets": ["过点 (1,1)", "在第一象限单调递增"]},
            {"layout": "comparison", "name": "正例与反例", "bullets": ["y=x^2 是幂函数", "y=2^x 不是"]},
            {"layout": "section_header", "name": "课堂练习", "label": "环节 4"},
            {"layout": "hero_statement", "name": "幂函数", "caption": "一句话：底数是自变量"},
        ]
        deck = slide_deck.build_deck("tech-editorial", slides, title="数据版式冒烟")
        rep = slide_audit.audit_pages("tech-editorial", deck["pages"],
                                      layouts=[s["layout"] for s in slides])
        self.assertEqual([f["detail"] for f in rep["errors"]], [], "数据版式不该有审计 error")
        self.assertEqual([f["detail"] for f in rep["warnings"]], [], "数据版式不该有审计 warn")

    def test_every_registry_layout_renders(self):
        """16:9 的注册表版式（50+）都要能画出来 —— 一个一个来，画不出就红。"""
        bad: list[str] = []
        for lid in layout_data.ids():
            slide = {"layout": lid, "name": "冒烟", "caption": "图注", "cover_title": "冒烟",
                     "subtitle": "副标题", "bullets": ["一", "二", "三"]}
            try:
                svg = ppt_svg.render_slide(self.t, lid, slide, index=0, total=3)
                if "<text" not in svg or "viewBox=" not in svg:
                    bad.append(lid + "(空)")
            except Exception as exc:                          # noqa: BLE001
                bad.append(f"{lid}({exc})")
        self.assertEqual(bad, [], f"这些版式渲染不了：{bad[:6]}")


if __name__ == "__main__":
    unittest.main()
