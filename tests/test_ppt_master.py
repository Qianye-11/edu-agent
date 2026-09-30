# -*- coding: utf-8 -*-
"""课件生成桥接层测试（ppt_master / wsl_ppt_bridge 的确定性渲染部分）。

设计（对齐 test_wps_tools 的约定）：**默认全离线** —— 不起 WSL、不联网、不跑引擎。
离线能验的是「渲染出来的 SVG 是否真满足 ppt-master 质量门的机器规则」：
  · XML 良构 + 根 data-pptx-page-role ∈ 合法词表
  · 每个根级 <g> 带 data-pptx-bounds（少了质量门直接报错，接装时踩过）
  · 只使用 spec_lock 里登记过的字号（未登记 → 门报 undeclared font-size）
  · 文本只用 XML 实体转义，不出现 HTML 具名实体（&nbsp; 之类门会报错）
  · cards 最多 3 张、bullets 最多 6 条
真跑引擎 + WSL 的用例统一 skipUnless(EDU_TEST_PPT=1)。

用法：
    python -m unittest discover -s tests -p "test_*.py"
    EDU_TEST_PPT=1 python -m unittest tests.test_ppt_master -v
"""
import importlib.util
import os
import re
import sys
import unittest
from pathlib import Path
from xml.etree import ElementTree as ET

_PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJ / "src"))

from edu_agent import ppt_master                                    # noqa: E402

BRIDGE_PATH = _PROJ / "scripts" / "wsl_ppt_bridge.py"


def _load_bridge():
    """把 WSL 侧桥接脚本当模块加载（它不是包，只能按文件路径加载）。"""
    spec = importlib.util.spec_from_file_location("wsl_ppt_bridge", BRIDGE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


BR = _load_bridge()
SVG_NS = "{http://www.w3.org/2000/svg}"
PAGE_ROLES = {"cover", "toc", "section", "content", "ending"}
# 只列「HTML 具名实体」：XML 自带的 &amp; &lt; &gt; &quot; &apos; 是合法转义，不算违例
HTML_ENTITY = re.compile(r"&(nbsp|mdash|ndash|copy|hellip|ldquo|rdquo|lsquo|rsquo|times|deg);")


def _render_all(canvas="ppt169"):
    """每种版式渲染一页，返回 [(layout, svg_text)]。"""
    w, h = BR.CANVAS[canvas]
    pal = dict(BR.DEFAULT_PALETTE)
    pages = {
        "cover": {"layout": "cover", "title": "标题 & 副标题", "subtitle": "副标题"},
        "section": {"layout": "section", "title": "章节", "subtitle": "说明"},
        "cards": {"layout": "cards", "title": "卡片", "items": [
            {"heading": "A", "body": ["行一", "行二"]},
            {"heading": "B", "body": ["行一"]},
            {"heading": "C"}]},
        "bullets": {"layout": "bullets", "title": "要点", "items": ["一", "二", "三"]},
    }
    return [(k, BR.LAYOUTS[k](p, w, h, pal)) for k, p in pages.items()]


class TestSvgContract(unittest.TestCase):
    """渲染结果必须满足质量门的机器规则（离线可验）。"""

    def test_wellformed_and_page_role(self):
        for layout, svg in _render_all():
            with self.subTest(layout=layout):
                root = ET.fromstring(svg)
                self.assertEqual(root.tag, SVG_NS + "svg")
                self.assertIn(root.get("data-pptx-page-role"), PAGE_ROLES,
                              f"{layout} 的 page-role 不在合法词表内")
                self.assertEqual(root.get("viewBox"), "0 0 1280 720")

    def test_every_root_group_declares_bounds(self):
        """根级 <g> 必须带 data-pptx-bounds，否则质量门报错（本次接装第一次就是栽在这）。"""
        for layout, svg in _render_all():
            root = ET.fromstring(svg)
            groups = [c for c in root if c.tag == SVG_NS + "g"]
            self.assertTrue(groups, f"{layout} 没有任何根级 <g>")
            for g in groups:
                with self.subTest(layout=layout, gid=g.get("id")):
                    bounds = (g.get("data-pptx-bounds") or "").strip()
                    self.assertTrue(bounds, f"{g.get('id')} 缺 data-pptx-bounds")
                    nums = bounds.split()
                    self.assertEqual(len(nums), 4, f"{g.get('id')} bounds 不是 4 个数字：{bounds}")
                    for n in nums:
                        int(n)          # 必须是整数，不能是小数或表达式

    def test_only_declared_font_sizes(self):
        """只用 SIZES 里登记过的字号；未登记的字号会被门判 undeclared font-size。"""
        allowed = {str(v) for v in BR.SIZES.values()}
        for layout, svg in _render_all():
            used = set(re.findall(r'font-size="([^"]+)"', svg))
            with self.subTest(layout=layout):
                lost = used - allowed
                self.assertFalse(lost, f"{layout} 用了未登记字号 {lost}（门会报错）")

    def test_no_html_named_entities(self):
        for layout, svg in _render_all():
            with self.subTest(layout=layout):
                body = svg.split("?>", 1)[-1]
                self.assertIsNone(HTML_ENTITY.search(body), "出现了 HTML 具名实体")

    def test_xml_escaping(self):
        svg = BR.LAYOUTS["cover"]({"layout": "cover", "title": 'A&B <C> "D"'},
                                  1280, 720, dict(BR.DEFAULT_PALETTE))
        ET.fromstring(svg)                       # 良构即说明转义正确
        self.assertIn("A&amp;B &lt;C&gt; &quot;D&quot;", svg)

    def test_item_caps(self):
        svg = BR.LAYOUTS["cards"]({"layout": "cards", "title": "T",
                                  "items": [{"heading": f"H{i}"} for i in range(6)]},
                                  1280, 720, dict(BR.DEFAULT_PALETTE))
        self.assertEqual(svg.count("<g id=\"card-"), 3, "cards 版式最多 3 张")
        svg = BR.LAYOUTS["bullets"]({"layout": "bullets", "title": "T",
                                    "items": [f"第{i}条" for i in range(9)]},
                                    1280, 720, dict(BR.DEFAULT_PALETTE))
        self.assertEqual(svg.count("<circle id=\"bullet-dot-"), 6, "bullets 版式最多 6 条")

    def test_layout_aliases(self):
        self.assertIs(BR.LAYOUTS["title"], BR.LAYOUTS["cover"], "title 是 cover 的别名")
        for name in ("cover", "section", "cards", "bullets"):
            self.assertIn(name, BR.LAYOUTS)


class TestSpecLock(unittest.TestCase):
    """spec_lock 生成：画布 + 全部字号 + schema 首行（门与 validate 都读它）。"""

    def _lock(self, canvas="ppt169"):
        w, h = BR.CANVAS[canvas]
        return BR._lock_md(canvas, dict(BR.DEFAULT_PALETTE),
                           [{"layout": "cover"}, {"layout": "cards"}])

    def test_schema_first_line(self):
        text = self._lock()
        first = next(l for l in text.splitlines() if l.strip())
        self.assertEqual(first, "<!-- ppt-master-schema: spec-lock/v1 -->")
        self.assertIn("# Execution Lock", text)

    def test_canvas_and_sizes_declared(self):
        text = self._lock("ppt169")
        self.assertIn("- viewBox: 0 0 1280 720", text)
        self.assertIn("- format: ppt169", text)
        for role, size in BR.SIZES.items():
            self.assertIn(f"- {role}: {size}", text, f"字号角色 {role} 没登记进 lock")

    def test_page_rhythm_rows_match_pages(self):
        text = self._lock()
        self.assertIn("- P01: anchor", text)     # 封面走 anchor
        self.assertIn("- P02: dense", text)      # 内容页走 dense

    def test_flat_structure_and_forbidden_rows(self):
        text = self._lock()
        self.assertIn("## pptx_structure", text)
        self.assertIn("- mode: flat", text)
        self.assertIn("## forbidden", text)


class TestWindowsSide(unittest.TestCase):
    """Windows 侧转发器的离线断言（不发任何 WSL 调用）。"""

    def test_backend_defaults_to_wsl(self):
        """默认必须是 wsl：本机 native_ok() 会因解释器环境不同而误判（实测踩过）。"""
        old = os.environ.pop("EDU_PPT_BACKEND", None)
        try:
            self.assertEqual(ppt_master.backend(), "wsl")
        finally:
            if old is not None:
                os.environ["EDU_PPT_BACKEND"] = old

    def test_backend_env_override(self):
        old = os.environ.get("EDU_PPT_BACKEND")
        try:
            os.environ["EDU_PPT_BACKEND"] = "native"
            self.assertEqual(ppt_master.backend(), "native")
        finally:
            if old is None:
                os.environ.pop("EDU_PPT_BACKEND", None)
            else:
                os.environ["EDU_PPT_BACKEND"] = old

    def test_generate_rejects_bad_input(self):
        self.assertFalse(ppt_master.generate([])["ok"])
        r = ppt_master.generate([{"layout": "cover", "title": "x"}], canvas="a4")
        self.assertFalse(r["ok"])
        self.assertIn("canvas", r["error"])

    def test_window_path_conversion(self):
        from edu_agent.wsl_docx import to_win, to_wsl

        self.assertEqual(to_wsl(r"D:\hermes\ppt-master"), "/mnt/d/hermes/ppt-master")
        self.assertEqual(to_win("/mnt/d/hermes/ppt-master"), r"D:\hermes\ppt-master")

    def test_engine_root_respects_env(self):
        old = os.environ.get("EDU_PPT_MASTER_ROOT")
        try:
            os.environ["EDU_PPT_MASTER_ROOT"] = r"D:\demo\ppt-master"
            import importlib

            importlib.reload(ppt_master)
            self.assertEqual(str(ppt_master.engine_root()), r"D:\demo\ppt-master")
        finally:
            if old is None:
                os.environ.pop("EDU_PPT_MASTER_ROOT", None)
            else:
                os.environ["EDU_PPT_MASTER_ROOT"] = old
            import importlib

            importlib.reload(ppt_master)


class TestBridgeGeometry(unittest.TestCase):
    """版式几何：卡片不越界、bullet 不越界（离线可验，防止渲染参数改坏）。"""

    def test_cards_within_canvas(self):
        for canvas in BR.CANVAS:
            w, h = BR.CANVAS[canvas]
            items = [{"heading": f"H{i}"} for i in range(3)]
            svg = BR.LAYOUTS["cards"]({"layout": "cards", "title": "T", "items": items},
                                      w, h, dict(BR.DEFAULT_PALETTE))
            xs = [int(m) for m in re.findall(r'<rect id="card-\d-bg" x="(\d+)"', svg)]
            ws = [int(m) for m in re.findall(r'<rect id="card-\d-bg"[^/]*width="(\d+)"', svg)]
            self.assertEqual(len(xs), 3)
            for x, cw in zip(xs, ws):
                self.assertGreaterEqual(x, 0)
                self.assertLessEqual(x + cw, w, f"{canvas}: 卡片越出画布右边界")

    def test_bullets_within_canvas(self):
        w, h = BR.CANVAS["ppt169"]
        svg = BR.LAYOUTS["bullets"]({"layout": "bullets", "title": "T",
                                    "items": [f"第{i}条" for i in range(6)]},
                                    w, h, dict(BR.DEFAULT_PALETTE))
        ys = [int(m) for m in re.findall(r'<text id="bullet-text-\d" x="\d+" y="(\d+)"', svg)]
        self.assertEqual(len(ys), 6)
        self.assertLess(max(ys), h, "要点越出画布下边界")

LIVE = os.getenv("EDU_TEST_PPT") == "1"


@unittest.skipUnless(LIVE, "默认离线：真跑引擎请用 EDU_TEST_PPT=1")
class TestLiveEngine(unittest.TestCase):
    """真链：Windows → WSL → ppt-master 引擎 → 原生 pptx（含质量门）。"""

    def test_generate_and_verify(self):
        ok, why = ppt_master.available()
        self.assertTrue(ok, f"引擎/WSL 未就绪：{why}")
        pages = [{"layout": "cover", "title": "链路自检", "subtitle": "ppt_master"},
                 {"layout": "bullets", "title": "检查项", "items": ["质量门", "原生形状"]}]
        r = ppt_master.generate(pages, name="unittest-deck")
        self.assertTrue(r["ok"], r.get("error"))
        self.assertEqual(r["gate"]["errors"], 0)
        v = ppt_master.verify(r["pptx"])
        self.assertTrue(v["native_editable"], v)
        self.assertGreaterEqual(v["slides"], 2)
        self.assertEqual(v["pictures"], 0)
        self.assertEqual(v["media_files"], 0)


if __name__ == "__main__":
    unittest.main()
