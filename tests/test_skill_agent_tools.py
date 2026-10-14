# -*- coding: utf-8 -*-
"""`skill_market.agent_tools` 的单元测试 —— 聊天技能快路与工具接线。

覆盖：
  * 已安装且可加载的技能才会进工具列表（草稿/停用不进）；
  * 工具名与参数按 inputs_spec 正确生成（参数全部可选）；
  * 快路命中矩阵：生成类请求命中、越界章节拦截、问答题/问候**不**劫持；
  * 章节号越界守卫（教材库对不存在的章节会静默回退到第一章，必须拦住）。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_PROJ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PROJ / "src"))

_TMP = Path(tempfile.mkdtemp(prefix="edu_agent_tools_"))
# ⚠️ import 期只做**无破坏**的默认值（与其它测试文件一致）：
# 早先这里无条件覆盖 EDU_DATA_DIR 且不还原，结果把它后面的测试文件（如 test_template_rich）
# 指到一个已被删掉的临时目录 → 出现"跟我改动无关"的 FileNotFoundError。
# 真正的隔离放在 setUp 里做，并在 cleanup 里**逐项还原**。
os.environ.setdefault("EDU_DATA_DIR", str(_TMP / "data"))

from edu_agent.skill_market import (agent_tools, lifecycle, manifest as mf,  # noqa: E402
                                   packager, session_bootstrap, skill_studio, store)

_ENV_KEYS = ("EDU_SKILL_MARKET_DIR", "EDU_SKILL_RUNTIME_DIR", "EDU_DATA_DIR",
             "EDU_SKILL_REGISTRY_DB", "EDU_AGENT_HOME")

DRAFT = """---
name: mindmap-probe
title: 教材章节思维导图（测试桩）
description: 读取本地教材库指定课本的某一章，生成 Markdown 思维导图（测试用）
owner: teacher-A
plugin: plugin.py
intents: 思维导图, 导图
allowed_capabilities: ["textbook:read"]
data_scope: ["teaching_context"]
inputs_spec: [{"name": "book_id", "type": "string", "description": "教材库里的书本 id"}, {"name": "chapter_no", "type": "integer", "description": "章节序号"}]
outputs: ["ok", "markdown"]
tests:
  - input: {chapter_no: 2}
    expect_contains: "- 章节思维导图"
    expect_fields: ["ok", "markdown"]
---
# mindmap-probe

## 功能

读教材库，出导图。
"""

PLUGIN = """from semantic_kernel.functions import kernel_function


class MindmapProbe:
    @kernel_function(name="generate_mindmap", description="生成章节思维导图")
    def generate_mindmap(self, book_id: str = "", chapter_no: int = 0) -> dict:
        return {"ok": True, "markdown": "- 章节思维导图\\n  - 第 %s 章" % chapter_no}
"""


class AgentToolsTestCase(unittest.TestCase):
    def setUp(self):
        # 记下原值，逐项还原 —— 否则会污染后面跑的测试文件（吃过这个亏）
        self._saved = {k: os.environ.get(k) for k in _ENV_KEYS}
        self._saved_single = os.environ.get("EDU_SINGLE_USER_MODE")
        self.addCleanup(self._restore_env)
        for d in ("market", "runtime", "registry.db", "data"):
            p = _TMP / d
            if p.is_dir():
                shutil.rmtree(p, ignore_errors=True)
            elif p.exists():
                p.unlink()
        os.environ.update({
            "EDU_SKILL_MARKET_DIR": str(_TMP / "market"),
            "EDU_SKILL_RUNTIME_DIR": str(_TMP / "runtime"),
            "EDU_DATA_DIR": str(_TMP / "data"),
            "EDU_SKILL_REGISTRY_DB": str(_TMP / "registry.db"),
            "EDU_AGENT_HOME": str(_TMP / "home"),
        })
        os.environ.pop("EDU_SINGLE_USER_MODE", None)
        self.patch = mock.patch("edu_agent.permissions.policy", return_value="allow")
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def _restore_env(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        if self._saved_single is None:
            os.environ.pop("EDU_SINGLE_USER_MODE", None)
        else:
            os.environ["EDU_SINGLE_USER_MODE"] = self._saved_single

    def make_published(self, name="mindmap-probe", *, install=True):
        r = packager.import_draft(DRAFT.replace("mindmap-probe", name),
                                  actor="teacher-A", confirm=True)
        man, body = mf.from_document((Path(r["dir"]) / "SKILL.md").read_text(encoding="utf-8"))
        store.write_draft(man, body, files={"plugin.py": PLUGIN}, allow_code=True)
        self.assertTrue(lifecycle.submit(name, "teacher-A")["ok"])
        self.assertTrue(skill_studio.approve(name, "reviewer-G", note="ok")["ok"])
        if install:
            got = skill_studio.install(name, "teacher-A", confirm=True)
            self.assertTrue(got["ok"], got)
        session_bootstrap.ensure_session("t1", force=True)
        return name

    # ---------------------------------------------------------------- 工具
    def test_only_installed_skills_become_tools(self):
        """已安装的技能进工具列表；只有草稿的不进（没批准的东西不该被模型调到）。"""
        self.make_published("with-install")
        rep = packager.import_draft(DRAFT.replace("mindmap-probe", "draft-only"),
                                    actor="teacher-A", confirm=True)
        man, body = mf.from_document((Path(rep["dir"]) / "SKILL.md").read_text(encoding="utf-8"))
        store.write_draft(man, body, files={"plugin.py": PLUGIN}, allow_code=True)

        names = [s["skill"] for s in agent_tools.loadable_skills()]
        self.assertIn("with-install", names)
        self.assertNotIn("draft-only", names)

    def test_tool_name_and_all_optional_params(self):
        self.make_published()
        tools = agent_tools.build_tools()
        names = [t.name for t in tools]
        self.assertIn("mindmap_probe__generate_mindmap", names)
        tool = [t for t in tools if t.name == "mindmap_probe__generate_mindmap"][0]
        self.assertEqual(sorted(tool.args.keys()), ["book_id", "chapter_no"])
        # 参数全部可选（缺省由技能自己兜底），否则模型会被迫编一个 book_id
        for spec in tool.args.values():
            self.assertIsNone(spec.get("default"))

    def test_tool_invocation_returns_skill_text(self):
        self.make_published()
        tool = [t for t in agent_tools.build_tools() if "generate_mindmap" in t.name][0]
        out = tool.invoke({"chapter_no": 2})
        self.assertIn("章节思维导图", out)
        self.assertIn("第 2 章", out)

    # ---------------------------------------------------------------- 快路
    def test_intent_hits_generation_requests(self):
        self.make_published()
        for q, want in (("做个第二章的思维导图", 2), ("第3章梳理一下", 3), ("画个导图 第五章", 5)):
            hit = agent_tools.match_intent(q)
            self.assertIsNotNone(hit, q)
            self.assertEqual(hit["args"].get("chapter_no"), want, q)
            self.assertIn("章节思维导图", hit["text"], q)

    def test_intent_does_not_hijack_questions_or_greetings(self):
        """问答题 / 问候 / 没有章节号的生成请求都不该被技能快路劫持。"""
        self.make_published()
        for q in ("什么是幂函数", "帮我讲讲单调性", "你好", "这题怎么做",
                  "第二章有哪些小节", "做个思维导图"):
            self.assertIsNone(agent_tools.match_intent(q), q)

    def test_chapter_out_of_range_is_blocked_not_silently_wrong(self):
        """章节越界必须拦：教材库对不存在的章节会**静默回退到第一章**。"""
        self.make_published()
        hit = agent_tools.match_intent("帮我画个导图，第九十九章")
        if hit is None:
            self.skipTest("教材库不可用（拦不拦都无从判断）")
        self.assertEqual(hit.get("blocked"), "chapter_range")
        self.assertIsNone(hit.get("result"))
        self.assertIn("没有第 99 章", hit["text"])

    def test_chapter_guard_passes_valid_numbers(self):
        if not agent_tools.chapter_guard({"chapter_no": 999}):
            self.skipTest("教材库不可用")
        self.assertEqual(agent_tools.chapter_guard({"chapter_no": 1}), "")
        self.assertEqual(agent_tools.chapter_guard({}), "")
        self.assertEqual(agent_tools.chapter_guard({"chapter_no": "x"}), "")


class ChineseNumberTest(unittest.TestCase):
    def test_cn_to_int(self):
        cases = {"一": 1, "二": 2, "十": 10, "十一": 11, "十二": 12, "二十": 20,
                 "二十五": 25, "3": 3, "12": 12}
        for text, want in cases.items():
            self.assertEqual(agent_tools._cn_to_int(text), want, text)

    def test_extract_chapter_from_sentences(self):
        params = [{"name": "book_id", "type": "string"}, {"name": "chapter_no", "type": "integer"}]
        for q, want in (("做个第二章的思维导图", 2), ("第 3 章梳理", 3),
                        ("第十二章画个导图", 12), ("第十二章", 12)):
            self.assertEqual(agent_tools.extract_args(q, params).get("chapter_no"), want, q)
        self.assertEqual(agent_tools.extract_args("做个思维导图", params), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
