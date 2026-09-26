# -*- coding: utf-8 -*-
"""版式审计（`slide_audit`）单测：基线稳定、指标口径正确、**坏页抓得住**。

对应 `docs/23` M2 / `docs/22` §7.1、§7.1b：
- 好页 0 error；数字可复现（同输入同输出）；
- 负样本（越界 / 白名单 / 字号阶梯 / 边距）必须各自被抓到 —— 否则闸门就是摆设；
- 主色按**墨迹面积**算（用包围盒量曲线会把 2px 细线算成一大块色板，这是踩过的坑）。
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

_PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJ / "src"))

from edu_agent import ppt_svg                              # noqa: E402
from edu_agent import slide_audit as aud                   # noqa: E402
from edu_agent import slide_deck                           # noqa: E402
from edu_agent import ppt_tokens as tk                     # noqa: E402

THEMES = ("tech-editorial", "illustration", "hand-drawn")


def _deck(theme: str = "tech-editorial") -> dict:
    return slide_deck.build_deck(theme, slide_deck.SAMPLE_DECK, title="3.3 幂函数")


class BaselineTest(unittest.TestCase):
    def test_all_themes_have_no_errors(self):
        for tid in THEMES:
            with self.subTest(theme=tid):
                rep = aud.audit_deck(tid)
                self.assertEqual(rep["stats"]["errors"], 0, rep["errors"])
                self.assertTrue(rep["ok"])

    def test_metrics_within_limits(self):
        for tid in THEMES:
            base = aud.baseline([tid])[tid]
            with self.subTest(theme=tid):
                self.assertGreaterEqual(base["active_ratio_min"], aud.LIMITS["active_ratio_min"])
                self.assertLessEqual(base["bottom_gap_max"], aud.LIMITS["bottom_gap_max"])
                self.assertEqual(base["collisions"], 0)
                self.assertLess(base["accent_ratio_max"], aud.LIMITS["accent_ratio_max"])
                self.assertGreaterEqual(base["title_gap_min"], aud.LIMITS["title_gap_min"])
                # 字号必须正好是 tokens 里那 5 档
                self.assertEqual(base["font_sizes"],
                                 sorted(float(tk.load(tid)["type"][k]) for k in tk.TYPE_KEYS))
                # 边距不低于该主题的 tokens.margin
                self.assertGreaterEqual(base["margin_min"], float(tk.load(tid)["canvas"]["margin"]))

    def test_deterministic(self):
        a = aud.baseline(["tech-editorial"])["tech-editorial"]
        b = aud.baseline(["tech-editorial"])["tech-editorial"]
        self.assertEqual(a, b, "同样的输入必须给出同样的数字（否则基线无意义）")

    def test_quiet_side_is_info_not_error(self):
        """左对齐版式的右倾是"设计如此"，不该判不合格。"""
        rep = aud.audit_pages("tech-editorial", _deck()["pages"], _deck()["layouts"])
        codes = {f["code"] for f in rep["errors"]}
        self.assertNotIn("imbalance", codes)
        self.assertTrue(any(f["code"] == "imbalance" for f in rep["infos"]))


class NegativeSampleTest(unittest.TestCase):
    """闸门要能抓住坏页（`tools/slide_audit.py --selftest` 的同一批用例）。"""

    def _codes(self, pages, layouts) -> set[str]:
        return {f["code"] for f in aud.audit_pages("tech-editorial", pages, layouts)["errors"]}

    def test_long_cover_title_overflows(self):
        deck = _deck()
        pages = [deck["pages"][0].replace("3.3 幂函数", "3.3 幂函数与它的十七个常见变形以及全部图象性质汇总")] + deck["pages"][1:]
        self.assertIn("overflow", self._codes(pages, deck["layouts"]))

    def test_unknown_layout_is_rejected(self):
        deck = _deck()
        self.assertIn("whitelist", self._codes(deck["pages"], ["cover", "not-a-layout", deck["layouts"][2]]))

    def test_stray_font_size_is_rejected(self):
        deck = _deck()
        pages = [deck["pages"][0], deck["pages"][1].replace('font-size="24.0"', 'font-size="13.0"'), deck["pages"][2]]
        self.assertIn("font-scale", self._codes(pages, deck["layouts"]))

    def test_content_outside_margin_is_rejected(self):
        deck = _deck()
        pages = [deck["pages"][0].replace('x="72.0"', 'x="20.0"')] + deck["pages"][1:]
        self.assertIn("margin", self._codes(pages, deck["layouts"]))

    def test_too_many_blocks_would_collide_or_overflow(self):
        """密度上限不是摆设：真塞 8 块（绕过 emit 的截断）时审计要报警。"""
        theme = tk.load("tech-editorial")
        cv = ppt_svg.Canvas(theme)
        blocks = "".join(ppt_svg.text(cv, 96, 200 + i * 20, f"第 {i} 行", size=24) for i in range(8))
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
               + ppt_svg.rect(0, 0, 1280, 720, fill="#FFFFFF") + blocks + "</svg>")
        codes = {f["code"] for f in aud.audit_pages("tech-editorial", [svg], ["bullets"])["errors"]}
        self.assertTrue({"collision", "overflow"} & codes, codes)


class BoxTest(unittest.TestCase):
    def test_text_box_follows_anchor(self):
        theme = tk.load("tech-editorial")
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
               + ppt_svg.text(ppt_svg.Canvas(theme), 600, 300, "中间", size=24, anchor="middle")
               + ppt_svg.text(ppt_svg.Canvas(theme), 600, 400, "右对齐", size=24, anchor="end") + "</svg>")
        boxes = aud.boxes_from_svg(svg)
        self.assertAlmostEqual(boxes[0]["x"] + boxes[0]["w"] / 2, 600, places=1)
        self.assertAlmostEqual(boxes[1]["x"] + boxes[1]["w"], 600, places=1)
        self.assertAlmostEqual(boxes[0]["h"], 24, places=2)

    def test_canvas_background_is_not_content(self):
        theme = tk.load("tech-editorial")
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
               + ppt_svg.rect(0, 0, 1280, 720, fill=theme["canvas"]["bg"])
               + ppt_svg.text(ppt_svg.Canvas(theme), 72, 100, "标题", size=44) + "</svg>")
        boxes = aud.boxes_from_svg(svg)
        self.assertEqual([b["role"] for b in boxes], ["text"])

    def test_ink_area_uses_stroke_not_bbox(self):
        """细长曲线的"墨迹"应该很小 —— 这是主色占比不被误判的关键。"""
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">'
               '<polyline points="0,700 640,0 1280,700" fill="none" stroke="#2F6BFF" stroke-width="2"/></svg>')
        box = aud.boxes_from_svg(svg)[0]
        self.assertGreater(box["w"] * box["h"], 400000, "包围盒很大")
        self.assertLess(box["ink"], 5000, "但墨迹很小")

    def test_empty_rect_helpers(self):
        self.assertAlmostEqual(aud.largest_empty_rect([], 1280, 720), 1.0, places=2)
        full = [{"x": 0, "y": 0, "w": 1280, "h": 720}]
        self.assertLess(aud.largest_empty_rect(full, 1280, 720), 0.02)


class NoDiskWriteTest(unittest.TestCase):
    """审计只读不写（`--json` 才写临时目录）。"""

    WATCH = (_PROJ / "data" / "tmp" / "slide_audit", _PROJ / "content" / "plans")

    def test_audit_does_not_write(self):
        def snap() -> dict:
            out = {}
            for d in self.WATCH:
                if d.exists():
                    for p in d.rglob("*"):
                        if p.is_file():
                            out[str(p)] = p.stat().st_mtime
            return out

        before = snap()
        aud.audit_deck("tech-editorial")
        aud.baseline(["illustration"])
        self.assertEqual(before, snap())


if __name__ == "__main__":
    unittest.main()
