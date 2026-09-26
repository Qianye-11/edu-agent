# -*- coding: utf-8 -*-
"""演示模板库（M4b / docs/19 §9）单测：导入、抽取、退化、软删、红线。

用**仓库里的样例 pptx** 当"学校模板"（11 页、Office 默认配色），所以要验的是：
1. 配色/字体/字号真的从模板里读出来，且夹进我们的规范区间（越界要记原因）；
2. 槽位抽得出（角色 + 容量 + 几何）；
3. 抽不出来时**退化成只借配色**，不报错、不硬造；
4. **只写模板库**（原稿只读）；内置模板不可删；
5. `EDU_PPT_TEMPLATES` 语义 = **模板库根**（内置在 `_builtin/`）。
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

from edu_agent import slide_template as st        # noqa: E402
from edu_agent import ppt_tokens as tk            # noqa: E402

SAMPLE_PPTX = _PROJ / "content" / "plans" / "完整课时模板（默认）.pptx"


class _LibCase(unittest.TestCase):
    """每个用例一个临时模板库（内置 3 套复制进去），别动仓库里的真实模板库。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="edu_tpl_")
        self._old = os.environ.get("EDU_PPT_TEMPLATES")
        os.environ["EDU_PPT_TEMPLATES"] = self.tmp.name
        builtin = Path(self.tmp.name) / "_builtin"
        builtin.mkdir(parents=True)
        for f in (_PROJ / "content" / "ppt_templates" / "_builtin").glob("*.json"):
            shutil.copy(f, builtin / f.name)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._old is None:
            os.environ.pop("EDU_PPT_TEMPLATES", None)
        else:
            os.environ["EDU_PPT_TEMPLATES"] = self._old
        self.tmp.cleanup()


class LibraryTest(_LibCase):
    def test_env_var_means_library_root(self):
        self.assertEqual(tk.templates_root(), Path(self.tmp.name))
        self.assertEqual(tk.builtin_dir(), Path(self.tmp.name) / "_builtin")
        self.assertEqual(tk.available(), ["hand-drawn", "illustration", "tech-editorial"])

    def test_list_builtin_and_imported(self):
        items = st.list_templates()
        self.assertEqual([t["kind"] for t in items], ["json", "json", "json"])
        st.import_pptx(SAMPLE_PPTX, name="学校模板")
        items = st.list_templates()
        self.assertEqual(len(items), 4)
        imported = [t for t in items if t["kind"] == "pptx"][0]
        self.assertEqual(imported["name"], "学校模板")
        self.assertGreater(imported["pages"], 5)
        self.assertTrue(imported["ok"])

    def test_index_file_is_written(self):
        st.import_pptx(SAMPLE_PPTX, name="学校模板")
        self.assertTrue(st.index_path().is_file())
        self.assertIn("学校模板", st.index_path().read_text(encoding="utf-8"))


class ImportTest(_LibCase):
    @unittest.skipUnless(SAMPLE_PPTX.is_file(), "需要仓库样例 pptx")
    def test_theme_extracted_from_template(self):
        r = st.import_pptx(SAMPLE_PPTX, name="学校模板")
        self.assertTrue(r["ok"], r)
        tokens = r["theme"]
        self.assertEqual(tokens["palette"]["text"].startswith("#"), True)
        self.assertEqual(tokens["palette"]["accent"].startswith("#"), True)
        self.assertEqual(tokens["canvas"]["bg"].startswith("#"), True)
        self.assertTrue(tokens["fonts"]["ea"])                   # 中文字体一定有兜底
        self.assertEqual(tk.validate(tokens), [], "抽出来的 tokens 必须自己合法")
        # 字号夹进规范区间：要么本来就合法，要么记了原因
        for key, (lo, hi) in {k: tokens["ranges"][k] for k in ("cover", "title", "body", "caption")}.items():
            self.assertGreaterEqual(tokens["type"][key], lo)
            self.assertLessEqual(tokens["type"][key], hi)

    @unittest.skipUnless(SAMPLE_PPTX.is_file(), "需要仓库样例 pptx")
    def test_slots_extracted(self):
        r = st.import_pptx(SAMPLE_PPTX, name="学校模板")
        self.assertTrue(r["slots_ok"], r.get("degraded"))
        slots = st._read_json(Path(r["dir"]) / "slots.json")     # noqa: SLF001
        self.assertGreater(len(slots["slides"]), 5)
        first = slots["slides"][0]
        self.assertTrue(first["slots"], "至少一个文本槽")
        self.assertTrue(all({"role", "capacity", "geo"} <= set(s) for s in first["slots"]))

    @unittest.skipUnless(SAMPLE_PPTX.is_file(), "需要仓库样例 pptx")
    def test_original_is_read_only_copy(self):
        src_mtime = SAMPLE_PPTX.stat().st_mtime
        r = st.import_pptx(SAMPLE_PPTX, name="学校模板")
        self.assertEqual(SAMPLE_PPTX.stat().st_mtime, src_mtime, "原文件不该被碰")
        self.assertTrue((Path(r["dir"]) / "template.pptx").is_file())

    def test_broken_pptx_degrades(self):
        bad = Path(self.tmp.name) / "bad.pptx"
        bad.write_bytes(b"not a pptx at all")
        r = st.import_pptx(bad, name="坏模板")
        self.assertTrue(r["ok"], "坏包也要能收下（退化成只借配色）")
        self.assertFalse(r["slots_ok"])
        self.assertTrue(any("槽" in d or "配色" in d for d in r["degraded"]), r["degraded"])
        self.assertTrue(st.theme_for(r["id"]), "退化后仍要能渲染（用内置兜底）")

    def test_import_does_not_touch_other_dirs(self):
        watch = (_PROJ / "content" / "plans", _PROJ / "data" / "tmp")

        def snap() -> set:
            out = set()
            for d in watch:
                if d.exists():
                    out |= {str(p) for p in d.rglob("*") if p.is_file()}
            return out

        before = snap()
        st.import_pptx(SAMPLE_PPTX, name="学校模板")
        self.assertEqual(before, snap(), "导入只许写模板库")


class ThemeForTest(_LibCase):
    def test_builtin_id_still_works(self):
        self.assertEqual(st.theme_for("tech-editorial")["id"], "tech-editorial")

    def test_unknown_id_is_none(self):
        self.assertIsNone(st.theme_for("不存在的模板"))
        self.assertIsNone(st.theme_for(""))

    @unittest.skipUnless(SAMPLE_PPTX.is_file(), "需要仓库样例 pptx")
    def test_imported_theme_renders_a_deck(self):
        from edu_agent import slide_deck

        r = st.import_pptx(SAMPLE_PPTX, name="学校模板")
        tokens = st.theme_for(r["id"])
        out = slide_deck.build_deck("", slide_deck.SAMPLE_DECK, title="3.3 幂函数", tokens=tokens)
        self.assertEqual(len(out["pages"]), len(slide_deck.SAMPLE_DECK))
        self.assertIn("<svg", out["pages"][0])


class DeleteTest(_LibCase):
    @unittest.skipUnless(SAMPLE_PPTX.is_file(), "需要仓库样例 pptx")
    def test_soft_delete_moves_to_trash(self):
        r = st.import_pptx(SAMPLE_PPTX, name="学校模板")
        d = st.delete_template(r["id"])
        self.assertTrue(d["ok"])
        self.assertIn("_trash", d["moved_to"])
        self.assertIsNone(st.get(r["id"]))
        self.assertTrue(Path(d["moved_to"], "template.pptx").is_file(), "软删 = 还能找回来")

    def test_builtin_cannot_be_deleted(self):
        r = st.delete_template("tech-editorial")
        self.assertFalse(r["ok"])
        self.assertIn("内置", r["error"])


if __name__ == "__main__":
    unittest.main()
