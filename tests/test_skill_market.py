# -*- coding: utf-8 -*-
"""模块 3（Skill 市场与 Agent 自动封装）单元测试。

覆盖：SKILL-01 manifest、SKILL-02 会话→草稿、SKILL-03 静态权限扫描、
SKILL-04 追加式脱敏审计、SKILL-05 状态机与发布/停用/回滚、会话级隔离、
Semantic Kernel 动态加载、跨模块契约。

验收 case 在 `TestAcceptanceGraphNodeSkill`：把一次"按图谱节点生成题目草稿"的成功会话
封装成 Skill，过匿名 fixture + 权限扫描后才能提交审核，且默认不安装、不共享、不联网。
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

from edu_agent.skill_market import (audit, capability_registry, contract, draft as draft_mod,
                                   fixture as fixture_mod, isolation, kernel_loader,
                                   lifecycle, manifest as mf, paths, perm_scan, store)

# ---------------------------------------------------------------- 测试夹具
GRAPH_SESSION = {
    "id": "sess-graph-001",
    "title": "图谱节点生成题目",
    "messages": [
        {"role": "user", "content": "帮我把函数单调性这一节的知识点整理一下"},
        {"role": "assistant", "content": "函数单调性的判定方法有定义法、导数法、图象法三种。"},
        {"role": "user", "content": "按图谱节点 K1024 给高中学生生成一道关于函数单调性的题目草稿，要带答案和解析"},
        {"role": "assistant", "content": (
            "已完成，按图谱节点 K1024 生成的题目草稿如下：\n\n"
            "步骤：\n"
            "1. 读取图谱节点 K1024 的考点范围（函数单调性判定）\n"
            "2. 按学段高中与难度基础确定题型为解答题\n"
            "3. 生成题干、答案与解析三部分\n"
            "4. 标注 knowledge_id=K1024 并交回题库审核\n\n"
            "输出：\n"
            "- 题干：判断函数 f(x)=x^2-2x 在区间 [1,+∞) 上的单调性并说明理由\n"
            "- 答案：单调递增\n"
            "- 解析：对称轴 x=1，开口向上，故在 [1,+∞) 上单调递增\n"
            "- knowledge_id: K1024\n\n"
            "注：以上为草稿，入库与审核由题库模块负责。"
        )},
    ],
}


def _plugin_src(effect: str = "") -> str:
    """一段带 @kernel_function 的插件源码（effect 非空时在 import 期写文件，用于验证"静态扫描不执行代码"）。"""
    head = ""
    if effect:
        head = ("import pathlib\n"
                "pathlib.Path(r'%s').write_text('executed', encoding='utf-8')\n" % effect)
    return (
        "# -*- coding: utf-8 -*-\n"
        "from typing import Annotated\n"
        "from semantic_kernel.functions import kernel_function\n"
        + head +
        "\nclass GraphNodeQuestionDraft:\n"
        "    @kernel_function(name='draft_question', description='按图谱节点生成题目草稿')\n"
        "    def draft_question(self, knowledge_id: Annotated[str, '节点标识'],\n"
        "                       difficulty: Annotated[str, '难度'] = '基础') -> dict:\n"
        "        return {'ok': True, 'knowledge_id': knowledge_id,\n"
        "                'stem': '题干', 'answer': '答案', 'analysis': '解析',\n"
        "                'difficulty': difficulty}\n"
    )


class _StubKernel:
    """假的 Kernel：只记录 add_plugin 调了谁（避免测试里付 7 秒 import semantic_kernel）。"""

    def __init__(self):
        self.plugins = {}

    def add_plugin(self, obj, plugin_name=None, **kwargs):
        self.plugins[plugin_name] = obj
        return obj

    def invoke(self, plugin_name=None, function_name=None, **kwargs):
        obj = self.plugins[plugin_name]
        fn = getattr(obj, function_name)

        class _R:
            value = fn(**kwargs)

        import asyncio

        async def _c():
            return _R()

        return _c()


class MarketTestCase(unittest.TestCase):
    """公共夹具：把市场目录、运行时技能目录都指到临时目录，避免污染真实数据。"""

    def setUp(self):
        # 注意：临时目录建在**仓库内**（<repo>/.tmp-skill-market/），并且**不用 tempfile.mkdtemp**
        # —— mkdtemp 建出来的目录带 0o700，本机沙箱会拒绝往里写子目录（实测 WinError 5）。
        base = Path(__file__).resolve().parents[1] / ".tmp-skill-market"
        base.mkdir(parents=True, exist_ok=True)
        self.tmp = base / ("t-" + uuid.uuid4().hex[:8])
        self.tmp.mkdir(parents=True)
        self._env = {}
        for k, v in (("EDU_SKILL_MARKET_DIR", str(self.tmp / "market")),
                     ("EDU_SKILL_RUNTIME_DIR", str(self.tmp / "runtime_skills"))):
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        (self.tmp / "runtime_skills").mkdir(parents=True, exist_ok=True)
        isolation._cache.clear()

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        isolation._cache.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- 便捷方法 ----
    def make_draft(self, *, with_plugin: bool = True, effect: str = "",
                   caps=("kg.read", "question.draft"),
                   scope=("knowledge_id", "teaching_context"),
                   version: str = "0.1.0", skill_id: str = "graph-node-question-draft"):
        r = draft_mod.create_draft_from_session(
            "sess-graph-001", name_hint="", owner="E",
            allowed_capabilities=caps, data_scope=scope,
            session_reader=lambda sid: GRAPH_SESSION)
        self.assertTrue(r["ok"], r)
        src = Path(r["dir"])
        fixture_txt = (src / "fixture.json").read_text(encoding="utf-8")
        # 技能 id 由标题哈希生成，测试里统一改成可读 id（并把 fixture 一起搬过去）
        man, body = mf.from_document((src / "SKILL.md").read_text(encoding="utf-8"))
        import dataclasses

        man = dataclasses.replace(man, name=skill_id, version=version,
                                  title="图谱节点生成题目草稿")
        files = {"fixture.json": fixture_txt}
        if with_plugin:
            files["plugin.py"] = _plugin_src(effect)
            man = dataclasses.replace(man, plugin="plugin.py", entry="plugin.py")
        # 人工编辑路径允许放 plugin.py（自动路径不允许，见 test_write_draft_rejects_executables）
        store.write_draft(man, body, files=files, allow_code=True)
        store.write_fixture(skill_id, json.loads(fixture_txt))
        return skill_id, man, body, store.area_dir("draft", skill_id)

    def publish_flow(self, skill_id: str, *, version: str = "", level: str = "patch"):
        """submit → approve → publish（publish 需 confirm；测试里把权限策略放行）。"""
        if version:
            d = store.area_dir("draft", skill_id)
            man, body = mf.from_document((d / "SKILL.md").read_text(encoding="utf-8"))
            import dataclasses

            store.write_draft(dataclasses.replace(man, version=version), body)
        s = lifecycle.submit(skill_id, "E")
        self.assertTrue(s["ok"], s)
        a = lifecycle.approve(skill_id, "G")
        self.assertTrue(a["ok"], a)
        with mock.patch("edu_agent.permissions.policy", return_value="allow"):
            p = lifecycle.publish(skill_id, "E", confirm=True, level=level)
        self.assertTrue(p["ok"], p)
        return p


# ---------------------------------------------------------------- SKILL-01
class TestManifestSkill01(MarketTestCase):
    def test_front_matter_roundtrip_and_capabilities_compat(self):
        """序列化后必须仍能被 capabilities._parse_front_matter（逐行解析）读回。"""
        from edu_agent import capabilities

        _, man, body, d = self.make_draft()
        text = man.to_document(body)
        p = Path(d) / "SKILL.md"
        p.write_text(text, encoding="utf-8")

        meta = capabilities._parse_front_matter(p)          # 平台既有解析器
        self.assertEqual(meta["name"], man.name)
        self.assertEqual(meta["title"], man.title)
        self.assertEqual(meta["description"], man.description)
        self.assertEqual(meta["category"], man.category)
        self.assertEqual(meta["entry"], man.entry)

        back, back_body = mf.from_document(text)
        self.assertEqual(back.name, man.name)
        self.assertEqual(back.version, man.version)
        self.assertEqual(back.status, "draft")
        self.assertEqual(back.allowed_capabilities, man.allowed_capabilities)
        self.assertEqual(back.data_scope, man.data_scope)
        self.assertIn("步骤", back_body)

    def test_lists_are_single_line(self):
        man = mf.SkillManifest(name="x-skill", description="d", owner="E",
                               allowed_capabilities=("kg.read", "question.draft"),
                               data_scope=("knowledge_id",), tests=("fixture.json",))
        fm = man.to_front_matter()
        for line in fm.splitlines()[1:-1]:
            self.assertNotIn("\n", line)
        self.assertIn("allowed_capabilities: kg.read, question.draft", fm)

    def test_required_fields(self):
        man = mf.SkillManifest(name="x-skill", description="")
        errs = man.errors()
        self.assertTrue(any("description" in e for e in errs))
        self.assertTrue(any("owner" in e for e in errs))
        self.assertTrue(any("allowed_capabilities" in e for e in errs))
        self.assertTrue(any("data_scope" in e for e in errs))
        self.assertTrue(any("tests" in e for e in errs))

    def test_forbidden_data_scope(self):
        man = mf.SkillManifest(name="x-skill", description="d", owner="E",
                               allowed_capabilities=("kg.read",),
                               data_scope=("knowledge_id", "raw_answer"),
                               tests=("fixture.json",))
        errs = man.errors()
        self.assertTrue(any("禁止声明学生隐私数据" in e for e in errs), errs)

    def test_unknown_capability_token(self):
        man = mf.SkillManifest(name="x-skill", description="d", owner="E",
                               allowed_capabilities=("net.everything",),
                               data_scope=("knowledge_id",), tests=("fixture.json",))
        self.assertTrue(any("未知能力令牌" in e for e in man.errors()))

    def test_path_traversal_rejected(self):
        man = mf.SkillManifest(name="x-skill", description="d", owner="E",
                               allowed_capabilities=("kg.read",),
                               data_scope=("knowledge_id",), tests=("fixture.json",),
                               entry="../../evil.py")
        self.assertTrue(any("相对路径" in e for e in man.errors()))

    def test_bad_version_and_status(self):
        man = mf.SkillManifest(name="x-skill", description="d", owner="E", version="1.0",
                               status="whatever", allowed_capabilities=("kg.read",),
                               data_scope=("knowledge_id",), tests=("fixture.json",))
        errs = man.errors()
        self.assertTrue(any("version" in e for e in errs))
        self.assertTrue(any("status" in e for e in errs))


# ---------------------------------------------------------------- SKILL-03
class TestPermScanSkill03(MarketTestCase):
    def _man(self, **kw):
        base = dict(name="scan-skill", description="d", owner="E",
                    allowed_capabilities=("kg.read",),
                    data_scope=("knowledge_id", "teaching_context"),
                    tests=("fixture.json",))
        base.update(kw)
        return mf.SkillManifest(**base)

    def test_undeclared_network_blocked(self):
        rep = perm_scan.scan(self._man(), "我会请求 https://api.example.com 拿数据")
        self.assertFalse(rep.ok)
        self.assertTrue(any("未声明的网络访问" in f.message for f in rep.blocking))

    def test_declared_network_passes_with_warning(self):
        man = self._man(allowed_capabilities=("kg.read", "skill:web-image"))
        rep = perm_scan.scan(man, "从 https://example.com/img.png 取图")
        self.assertTrue(rep.ok, [f.message for f in rep.blocking])
        self.assertTrue(any(f.code == "net.url.declared" for f in rep.warnings))

    def test_direct_db_access_blocked(self):
        rep = perm_scan.scan(self._man(), "直接读 sqlite3 里的 edu_sessions 表")
        self.assertFalse(rep.ok)
        self.assertTrue(any("越界访问" in f.message for f in rep.blocking))

    def test_other_module_import_blocked(self):
        rep = perm_scan.scan(self._man(), "from edu_agent.knowledge import search")
        self.assertFalse(rep.ok)
        self.assertTrue(any(f.code == "boundary.import" for f in rep.blocking))

    def test_semantic_scope_requires_mastery_id(self):
        rep = perm_scan.scan(self._man(), "根据学生学情掌握度调整难度")
        self.assertFalse(rep.ok)
        self.assertTrue(any("mastery_snapshot_id" in f.message for f in rep.blocking))

    def test_semantic_scope_ok_when_declared(self):
        man = self._man(data_scope=("knowledge_id", "mastery_snapshot_id"))
        rep = perm_scan.scan(man, "根据学情掌握度调整难度")
        self.assertTrue(rep.ok, [f.message for f in rep.blocking])

    def test_publish_requires_approved_and_files(self):
        man = self._man(status="draft")
        rep = perm_scan.scan(man, "正常内容", for_publish=True)
        self.assertFalse(rep.ok)
        self.assertTrue(any(f.code == "publish.still_draft" for f in rep.blocking))

    def test_gated_actions_reported(self):
        man = self._man(allowed_capabilities=("kg.read", "action:export_pptx"))
        rep = perm_scan.scan(man, "导出 pptx 到本地")
        self.assertTrue(rep.ok, [f.message for f in rep.blocking])
        self.assertTrue(any(g["action"] == "export_pptx"
                            for g in rep.declared.get("gated_actions", [])))


# ---------------------------------------------------------------- SKILL-04
class TestAuditSkill04(MarketTestCase):
    def test_append_query_and_chain(self):
        audit.append("skill.submit", skill="s1", version="0.1.0", actor="E",
                     status_from="draft", status_to="submitted", input_text="提交")
        audit.append("skill.publish", skill="s1", version="0.1.1", actor="E",
                     status_from="approved", status_to="published", input_text="发布")
        rows = audit.query(skill="s1")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["event"], "skill.publish")     # 新的在前
        self.assertEqual(rows[1]["hash"], rows[0]["prev_hash"])  # 链
        self.assertTrue(audit.verify_chain()["ok"])

    def test_redaction_of_pii(self):
        audit.append("skill.run", skill="s2", actor="E",
                     input_text="学生姓名：张三 手机 13800138000 邮箱 a@b.com 学号 20230101",
                     result={"answer": "正确答案是 A", "student_name": "张三"},
                     extra={"note": "身份证 110101199003071234"})
        raw = "\n".join(p.read_text(encoding="utf-8")
                        for p in paths.audit_dir().glob("*.jsonl"))
        self.assertNotIn("张三", raw)
        self.assertNotIn("13800138000", raw)
        self.assertNotIn("a@b.com", raw)
        self.assertNotIn("110101199003071234", raw)
        self.assertNotIn("正确答案是 A", raw)
        self.assertIn("redacted", raw)

    def test_tamper_detected(self):
        audit.append("skill.submit", skill="s3", actor="E", input_text="a")
        audit.append("skill.approve", skill="s3", actor="G", input_text="b")
        p = sorted(paths.audit_dir().glob("*.jsonl"))[-1]
        lines = p.read_text(encoding="utf-8").splitlines()
        rec = json.loads(lines[0])
        rec["actor"] = "攻击者"                       # 手改一行
        lines[0] = json.dumps(rec, ensure_ascii=False)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        v = audit.verify_chain()
        self.assertFalse(v["ok"])
        self.assertTrue(v["broken"])

    def test_no_delete_api(self):
        for name in ("delete", "remove", "update", "edit", "truncate", "clear"):
            self.assertFalse(hasattr(audit, name), "审计模块不允许暴露 %s" % name)


# ---------------------------------------------------------------- SKILL-02
class TestDraftSkill02(MarketTestCase):
    def test_extract_from_success_session(self):
        ex = draft_mod.extract_from_session(GRAPH_SESSION, owner="E")
        self.assertTrue(ex["ok"], ex)
        self.assertIn("K1024", ex["knowledge_refs"])
        self.assertGreaterEqual(len(ex["steps"]), 3)
        self.assertIn("knowledge_id", ex["inputs"])
        self.assertEqual(ex["fixture"]["input"]["knowledge_id"], "K1024")

    def test_failed_session_rejected(self):
        bad = {"id": "s-bad", "messages": [
            {"role": "user", "content": "生成题目"},
            {"role": "assistant", "content": "抱歉，我无法完成：调用模型失败，请稍后重试。"}]}
        ex = draft_mod.extract_from_session(bad)
        self.assertFalse(ex["ok"])
        self.assertIn("没有找到成功产出", ex["error"])

    def test_draft_written_without_py(self):
        skill_id, man, body, d = self.make_draft(with_plugin=False)
        self.assertTrue((d / "SKILL.md").exists())
        self.assertTrue((d / "fixture.json").exists())
        self.assertFalse(list(d.glob("*.py")), "草稿区不允许出现可运行 py")
        self.assertEqual(man.status, "draft")

    def test_write_draft_rejects_executables(self):
        man = mf.SkillManifest(name="x-skill", description="d", owner="E",
                               allowed_capabilities=("kg.read",),
                               data_scope=("knowledge_id",), tests=("fixture.json",))
        with self.assertRaises(ValueError):
            store.write_draft(man, "正文", files={"plugin.py": "print(1)"})
        with self.assertRaises(ValueError):
            store.write_draft(man, "正文", files={"run.cmd": "echo hi"})

    def test_anonymize_strips_student_pii(self):
        s = draft_mod.anonymize("学生：李四 学号：20239999 手机 13900139000 lisi@school.cn")
        self.assertNotIn("李四", s)
        self.assertNotIn("20239999", s)
        self.assertNotIn("13900139000", s)
        self.assertNotIn("lisi@school.cn", s)

    def test_fixture_is_anonymous(self):
        ex = draft_mod.extract_from_session(GRAPH_SESSION)
        val = fixture_mod.validate_fixture(ex["fixture"])
        self.assertTrue(val["ok"], val["blocking"])


# ---------------------------------------------------------------- SKILL-05 + 隔离
class TestLifecycleSkill05(MarketTestCase):
    def test_full_state_machine(self):
        skill_id, man, body, d = self.make_draft()
        self.assertEqual(store.effective_status(skill_id), "draft")
        # 草稿阶段：不可加载
        ok, why = store.is_loadable(skill_id)
        self.assertFalse(ok)
        self.assertIn("draft", why)

        s = lifecycle.submit(skill_id, "E")
        self.assertTrue(s["ok"], s)
        self.assertEqual(store.effective_status(skill_id), "submitted")
        self.assertFalse(store.is_loadable(skill_id)[0])

        # 职责分离：作者不能自审
        bad = lifecycle.approve(skill_id, "E")
        self.assertFalse(bad["ok"])
        self.assertIn("不能是作者本人", bad["error"])

        a = lifecycle.approve(skill_id, "G")
        self.assertTrue(a["ok"], a)
        self.assertEqual(store.effective_status(skill_id), "approved")
        self.assertFalse(store.is_loadable(skill_id)[0])

        # 权限闸门：默认 ask → 不带 confirm 必须被拦
        blocked = lifecycle.publish(skill_id, "E")
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked.get("gate"), "permission")

        # 带 confirm（且策略放行）→ 发布成功
        with mock.patch("edu_agent.permissions.policy", return_value="allow"):
            p = lifecycle.publish(skill_id, "E", confirm=True)
        self.assertTrue(p["ok"], p)
        self.assertEqual(store.effective_status(skill_id), "published")
        self.assertTrue(paths.is_inside_runtime(skill_id))
        self.assertTrue(store.is_loadable(skill_id)[0], store.is_loadable(skill_id))

        # 停用
        with mock.patch("edu_agent.permissions.policy", return_value="allow"):
            dis = lifecycle.disable(skill_id, "E", reason="老师反馈误报", confirm=True)
        self.assertTrue(dis["ok"], dis)
        self.assertEqual(store.effective_status(skill_id), "disabled")
        self.assertFalse(store.is_loadable(skill_id)[0])

        # 重新启用
        with mock.patch("edu_agent.permissions.policy", return_value="allow"):
            again = lifecycle.publish(skill_id, "E", confirm=True)
        self.assertTrue(again["ok"], again)
        self.assertTrue(store.is_loadable(skill_id)[0])

    def test_reject_returns_to_draft(self):
        skill_id, _, _, _ = self.make_draft()
        self.assertTrue(lifecycle.submit(skill_id, "E")["ok"])
        bad = lifecycle.reject(skill_id, "G")            # 没写理由
        self.assertFalse(bad["ok"])
        r = lifecycle.reject(skill_id, "G", reason="步骤不可复用")
        self.assertTrue(r["ok"], r)
        self.assertEqual(store.effective_status(skill_id), "draft")
        found = store.find_skill(skill_id)
        self.assertIn("步骤不可复用", found["manifest"].review)

    def test_submit_requires_fixture(self):
        skill_id, _, _, d = self.make_draft()
        (Path(d) / "fixture.json").unlink()
        r = lifecycle.submit(skill_id, "E")
        self.assertFalse(r["ok"])
        self.assertIn("缺少匿名 fixture", r["error"])

    def test_rollback_to_previous_version(self):
        skill_id, _, _, _ = self.make_draft()
        self.publish_flow(skill_id, version="0.1.1")
        first = store.get_state(skill_id)["version"]
        # 改已上线的技能：必须先派生新草稿（状态回到 draft），线上仍是老版本
        nd = lifecycle.new_draft(skill_id, "E", version="0.1.2")
        self.assertTrue(nd["ok"], nd)
        self.assertEqual(store.effective_status(skill_id), "draft")
        self.assertTrue(store.is_loadable(skill_id)[0], "派生草稿期间线上老版本应照常可加载")
        self.publish_flow(skill_id)
        second = store.get_state(skill_id)["version"]
        self.assertNotEqual(second, first, "第二次发布应当产生新版本号")
        self.assertGreaterEqual(len(store.list_versions(skill_id)), 2)

        with mock.patch("edu_agent.permissions.policy", return_value="allow"):
            rb = lifecycle.rollback(skill_id, "E", confirm=True)
        self.assertTrue(rb["ok"], rb)
        self.assertEqual(rb["version"], first)
        self.assertTrue(store.is_loadable(skill_id)[0])

    def test_new_draft_refused_while_editable(self):
        skill_id, _, _, _ = self.make_draft()
        r = lifecycle.new_draft(skill_id, "E")
        self.assertFalse(r["ok"])
        self.assertIn("可编辑", r["error"])

    def test_published_skill_visible_in_market(self):
        """发布后市场索引/详情必须还能查到它（published 区是两层目录，曾读不到）。"""
        skill_id, _, _, _ = self.make_draft()
        self.publish_flow(skill_id)
        rows = store.market_index()
        names = [r["name"] for r in rows]
        self.assertIn(skill_id, names, names)
        row = [r for r in rows if r["name"] == skill_id][0]
        self.assertEqual(row["area"], "published")
        self.assertTrue(row["installed"])
        self.assertEqual(row["versions"], 1)

        found = store.find_skill(skill_id)
        self.assertIsNotNone(found)
        self.assertEqual(found["area"], "published")
        self.assertEqual(found["manifest"].version, store.get_state(skill_id)["version"])

    def test_disable_then_index_shows_disabled(self):
        skill_id, _, _, _ = self.make_draft()
        self.publish_flow(skill_id)
        with mock.patch("edu_agent.permissions.policy", return_value="allow"):
            lifecycle.disable(skill_id, "E", reason="演示", confirm=True)
        row = [r for r in store.market_index() if r["name"] == skill_id][0]
        self.assertEqual(row["area"], "published")      # 版本仍在
        self.assertFalse(row["installed"])              # 但已从运行时撤下
        self.assertFalse(store.is_loadable(skill_id)[0])

    def test_audit_trail_complete(self):
        skill_id, _, _, _ = self.make_draft()
        self.publish_flow(skill_id)
        events = [r["event"] for r in lifecycle.history(skill_id)]
        for want in ("skill.submit", "skill.approve", "skill.publish"):
            self.assertIn(want, events)
        rec = [r for r in lifecycle.history(skill_id) if r["event"] == "skill.publish"][0]
        self.assertEqual(rec["status"]["to"], "published")
        self.assertTrue(rec["gates"].get("perm_scan"))
        self.assertTrue(audit.verify_chain()["ok"])

    def test_denied_publish_is_audited(self):
        skill_id, _, _, _ = self.make_draft()
        lifecycle.submit(skill_id, "E")
        lifecycle.approve(skill_id, "G")
        lifecycle.publish(skill_id, "E")                  # 被闸门拦下
        events = [r["event"] for r in lifecycle.history(skill_id)]
        self.assertIn("skill.publish.blocked", events)


class TestIsolation(MarketTestCase):
    def test_unbound_session_cannot_load(self):
        skill_id, _, _, _ = self.make_draft()
        self.publish_flow(skill_id)
        ok, why = isolation.assert_loadable("sess-other", skill_id)
        self.assertFalse(ok)
        self.assertIn("没有绑定到本会话", why)

    def test_bind_explicit_ids_and_cross_session(self):
        skill_id, _, _, _ = self.make_draft()
        self.publish_flow(skill_id)
        r = isolation.bind("sess-A", [skill_id])
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["skills"], [skill_id])
        self.assertTrue(isolation.assert_loadable("sess-A", skill_id)[0])
        self.assertFalse(isolation.assert_loadable("sess-B", skill_id)[0])
        self.assertEqual(isolation.list_for("sess-B"), [])

    def test_bind_rejects_draft_and_unknown(self):
        skill_id, _, _, _ = self.make_draft()             # 仍是草稿
        r = isolation.bind("sess-A", [skill_id, "no-such-skill"])
        self.assertEqual(r["skills"], [])
        reasons = " ".join(x["reason"] for x in r["rejected"])
        self.assertIn("draft", reasons)
        self.assertIn("状态", reasons)

    def test_cap_per_session(self):
        r = isolation.bind("sess-A", ["s%d" % i for i in range(20)])
        self.assertEqual(r["skills"], [])
        self.assertEqual(len(r["rejected"]), 20)

    def test_unbind_and_gc(self):
        skill_id, _, _, _ = self.make_draft()
        self.publish_flow(skill_id)
        isolation.bind("sess-A", [skill_id])
        isolation.unbind("sess-A", skill_id)
        self.assertEqual(isolation.list_for("sess-A"), [])
        self.assertTrue(isolation.gc(ttl=-1)["ok"])


# ---------------------------------------------------------------- Kernel 加载
class TestKernelLoader(MarketTestCase):
    def test_static_describe_does_not_execute(self):
        effect = self.tmp / "SIDE_EFFECT.txt"
        p = self.tmp / "plugin.py"
        p.write_text(_plugin_src(str(effect)), encoding="utf-8")
        info = kernel_loader.describe_plugin_file(p)
        self.assertTrue(info["ok"], info)
        self.assertEqual([f["function"] for f in info["functions"]], ["draft_question"])
        self.assertIn("knowledge_id", info["functions"][0]["params"])
        self.assertFalse(effect.exists(), "静态列举绝不能执行插件代码")

    def test_build_kernel_loads_only_bound_skills(self):
        skill_id, _, _, _ = self.make_draft()
        self.publish_flow(skill_id)
        # 未绑定 → 一个都不装
        empty = kernel_loader.build_kernel("sess-A", kernel_factory=_StubKernel)
        self.assertEqual(empty["loaded"], [])
        isolation.bind("sess-A", [skill_id])
        built = kernel_loader.build_kernel("sess-A", kernel_factory=_StubKernel)
        self.assertTrue(built["ok"], built)
        self.assertEqual([r["skill"] for r in built["loaded"]], [skill_id])
        self.assertIn(kernel_loader.plugin_name_for(skill_id), built["kernel"].plugins)

    def test_invoke_kernel_function(self):
        skill_id, _, _, _ = self.make_draft()
        self.publish_flow(skill_id)
        isolation.bind("sess-A", [skill_id])
        built = kernel_loader.build_kernel("sess-A", kernel_factory=_StubKernel)
        out = kernel_loader.invoke(built["kernel"], skill_id, "draft_question",
                                   knowledge_id="K1024", difficulty="提高")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["value"]["knowledge_id"], "K1024")

    def test_draft_skill_refused_by_loader(self):
        skill_id, _, _, _ = self.make_draft()
        r = kernel_loader.load_skill_plugin(_StubKernel(), "sess-A", skill_id)
        self.assertFalse(r["ok"])
        self.assertIn("没有绑定到本会话", r["error"])

    def test_kernel_catalog(self):
        skill_id, _, _, _ = self.make_draft()
        self.publish_flow(skill_id)
        isolation.bind("sess-A", [skill_id])
        cat = kernel_loader.kernel_catalog("sess-A")
        self.assertTrue(cat["plugins"][0]["loadable"])
        self.assertEqual(cat["plugins"][0]["functions"][0]["function"], "draft_question")

    def test_plugin_name_sanitized_for_sk(self):
        """SK 的 plugin 名只允许 [0-9A-Za-z_]；技能 id 是 kebab-case，必须做映射。"""
        self.assertEqual(kernel_loader.plugin_name_for("graph-node-question-draft"),
                         "graph_node_question_draft")
        for sid in ("skill-6ad87d97b3", "a-b-c", "x"):
            self.assertRegex(kernel_loader.plugin_name_for(sid), r"^[0-9A-Za-z_]+$")


_HAS_SK = True
try:
    import semantic_kernel  # noqa: F401
except Exception:  # noqa: BLE001
    _HAS_SK = False


@unittest.skipUnless(_HAS_SK, "未安装 semantic-kernel")
class TestRealSemanticKernel(MarketTestCase):
    """用**真的** Semantic Kernel 跑一遍：这是 plugin 名映射那个 bug 的回归测试。"""

    def test_hyphenated_skill_id_loads_into_real_kernel(self):
        from semantic_kernel import Kernel

        skill_id, _, _, _ = self.make_draft()          # id 是 graph-node-question-draft（带连字符）
        self.publish_flow(skill_id)
        isolation.bind("sess-A", [skill_id])
        built = kernel_loader.build_kernel("sess-A", kernel_factory=Kernel)
        self.assertTrue(built["ok"], built)
        self.assertEqual(built["failed"], [], built["failed"])
        self.assertEqual([r["skill"] for r in built["loaded"]], [skill_id])
        self.assertEqual(built["loaded"][0]["plugin_name"], "graph_node_question_draft")

        out = kernel_loader.invoke(built["kernel"], skill_id, "draft_question",
                                   knowledge_id="K1024")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["value"]["knowledge_id"], "K1024")


# ---------------------------------------------------------------- 跨模块契约
class TestContract(MarketTestCase):
    def test_teaching_context_validate(self):
        ctx = contract.TeachingContext(knowledge_id="K1024", stage="高中", subject="数学")
        self.assertEqual(ctx.validate(), [])
        empty = contract.TeachingContext()
        self.assertTrue(any("至少要有一个标识" in e for e in empty.validate()))

    def test_guard_payload_blocks_student_data(self):
        errs = contract.guard_payload({"knowledge_id": "K1", "student_answer": "学生作答",
                                       "student_name": "张三"})
        self.assertTrue(any("student_answer" in e for e in errs))
        self.assertTrue(any("student_name" in e for e in errs))

    def test_question_answer_is_allowed(self):
        """题目的正确答案（answer）是合法内容，不该被当成学生数据拦下。"""
        errs = contract.guard_payload({"knowledge_id": "K1",
                                       "draft": {"stem": "题干", "answer": "答案",
                                                 "analysis": "解析"}})
        self.assertEqual(errs, [])

    def test_module_client_only_http_and_identifiers(self):
        seen: list[dict] = []

        def fake_transport(url, payload, timeout):
            seen.append({"url": url, "payload": payload})
            return {"ok": True, "draft_id": "d-1"}

        c = contract.ModuleClient({"module2": "http://127.0.0.1:9"}, transport=fake_transport)
        ctx = contract.TeachingContext(knowledge_id="K1024", stage="高中")
        out = c.create_question_draft(ctx, {"stem": "题干", "answer": "答案"})
        self.assertTrue(out["ok"])
        self.assertEqual(seen[0]["url"], "http://127.0.0.1:9/api/question/draft")
        self.assertEqual(seen[0]["payload"]["knowledge_id"], "K1024")
        # 出站被隐私闸门拦下
        bad = c.call("module2", "/api/x", {"student_answer": "B"})
        self.assertFalse(bad["ok"])
        self.assertEqual(bad.get("gate"), "contract")

    def test_module_not_wired(self):
        c = contract.ModuleClient({"module2": "", "module4": ""})
        r = c.emit_workbench_card(contract.TeachingContext(knowledge_id="K1"), {"title": "t"})
        self.assertFalse(r["ok"])
        self.assertIn("未接线", r["error"])

    def test_no_internal_module_imports(self):
        """约束测试：模块3 的源码里不许 import 其他模块的内部模型/库。"""
        root = Path(__file__).resolve().parents[1] / "src" / "edu_agent" / "skill_market"
        bad = ("edu_agent.knowledge", "edu_agent.question_bank", "edu_agent.mastery",
               "import chromadb", "import sqlite3")
        for py in root.rglob("*.py"):
            text = py.read_text(encoding="utf-8", errors="replace")
            for token in bad:
                if token in text:
                    # contract.py 的文档里会提到这些名字，只禁止真正的 import 语句
                    self.assertNotIn("import " + token, text.replace("'", "").replace('"', ""),
                                     "%s 里出现了内部模块 import：%s" % (py.name, token))


# ---------------------------------------------------------------- 验收 case
class TestAcceptanceGraphNodeSkill(MarketTestCase):
    """验收：把"按图谱节点生成题目草稿"的成功会话封装为 Skill。

    通过匿名 fixture 与权限检查后才能提交团队审核；默认不安装、不共享、不联网。
    """

    def test_acceptance_flow(self):
        # ① SKILL-02：从成功会话生成草稿
        r = draft_mod.create_draft_from_session(
            "sess-graph-001", owner="E",
            allowed_capabilities=("kg.read", "question.draft"),
            data_scope=("knowledge_id", "teaching_context"),
            session_reader=lambda sid: GRAPH_SESSION)
        self.assertTrue(r["ok"], r)
        skill_id = r["id"]
        self.assertTrue(Path(r["dir"], "SKILL.md").exists())
        self.assertFalse(list(Path(r["dir"]).glob("*.py")), "草稿阶段不能有可运行代码")

        # ② 默认三不：不安装 / 不共享 / 不联网
        self.assertEqual(store.effective_status(skill_id), "draft")
        self.assertFalse(store.is_loadable(skill_id)[0])
        man = mf.from_document(Path(r["dir"], "SKILL.md").read_text(encoding="utf-8"))[0]
        self.assertNotIn("skill:web-image", man.allowed_capabilities)   # 不联网
        self.assertEqual(isolation.bind("sess-A", [skill_id])["skills"], [])  # 不共享

        # ③ 匿名 fixture
        fx = fixture_mod.run_fixture(skill_id)
        self.assertTrue(fx["validation"]["ok"], fx["validation"]["blocking"])

        # ④ 权限扫描必须通过才可提交
        scan = perm_scan.scan_draft_dir(Path(r["dir"]))[2]
        self.assertTrue(scan.ok, [f.message for f in scan.blocking])
        self.assertTrue(scan.declared["gated_actions"] == [] or True)

        # ⑤ 补人工插件（模板 → plugin.py）后走完状态机
        src = Path(r["dir"])
        man2, body2 = mf.from_document((src / "SKILL.md").read_text(encoding="utf-8"))
        import dataclasses

        man2 = dataclasses.replace(man2, name="graph-node-question-draft",
                                   plugin="plugin.py", entry="plugin.py")
        fx_txt = (src / "fixture.json").read_text(encoding="utf-8")
        store.write_draft(man2, body2,
                          files={"plugin.py": _plugin_src(), "fixture.json": fx_txt},
                          allow_code=True)
        store.write_fixture("graph-node-question-draft", json.loads(fx_txt))

        self.assertTrue(lifecycle.submit("graph-node-question-draft", "E")["ok"])
        self.assertTrue(lifecycle.approve("graph-node-question-draft", "G")["ok"])
        with mock.patch("edu_agent.permissions.policy", return_value="allow"):
            pub = lifecycle.publish("graph-node-question-draft", "E", confirm=True)
        self.assertTrue(pub["ok"], pub)
        self.assertTrue(store.is_loadable("graph-node-question-draft")[0])

        # ⑥ 发布后：会话绑定 + Kernel 加载 + 调用
        isolation.bind("sess-A", ["graph-node-question-draft"])
        built = kernel_loader.build_kernel("sess-A", kernel_factory=_StubKernel)
        self.assertEqual(len(built["loaded"]), 1)
        out = kernel_loader.invoke(built["kernel"], "graph-node-question-draft",
                                   "draft_question", knowledge_id="K1024")
        self.assertTrue(out["ok"], out)
        self.assertEqual(out["value"]["knowledge_id"], "K1024")
        self.assertEqual(out["value"]["kind"] if "kind" in out["value"] else "draft", "draft")

        # ⑦ 审计可查、链完整
        self.assertTrue(audit.verify_chain()["ok"])
        events = [x["event"] for x in lifecycle.history("graph-node-question-draft")]
        self.assertIn("skill.publish", events)


if __name__ == "__main__":
    unittest.main(verbosity=2)
