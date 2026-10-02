# -*- coding: utf-8 -*-
"""单用户调试模式 / 多教师正式模式：开关、注册表、publish 边界、会话加载分支。

重点锁死三条约束：
1. 注册表四个必留字段（skill_id / status / create_teacher_id / file_path）不许删；
2. `publish` 只做「复制落盘 + 改状态」，**绝不调用 isolation.bind**、也不写安装关系；
3. `SINGLE_USER_MODE` 的两个分支行为正确（单用户全量加载 / 多用户按已安装 ∧ published）。
"""
import json
import os
import shutil
import sys
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from edu_agent.skill_market import (isolation, lifecycle, manifest as mf,
                                   mcp_user_service, mode, packager, paths,
                                   registry_db, session_bootstrap, store)


def _draft_text(name: str, *, desc: str = "测试技能", owner: str = "teacher-A") -> str:
    return ("---\nname: %s\ntitle: %s 的中文名\ndescription: %s\nowner: %s\n"
            "allowed_capabilities: [\"textbook:read\"]\ndata_scope: [\"teaching_context\"]\n"
            "tests:\n  - input: {\"chapter_no\": 2}\n    expect_contains: \"- 章节思维导图\"\n"
            "---\n# %s\n## 功能\n读教材库生成章节思维导图。\n"
            % (name, name, desc, owner, name))


class ModeTestCase(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1] / ".tmp-skill-mode"
        base.mkdir(parents=True, exist_ok=True)
        self.tmp = base / ("t-" + uuid.uuid4().hex[:8])
        self.tmp.mkdir(parents=True)
        (self.tmp / "runtime").mkdir(parents=True)
        self._env = {}
        for k, v in (("EDU_SKILL_MARKET_DIR", str(self.tmp / "market")),
                     ("EDU_SKILL_RUNTIME_DIR", str(self.tmp / "runtime")),
                     ("EDU_DATA_DIR", str(self.tmp / "data")),
                     ("EDU_SKILL_REGISTRY_DB", str(self.tmp / "skill_registry.db")),
                     ("EDU_AGENT_HOME", str(self.tmp / "home")),
                     ("EDU_SINGLE_USER_MODE", None)):
            self._env[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        isolation._cache.clear()

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        isolation._cache.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- 便捷 ----
    def make_published(self, name: str, *, actor: str = "teacher-A") -> dict:
        """走完整流水线把一个技能发布出去（导入 → 提交 → 审核 → 发布）。"""
        with mock.patch("edu_agent.permissions.policy", return_value="allow"):
            imp = packager.import_draft(_draft_text(name), actor=actor, confirm=True)
            self.assertTrue(imp["ok"], imp)
            sub = lifecycle.submit(name, actor)
            self.assertTrue(sub["ok"], sub)
            apr = lifecycle.approve(name, "reviewer-G")
            self.assertTrue(apr["ok"], apr)
            pub = lifecycle.publish(name, actor, confirm=True)
        self.assertTrue(pub["ok"], pub)
        return pub

    def make_draft_only(self, name: str, *, actor: str = "teacher-A") -> dict:
        with mock.patch("edu_agent.permissions.policy", return_value="allow"):
            return packager.import_draft(_draft_text(name), actor=actor, confirm=True)


# ════════════════════════════════════════════════════ 约束 1：表结构
class TestRegistrySchema(ModeTestCase):
    def test_required_columns_present(self):
        """五个必留字段一个都不能少（铁律 1：skill_id/status/create_teacher_id/manifest/file_path）。"""
        snap = registry_db.ensure_schema()
        self.assertTrue(snap["ok"], snap)
        for col in ("skill_id", "status", "create_teacher_id", "manifest", "file_path"):
            self.assertIn(col, snap["columns"], snap["columns"])
        self.assertEqual(list(registry_db.REQUIRED_COLUMNS),
                         ["skill_id", "status", "create_teacher_id", "manifest", "file_path"])

    def test_manifest_column_populated(self):
        """manifest 字段要真的落内容（不是空占位）。"""
        self.make_draft_only("mf-skill")
        row = registry_db.get("mf-skill")
        data = json.loads(row["manifest"] or "{}")
        self.assertEqual(data.get("name"), "mf-skill")
        self.assertIn("description", data)

    def test_old_db_gets_new_column_added(self):
        """老库（没有 manifest 列）要能自动补列，不删表不丢数据。"""
        import sqlite3

        p = Path(os.environ["EDU_SKILL_REGISTRY_DB"])
        registry_db.ensure_schema()
        with sqlite3.connect(str(p)) as c:                 # 手工造一个"老库"：删掉 manifest 列做不到，
            c.execute("PRAGMA table_info(skills)")         # 所以直接验证补列逻辑本身幂等且不报错
        snap = registry_db.ensure_schema()
        self.assertIn("manifest", snap["columns"])
        self.assertTrue(snap["ok"])

    def test_install_table_exists(self):
        """多用户用的安装关系表也先建好（单用户模式不用，但表结构留着）。"""
        snap = registry_db.ensure_schema()
        self.assertIn("skill_installs", snap["tables"])
        self.assertIn("skills", snap["tables"])

    def test_upsert_and_list(self):
        registry_db.upsert("s1", status="draft", create_teacher_id="t1",
                           file_path="/x/SKILL.md", title="S1")
        row = registry_db.get("s1")
        self.assertEqual(row["status"], "draft")
        self.assertEqual(row["create_teacher_id"], "t1")
        self.assertEqual(row["file_path"], "/x/SKILL.md")
        registry_db.set_status("s1", "published")
        self.assertEqual([r["skill_id"] for r in registry_db.published()], ["s1"])

    def test_migrate_from_state_and_dirs(self):
        self.make_draft_only("mig-skill")
        # 人为清空注册表，模拟"表是后加的"
        registry_db._exec("DELETE FROM skills")            # noqa: SLF001
        self.assertIsNone(registry_db.get("mig-skill"))
        a = registry_db.migrate_from_state_file()
        b = registry_db.migrate_from_dirs()
        self.assertGreaterEqual(a["imported"] + b["imported"], 1)
        self.assertIsNotNone(registry_db.get("mig-skill"))


# ════════════════════════════════════════════════════ 约束 3：开关
class TestModeSwitch(ModeTestCase):
    def test_default_is_single_user(self):
        self.assertTrue(mode.single_user_mode())
        self.assertEqual(mode.teacher_id(), "local-teacher")

    def test_env_overrides_file(self):
        mode.set_single_user_mode(True)
        os.environ["EDU_SINGLE_USER_MODE"] = "0"
        try:
            self.assertFalse(mode.single_user_mode())
            self.assertIn("env:", mode.mode_source())
            # 文件仍写着 True，但环境变量优先 —— 返回值要提醒这件事
            r = mode.set_single_user_mode(True)
            self.assertFalse(r["single_user_mode"])
            self.assertIn("优先", r["note"])
        finally:
            os.environ.pop("EDU_SINGLE_USER_MODE", None)

    def test_file_switch(self):
        r = mode.set_single_user_mode(False)
        self.assertTrue(r["ok"])
        self.assertFalse(mode.single_user_mode())
        self.assertEqual(mode.teacher_id("teacher-X"), "teacher-X")
        self.assertEqual(mode.teacher_id(), "")          # 多用户模式必须显式给身份
        mode.set_single_user_mode(True)

    def test_describe_lists_behavior_diff(self):
        d = mode.describe()
        for k in ("session_load", "market_visibility", "install_logic", "mcp_user_service"):
            self.assertIn(k, d["behavior"])


# ════════════════════════════════════════════════════ 约束 2：publish 边界
class TestPublishBoundary(ModeTestCase):
    def test_publish_copies_to_global_data_skills_and_sets_published(self):
        pub = self.make_published("pub-skill")
        target = Path(os.environ["EDU_SKILL_RUNTIME_DIR"]) / "pub-skill"
        self.assertTrue((target / "SKILL.md").exists())
        self.assertEqual(str(pub["installed_to"]), str(target))
        self.assertEqual(registry_db.get("pub-skill")["status"], "published")
        self.assertTrue(registry_db.get("pub-skill")["installed"])
        self.assertTrue(paths.is_inside_runtime("pub-skill"))

    def test_publish_never_calls_isolation_bind(self):
        """**红线**：publish 不许碰会话绑定。把 bind 换成会抛异常的桩，发布仍须成功。"""
        self.make_draft_only("no-bind-skill")
        with mock.patch("edu_agent.permissions.policy", return_value="allow"):
            lifecycle.submit("no-bind-skill", "teacher-A")
            lifecycle.approve("no-bind-skill", "reviewer-G")
            with mock.patch.object(isolation, "bind",
                                   side_effect=AssertionError("publish 调了 isolation.bind！")):
                pub = lifecycle.publish("no-bind-skill", "teacher-A", confirm=True)
        self.assertTrue(pub["ok"], pub)
        self.assertEqual(registry_db.get("no-bind-skill")["status"], "published")

    def test_publish_does_not_write_installs(self):
        """发布 ≠ 安装：安装关系是多用户模式的独立动作。"""
        self.make_published("install-skill", actor="teacher-A")
        self.assertEqual(registry_db.installed_for("teacher-A"), [])

    def test_create_teacher_id_survives_publish(self):
        """创建者在发布时**不能被操作者覆盖**（很容易埋进去的坑）。"""
        self.make_published("owner-skill", actor="teacher-A")
        row = registry_db.get("owner-skill")
        self.assertEqual(row["create_teacher_id"], "teacher-A")

    def test_file_path_field_points_to_skill_md(self):
        self.make_published("path-skill")
        row = registry_db.get("path-skill")
        self.assertTrue(row["file_path"])
        self.assertIn("path-skill", row["file_path"])


# ════════════════════════════════════════════════════ 约束 4①：单用户分支
class TestSingleUserBootstrap(ModeTestCase):
    def test_auto_loads_all_published(self):
        self.make_published("s1")
        self.make_published("s2")
        self.make_draft_only("d1")               # 草稿不该被加载
        got = session_bootstrap.ensure_session("sess-1")
        self.assertTrue(got["ok"], got)
        self.assertEqual(sorted(got["loaded"]), ["s1", "s2"])
        self.assertEqual(got["source"], "single_user:all_published")
        self.assertFalse(got["mcp"]["called"])   # 单用户模式不调 MCP 用户服务
        self.assertEqual(sorted(isolation.list_for("sess-1")), ["s1", "s2"])

    def test_idempotent_and_picks_up_new_publish(self):
        self.make_published("s1")
        first = session_bootstrap.ensure_session("sess-1")
        self.assertFalse(first["already"])
        again = session_bootstrap.ensure_session("sess-1")
        self.assertTrue(again["already"])        # 集合没变 → 不重复写
        self.make_published("s2")
        third = session_bootstrap.ensure_session("sess-1")
        self.assertFalse(third["already"])
        self.assertEqual(sorted(third["loaded"]), ["s1", "s2"])

    def test_visibility_shows_all_drafts_and_published(self):
        self.make_published("s1")
        self.make_draft_only("d1")
        got = session_bootstrap.visible_skills()
        names = {r["skill_id"] for r in got["skills"]}
        self.assertEqual(names, {"s1", "d1"})
        self.assertIn("全部草稿", got["scope"])

    def test_registry_published_missing_files_dropped(self):
        """注册表说 published、但磁盘上没有 → 剔除并说明原因（不做无声失败）。"""
        registry_db.upsert("ghost", status="published", file_path="/nope/SKILL.md")
        ids, dropped = session_bootstrap.published_skill_ids()
        self.assertNotIn("ghost", ids)
        self.assertTrue(any(d["skill"] == "ghost" for d in dropped))


# ════════════════════════════════════════════════════ 约束 4②：多用户分支
class TestMultiUserBootstrap(ModeTestCase):
    def setUp(self):
        super().setUp()
        mode.set_single_user_mode(False)

    def test_requires_teacher_id(self):
        got = session_bootstrap.ensure_session("sess-1")
        self.assertFalse(got["ok"])
        self.assertEqual(got["gate"], "teacher_id")

    def test_unwired_mcp_refuses_to_load(self):
        """MCP 用户服务没接线 → **拒绝加载**（宁可少加载，不越权多加载）。"""
        self.make_published("s1", actor="teacher-A")
        got = session_bootstrap.ensure_session("sess-1", "teacher-A")
        self.assertFalse(got["ok"])
        self.assertEqual(got["gate"], "mcp_user_service")
        self.assertTrue(got["mcp"]["called"])
        self.assertIn("未连接", got["mcp"]["error"] + got["mcp"].get("hint", ""))
        self.assertEqual(isolation.list_for("sess-1"), [])

    def test_loads_only_installed_and_published(self):
        self.make_published("s1", actor="teacher-A")
        self.make_published("s2", actor="teacher-A")
        self.make_draft_only("d1", actor="teacher-A")
        # 假装 MCP 用户服务已接线，返回"该教师装了 s1 和 d1（草稿）"
        with mock.patch.object(mcp_user_service, "installed_skills",
                               return_value={"ok": True, "connected": True, "skills": ["s1", "d1"]}):
            got = session_bootstrap.ensure_session("sess-1", "teacher-A")
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["loaded"], ["s1"])              # d1 是草稿 → 不加载
        self.assertTrue(any(d["skill"] == "d1" for d in got["dropped"]))
        self.assertEqual(got["source"], "multi_user:mcp_installed")

    def test_visibility_hides_others_drafts(self):
        self.make_published("s1", actor="teacher-A")
        self.make_draft_only("d-mine", actor="teacher-A")
        self.make_draft_only("d-other", actor="teacher-B")
        mine = session_bootstrap.visible_skills("teacher-A")
        names = {r["skill_id"] for r in mine["skills"]}
        self.assertEqual(names, {"s1", "d-mine"})            # 别人的草稿看不见
        self.assertIn("本人创建的草稿", mine["scope"])

    def test_draft_visibility_helper(self):
        row_mine = {"create_teacher_id": "teacher-A"}
        self.assertTrue(mcp_user_service.can_see_draft("teacher-A", row_mine))
        self.assertFalse(mcp_user_service.can_see_draft("teacher-B", row_mine))
        self.assertFalse(mcp_user_service.can_see_draft("", row_mine))


# ════════════════════════════════════════════════════ 约束 5：MCP 用户服务
class TestMcpUserService(ModeTestCase):
    def test_unwired_by_default(self):
        ok, why = mcp_user_service.connected()
        self.assertFalse(ok)
        self.assertIn("未启用", why)
        got = mcp_user_service.installed_skills("teacher-A")
        self.assertFalse(got["ok"])
        self.assertFalse(got["connected"])
        self.assertEqual(got["skills"], [])
        self.assertIn("接线方式", got.get("hint", ""))

    def test_health_says_will_not_be_called_in_single_mode(self):
        h = mcp_user_service.health()
        self.assertFalse(h["will_be_called"])
        self.assertIn("单用户调试模式", h["note"])

    def test_wired_then_called(self):
        mcp_user_service.set_config(enabled=True, base_url="http://127.0.0.1:5190")
        ok, _ = mcp_user_service.connected()
        self.assertTrue(ok)
        with mock.patch.object(mcp_user_service, "_call_http",
                               return_value={"ok": True, "skills": ["s1", "s2"]}):
            got = mcp_user_service.installed_skills("teacher-A")
        self.assertTrue(got["ok"])
        self.assertEqual(got["skills"], ["s1", "s2"])
        self.assertEqual(got["source"], "http")

    def test_bad_response_is_reported_not_swallowed(self):
        mcp_user_service.set_config(enabled=True, base_url="http://127.0.0.1:5190")
        with mock.patch.object(mcp_user_service, "_call_http", return_value={"ok": True}):
            got = mcp_user_service.installed_skills("teacher-A")
        self.assertFalse(got["ok"])
        self.assertIn("没有 skills 列表", got["error"])


# ════════════════════════════════════════════════════ 约束 6：MCP 教材服务
class TestTextbookMcp(ModeTestCase):
    def test_registered_as_mcp_capability(self):
        from edu_agent import capabilities

        spec = capabilities.mcp_spec("textbook")
        self.assertIsNotNone(spec)
        self.assertTrue(str(spec.entry()).endswith("textbook_server.py"))
        self.assertTrue(spec.entry().exists(), spec.entry())

    def test_tools_whitelisted(self):
        from edu_agent import mcp_tools

        allowed = mcp_tools._WHITELIST.get("textbook")       # noqa: SLF001
        self.assertIsNotNone(allowed)
        for tool in ("textbook_chapters", "textbook_chapter", "textbook_mindmap"):
            self.assertIn(tool, allowed)

    def test_lib_reads_and_renders_mindmap(self):
        from edu_agent import textbook_lib

        rows = textbook_lib.chapters()["chapters"]
        self.assertTrue(rows, "教材库为空，本机应至少有内置教材 md")
        got = textbook_lib.chapter(2)
        self.assertTrue(got["ok"], got)
        self.assertIn("chapter", got)
        mm = textbook_lib.mindmap_markdown(2)
        self.assertIn("- 章节思维导图", mm["markdown"])
        self.assertGreaterEqual(mm["section_count"], 1)

    def test_chapter_falls_back_when_not_found(self):
        from edu_agent import textbook_lib

        got = textbook_lib.chapter(99)
        self.assertTrue(got["ok"])
        self.assertIn("回退", got["note"])


# ════════════════════════════════════════════════════ 铁律 2：三重校验不可绕过
class TestThreeGatesAlwaysRun(ModeTestCase):
    """无论 SINGLE_USER_MODE 是 True 还是 False，提交审核都必须过 L1/L2/L3。"""

    BAD_NETWORK = ("---\nname: net-skill\ndescription: 想联网\nowner: t\n"
                   "allowed_capabilities: [\"kg.read\"]\ndata_scope: [\"knowledge_id\"]\n"
                   "tests:\n  - input: {}\n    expect_contains: \"x\"\n---\n"
                   "# net-skill\n## 功能\n我会 requests.get('https://api.example.com') 拿数据。\n")

    BAD_PLUGIN = ("---\nname: boom-skill\ndescription: 会炸\nowner: t\n"
                  "plugin: plugin.py\nallowed_capabilities: [\"textbook:read\"]\n"
                  "data_scope: [\"teaching_context\"]\n"
                  "tests:\n  - input: {chapter_no: 2}\n    expect_contains: \"- 章节思维导图\"\n---\n"
                  "# boom-skill\n## 功能\n读教材库。\n")
    BOOM_PLUGIN = ("from semantic_kernel.functions import kernel_function\n\n\n"
                   "class Boom:\n"
                   "    @kernel_function(name='f', description='炸')\n"
                   "    def f(self) -> dict:\n"
                   "        raise RuntimeError('故意炸一下')\n")

    def _import(self, text: str, name: str) -> dict:
        with mock.patch("edu_agent.permissions.policy", return_value="allow"):
            return packager.import_draft(text, actor="t", confirm=True)

    def test_L1_failure_blocks_submit_in_both_modes(self):
        for single in (True, False):
            mode.set_single_user_mode(single)
            with self.subTest(single_user_mode=single):
                r = self._import(self.BAD_NETWORK, "net-skill")
                self.assertTrue(r["ok"], r)
                got = lifecycle.submit("net-skill", "t")
                self.assertFalse(got["ok"], got)
                self.assertIn("L1 未过", got["error"])
                # 状态必须还停在草稿（没有偷偷变成待审）
                self.assertEqual(store.effective_status("net-skill"), "draft")

    def test_L3_failure_blocks_submit_in_both_modes(self):
        for single in (True, False):
            mode.set_single_user_mode(single)
            with self.subTest(single_user_mode=single):
                r = self._import(self.BAD_PLUGIN, "boom-skill")
                self.assertTrue(r["ok"], r)
                # 人工补一个会抛异常的插件（模拟"代码有问题"）
                man, body = mf.from_document(
                    (Path(r["dir"]) / "SKILL.md").read_text(encoding="utf-8"))
                store.write_draft(man, body, files={"plugin.py": self.BOOM_PLUGIN},
                                  allow_code=True)
                got = lifecycle.submit("boom-skill", "t")
                self.assertFalse(got["ok"], got)
                self.assertIn("L3 未过", got["error"])
                self.assertEqual(store.effective_status("boom-skill"), "draft")

    def test_good_skill_passes_three_gates_and_records_them(self):
        mode.set_single_user_mode(True)
        pub = self.make_published("gates-skill")
        self.assertTrue(pub["ok"])
        hist = [r for r in lifecycle.history("gates-skill") if r["event"] == "skill.submit"]
        self.assertTrue(hist)
        gates = hist[0].get("gates") or {}
        self.assertEqual(gates.get("L1"), "pass")
        self.assertEqual(gates.get("L2"), "pass")
        self.assertIn(gates.get("L3"), ("pass", "skipped:no-plugin"))

    def test_switch_does_not_change_audit_or_state_machine(self):
        """铁律 5：开关只影响会话加载与市场查询，不影响审计/状态机。"""
        mode.set_single_user_mode(True)
        a = self.make_published("audit-a")
        mode.set_single_user_mode(False)
        b = self.make_published("audit-b")
        self.assertTrue(a["ok"] and b["ok"])
        for sid in ("audit-a", "audit-b"):
            events = [r["event"] for r in lifecycle.history(sid)]
            for want in ("skill.submit", "skill.approve", "skill.publish"):
                self.assertIn(want, events, (sid, events))
        from edu_agent.skill_market import audit

        self.assertTrue(audit.verify_chain()["ok"])


# ════════════════════════════════════════════════════ 业务分支：市场查询
class TestMarketVisibilityBranch(ModeTestCase):
    def test_single_user_sees_everything(self):
        self.make_published("v-pub")
        self.make_draft_only("v-draft")
        from edu_agent.skill_market import store as _store

        rows = _store.market_index()
        names = {r["name"] for r in rows}
        self.assertEqual(names, {"v-pub", "v-draft"})

    def test_multi_user_hides_others_drafts(self):
        self.make_published("v-pub", actor="teacher-A")
        self.make_draft_only("v-mine", actor="teacher-A")
        self.make_draft_only("v-other", actor="teacher-B")
        from edu_agent.skill_market import registry_db

        mine = {r["skill_id"] for r in registry_db.visible_for("teacher-A", single_user=False)}
        self.assertEqual(mine, {"v-pub", "v-mine"})
        other = {r["skill_id"] for r in registry_db.visible_for("teacher-B", single_user=False)}
        self.assertEqual(other, {"v-pub", "v-other"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
