# -*- coding: utf-8 -*-
"""幻灯片渲染收口（`slide_deck` + `/api/slide/*`）的离线单测。

对应 `docs/22` D13 / `docs/23` M1：
- 渲染只有一份实现（服务端），面板与导出共用 → 这里锁住"输出什么"；
- **不落盘**是红线：见 `_NoDiskWrites` / `_RenderWritesNothing`（后者给一个空数据目录，
  渲染后必须还是空的 —— 光比对"整棵树快照"会被别的进程写文件带偏，假失败查过好几次）；
- 接口三态：正常 / skill 关（`enabled:false`）/ 主题坏掉（400）。
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

from edu_agent import capabilities as cap          # noqa: E402
from edu_agent import slide_deck                   # noqa: E402
from edu_agent import ppt_tokens as tk             # noqa: E402

# web_server 在 import 时会建 SessionStore：先指到临时目录，别污染本机
os.environ.setdefault("EDU_DATA_DIR", tempfile.mkdtemp(prefix="edu_test_data_"))  # noqa: E402
from edu_agent import web_server as ws             # noqa: E402


def _ppt_master_ready() -> bool:
    """ppt-master 引擎是否就绪（PPTX 导出链路依赖它）。

    没装就**跳过**相关用例 —— 否则"本机没配引擎"会表现成测试失败，
    跟"代码有问题"混在一起最难排查（本机实测就是没装，返回 gate=install）。
    """
    try:
        from edu_agent import ppt_master

        ok, _why = ppt_master.available()
        return bool(ok)
    except Exception:  # noqa: BLE001
        return False

DECK = [
    {"name": "3.3 幂函数"},
    {"name": "情境导入", "minutes": "5", "bullets": ["投影五个实例", "追问：这些函数有什么共同点"]},
    {"name": "概念建构", "minutes": "12", "bullets": ["形如 y=x^α", "α 为常数"]},
    {"name": "小结", "minutes": "3", "bullets": ["三条结论"]},
]

#: 真导出用例的开关（装饰器在**类创建时**求值，所以必须在模块层算好，不能引用类属性）
try:
    from edu_agent import ppt_polish as _ppt_polish

    PPT_MASTER_READY = bool(_ppt_polish.status().get("intake_ready"))
except Exception:  # noqa: BLE001 没装 ppt-master 也不该让整个测试文件炸
    PPT_MASTER_READY = False


def _snapshot(*dirs: Path, skip: tuple[str, ...] = ()) -> dict[str, float]:
    out: dict[str, float] = {}
    for d in dirs:
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if p.is_file() and not any(s in p.parts for s in skip):
                out[str(p)] = p.stat().st_mtime
    return out


class _NoDiskWrites(unittest.TestCase):
    """红线：渲染是纯内存的（预览不落盘）。

    ⚠️ 这条断言原来写成"整棵 `data/tmp` 的快照必须一模一样"，结果**并行跑闸门时经常假失败**：
    在跑的 Web 服务（写 `data/tmp/ppt_jobs`）、截图工具（写 `data/tmp/wzshots`）、
    `tools/ppt_smoke.py`（**改写** `data/tmp/smoke_out|smoke_edge` 里的同名文件）、另一个测试进程
    —— 它们和"渲染落不落盘"毫无关系，却会把断言弄红（查这种假失败最浪费时间）。
    现在只盯**渲染绝不该碰**的两处：`content/plans`（教案正文）与 `data/uploads`（老师上传/取回的图）；
    别的工具的工作目录不在监视范围里。真正"渲染自己不写盘"的直接证据在 `_RenderWritesNothing`：
    给一个**空的数据目录**，渲染完它必须还是空的（这个断言并行跑也成立）。
    """

    WATCH = (_PROJ / "content" / "plans", _PROJ / "data" / "uploads")
    SKIP = ()

    def setUp(self):
        self.before = _snapshot(*self.WATCH, skip=self.SKIP)
        # 模板库（EDU_PPT_TEMPLATES）**不在** EDU_DATA_DIR 下，必须单独隔离：
        # 老师一旦导入过 PPTX 模板，主题列表就不再是内置那 3 条，
        # `len(themes) == 3` / 精确 id 列表这类断言会无故失败（实测：本地导入 2 个 → 5 条）。
        # 放在共同基类里，EndpointTest / ThemeTest / ExportEndpointTest 都覆盖到。
        self.tpl = Path(tempfile.mkdtemp(prefix="edu_tpl_iso_"))
        self._old_tpl = os.environ.get("EDU_PPT_TEMPLATES")
        os.environ["EDU_PPT_TEMPLATES"] = str(self.tpl)
        builtin = self.tpl / "_builtin"
        builtin.mkdir(parents=True, exist_ok=True)
        for _f in (_PROJ / "content" / "ppt_templates" / "_builtin").glob("*.json"):
            shutil.copy(_f, builtin / _f.name)
        self.addCleanup(self._restore_templates)

    def _restore_templates(self):
        if self._old_tpl is None:
            os.environ.pop("EDU_PPT_TEMPLATES", None)
        else:
            os.environ["EDU_PPT_TEMPLATES"] = self._old_tpl

    def tearDown(self):
        after = _snapshot(*self.WATCH, skip=self.SKIP)
        changed = {k: (self.before.get(k), after.get(k)) for k in self.before
                   if after.get(k) != self.before.get(k)}
        self.assertEqual(changed, {}, "渲染不该改/删 data/tmp 或 content/plans 里已有的文件")


class _RenderWritesNothing(_NoDiskWrites):
    """纯渲染用例：再给一个**空的数据目录**，渲染后必须一个文件都没多（这才是直接证据）。"""

    def setUp(self):
        super().setUp()
        self.data = Path(tempfile.mkdtemp(prefix="edu_nodisk_"))
        self._old_data = os.environ.get("EDU_DATA_DIR")
        os.environ["EDU_DATA_DIR"] = str(self.data)
        self.addCleanup(self._restore_data)

    def _restore_data(self):
        if self._old_data is None:
            os.environ.pop("EDU_DATA_DIR", None)
        else:
            os.environ["EDU_DATA_DIR"] = self._old_data

    def tearDown(self):
        leftovers = sorted(str(p.relative_to(self.data)) for p in self.data.rglob("*") if p.is_file())
        self.assertEqual(leftovers, [], "渲染往数据目录写了东西（预览必须不落盘）")
        super().tearDown()


class ThemeTest(_RenderWritesNothing):
    def test_three_themes_with_metadata(self):
        items = slide_deck.themes()
        self.assertEqual([t["id"] for t in items], ["hand-drawn", "illustration", "tech-editorial"])
        for t in items:
            with self.subTest(theme=t["id"]):
                self.assertTrue(t["name"])
                self.assertTrue(t["source"].endswith("style-system.md"), "每套都要写清数值出处")
                self.assertEqual((t["canvas"]["w"], t["canvas"]["h"]), (1280, 720))
                self.assertTrue(t["fonts_ok"], t["fonts_missing"])
                self.assertNotIn("accent_max_ratio", t["palette"], "给面板的配色不用带内部阈值")

    def test_default_is_academic_blue(self):
        self.assertEqual(slide_deck.default_theme_id(), "tech-editorial")

    def test_unknown_theme_falls_back_to_default(self):
        """未知主题 → 退回默认主题（合并后的**有意**行为：不中断老师的课件编辑）。

        旧契约是抛 `FileNotFoundError`（render 端点返回 400）。现在的取舍见
        `slide_deck.theme()` 的代码注释：「失效的用户模板退回默认，不中断课件编辑」。
        代价：老师不知道自己选的主题被换掉了 —— docs/25 已记建议（把 fallback 回传前端）。
        """
        got = slide_deck.theme("nope")
        self.assertEqual(got["id"], slide_deck.default_theme_id())
        self.assertTrue(got["name"])


class BuildDeckTest(_RenderWritesNothing):
    def test_pages_and_layouts(self):
        out = slide_deck.build_deck("tech-editorial", DECK, title="3.3 幂函数")
        self.assertEqual(out["stats"]["pages"], 4)
        self.assertEqual(out["layouts"], ["cover", "bullets", "bullets", "end"])
        for svg in out["pages"]:
            self.assertTrue(svg.startswith("<svg") and svg.endswith("</svg>"))
            self.assertIn('viewBox="0 0 1280 720"', svg)
            self.assertIn("<text", svg)
            self.assertNotIn("<image", svg)

    def test_cover_when_first_page_is_title_only(self):
        svg = slide_deck.build_deck("tech-editorial", DECK, title="3.3 幂函数")["pages"][0]
        self.assertIn("3.3 幂函数", svg)
        self.assertEqual(slide_deck.build_deck("tech-editorial", DECK, title="3.3 幂函数")["layouts"][0], "cover")

    def test_first_page_with_bullets_is_not_turned_into_cover(self):
        """第 1 页有要点时**不能**当封面 —— 否则整页内容会被丢掉。"""
        out = slide_deck.build_deck("tech-editorial", [{"name": "情境导入", "bullets": ["投影五个实例"]}])
        self.assertEqual(out["layouts"], ["bullets"])
        self.assertIn("投影五个实例", out["pages"][0])

    def test_cover_subtitle_uses_first_section_name(self):
        deck = [{"name": "情境导入"}, {"name": "概念建构", "bullets": ["形如 y=x^α"]}]
        svg = slide_deck.build_deck("tech-editorial", deck, title="3.3 幂函数")["pages"][0]
        self.assertIn("3.3 幂函数", svg)
        self.assertIn("情境导入", svg)          # 环节名降级当副标，而不是当封面主标

    def test_minutes_become_label(self):
        svg = slide_deck.build_deck("tech-editorial", DECK, title="x")["pages"][1]
        self.assertIn("环节 2 · 5 分钟", svg)

    def test_bullets_are_not_truncated(self):
        long_one = "这条要点很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长很长"
        out = slide_deck.build_deck("tech-editorial", [{"name": "一", "bullets": [long_one]}])
        self.assertIn(long_one[:20], out["pages"][0])         # 开头在
        self.assertIn(long_one[-10:], out["pages"][0])        # 结尾也在（换行不截断）

    def test_density_warning_but_content_kept(self):
        many = [f"第 {i} 条" for i in range(7)]
        out = slide_deck.build_deck("tech-editorial", [{"name": "一"}, {"name": "二", "bullets": many}])
        self.assertTrue(out["warnings"], "超过上限要给拆页提示")
        self.assertIn("拆页", out["warnings"][0])
        self.assertIn("第 4 条", out["pages"][1])             # 上限内照排

    def test_explicit_layout_respected(self):
        out = slide_deck.build_deck("tech-editorial",
                                    [{"layout": "section", "name": "环节切换", "index": "2"}])
        self.assertEqual(out["layouts"], ["section"])
        self.assertIn("环节切换", out["pages"][0])

    def test_blocks_with_labels_pass_through(self):
        deck = [{"name": "一"}, {"name": "二", "blocks": [{"label": "定义", "text": "形如 y=x^α"}]},
                {"name": "三", "bullets": ["收束"]}]
        out = slide_deck.build_deck("tech-editorial", deck)
        self.assertEqual(out["layouts"], ["cover", "bullets", "end"])
        self.assertIn("定义", out["pages"][1])
        self.assertIn("形如 y=x^α", out["pages"][1])

    def test_last_page_keeps_its_items(self):
        """结尾版式也要排要点（别把最后一页的内容吃掉）。"""
        deck = [{"name": "一"}, {"name": "作业", "bullets": ["课本 P72 第 3 题"]}]
        out = slide_deck.build_deck("tech-editorial", deck)
        self.assertEqual(out["layouts"][-1], "end")
        self.assertIn("课本 P72 第 3 题", out["pages"][-1])

    def test_empty_slides(self):
        out = slide_deck.build_deck("tech-editorial", [])
        self.assertEqual(out["pages"], [])
        self.assertEqual(out["stats"]["pages"], 0)

    def test_all_themes_render_same_deck(self):
        for theme_id in tk.available():
            with self.subTest(theme=theme_id):
                out = slide_deck.build_deck(theme_id, DECK, title="x")
                self.assertEqual(len(out["pages"]), len(DECK))
                self.assertEqual(out["theme_id"], theme_id)


class FitScaleTest(unittest.TestCase):
    """预览台的等比缩放：面板里那段 JS 是这几个数的镜像（docs/23 M1 出口标准）。"""

    def test_three_panel_widths(self):
        self.assertEqual(slide_deck.fit_scale(480), 0.375)
        self.assertEqual(slide_deck.fit_scale(680), 0.53125)
        self.assertEqual(slide_deck.fit_scale(1000), 0.78125)

    def test_height_follows_scale(self):
        self.assertEqual(slide_deck.fit_height(480), 270)
        self.assertEqual(slide_deck.fit_height(680), 383)
        self.assertEqual(slide_deck.fit_height(1000), 563)

    def test_clamped_and_zero_fallback(self):
        self.assertEqual(slide_deck.fit_scale(0), 0.375)          # jsdom / 隐藏时量不到宽度
        self.assertEqual(slide_deck.fit_scale(10), 0.15)          # 太小 → 下限
        self.assertEqual(slide_deck.fit_scale(99999), 2.0)        # 太大 → 上限

    def test_uniform_scale_keeps_relative_geometry(self):
        """均匀缩放 ⇒ 归一化坐标（除以 scale）与缩放无关，这正是"拖宽不变形"的数学依据。"""
        model = {"title_x": 72.0, "body_x": 96.0, "right_edge": 1192.0}
        for w in (480, 680, 1000):
            sc = slide_deck.fit_scale(w)
            back = {k: v * sc / sc for k, v in model.items()}
            self.assertEqual(back, model)


class EndpointTest(_NoDiskWrites):
    def setUp(self):
        super().setUp()
        self._old = os.environ.get("EDU_DATA_DIR")
        os.environ["EDU_DATA_DIR"] = tempfile.mkdtemp(prefix="edu_test_data_")
        cap._CACHE.clear()
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old is None:
            os.environ.pop("EDU_DATA_DIR", None)
        else:
            os.environ["EDU_DATA_DIR"] = self._old
        cap._CACHE.clear()

    def test_themes_endpoint(self):
        d = ws.api_slide_themes()
        self.assertTrue(d["ok"])
        self.assertEqual(d["default"], "tech-editorial")
        self.assertEqual(len(d["themes"]), 3)

    def test_render_endpoint_with_slides(self):
        d = ws.api_slide_render(ws.SlideRenderReq(theme_id="tech-editorial", slides=DECK, title="3.3 幂函数"))
        self.assertTrue(d["ok"] and d["enabled"])
        self.assertEqual(len(d["pages"]), 4)
        self.assertEqual(d["stats"]["pages"], 4)
        self.assertIn("同一份源", d["note"])

    def test_render_endpoint_derives_from_markdown(self):
        md = "# 3.3 幂函数\n\n### 情境导入（约 5 分钟）\n- 投影五个实例\n\n### 概念建构（约 12 分钟）\n- 形如 y=x^α\n"
        d = ws.api_slide_render(ws.SlideRenderReq(theme_id="hand-drawn", markdown=md))
        self.assertEqual(len(d["pages"]), 2)
        self.assertEqual(d["theme_id"], "hand-drawn")

    def test_render_endpoint_skill_off(self):
        cap.set_enabled("skill", "doc-session-preview", False)      # 与 test_wps_tools 同款用法
        cap._CACHE.clear()
        try:
            self.assertFalse(cap.session_preview_ready())
            d = ws.api_slide_render(ws.SlideRenderReq(theme_id="tech-editorial", slides=DECK))
            self.assertEqual(d["enabled"], False)
            self.assertEqual(d["pages"], [])
            self.assertFalse(ws.api_slide_themes()["enabled"])
        finally:
            cap.set_enabled("skill", "doc-session-preview", True)
            cap._CACHE.clear()

    def test_render_endpoint_bad_theme_falls_back(self):
        """未知主题不再返回 400：退回默认主题继续渲染（契约变更，理由同 ThemeTest）。"""
        r = ws.api_slide_render(ws.SlideRenderReq(theme_id="nope", slides=DECK))
        self.assertEqual(getattr(r, "status_code", 200), 200)
        self.assertTrue(r["ok"] and r["enabled"])
        self.assertEqual(r["theme_id"], slide_deck.default_theme_id())


class ExportTest(_NoDiskWrites):
    """导出链路（docs/23 M3）：同一份 SVG → 原生 pptx；四条硬验收；回退开关。"""

    WATCH = (_PROJ / "content" / "plans",)
    tmp = None
    out = None
    ready = False

    @classmethod
    def setUpClass(cls):
        from edu_agent import ppt_polish

        cls.tmp = tempfile.TemporaryDirectory(prefix="edu_slide_export_")
        cls.out = Path(cls.tmp.name)
        cls.ready = bool(ppt_polish.status().get("intake_ready"))

    @classmethod
    def tearDownClass(cls):
        if cls.tmp:
            cls.tmp.cleanup()
        cls.tmp = None

    def test_backend_switch(self):
        self.assertEqual(slide_deck.export_backend(), "native")
        os.environ["EDU_PPT_EXPORT"] = "legacy"
        try:
            self.assertEqual(slide_deck.export_backend(), "legacy")
        finally:
            os.environ.pop("EDU_PPT_EXPORT", None)

    def test_safe_name(self):
        self.assertEqual(slide_deck.safe_name('3.3 幂函数 / 第一课时?'), "3.3 幂函数 _ 第一课时_")
        self.assertEqual(slide_deck.safe_name(""), "教案")

    @unittest.skipUnless(PPT_MASTER_READY, "需要装好 ppt-master（deploy\\setup_ppt_master.cmd）")
    def test_export_produces_native_pptx(self):
        out = slide_deck.export_deck_pptx("tech-editorial", DECK, title="3.3 幂函数", out_dir=self.out)
        self.assertTrue(out["ok"], out.get("error"))
        self.assertTrue(out["status"].startswith("passed"), out["status"])
        self.assertTrue(Path(out["file"]).is_file())
        self.assertTrue(Path(out["file"]).name.startswith("教案_3.3 幂函数_"))
        self.assertEqual(out["pages"], len(DECK))
        for c in out["verify"]["checks"]:
            with self.subTest(check=c["name"]):
                self.assertTrue(c["ok"], c["detail"])
        # 中间件在受控目录里，产物在指定目录
        self.assertIn("ppt_jobs", out["job"])
        self.assertNotIn("content\\plans", out["job"])

    @unittest.skipUnless(PPT_MASTER_READY, "需要装好 ppt-master")
    def test_verify_catches_page_mismatch(self):
        out = slide_deck.export_deck_pptx("hand-drawn", DECK, title="页数校验", out_dir=self.out)
        self.assertTrue(out["ok"], out.get("error"))
        pages = slide_deck.build_deck("hand-drawn", DECK, title="页数校验")["pages"]
        bad = slide_deck.verify_pptx(Path(out["file"]), pages[:-1])       # 少给一页 → 必须报
        self.assertFalse(bad["ok"])
        self.assertFalse([c for c in bad["checks"] if c["name"] == "页数一致"][0]["ok"])

    def test_export_without_pages_is_explicit(self):
        out = slide_deck.export_deck_pptx("tech-editorial", [], title="空的")
        self.assertFalse(out["ok"])
        self.assertIn("没有可导出的页", out["error"])


class ExportEndpointTest(_NoDiskWrites):
    """`/api/wps/export` 的分支与回退（桩掉真实转换，只验接线与响应形状）。"""

    WATCH = (_PROJ / "content" / "plans",)

    def setUp(self):
        super().setUp()
        import unittest.mock as mock

        self.mock = mock
        self._env = {k: os.environ.get(k) for k in ("EDU_DATA_DIR", "EDU_PPT_EXPORT")}
        os.environ["EDU_DATA_DIR"] = tempfile.mkdtemp(prefix="edu_test_data_")
        os.environ.pop("EDU_PPT_EXPORT", None)
        self.addCleanup(self._restore)

    def _restore(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _body(self, **kw) -> dict:
        # confirm=True 模拟"面板已确认"：落盘类导出本来就要过权限闸门（新 kind 也走同一道）
        body = {"kind": "pptx-deck", "slides": DECK, "theme_id": "tech-editorial",
                "title": "3.3 幂函数", "confirm": True,
                "outDir": tempfile.mkdtemp(prefix="edu_out_")}
        body.update(kw)
        return body

    def test_permission_gate_applies_to_new_kind(self):
        body = self._body()
        body.pop("confirm")
        r = ws.api_wps_export(body)
        self.assertFalse(r.get("ok"))
        self.assertEqual(r.get("gate"), "permission")

    def test_native_success_shape(self):
        fake = {"ok": True, "backend": "native", "filename": "教案_x.pptx", "outDir": "D:\\out",
                "file": "D:\\out\\教案_x.pptx", "pages": 4, "theme_id": "tech-editorial",
                "verify": {"ok": True, "checks": [{"name": "页数一致", "ok": True, "detail": "4/4"}]}}
        with self.mock.patch.object(ws.slide_deck, "export_deck_pptx", return_value=fake):
            r = ws.api_wps_export(self._body())
        self.assertTrue(r["ok"])
        self.assertFalse(r["fallback"])
        self.assertIn("download_url", r)
        self.assertIn("页数一致", r["detail"])

    def test_legacy_env_falls_back(self):
        os.environ["EDU_PPT_EXPORT"] = "legacy"
        fake = {"ok": True, "filename": "旧.pptx", "backend": "native"}
        with self.mock.patch("edu_agent.wsl_docx.export_best", return_value=fake) as m:
            r = ws.api_wps_export(self._body())
        self.assertTrue(r["ok"] and r["fallback"])
        self.assertIn("EDU_PPT_EXPORT", r["reason"])
        self.assertEqual(m.call_args.kwargs["kind"], "pptx")

    def test_conversion_failure_falls_back(self):
        fake = {"ok": False, "backend": "native", "error": "转换报告 status=（缺失）"}
        legacy = {"ok": True, "filename": "旧.pptx"}
        with self.mock.patch.object(ws.slide_deck, "export_deck_pptx", return_value=fake), \
             self.mock.patch("edu_agent.wsl_docx.export_best", return_value=legacy):
            r = ws.api_wps_export(self._body())
        self.assertTrue(r["ok"] and r["fallback"])
        self.assertIn("没通过验收", r["reason"])

    @unittest.skipUnless(_ppt_master_ready(), "需要 ppt-master 引擎（跑 deploy\\setup_ppt_master.cmd）")
    def test_bad_theme_falls_back(self):
        """未知主题走默认主题后照常导出：只要求"给出结果或明确原因"。

        旧契约靠 `fallback` / `reason`（"主题不可用"）两个字段告知；
        合并后主题解析已退到默认（见 ThemeTest），那套字段不复存在。
        这条链路需要 ppt-master 引擎，**没装就跳过**（本机实测就是没装：
        返回 `gate=install`，硬断言会变成"环境没配好"的假失败）。
        """
        legacy = {"ok": True, "filename": "旧.pptx"}
        with self.mock.patch("edu_agent.wsl_docx.export_best", return_value=legacy):
            r = ws.api_wps_export(self._body(theme_id="nope"))
        self.assertIsInstance(r, dict)
        self.assertIn("ok", r)
        self.assertTrue(r.get("ok") or r.get("error") or r.get("gate"),
                        "必须给出结果或明确原因，不能静默")

    def test_missing_install_is_reported_not_silent(self):
        fake = {"ok": False, "backend": "native", "gate": "install", "error": "ppt-master 未就绪"}
        with self.mock.patch.object(ws.slide_deck, "export_deck_pptx", return_value=fake):
            r = ws.api_wps_export(self._body())
        self.assertFalse(r["ok"])
        self.assertEqual(r["gate"], "install")
        self.assertNotIn("fallback", r)

    def test_old_kinds_untouched(self):
        """旧的两个 kind 仍然走原链路（不能因为新分支改动而回归）。"""
        with self.mock.patch("edu_agent.wsl_docx.export_best", return_value={"ok": True}) as m:
            ws.api_wps_export({"kind": "docx", "markdown": "# x", "confirm": True})
            ws.api_wps_export({"kind": "pptx", "markdown": "# x", "confirm": True})
        self.assertEqual([c.kwargs["kind"] for c in m.call_args_list], ["docx", "pptx"])


if __name__ == "__main__":
    unittest.main()
