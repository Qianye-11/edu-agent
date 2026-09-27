"""版式数值（tokens）与 SVG 渲染的离线单测。

对应 `docs/22-预览版式与导出规格.md`：
- §3 数值唯一权威（合法性、字号在区间内、派生色、字体已装）；
- §5 六版式与槽位；
- §6 上游硬约束（viewBox / 真文本 / 显式 fill / 禁 filter·阴影·渐变·外链图 / 不截断文字）。

这些测试**不依赖网络、不依赖 Edge、不依赖 WPS**（字体检查在非 Windows 上自动跳过）。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from edu_agent import ppt_svg, ppt_tokens as tk  # noqa: E402


def _slide(theme: str, layout: str, **slots) -> str:
    return ppt_svg.render_slide(tk.load(theme), layout, slots, index=0, total=1)


class TokensTest(unittest.TestCase):
    """三套内置主题的数值必须合法、可追溯。"""

    def test_three_builtin_themes_exist(self):
        self.assertEqual(tk.available(), ["hand-drawn", "illustration", "tech-editorial"])

    def test_all_themes_pass_validation(self):
        for theme in tk.available():
            with self.subTest(theme=theme):
                data = tk.load(theme)                      # 内部会校验，不合法就抛
                self.assertEqual(tk.validate(data), [])
                self.assertEqual((data["canvas"]["w"], data["canvas"]["h"]), (1280, 720))
                self.assertTrue(data["source"].endswith("style-system.md"),
                                "每套主题都要写清数值出处（docs/22 §3.2）")

    def test_type_scale_inside_declared_ranges(self):
        for theme in tk.available():
            data = tk.load(theme)
            for key in tk.TYPE_KEYS:
                with self.subTest(theme=theme, key=key):
                    lo, hi = data["ranges"][key]
                    self.assertLessEqual(lo, data["type"][key])
                    self.assertLessEqual(data["type"][key], hi)

    def test_no_forbidden_effects(self):
        for theme in tk.available():
            geo = tk.load(theme)["geometry"]
            self.assertFalse(geo["shadow"] or geo["glow"] or geo["gradient"],
                             "上游 Forbidden Treatments：禁阴影/发光/渐变")

    def test_derived_colors_match_blend(self):
        for theme in tk.available():
            data = tk.load(theme)
            for key, spec in (data.get("derived") or {}).items():
                with self.subTest(theme=theme, key=key):
                    self.assertEqual(data["palette"][key].upper(),
                                     tk.blend(data["palette"][spec["from"]], data["canvas"]["bg"],
                                              spec["alpha"]).upper())

    @unittest.skipUnless(sys.platform == "win32", "字体清单只在 Windows 上查")
    def test_fonts_are_installed(self):
        self.assertTrue(tk.font_installed("微软雅黑"))
        self.assertTrue(tk.font_installed("Arial"))
        self.assertFalse(tk.font_installed("Inter"), "Inter 本机没装，写进 tokens 就该被拦下")


class UnitTest(unittest.TestCase):
    """画布 px 是唯一口径：1px = 0.75pt = 9525 EMU（docs/22 §3.3）。"""

    def test_px_to_pt_and_emu(self):
        self.assertEqual(tk.px_to_pt(24), 18.0)
        self.assertEqual(tk.px_to_pt(72), 54.0)
        self.assertEqual(tk.px_to_emu(1280), 12192000)
        self.assertEqual(tk.px_to_emu(720), 6858000)

    def test_blend_returns_explicit_hex(self):
        self.assertEqual(tk.blend("#000000", "#FFFFFF", 0.5), "#808080")
        self.assertEqual(tk.blend("#2B2B2B", "#F8F6F2", 0.62), "#797877")

    def test_validate_catches_bad_tokens(self):
        data = tk.load("tech-editorial")
        bad = dict(data)
        bad["type"] = dict(data["type"], body=99)          # 超出声明区间
        self.assertTrue(any("body" in p for p in tk.validate(bad)))
        bad2 = dict(data)
        bad2["geometry"] = dict(data["geometry"], shadow=True)
        self.assertTrue(any("阴影" in p for p in tk.validate(bad2)))
        bad3 = dict(data)
        bad3["marker"] = "nope"
        self.assertTrue(any("marker" in p for p in tk.validate(bad3)))


class SvgContractTest(unittest.TestCase):
    """渲染结果必须满足上游硬约束（否则转 pptx 会被挡下）。"""

    LAYOUTS = ppt_svg.LAYOUTS

    def test_all_layouts_render_one_page_each(self):
        for theme in tk.available():
            data = tk.load(theme)
            slides = [{"layout": lo, "title": "标题", "label": "环节 1", "index": "1",
                       "subtitle": "副标题", "meta": "元信息", "caption": "注释",
                       "bullets": ["一", "二"], "closing": "收束",
                       "left": {"heading": "左", "bullets": ["甲"]},
                       "right": {"heading": "右", "bullets": ["乙"]}} for lo in self.LAYOUTS]
            pages = ppt_svg.render_deck(data, slides)
            self.assertEqual(len(pages), len(self.LAYOUTS))
            for lo, svg in zip(self.LAYOUTS, pages):
                with self.subTest(theme=theme, layout=lo):
                    self.assertIn('viewBox="0 0 1280 720"', svg)
                    self.assertIn("<text", svg)
                    self.assertTrue(svg.startswith("<svg") and svg.endswith("</svg>"))
                    self.assertNotIn("<image", svg)
                    self.assertNotIn("filter=", svg)
                    self.assertNotIn("Gradient", svg)
                    self.assertNotIn("drop-shadow", svg)
                    self.assertNotIn("opacity=", svg, "显式颜色，不用透明度（转 pptx 会丢）")

    def test_text_uses_theme_fonts_and_explicit_fill(self):
        svg = _slide("hand-drawn", "bullets", title="幂函数", bullets=["形如 y=x^α"])
        self.assertIn('font-family="Arial, 微软雅黑, sans-serif"', svg)
        self.assertIn('fill="#2B2B2B"', svg)
        self.assertIn('letter-spacing="0.02em"', svg, "手绘主题的字距来自上游 0.02em")

    def test_page_number_inside_safe_margin_and_no_number_on_cover(self):
        data = tk.load("tech-editorial")
        page = ppt_svg.render_slide(data, "bullets", {"title": "要点"}, index=1, total=3)
        self.assertIn(">2 / 3<", page)
        cover = ppt_svg.render_slide(data, "cover", {"title": "封面"}, index=0, total=3)
        self.assertNotIn("1 / 3", cover)

    def test_background_uses_theme_canvas_color(self):
        self.assertIn('fill="#F8F6F2"', _slide("hand-drawn", "cover", title="x"))
        self.assertIn('fill="#FFFFFF"', _slide("tech-editorial", "cover", title="x"))

    def test_marks_use_marker_color(self):
        """插画暖的 accent（浅蓝）做记号看不清 → 记号走 accent2（脏橘）。"""
        svg = _slide("illustration", "bullets", title="t", bullets=[{"label": "定义", "text": "x"}])
        self.assertIn("#E8A87C", svg)
        tech = _slide("tech-editorial", "bullets", title="t", bullets=[{"label": "定义", "text": "x"}])
        self.assertIn("#2F6BFF", tech)


class WrapAndDensityTest(unittest.TestCase):
    """不截断文字；模块超上限只提示拆页。"""

    def test_wrap_never_drops_characters(self):
        s = "形如 y=x^α 的函数，α 为常数，底数是自变量，指数是常数，定义域随 α 变化"
        for max_w in (120, 200, 320):
            with self.subTest(max_w=max_w):
                self.assertEqual("".join(ppt_svg.wrap(s, max_w, 24)), s)

    def test_wrap_keeps_going_instead_of_one_giant_line(self):
        """回归 2026-09-27：超长文字**必须继续换行**，不能并成一个巨宽的行。

        并行的后果：SVG 里出现 6456px 宽的文字（横向溢出 404%）→ 转换器拒绝 → 导出静默回退旧版式。
        """
        s = "字" * 400
        lines = ppt_svg.wrap(s, 1096, 24)
        self.assertGreaterEqual(len(lines), 8, f"400 字至少该换 8 行，实际 {len(lines)}")
        self.assertEqual("".join(lines), s)
        for ln in lines:
            with self.subTest(line=ln[:8]):
                self.assertLessEqual(sum(ppt_svg.char_w(c, 24) for c in ln), 1096, "每行都不能超过版心宽")

    def test_long_text_page_stays_inside_canvas(self):
        """整页渲染后，任何文字元素都不该越出画布（转换器的硬要求）。"""
        from edu_agent import slide_audit

        out = ppt_svg.render_deck(tk.load("tech-editorial"),
                                 [{"layout": "bullets", "title": "长文本", "bullets": ["字" * 400]}])
        boxes = slide_audit.boxes_from_svg(out[0])
        for b in boxes:
            with self.subTest(text=b["text"][:8]):
                self.assertLessEqual(b["x"] + b["w"], 1280.5, f"文字越出右边界：{b['text'][:10]}")

    def test_density_warns_and_caps_blocks(self):
        data = tk.load("tech-editorial")
        warn: list[str] = []
        svg = ppt_svg.render_slide(data, "bullets",
                                   {"title": "t", "bullets": [f"第 {i} 条" for i in range(7)]},
                                   warnings=warn)
        self.assertEqual(len(warn), 1)
        self.assertIn("拆页", warn[0])
        self.assertIn("第 4 条", svg)
        self.assertNotIn("第 5 条", svg)

    def test_unknown_layout_raises(self):
        with self.assertRaises(ValueError):
            _slide("tech-editorial", "nope", title="x")


if __name__ == "__main__":
    unittest.main()
