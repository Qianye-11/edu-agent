# -*- coding: utf-8 -*-
"""Skill Studio（doc 34）单元测试 —— 逐条对应 §8.1 的验收清单 + §8.2 端到端案例。

    1. 三次相似对话生成一个候选           → test_three_similar_sessions_make_candidate
    2. 两次不相似对话不生成候选           → test_two_dissimilar_sessions_make_nothing
    3. 含学生姓名/成绩的候选被拦截或脱敏   → test_sensitive_candidate_is_scrubbed
    4. 编辑草稿后批准失效                 → test_edit_after_approval_invalidates_it
    5. 未批准时安装被拒绝                 → test_install_requires_approval
    6. 同名覆盖前创建可恢复备份           → test_overwrite_creates_backup
    7. 拒绝后冷却期不重复提醒             → test_reject_sets_cooldown
    8. 团队服务不可用时本地功能不出错      → test_team_service_down_local_still_works
    9. 未批准 Skill 不允许进入同步队列     → test_unapproved_cannot_enqueue_sync
    + 端到端：4 次相近对话 → 候选 → 草稿 → 修改 → 重批 → 安装 → 同步
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

from edu_agent.skill_market import (isolation, lifecycle, miner, packager, paths,
                                   registry_db, runs, skill_privacy, skill_studio, store)

STEPS = [{"kind": "skill", "id": "textbook:read"}]

# 高一语文「文本精读 + 分层练习」（doc §5.2 与 §8.2 的场景）
U = ("为高一《劝学》设计 45 分钟文本精读课；学生文言实词薄弱，需要分层练习。"
     "要给出 A/B/C 三层练习。")
R = ("已完成，按分层设计：\n- 篇目：《劝学》\n- 课时：45 分钟\n"
     "- 目标：文本精读 + 文言实词\n- 分层：A/B/C 三层练习\n"
     "- 输出物：教案（含分层练习）\n")
U2 = "再帮我为高一《师说》做一次 45 分钟文本精读课，文言实词要分层练习。"
U3 = "还有《赤壁赋》也来一节高一文本精读课，45 分钟，文言实词分层练习。"


def _draft_text(name: str, *, owner: str = "teacher-A") -> str:
    return ("---\nname: %s\ntitle: 高一语文文本精读与分层练习\ndescription: 45 分钟文本精读 + 文言实词分层练习\n"
            "owner: %s\nplugin: plugin.py\n"
            "allowed_capabilities: [\"textbook:read\"]\ndata_scope: [\"teaching_context\"]\n"
            "tests:\n  - input: {chapter_no: 2}\n    expect_contains: \"- 章节思维导图\"\n"
            "    expect_fields: [\"ok\", \"markdown\"]\n"
            "---\n# %s\n## 功能\n按篇目与课时生成文本精读课与分层练习。\n" % (name, owner, name))


STUB_PLUGIN = ("from semantic_kernel.functions import kernel_function\n\n\n"
               "class Studio:\n"
               "    @kernel_function(name='f', description='桩')\n"
               "    def f(self) -> dict:\n"
               "        return {'ok': True, 'markdown': '- 章节思维导图'}\n")


class StudioTestCase(unittest.TestCase):
    def setUp(self):
        base = Path(__file__).resolve().parents[1] / ".tmp-skill-studio"
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
                     ("EDU_SINGLE_USER_MODE", None),
                     ("SKILL_STUDIO_DIR", None),
                     ("SKILL_STUDIO_EXECUTE_PLUGIN", "1"),
                     ("SKILL_CANDIDATE_MIN_SESSIONS", "3"),
                     ("SKILL_CANDIDATE_WINDOW_DAYS", "30"),
                     ("TENCENT_MEMORY_ENABLED", "0")):
            self._env[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        isolation._cache.clear()
        runs._last_turn.clear()
        runs.begin_turn("")
        # 阈值：`runs.config()` 自己按「显式设置 > 环境变量 > 默认」解析
        # （SKILL_CANDIDATE_MIN_SESSIONS=3 已在上面设进环境，无需额外调用）
        assert runs.config()["min_sessions"] == 3

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        isolation._cache.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- 便捷 ----
    def seed(self, texts, *, steps=None):
        """把 (用户, 助手) 轮次写成运行痕迹。"""
        for i, t in enumerate(texts):
            runs.record_turn("sess-%d" % i, 1, t, R,
                             steps=[dict(s) for s in (steps or STEPS)])

    def make_draft(self, name="gaoyi-yuwen-jingdu", *, owner="teacher-A"):
        with mock.patch("edu_agent.permissions.policy", return_value="allow"):
            r = packager.import_draft(_draft_text(name, owner=owner), actor=owner, confirm=True)
        self.assertTrue(r["ok"], r)
        from edu_agent.skill_market import manifest as mf

        man, body = mf.from_document((Path(r["dir"]) / "SKILL.md").read_text(encoding="utf-8"))
        store.write_draft(man, body, files={"plugin.py": STUB_PLUGIN}, allow_code=True)
        return name, Path(r["dir"])

    def submit_and_approve(self, name, *, reviewer="reviewer-G"):
        s = skill_studio.submit_review(name, "teacher-A")
        self.assertTrue(s["ok"], s)
        a = skill_studio.approve(name, reviewer, note="同意")
        self.assertTrue(a["ok"], a)
        return a

    def skill_dir(self, name) -> Path:
        """技能当前所在目录（提交后草稿区会搬到 submitted/，所以不能缓存旧路径）。"""
        found = store.find_skill(name)
        self.assertIsNotNone(found, "找不到技能目录：%s" % name)
        return Path(found["dir"])

    def edit_body(self, name, extra: str, **manifest_changes):
        """在技能当前目录里改正文（先确保回到可编辑草稿态，再写回）。"""
        import dataclasses

        from edu_agent.skill_market import manifest as mf

        ed = skill_studio.ensure_editable(name, "teacher-A")
        self.assertTrue(ed.get("ok"), ed)
        d = self.skill_dir(name)
        man, body = mf.from_document((d / "SKILL.md").read_text(encoding="utf-8"))
        if manifest_changes:
            man = dataclasses.replace(man, **manifest_changes)
        (d / "SKILL.md").write_text(man.to_document(body + extra), encoding="utf-8",
                                    newline="\n")   # 与真实 API 路径一致，见下
        # ↑ newline="\n" 不能省：默认写会把 \n 变 \r\n（Windows），而内容哈希算的是正文字节
        #   → 辅助函数自己就把哈希改了，测试会得出"哈希无故变化"的错误结论。
        return d

    def install(self, name, **kw):
        with mock.patch("edu_agent.permissions.policy", return_value="allow"):
            return skill_studio.install(name, "teacher-A", confirm=True, **kw)


# ════════════════════════════════════════════════════ 1 / 2 候选检测
class TestCandidateDetection(StudioTestCase):
    def test_three_similar_sessions_make_candidate(self):
        """doc §8.1-1：三次相似对话 → 一个候选。"""
        self.seed([U, U2, U3])
        scan = miner.scan()
        self.assertGreaterEqual(scan["candidates"], 1)
        cands = skill_studio.candidates()
        self.assertEqual(cands["count"], 1)
        c = cands["candidates"][0]
        self.assertEqual(c["sessions"], 3)              # 3 个独立会话
        self.assertEqual(c["studio_state"], "detected")
        self.assertTrue(all(c["criteria"].values()), c["blocking"])
        # 证据只给会话编号，不带原文
        self.assertTrue(all(set(e) <= {"session_id", "turn", "outcome"} for e in c["evidence"]))

    def test_two_dissimilar_sessions_make_nothing(self):
        """doc §8.1-2：两次不相似对话（不同签名）不生成候选。"""
        runs.record_turn("a", 1, U, R, steps=STEPS)
        runs.record_turn("b", 1, "帮我看看今天天气怎么样", "今天多云。", steps=[])
        miner.scan()
        ready = [c for c in skill_studio.candidates()["candidates"]
                 if all((c.get("criteria") or {}).values())]
        self.assertEqual(ready, [])

    def test_min_sessions_threshold_from_env(self):
        """阈值来自 .env：SKILL_CANDIDATE_MIN_SESSIONS=3 时，两个会话不够。"""
        self.seed([U, U2])
        miner.scan()
        rows = [x for x in skill_studio.candidates()["candidates"]]
        self.assertTrue(rows)
        c = rows[0]
        self.assertFalse(c["criteria"]["R1"])
        self.assertEqual(c["sessions"], 2)


# ════════════════════════════════════════════════════ 3 隐私
class TestPrivacy(StudioTestCase):
    def test_sensitive_candidate_is_scrubbed(self):
        """doc §8.1-3：含学生姓名/成绩的候选被脱敏（候选箱不回显原文）。"""
        skill_studio.ensure_dirs()
        cand = {"candidate_id": "sig-x", "signature": "sig-x", "state": "candidate",
                "title": "给 学生姓名：张三 出题，成绩：88 分",
                "runs": 3, "sessions": 3, "score": 0.7, "criteria": {"R1": True},
                "evidence": [{"session_id": "s1", "turn": 1, "outcome": "success"}]}
        (paths.candidates_dir() / "c-x.json").write_text(
            json.dumps(cand, ensure_ascii=False), encoding="utf-8")
        got = skill_studio.candidates()
        row = [c for c in got["candidates"] if c["candidate_id"] == "sig-x"][0]
        self.assertTrue(row["sensitive_title"])
        self.assertNotIn("张三", row["title"])
        self.assertIn("已脱敏", row["title"])

    def test_scan_text_detects_categories(self):
        got = skill_privacy.scan_text("学生姓名：李四 学号：20230101 手机 13800138000 "
                                      "邮箱 a@b.com 成绩：95 分 家长：王五")
        codes = {h["code"] for h in got["hits"]}
        for want in ("student_name", "student_id", "phone", "email", "score", "family"):
            self.assertIn(want, codes, codes)

    def test_risk_level_from_capabilities(self):
        self.assertEqual(skill_privacy.risk_level({"allowed_capabilities": ["textbook:read"]})["level"],
                         "low")
        self.assertEqual(skill_privacy.risk_level({"allowed_capabilities": ["skill:web-image"]})["level"],
                         "medium")
        self.assertEqual(skill_privacy.risk_level({"allowed_capabilities": ["action:pdf_ingest"]})["level"],
                         "high")


# ════════════════════════════════════════════════════ 4 / 5 批准与安装闸门
class TestApprovalAndInstall(StudioTestCase):
    def test_edit_after_approval_invalidates_it(self):
        """doc §8.1-4：编辑草稿后批准失效（哈希不一致）。"""
        name, _d = self.make_draft()
        self.submit_and_approve(name)
        self.assertTrue(skill_studio.approval_status(name)["hash_matches"])
        self.edit_body(name, "\n教师补充：加一条时间控制。\n")
        st = skill_studio.approval_status(name)
        self.assertTrue(st["approved"])
        self.assertFalse(st["hash_matches"])
        got = self.install(name)
        self.assertFalse(got["ok"])
        self.assertEqual(got["gate"], "stale")

    def test_install_requires_approval(self):
        """doc §8.1-5：未批准时安装接口被拒绝。"""
        name, _d = self.make_draft()
        got = self.install(name)
        self.assertFalse(got["ok"])
        self.assertEqual(got["gate"], "approval")
        self.assertIn("没有教师批准记录", got["error"])

    def test_approval_binds_the_reviewed_draft_not_the_installed_copy(self):
        """**回归**：批准绑定的必须是"正在审的那份内容"，不是已安装的旧副本。

        踩过的坑：`skill_files()` 早先**优先取运行时（已安装）目录**，而老师审的是草稿
        —— 于是 `approve()` 绑定了**旧内容的哈希**；等安装把运行时刷成新内容，
        `hash_matches()` 立刻变 False，表现为"刚批准装完就说批准失效"
        （实测：批准记录里是旧版 1499/7683 字节，草稿是 1457/7517）。

        这里刻意让"草稿内容 ≠ 已安装内容"：先发一版装上去，再派生草稿改正文，
        然后提交 → 批准 → 安装，最后必须仍然 hash_matches=True。
        """
        name, _d = self.make_draft()
        # ① 第一版：走完流水线并安装 → 运行时目录有内容
        self.assertTrue(skill_studio.submit_review(name, "teacher-A")["ok"])
        self.assertTrue(skill_studio.approve(name, "reviewer-G", note="v1")["ok"])
        self.assertTrue(self.install(name)["ok"])
        v1 = skill_studio.content_hash(name)

        # ② 派生新草稿并改正文（此时"草稿内容"与"已安装内容"必然不同）
        self.assertTrue(lifecycle.new_draft(name, "teacher-A", note="改一版")["ok"])
        self.edit_body(name, "\n教师补充：这一版加了板书时间轴。\n")
        draft_hash = skill_studio.content_hash(name)
        self.assertNotEqual(draft_hash, v1, "派生草稿改了正文，哈希就该变")

        # ③ 再次提交 → 批准：绑定的应当是上面那份草稿哈希
        self.assertTrue(skill_studio.submit_review(name, "teacher-A")["ok"])
        ap = skill_studio.approve(name, "reviewer-G", note="v2")
        self.assertTrue(ap["ok"], ap)
        bound = skill_studio.approval_status(name)["content_hash"]
        self.assertEqual(bound, draft_hash,
                         "批准绑定了错误的哈希（应当是被审草稿的，不是已安装副本的）")
        self.assertTrue(skill_studio.approval_status(name)["hash_matches"])

        # ④ 安装后仍然一致（这一步就是当初暴露 bug 的地方）
        got = self.install(name, allow_overwrite=True)
        self.assertTrue(got["ok"], got)
        st = skill_studio.approval_status(name)
        self.assertTrue(st["hash_matches"],
                        "安装后批准就失效了：说明批准绑的不是被审内容。current=%s approved=%s"
                        % (st.get("current_hash"), st.get("content_hash")))

    def test_approval_requires_review_state_and_separation(self):
        name, _d = self.make_draft()
        r = skill_studio.approve(name, "reviewer-G")            # 还没 submit-review
        self.assertFalse(r["ok"])
        self.assertIn("不能批准", r["error"])
        self.assertTrue(skill_studio.submit_review(name, "teacher-A")["ok"])
        same = skill_studio.approve(name, "teacher-A")          # 自己批自己
        self.assertFalse(same["ok"])
        self.assertIn("不能是创建者本人", same["error"])

    def test_install_flow_and_state_mapping(self):
        name, _d = self.make_draft()
        self.submit_and_approve(name)
        self.assertEqual(store.effective_status(name), "approved")
        got = self.install(name)
        self.assertTrue(got["ok"], got)
        self.assertEqual(got["studio_state"], "installed")
        self.assertTrue(paths.is_inside_runtime(name))
        self.assertEqual(registry_db.get(name)["status"], "published")
        # doc 状态映射
        self.assertEqual(skill_studio.STATE_ALIAS["submitted"], "in_review")
        self.assertEqual(skill_studio.STATE_ALIAS["published"], "installed")

    def test_overwrite_creates_backup(self):
        """doc §8.1-6：同名覆盖前创建可恢复备份。"""
        name, _d = self.make_draft()
        self.submit_and_approve(name)
        self.assertTrue(self.install(name)["ok"])
        # 改一版（正文变了 → 批准失效）→ 重批 → 覆盖安装
        self.edit_body(name, "\n第二版：增加课前预习单。\n")
        self.assertTrue(skill_studio.submit_review(name, "teacher-A")["ok"])
        self.assertTrue(skill_studio.approve(name, "reviewer-H", note="改后重批")["ok"])
        blocked = self.install(name)
        self.assertFalse(blocked["ok"])
        self.assertEqual(blocked["gate"], "overwrite")         # 需明确允许
        got = self.install(name, allow_overwrite=True)
        self.assertTrue(got["ok"], got)
        self.assertTrue(got["backup"])
        self.assertTrue(Path(got["backup"]).exists())

    def test_validate_draft_rejects_executables(self):
        name, _d = self.make_draft()
        (self.skill_dir(name) / "run.cmd").write_text("echo hi", encoding="utf-8")
        v = skill_studio.validate_draft(name)
        self.assertFalse(v["ok"])
        self.assertTrue(any("可执行文件" in p for p in v["problems"]), v["problems"])


# ════════════════════════════════════════════════════ 7 冷却
class TestCooldown(StudioTestCase):
    def test_reject_sets_cooldown(self):
        """doc §8.1-7：拒绝后进入冷却期，不再重复提醒（候选箱带 cooling_until）。"""
        self.seed([U, U2, U3])
        miner.scan()
        c = skill_studio.candidates()["candidates"][0]
        r = miner.reject(c["candidate_id"], reason="不需要", actor="teacher-A")
        self.assertTrue(r["ok"], r)
        self.assertGreater(miner.rejection_until(c["signature"]), 0)
        miner.scan()
        row = [x for x in skill_studio.candidates()["candidates"]
               if x["candidate_id"] == c["candidate_id"]][0]
        self.assertGreater(row["cooldown_until"], 0)
        self.assertEqual(row["state"], "rejected")

    def test_draft_reject_returns_to_draft(self):
        """技能草稿被驳回：回到 draft 状态（理由必填）。"""
        name, _d = self.make_draft()
        self.assertTrue(skill_studio.submit_review(name, "teacher-A")["ok"])
        bad = skill_studio.reject(name, "reviewer-G", reason="")
        self.assertFalse(bad["ok"])
        good = skill_studio.reject(name, "reviewer-G", reason="场景描述太泛")
        self.assertTrue(good["ok"], good)
        self.assertEqual(store.effective_status(name), "draft")


# ════════════════════════════════════════════════════ 8 / 9 团队服务与同步队列
class TestTeamSync(StudioTestCase):
    def test_team_service_down_local_still_works(self):
        """doc §8.1-8：团队服务不可用时本地功能不出错。"""
        os.environ["TENCENT_MEMORY_ENABLED"] = "0"
        os.environ["TENCENT_MEMORY_API_BASE"] = ""
        name, _d = self.make_draft()
        self.assertTrue(self.submit_and_approve(name)["ok"])
        self.assertTrue(self.install(name)["ok"])
        ov = skill_studio.overview()
        self.assertTrue(ov["ok"])
        self.assertFalse(ov["config"]["enabled"] is None)
        # 本地状态/审批/候选箱都照常
        self.assertEqual(store.effective_status(name), "published")
        self.assertTrue(skill_studio.candidates()["ok"])

    def test_unapproved_cannot_enqueue_sync(self):
        """doc §8.1-9：未批准 Skill 不允许进入同步队列。"""
        name, _d = self.make_draft()                       # 只是草稿
        got = skill_studio.enqueue_sync(name, "teacher-A")
        self.assertFalse(got["ok"])
        self.assertEqual(got["gate"], "export_policy")
        self.assertTrue(any("published" in r or "批准" in r for r in got["reasons"]), got["reasons"])
        self.assertEqual(skill_studio.outbox()["count"], 0)

    def test_approved_and_installed_can_enqueue(self):
        name, _d = self.make_draft()
        self.submit_and_approve(name)
        self.assertTrue(self.install(name)["ok"])
        pol = skill_privacy.export_policy(name)
        self.assertTrue(pol["allowed"], pol["reasons"])
        got = skill_studio.enqueue_sync(name, "teacher-A")
        self.assertTrue(got["ok"], got)
        self.assertEqual(skill_studio.outbox()["count"], 1)
        meta = skill_studio.outbox()["items"][0]
        self.assertEqual(meta["skill"], name)
        self.assertTrue(meta["content_hash"].startswith("sha256:"))
        self.assertIn("resources", meta)

    def test_sensitive_content_blocks_export(self):
        name, d = self.make_draft()
        # 往技能目录塞一个含学生信息的文件
        (d / "notes.md").write_text("学生姓名：张三 成绩：90 分", encoding="utf-8")
        self.submit_and_approve(name)
        self.assertTrue(self.install(name)["ok"])
        pol = skill_privacy.export_policy(name)
        self.assertFalse(pol["allowed"])
        self.assertTrue(any("敏感信息" in r for r in pol["reasons"]), pol["reasons"])


# ════════════════════════════════════════════════════ §8.2 端到端验收
class TestEndToEndAcceptance(StudioTestCase):
    def test_doc_8_2_full_flow(self):
        """连续 4 次相近对话 → 候选 → 草稿 → 修改 → 重批 → 安装 → 同步。"""
        # ① 4 次相近对话
        self.seed([U, U2, U3, "《劝学》45 分钟文本精读，文言实词分层练习，再给 A/B/C 三层。"])
        miner.scan()
        cands = skill_studio.candidates()["candidates"]
        self.assertEqual(len(cands), 1, cands)
        c = cands[0]
        self.assertEqual(c["studio_state"], "detected")

        # ② 生成草稿（候选 → draft）
        with mock.patch("edu_agent.permissions.policy", return_value="allow"):
            d = packager.make_draft(c["candidate_id"], actor="teacher-A", confirm=True,
                                    session_reader=lambda s: None)
        self.assertTrue(d["ok"], d)
        skill_id = d["id"]

        # ③ 教师改成「人教版必修上册」→ 结构校验
        v = skill_studio.validate_draft(skill_id)
        self.assertTrue(v["ok"], v["problems"])

        # ④ 提交审查（内部三重校验）→ 批准
        self.assertTrue(skill_studio.submit_review(skill_id, "teacher-A")["ok"])
        self.assertTrue(skill_studio.approve(skill_id, "reviewer-G")["ok"])

        # ⑤ 修改后必须**重新批准**
        self.edit_body(skill_id, "\n- 教材版本：人教版必修上册\n",
                       title="高一语文文本精读与分层练习（人教版必修上册）")
        stale = self.install(skill_id)
        self.assertFalse(stale["ok"])
        self.assertEqual(stale["gate"], "stale")
        self.assertTrue(skill_studio.submit_review(skill_id, "teacher-A")["ok"])
        self.assertTrue(skill_studio.approve(skill_id, "reviewer-G", note="改后重批")["ok"])

        # ⑥ 安装 → 本地生效
        inst = self.install(skill_id)
        self.assertTrue(inst["ok"], inst)
        self.assertTrue(paths.is_inside_runtime(skill_id))
        self.assertTrue(store.is_loadable(skill_id)[0])

        # ⑦ 未同步前，团队端看不到
        self.assertEqual(skill_studio.outbox()["count"], 0)
        pol = skill_privacy.export_policy(skill_id)
        self.assertTrue(pol["allowed"], pol["reasons"])

        # ⑧ 手动同步 → 入队（含哈希与资源清单）
        q = skill_studio.enqueue_sync(skill_id, "teacher-A")
        self.assertTrue(q["ok"], q)
        self.assertEqual(skill_studio.outbox()["count"], 1)


# ════════════════════════════════════════════════════ doc34 §5.5 路径白名单
class TestSafeImport(StudioTestCase):
    def test_import_rejects_path_outside_roots(self):
        """§5.5：不得接受客户端传入的本机绝对路径（否则等于任意文件读取）。"""
        from edu_agent.skill_market import safe_import

        got = safe_import.resolve_import_path(r"C:\Windows\System32\drivers\etc\hosts")
        self.assertFalse(got["ok"])
        self.assertIn("导入根之外", got["error"])
        for bad in (r"C:\Users\auror\.dsh\.credentials.yaml",
                    "D:\\ruanjian\\deep code\\edu-agent\\.env",
                    "../../../../etc/passwd"):
            self.assertFalse(safe_import.resolve_import_path(bad)["ok"], bad)

    def test_import_accepts_path_inside_root(self):
        from edu_agent.skill_market import safe_import

        root = safe_import.import_roots()[0]
        root.mkdir(parents=True, exist_ok=True)
        f = root / "probe-skill.md"
        f.write_text("---\nname: probe-x\ndescription: d\nowner: t\n"
                     "allowed_capabilities: [\"textbook:read\"]\ndata_scope: [\"teaching_context\"]\n"
                     "tests: [inline-tests(1)]\n---\n正文\n", encoding="utf-8")
        try:
            self.assertTrue(safe_import.resolve_import_path(str(f))["ok"])
            self.assertTrue(safe_import.resolve_import_path("probe-skill.md")["ok"])   # 相对路径
            self.assertFalse(safe_import.resolve_import_path("probe-skill.md/../../../x")["ok"])
        finally:
            f.unlink(missing_ok=True)

    def test_resource_name_whitelist(self):
        from edu_agent.skill_market import safe_import

        for ok_name in ("plugin.py", "SKILL.md", "fixture.json", "notes.txt"):
            self.assertTrue(safe_import.validate_resource_name(ok_name)[0], ok_name)
        for bad in ("../evil.py", "a/b.py", "run.exe", "x.cmd", ".hidden.py",
                    "shell.ps1", "key.pem", "app.js", ""):
            ok, why, _ = safe_import.validate_resource_name(bad)
            self.assertFalse(ok, bad)
            self.assertTrue(why)

    def test_skill_id_whitelist(self):
        from edu_agent.skill_market import safe_import

        self.assertTrue(safe_import.validate_skill_id("textbook-mindmap")[0])
        for bad in ("TextbookMindMap", "a", "has space", "../evil", ""):
            self.assertFalse(safe_import.validate_skill_id(bad)[0], bad)


# ════════════════════════════════════════════════════ doc34 §5.3 最小结构
class TestDraftStructure(StudioTestCase):
    GEN_NO_SECTIONS = ("---\nname: gen-skill\ndescription: 自动生成的草稿但缺段\nowner: t\n"
                       "source: conversation_candidate\nplugin: plugin.py\n"
                       "allowed_capabilities: [\"textbook:read\"]\ndata_scope: [\"teaching_context\"]\n"
                       "tests:\n  - input: {chapter_no: 2}\n    expect_contains: \"x\"\n"
                       "    expect_fields: [\"ok\", \"markdown\"]\n---\n"
                       "# gen-skill\n随便写点正文，没有任何规定段落。\n")

    def test_generated_draft_without_sections_fails_validation(self):
        from edu_agent.skill_market import draft as draft_mod

        with mock.patch("edu_agent.permissions.policy", return_value="allow"):
            r = packager.import_draft(self.GEN_NO_SECTIONS, actor="t", confirm=True)
        self.assertTrue(r["ok"], r)
        v = skill_studio.validate_draft("gen-skill")
        self.assertFalse(v["ok"])
        self.assertTrue(any("§5.3" in p for p in v["problems"]), v["problems"])
        self.assertEqual(len(v["sections_missing"]), 7)
        self.assertEqual(v["sections_required"], list(draft_mod.REQUIRED_SECTIONS))

    def test_handwritten_skill_not_forced_into_structure(self):
        """手写技能不强制七段（不能一刀切，否则把已有技能全拒了）。"""
        name, _d = self.make_draft("handwritten-skill")
        v = skill_studio.validate_draft(name)
        self.assertTrue(v["ok"], v["problems"])
        self.assertEqual(v["sections_required"], [])

    def test_build_body_produces_all_sections(self):
        from edu_agent.skill_market import draft as draft_mod

        body = draft_mod.build_body(
            {"title": "高一语文文本精读", "owner": "t", "trigger": "做精读课",
             "inputs": ["textbook_id"], "outputs": ["lesson_doc"], "steps": ["读文本", "分层练习"],
             "warnings": ["示例警告"], "session_id": "s1", "source_chars": 120,
             "fixture": {"expected_digest": "abc"}},
            allowed=("textbook:read",), data_scope=("teaching_context",))
        self.assertEqual(draft_mod.missing_sections(body), [])
        for sec in draft_mod.REQUIRED_SECTIONS:
            self.assertIn("## " + sec, body)


# ════════════════════════════════════════════════════ doc34 §5.4 superseded
class TestSuperseded(StudioTestCase):
    def test_publish_new_version_marks_old_superseded(self):
        name, _d = self.make_draft()
        self.submit_and_approve(name)
        first = self.install(name)
        self.assertTrue(first["ok"], first)
        v1 = first["version"]
        self.edit_body(name, "\n新版：增加课前预习单。\n")
        self.assertTrue(skill_studio.submit_review(name, "teacher-A")["ok"])
        self.assertTrue(skill_studio.approve(name, "reviewer-G", note="改后重批")["ok"])
        second = self.install(name, allow_overwrite=True)
        self.assertTrue(second["ok"], second)
        self.assertNotEqual(second["version"], v1)
        self.assertEqual(second["superseded_version"], v1)

        got = skill_studio.versions(name)
        self.assertEqual(got["superseded_count"], 1)
        old = [r for r in got["versions"] if r["version"] == v1][0]
        self.assertTrue(old["superseded"])
        self.assertFalse(old["active"])
        self.assertEqual(old["superseded_by"], second["version"])
        self.assertTrue(Path(old["dir"]).is_dir())          # 历史保留，不物理删
        self.assertIn("skill.supersede", [r["event"] for r in lifecycle.history(name)])


# ════════════════════════════════════════════════════ doc34 §6 团队同步
class TestTencentAdapter(StudioTestCase):
    def test_offline_by_default(self):
        """§6.1：默认关闭时零远程调用，本地功能不受影响。"""
        from edu_agent.skill_market import tencent_memory_adapter as tma

        st = tma.status()
        self.assertFalse(st["enabled"])
        self.assertIn("本地模式", st["note"])
        ct = tma.connection_test()
        self.assertTrue(ct["ok"] and ct["skipped"])
        self.assertIn("不做任何远程调用", ct["reason"])
        self.assertEqual(tma.pull_team_metadata()["count"], 0)

    def test_push_requires_approval(self):
        from edu_agent.skill_market import tencent_memory_adapter as tma

        name, _d = self.make_draft()                       # 只是草稿
        got = tma.push_approved(name)
        self.assertFalse(got["ok"])
        self.assertIn("未启用", got["error"])
        os.environ["TENCENT_MEMORY_ENABLED"] = "1"
        os.environ["TENCENT_MEMORY_API_BASE"] = "http://127.0.0.1:59999"
        try:
            asset = tma.to_team_asset(name)                # 即使启用，未批准也推不出
            self.assertFalse(asset["ok"])
            self.assertEqual(asset["gate"], "export_policy")
        finally:
            os.environ["TENCENT_MEMORY_ENABLED"] = "0"
            os.environ.pop("TENCENT_MEMORY_API_BASE", None)

    def test_to_team_asset_contains_only_metadata(self):
        from edu_agent.skill_market import tencent_memory_adapter as tma

        name, _d = self.make_draft()
        self.submit_and_approve(name)
        self.assertTrue(self.install(name)["ok"])
        asset = tma.to_team_asset(name)
        self.assertTrue(asset["ok"], asset.get("reasons"))
        a = asset["asset"]
        self.assertEqual(a["skill_id"], name)
        self.assertTrue(a["content_hash"].startswith("sha256:"))
        flat = json.dumps(a, ensure_ascii=False)
        for forbidden in ("student", "成绩", "姓名"):       # 绝不含学生数据
            self.assertNotIn(forbidden, flat)

    def test_import_team_asset_creates_draft_only(self):
        """§6.2：远程资产导入为**本地草稿**，绝不直接安装。"""
        from edu_agent.skill_market import tencent_memory_adapter as tma

        remote = {"skill_id": "team-skill-x", "version": "2.0.0", "content_hash": "sha256:abc",
                  "files": {"SKILL.md": ("---\nname: team-skill-x\ndescription: 团队来的技能\n"
                                         "owner: team\nallowed_capabilities: [\"textbook:read\"]\n"
                                         "data_scope: [\"teaching_context\"]\n"
                                         "tests: [inline-tests(1)]\n---\n# team-skill-x\n正文\n"),
                            "plugin.py": "x = 1\n"}}
        got = tma.import_team_asset(remote, actor="team-sync")
        self.assertTrue(got["ok"], got)
        self.assertEqual(store.effective_status("team-skill-x"), "draft")
        self.assertFalse(paths.is_inside_runtime("team-skill-x"))
        self.assertFalse(store.is_loadable("team-skill-x")[0])
        self.assertTrue(any(e.get("remote_id") == "team-skill-x"
                            for e in tma.sync_log()["entries"]))

    def test_import_rejects_sensitive_and_bad_resources(self):
        from edu_agent.skill_market import tencent_memory_adapter as tma

        head = ("---\nname: team-skill-y\ndescription: 带隐私\n"
                "owner: t\nallowed_capabilities: [\"textbook:read\"]\n"
                "data_scope: [\"teaching_context\"]\ntests: [inline-tests(1)]\n---\n")
        got = tma.import_team_asset({"skill_id": "team-skill-y",
                                     "files": {"SKILL.md": head + "学生姓名：张三 成绩：88 分\n"}})
        self.assertFalse(got["ok"])
        self.assertIn("敏感信息", got["error"])

        got2 = tma.import_team_asset({"skill_id": "team-skill-z",
                                      "files": {"SKILL.md": head + "正文\n",
                                                "../escape.py": "x=1\n"}})
        self.assertFalse(got2["ok"])
        self.assertIn("不允许的资源", got2["error"])

    def test_sync_matrix_documents_policy(self):
        from edu_agent.skill_market import tencent_memory_adapter as tma

        self.assertEqual(tma.SYNC_MATRIX["teacher_conversation"], "never")
        self.assertEqual(tma.SYNC_MATRIX["student_artifact"], "never")
        self.assertEqual(tma.SYNC_MATRIX["approved_skill"], "manual")
        self.assertEqual(tma.SYNC_MATRIX["team_skill"], "draft_only")


# ════════════════════════════════════════════════════ doc34 §5.1 请求模型
class TestStudioModels(StudioTestCase):
    def test_misspelled_field_is_rejected(self):
        """拼错字段名必须报错 —— 否则 `review`(少个 er) 会静默变成匿名批准。"""
        from edu_agent.skill_market import skill_studio_models as m

        ok, err, _obj = m.validate_or_error(m.ApproveBody, {"review": "G"})
        self.assertFalse(ok)
        self.assertIn("reviewer", err["error"])          # 提示缺哪个必填字段
        self.assertIn("review", err["error"])            # 并指出多传了哪个

        ok2, _e2, obj2 = m.validate_or_error(m.ApproveBody, {"reviewer": "G", "note": "同意"})
        self.assertTrue(ok2)
        self.assertEqual(obj2.reviewer, "G")

    def test_required_fields_enforced(self):
        from edu_agent.skill_market import skill_studio_models as m

        self.assertFalse(m.validate_or_error(m.ApproveBody, {})[0])           # reviewer 必填
        self.assertFalse(m.validate_or_error(m.RejectBody, {"reviewer": "G"})[0])   # reason 必填
        self.assertFalse(m.validate_or_error(m.RejectBody, {"reviewer": "G", "reason": ""})[0])
        # 真正非法的类型要被挡（注意：Pydantic 宽松模式会把 "yes"/"1" 正常转成 True —— 这是
        # 有意的友好行为，不是漏洞；要防的是**拼错字段**与**结构不对**）
        self.assertFalse(m.validate_or_error(m.ModeBody, {"single_user_mode": {"a": 1}})[0])
        self.assertFalse(m.validate_or_error(m.ModeBody, {"single_user_mode": [1, 2]})[0])
        ok, _e, obj = m.validate_or_error(m.ModeBody, {"single_user_mode": "yes"})
        self.assertTrue(ok)
        self.assertIs(obj.single_user_mode, True)

    def test_limits_enforced(self):
        from edu_agent.skill_market import skill_studio_models as m

        big = {"files": {("f%d.py" % i): "x" for i in range(40)}}              # >32 个文件
        self.assertFalse(m.validate_or_error(m.PatchDraftBody, big)[0])
        self.assertFalse(m.validate_or_error(m.McpUserServiceBody, {"timeout": -1})[0])
        self.assertTrue(m.validate_or_error(m.McpUserServiceBody, {"timeout": 8})[0])

    def test_envelope_shape(self):
        """doc §5.5 的固定信封字段齐全。"""
        from edu_agent.skill_market import skill_studio_models as m

        fields = set(m.MutationEnvelope.model_fields)
        for k in ("ok", "candidate_id", "state", "content_hash", "updated_at"):
            self.assertIn(k, fields)
        env = m.MutationEnvelope(ok=True, state="draft", content_hash="sha256:x",
                                 updated_at="2026-10-02T00:00:00")
        self.assertTrue(env.ok)

    def test_studio_api_uses_models(self):
        """接口层真的接了模型（拼错字段的请求被 gate=request 挡下，且不落盘）。"""
        from edu_agent.skill_market import studio_api

        r = studio_api.api_approve("whatever", {"reviewer": "G", "review": "多传的字段"})
        self.assertFalse(r["ok"])
        self.assertEqual(r["gate"], "request")
        r2 = studio_api.api_patch_draft("textbook-mindmap", {"evil_field": 1})
        self.assertFalse(r2["ok"])
        self.assertEqual(r2["gate"], "request")

    def test_schemas_endpoint_lists_models(self):
        from edu_agent.skill_market import studio_api

        got = studio_api.api_schemas()
        self.assertTrue(got["ok"])
        for name in ("ApproveBody", "RejectBody", "InstallBody", "PatchDraftBody",
                     "ImportBody", "TeamImportBody", "ModeBody"):
            self.assertIn(name, got["models"])
        self.assertIn("content_hash", got["envelope"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
