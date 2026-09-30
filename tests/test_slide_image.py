# -*- coding: utf-8 -*-
"""联网取图（R18 前置）单测：**安全边界**与"预览不落盘"是这轮的重点。

老师要的是"启用联网功能，因为计划与 agent 相连" —— 同事的 agent 会给我们一个图片 URL。
所以这里要钉死：
1. 只认公网 http/https（**内网/本机一律拒**，SSRF 防护有负样本）；
2. 只落 `data/uploads/`（`content/plans/` 一个字不写）；
3. 类型白名单 + 单张体积上限；
4. 有图时 `media` 版式出 `<image href="data:...">`；**没图时逐字节不变**；
5. 缺 key/图源连不上时如实报（不假装搜到图）。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJ / "src"))

from edu_agent import slide_deck                          # noqa: E402
from edu_agent import slide_image as si                   # noqa: E402

PNG = (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 32)


def _fake_opener(url: str, max_bytes: int, timeout: float):
    """假下载：返回 (bytes, content-type, 最终 URL)，不发真网络请求。"""
    if "big" in url:
        return b"x" * (max_bytes + 1), "image/png", url
    if "text" in url:
        return b"hello", "text/plain", url
    return PNG, "image/png", url


class _Case(unittest.TestCase):
    """数据目录用 `mkdtemp` 且**不删**：会话库的 sqlite 连接还开着，删目录会 WinError 32
    （`test_slide_boxes` 里踩过同一个坑；这里与它保持一致）。"""

    def setUp(self):
        self.data = Path(tempfile.mkdtemp(prefix="edu_img_data_"))
        self._old = os.environ.get("EDU_DATA_DIR")
        os.environ["EDU_DATA_DIR"] = str(self.data)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old is None:
            os.environ.pop("EDU_DATA_DIR", None)
        else:
            os.environ["EDU_DATA_DIR"] = self._old


class GuardTest(_Case):
    def test_rejects_private_and_local_hosts(self):
        for bad in ("http://127.0.0.1:5174/x.png", "http://localhost/a.png",
                    "http://10.0.0.5/a.png", "http://192.168.1.9/a.png",
                    "file:///C:/x.png", "ftp://example.com/a.png", ""):
            with self.subTest(url=bad):
                got = si.check_url(bad)
                self.assertFalse(got["ok"], f"{bad} 不该放行")
                self.assertTrue(got.get("error"), got)

    def test_rejects_non_image_and_oversize(self):
        big = si.fetch_url("https://example.com/big.png", "s1", opener=_fake_opener)
        self.assertFalse(big["ok"])
        self.assertIn("太大", big["error"])
        txt = si.fetch_url("https://example.com/text.png", "s1", opener=_fake_opener)
        self.assertFalse(txt["ok"])
        self.assertIn("不是图片", txt["error"])

    def test_writes_only_uploads(self):
        got = si.fetch_url("https://example.com/photo.png", "sess-1", opener=_fake_opener)
        self.assertTrue(got["ok"], got)
        self.assertTrue(got["path"].startswith("sess-1/"), got["path"])
        self.assertTrue(Path(got["file"]).is_file())
        self.assertTrue(str(Path(got["file"])).startswith(str(self.data / "uploads")))
        plans = _PROJ / "content" / "plans"
        before = {p.name for p in plans.glob("*")} if plans.is_dir() else set()
        again = si.fetch_url("https://example.com/photo.png", "sess-1", opener=_fake_opener)
        self.assertTrue(again["ok"])
        self.assertEqual(before, {p.name for p in plans.glob("*")} if plans.is_dir() else set(),
                         "content/plans 一个字都不能多")

    def test_data_uri_is_readonly_and_bounded(self):
        got = si.fetch_url("https://example.com/photo.png", "s2", opener=_fake_opener)
        uri = si.data_uri(got["path"])
        self.assertTrue(uri.startswith("data:image/png;base64,"))
        self.assertEqual(si.data_uri("../etc/passwd"), "", "越界路径要拒")
        self.assertEqual(si.data_uri("nope/none.png"), "", "文件不在就是空（不抛）")


class ProviderStatusTest(_Case):
    def test_status_is_honest_when_no_key(self):
        old = {k: os.environ.pop(k, None) for k in ("PEXELS_API_KEY", "PIXABAY_API_KEY")}
        try:
            st = si.provider_status()
            self.assertFalse(st["usable"], st)
            self.assertIn("PEXELS_API_KEY", st["hint"])
            self.assertIn("让同事的 agent 直接把图 URL 给你", st["hint"])
        finally:
            for k, v in old.items():
                if v is not None:
                    os.environ[k] = v

    def test_search_reports_no_provider(self):
        old = {k: os.environ.pop(k, None) for k in ("PEXELS_API_KEY", "PIXABAY_API_KEY")}
        try:
            got = si.search("幂函数")
            self.assertFalse(got["ok"])
            self.assertIn("一个图源都用不了", got["error"])
        finally:
            for k, v in old.items():
                if v is not None:
                    os.environ[k] = v


class RenderTest(_Case):
    DECK = [{"layout": "media", "name": "图象", "caption": "图 1 幂函数图象",
             "bullets": ["看第一象限"], "image": None}]

    def test_no_image_is_byte_identical(self):
        base = slide_deck.build_deck("tech-editorial", [dict(self.DECK[0])], "x")["pages"][0]
        self.assertNotIn("<image", base)
        self.assertIn("<polyline", base, "没图时仍走纯矢量示意（老样子）")

    def test_image_lands_in_svg_and_cleans_up(self):
        got = si.fetch_url("https://example.com/figure.png", "s3", opener=_fake_opener)
        deck = [dict(self.DECK[0], image={"src": got["path"], "credit": "来源：example.com"})]
        svg = slide_deck.build_deck("tech-editorial", deck, "x")["pages"][0]
        self.assertIn('<image ', svg)
        self.assertIn('href="data:image/png;base64,', svg)
        self.assertNotIn("xlink:", svg, "别引 xlink 命名空间（没图的页要保持逐字节一致）")
        self.assertIn("来源：example.com", svg, "出处要画在图上")
        self.assertNotIn("<polyline", svg.split("</text>")[-1] or "", "有图就不再画示意曲线")

    def test_clean_slides_accepts_and_sanitizes_image(self):
        ok = slide_deck.clean_slides([dict(self.DECK[0], image={"src": "s1/a.png", "alt": "图",
                                                                "credit": "来源：x"})])[0]
        self.assertEqual(ok["image"]["src"], "s1/a.png")
        self.assertEqual(ok["image"]["credit"], "来源：x")
        for bad in ({"src": "../secret.png"}, {"src": "/etc/passwd"}, {"src": ""},
                    {"src": "a.png", "credit": "x" * 200}):
            with self.subTest(bad=bad):
                got = slide_deck.clean_slides([dict(self.DECK[0], image=bad)])[0]
                if bad.get("src") in ("../secret.png", "/etc/passwd", ""):
                    self.assertNotIn("image", got, f"{bad} 该被丢掉")
        long_credit = slide_deck.clean_slides([dict(self.DECK[0],
                                                    image={"src": "a.png", "credit": "字" * 200})])[0]
        self.assertEqual(len(long_credit["image"]["credit"]), 40, "出处限长")


class EndpointTest(_Case):
    def test_attach_requires_skill_and_confirm(self):
        from edu_agent import web_server as ws

        sid = "img-ep-1"
        ws._store.set_session_doc(sid, {"slides": [dict(RenderTest.DECK[0])], "title": "x"})
        req = ws.SlideImageReq(session_id=sid, index=0, url="https://example.com/f.png",
                               confirm=True)
        old_fetch = si.fetch_url
        si.fetch_url = lambda url, s, **kw: {"ok": True, "path": "s/a.png", "file": "x",
                                             "bytes": 1234, "sha256": "abc"}
        try:
            got = ws.api_slide_image_attach(req)
            self.assertTrue(got["ok"], got)
            self.assertEqual(got["slides"][0]["image"]["src"], "s/a.png")
            self.assertEqual(ws._store.get_session_doc(sid)["slides"][0]["image"]["src"], "s/a.png")
            off = ws.api_slide_image_attach(ws.SlideImageReq(session_id=sid, index=0))
            self.assertEqual(getattr(off, "status_code", 200), 400, "没给 url 要明确报错")
            det = ws.api_slide_image_attach(ws.SlideImageReq(session_id=sid, index=0, action="detach"))
            self.assertTrue(det["ok"])
            self.assertNotIn("image", det["slides"][0])
        finally:
            si.fetch_url = old_fetch


if __name__ == "__main__":
    unittest.main()
