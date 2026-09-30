# -*- coding: utf-8 -*-
"""课件生成（对话链路）：意图判定 / 内容→版式映射 / 接口两道闸门。

**全离线**：不调 LLM、不调检索、不启 WSL 引擎（引擎真链路用例在 test_ppt_master.py，
由 EDU_TEST_PPT=1 门控）。接口用例用 mock 顶掉 courseware.generate，只验接线。
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJ / "src"))
os.environ.setdefault("EDU_DATA_DIR", tempfile.mkdtemp(prefix="edu_test_data_"))

from edu_agent import capabilities, courseware, permissions   # noqa: E402
from edu_agent import web_server as ws                        # noqa: E402

PLAN_MD = """# 3.3 幂函数

## 教学目标
- 理解幂函数的定义
- 会画五个常见幂函数的图象

### 导入（5 分钟）
- 回顾函数的概念
- 提出问题：y=x²、y=x³ 这类函数叫什么

### 建构（20 分钟）
- 定义：形如 y=x^α 的函数叫幂函数
- 五个常见幂函数：y=x、y=x²、y=x³、y=x^½、y=x^-1
- 观察图象的公共点 (1,1)

### 小结（5 分钟）
- 定义与图象特征
"""

LONG_SLIDE_MD = """# 单调性复习

### 练习（20 分钟）
- 要点一
- 要点二
- 要点三
- 要点四
- 要点五
- 要点六
- 要点七
- 要点八
"""


class TestIntent(unittest.TestCase):
    """只认显式指令：动作词 + 课件/PPT 相邻；疑问句不算。"""

    def test_positive(self):
        for q in ("生成课件", "帮我做一份课件", "把这个教案做成PPT", "导出幻灯片",
                  "帮我备一节 3.3 幂函数的课，要课件", "来一份演示文稿",
                  "我要做课件", "想要一份课件", "能生成课件吗"):
            self.assertTrue(courseware.wants_deck(q), q)

    def test_negative(self):
        for q in ("这节教案怎么写", "怎么做PPT", "如何生成课件", "什么是幻灯片",
                  "PPT 里应该放什么", "函数的单调性怎么判断", "为什么课件要分层",
                  "帮我备一节 3.3 幂函数的课", "要课件吗", "需要课件吗"):
            self.assertFalse(courseware.wants_deck(q), q)

    def test_web_server_flag_wrapper(self):
        """web_server 的包装（吞异常）与判定保持一致。"""
        self.assertTrue(ws._deck_flag("生成课件"))
        self.assertFalse(ws._deck_flag("什么是幂函数"))
        self.assertFalse(ws._deck_flag(None))


class TestPages(unittest.TestCase):
    def test_from_markdown_structure(self):
        pages = courseware.pages_from_markdown(PLAN_MD)
        self.assertEqual(pages[0]["layout"], "cover")
        self.assertEqual(pages[0]["title"], "3.3 幂函数")
        # 有 ### 环节时，一环节一页（与「预览 PPT / 导出 PPT · 本地」同一解析口径）
        titles = [p["title"] for p in pages[1:]]
        self.assertTrue(any("导入" in t for t in titles), titles)
        self.assertTrue(any("建构" in t for t in titles), titles)
        self.assertTrue(all(p["layout"] in ("bullets", "section") for p in pages[1:]))
        # 时长进页标题，不再当要点
        self.assertTrue(any("5 分钟" in t for t in titles), titles)
        for p in pages[1:]:
            if p["layout"] == "bullets":
                self.assertLessEqual(len(p["items"]), courseware.MAX_BULLETS_PER_PAGE)

    def test_from_markdown_chunks_long_slide(self):
        pages = courseware.pages_from_markdown(LONG_SLIDE_MD)
        body = [p for p in pages[1:] if p["layout"] == "bullets"]
        self.assertEqual(len(body), 2, "8 条要点应拆成 2 页（6 + 2）")
        self.assertEqual(len(body[0]["items"]), 6)
        self.assertEqual(len(body[1]["items"]), 2)
        self.assertIn("续", body[1]["title"])

    def test_from_markdown_empty_section_becomes_section_page(self):
        md = "# 课题\n\n### 空环节（5 分钟）\n"
        pages = courseware.pages_from_markdown(md)
        self.assertEqual(pages[1]["layout"], "section")

    def test_from_payload_cards(self):
        payload = {"title": "函数的奇偶性", "grade": "高一",
                   "steps": [{"name": "导入", "minutes": "5",
                              "points": ["观察图象", "提出问题", "给出定义"]},
                             {"name": "练习", "minutes": "10",
                              "points": ["判断奇偶性：f(x)=x²"]}]}
        pages = courseware.pages_from_payload(payload)
        self.assertEqual(pages[0]["layout"], "cover")
        self.assertIn("高一", pages[0]["subtitle"])
        cards = [p for p in pages[1:] if p["layout"] == "cards"]
        self.assertEqual(len(cards), 2)
        self.assertEqual(len(cards[0]["items"]), 3)
        self.assertLessEqual(len(cards[0]["items"]), courseware.MAX_CARDS_PER_PAGE)
        self.assertTrue(all("heading" in it for it in cards[1]["items"]))

    def test_pages_for_prefers_payload(self):
        payload = {"title": "P", "steps": [{"name": "环节", "points": ["a"]}]}
        pages = courseware.pages_for(markdown=PLAN_MD, payload=payload)
        self.assertEqual(pages[0]["title"], "P")

    def test_too_little_content_fails_fast(self):
        """内容不足时**不启引擎**就明确失败（免得白等 20 秒 WSL 冷启动）。"""
        with mock.patch.object(courseware.ppt_master, "generate") as gen:
            r = courseware.generate(markdown="")
        self.assertFalse(r["ok"])
        self.assertIn("内容太少", r["error"])
        gen.assert_not_called()

    def test_long_point_splits_into_heading_and_body(self):
        point = "解题步骤：先用定义判断 f(-x) 与 f(x) 的关系，再下结论"
        card = courseware._card(point)
        self.assertTrue(card["heading"])
        self.assertTrue(card["body"])


class TestGates(unittest.TestCase):
    """接口两道闸门 + 接线（mock 掉引擎，不启 WSL）。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="edu_deck_")
        self._old = os.environ.get("EDU_DATA_DIR")
        os.environ["EDU_DATA_DIR"] = self._tmp
        capabilities._CACHE.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old is not None:
            os.environ["EDU_DATA_DIR"] = self._old
        capabilities._CACHE.clear()

    def _req(self, **kw):
        base = dict(markdown=PLAN_MD, confirm=False)
        base.update(kw)
        return ws.CoursewareReq(**base)

    def test_capability_gate(self):
        capabilities.set_enabled("skill", "ppt-master", False)
        r = ws.api_ppt_courseware(self._req())
        self.assertFalse(r["ok"])
        self.assertEqual(r["gate"], "capability")

    def test_permission_ask_gate(self):
        capabilities.set_enabled("skill", "ppt-master", True)
        permissions.reset()                                   # 回默认 ask
        r = ws.api_ppt_courseware(self._req())
        self.assertFalse(r["ok"])
        self.assertEqual(r["gate"], "permission")
        self.assertEqual(r["need"], "confirm")

    def test_permission_deny_gate(self):
        capabilities.set_enabled("skill", "ppt-master", True)
        permissions.set_policy("export_pptx", "deny")
        r = ws.api_ppt_courseware(self._req())
        self.assertFalse(r["ok"])
        self.assertEqual(r["policy"], "deny")
        permissions.reset()

    def test_allow_path_wires_download_token(self):
        capabilities.set_enabled("skill", "ppt-master", True)
        permissions.set_policy("export_pptx", "allow")
        fake = {"ok": True, "pptx": str(Path(self._tmp) / "课件.pptx"), "page_count": 4}
        with mock.patch.object(courseware, "generate", return_value=dict(fake)) as gen:
            with mock.patch.object(ws, "register_download", return_value="TOK") as reg:
                r = ws.api_ppt_courseware(self._req())
        self.assertTrue(r["ok"])
        self.assertEqual(r["download_url"], "/api/wps/download?id=TOK")
        reg.assert_called_once()
        gen.assert_called_once()
        permissions.reset()

    def test_confirm_bypasses_ask(self):
        capabilities.set_enabled("skill", "ppt-master", True)
        permissions.reset()
        with mock.patch.object(courseware, "generate",
                               return_value={"ok": True, "pptx": "x.pptx"}):
            with mock.patch.object(ws, "register_download", return_value="TOK2"):
                r = ws.api_ppt_courseware(self._req(confirm=True))
        self.assertTrue(r["ok"])
        self.assertEqual(r["download_url"], "/api/wps/download?id=TOK2")
        permissions.reset()


if __name__ == "__main__":
    unittest.main()
