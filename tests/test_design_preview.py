# -*- coding: utf-8 -*-
"""R27c 设计规格驱动的渲染与逃生口（单测）。

管什么：
* `POST /api/slide/design/preview`：给一份 `design` → 立刻出预览 SVG（**不落盘**）；
  换视觉风格 / 换主色 / 逐页版式都能立刻见效，`resolved` 与 `warnings` 如实回报；
* `POST /api/slide/deck` 的 `design` 字段：**不传 = 与以前完全一致**（向后兼容）；传了就按规格渲染导出；
* **逃生口** `slides[i].svg_override`：合规的整页 SVG 直接用（预览与导出同一份），
  不合规的一律丢掉（script / 外链 / 非 svg / 超大 / 多根）；
* `design_spec.apply_to_slides()`：逐页版式只覆盖给到的那一页，且**不改动原对象**。
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# 与本仓库其它测试一致的引导：把 src/ 加进来（`unittest discover` 时 cwd 是仓库根，但不保证 PYTHONPATH）
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from edu_agent import design_spec as ds          # noqa: E402
from edu_agent import slide_deck                  # noqa: E402


class ApplyPerPageTest(unittest.TestCase):
    def test_applies_only_given_pages_and_does_not_mutate(self):
        slides = [{"layout": "cover", "name": "A"}, {"layout": "bullets", "name": "B"},
                  {"layout": "end", "name": "C"}]
        r = ds.resolve({"per_page": [{"layout": "hero_statement"}]})
        out = ds.apply_to_slides(slides, r)
        self.assertEqual(out[0]["layout"], "hero_statement")
        self.assertEqual(out[1]["layout"], "bullets")
        self.assertEqual(out[2]["layout"], "end")
        self.assertEqual(slides[0]["layout"], "cover", "不许改到原对象（预览/导出都要用同一份数据）")


class OverrideTest(unittest.TestCase):
    GOOD = ('<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" '
            'viewBox="0 0 1280 720"><rect x="0" y="0" width="1280" height="720" fill="#FFFFFF"/>'
            '<text x="96" y="200" font-size="44" fill="#1A1A1A">agent 自己画的这一页</text></svg>')
    BAD = [
        ('<svg viewBox="0 0 1280 720"><script>alert(1)</script></svg>', "script"),
        ('<svg viewBox="0 0 1280 720"><image href="http://x/y.png"/></svg>', "外链图"),
        ('<div>不是 svg</div>', "不是 svg"),
        ('<svg viewBox="0 0 1280 720"></svg><svg viewBox="0 0 1280 720"></svg>', "多根"),
        ('<svg><rect/></svg>', "缺 viewBox"),
    ]

    def test_good_override_passes(self):
        self.assertTrue(slide_deck.svg_override_of({"svg_override": self.GOOD}))

    def test_bad_overrides_are_dropped(self):
        for raw, why in self.BAD:
            with self.subTest(why=why):
                self.assertEqual(slide_deck.svg_override_of({"svg_override": raw}), "",
                                 f"{why} 应被丢掉")

    def test_clean_slides_keeps_good_override_and_drops_bad(self):
        out = slide_deck.clean_slides([
            {"name": "自由页", "svg_override": self.GOOD},
            {"name": "坏页", "svg_override": self.BAD[0][0]},
        ])
        self.assertIn("svg_override", out[0])
        self.assertNotIn("svg_override", out[1])

    def test_build_deck_uses_override_page_verbatim(self):
        slides = [{"name": "普通页", "bullets": ["一"]},
                  {"name": "自由页", "svg_override": self.GOOD}]
        built = slide_deck.build_deck("tech-editorial", slides, title="逃生口")
        self.assertEqual(len(built["pages"]), 2)
        self.assertIn("agent 自己画的这一页", built["pages"][1])
        self.assertEqual(built["layouts"][1], "svg-override")
        self.assertNotIn("agent 自己画的这一页", built["pages"][0])

    def test_export_accepts_tokens(self):
        """导出要能吃到 design 解析出的 tokens（否则"预览=导出"就破了）。"""
        import inspect

        sig = inspect.signature(slide_deck.export_deck_pptx)
        self.assertIn("tokens", sig.parameters)


class EndpointTest(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient

        from edu_agent import web_server

        self.client = TestClient(web_server.app)
        self.lesson = {"title": "3.3 幂函数", "slides": [
            {"layout": "cover", "name": "3.3 幂函数", "cover_title": "3.3 幂函数",
             "subtitle": "人教A版", "meta": "1 课时"},
            {"layout": "bullets", "name": "情境导入", "minutes": "5",
             "bullets": ["投影五个实例", "追问：这些函数有什么共同点"]},
        ]}

    def _preview(self, design):
        r = self.client.post("/api/slide/design/preview",
                             json={"design": design, "slides": self.lesson["slides"],
                                   "title": "3.3 幂函数"})
        self.assertEqual(r.status_code, 200)
        return r.json()

    def test_preview_returns_pages_and_resolved(self):
        body = self._preview({"visual_style": "ink-wash"})
        self.assertTrue(body["ok"])
        self.assertEqual(body["schema"], "edu-agent.design-preview.v1")
        self.assertEqual(len(body["pages"]), 2)
        self.assertIn("viewBox", body["pages"][0])
        self.assertEqual(body["resolved"]["visual_style"], "ink-wash")
        self.assertEqual(body["resolved"]["palette"]["accent"], "#9E2B25")
        self.assertIn("locked", body)
        self.assertTrue(body["boxes"], "预览默认要带可编辑盒子")

    def test_preview_switching_style_changes_pixels(self):
        a = self._preview({"visual_style": "ink-wash"})["pages"][0]
        b = self._preview({"visual_style": "swiss-minimal"})["pages"][0]
        self.assertNotEqual(a, b, "换视觉风格必须立刻出不同的预览（这正是'可调控'的意思）")
        c = self._preview({"visual_style": "ink-wash",
                           "overrides": {"palette": {"accent": "#002FA7"}}})["pages"][0]
        self.assertNotEqual(a, c, "换主色也要立刻见效")

    def test_preview_per_page_layouts(self):
        body = self._preview({"visual_style": "editorial",
                              "per_page": [{"layout": "hero_statement"}, {"layout": "two_content"}]})
        self.assertEqual(body["layouts"], ["hero_statement", "two_content"])

    def test_preview_reports_clamped_values(self):
        body = self._preview({"visual_style": "swiss-minimal",
                              "overrides": {"density": {"blocks_max": 99}}})
        self.assertEqual(body["resolved"]["density"]["blocks_max"], 8)
        self.assertTrue(any("越界" in w for w in body["warnings"]), body["warnings"])

    def test_preview_falls_back_to_session_then_errors(self):
        """给 slides 就用；不给就回落到会话那份；**两边都没有才 400**（不许 500）。

        ⚠️ 这里**故意不假设"当前会话里一定有教案"**：全量跑时它取决于别的测试跑过什么
        （踩过：单跑绿、全量红）。所以只断言契约本身 —— 200 就带 pages，400 就带清楚的原因。
        """
        r = self.client.post("/api/slide/design/preview",
                             json={"design": {}, "slides": self.lesson["slides"]})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["pages"])

        r2 = self.client.post("/api/slide/design/preview", json={"design": {}})
        self.assertIn(r2.status_code, (200, 400), "回落会话：有就渲染，没有就明确报错，不许 500")
        body = r2.json()
        if r2.status_code == 200:
            self.assertTrue(body["ok"] and body["pages"])
        else:
            self.assertFalse(body["ok"])
            self.assertIn("没有可渲染的页", body["error"])

    def test_preview_without_design_is_backward_compatible(self):
        """不传 design：仍然能出预览（用基准主题），等于今天的行为。"""
        body = self._preview(None)
        self.assertTrue(body["ok"])
        self.assertEqual(body["resolved"]["visual_style"], "")
        self.assertEqual(len(body["pages"]), 2)

    def test_override_page_survives_preview(self):
        slides = [{"layout": "cover", "name": "普通"},
                  {"name": "自由页", "svg_override": OverrideTest.GOOD}]
        r = self.client.post("/api/slide/design/preview",
                             json={"design": {"visual_style": "editorial"}, "slides": slides})
        body = r.json()
        self.assertIn("agent 自己画的这一页", body["pages"][1])
        self.assertEqual(body["layouts"][1], "svg-override")


class SpecAndAuditTest(unittest.TestCase):
    """R27c 的另外两个接口：`GET /api/slide/spec`（内容规格包）与 `POST /api/slide/audit`（自查）。"""

    def setUp(self):
        from fastapi.testclient import TestClient

        from edu_agent import web_server

        self.client = TestClient(web_server.app)

    def test_spec_lists_keys_limits_and_layouts(self):
        r = self.client.get("/api/slide/spec")
        self.assertEqual(r.status_code, 200)
        b = r.json()
        self.assertTrue(b["ok"])
        self.assertEqual(b["schema"], "edu-agent.slide-spec.v1")
        for key in ("layout", "bullets", "blocks", "objects", "svg_override", "notes"):
            self.assertIn(key, b["accepted_keys"])
        self.assertEqual(b["limits"]["chars_per_block"], 400)
        self.assertEqual(b["limits"]["objects_per_page"], 30)
        self.assertIn("cover", b["layouts"]["core"])
        self.assertGreaterEqual(len(b["layouts"]["usable_from_registry"]), 30)
        reg = b["layouts"]["usable_from_registry"]
        self.assertEqual(len(reg), len(set(reg)), "注册表版式要去重（跨族重名只算一次）")
        # 与渲染白名单一致：不该出现"规格包说有、实际渲染不认"的版式
        from edu_agent import slide_deck

        self.assertEqual(set(reg), set(slide_deck.LAYOUTS) - set(b["layouts"]["core"]))
        self.assertEqual(b["layouts"]["total_usable"], len(slide_deck.LAYOUTS))
        self.assertIn("design/preview", b["endpoints"]["preview"])
        self.assertEqual(b["canvas"]["w"], 1280)
        self.assertIn("warnings[]", " ".join(b["must_read_in_response"]))
        self.assertTrue(b["locked"]["preview_equals_export"])

    def test_audit_reports_error_with_fix(self):
        # ⚠️ `clean_slides()` 会把"全空的一页"丢掉：判据是 name/title/bullets/blocks/left 至少有一个
        #    （只有 cover_title/subtitle 的封面页会被当空页丢掉 —— 踩过）。所以封面页也带上 name。
        slides = [{"layout": "cover", "name": "3.3 幂函数", "cover_title": "3.3 幂函数", "subtitle": "人教A版"},
                  {"layout": "bullets", "name": "情境导入",
                   "bullets": ["投影五个实例", "追问：这些函数有什么共同点"]}]
        r = self.client.post("/api/slide/audit", json={"slides": slides})
        self.assertEqual(r.status_code, 200)
        b = r.json()
        self.assertTrue(b["ok"], b.get("errors"))
        self.assertEqual(b["stats"]["errors"], 0)
        self.assertEqual(len(b["pages"]), 2)
        for row in b["pages"]:
            self.assertIn("layout", row)
            for f in row["findings"]:
                self.assertIn("fix", f)

    def test_audit_accepts_design(self):
        slides = [{"layout": "cover", "name": "水墨", "cover_title": "水墨"},
                  {"layout": "bullets", "name": "要点", "bullets": ["一", "二"]}]
        r = self.client.post("/api/slide/audit",
                             json={"slides": slides, "design": {"visual_style": "ink-wash"}})
        b = r.json()
        self.assertTrue(b["ok"], b.get("errors"))
        self.assertTrue(b["theme_id"].endswith("ink-wash"), b["theme_id"])
        self.assertEqual(b["design"]["visual_style"], "ink-wash")

    def test_audit_needs_something_to_audit(self):
        r = self.client.post("/api/slide/audit", json={"slides": [], "session_id": "no-such-xyz"})
        self.assertIn(r.status_code, (200, 400))
        if r.status_code == 400:
            self.assertIn("没有可审的页", r.json()["error"])


if __name__ == "__main__":
    unittest.main()
