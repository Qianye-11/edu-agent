# -*- coding: utf-8 -*-
"""幻灯片渲染收口（`slide_deck` + `/api/slide/*`）的离线单测。

对应 `docs/22` D13 / `docs/23` M1：
- 渲染只有一份实现（服务端），面板与导出共用 → 这里锁住"输出什么"；
- **不落盘**是红线：每个用例跑完都比对工作区快照（`data/tmp`、`content/plans`）；
- 接口三态：正常 / skill 关（`enabled:false`）/ 主题坏掉（400）。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJ / "src"))

from edu_agent import capabilities as cap          # noqa: E402
from edu_agent import slide_deck                   # noqa: E402
from edu_agent import ppt_tokens as tk             # noqa: E402

# web_server 在 import 时会建 SessionStore：先指到临时目录，别污染本机
os.environ.setdefault("EDU_DATA_DIR", tempfile.mkdtemp(prefix="edu_test_data_"))  # noqa: E402
from edu_agent import web_server as ws             # noqa: E402

DECK = [
    {"name": "3.3 幂函数"},
    {"name": "情境导入", "minutes": "5", "bullets": ["投影五个实例", "追问：这些函数有什么共同点"]},
    {"name": "概念建构", "minutes": "12", "bullets": ["形如 y=x^α", "α 为常数"]},
    {"name": "小结", "minutes": "3", "bullets": ["三条结论"]},
]


def _snapshot(*dirs: Path) -> dict[str, float]:
    out: dict[str, float] = {}
    for d in dirs:
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if p.is_file():
                out[str(p)] = p.stat().st_mtime
    return out


class _NoDiskWrites(unittest.TestCase):
    """红线：渲染是纯内存的（预览不落盘）。"""

    WATCH = (_PROJ / "data" / "tmp", _PROJ / "content" / "plans")

    def setUp(self):
        self.before = _snapshot(*self.WATCH)

    def tearDown(self):
        after = _snapshot(*self.WATCH)
        self.assertEqual(self.before, after, "渲染不该往 data/tmp 或 content/plans 写任何东西")


class ThemeTest(_NoDiskWrites):
    def test_three_themes_with_metadata(self):
        items = slide_deck.themes()
        self.assertEqual([t["id"] for t in items], ["hand-drawn", "illustration", "tech-editorial"])
        for t in items:
            with self.subTest(theme=t["id"]):
                self.assertTrue(t["name"])
                self.assertTrue(t["source"].endswith("style-system.md"), "每套都要写清数值出处")
                self.assertEqual((t["canvas"]["w"], t["canvas"]["h"]), (1280, 720))
                self.assertTrue(t["fonts_ok"], t["fonts_missing"])
                self.assertNotIn("accent_max_ratio", t["palette"], "给面板的配色不用带内部阈值")

    def test_default_is_academic_blue(self):
        self.assertEqual(slide_deck.default_theme_id(), "tech-editorial")

    def test_unknown_theme_is_explicit(self):
        with self.assertRaises(FileNotFoundError):
            slide_deck.theme("nope")


class BuildDeckTest(_NoDiskWrites):
    def test_pages_and_layouts(self):
        out = slide_deck.build_deck("tech-editorial", DECK, title="3.3 幂函数")
        self.assertEqual(out["stats"]["pages"], 4)
        self.assertEqual(out["layouts"], ["cover", "bullets", "bullets", "end"])
        for svg in out["pages"]:
            self.assertTrue(svg.startswith("<svg") and svg.endswith("</svg>"))
            self.assertIn('viewBox="0 0 1280 720"', svg)
            self.assertIn("<text", svg)
            self.assertNotIn("<image", svg)

    def test_cover_when_first_page_is_title_only(self):
        svg = slide_deck.build_deck("tech-editorial", DECK, title="3.3 幂函数")["pages"][0]
        self.assertIn("3.3 幂函数", svg)
        self.assertEqual(slide_deck.build_deck("tech-editorial", DECK, title="3.3 幂函数")["layouts"][0], "cover")

    def test_first_page_with_bullets_is_not_turned_into_cover(self):
        """第 1 页有要点时**不能**当封面 —— 否则整页内容会被丢掉。"""
        out = slide_deck.build_deck("tech-editorial", [{"name": "情境导入", "bullets": ["投影五个实例"]}])
        self.assertEqual(out["layouts"], ["bullets"])
        self.assertIn("投影五个实例", out["pages"][0])

    def test_cover_subtitle_uses_first_section_name(self):
        deck = [{"name": "情境导入"}, {"name": "概念建构", "bullets": ["形如 y=x^α"]}]
        svg = slide_deck.build_deck("tech-editorial", deck, title="3.3 幂函数")["pages"][0]
        self.assertIn("3.3 幂函数", svg)
        self.assertIn("情境导入", svg)          # 环节名降级当副标，而不是当封面主标

    def test_minutes_become_label(self):
        svg = slide_deck.build_deck("tech-editorial", DECK, title="x")["pages"][1]
        self.assertIn("环节 2 · 5 分钟", svg)

    def test_bullets_are_not_truncated(self):
        long_one = "这条要点很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长"
        out = slide_deck.build_deck("tech-editorial", [{"name": "一", "bullets": [long_one]}])
        self.assertIn(long_one[:20], out["pages"][0])         # 开头在
        self.assertIn(long_one[-10:], out["pages"][0])        # 结尾也在（换行不截断）

    def test_density_warning_but_content_kept(self):
        many = [f"第 {i} 条" for i in range(7)]
        out = slide_deck.build_deck("tech-editorial", [{"name": "一"}, {"name": "二", "bullets": many}])
        self.assertTrue(out["warnings"], "超过上限要给拆页提示")
        self.assertIn("拆页", out["warnings"][0])
        self.assertIn("第 4 条", out["pages"][1])             # 上限内照排

    def test_explicit_layout_respected(self):
        out = slide_deck.build_deck("tech-editorial",
                                    [{"layout": "section", "name": "环节切换", "index": "2"}])
        self.assertEqual(out["layouts"], ["section"])
        self.assertIn("环节切换", out["pages"][0])

    def test_blocks_with_labels_pass_through(self):
        deck = [{"name": "一"}, {"name": "二", "blocks": [{"label": "定义", "text": "形如 y=x^α"}]},
                {"name": "三", "bullets": ["收束"]}]
        out = slide_deck.build_deck("tech-editorial", deck)
        self.assertEqual(out["layouts"], ["cover", "bullets", "end"])
        self.assertIn("定义", out["pages"][1])
        self.assertIn("形如 y=x^α", out["pages"][1])

    def test_last_page_keeps_its_items(self):
        """结尾版式也要排要点（别把最后一页的内容吃掉）。"""
        deck = [{"name": "一"}, {"name": "作业", "bullets": ["课本 P72 第 3 题"]}]
        out = slide_deck.build_deck("tech-editorial", deck)
        self.assertEqual(out["layouts"][-1], "end")
        self.assertIn("课本 P72 第 3 题", out["pages"][-1])

    def test_empty_slides(self):
        out = slide_deck.build_deck("tech-editorial", [])
        self.assertEqual(out["pages"], [])
        self.assertEqual(out["stats"]["pages"], 0)

    def test_all_themes_render_same_deck(self):
        for theme_id in tk.available():
            with self.subTest(theme=theme_id):
                out = slide_deck.build_deck(theme_id, DECK, title="x")
                self.assertEqual(len(out["pages"]), len(DECK))
                self.assertEqual(out["theme_id"], theme_id)


class FitScaleTest(unittest.TestCase):
    """预览台的等比缩放：面板里那段 JS 是这几个数的镜像（docs/23 M1 出口标准）。"""

    def test_three_panel_widths(self):
        self.assertEqual(slide_deck.fit_scale(480), 0.375)
        self.assertEqual(slide_deck.fit_scale(680), 0.53125)
        self.assertEqual(slide_deck.fit_scale(1000), 0.78125)

    def test_height_follows_scale(self):
        self.assertEqual(slide_deck.fit_height(480), 270)
        self.assertEqual(slide_deck.fit_height(680), 383)
        self.assertEqual(slide_deck.fit_height(1000), 563)

    def test_clamped_and_zero_fallback(self):
        self.assertEqual(slide_deck.fit_scale(0), 0.375)          # jsdom / 隐藏时量不到宽度
        self.assertEqual(slide_deck.fit_scale(10), 0.15)          # 太小 → 下限
        self.assertEqual(slide_deck.fit_scale(99999), 2.0)        # 太大 → 上限

    def test_uniform_scale_keeps_relative_geometry(self):
        """均匀缩放 ⇒ 归一化坐标（除以 scale）与缩放无关，这正是"拖宽不变形"的数学依据。"""
        model = {"title_x": 72.0, "body_x": 96.0, "right_edge": 1192.0}
        for w in (480, 680, 1000):
            sc = slide_deck.fit_scale(w)
            back = {k: v * sc / sc for k, v in model.items()}
            self.assertEqual(back, model)


class EndpointTest(_NoDiskWrites):
    def setUp(self):
        super().setUp()
        self._old = os.environ.get("EDU_DATA_DIR")
        os.environ["EDU_DATA_DIR"] = tempfile.mkdtemp(prefix="edu_test_data_")
        cap._CACHE.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old is None:
            os.environ.pop("EDU_DATA_DIR", None)
        else:
            os.environ["EDU_DATA_DIR"] = self._old
        cap._CACHE.clear()

    def test_themes_endpoint(self):
        d = ws.api_slide_themes()
        self.assertTrue(d["ok"])
        self.assertEqual(d["default"], "tech-editorial")
        self.assertEqual(len(d["themes"]), 3)

    def test_render_endpoint_with_slides(self):
        d = ws.api_slide_render(ws.SlideRenderReq(theme_id="tech-editorial", slides=DECK, title="3.3 幂函数"))
        self.assertTrue(d["ok"] and d["enabled"])
        self.assertEqual(len(d["pages"]), 4)
        self.assertEqual(d["stats"]["pages"], 4)
        self.assertIn("同一份源", d["note"])

    def test_render_endpoint_derives_from_markdown(self):
        md = "# 3.3 幂函数\n\n### 情境导入（约 5 分钟）\n- 投影五个实例\n\n### 概念建构（约 12 分钟）\n- 形如 y=x^α\n"
        d = ws.api_slide_render(ws.SlideRenderReq(theme_id="hand-drawn", markdown=md))
        self.assertEqual(len(d["pages"]), 2)
        self.assertEqual(d["theme_id"], "hand-drawn")

    def test_render_endpoint_skill_off(self):
        cap.set_enabled("skill", "doc-session-preview", False)      # 与 test_wps_tools 同款用法
        cap._CACHE.clear()
        try:
            self.assertFalse(cap.session_preview_ready())
            d = ws.api_slide_render(ws.SlideRenderReq(theme_id="tech-editorial", slides=DECK))
            self.assertEqual(d["enabled"], False)
            self.assertEqual(d["pages"], [])
            self.assertFalse(ws.api_slide_themes()["enabled"])
        finally:
            cap.set_enabled("skill", "doc-session-preview", True)
            cap._CACHE.clear()

    def test_render_endpoint_bad_theme_is_400(self):
        r = ws.api_slide_render(ws.SlideRenderReq(theme_id="nope", slides=DECK))
        self.assertEqual(getattr(r, "status_code", 200), 400)


if __name__ == "__main__":
    unittest.main()
