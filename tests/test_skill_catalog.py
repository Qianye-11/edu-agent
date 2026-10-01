# -*- coding: utf-8 -*-
"""DSH 式技能目录（发现 + 按需加载）单元测试。

覆盖：frontmatter 校验（真 YAML / 必填字段 / 严格布尔 / 老技能中文名兜底）、
多根与 rank 优先级、不递归发现、平铺 `<name>.md`、热重载、点名与相关性注入、
以及"用 skills_local 写一个技能 → 目录立刻能发现"的闭环。
"""
import os
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from edu_agent import skill_catalog as sc


def _write(root: Path, name: str, text: str, *, flat: bool = False) -> Path:
    if flat:
        p = root / (name + ".md")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    p = d / "SKILL.md"
    p.write_text(text, encoding="utf-8")
    return p


GOOD = """---
name: mindmap-helper
description: >
  把某一章整理成 Markdown 思维导图。
  当用户说「做个导图」「梳理这一章」时使用。
metadata:
  version: "1.0.0"
---
# mindmap-helper
1. 读教材库对应章节
2. 输出 `- 章节思维导图` 结构
"""


class CatalogTestCase(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1] / ".tmp-skill-catalog"
        base.mkdir(parents=True, exist_ok=True)
        self.tmp = base / ("t-" + uuid.uuid4().hex[:8])
        (self.tmp / "project" / "skills").mkdir(parents=True)
        (self.tmp / "data" / "skills").mkdir(parents=True)
        (self.tmp / "home" / "skills").mkdir(parents=True)
        self._env = {}
        for k, v in (("EDU_SKILLS_DIR", str(self.tmp / "env_skills")),
                     ("EDU_DATA_DIR", str(self.tmp / "data")),
                     ("EDU_AGENT_HOME", str(self.tmp / "home"))):
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        self._patch = mock.patch.object(sc, "_project_root",
                                        return_value=self.tmp / "project")
        self._patch.start()

    def tearDown(self):
        self._patch.stop()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- 便捷 ----
    @property
    def proj(self) -> Path:
        return self.tmp / "project" / "skills"

    @property
    def runtime(self) -> Path:
        return self.tmp / "data" / "skills"

    @property
    def home(self) -> Path:
        return self.tmp / "home" / "skills"


# ════════════════════════════════════════════════════ frontmatter
class TestFrontMatter(CatalogTestCase):
    def test_real_yaml_folded_description(self):
        """DSH 用真 YAML：`description: >` 折叠块必须能读（这也是你已装 skill 的写法）。"""
        _write(self.proj, "mindmap-helper", GOOD)
        rows = sc.catalog()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "mindmap-helper")
        self.assertIn("思维导图", rows[0]["description"])
        self.assertIn("做个导图", rows[0]["description"])       # 折叠块内容都在
        self.assertNotIn(">", rows[0]["description"][:2])

    def test_required_fields(self):
        _write(self.proj, "no-desc", "---\nname: no-desc\n---\n正文\n")
        _write(self.proj, "ok-skill", "---\nname: ok-skill\ndescription: d\n---\n正文\n")
        got = sc.discover()
        self.assertEqual([r["name"] for r in got["entries"]], ["ok-skill"])
        self.assertTrue(any("description" in e["_error"] for e in got["errors"]))

    def test_name_must_be_kebab_case_but_legacy_falls_back(self):
        """老技能把中文名写在 name: 里 → 用目录名当 id（中文名降级为 title），不丢技能。"""
        _write(self.proj, "graph-node-question-draft",
               "---\nname: 图谱节点生成题目草稿\ndescription: 出题\n---\n正文\n")
        rows = sc.catalog()
        self.assertEqual([r["name"] for r in rows], ["graph-node-question-draft"])
        self.assertEqual(rows[0]["title"], "图谱节点生成题目草稿")
        # 目录名也不是 kebab-case 时才算错
        _write(self.proj, "中文目录", "---\nname: 中文名\ndescription: d\n---\n正文\n")
        got = sc.discover()
        self.assertTrue(any("kebab-case" in e["_error"] for e in got["errors"]))

    def test_strict_boolean_drops_whole_skill(self):
        """布尔拼错 → **整个技能被丢弃**（与 DSH 一致，不静默放行）。"""
        _write(self.proj, "bad-flag",
               "---\nname: bad-flag\ndescription: d\ndisable-model-invocation: maybe\n---\n正文\n")
        got = sc.discover()
        self.assertEqual(got["entries"], [])
        self.assertTrue(any("不是合法布尔值" in e["_error"] for e in got["errors"]))

    def test_disable_model_invocation_excluded_from_catalog(self):
        _write(self.proj, "hidden-skill",
               "---\nname: hidden-skill\ndescription: 只给用户用\ndisable-model-invocation: true\n---\n正文\n")
        _write(self.proj, "visible-skill",
               "---\nname: visible-skill\ndescription: d\n---\n正文\n")
        self.assertEqual([r["name"] for r in sc.catalog()], ["visible-skill"])
        self.assertEqual(len(sc.discover()["entries"]), 2)      # 发现阶段仍能看到
        self.assertFalse(sc.load("hidden-skill")["ok"])         # 模型不能加载它


# ════════════════════════════════════════════════════ 发现与优先级
class TestDiscovery(CatalogTestCase):
    def test_rank_precedence(self):
        """同名技能：rank 小的赢（project 100 > runtime 200 > user 400）。"""
        _write(self.proj, "same-name", "---\nname: same-name\ndescription: 来自项目\n---\n项目正文\n")
        _write(self.runtime, "same-name", "---\nname: same-name\ndescription: 来自运行时\n---\n运行时正文\n")
        _write(self.home, "same-name", "---\nname: same-name\ndescription: 来自用户\n---\n用户正文\n")
        rows = sc.catalog()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "project-skills")
        self.assertIn("项目正文", sc.load("same-name")["text"])
        # 被压掉的同名技能会记一条告警（不是静默忽略）
        self.assertTrue(any("同名技能已由 rank" in e["_error"] for e in sc.discover()["errors"]))

    def test_no_nested_discovery(self):
        """刻意不递归 `**/SKILL.md`（与 DSH 一致）。"""
        nested = self.proj / "group" / "inner"
        nested.mkdir(parents=True)
        (nested / "SKILL.md").write_text("---\nname: inner\ndescription: d\n---\n正文\n",
                                         encoding="utf-8")
        self.assertEqual(sc.catalog(), [])

    def test_flat_md_supported(self):
        _write(self.proj, "flat-skill", "---\nname: flat-skill\ndescription: 平铺文件\n---\n正文\n",
               flat=True)
        rows = sc.catalog()
        self.assertEqual([r["name"] for r in rows], ["flat-skill"])

    def test_roots_listing(self):
        got = sc.stats()
        ranks = [r["rank"] for r in got["roots"]]
        self.assertEqual(ranks, sorted(ranks))
        self.assertIn(100, ranks)
        self.assertIn(200, ranks)

    def test_hot_reload_after_edit(self):
        """改正文不用重启：每次 load 现读盘。"""
        p = _write(self.proj, "hot-skill", "---\nname: hot-skill\ndescription: d\n---\n第一版\n")
        self.assertIn("第一版", sc.load("hot-skill")["text"])
        p.write_text("---\nname: hot-skill\ndescription: d\n---\n第二版\n", encoding="utf-8")
        self.assertIn("第二版", sc.load("hot-skill")["text"])

    def test_new_file_discovered_without_restart(self):
        self.assertEqual(sc.catalog(), [])
        _write(self.proj, "brand-new", "---\nname: brand-new\ndescription: 刚写的\n---\n正文\n")
        self.assertEqual([r["name"] for r in sc.catalog()], ["brand-new"])


# ════════════════════════════════════════════════════ 按需加载
class TestLoading(CatalogTestCase):
    def test_load_unknown(self):
        got = sc.load("nope")
        self.assertFalse(got["ok"])
        self.assertIn("未知技能", got["error"])

    def test_explicit_intent(self):
        self.assertEqual(sc.explicit_intent("用技能 mindmap-helper 做一下"), "mindmap-helper")
        self.assertEqual(sc.explicit_intent("按技能 mindmap-helper："), "mindmap-helper")
        self.assertEqual(sc.explicit_intent("帮我看看这题"), "")

    def test_prompt_block_catalog_only_when_irrelevant(self):
        _write(self.proj, "mindmap-helper", GOOD)
        block = sc.prompt_block("今天天气怎么样")
        self.assertIn("mindmap-helper", block)                 # 目录里有
        self.assertNotIn("输出 `- 章节思维导图`", block)          # 正文没注入

    def test_prompt_block_injects_body_when_relevant(self):
        _write(self.proj, "mindmap-helper", GOOD)
        block = sc.prompt_block("帮我做个思维导图，梳理这一章")
        self.assertIn("相关技能 mindmap-helper", block)
        self.assertIn("输出 `- 章节思维导图`", block)             # 正文进来了

    def test_explicit_beats_relevance(self):
        _write(self.proj, "mindmap-helper", GOOD)
        _write(self.proj, "other-skill",
               "---\nname: other-skill\ndescription: 思维导图相关的另一个技能\n---\n另一个正文\n")
        block = sc.prompt_block("用技能 mindmap-helper 做个导图")
        self.assertIn("用户点名使用技能 mindmap-helper", block)
        self.assertIn("输出 `- 章节思维导图`", block)
        self.assertNotIn("另一个正文", block)

    def test_explicit_missing_skill_is_reported(self):
        block = sc.prompt_block("用技能 no-such-skill 做")
        self.assertIn("但它不存在", block)

    def test_body_injection_is_truncated(self):
        long_body = "正文" * 4000
        _write(self.proj, "long-skill",
               "---\nname: long-skill\ndescription: 长正文\n---\n" + long_body)
        block = sc.prompt_block("用技能 long-skill 做", max_chars=200)
        self.assertLess(len(block), 2000)

    def test_coverage_beats_jaccard_for_short_query(self):
        """短问题 vs 长描述：覆盖率才是对的信号（Jaccard 会被长描述稀释）。"""
        long_desc = ("根据 book_id 与章节号读取本地教材库，生成该章节 Markdown 思维导图，"
                     "输出小节标题与页码，供教师备课使用")
        _write(self.proj, "mindmap-long",
               "---\nname: mindmap-long\ndescription: %s\n---\n导图正文\n" % long_desc)
        q = "帮我做个思维导图"
        cov, hits = sc.coverage(q, long_desc)
        self.assertGreaterEqual(cov, 0.3, (cov, hits))
        self.assertGreaterEqual(hits, 3)
        self.assertLess(sc.similarity(q, long_desc), 0.15)      # Jaccard 确实被稀释了
        self.assertEqual([r["name"] for r in sc.relevant(q)], ["mindmap-long"])

    def test_irrelevant_query_matches_nothing(self):
        _write(self.proj, "mindmap-helper", GOOD)
        self.assertEqual(sc.relevant("今天天气怎么样"), [])

    def test_title_participates_in_relevance(self):
        """skills_local 写的中文 title 也要参与相关性（那是它最好认的名字）。"""
        _write(self.proj, "graph-node-question-draft",
               "---\nname: graph-node-question-draft\ntitle: 图谱节点生成题目草稿\n"
               "description: 按图谱节点出题\n---\n正文\n")
        self.assertEqual([r["name"] for r in sc.relevant("帮我按图谱节点生成一道题目")],
                         ["graph-node-question-draft"])


# ════════════════════════════════════════════════════ 与写入路径的闭环
class TestWriteThenDiscover(CatalogTestCase):
    def test_skills_local_write_is_discovered(self):
        """闭环：`skills_local.save_skill()` 写出来 → 目录立刻发现（含中文名的老写法）。"""
        from edu_agent import skills_local

        r = skills_local.save_skill("", "图谱节点生成题目草稿", "按图谱节点出题，带解析",
                                    "# 步骤\n1. 读节点\n2. 出题")
        self.assertTrue(r["ok"], r)
        rows = sc.catalog()
        names = [x["name"] for x in rows]
        self.assertIn(r["id"], names, rows)
        entry = [x for x in rows if x["name"] == r["id"]][0]
        self.assertEqual(entry["title"], "图谱节点生成题目草稿")     # 中文名进了 title
        self.assertIn("出题", entry["description"])
        self.assertIn("读节点", sc.load(r["id"])["text"])
        # skills_local 的显示名不受影响（仍显示中文名）
        self.assertEqual(skills_local.list_skills()[0]["name"], "图谱节点生成题目草稿")

    def test_draft_area_is_never_discovered(self):
        """模块3 的草稿区不在任何扫描根里 —— 草稿永远进不了目录（红线）。"""
        from edu_agent.skill_market import paths

        d = paths.drafts_dir() / "should-not-appear"
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text("---\nname: should-not-appear\ndescription: 草稿\n---\n正文\n",
                                    encoding="utf-8")
        try:
            names = [r["name"] for r in sc.catalog()]
            self.assertNotIn("should-not-appear", names)
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
