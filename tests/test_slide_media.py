# -*- coding: utf-8 -*-
"""导出 PNG / JPEG / PDF（R23）：同一份 SVG 出图，页数与尺寸都要对得上。

要钉住的三条：

1. **同源**：图/PDF 用的是 `slide_deck.build_deck` 那份 SVG（预览与 pptx 也是它）；
2. **尺寸与页数**：每页 1280×720、页数与幻灯片一致、**不是空白**（墨迹占比 > 0）；
3. **缺浏览器就如实说**：这台机器没有 Edge/Chromium 时，导出要**明确报**而不是出半张图。
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJ / "src"))

from edu_agent import slide_deck as sd          # noqa: E402
from edu_agent import slide_media as sm         # noqa: E402

THEME = "tech-editorial"
DECK = [{"layout": "cover", "title": "3.3 幂函数", "subtitle": "数学"},
        {"layout": "bullets", "name": "导入", "blocks": [{"label": "环节 1", "text": "投影五个实例"},
                                                          {"label": "", "text": "追问：这些函数有什么共同点"}]},
        {"layout": "end", "title": "小结", "closing": "三条结论"}]
HAS_BROWSER = bool(sm.edge_exe())


class _Case(unittest.TestCase):
    def setUp(self):
        self.data = Path(tempfile.mkdtemp(prefix="edu_media_"))
        self._old = os.environ.get("EDU_DATA_DIR")
        os.environ["EDU_DATA_DIR"] = str(self.data)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old is None:
            os.environ.pop("EDU_DATA_DIR", None)
        else:
            os.environ["EDU_DATA_DIR"] = self._old

    def _pages(self):
        return sd.build_deck(THEME, DECK, "3.3 幂函数")["pages"]


class HtmlWrapTest(unittest.TestCase):
    def test_page_html_contains_only_the_svg(self):
        html = sm.page_html('<svg xmlns="http://www.w3.org/2000/svg"></svg>')
        self.assertIn("<svg", html)
        self.assertNotIn("<script", html, "页面里不该有脚本（截图要干净）")
        self.assertIn("margin:0", html, "去掉默认边距，尺寸才准")


@unittest.skipUnless(HAS_BROWSER, "这台机器没有 Edge/Chromium：跳过真实截图")
class RenderPngTest(_Case):
    def test_pngs_match_page_count_and_size(self):
        pages = self._pages()
        out = self.data / "png"
        got = sm.render_png(pages, out)
        self.assertTrue(got.get("ok"), got.get("error"))
        self.assertEqual(len(got["files"]), len(pages))
        for f in got["files"]:
            st = sm.png_stats(f)
            self.assertEqual((st["width"], st["height"]), (1280, 720), f)
            self.assertGreater(st["ink"], 0.002, f"{Path(f).name} 几乎是空白（渲染失败了？）")
            self.assertLess(st["ink"], 0.9, f"{Path(f).name} 墨迹占比过高（糊成一片？）")

    def test_jpegs_are_valid_and_reasonably_sized(self):
        """JPEG 的验收：尺寸对、体积在合理范围。

        **不**断言"JPEG 一定比 PNG 小"：纯文字白底页 PNG 反而更小（实测 15.7KB vs 18KB），
        JPEG 的优势在有照片/渐变时 —— 那种绝对化的断言只会变成假失败（踩过）。
        """
        pages = self._pages()[:1]
        out = self.data / "jpg"
        pngs = sm.render_png(pages, out / "_png")["files"]
        made = sm.make_jpegs(pngs, out)
        self.assertTrue(made.get("ok"), made.get("error"))
        self.assertEqual(len(made["files"]), 1)
        st = sm.png_stats(made["files"][0])
        self.assertEqual((st["width"], st["height"]), (1280, 720))
        self.assertGreater(st["ink"], 0.002, "转出来的 JPEG 是空白")
        self.assertLess(Path(made["files"][0]).stat().st_size, 600 * 1024, "JPEG 体积不该失控")

    def test_pdf_has_one_page_per_slide(self):
        pages = self._pages()
        pngs = sm.render_png(pages, self.data / "p")["files"]
        pdf = self.data / "deck.pdf"
        made = sm.make_pdf(pngs, pdf)
        self.assertTrue(made.get("ok"), made.get("error"))
        self.assertEqual(made["pages"], len(pages))
        try:
            import pymupdf

            doc = pymupdf.open(str(pdf))
            self.assertEqual(doc.page_count, len(pages), "PDF 页数要与幻灯片一致")
            rect = doc[0].rect
            self.assertAlmostEqual(rect.width / rect.height, 1280 / 720, places=2)
        except ImportError:                       # 没装 PyMuPDF 就只验文件在
            self.assertGreater(pdf.stat().st_size, 1000)

    def test_scale_option(self):
        got = sm.render_png(self._pages()[:1], self.data / "big", scale=2.0)
        self.assertTrue(got.get("ok"), got.get("error"))
        st = sm.png_stats(got["files"][0])
        self.assertEqual((st["width"], st["height"]), (2560, 1440))


class MissingBrowserTest(_Case):
    def test_says_it_clearly_when_no_browser(self):
        """把候选路径换成不存在的 → 必须明确报错，而不是抛异常或出半张图。"""
        old = sm.EDGE_CANDIDATES
        sm.EDGE_CANDIDATES = ("C:\\nope\\msedge.exe",)
        old_which = sm.shutil.which
        sm.shutil.which = lambda name: None
        try:
            got = sm.render_png(self._pages()[:1], self.data / "x")
            self.assertFalse(got["ok"])
            self.assertIn("Edge", got["error"])
        finally:
            sm.EDGE_CANDIDATES = old
            sm.shutil.which = old_which


class EndpointTest(_Case):
    def test_endpoint_kinds(self):
        from edu_agent import web_server as ws

        sid = "media-1"
        ws._store.set_session_doc(sid, {"slides": DECK, "title": "3.3 幂函数"})
        if not HAS_BROWSER:
            got = ws._export_media({"kind": "png", "session_id": sid, "theme_id": THEME})
            self.assertFalse(got.get("ok"))
            self.assertIn("Edge", str(got.get("error")))
            return
        for kind, want in (("png", ".png"), ("jpeg", ".jpg"), ("pdf", ".pdf")):
            with self.subTest(kind=kind):
                got = ws._export_media({"kind": kind, "session_id": sid, "theme_id": THEME,
                                        "title": "3.3 幂函数"})
                self.assertTrue(got.get("ok"), got.get("error"))
                self.assertEqual(got["pages"], len(DECK))
                self.assertTrue(all(c["ok"] for c in got["checks"]), got["checks"])
                self.assertTrue(Path(got["outDir"]).is_dir())
                if kind == "pdf":
                    self.assertEqual(len(got["files"]), 1)
                    self.assertTrue(got["files"][0].endswith(want))
                else:
                    self.assertEqual(len(got["files"]), len(DECK))
                    self.assertTrue(all(f.endswith(want) for f in got["files"]))

    def test_endpoint_needs_slides(self):
        from edu_agent import web_server as ws

        got = ws._export_media({"kind": "png", "session_id": "nobody", "slides": []})
        self.assertEqual(getattr(got, "status_code", 200), 400)


class DeliveryCheckTest(_Case):
    """交付体检（R23）：只读、不阻断；工具不在或文件不在时要如实说。"""

    def test_missing_file_and_tool_are_reported(self):
        from edu_agent import web_server as ws

        self.assertFalse(ws._delivery_check("")["ok"])
        self.assertFalse(ws._delivery_check(str(self.data / "nope.pptx"))["ok"])

    def test_runs_on_a_real_pptx(self):
        try:
            from edu_agent import ppt_polish
            ready = bool(ppt_polish.status().get("intake_ready"))
        except Exception:                                     # noqa: BLE001
            ready = False
        if not ready:
            self.skipTest("没装 ppt-master")
        from edu_agent import web_server as ws

        out = sd.export_deck_pptx(THEME, DECK, "交付体检验收")
        if not out.get("ok"):
            self.skipTest("导出没通过（本机环境问题）：" + str(out.get("error"))[:60])
        got = ws._delivery_check(out["file"])
        self.assertTrue(got.get("ok"), got.get("error"))
        self.assertIsInstance(got.get("risk_count"), int)
        self.assertIn("summary", got)


if __name__ == "__main__":
    unittest.main()
