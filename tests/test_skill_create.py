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

from edu_agent.skill_market import (lifecycle, miner, packager, runs, store, verifier)

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
        self.assertIn("source: skill-create", text)
        self.assertIn("candidate_id: " + c["candidate_id"], text)
        self.assertIn("evidence_runs: 3", text)
        self.assertIn("evidence_sessions: 2", text)

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
