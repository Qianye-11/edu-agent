# -*- coding: utf-8 -*-
"""查找替换（R18）：**跨页找 + 跨页替换**，且与页内编辑走同一条写路径（`apply_slot`）。

为什么要单测钉住：查找替换最容易"看起来能用、其实漏字段"（只改 `blocks` 不改 `bullets`、
只改正文不改备注）。这里把"有哪些可编辑文字"的清单先钉死，再验找/替换的行为。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJ / "src"))

from edu_agent import slide_deck as sd                    # noqa: E402

DECK = [
    {"layout": "cover", "name": "3.3 幂函数", "cover_title": "3.3 幂函数", "subtitle": "数学"},
    {"layout": "bullets", "name": "幂函数导入", "label": "环节一",
     "blocks": [{"label": "要点", "text": "投影五个实例"}, {"label": "", "text": "追问共同点"}],
     "notes": "先问幂函数是什么"},
    {"layout": "two-col", "name": "对比", "left": {"heading": "相同点", "bullets": ["都过(1,1)"]},
     "right": {"heading": "不同点", "bullets": ["单调性不同"]}},
]


class SlotListTest(unittest.TestCase):
    def test_iter_covers_every_editable_text(self):
        got = dict(sd.iter_text_slots(DECK[1]))
        self.assertEqual(got["title"], "幂函数导入")
        self.assertEqual(got["label"], "环节一")
        self.assertEqual(got["block:0:label"], "要点")
        self.assertEqual(got["block:0"], "投影五个实例")
        self.assertEqual(got["block:1"], "追问共同点")
        two = dict(sd.iter_text_slots(DECK[2]))
        self.assertEqual(two["left:heading"], "相同点")
        self.assertEqual(two["left:bullet:0"], "都过(1,1)")
        self.assertEqual(two["right:bullet:0"], "单调性不同")

    def test_slot_names_are_writable_by_apply_slot(self):
        """清单里给的槽位名必须都能被 `apply_slot` 认（否则"找得到、替换不了"）。"""
        for i, s in enumerate(DECK):
            for slot, _text in sd.iter_text_slots(s):
                with self.subTest(page=i, slot=slot):
                    self.assertTrue(sd.apply_slot(dict(s), slot, "改一下").get("ok"), slot)


class FindTest(unittest.TestCase):
    def test_finds_across_pages_and_notes(self):
        hits = sd.find_text(DECK, "幂函数")
        self.assertEqual([(h["index"], h["slot"]) for h in hits],
                         [(0, "title"), (1, "title"), (1, "notes")], hits)

    def test_query_is_literal_not_regex(self):
        hits = sd.find_text(DECK, "(1,1)")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["slot"], "left:bullet:0")
        self.assertEqual(sd.find_text(DECK, "(1,1").__len__(), 1, "半个括号也要能找")

    def test_case_sensitivity_switch(self):
        deck = [{"layout": "bullets", "name": "Photosynthesis 光合作用", "blocks": [{"text": "PHOTO"}]}]
        self.assertEqual(len(sd.find_text(deck, "photo")), 2, "默认不分大小写")
        self.assertEqual(len(sd.find_text(deck, "photo", case_sensitive=True)), 0)
        self.assertEqual(len(sd.find_text(deck, "PHOTO", case_sensitive=True)), 1)

    def test_page_scope(self):
        hits = sd.find_text(DECK, "不同点", pages=[2])
        self.assertEqual(len(hits), 1, hits)
        self.assertEqual(sd.find_text(DECK, "不同点", pages=[0]), [])

    def test_count_when_multiple_in_one_slot(self):
        deck = [{"layout": "bullets", "name": "x", "blocks": [{"text": "幂函数·幂函数"}]}]
        hits = sd.find_text(deck, "幂函数")
        self.assertEqual(hits[0]["count"], 2, hits)


class ReplaceTest(unittest.TestCase):
    def test_replaces_all_pages_and_reports(self):
        got = sd.replace_text(DECK, "幂函数", "幂指函数")
        self.assertEqual(got["changed"], 3, got["hits"])
        self.assertEqual(got["pages"], [0, 1])
        self.assertEqual(got["slides"][0]["name"], "3.3 幂指函数")
        self.assertEqual(got["slides"][0]["cover_title"], "3.3 幂指函数")
        self.assertEqual(got["slides"][1]["name"], "幂指函数导入")
        self.assertEqual(got["slides"][1]["notes"], "先问幂指函数是什么")
        self.assertEqual(DECK[1]["name"], "幂函数导入", "原数据不许被就地改（要返回新的一份）")

    def test_cover_title_follows_title_only_when_it_was_in_sync(self):
        """封面标题 `cover_title` 原来跟标题同一串字 → 一起改；老师特意写的不一样的 → 不动。"""
        same = {"layout": "cover", "name": "3.3 幂函数", "cover_title": "3.3 幂函数"}
        sd.apply_slot(same, "title", "3.3 幂指函数")
        self.assertEqual(same["cover_title"], "3.3 幂指函数")
        mine = {"layout": "cover", "name": "3.3 幂函数", "cover_title": "封面：幂函数"}
        sd.apply_slot(mine, "title", "3.3 幂指函数")
        self.assertEqual(mine["cover_title"], "封面：幂函数", "老师自己写的封面标题不许被覆盖")

    def test_empty_replace_deletes_match(self):
        got = sd.replace_text(DECK, "共同点", "", pages=[1])
        self.assertEqual(got["changed"], 1)
        self.assertEqual([b["text"] for b in got["slides"][1]["blocks"]], ["投影五个实例", "追问"])

    def test_only_given_slots(self):
        got = sd.replace_text(DECK, "相同点", "一样的地方", slots=["left:heading"])
        self.assertEqual(got["changed"], 1)
        self.assertEqual(got["slides"][2]["left"]["heading"], "一样的地方")

    def test_case_sensitive_replace_keeps_others(self):
        deck = [{"layout": "bullets", "name": "photo", "blocks": [{"text": "PHOTO"}]}]
        got = sd.replace_text(deck, "photo", "图", case_sensitive=True)
        self.assertEqual(got["changed"], 1)
        self.assertEqual(got["slides"][0]["name"], "图")
        self.assertEqual(got["slides"][0]["blocks"][0]["text"], "PHOTO")

    def test_uses_apply_slot_semantics_for_labels(self):
        """块的小标签也走同一套：替换后标签还在、不是被并进正文。"""
        got = sd.replace_text(DECK, "要点", "重点")
        self.assertEqual(got["slides"][1]["blocks"][0]["label"], "重点")
        self.assertEqual(got["slides"][1]["blocks"][0]["text"], "投影五个实例")


class UnderlineTest(unittest.TestCase):
    def test_underline_is_a_known_override(self):
        got = sd.clean_slides([dict(DECK[1], slots={"block:0": {"underline": True}})])[0]
        self.assertTrue(got["slots"]["block:0"]["underline"])

    def test_underline_lands_in_svg(self):
        page = sd.build_deck("tech-editorial",
                             [dict(DECK[1], slots={"block:0": {"underline": True}})],
                             "x")["pages"][0]
        self.assertIn('text-decoration="underline"', page)
        plain = sd.build_deck("tech-editorial", [DECK[1]], "x")["pages"][0]
        self.assertNotIn("underline", plain, "没改过就一个字节都不加")


class FindEndpointTest(unittest.TestCase):
    def setUp(self):
        self.data = Path(tempfile.mkdtemp(prefix="edu_find_data_"))
        self._old = os.environ.get("EDU_DATA_DIR")
        os.environ["EDU_DATA_DIR"] = str(self.data)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old is None:
            os.environ.pop("EDU_DATA_DIR", None)
        else:
            os.environ["EDU_DATA_DIR"] = self._old

    def test_find_and_replace_endpoint(self):
        from edu_agent import web_server as ws

        sid = "find-ep-1"
        ws._store.set_session_doc(sid, {"slides": DECK, "title": "3.3 幂函数", "markdown": "# 正文"})
        got = ws.api_slide_find(ws.SlideFindReq(session_id=sid, query="幂函数", slides=DECK))
        self.assertTrue(got["ok"])
        self.assertEqual(len(got["hits"]), 3)
        self.assertEqual(got["changed"], 0, "查找不该改数据")
        self.assertEqual(ws._store.get_session_doc(sid)["slides"][1]["name"], "幂函数导入")

        rep = ws.api_slide_find(ws.SlideFindReq(session_id=sid, query="幂函数", replace="幂指函数",
                                                do="replace", slides=DECK))
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["changed"], 3)
        self.assertEqual(rep["page_count"], 2)
        doc = ws._store.get_session_doc(sid)
        self.assertEqual(doc["slides"][1]["name"], "幂指函数导入")
        self.assertEqual(doc["markdown"], "# 正文", "读-改-写：别把 markdown 抹了")
        self.assertTrue(doc["slides_edited"])

    def test_empty_query_is_noop_and_missing_slides_is_400(self):
        from edu_agent import web_server as ws

        sid = "find-ep-2"
        ws._store.set_session_doc(sid, {"slides": DECK})
        got = ws.api_slide_find(ws.SlideFindReq(session_id=sid, query="   ", slides=DECK))
        self.assertTrue(got["ok"])
        self.assertEqual(got["hits"], [])
        bad = ws.api_slide_find(ws.SlideFindReq(session_id="nobody", query="x", slides=[]))
        self.assertEqual(getattr(bad, "status_code", 200), 400)


if __name__ == "__main__":
    unittest.main()
