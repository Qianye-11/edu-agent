# -*- coding: utf-8 -*-
"""自由层（R21）：老师自己插的文本框与形状 —— 收口、渲染、导出。

三条要钉住的：

1. **几何不能自己编**：每个预设形状的路径与 ppt-master 的 `preset_shape_svg.py` **对拍**
   （黄金测试；没装 ppt-master 就跳过）—— 这是"预览=导出"不破的前提；
2. **入口要严**：`clean_objects` 只认认识的形状/键、数值夹范围、id 不撞车、一页有上限；
3. **导出真的带过去**：同一份 SVG → pptx 里能看到形状（原生可编辑，不是图片）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJ / "src"))

from edu_agent import ppt_tokens as tk                  # noqa: E402
from edu_agent import ppt_svg                            # noqa: E402
from edu_agent import slide_deck as sd                   # noqa: E402

THEME = "tech-editorial"
_T = tk.load(THEME)


def _ppt_master_ready() -> bool:
    try:
        from edu_agent import ppt_polish
        return bool(ppt_polish.status().get("intake_ready"))
    except Exception:                                        # noqa: BLE001
        return False


PM_READY = _ppt_master_ready()
PM_SCRIPT = _PROJ / ".deps" / "ppt-master" / "skills" / "ppt-master" / "scripts" / "preset_shape_svg.py"


def _script_path(preset: str, x: float, y: float, w: float, h: float) -> str:
    """去问 ppt-master 要"标准答案"（同一个预设、同一个框）。"""
    cmd = [sys.executable, str(PM_SCRIPT), "render", "--id", "t",
           "--frame", str(x), str(y), str(w), str(h)]
    if preset == "line":
        cmd += ["--object-kind", "connector", "--stroke", "#1A1A1A", preset]
    else:
        cmd += ["--fill", "#EEEEEE", preset]
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                         timeout=120).stdout
    for chunk in out.split('"'):
        if chunk.startswith("M "):
            return chunk
    return ""


class CleanObjectsTest(unittest.TestCase):
    def test_unknown_kind_and_shape_are_dropped_or_fixed(self):
        got = sd.clean_objects([{"kind": "blob"}, {"kind": "shape", "shape": "worm"}])
        self.assertEqual(len(got), 1, got)
        self.assertEqual(got[0]["shape"], "rect", "不认识的形状退回矩形，而不是丢掉整个对象")

    def test_ranges_and_ids(self):
        got = sd.clean_objects([{"kind": "shape", "id": "o1", "x": 99999, "y": -99999, "w": 1, "h": 1},
                                {"kind": "shape", "id": "o1", "shape": "ellipse"},
                                {"kind": "text", "id": "bad id!", "w": 200, "h": 80}])
        self.assertEqual([o["id"] for o in got], ["o1", "o2", "o3"], "撞车/非法 id 要重编")
        self.assertLessEqual(got[0]["x"], sd.OBJ_RANGE["x"][1])
        self.assertGreaterEqual(got[0]["y"], sd.OBJ_RANGE["y"][0])
        self.assertGreaterEqual(got[0]["w"], sd.OBJ_RANGE["w"][0], "太小的尺寸兜到下限")
        self.assertEqual(got[2]["kind"], "text")

    def test_styles_are_sanitized(self):
        got = sd.clean_objects([{"kind": "text", "color": "红", "fill": "#abc", "size": 999,
                                 "align": "center", "lh": 99, "text": "x" * 900, "bold": 1}])[0]
        self.assertNotIn("color", got, "认不出的颜色丢掉（不猜）")
        self.assertEqual(got["fill"], "#AABBCC", "三位缩写补全成六位大写")
        self.assertEqual(got["size"], 200.0)
        self.assertNotIn("align", got, "只认 start/middle/end")
        self.assertEqual(got["lh"], 3.0)
        self.assertEqual(len(got["text"]), 400)
        self.assertTrue(got["bold"])

    def test_page_cap(self):
        many = [{"kind": "shape", "id": f"o{i}"} for i in range(sd.MAX_OBJECTS + 5)]
        self.assertEqual(len(sd.clean_objects(many)), sd.MAX_OBJECTS)

    def test_new_object_and_next_id(self):
        t = tk.load("tech-editorial")
        o = sd.new_object("shape", "ellipse", theme=t)
        self.assertEqual(o["kind"], "shape")
        self.assertEqual(o["shape"], "ellipse")
        self.assertGreater(o["w"], 0)
        self.assertIn("fill", o, "默认要有填充（瑞士风：纯色、可读）")
        txt = sd.new_object("text", theme=t)
        self.assertEqual(txt["kind"], "text")
        self.assertTrue(txt.get("text"))
        self.assertEqual(sd.next_object_id([o, {"id": "o2"}]), "o3")
        self.assertEqual(sd.next_object_id([]), "o1")

    def test_patch_object(self):
        objs = sd.clean_objects([{"kind": "shape", "id": "o1"}, {"kind": "shape", "id": "o2"}])
        got = sd.patch_object(objs, "o1", {"x": 10, "w": 500, "fill": "#123456"})
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["obj"]["x"], 10.0)
        self.assertEqual(got["obj"]["w"], 500.0)
        self.assertEqual(got["obj"]["fill"], "#123456")
        self.assertEqual(len(got["objects"]), 2, "只改指定的那一个")
        self.assertFalse(sd.patch_object(objs, "nope", {"x": 1})["ok"])
        self.assertFalse(sd.patch_object(objs, "o1", {"w": "abc"})["ok"])
        self.assertFalse(sd.patch_object(objs, "o1", None)["ok"])

    def test_clean_slides_keeps_objects(self):
        s = sd.clean_slides([{"layout": "bullets", "name": "x", "blocks": [{"text": "一"}],
                              "objects": [{"kind": "shape", "id": "o1", "x": 5, "w": 100, "h": 50}]}])[0]
        self.assertEqual(s["objects"][0]["id"], "o1")
        self.assertNotIn("objects", sd.clean_slides([{"layout": "bullets", "name": "x",
                                                      "objects": []}])[0])


class RenderObjectsTest(unittest.TestCase):
    DECK = [{"layout": "bullets", "name": "要点", "blocks": [{"text": "一"}],
             "objects": [{"kind": "shape", "id": "o1", "shape": "rect", "x": 100, "y": 200,
                          "w": 300, "h": 120, "fill": "#DCE6F5", "stroke": "#2F6BFF"},
                         {"kind": "text", "id": "o2", "x": 100, "y": 400, "w": 400, "h": 80,
                          "text": "老师写的字", "size": 28}]}]

    def test_canonical_group_in_svg(self):
        svg = sd.build_deck(THEME, self.DECK, "x", annotate=True)
        page = svg["pages"][0]
        self.assertIn('data-pptx-authoring="preset"', page)
        self.assertIn('data-pptx-prst="rect"', page)
        self.assertIn('data-pptx-object="shape"', page)
        self.assertIn('data-pptx-frame="100 200 300 120"', page)
        self.assertIn('fill="#DCE6F5" stroke="#2F6BFF"', page)
        self.assertIn("M 100 200 L 400 200 L 400 320 L 100 320 Z", page)
        self.assertIn("老师写的字", page)
        slots = [b["slot"] for b in svg["boxes"][0]]
        self.assertIn("obj:o1", slots)
        self.assertIn("obj:o2", slots)
        kinds = {b["slot"]: b["kind"] for b in svg["boxes"][0]}
        self.assertEqual(kinds["obj:o1"], "obj-shape")
        self.assertEqual(kinds["obj:o2"], "obj-text")
        box = [b for b in svg["boxes"][0] if b["slot"] == "obj:o1"][0]
        self.assertEqual((box["x"], box["y"], box["w"], box["h"]), (100.0, 200.0, 300.0, 120.0))

    def test_export_path_also_draws_objects(self):
        """导出那条路（不带 annotate）也必须画出来 —— 否则"预览有、导出没有"。"""
        pages = ppt_svg.render_deck(_T, self.DECK)
        self.assertIn('data-pptx-authoring="preset"', pages[0])
        self.assertNotIn("data-slot=", pages[0], "导出不带打点")

    def test_no_objects_is_byte_identical(self):
        """没插对象时，输出与以前逐字节一致（老数据不受影响）。"""
        a = sd.build_deck(THEME, [{"layout": "bullets", "name": "要点",
                                   "blocks": [{"text": "一"}]}], "x")["pages"][0]
        b = sd.build_deck(THEME, [{"layout": "bullets", "name": "要点",
                                   "blocks": [{"text": "一"}], "objects": []}], "x")["pages"][0]
        self.assertEqual(a, b)

    def test_shape_with_text_gets_two_boxes(self):
        deck = [{"layout": "bullets", "name": "要点", "blocks": [{"text": "一"}],
                 "objects": [{"kind": "shape", "id": "o1", "shape": "roundRect", "x": 50, "y": 60,
                              "w": 300, "h": 120, "text": "框里的字", "size": 24}]}]
        got = sd.build_deck(THEME, deck, "x", annotate=True)
        slots = [b["slot"] for b in got["boxes"][0]]
        self.assertIn("obj:o1", slots)
        self.assertIn("obj:o1:text", slots, "形状上的文字要单独一个盒子，否则会顶掉形状盒子")
        self.assertIn("框里的字", got["pages"][0])

    def test_z_order_is_array_order(self):
        deck = [{"layout": "bullets", "name": "要点", "blocks": [{"text": "一"}],
                 "objects": [{"kind": "shape", "id": "o1", "shape": "rect", "x": 0, "y": 0,
                              "w": 100, "h": 100, "fill": "#111111"},
                             {"kind": "shape", "id": "o2", "shape": "rect", "x": 0, "y": 0,
                              "w": 100, "h": 100, "fill": "#222222"}]}]
        page = sd.build_deck(THEME, deck, "x")["pages"][0]
        self.assertLess(page.index("#111111"), page.index("#222222"), "数组在后的画在上面")


@unittest.skipUnless(PM_READY and PM_SCRIPT.is_file(), "没装 ppt-master：跳过与预设形状目录的对拍")
class GoldenPresetTest(unittest.TestCase):
    """**黄金测试**：我们算的预设路径必须与 ppt-master 的 `preset_shape_svg.py` 一模一样。

    为什么值得花这个时间：形状的规范组是导出时"变成原生可编辑形状"的唯一依据；
    几何一旦悄悄漂开，预览与导出就会不一致 —— 而那种不一致肉眼很难发现。
    """

    FRAMES = ((0.0, 0.0, 300.0, 120.0), (64.0, 88.0, 200.0, 200.0), (10.0, 20.0, 640.0, 90.0))

    def test_every_preset_matches_the_script(self):
        for preset in ppt_svg.SHAPE_PRESETS:
            for (x, y, w, h) in self.FRAMES:
                with self.subTest(preset=preset, frame=(x, y, w, h)):
                    mine = ppt_svg.preset_path(preset, x, y, w, h)
                    theirs = _script_path(preset, x, y, w, h)
                    self.assertTrue(theirs, f"{preset} 没拿到标准答案")
                    self.assertEqual(_norm(mine), _norm(theirs),
                                     f"{preset} @ {(x, y, w, h)} 与 ppt-master 不一致")


def _norm(d: str) -> list[float]:
    """把 `d` 归一成数字串，容忍空格/大小写/小数写法差异。"""
    out = []
    for tok in str(d).replace(",", " ").split():
        try:
            out.append(round(float(tok), 2))
        except ValueError:
            out.append(tok.upper())
    return out


class ObjectEndpointTest(unittest.TestCase):
    def setUp(self):
        self.data = Path(tempfile.mkdtemp(prefix="edu_obj_data_"))
        self._old = os.environ.get("EDU_DATA_DIR")
        os.environ["EDU_DATA_DIR"] = str(self.data)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old is None:
            os.environ.pop("EDU_DATA_DIR", None)
        else:
            os.environ["EDU_DATA_DIR"] = self._old

    def test_add_patch_move_delete(self):
        from edu_agent import web_server as ws

        sid = "obj-ep-1"
        slides = [{"layout": "bullets", "name": "要点", "blocks": [{"text": "一"}]}]
        ws._store.set_session_doc(sid, {"slides": slides, "title": "3.3"})

        add = ws.api_slide_object(ws.SlideObjectReq(session_id=sid, index=0, action="add",
                                                    kind="shape", shape="ellipse", slides=slides))
        self.assertTrue(add["ok"], add)
        self.assertEqual(add["count"], 1)
        oid = add["id"]
        self.assertEqual(add["objects"][0]["shape"], "ellipse")
        self.assertEqual(ws._store.get_session_doc(sid)["slides"][0]["objects"][0]["id"], oid)

        two = ws.api_slide_object(ws.SlideObjectReq(session_id=sid, index=0, action="add",
                                                    kind="text", slides=add["slides"]))
        self.assertEqual(two["count"], 2)
        self.assertNotEqual(two["id"], oid, "新对象的 id 不能撞车")

        pat = ws.api_slide_object(ws.SlideObjectReq(session_id=sid, index=0, action="patch", id=oid,
                                                    patch={"x": 12, "w": 200}, slides=two["slides"]))
        self.assertTrue(pat["ok"], pat)
        self.assertEqual(pat["objects"][0]["x"], 12.0)

        # 层级：数组在后的画在上面 → `up` = 往后挪（更靠上）
        up = ws.api_slide_object(ws.SlideObjectReq(session_id=sid, index=0, action="up", id=oid,
                                                   slides=pat["slides"]))
        self.assertTrue(up["ok"], up)
        self.assertEqual(up["objects"][-1]["id"], oid, "上移一层 = 换到数组后面（画在更上面）")
        again = ws.api_slide_object(ws.SlideObjectReq(session_id=sid, index=0, action="up", id=oid,
                                                      slides=up["slides"]))
        self.assertFalse(again["ok"], "已经最上面了就不该说成功")
        self.assertIn("最上面", again["error"])
        bot = ws.api_slide_object(ws.SlideObjectReq(session_id=sid, index=0, action="down", id=oid,
                                                    slides=up["slides"]))
        self.assertTrue(bot["ok"], bot)
        self.assertEqual(bot["objects"][0]["id"], oid, "下移一层 = 换回数组前面")

        dele = ws.api_slide_object(ws.SlideObjectReq(session_id=sid, index=0, action="delete", id=oid,
                                                     slides=bot["slides"]))
        self.assertTrue(dele["ok"], dele)
        self.assertEqual(dele["count"], 1)
        self.assertEqual(dele["objects"][0]["kind"], "text")

    def test_bad_inputs(self):
        from edu_agent import web_server as ws

        sid = "obj-ep-2"
        slides = [{"layout": "bullets", "name": "要点", "blocks": [{"text": "一"}]}]
        ws._store.set_session_doc(sid, {"slides": slides})
        self.assertEqual(getattr(ws.api_slide_object(ws.SlideObjectReq(
            session_id=sid, index=9, action="add", slides=slides)), "status_code", 200), 400)
        self.assertEqual(getattr(ws.api_slide_object(ws.SlideObjectReq(
            session_id=sid, index=0, action="nope", slides=slides)), "status_code", 200), 400)
        self.assertFalse(ws.api_slide_object(ws.SlideObjectReq(
            session_id=sid, index=0, action="patch", id="ghost", patch={"x": 1},
            slides=slides))["ok"])
        self.assertFalse(ws.api_slide_object(ws.SlideObjectReq(
            session_id=sid, index=0, action="delete", id="ghost", slides=slides))["ok"])

    def test_set_replaces_all(self):
        from edu_agent import web_server as ws

        sid = "obj-ep-3"
        slides = [{"layout": "bullets", "name": "要点", "blocks": [{"text": "一"}],
                   "objects": [{"kind": "shape", "id": "o1", "x": 0, "y": 0, "w": 100, "h": 40}]}]
        ws._store.set_session_doc(sid, {"slides": slides})
        got = ws.api_slide_object(ws.SlideObjectReq(
            session_id=sid, index=0, action="set", slides=slides,
            objects=[{"kind": "text", "id": "t1", "x": 5, "y": 6, "w": 300, "h": 60, "text": "换了"}]))
        self.assertTrue(got["ok"], got)
        self.assertEqual([o["id"] for o in got["objects"]], ["t1"])
        self.assertEqual(got["objects"][0]["text"], "换了")


class ExportObjectsTest(unittest.TestCase):
    """导出真的带过去：同一份 SVG → pptx 里能看到形状与文字（用 python-pptx 读回来）。"""

    def setUp(self):
        self.data = Path(tempfile.mkdtemp(prefix="edu_obj_exp_"))
        self._old = os.environ.get("EDU_DATA_DIR")
        os.environ["EDU_DATA_DIR"] = str(self.data)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old is None:
            os.environ.pop("EDU_DATA_DIR", None)
        else:
            os.environ["EDU_DATA_DIR"] = self._old

    @unittest.skipUnless(PM_READY, "没装 ppt-master：跳过导出")
    def test_export_carries_free_layer(self):
        from pptx import Presentation

        deck = [{"layout": "cover", "title": "自由层"},
                {"layout": "bullets", "name": "一页", "blocks": [{"text": "要点"}],
                 "objects": [{"kind": "shape", "id": "o1", "shape": "rect", "x": 100, "y": 200,
                              "w": 300, "h": 120, "fill": "#DCE6F5", "stroke": "#2F6BFF"},
                             {"kind": "text", "id": "o2", "x": 120, "y": 420, "w": 400, "h": 60,
                              "text": "自由层的字", "size": 28}]}]
        got = sd.export_deck_pptx(THEME, deck, "自由层验收")
        self.assertTrue(got.get("ok"), got.get("error"))
        prs = Presentation(got["file"])
        page = prs.slides[1]
        shapes = list(page.shapes)
        texts = " ".join(s.text_frame.text for s in shapes if s.has_text_frame)
        self.assertIn("自由层的字", texts)
        self.assertIn("要点", texts)

        def auto(s):
            try:
                return s.auto_shape_type            # 文本框不是自选图形，取它会抛 ValueError
            except Exception:                        # noqa: BLE001
                return None

        # 100 设计像素 = 9525 EMU（1px@96dpi）：宽 300px → 2,857,500 EMU
        want_w = int(300 * 9525)
        hit = [s for s in shapes if abs(int(s.width) - want_w) <= 9525 and s.has_text_frame]
        self.assertTrue(hit, f"300px 宽的那个形状没落到 pptx：{[(s.name, int(s.width)) for s in shapes]}")
        # 形状是**原生可编辑**的（不是图片）：要么自选图形，要么任意多边形（有几何、能改）
        self.assertTrue(any(auto(s) is not None or s.shape_type is not None for s in hit),
                        [(s.name, str(s.shape_type)) for s in hit])


if __name__ == "__main__":
    unittest.main()
