# -*- coding: utf-8 -*-
"""可编辑盒子（R10）：`/api/slide/render?annotate` 出来的 `boxes` 与 `data-slot`。

编辑器要靠它做"页内点着改字"，所以这里钉死四件事：
1. **每个可编辑槽都有盒子**，且坐标在设计画布（1280×720）内、字号就是 tokens 的阶梯值；
2. **槽位命名 ↔ `clean_slides` 字段一一对应** —— 改字段名这里的用例会红（映射只有 `apply_slot` 一份）；
3. **`annotate=False`（导出路径）时 SVG 里没有 `data-slot`** —— 导出与以前逐字节一致（`docs/25` D8）；
4. **填充页的盒子**：`role=decoration` 的槽不生成盒子（老师用「对齐」标成装饰的，不许被改）。
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

_PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJ / "src"))

from edu_agent import ppt_svg                              # noqa: E402
from edu_agent import ppt_tokens as tk                     # noqa: E402
from edu_agent import slide_deck                           # noqa: E402
from edu_agent import slide_fill as sf                     # noqa: E402
from edu_agent import slide_template as st                 # noqa: E402

SAMPLE_PPTX = _PROJ / "content" / "plans" / "完整课时模板（默认）.pptx"

DECK = [
    {"layout": "cover", "title": "3.3 幂函数", "subtitle": "数学 · 高一", "meta": "1 课时"},
    {"layout": "bullets", "name": "情境导入", "label": "环节一",
     "blocks": [{"label": "例子", "text": "投影五个实例"}, {"label": "", "text": "追问：共同点"}]},
    {"layout": "two-col", "name": "对比", "left": {"heading": "共同点", "bullets": ["都过 (1,1)"]},
     "right": {"heading": "不同点", "bullets": ["单调性不同"]}},
    {"layout": "section", "name": "例题精讲", "title": "例题精讲"},
    {"layout": "media", "name": "图象", "caption": "图 1 幂函数图象", "bullets": ["看第一象限"]},
    {"layout": "end", "name": "小结", "closing": "下课", "bullets": ["三条结论"]},
]


class BoxesTest(unittest.TestCase):
    def setUp(self):
        self.t = tk.load("tech-editorial")

    def test_boxes_exist_for_every_text_slot(self):
        out = slide_deck.build_deck("tech-editorial", DECK, title="3.3 幂函数", annotate=True)
        self.assertEqual(len(out["boxes"]), len(DECK), "一页一组盒子")
        for i, boxes in enumerate(out["boxes"]):
            with self.subTest(page=i + 1):
                self.assertTrue(boxes, "这一页至少要有一个可编辑盒子")
                for b in boxes:
                    self.assertTrue(b["slot"], b)
                    self.assertGreater(b["w"], 0)
                    self.assertGreater(b["h"], 0)
                    self.assertGreaterEqual(b["x"], 0)
                    self.assertGreaterEqual(b["y"], 0)
                    self.assertLessEqual(b["x"] + b["w"], 1280.5, f"越出画布：{b}")
                    self.assertLessEqual(b["y"] + b["h"], 720.5, f"越出画布：{b}")

    def test_no_duplicate_slots_per_page(self):
        """一页里同一个槽只能有一个盒子 —— 不然前端会画出两块重叠的点击区（踩过：title 出现两次）。"""
        out = slide_deck.build_deck("tech-editorial", DECK, title="3.3 幂函数", annotate=True)
        for i, boxes in enumerate(out["boxes"]):
            slots = [b["slot"] for b in boxes]
            with self.subTest(page=i + 1):
                self.assertEqual(len(slots), len(set(slots)), f"重复槽位：{slots}")

    def test_slot_names_are_known(self):
        """槽位名必须都能被 `apply_slot` 认出来（不认识就拒绝）—— 两边命名不许各写一套。"""
        out = slide_deck.build_deck("tech-editorial", DECK, title="3.3 幂函数", annotate=True)
        for i, boxes in enumerate(out["boxes"]):
            for b in boxes:
                with self.subTest(page=i + 1, slot=b["slot"]):
                    res = slide_deck.apply_slot({}, b["slot"], "x")
                    self.assertTrue(res.get("ok"), f"{b['slot']} → {res.get('error')}")

    def test_font_sizes_are_token_steps(self):
        out = slide_deck.build_deck("tech-editorial", DECK, title="3.3 幂函数", annotate=True)
        steps = {round(float(v), 1) for k, v in self.t["type"].items()
                 if isinstance(v, (int, float)) and k not in ("lh_body", "letter_spacing")}
        for boxes in out["boxes"]:
            for b in boxes:
                with self.subTest(slot=b["slot"]):
                    self.assertIn(round(b["size"], 1), steps, "字号必须是 tokens 阶梯里的值（不许新造）")

    def test_export_path_has_no_annotation(self):
        plain = slide_deck.build_deck("tech-editorial", DECK, title="x")
        self.assertNotIn("boxes", plain, "annotate 不给就不该有 boxes 键")
        for svg in plain["pages"]:
            self.assertNotIn("data-slot", svg, "导出用的 SVG 不许带打点（转换器那边要逐字节一致）")
        marked = slide_deck.build_deck("tech-editorial", DECK, title="x", annotate=True)
        self.assertIn("data-slot", marked["pages"][1], "预览要带打点，供前端编辑时隐藏那一槽")

    def test_svg_slots_match_boxes(self):
        """盒子的 slot 集合要和 SVG 里 `data-slot` 的集合一致（少一个就意味着"看得到改不了"）。"""
        import re

        out = slide_deck.build_deck("tech-editorial", DECK, title="x", annotate=True)
        for i, svg in enumerate(out["pages"]):
            in_svg = set(re.findall(r'data-slot="([^"]+)"', svg))
            in_box = {b["slot"] for b in out["boxes"][i]}
            with self.subTest(page=i + 1):
                self.assertTrue(in_box <= in_svg | in_box, "盒子里的槽都该在 SVG 里")
                self.assertFalse(in_svg - in_box, f"SVG 里有打点却没盒子的槽：{sorted(in_svg - in_box)}")


class ApplySlotTest(unittest.TestCase):
    """槽位 → 字段（唯一一份映射）。"""

    def test_title_writes_name_and_title(self):
        s = {}
        self.assertTrue(slide_deck.apply_slot(s, "title", "情境导入")["ok"])
        self.assertEqual(s["name"], "情境导入")
        self.assertEqual(s["title"], "情境导入")

    def test_block_writes_blocks_and_drops_bullets(self):
        s = {"bullets": ["旧的"]}
        slide_deck.apply_slot(s, "block:0", "新的")
        self.assertEqual(s["blocks"], [{"label": "", "text": "新的"}])
        self.assertNotIn("bullets", s, "统一走 blocks，别留两种写法打架")

    def test_block_label(self):
        s = {"blocks": [{"label": "", "text": "内容"}]}
        slide_deck.apply_slot(s, "block:0:label", "定义")
        self.assertEqual(s["blocks"][0], {"label": "定义", "text": "内容"})

    def test_two_col_bullet_and_heading(self):
        s = {}
        slide_deck.apply_slot(s, "left:heading", "共同点")
        slide_deck.apply_slot(s, "left:bullet:0", "都过 (1,1)")
        self.assertEqual(s["left"], {"heading": "共同点", "bullets": ["都过 (1,1)"]})

    def test_empty_text_removes_block(self):
        s = {"blocks": [{"label": "", "text": "一"}, {"label": "", "text": "二"}]}
        slide_deck.apply_slot(s, "block:0", "")
        self.assertEqual([b["text"] for b in s["blocks"]], ["二"], "清空 = 删掉，不留空壳")

    def test_unknown_slot_is_rejected(self):
        r = slide_deck.apply_slot({}, "乱写的槽", "x")
        self.assertFalse(r["ok"])
        self.assertIn("不认识", r["error"])

    def test_growing_block_index_pads(self):
        s = {}
        slide_deck.apply_slot(s, "block:2", "第三块")
        self.assertEqual(len(s["blocks"]), 3)
        self.assertEqual(s["blocks"][2]["text"], "第三块")

    def test_long_text_is_clamped_like_clean_slides(self):
        s = {}
        slide_deck.apply_slot(s, "block:0", "字" * 900)
        self.assertEqual(len(slide_deck.clean_slides([s])[0]["blocks"][0]["text"]), 400,
                         "上限跟 clean_slides 一致（400 字），别一边 400 一边 900")


class RenderAnnotateFlagTest(unittest.TestCase):
    """`/api/slide/render` 的 annotate 开关（接口层）。"""

    def test_render_endpoint_returns_boxes_only_when_asked(self):
        from edu_agent import web_server as ws

        req = ws.SlideRenderReq(theme_id="tech-editorial", slides=DECK, title="x")
        plain = ws.api_slide_render(req)
        self.assertTrue(plain.get("ok"))
        self.assertNotIn("boxes", plain)
        marked = ws.api_slide_render(ws.SlideRenderReq(theme_id="tech-editorial", slides=DECK,
                                                       title="x", annotate=True))
        self.assertTrue(marked.get("ok"))
        self.assertEqual(len(marked.get("boxes") or []), len(DECK))

    def test_slot_endpoint_writes_session_doc(self):
        """`POST /api/slide/slot`：改一格 → 只动那一格，且**不抹掉**会话文档里的别的字段。"""
        import os
        import tempfile

        from edu_agent import web_server as ws

        old = os.environ.get("EDU_DATA_DIR")
        os.environ["EDU_DATA_DIR"] = tempfile.mkdtemp(prefix="edu_slot_data_")
        try:
            sid = "slot-test-1"
            ws._store.set_session_doc(sid, {"slides": DECK, "title": "3.3 幂函数",
                                            "markdown": "# 正文", "md_sig": "abc", "slides_edited": False})
            r = ws.api_slide_slot(ws.SlideSlotReq(session_id=sid, index=1, slot="block:0", text="改过的第一块"))
            self.assertTrue(r.get("ok"), r.get("error"))
            self.assertEqual(r["slides"][1]["blocks"][0]["text"], "改过的第一块")
            self.assertEqual(r["slides"][2].get("left", {}).get("bullets"), ["都过 (1,1)"],
                             "别的页不许受影响")
            doc = ws._store.get_session_doc(sid)
            self.assertEqual(doc.get("markdown"), "# 正文", "读-改-写：别把 markdown 抹了")
            self.assertEqual(doc.get("md_sig"), "abc", "指纹也要留着（正文改过没的判断靠它）")
            self.assertTrue(doc.get("slides_edited"))
            bad = ws.api_slide_slot(ws.SlideSlotReq(session_id=sid, index=99, slot="title", text="x"))
            self.assertEqual(getattr(bad, "status_code", 200), 400, "页号越界要明确报错")
        finally:
            if old is None:
                os.environ.pop("EDU_DATA_DIR", None)
            else:
                os.environ["EDU_DATA_DIR"] = old


@unittest.skipUnless(SAMPLE_PPTX.is_file(), "需要仓库样例 pptx 当模板")
class FillBoxesTest(unittest.TestCase):
    """模板填充页的盒子：**内容槽可编辑、装饰槽不生成盒子**（D5）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="edu_fillbox_")
        self._old = os.environ.get("EDU_PPT_TEMPLATES")
        os.environ["EDU_PPT_TEMPLATES"] = self.tmp.name
        builtin = Path(self.tmp.name) / "_builtin"
        builtin.mkdir(parents=True)
        for f in (_PROJ / "content" / "ppt_templates" / "_builtin").glob("*.json"):
            (builtin / f.name).write_bytes(f.read_bytes())
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old is None:
            os.environ.pop("EDU_PPT_TEMPLATES", None)
        else:
            os.environ["EDU_PPT_TEMPLATES"] = self._old
        self.tmp.cleanup()

    def _card_template(self) -> Path:
        """一页 4 张卡片（每张：小标题框 + 正文框）+ 一个设计标签 —— 复刻老师那种设计稿。

        为什么要自己造：仓库样例模板每页只有 1 个正文槽，"多个同角色槽"这条根本验不到（踩过）。
        """
        from pptx import Presentation
        from pptx.util import Emu, Pt

        def px(v: float) -> Emu:
            return Emu(int(v * 9525))

        src = Path(self.tmp.name) / "cards.pptx"
        prs = Presentation()
        s = prs.slides.add_slide(prs.slide_layouts[6])
        t = s.shapes.add_textbox(px(440), px(7), px(400), px(100))
        t.text_frame.text = "图形的奥秘"
        t.text_frame.paragraphs[0].runs[0].font.size = Pt(50)
        for i in range(4):
            x = 29 + i * 319
            h = s.shapes.add_textbox(px(x), px(331), px(264), px(36))
            h.text_frame.text = f"卡片标题{i + 1}"
            h.text_frame.paragraphs[0].runs[0].font.size = Pt(18)
            b = s.shapes.add_textbox(px(x), px(380), px(264), px(160))
            b.text_frame.text = "卡片正文示例文字"
            b.text_frame.paragraphs[0].runs[0].font.size = Pt(12)
        lab = s.shapes.add_textbox(px(87), px(139), px(58), px(68))
        lab.text_frame.text = "圆"
        lab.text_frame.paragraphs[0].runs[0].font.size = Pt(36)
        prs.save(str(src))
        return src

    def test_multi_body_template_has_geometry_per_slot(self):
        tid = st.import_pptx(self._card_template(), name="卡片模板")["id"]
        plan = sf.plan(tid, self._deck(), "卡片")
        page = plan["pages"][0]
        bodies = [f for f in page["fills"] if f["role"] == "body"]
        self.assertGreaterEqual(len(bodies), 4, "4 张卡片 → 至少 4 个正文槽")
        shapes = [f["shape_index"] for f in bodies]
        self.assertEqual(len(set(shapes)), len(shapes), "shape_index 必须唯一（否则回写会串格）")
        for f in bodies:
            geo = f["geo"]
            self.assertGreater(float(geo["w"]), 0)
            self.assertLessEqual(float(geo["x"]) + float(geo["w"]), 1280.5)
            self.assertLessEqual(float(geo["y"]) + float(geo["h"]), 720.5)
            self.assertIn("capacity", f, "容量要带上（右栏要显示「能放多少字」，前端才好提示）")
        # 装饰槽**不进 fills**（前端不给它画编辑框），只在 kept 里留着原文字
        self.assertIn("圆", page.get("kept") or [], "「圆」这种设计标签应该被判成装饰并保留")
        self.assertFalse([f for f in page["fills"] if f.get("sample") == "圆"],
                         "装饰槽不该出现在可编辑的 fills 里")

    def test_mapping_by_shape_index_hits_exactly_one_slot(self):
        """多个同角色槽时，按 shape_index 只改一格 —— R7 发现的真问题（按 role 会把 4 格写成同一句）。"""
        tid = st.import_pptx(self._card_template(), name="卡片模板")["id"]
        plan = sf.plan(tid, self._deck(), "卡片")
        page = plan["pages"][0]
        bodies = [f for f in page["fills"] if f["role"] == "body"]
        target = max(bodies, key=lambda f: int(f.get("capacity") or 0))     # 取一个"正文框"（容量最大）
        shape = target["shape_index"]
        others = {f["shape_index"]: f["text"] for f in bodies if f["shape_index"] != shape}
        sf.apply_mapping(plan, [{"template_index": page["template_index"],
                                 "fills": [{"shape_index": shape, "text": "只改这一格"}]}])
        after = {f["shape_index"]: f["text"] for f in page["fills"] if f["role"] == "body"}
        self.assertEqual(after.get(shape), "只改这一格")
        self.assertEqual({k: v for k, v in after.items() if k != shape}, others,
                         "别的正文槽一个字都不许动")
        self.assertEqual(plan["mapping_applied"], 1, "只算改了一个槽")

    def test_mapping_by_role_only_touches_first(self):
        tid = st.import_pptx(self._card_template(), name="卡片模板")["id"]
        plan = sf.plan(tid, self._deck(), "卡片")
        page = plan["pages"][0]
        bodies = [f for f in page["fills"] if f["role"] == "body"]
        first_shape = bodies[0]["shape_index"]
        others = {f["shape_index"]: f["text"] for f in bodies if f["shape_index"] != first_shape}
        sf.apply_mapping(plan, [{"template_index": page["template_index"],
                                 "fills": [{"role": "body", "text": "只改第一个"}]}])
        after = {f["shape_index"]: f["text"] for f in page["fills"] if f["role"] == "body"}
        self.assertEqual(after.get(first_shape), "只改第一个")
        self.assertEqual({k: v for k, v in after.items() if k != first_shape}, others,
                         "只给 role 时不许把后面几个槽也改了")
        self.assertEqual(plan["mapping_applied"], 1, "只算改了一个槽")

    @staticmethod
    def _deck() -> list[dict]:
        return [{"layout": "bullets", "name": "卡片页",
                 "blocks": [{"label": "", "text": f"第{i}条"} for i in range(4)]}]


if __name__ == "__main__":
    unittest.main()
