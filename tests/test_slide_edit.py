# -*- coding: utf-8 -*-
"""幻灯片**编辑**（M4a）单测：数据收干净、存回会话、预览/导出都以改过的那份为准。

对应 `docs/23` M4a（T4.1/T4.2）：
- 编辑只改数据（`session_docs` 表），渲染仍由服务端现场出 SVG ⇒ "预览与导出同时变"是结构保证；
- 逐块字数超上限**只提示不截断**（与 §3.3 一致）；
- 老库不需要迁移（加表不改表）。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJ / "src"))

from edu_agent import slide_deck                   # noqa: E402
from edu_agent.host.store import SessionStore      # noqa: E402

os.environ.setdefault("EDU_DATA_DIR", tempfile.mkdtemp(prefix="edu_test_data_"))  # noqa: E402
from edu_agent import web_server as ws             # noqa: E402

MD = ("# 3.3 幂函数\n\n"
      "### 情境导入（约 5 分钟）\n- 投影五个实例\n- 追问：共同点是什么\n\n"
      "### 概念建构（约 12 分钟）\n- 形如 y=x^α\n")


class CleanSlidesTest(unittest.TestCase):
    def test_keeps_known_keys_and_drops_junk(self):
        raw = [{"name": " 情境导入 ", "title": "t", "minutes": 5, "layout": "bullets",
                "junk": "别存我", "bullets": [" 一 ", "", "二"],
                "blocks": [{"label": " 定义 ", "text": " 形如 y=x^α "}, {"text": ""}, "裸字符串"]}]
        out = slide_deck.clean_slides(raw)
        self.assertEqual(len(out), 1)
        s = out[0]
        self.assertEqual(s["name"], "情境导入")
        self.assertEqual(s["minutes"], "5")
        self.assertNotIn("junk", s)
        self.assertEqual(s["bullets"], ["一", "二"])
        self.assertEqual(s["blocks"], [{"label": "定义", "text": "形如 y=x^α"}, {"label": "", "text": "裸字符串"}])

    def test_unknown_layout_is_dropped(self):
        out = slide_deck.clean_slides([{"name": "x", "layout": "not-a-layout"}])
        self.assertNotIn("layout", out[0])

    def test_empty_pages_are_dropped(self):
        self.assertEqual(slide_deck.clean_slides([{"name": ""}, {}, {"name": "有名字"}]), [{"name": "有名字"}])
        self.assertEqual(slide_deck.clean_slides(None), [])

    def test_long_text_is_capped_not_truncated_mid_sentence(self):
        out = slide_deck.clean_slides([{"name": "x", "bullets": ["字" * 900]}])
        self.assertEqual(len(out[0]["bullets"][0]), 400, "手滑粘贴整篇 → 截到 400 字上限")

    def test_two_col_columns_kept(self):
        out = slide_deck.clean_slides([{"name": "对比", "layout": "two-col",
                                       "left": {"heading": "左", "bullets": ["甲"]},
                                       "right": {"heading": "", "bullets": ["乙"]}}])
        self.assertEqual(out[0]["left"]["heading"], "左")
        self.assertEqual(out[0]["right"]["bullets"], ["乙"])


class LengthWarningTest(unittest.TestCase):
    """T4.2：字数超上限只提示，绝不截断。"""

    def test_over_limit_warns(self):
        w = slide_deck.length_warnings([{"name": "a"}, {"name": "b", "bullets": ["字" * 30]}])
        self.assertEqual(len(w), 1)
        self.assertIn("第 2 页第 1 块", w[0])
        self.assertIn("30 字", w[0])

    def test_at_limit_is_fine(self):
        self.assertEqual(slide_deck.length_warnings([{"name": "a", "bullets": ["字" * 22]}]), [])

    def test_blocks_are_checked_too(self):
        w = slide_deck.length_warnings([{"name": "a", "blocks": [{"label": "定义", "text": "字" * 25}]}])
        self.assertTrue(w and "25 字" in w[0])

    def test_build_deck_surfaces_length_warnings(self):
        out = slide_deck.build_deck("tech-editorial", [{"name": "a"}, {"name": "b", "bullets": ["字" * 30]}])
        self.assertTrue(any("30 字" in x for x in out["warnings"]))


class StoreDocTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="edu_store_")
        self.store = SessionStore(Path(self.tmp.name) / "s.db")
        self.sid = self.store.new("编辑测试")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_round_trip(self):
        self.assertEqual(self.store.get_session_doc(self.sid), {})
        self.store.set_session_doc(self.sid, {"slides": [{"name": "一"}], "slides_edited": True})
        got = self.store.get_session_doc(self.sid)
        self.assertEqual(got["slides"][0]["name"], "一")
        self.assertTrue(got["slides_edited"])

    def test_overwrite_and_clear(self):
        self.store.set_session_doc(self.sid, {"slides": [{"name": "一"}]})
        self.store.set_session_doc(self.sid, {"slides": [{"name": "二"}]})
        self.assertEqual(self.store.get_session_doc(self.sid)["slides"][0]["name"], "二")
        self.store.clear_session_doc(self.sid)
        self.assertEqual(self.store.get_session_doc(self.sid), {})

    def test_delete_session_removes_doc(self):
        self.store.set_session_doc(self.sid, {"slides": [{"name": "一"}]})
        self.store.delete(self.sid)
        self.assertEqual(self.store.get_session_doc(self.sid), {})

    def test_old_db_without_new_table_still_works(self):
        """老库（只有 sessions 表）打开后自动补表，不迁移、不报错。"""
        import sqlite3

        old = Path(self.tmp.name) / "old.db"
        con = sqlite3.connect(str(old))
        con.executescript("""
            CREATE TABLE sessions (id TEXT PRIMARY KEY, title TEXT NOT NULL,
              messages TEXT NOT NULL DEFAULT '[]', last_html TEXT NOT NULL DEFAULT '',
              created REAL NOT NULL, updated REAL NOT NULL);
        """)
        con.execute("INSERT INTO sessions VALUES('s1','老会话','[]','',1,1)")
        con.commit()
        con.close()
        st = SessionStore(old)
        try:
            self.assertEqual(st.get_session_doc("s1"), {})       # 老行照样能读
            st.set_session_doc("s1", {"slides": [{"name": "x"}]})
            self.assertEqual(st.get_session_doc("s1")["slides"][0]["name"], "x")
            self.assertEqual(st.get("s1")["title"], "老会话")      # 老数据没被动
        finally:
            st.close()


class EditEndpointTest(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("EDU_DATA_DIR")
        os.environ["EDU_DATA_DIR"] = tempfile.mkdtemp(prefix="edu_test_data_")
        self.sid = ws._store.new("编辑")
        self.addCleanup(self._restore)

    def _restore(self):
        # 别把编辑结果漏给别的测试（doc-preview 会优先用会话里那份）
        for sid in (getattr(self, "sid", ""), ws._store.current_id()):
            try:
                ws._store.clear_session_doc(sid or "")
            except Exception:  # noqa: BLE001
                pass
        if self._old is None:
            os.environ.pop("EDU_DATA_DIR", None)
        else:
            os.environ["EDU_DATA_DIR"] = self._old

    def test_save_requires_session(self):
        """没有当前会话、也没传 session_id 时要明确报错，而不是悄悄存到别处。"""
        import unittest.mock as mock

        with mock.patch.object(ws._store, "current_id", return_value=None):
            r = ws.api_slide_save(ws.SlideSaveReq(session_id="", slides=[]))
        self.assertEqual(getattr(r, "status_code", 200), 400)

    def test_save_falls_back_to_current_session(self):
        """不传 session_id 时存到"当前会话"（与 doc-preview 的兜底口径一致）。"""
        r = ws.api_slide_save(ws.SlideSaveReq(session_id="", slides=[{"name": "当会话"}]))
        self.assertTrue(r["ok"])
        self.assertEqual(r["session_id"], ws._store.current_id())
        self.assertEqual(ws._store.get_session_doc(r["session_id"])["slides"][0]["name"], "当会话")

    def test_save_then_preview_prefers_edited(self):
        r = ws.api_slide_save(ws.SlideSaveReq(session_id=self.sid, title="3.3 幂函数", slides=[
            {"name": "情境导入（我改过）", "layout": "bullets", "bullets": ["只留这一条"]},
        ]))
        self.assertTrue(r["ok"] and r["pages"] == 1)
        d = ws.api_session_doc_preview(ws.DocPreviewReq(markdown=MD, session_id=self.sid))
        self.assertTrue(d["slides_edited"])
        self.assertEqual(d["slides"][0]["name"], "情境导入（我改过）")
        self.assertEqual(d["slides"][0]["bullets"], ["只留这一条"])

    def test_render_prefers_saved_slides(self):
        ws.api_slide_save(ws.SlideSaveReq(session_id=self.sid, slides=[
            {"name": "改过的页", "layout": "bullets", "bullets": ["甲"]}]))
        out = ws.api_slide_render(ws.SlideRenderReq(theme_id="tech-editorial", markdown=MD,
                                                    session_id=self.sid))
        self.assertEqual(len(out["pages"]), 1)
        self.assertIn("改过的页", out["pages"][0])

    def test_export_reads_saved_slides_when_body_has_none(self):
        """只给 session_id 的导出也要用"改过的那份"（面板走的就是这条路）。"""
        import unittest.mock as mock

        ws.api_slide_save(ws.SlideSaveReq(session_id=self.sid, title="改过的标题", slides=[
            {"name": "改过的页", "layout": "bullets", "bullets": ["甲"]}]))
        captured: dict = {}

        def fake_export(theme_id, slides, title="", **kw):
            captured.update({"theme_id": theme_id, "slides": slides, "title": title})
            return {"ok": True, "backend": "native", "filename": "x.pptx", "outDir": "d",
                    "file": "d/x.pptx", "pages": len(slides or []), "theme_id": theme_id,
                    "verify": {"ok": True, "checks": []}}

        with mock.patch.object(ws.slide_deck, "export_deck_pptx", side_effect=fake_export):
            r = ws.api_wps_export({"kind": "pptx-deck", "theme_id": "tech-editorial",
                                   "session_id": self.sid, "confirm": True})
        self.assertTrue(r["ok"], r)
        self.assertEqual(captured["slides"][0]["name"], "改过的页")
        self.assertEqual(captured["title"], "改过的标题")

    def test_doc_preview_marks_not_edited_before_save(self):
        d = ws.api_session_doc_preview(ws.DocPreviewReq(markdown=MD, session_id=self.sid))
        self.assertFalse(d["slides_edited"])
        self.assertGreater(len(d["slides"]), 0)          # 没改过就用 markdown 派生的

    def test_markdown_change_makes_saved_slides_stale(self):
        """改过幻灯片之后正文又被重出：这次按正文出，但**明确告诉前端**（不静默覆盖、也不删老师的改动）。"""
        ws.api_slide_save(ws.SlideSaveReq(session_id=self.sid, slides=[
            {"name": "改过的页", "layout": "bullets", "bullets": ["甲"]}], markdown=MD))
        d = ws.api_session_doc_preview(ws.DocPreviewReq(markdown=MD + "\n### 新增环节\n- 乙\n",
                                                       session_id=self.sid))
        self.assertTrue(d["slides_edited"], "改过的事实要留着")
        self.assertTrue(d["slides_stale"], "正文变了要标出来")
        self.assertNotIn("改过的页", [s["name"] for s in d["slides"]], "这次按新正文出")
        self.assertEqual(ws._store.get_session_doc(self.sid)["slides"][0]["name"], "改过的页",
                         "老师的改动还在会话里（没被删）")

    def test_same_markdown_keeps_saved_slides(self):
        ws.api_slide_save(ws.SlideSaveReq(session_id=self.sid, slides=[
            {"name": "改过的页", "layout": "bullets", "bullets": ["甲"]}], markdown=MD))
        d = ws.api_session_doc_preview(ws.DocPreviewReq(markdown=MD, session_id=self.sid))
        self.assertFalse(d["slides_stale"])
        self.assertEqual(d["slides"][0]["name"], "改过的页")

    def test_render_ignores_stale_saved_slides(self):
        ws.api_slide_save(ws.SlideSaveReq(session_id=self.sid, slides=[
            {"name": "改过的页", "layout": "bullets", "bullets": ["甲"]}], markdown=MD))
        out = ws.api_slide_render(ws.SlideRenderReq(theme_id="tech-editorial",
                                                    markdown=MD + "\n### 新增环节\n- 乙\n",
                                                    session_id=self.sid))
        self.assertNotIn("改过的页", "".join(out["pages"]))


if __name__ == "__main__":
    unittest.main()
