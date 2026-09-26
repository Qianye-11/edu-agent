# -*- coding: utf-8 -*-
"""填充器（M4b / docs/19 §10）单测：映射规则、页数策略、两道校验、红线。

映射规则是**确定性**的，所以逐条对：封面→封面槽、`name`→标题槽、`bullets`→正文槽、`minutes`→页脚槽；
模板没有的角色**不硬造**；页数不匹配按 §10.2 的策略走（多→复制模板自己的内容页；少→默认删多余页）。
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

from edu_agent import slide_deck                 # noqa: E402
from edu_agent import slide_fill as sf           # noqa: E402
from edu_agent import slide_template as st       # noqa: E402

SAMPLE_PPTX = _PROJ / "content" / "plans" / "完整课时模板（默认）.pptx"
DECK = [
    {"layout": "cover", "name": "3.3 幂函数", "cover_title": "3.3 幂函数",
     "subtitle": "数学 · 高一", "meta": "1 课时"},
    {"name": "情境导入", "minutes": "5", "bullets": ["投影五个实例", "追问：共同点"]},
    {"name": "概念建构", "minutes": "12", "bullets": ["形如 y=x^α"]},
]


class _FillCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="edu_fill_")
        self._old = os.environ.get("EDU_PPT_TEMPLATES")
        os.environ["EDU_PPT_TEMPLATES"] = self.tmp.name
        builtin = Path(self.tmp.name) / "_builtin"
        builtin.mkdir(parents=True)
        for f in (_PROJ / "content" / "ppt_templates" / "_builtin").glob("*.json"):
            shutil.copy(f, builtin / f.name)
        self.out = Path(self.tmp.name) / "out"
        self.tid = ""
        if SAMPLE_PPTX.is_file():
            self.tid = st.import_pptx(SAMPLE_PPTX, name="学校模板")["id"]
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old is None:
            os.environ.pop("EDU_PPT_TEMPLATES", None)
        else:
            os.environ["EDU_PPT_TEMPLATES"] = self._old
        self.tmp.cleanup()


@unittest.skipUnless(SAMPLE_PPTX.is_file(), "需要仓库样例 pptx 当模板")
class PlanTest(_FillCase):
    def test_cover_maps_to_cover_page(self):
        p = sf.plan(self.tid, DECK, "3.3 幂函数")
        self.assertTrue(p["pages"])
        self.assertEqual(p["pages"][0]["fills"][0]["role"], "title")
        self.assertIn("3.3 幂函数", p["pages"][0]["fills"][0]["text"])

    def test_page_names_and_bullets_go_in_order(self):
        p = sf.plan(self.tid, DECK, "3.3 幂函数")
        bodies = ["".join(f["text"] for f in page["fills"]) for page in p["pages"][1:]]
        self.assertTrue(any("情境导入" in b for b in bodies))
        self.assertTrue(any("投影五个实例" in b for b in bodies))
        self.assertTrue(any("形如 y=x^α" in b for b in bodies))

    def test_fewer_pages_deletes_extra(self):
        p = sf.plan(self.tid, DECK, "3.3 幂函数")
        self.assertGreater(p["policy"].get("deleted", 0), 0)
        self.assertTrue(any("删掉" in w for w in p["warnings"]))

    def test_more_pages_copies_template_content_page(self):
        deck = DECK + [{"name": f"补充 {i}", "bullets": [f"第 {i} 条"]} for i in range(20)]
        p = sf.plan(self.tid, deck, "3.3 幂函数")
        self.assertGreater(p["policy"].get("copied", 0), 0)
        self.assertTrue(any("复制" in w for w in p["warnings"]))
        self.assertEqual(len(p["pages"]), len(deck))

    def test_no_slots_degrades(self):
        (Path(self.tmp.name) / "empty-tpl").mkdir()
        (Path(self.tmp.name) / "empty-tpl" / "theme.json").write_text(
            '{"name":"空模板","tokens":{}}', encoding="utf-8")
        p = sf.plan("empty-tpl", DECK, "x")
        self.assertEqual(p["pages"], [])
        self.assertTrue(any("只用配色" in d or "槽位" in d for d in p["degraded"]))


@unittest.skipUnless(SAMPLE_PPTX.is_file(), "需要仓库样例 pptx 当模板")
class FillTest(_FillCase):
    def test_fill_produces_readable_pptx_with_content(self):
        out = sf.fill_pptx(self.tid, DECK, "3.3 幂函数", out_dir=self.out)
        self.assertTrue(out["ok"], out.get("error"))
        target = Path(out["file"])
        self.assertTrue(target.is_file())
        from pptx import Presentation

        prs = Presentation(str(target))
        blob = "\n".join(sh.text_frame.text for s in prs.slides for sh in s.shapes
                         if getattr(sh, "has_text_frame", False))
        for want in ("3.3 幂函数", "情境导入", "投影五个实例", "形如 y=x^α"):
            with self.subTest(text=want):
                self.assertIn(want, blob)

    def test_two_checks_present_and_blocking_flag(self):
        out = sf.fill_pptx(self.tid, DECK, "3.3 幂函数", out_dir=self.out)
        names = {c["name"]: c for c in out["checks"]}
        self.assertIn("内容零丢失", names)
        self.assertIn("容量自查", names)
        self.assertTrue(names["内容零丢失"]["ok"])
        self.assertFalse(names["容量自查"]["blocking"], "容量自查只提示，不阻断")

    def test_template_original_untouched(self):
        tpl = st.root() / self.tid / "template.pptx"
        before = (tpl.stat().st_size, tpl.stat().st_mtime)
        sf.fill_pptx(self.tid, DECK, "3.3 幂函数", out_dir=self.out)
        after = (tpl.stat().st_size, tpl.stat().st_mtime)
        self.assertEqual(before, after, "模板原稿只读：填充在副本上做")

    def test_never_writes_content_plans(self):
        plans = _PROJ / "content" / "plans"
        before = {p.name for p in plans.glob("*")} if plans.is_dir() else set()
        sf.fill_pptx(self.tid, DECK, "3.3 幂函数", out_dir=self.out)
        after = {p.name for p in plans.glob("*")} if plans.is_dir() else set()
        self.assertEqual(before, after)

    def test_long_text_is_flagged_but_not_cut(self):
        """超容量只提示、不截断。注意：我们自己的 400 字/块上限 < 模板正文槽容量，
        所以要用**多块**把这一页撑过容量（单块撑不过去）。"""
        probe = sf.plan(self.tid, DECK, "3.3 幂函数")
        caps = [int(f.get("capacity") or 0) for page in probe["pages"] for f in page["fills"]]
        cap = max(caps) if caps else 0
        bullets = ["要点" * 95 for _ in range(3)]          # 每块 190 字（不触发 400 上限），合计 570+
        deck = [DECK[0], {"name": "长内容", "bullets": bullets}]
        self.assertGreater(sum(len(b) for b in bullets), cap)
        p = sf.plan(self.tid, deck, "3.3 幂函数")
        self.assertTrue(any("超出模板框" in w for w in p["warnings"]), p["warnings"])
        self.assertTrue(any("拆页" in w or "缩短标题" in w for w in p["warnings"]), p["warnings"])
        out = sf.fill_pptx(self.tid, deck, "3.3 幂函数", out_dir=self.out)
        from pptx import Presentation

        prs = Presentation(out["file"])
        blob = "\n".join(sh.text_frame.text for s in prs.slides for sh in s.shapes
                         if getattr(sh, "has_text_frame", False))
        self.assertIn(bullets[0][:40], blob, "超容量只提示，文字不截断")
        self.assertIn(bullets[2][:40], blob, "最后一块也要在（没被丢掉）")

    def test_unknown_template_is_explicit(self):
        out = sf.fill_pptx("没有这个模板", DECK, "x", out_dir=self.out)
        self.assertFalse(out["ok"])
        self.assertIn("没有原稿文件", out["error"])


@unittest.skipUnless(SAMPLE_PPTX.is_file(), "需要仓库样例 pptx 当模板")
class RenderWithTemplateTest(_FillCase):
    def test_imported_template_can_render_deck(self):
        tokens = st.theme_for(self.tid)
        out = slide_deck.build_deck("", slide_deck.SAMPLE_DECK, title="3.3 幂函数", tokens=tokens)
        self.assertEqual(len(out["pages"]), len(slide_deck.SAMPLE_DECK))
        self.assertIn(tokens["canvas"]["bg"], out["pages"][0])


class MultiRunRegressionTest(_FillCase):
    """回归：**一段里有多个 run** 的模板（老师从 PowerPoint 里粘来的标题常见）。

    2026-09-25 线上报错：`AttributeError: '_Run' object has no attribute '_element'`
    —— python-pptx 的元素访问器不一样（`_Run._r` / `_Paragraph._p` / 形状 `._element`），
    而仓库样例的段落只有一个 run，所以当时的单测没覆盖到这条路径。
    """

    def _multi_run_template(self) -> Path:
        """一个"真模板会有的样子"：标题占位符里是一段多 run 文字 + 第二行；
        另有一个**非占位**文本框（模板自己的装饰文字，用来验证我们不会去动它）。"""
        from pptx import Presentation
        from pptx.util import Inches, Pt

        src = Path(self.tmp.name) / "multi-run.pptx"
        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[5])          # Title Only：有标题占位符
        tf = slide.shapes.title.text_frame
        r1 = tf.paragraphs[0].add_run()
        r1.text = "点击此处添加标题"
        r1.font.size = Pt(32)
        r1.font.bold = True
        r2 = tf.paragraphs[0].add_run()                             # ← 第二个 run：触发删多余 run 的分支
        r2.text = "（模板原名）"
        para2 = tf.add_paragraph()
        para2.add_run().text = "（占位第二行）"                      # ← 第二段：触发删多余段落的分支
        box = slide.shapes.add_textbox(Inches(1), Inches(5), Inches(4), Inches(0.6))
        box.text_frame.text = "模板自己的装饰文字"
        prs.save(str(src))
        return src

    def test_multi_run_paragraph_is_handled(self):
        """回归 2026-09-25：`AttributeError: '_Run' object has no attribute '_element'`
        （python-pptx 的 `_Run` 是 `._r`；仓库样例的段落只有一个 run，所以当时没覆盖到）。"""
        tid = st.import_pptx(self._multi_run_template(), name="多run模板")["id"]
        deck = [{"layout": "cover", "name": "3.3 幂函数", "cover_title": "3.3 幂函数"}]
        out = sf.fill_pptx(tid, deck, "3.3 幂函数", out_dir=self.out)
        self.assertTrue(out["ok"], out.get("error"))
        from pptx import Presentation

        prs = Presentation(out["file"])
        blob = "\n".join(sh.text_frame.text for s in prs.slides for sh in s.shapes
                         if getattr(sh, "has_text_frame", False))
        self.assertIn("3.3 幂函数", blob)
        self.assertNotIn("（模板原名）", blob, "多余 run 的文字要被换掉")
        self.assertNotIn("（占位第二行）", blob, "多余段落的文字要被换掉")

    def test_run_formatting_of_first_run_kept(self):
        """替换文字时保留第一个 run 的格式（字号/加粗），否则标题会变小。"""
        tid = st.import_pptx(self._multi_run_template(), name="多run模板")["id"]
        deck = [{"layout": "cover", "name": "封面标题", "cover_title": "封面标题"}]
        out = sf.fill_pptx(tid, deck, "封面标题", out_dir=self.out)
        self.assertTrue(out["ok"], out.get("error"))
        from pptx import Presentation

        prs = Presentation(out["file"])
        sizes = [run.font.size.pt for s in prs.slides for sh in s.shapes
                 if getattr(sh, "has_text_frame", False)
                 for p in sh.text_frame.paragraphs for run in p.runs
                 if run.text.strip() and run.font.size]
        self.assertIn(32.0, sizes, "第一个 run 的 32pt 应该留着")

    def test_untouched_box_keeps_template_text(self):
        """我们这页没有对应角色的内容时，**不动**模板那个框（装饰文字/页脚不该被清空）。"""
        tid = st.import_pptx(self._multi_run_template(), name="多run模板")["id"]
        out = sf.fill_pptx(tid, [{"layout": "cover", "name": "只有标题", "cover_title": "只有标题"}],
                           "只有标题", out_dir=self.out)
        self.assertTrue(out["ok"], out.get("error"))
        from pptx import Presentation

        prs = Presentation(out["file"])
        blob = "\n".join(sh.text_frame.text for s in prs.slides for sh in s.shapes
                         if getattr(sh, "has_text_frame", False))
        self.assertIn("模板自己的装饰文字", blob)


class EndpointFillTest(_FillCase):
    """走接口（面板点按钮的真实路径）：`POST /api/slide/fill`。"""

    def setUp(self):
        super().setUp()
        import tempfile as _tf

        self._old_data = os.environ.get("EDU_DATA_DIR")
        os.environ["EDU_DATA_DIR"] = _tf.mkdtemp(prefix="edu_fill_data_")
        from edu_agent import web_server as ws
        from edu_agent import capabilities as cap

        self.ws, self.cap = ws, cap
        cap._CACHE.clear()
        self.addCleanup(self._restore_data)

    def _restore_data(self):
        if self._old_data is None:
            os.environ.pop("EDU_DATA_DIR", None)
        else:
            os.environ["EDU_DATA_DIR"] = self._old_data
        self.cap._CACHE.clear()

    def test_endpoint_fills_and_reports_checks(self):
        from pptx import Presentation
        from pptx.util import Inches

        src = Path(self.tmp.name) / "tpl.pptx"
        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[5])
        slide.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(1)).text_frame.text = "标题占位"
        prs.save(str(src))
        tid = st.import_pptx(src, name="接口模板")["id"]

        r = self.ws.api_slide_fill(self.ws.SlideFillReq(
            template_id=tid, slides=DECK, title="3.3 幂函数",
            outDir=str(self.out), confirm=True))
        self.assertTrue(r.get("ok"), r.get("error"))
        self.assertTrue(Path(r["file"]).is_file())
        names = {c["name"]: c["ok"] for c in r["checks"]}
        self.assertTrue(names["内容零丢失"])
        self.assertIn("download_url", r)

    def test_endpoint_needs_template_and_content(self):
        r = self.ws.api_slide_fill(self.ws.SlideFillReq(template_id="", slides=DECK, confirm=True))
        self.assertEqual(getattr(r, "status_code", 200), 400)
        r2 = self.ws.api_slide_fill(self.ws.SlideFillReq(template_id=self.tid, slides=[], confirm=True))
        self.assertEqual(getattr(r2, "status_code", 200), 400)


if __name__ == "__main__":
    unittest.main()
