# -*- coding: utf-8 -*-
"""skill-create（自动技能封装）单元测试。

覆盖 M1 运行痕迹 / M2 挖掘器 / M3 封装器 / M4 检验器 / M5 接口闸门，
以及验收 case `TestSkillCreateE2E`（对话里给的 SC-E2E-01）与它的 8 条反向用例。
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

from edu_agent.skill_market import (lifecycle, manifest as mf, miner, packager,  # noqa: E402
                                   runs, store, verifier)

# ---------------------------------------------------------------- 测试数据
STEPS_Q = [{"kind": "skill", "id": "kg.read"}, {"kind": "skill", "id": "question.draft"}]

USER_A1 = "按图谱节点 K1024 给高中学生生成一道函数单调性的题目草稿，要带解析"
REPLY_A1 = ("已完成，草稿如下：\n步骤：\n1. 读节点 K1024\n2. 按学段高中定题型\n"
            "3. 生成题干答案解析\n4. 标注 knowledge_id=K1024\n"
            "输出：\n- 题干：判断 f(x)=x^2-2x 在 [1,+∞) 的单调性\n- 答案：单调递增\n"
            "- 解析：对称轴 x=1，开口向上\n- knowledge_id: K1024")
USER_A2 = "再来一道，还是 K1024，难度提高到拔高"
REPLY_A2 = ("已完成：\n步骤：\n1. 读节点 K1024\n2. 难度设为拔高\n3. 生成题干答案解析\n"
            "输出：\n- 题干：讨论 f(x)=x^3-3x 的单调区间\n- 答案：增区间 (-∞,-1) 与 (1,+∞)\n"
            "- 解析：求导 f'(x)=3x²-3\n- knowledge_id: K1024")
USER_B1 = "帮我按知识点 K1024 出个题，高一，基础难度，要有答案和解析"
REPLY_B1 = ("已完成，按知识点 K1024 出题：\n1. 读取知识点 K1024\n2. 按高一基础难度命题\n"
            "3. 补答案与解析\n输出：\n- 题干：求 f(x)=2x+1 在 R 上的单调性\n"
            "- 答案：单调递增\n- 解析：一次项系数 2>0\n- knowledge_id: K1024")
USER_C1 = "什么是导数？用一句话说明"
REPLY_C1 = "导数是函数在某点的瞬时变化率，几何上是切线斜率。"

SESS_A = {"id": "sess-A", "title": "图谱节点生成题目", "messages": [
    {"role": "user", "content": USER_A1}, {"role": "assistant", "content": REPLY_A1},
    {"role": "user", "content": USER_A2}, {"role": "assistant", "content": REPLY_A2}]}
SESS_B = {"id": "sess-B", "title": "出题", "messages": [
    {"role": "user", "content": USER_B1}, {"role": "assistant", "content": REPLY_B1}]}
SESS_C = {"id": "sess-C", "title": "概念问答", "messages": [
    {"role": "user", "content": USER_C1}, {"role": "assistant", "content": REPLY_C1}]}

READER = {"sess-A": SESS_A, "sess-B": SESS_B, "sess-C": SESS_C}


def reader(sid: str):
    return READER.get(sid)


class CreateTestCase(unittest.TestCase):
    """公共夹具：市场目录指到仓库内临时目录（沙箱不允许写系统 %TEMP% 的子目录）。"""

    def setUp(self):
        base = Path(__file__).resolve().parents[1] / ".tmp-skill-create"
        base.mkdir(parents=True, exist_ok=True)
        self.tmp = base / ("t-" + uuid.uuid4().hex[:8])
        self.tmp.mkdir(parents=True)
        self._env = {}
        for k, v in (("EDU_SKILL_MARKET_DIR", str(self.tmp / "market")),
                     ("EDU_SKILL_RUNTIME_DIR", str(self.tmp / "runtime"))):
            self._env[k] = os.environ.get(k)
            os.environ[k] = v
        (self.tmp / "runtime").mkdir(parents=True, exist_ok=True)
        runs._last_turn.clear()
        runs.begin_turn("")
        runs.set_config(trace_enabled=True, min_sessions=2, window_days=30,
                        consistency_min=1.0, reject_cooldown_days=30, dedupe_threshold=0.9)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        runs._last_turn.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- 便捷 ----
    def seed(self, sessions=(SESS_A, SESS_B, SESS_C), *, steps=None, include_c=False):
        """把会话折成轮次并写运行痕迹。"""
        n = 0
        for sess in sessions:
            last_user = ""
            turn = 0
            for m in sess["messages"]:
                if m["role"] == "user":
                    last_user = m["content"]
                elif m["role"] == "assistant" and last_user:
                    turn += 1
                    runs.record_turn(sess["id"], turn, last_user, m["content"],
                                     steps=[dict(s) for s in (steps if steps is not None
                                                              else STEPS_Q)])
                    n += 1
                    last_user = ""
        return n

    def candidate(self, sessions=(SESS_A, SESS_B), **kw):
        self.seed(sessions, **kw)
        miner.scan()
        cands = miner.list_candidates()
        self.assertTrue(cands, "没有产出候选")
        return cands[0]

    def drafted(self, sessions=(SESS_A, SESS_B)):
        c = self.candidate(sessions)
        with mock.patch("edu_agent.permissions.policy", return_value="allow"):
            r = packager.make_draft(c["candidate_id"], confirm=True, session_reader=reader)
        self.assertTrue(r["ok"], r)
        return c, r


# ════════════════════════════════════════════════════ M1 运行痕迹
class TestRuns(CreateTestCase):
    def test_record_and_read(self):
        rec = runs.record_turn("s1", 1, USER_A1, REPLY_A1, steps=STEPS_Q)
        self.assertTrue(rec.get("hash"))
        self.assertEqual(rec["outcome"], "success")
        self.assertEqual(rec["output"]["shape"],
                         ["analysis", "answer", "knowledge_id", "stem"])
        self.assertEqual(rec["inputs"]["knowledge_id"], "K1024")
        self.assertEqual(rec["inputs"]["stage"], "高中")
        self.assertEqual(len(runs.runs()), 1)

    def test_failed_and_abandoned_outcomes(self):
        runs.record_turn("s1", 1, "出个题", "抱歉，我无法完成：调用模型失败")
        runs.record_turn("s1", 2, "出个题", "导数是变化率。")
        got = sorted(r["outcome"] for r in runs.runs())
        self.assertEqual(got, ["abandoned", "failed"])

    def test_redaction_no_pii(self):
        runs.record_turn("s1", 1, "学生姓名：张三 手机 13800138000 出个题",
                         "已完成：\n- 题干：x\n- 答案：y\n- 解析：z")
        raw = "".join(p.read_text(encoding="utf-8") for p in Path(os.environ["EDU_SKILL_MARKET_DIR"],
                                                                  "runs").glob("*.jsonl"))
        self.assertNotIn("张三", raw)
        self.assertNotIn("13800138000", raw)

    def test_chain_verify_and_tamper(self):
        runs.record_turn("s1", 1, USER_A1, REPLY_A1, steps=STEPS_Q)
        runs.record_turn("s2", 1, USER_B1, REPLY_B1, steps=STEPS_Q)
        self.assertTrue(runs.verify()["ok"])
        p = sorted(Path(os.environ["EDU_SKILL_MARKET_DIR"], "runs").glob("*.jsonl"))[-1]
        lines = p.read_text(encoding="utf-8").splitlines()
        rec = json.loads(lines[0])
        rec["outcome"] = "failed"           # 手改一行（改成不同的值）
        lines[0] = json.dumps(rec, ensure_ascii=False)
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertFalse(runs.verify()["ok"])

    def test_no_delete_api(self):
        for name in ("delete", "remove", "update", "clear"):
            self.assertFalse(hasattr(runs, name), "运行痕迹不允许暴露 %s" % name)

    def test_sweep_session_is_idempotent(self):
        r1 = runs.sweep_session("sess-A", session_reader=reader)
        self.assertEqual(r1["recorded"], 2)
        r2 = runs.sweep_session("sess-A", session_reader=reader)
        self.assertEqual(r2["recorded"], 0)
        self.assertEqual(r2["skipped"], 2)
        self.assertEqual(len(runs.runs()), 2)

    def test_correction_detected_on_next_turn(self):
        runs.record_turn("s1", 1, USER_A1, REPLY_A1, steps=STEPS_Q)
        runs.record_turn("s1", 2, "不对，重来一次", REPLY_A2, steps=STEPS_Q)
        corr = runs.corrections()
        self.assertIn(1, corr.get("s1", set()))

    def test_trace_disabled_writes_nothing(self):
        runs.set_config(trace_enabled=False)
        runs.record_turn("s1", 1, USER_A1, REPLY_A1, steps=STEPS_Q)
        self.assertEqual(runs.runs(), [])
        runs.set_config(trace_enabled=True)

    def test_skill_call_buffer(self):
        runs.begin_turn("s1")
        runs.record_skill_call("kg.read", ok=True, ms=12, args={"knowledge_id": "K1"})
        rec = runs.record_turn("s1", 1, USER_A1, REPLY_A1)
        self.assertEqual(len(rec["steps"]), 1)
        self.assertEqual(rec["steps"][0]["id"], "kg.read")
        self.assertTrue(rec["steps"][0]["args_digest"].startswith("sha256:"))

    def test_shape_ignores_citation_metadata(self):
        """引用元数据（citations/coverage/第 2 段…）不是产出字段。"""
        reply = ('导数的定义如下：\n- 讲解：变化率\n- 例题：求导\n'
                 '{"citations":[{"section":"1.2","page":12}],"confidence":"high",'
                 '"coverage":"medium"}\n第 2 段：补充说明')
        shape = runs.output_shape(reply)
        self.assertIn("讲解", shape)
        for noise in ("citations", "confidence", "coverage", "page", "section", "第 2 段"):
            self.assertNotIn(noise, shape, shape)


# ════════════════════════════════════════════════════ M2 挖掘器
class TestMiner(CreateTestCase):
    def test_signature_same_intent_different_wording(self):
        """措辞完全不同、能力与产出相同 → 必须落到同一签名（"熟悉你的说话方式"）。"""
        a = miner.signature_for(USER_A1, STEPS_Q, {"knowledge_id": "K1024"},
                                ["stem", "answer", "analysis", "knowledge_id"])
        b = miner.signature_for(USER_B1, STEPS_Q, {"knowledge_id": "K1024"},
                                ["stem", "answer", "analysis", "knowledge_id"])
        self.assertEqual(a, b)
        self.assertEqual(a, "draft-question|kg.read+question.draft|"
                            "analysis+answer+knowledge_id+stem")

    def test_signature_differs_by_capability(self):
        a = miner.signature_for(USER_A1, STEPS_Q, {}, ["stem"])
        b = miner.signature_for(USER_A1, [{"kind": "skill", "id": "ollama-qa"}], {}, ["stem"])
        self.assertNotEqual(a, b)

    def test_cross_session_candidate(self):
        c = self.candidate()
        self.assertEqual(c["runs"], 3)              # A 两轮 + B 一轮
        self.assertEqual(c["sessions"], 2)
        self.assertTrue(all(c["criteria"].values()), c["blocking"])
        self.assertGreater(c["score"], 0.6)

    def test_same_session_repeats_do_not_satisfy_R1(self):
        """SC-N1：同一会话里成功两次，仍然只有 1 个会话 → R1 不过。"""
        self.seed((SESS_A,))
        miner.scan()
        c = miner.list_candidates()[0]
        self.assertEqual(c["runs"], 2)
        self.assertEqual(c["sessions"], 1)
        self.assertFalse(c["criteria"]["R1"])
        self.assertTrue(any("R1" in b for b in c["blocking"]))

    def test_failed_run_blocks_R6(self):
        """SC-N2：同一流程被反复纠正（≥2 次）→ R6 不过。

        注意纠正语里必须仍带着意图词（"重出一道题"），否则那几轮会掉进 other 签名组、
        纠正根本算不到这个流程头上 —— 这也是 intent 规则要放开口语说法的原因。
        """
        self.seed((SESS_A, SESS_B))
        runs.record_turn("sess-D", 1, USER_A1, REPLY_A1, steps=STEPS_Q)
        runs.record_turn("sess-D", 2, "不对，重出一道题，还是 K1024", REPLY_A2, steps=STEPS_Q)
        runs.record_turn("sess-D", 3, "还是不对，再出几道题", REPLY_A1, steps=STEPS_Q)
        miner.scan()
        c = [x for x in miner.list_candidates() if x["signature"].startswith("draft-question")][0]
        self.assertEqual(c["corrections"], 2)
        self.assertFalse(c["criteria"]["R6"])
        self.assertTrue(any("R6" in b for b in c["blocking"]))

    def test_unmapped_capability_blocks_R5(self):
        """SC-N4 的挖掘阶段版本：调了未登记能力 → R5 不过。"""
        self.seed((SESS_A, SESS_B), steps=[{"kind": "skill", "id": "no-such-capability"}])
        miner.scan()
        c = miner.list_candidates()[0]
        self.assertFalse(c["criteria"]["R5"])
        self.assertIn("no-such-capability", c["unmapped_capabilities"])

    def test_plain_qa_session_produces_no_candidate(self):
        self.seed((SESS_C,))
        miner.scan()
        self.assertEqual(miner.list_candidates(), [])

    def test_hash_not_mistaken_for_idcard(self):
        """自家 sha256 里偶然出现的 18 位数字不能被当成身份证（曾随机拦住合法封装）。"""
        fake_hash = "b51ac093cc74d42146c8e5a9a795a3c0ec" + "603900622131297069" + "cebbc5af45a0"
        rec = {"hash": fake_hash, "prev_hash": fake_hash,
               "trigger_text": "按图谱节点生成题目草稿", "seq": 23, "turn": 1}
        self.assertFalse(miner._has_pii(rec))
        # 真身份证（带合法生日）仍然要检出
        self.assertTrue(miner._has_pii({"trigger_text": "身份证 110101199003071234"}))
        self.assertTrue(miner._has_pii({"trigger_text": "手机 13800138000"}))
        self.assertFalse(miner._has_pii({"trigger_text": "普通文本 2026-10-01"}))

    def test_r4_passes_despite_hash_digits(self):
        """整条链路回归：痕迹里的哈希含 18 位数字时，候选仍应通过 R4。"""
        self.seed((SESS_A, SESS_B))
        miner.scan()
        c = [x for x in miner.list_candidates() if x["signature"].startswith("draft-question")][0]
        self.assertTrue(c["criteria"]["R4"], c["blocking"])

    def test_fixture_digest_not_mistaken_for_idcard(self):
        from edu_agent.skill_market import fixture as fixture_mod

        val = fixture_mod.validate_fixture({
            "input": {"knowledge_id": "K1024"},
            "expected_output_shape": {"fields": ["stem", "answer"]},
            "assertions": ["输出字段与期望形状一致"],
            "expected_digest": "sha256:603900622131297069abcdef",
        })
        self.assertTrue(val["ok"], val["blocking"])
        bad = fixture_mod.validate_fixture({
            "input": {"knowledge_id": "K1024"},
            "expected_output_shape": {"fields": ["stem"]},
            "assertions": ["非空"],
            "note": "学生身份证 110101199003071234",
        })
        self.assertFalse(bad["ok"])

    def test_incomplete_trace_blocks_R2(self):
        # 触发语与答复里都不含任何可识别的标识（knowledge_id/学段/学科/难度）→ 痕迹不完整
        runs.record_turn("s1", 1, "帮我处理一下这个", "已完成：\n- 题干：x\n- 答案：y", steps=[])
        runs.record_turn("s2", 1, "再帮我处理一下", "已完成：\n- 题干：z\n- 答案：w", steps=[])
        miner.scan()
        cands = miner.list_candidates()
        self.assertTrue(cands)
        self.assertFalse(cands[0]["criteria"]["R2"])
        self.assertTrue(any("R2" in b for b in cands[0]["blocking"]))

    def test_reject_and_cooldown(self):
        c = self.candidate()
        r = miner.reject(c["candidate_id"], reason="不需要", actor="user")
        self.assertTrue(r["ok"])
        self.assertEqual(miner.get_candidate(c["candidate_id"])["state"], "rejected")
        self.assertGreater(miner.rejection_until(c["signature"]), 0)
        miner.scan()
        self.assertEqual(miner.get_candidate(c["candidate_id"])["state"], "rejected")

    def test_scan_summary(self):
        self.seed((SESS_A, SESS_B, SESS_C))
        s = miner.scan()
        self.assertTrue(s["ok"])
        # A 两轮 + B 一轮 + C 一轮（C 那轮是 abandoned，也会留下痕迹）
        self.assertEqual(s["runs_recorded"], 4)
        self.assertEqual(s["candidates"], 1)
        self.assertEqual(s["verified"], 1)

    def test_score_monotonic_in_runs(self):
        self.seed((SESS_A,))
        miner.scan()
        one = miner.list_candidates()[0]["score"]
        self.seed((SESS_B,))                    # 再来一个会话（B 的轮次还没记过）
        miner.scan()
        two = miner.list_candidates()[0]["score"]
        self.assertGreater(two, one)


# ════════════════════════════════════════════════════ M3 封装器
class TestPackager(CreateTestCase):
    def test_requires_criteria(self):
        self.seed((SESS_A,))                    # R1 不过
        miner.scan()
        c = miner.list_candidates()[0]
        r = packager.make_draft(c["candidate_id"], confirm=True)
        self.assertFalse(r["ok"])
        self.assertEqual(r.get("gate"), "criteria")
        self.assertTrue(r.get("blocking"))

    def test_permission_gate(self):
        c = self.candidate()
        r = packager.make_draft(c["candidate_id"])          # 不带 confirm（默认 ask）
        self.assertFalse(r["ok"])
        self.assertEqual(r.get("gate"), "permission")

    def test_draft_written_without_code(self):
        c, r = self.drafted()
        files = sorted(p.name for p in Path(r["dir"]).iterdir())
        self.assertIn("SKILL.md", files)
        self.assertIn("fixture.json", files)
        self.assertNotIn("plugin.py", files, "自动路径绝不能产出可执行代码")

    def test_front_matter_provenance(self):
        c, r = self.drafted()
        text = Path(r["dir"], "SKILL.md").read_text(encoding="utf-8")
        # doc 34 §5.3 规定的来源标记；七段最小结构同时校验
        self.assertIn("source: conversation_candidate", text)
        self.assertIn("candidate_id: " + c["candidate_id"], text)
        self.assertIn("evidence_runs: 3", text)
        self.assertIn("evidence_sessions: 2", text)

    def test_draft_has_doc34_sections(self):
        """doc 34 §5.3：自动生成的草稿必须七段齐全（教师无需手写 Markdown）。"""
        from edu_agent.skill_market import draft as draft_mod

        _c, r = self.drafted()
        text = Path(r["dir"], "SKILL.md").read_text(encoding="utf-8")
        self.assertEqual(draft_mod.missing_sections(text), [], "缺段：%s" % text[:200])
        for sec in ("适用场景", "必要输入", "工作流程", "输出格式",
                    "教师确认点", "质量与隐私约束", "版本来源"):
            self.assertIn("## " + sec, text)

    def test_allowed_capabilities_from_trace(self):
        c, r = self.drafted()
        self.assertEqual(sorted(r["manifest"]["allowed_capabilities"]),
                         ["kg.read", "question.draft"])
        self.assertIn("knowledge_id", r["manifest"]["data_scope"])

    def test_body_has_evidence_and_defaults_sections(self):
        c, r = self.drafted()
        body = Path(r["dir"], "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("## 证据链", body)
        self.assertIn("## 默认参数", body)
        self.assertIn(c["signature"], body)

    def test_candidate_state_and_audit(self):
        c, r = self.drafted()
        got = miner.get_candidate(c["candidate_id"])
        self.assertEqual(got["state"], "drafted")
        self.assertEqual(got["skill_id"], r["id"])
        from edu_agent.skill_market import audit

        self.assertTrue(any(x["event"] == "skill-create.draft" for x in audit.query()))

    def test_fallback_without_session(self):
        """证据会话已被删除时，用运行痕迹重建素材（仍能出草稿）。"""
        c = self.candidate()
        with mock.patch("edu_agent.permissions.policy", return_value="allow"):
            r = packager.make_draft(c["candidate_id"], confirm=True, session_reader=lambda s: None)
        self.assertTrue(r["ok"], r)
        self.assertTrue(any("重建" in w for w in r["warnings"]))

    def test_duplicate_suppressed(self):
        c, r = self.drafted()
        # 人为把阈值降到很低，模拟"和已有技能高度重合"
        runs.set_config(dedupe_threshold=0.05)
        miner.scan()
        c2 = miner.get_candidate(c["candidate_id"])
        with mock.patch("edu_agent.permissions.policy", return_value="allow"):
            dup = packager.make_draft(c2["candidate_id"], confirm=True, session_reader=reader)
        self.assertFalse(dup["ok"])
        self.assertEqual(dup.get("gate"), "duplicate")
        runs.set_config(dedupe_threshold=0.9)


# ════════════════════════════════════════════════════ M4 检验器
class TestVerifier(CreateTestCase):
    def test_verify_passes_and_sets_state(self):
        c, r = self.drafted()
        v = verifier.verify(c["candidate_id"])
        self.assertTrue(v["ok"], v)
        self.assertEqual(v["L1"]["verdict"], "pass")
        self.assertTrue(v["L2"]["ok"])
        self.assertEqual(v["L2"]["shape_match"], 1.0)
        self.assertEqual(v["L2"]["undeclared_calls"], [])
        self.assertEqual(v["L2"]["side_effects"], "none")
        self.assertFalse(v["L2"]["executed"], "L2 不执行草稿代码")
        self.assertEqual(miner.get_candidate(c["candidate_id"])["state"], "verified")

    def test_verify_requires_draft(self):
        c = self.candidate()
        v = verifier.verify(c["candidate_id"])
        self.assertFalse(v["ok"])
        self.assertEqual(v.get("gate"), "draft")

    def test_undeclared_call_detected(self):
        """SC-N4：痕迹里调过、但 manifest 没声明的能力 → L2 不过。

        真实场景：人工编辑草稿时把 `question.draft` 从 allowed_capabilities 里删了
        （想收紧权限），但流程实际调过它 —— 这正是审核要抓的"声明得比实际用的少"。
        """
        c, r = self.drafted()
        p = Path(r["dir"], "SKILL.md")
        text = p.read_text(encoding="utf-8").replace(
            "allowed_capabilities: kg.read, question.draft", "allowed_capabilities: kg.read")
        p.write_text(text, encoding="utf-8")
        v = verifier.verify(c["candidate_id"])
        self.assertFalse(v["ok"])
        self.assertIn("question.draft", v["L2"]["undeclared_calls"])

    def test_shape_mismatch_detected(self):
        """SC-N5：fixture 期望字段与痕迹形状对不上 → L2 形状一致率不达标。"""
        c, r = self.drafted()
        fx_path = Path(os.environ["EDU_SKILL_MARKET_DIR"], "fixtures", r["id"] + ".json")
        fx = json.loads(fx_path.read_text(encoding="utf-8"))
        fx["expected_output_shape"]["fields"] = ["stem", "answer", "analysis",
                                                 "knowledge_id", "rubric"]
        fx_path.write_text(json.dumps(fx, ensure_ascii=False), encoding="utf-8")
        v = verifier.verify(c["candidate_id"])
        self.assertFalse(v["ok"])
        self.assertIn("rubric", v["L2"]["missing"])

    def test_stale_verification_after_edit(self):
        """检验之后草稿被改过 → submit 必须要求重新检验。"""
        c, r = self.drafted()
        self.assertTrue(verifier.verify(c["candidate_id"])["ok"])
        self.assertTrue(verifier.verification_state(c["candidate_id"])["ok"])
        p = Path(r["dir"], "SKILL.md")
        p.write_text(p.read_text(encoding="utf-8") + "\n\n人工又改了一行\n", encoding="utf-8")
        st = verifier.verification_state(c["candidate_id"])
        self.assertFalse(st["ok"])
        self.assertEqual(st.get("gate"), "stale")

    def test_submit_requires_verified(self):
        c, r = self.drafted()
        self.assertEqual(verifier.verification_state(c["candidate_id"]).get("gate"), "verify")
        self.assertTrue(verifier.verify(c["candidate_id"])["ok"])
        sub = lifecycle.submit(r["id"], "E")
        self.assertTrue(sub["ok"], sub)
        self.assertEqual(store.effective_status(r["id"]), "submitted")
        self.assertFalse(store.is_loadable(r["id"])[0])      # 还没发布，Agent 拿不到


# ════════════════════════════════════════════════════ 验收 case（SC-E2E-01）
class TestSkillCreateE2E(CreateTestCase):
    """对话里给的例子：跨会话挖掘 → 检验 → 草稿 → 提交审核（不自动发布）。"""

    def test_cross_session_mining_to_draft(self):
        # ① 回扫历史会话（幂等；这里注入会话列表，真实环境从会话库读）
        swept = runs.sweep(sessions=[SESS_A, SESS_B, SESS_C], session_reader=reader)
        self.assertTrue(swept["ok"])
        self.assertEqual(swept["recorded"], 4)          # A 两轮 + B 一轮 + C 一轮

        # ② 挖掘
        scan = miner.scan()
        self.assertTrue(scan["ok"])

        # ③ 候选：1 个，跨 2 个会话
        cands = [c for c in miner.list_candidates() if c["signature"].startswith("draft-question")]
        self.assertEqual(len(cands), 1)
        c = cands[0]
        self.assertEqual(c["sessions"], 2)
        self.assertTrue(all(c["criteria"].values()), c["blocking"])
        self.assertGreater(c["score"], 0.6)

        # ④ 草稿（过权限闸门；仍不产代码）
        with mock.patch("edu_agent.permissions.policy", return_value="allow"):
            d = packager.make_draft(c["candidate_id"], confirm=True, session_reader=reader)
        self.assertTrue(d["ok"], d)
        files = sorted(p.name for p in Path(d["dir"]).iterdir())
        self.assertEqual(files, ["SKILL.md", "fixture.json"])

        # ⑤ 检验
        v = verifier.verify(c["candidate_id"])
        self.assertTrue(v["ok"], v)

        # ⑥ 提交审核（不自动发布）
        st = verifier.verification_state(c["candidate_id"])
        self.assertTrue(st["ok"])
        sub = lifecycle.submit(d["id"], "E")
        self.assertTrue(sub["ok"], sub)
        self.assertEqual(store.effective_status(d["id"]), "submitted")
        self.assertFalse(store.is_loadable(d["id"])[0])


# ════════════════════════════════════════════════════ 第二轮：L3 / L4 / 定时 / 提示
PLUGIN_OK = '''# -*- coding: utf-8 -*-
from typing import Annotated

from semantic_kernel.functions import kernel_function

from edu_agent.skill_market.contract import ModuleClient, TeachingContext


class Draft:
    @kernel_function(name="draft_question", description="生成题目草稿")
    def draft_question(self,
                       knowledge_id: Annotated[str, "节点标识"],
                       difficulty: Annotated[str, "难度"] = "基础") -> dict:
        ctx = TeachingContext(knowledge_id=knowledge_id)
        node = ModuleClient({"module2": "http://127.0.0.1:5180"}).read_knowledge_node(ctx)
        return {"stem": "题干", "answer": "答案", "analysis": "解析",
                "knowledge_id": knowledge_id, "node": node}
'''

PLUGIN_BOOM = '''# -*- coding: utf-8 -*-
from semantic_kernel.functions import kernel_function


class Draft:
    @kernel_function(name="draft_question", description="会炸")
    def draft_question(self, knowledge_id: str = "") -> dict:
        raise RuntimeError("故意炸一下")
'''

PLUGIN_EMPTY = '''# -*- coding: utf-8 -*-
class Draft:
    def helper(self) -> dict:
        return {}
'''


class TestHealthL4(CreateTestCase):
    """M6：发布后回归（L4）。"""

    def _install_fake(self, skill_id="installed-skill"):
        """造一个"已安装"的假技能（只放一个 SKILL.md 到运行时目录）。"""
        d = Path(os.environ["EDU_SKILL_RUNTIME_DIR"], skill_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text("---\nname: %s\ndescription: d\n---\n\n正文\n" % skill_id,
                                    encoding="utf-8")
        return skill_id

    def test_only_installed_skills_are_recorded(self):
        from edu_agent.skill_market import health

        self.assertEqual(health.record_run("not-installed", ok=True), {})
        sid = self._install_fake()
        rec = health.record_run(sid, ok=True, ms=120)
        self.assertTrue(rec.get("hash"))
        self.assertEqual(len(health.skill_stats()), 1)

    def test_ok_rate_and_attention(self):
        from edu_agent.skill_market import health

        sid = self._install_fake()
        for i in range(6):
            health.record_run(sid, ok=(i < 3), ms=100 + i, error="" if i < 3 else "上游 500")
        st = health.skill_stats()[0]
        self.assertEqual(st["calls"], 6)
        self.assertEqual(st["ok"], 3)
        self.assertEqual(st["ok_rate"], 0.5)
        self.assertTrue(st["attention"])
        self.assertIn("成功率", st["reason"])
        self.assertIn("上游 500", st["last_error"])
        self.assertGreaterEqual(st["latency_p95_ms"], st["latency_p50_ms"])

    def test_small_sample_not_flagged(self):
        from edu_agent.skill_market import health

        sid = self._install_fake()
        health.record_run(sid, ok=False)
        st = health.skill_stats()[0]
        self.assertFalse(st["attention"])
        self.assertIn("样本还少", st["reason"])

    def test_health_summary_and_no_auto_disable(self):
        from edu_agent.skill_market import health

        sid = self._install_fake()
        for _ in range(6):
            health.record_run(sid, ok=False, error="挂了")
        h = health.health()
        self.assertEqual(h["attention_count"], 1)
        self.assertIn("不会自动停用", h["hint"])
        # 只提示，不改变安装状态
        self.assertTrue(Path(os.environ["EDU_SKILL_RUNTIME_DIR"], sid).exists())

    def test_health_config_switch(self):
        from edu_agent.skill_market import health

        sid = self._install_fake()
        # 交错成功/失败：前后半段成功率一致，避免触发"下滑"那条规则
        for ok in (True, True, False, True, True, False):
            health.record_run(sid, ok=ok)
        self.assertTrue(health.skill_stats()[0]["attention"])      # 0.667 < 0.8
        runs.set_config(health_min_ok_rate=0.5)                    # 放宽阈值
        st = health.skill_stats()[0]
        self.assertFalse(st["attention"], st["reason"])
        runs.set_config(health_min_ok_rate=0.8)

    def test_trend_drop_flags_attention(self):
        """总成功率还达标、但最近几次基本都失败 → 也要提示（刚坏）。"""
        from edu_agent.skill_market import health

        sid = self._install_fake()
        for i in range(15):
            health.record_run(sid, ok=(i < 12))          # 12/15 = 0.8 达标，最近 3 次全失败
        st = health.skill_stats()[0]
        self.assertEqual(st["ok_rate"], 0.8)
        self.assertTrue(st["attention"])
        self.assertIn("下滑", st["reason"])


class TestShadowL3(CreateTestCase):
    """M7：影子运行（L3）—— 真代码跑一次，出站全被拦。"""

    def _draft_with_plugin(self, plugin: str):
        c, r = self.drafted()
        p = Path(r["dir"], "SKILL.md")
        text = p.read_text(encoding="utf-8").replace(
            "tests: fixture.json", "tests: fixture.json\nplugin: plugin.py")
        man, body = mf.from_document(text)
        store.write_draft(man, body, files={"plugin.py": plugin}, allow_code=True)
        return c, r

    def test_shadow_ok_and_intercepts_outbound(self):
        from edu_agent.skill_market import shadow

        c, r = self._draft_with_plugin(PLUGIN_OK)
        out = shadow.shadow_run(c["candidate_id"])
        self.assertTrue(out["ok"], out)
        l3 = out["L3"]
        self.assertTrue(l3["executed"])
        self.assertEqual(l3["shape_match"], 1.0)
        self.assertTrue(l3["outbound_attempts"], "应当记到一次出站尝试")
        self.assertEqual(l3["undeclared_targets"], [])
        self.assertIn("被记录桩拦截", l3["note"])

    def test_shadow_plugin_error(self):
        from edu_agent.skill_market import shadow

        c, _ = self._draft_with_plugin(PLUGIN_BOOM)
        out = shadow.shadow_run(c["candidate_id"])
        self.assertFalse(out["ok"])
        self.assertIn("故意炸一下", str(out.get("error")))

    def test_shadow_no_kernel_function(self):
        from edu_agent.skill_market import shadow

        c, _ = self._draft_with_plugin(PLUGIN_EMPTY)
        out = shadow.shadow_run(c["candidate_id"])
        self.assertFalse(out["ok"])
        self.assertIn("@kernel_function", str(out.get("error")))

    def test_shadow_skipped_without_plugin(self):
        from edu_agent.skill_market import shadow

        c, _ = self.drafted()
        out = shadow.shadow_run(c["candidate_id"])
        self.assertFalse(out["ok"])
        self.assertTrue(out.get("skipped"))
        self.assertIn("纯提示词技能", out["reason"])

    def test_submit_requires_shadow_when_plugin_declared(self):
        from edu_agent.skill_market import shadow

        c, r = self._draft_with_plugin(PLUGIN_OK)
        self.assertTrue(verifier.verify(c["candidate_id"])["ok"])
        st = verifier.verification_state(c["candidate_id"])
        self.assertFalse(st["ok"])
        self.assertEqual(st.get("gate"), "shadow")           # 有代码就必须影子运行
        self.assertTrue(shadow.shadow_run(c["candidate_id"])["ok"])
        self.assertTrue(verifier.verification_state(c["candidate_id"])["ok"])
        self.assertTrue(lifecycle.submit(r["id"], "E")["ok"])

    def test_require_shadow_can_be_disabled(self):
        c, r = self._draft_with_plugin(PLUGIN_OK)
        self.assertTrue(verifier.verify(c["candidate_id"])["ok"])
        runs.set_config(require_shadow=False)
        self.assertTrue(verifier.verification_state(c["candidate_id"])["ok"])
        runs.set_config(require_shadow=True)


class TestSchedulerAndPrompt(CreateTestCase):
    """M9 定时扫描 + M8 半自动提示。"""

    def setUp(self):
        super().setUp()
        # 调度器的 _state 是进程级全局：跑在同一个进程里的其它测试文件（历史上 import
        # web_server 会拉起调度线程）会留下"线程活着 / runs 计数不为 0"的残留，
        # 导致单独跑全绿、全量跑失败。这里显式重置，让本类不依赖执行顺序。
        from edu_agent.skill_market import scheduler

        scheduler.reset()

    def test_scheduler_disabled_when_interval_zero(self):
        from edu_agent.skill_market import scheduler

        runs.set_config(scan_interval_hours=0)
        r = scheduler.start()
        self.assertTrue(r.get("disabled"))
        self.assertFalse(r.get("running"))
        runs.set_config(scan_interval_hours=24)

    def test_scheduler_status_fields(self):
        from edu_agent.skill_market import scheduler

        st = scheduler.status()
        for k in ("interval_hours", "running", "runs", "last_run", "next_run"):
            self.assertIn(k, st)

    def test_scheduler_run_once_updates_board(self):
        from edu_agent.skill_market import scheduler

        self.seed((SESS_A, SESS_B))
        r = scheduler.run_once(sweep=False)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["scan"]["candidates"], 1)
        self.assertEqual(scheduler.status()["runs"], 1)

    def test_prompt_once_per_session(self):
        self.seed((SESS_A, SESS_B))
        miner.scan()
        p1 = miner.prompt_for_session("sess-B")
        self.assertTrue(p1["ask"], p1)
        self.assertIn("跑通", p1["text"])
        p2 = miner.prompt_for_session("sess-B")          # 同一会话不重复唠叨
        self.assertFalse(p2["ask"])
        p3 = miner.prompt_for_session("sess-A")          # 别的会话可以再提一次
        self.assertTrue(p3["ask"])

    def test_prompt_disabled_by_config(self):
        self.seed((SESS_A, SESS_B))
        miner.scan()
        runs.set_config(prompt_in_chat=False)
        self.assertFalse(miner.prompt_for_session("sess-B")["ask"])
        runs.set_config(prompt_in_chat=True)

    def test_prompt_skips_unverified_candidate(self):
        self.seed((SESS_A,))                             # 只有 1 个会话 → R1 不过
        miner.scan()
        self.assertFalse(miner.prompt_for_session("sess-A")["ask"])


class TestYamlDraftAndImport(CreateTestCase):
    """手写 SKILL.md（YAML 块状 front-matter）导入 + textbook:read 这类平台能力。"""

    YAML_DRAFT = """---
version: "1.0.0"
status: "draft"
inputs:
  - name: book_id
    type: string
    description: 教材库里面书本唯一id
  - name: chapter_no
    type: integer
    description: 需要生成思维导图的章节序号
allowed_capabilities: ["textbook:read"]
data_scope: ["teaching_context"]
owner: "test_agent"
tests:
  - input: {"book_id":"math_grade7","chapter_no":2}
    expect_contains: "- 章节思维导图"
---
# textbook_mindmap_generator
## 功能
根据book_id与章节号，读取本地教材库对应课本内容，自动生成该章节Markdown思维导图。
"""

    def test_parse_yaml_front_matter(self):
        man, body = mf.from_document(self.YAML_DRAFT)
        self.assertEqual(man.name, "textbook_mindmap_generator")     # 从 H1 标题兜出来
        self.assertEqual(man.version, "1.0.0")
        self.assertEqual(man.status, "draft")
        self.assertEqual(man.owner, "test_agent")
        self.assertEqual(man.inputs, ("book_id", "chapter_no"))
        self.assertEqual([d.get("type") for d in man.inputs_spec], ["string", "integer"])
        self.assertEqual(man.allowed_capabilities, ("textbook:read",))
        self.assertEqual(man.data_scope, ("teaching_context",))
        self.assertEqual(man.inline_tests[0]["input"]["chapter_no"], 2)
        self.assertIn("功能", body)

    def test_yaml_roundtrip_keeps_blocks_and_line_compat(self):
        """结构化字段序列化后仍能被读回；简单字段仍是单行（capabilities 按行读）。"""
        from edu_agent import capabilities

        man, body = mf.from_document(self.YAML_DRAFT)
        text = man.to_document(body)
        again, _ = mf.from_document(text)
        self.assertEqual(again.inputs_spec, man.inputs_spec)
        self.assertEqual(again.inline_tests, man.inline_tests)
        self.assertEqual(again.allowed_capabilities, ("textbook:read",))
        p = self.tmp / "SKILL.md"
        p.write_text(text, encoding="utf-8")
        meta = capabilities._parse_front_matter(p)
        self.assertEqual(meta["name"], "textbook_mindmap_generator")
        self.assertEqual(meta["owner"], "test_agent")

    def test_textbook_read_is_registered(self):
        from edu_agent.skill_market import capability_registry as caps

        ok, why = caps.resolve_capability("textbook:read")
        self.assertTrue(ok, why)
        self.assertIn("教材库", why)
        self.assertEqual(caps.targets_for(["textbook:read"]), {"platform"})
        self.assertEqual(caps.targets_for(["kg.read"]), {"module2"})
        self.assertEqual(caps.classify_url("http://127.0.0.1:5174/api/textbook/chapters"),
                         "platform")
        self.assertEqual(caps.classify_url("http://127.0.0.1:5180/api/knowledge/node"),
                         "module2")
        self.assertEqual(caps.classify_url("https://api.example.com/x"), "external")

    def test_import_draft_end_to_end(self):
        with mock.patch("edu_agent.permissions.policy", return_value="allow"):
            r = packager.import_draft(self.YAML_DRAFT, actor="test_agent", confirm=True)
        self.assertTrue(r["ok"], r)
        self.assertEqual(r["id"], "textbook_mindmap_generator")
        # description 从正文「## 功能」自动补出来
        self.assertIn("教材库", r["derived"].get("description", ""))
        # fixture 由 tests 块生成，断言含 expect_contains
        fx = r["fixture"]
        self.assertEqual(fx["input"]["chapter_no"], 2)
        self.assertTrue(any("- 章节思维导图" in a for a in fx["assertions"]), fx["assertions"])
        self.assertEqual(r["scan"]["verdict"], "pass")
        # 导入后进入草稿态，且草稿区不允许有可执行文件
        self.assertEqual(store.effective_status(r["id"]), "draft")
        self.assertFalse(list(Path(r["dir"]).glob("*.py")))

    def test_import_requires_permission_gate(self):
        r = packager.import_draft(self.YAML_DRAFT)        # 不带 confirm（默认 ask）
        self.assertFalse(r["ok"])
        self.assertEqual(r.get("gate"), "permission")

    def test_verify_and_shadow_accept_skill_id(self):
        """手写草稿没有候选 id，verify/shadow 必须也认技能 id。"""
        from edu_agent.skill_market import shadow

        with mock.patch("edu_agent.permissions.policy", return_value="allow"):
            r = packager.import_draft(self.YAML_DRAFT, confirm=True)
        self.assertTrue(r["ok"], r)
        v = verifier.verify("textbook_mindmap_generator")
        self.assertTrue(v["ok"], v)
        self.assertEqual(v["L1"]["verdict"], "pass")
        # 没有 plugin.py 的纯提示词技能：L3 跳过，verification_state 直接放行
        self.assertEqual(verifier.verification_state("textbook_mindmap_generator")["ok"], True)
        sh = shadow.shadow_run("textbook_mindmap_generator")
        self.assertTrue(sh.get("skipped"), sh)

    def test_inline_fixture_assertion_expect_contains(self):
        from edu_agent.skill_market import fixture as fixture_mod

        fx = fixture_mod.from_inline_tests([{"input": {"chapter_no": 2},
                                             "expect_contains": "- 章节思维导图"}],
                                           skill_id="x")
        self.assertTrue(fixture_mod.validate_fixture(fx)["ok"])
        # expect_contains 断言：包含即过、不包含即不过
        self.assertTrue(fixture_mod._assert_one(fx["assertions"][0],
                                                {"markdown": "- 章节思维导图\n  - 第二章"}, fx))
        self.assertFalse(fixture_mod._assert_one(fx["assertions"][0],
                                                 {"markdown": "别的内容"}, fx))
        # 隐私断言优先（"不得包含"里也有"包含"两个字）
        self.assertFalse(fixture_mod._assert_one("不得包含学生姓名/学号/作答明文",
                                                 {"markdown": "学生 13800138000"}, fx))


if __name__ == "__main__":
    unittest.main(verbosity=2)
