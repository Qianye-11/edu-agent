# -*- coding: utf-8 -*-
"""skill-create · M3 封装器：候选 + 证据 → SKILL.md 草稿。

三条约束：

1. **要求"跑通"**：候选的 R1~R6 必须全过，否则拒绝（`gate: "criteria"`，附差哪条）；
2. **仍然不产可运行代码**：走 `store.write_draft()` 默认通道（拒收 .py），
   插件依旧由人工按 `templates/plugin_template.py` 编写 —— 这一条不因为"自动"而放松；
3. **过权限闸门**：`permissions.guard("skill_create_draft")`（默认 ask，可改 allow/deny）。

额外做三件"人味"的事：

* **记忆融合**：把长期记忆里该用户的稳定偏好（学段/学科/难度）写成草稿的默认参数建议
  —— 这是"熟悉你"的落地方式，而不是让技能自己猜；
* **去重**：与市场已有技能、其它候选比相似度，重合度过高就抑制（不重复建草稿）；
* **来源链**：front-matter 写 `source: skill-create` / `candidate_id` / `evidence_runs`，
  审计里能一路追到是哪几次运行把它"喂"出来的。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from edu_agent.skill_market import audit, capability_registry as caps, draft as draft_mod
from edu_agent.skill_market import manifest as mf, miner, perm_scan, runs, store

MEM_PROFILE = "default"          # 与 web_server 的 MEM_PROFILE 对齐（多用户时按登录身份替换）
ACTION = "skill_create_draft"


# ---------------------------------------------------------------- 工具
def _bigrams(text: str) -> set[str]:
    s = "".join(str(text or "").split())
    return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) > 1 else set()


def similarity(a: str, b: str) -> float:
    """字符二元组 Jaccard（够用于"这俩是不是同一个技能"的判断，不需要模型）。"""
    ba, bb = _bigrams(a), _bigrams(b)
    if not ba or not bb:
        return 0.0
    return len(ba & bb) / len(ba | bb)


def _capability_tokens(ids: list[str]) -> tuple[tuple[str, ...], list[str]]:
    """痕迹里的能力 id → manifest 用的令牌（尽量用已登记的）。"""
    tokens: list[str] = []
    unmapped: list[str] = []
    for cid in ids or []:
        for cand in ("skill:" + cid, "mcp:" + cid, cid):
            ok, _ = caps.resolve_capability(cand)
            if ok:
                if cand not in tokens:
                    tokens.append(cand)
                break
        else:
            unmapped.append(cid)
    return tuple(tokens), unmapped


def _data_scope_for(candidate: dict, inputs: dict) -> tuple[str, ...]:
    """按候选实际用到的东西推导 data_scope（不能凭空写全）。"""
    scope: list[str] = []
    blob = " ".join([str(candidate.get("title") or ""), " ".join(candidate.get("shape") or []),
                     " ".join(str(k) for k in (inputs or {}))])
    if "knowledge_id" in blob or "knowledge_id" in (inputs or {}):
        scope.append("knowledge_id")
    if any(k in blob for k in ("stage", "subject", "学段", "学科")) or \
            any(k in (inputs or {}) for k in ("stage", "subject")):
        scope.append("teaching_context")
    if "mastery_snapshot_id" in blob or "学情" in blob or "掌握度" in blob:
        scope.append("mastery_snapshot_id")
    if not scope:
        scope = ["teaching_context"]
    return tuple(dict.fromkeys(scope))


def memory_defaults(profile: str = MEM_PROFILE) -> dict:
    """从长期记忆里取稳定偏好（只取标识类，不取任何学生信息）。"""
    out: dict[str, str] = {}
    try:
        from edu_agent import memory

        for fact in memory.list_facts(profile, limit=60) or []:
            text = str(fact.get("text") or "")
            if not text:
                continue
            for key, rx in (("stage", r"(初中|高中|小学)"),
                            ("subject", r"(数学|语文|英语|物理|化学|生物|历史|地理|政治)"),
                            ("difficulty", r"(基础|提高|拔高)")):
                if key in out:
                    continue
                import re as _re

                m = _re.search(rx, text)
                if m and ("默认" in text or "偏好" in text or "习惯" in text or "总是" in text):
                    out[key] = m.group(1)
    except Exception:  # noqa: BLE001
        pass
    return out


def _dedupe_check(candidate: dict, cfg: dict) -> dict | None:
    """与市场已有技能/其它候选比相似度，返回冲突信息（None = 不冲突）。"""
    thr = float(cfg.get("dedupe_threshold") or 0.6)
    mine = "%s %s" % (candidate.get("title") or "", candidate.get("signature") or "")
    for row in store.market_index():
        other = "%s %s" % (row.get("title") or "", row.get("description") or "")
        s = similarity(mine, other)
        if s >= thr:
            return {"kind": "market", "skill": row.get("name"), "title": row.get("title"),
                    "similarity": round(s, 3), "state": row.get("status")}
    for c in miner.list_candidates():
        if c.get("candidate_id") == candidate.get("candidate_id"):
            continue
        if c.get("state") not in ("drafted", "verified"):
            continue
        s = similarity(mine, "%s %s" % (c.get("title") or "", c.get("signature") or ""))
        if s >= thr:
            return {"kind": "candidate", "skill": c.get("skill_id") or c.get("candidate_id"),
                    "title": c.get("title"), "similarity": round(s, 3), "state": c.get("state")}
    return None


# ---------------------------------------------------------------- 素材
def _ex_from_candidate(candidate: dict, session_reader=None) -> dict:
    """优先从证据会话里抽素材（信息最全），会话不在了就用痕迹里的素材兜底。"""
    sid = ""
    for ev in reversed(candidate.get("evidence") or []):
        if ev.get("session_id"):
            sid = str(ev["session_id"])
            break
    if sid and session_reader is not None:
        try:
            sess = session_reader(sid)
        except Exception:  # noqa: BLE001
            sess = None
        if sess:
            ex = draft_mod.extract_from_session(sess, name_hint=candidate.get("title") or "",
                                                owner="E")
            if ex.get("ok"):
                ex["candidate_session"] = sid
                return ex

    # 兜底：用痕迹重建（保证"有证据就能出草稿"）
    recs = [r for r in runs.runs()
            if r.get("intent_signature") == candidate.get("signature")
            and r.get("outcome") == "success"]
    last = recs[-1] if recs else {}
    steps = []
    for st in (last.get("steps") or []):
        if isinstance(st, dict) and st.get("id"):
            steps.append("调用能力 %s%s" % (st["id"], "（失败）" if st.get("ok") is False else ""))
    if not steps:
        steps = ["按触发语完成该流程", "产出结构化结果"]
    inputs = dict(last.get("inputs") or {})
    shape = list((last.get("output") or {}).get("shape") or candidate.get("shape") or [])
    return {
        "ok": True,
        "skill_id": "",
        "title": candidate.get("title") or candidate.get("intent") or "会话技能草稿",
        "trigger": str(last.get("trigger_text") or candidate.get("title") or "")[:300],
        "steps": steps,
        "inputs": sorted(inputs) or ["teaching_context"],
        "outputs": shape or ["result"],
        "knowledge_refs": [str(inputs.get("knowledge_id"))] if inputs.get("knowledge_id") else [],
        "stage": str(inputs.get("stage") or ""),
        "fixture": {
            "skill": "",
            "source": {"session_id": sid, "captured_at": time.strftime("%Y-%m-%d %H:%M"),
                       "from": "run-trace"},
            "note": "匿名 fixture：由运行痕迹重建，只含标识与结构化槽位",
            "input": {"teaching_context": {"stage": str(inputs.get("stage") or "（待人工确认）"),
                                           "subject": str(inputs.get("subject") or "（待人工确认）")},
                      "knowledge_id": str(inputs.get("knowledge_id") or "（待人工确认）"),
                      "trigger_text": str(last.get("trigger_text") or "")[:200]},
            "expected_output_shape": {"kind": candidate.get("intent") or "workflow",
                                      "fields": shape},
            "expected_digest": str((last.get("output") or {}).get("digest") or ""),
            "assertions": ["输出字段与期望形状一致", "不得包含学生姓名/学号/作答明文"],
        },
        "warnings": ["证据会话已不在，素材由运行痕迹重建：步骤描述较粗，请人工校对"],
        "owner": "E",
        "session_id": sid,
        "source_chars": int((last.get("output") or {}).get("chars") or 0),
        "output_sample": "",
        "candidate_session": sid,
    }


# ---------------------------------------------------------------- 主入口
def make_draft(candidate_id: str, *, actor: str = "E", confirm: bool = False,
               session_reader=None, force: bool = False) -> dict:
    """候选 → 草稿。要求：候选存在 ∧ R1~R6 全过 ∧ 不在冷却期 ∧ 权限闸门放行 ∧ 不重复。"""
    cand = miner.get_candidate(candidate_id)
    if cand is None:
        return {"ok": False, "error": "候选不存在：%s" % candidate_id}
    cid = cand["candidate_id"]

    if cand.get("state") == "rejected" and miner.rejection_until(cand.get("signature") or ""):
        return {"ok": False, "gate": "cooldown", "candidate_id": cid,
                "error": "该候选被你否决过，冷却期内不再生成草稿"}

    bad = [k for k, v in (cand.get("criteria") or {}).items() if not v]
    if bad and not force:
        return {"ok": False, "gate": "criteria", "candidate_id": cid,
                "error": "候选未通过「跑通」判据：" + "；".join(cand.get("blocking") or bad),
                "blocking": cand.get("blocking") or bad}

    try:
        from edu_agent import permissions

        blocked = permissions.guard(ACTION, confirm=bool(confirm))
    except Exception as e:  # noqa: BLE001  权限层不可用 → 按拒绝处理（安全优先）
        blocked = {"ok": False, "gate": "permission", "action": ACTION,
                   "error": "权限层不可用，已按拒绝处理：%s" % e}
    if blocked:
        audit.append("skill-create.draft.blocked", actor=actor, input_text=cid,
                     result={"ok": False, "code": "permission"},
                     gates={"permission": blocked.get("policy")})
        blocked.update({"candidate_id": cid})
        return blocked

    cfg = runs.config()
    dup = _dedupe_check(cand, cfg)
    if dup and not force:
        audit.append("skill-create.draft.duplicate", actor=actor, input_text=cid,
                     result={"ok": False, "code": "duplicate", "match": dup})
        return {"ok": False, "gate": "duplicate", "candidate_id": cid,
                "error": "与已有技能过于相似（%.0f%%）：%s（%s）"
                         % (dup["similarity"] * 100, dup.get("title"), dup.get("skill")),
                "match": dup}

    ex = _ex_from_candidate(cand, session_reader=session_reader)
    if not ex.get("ok"):
        return {"ok": False, "candidate_id": cid,
                "error": "素材不足，无法生成草稿：%s" % ex.get("error")}

    # 用**痕迹里实际产出的形状**覆盖 fixture 的期望字段。
    # 为什么必须覆盖：SKILL-02 的 fixture 模板里写的是"题目草稿"的通用字段（含 difficulty），
    # 而这次挖掘出来的流程实际只产出 4 个字段 —— 不覆盖的话 L2 形状核对会拿模板去对证据，
    # 永远差一个字段（实测踩过）。
    cand_shape = [str(s) for s in (cand.get("shape") or []) if str(s).strip()]
    if cand_shape:
        fx = dict(ex.get("fixture") or {})
        shape_block = dict(fx.get("expected_output_shape") or {})
        shape_block["fields"] = cand_shape
        shape_block.setdefault("kind", cand.get("intent") or "workflow")
        fx["expected_output_shape"] = shape_block
        fx["evidence"] = {"runs": cand.get("runs"), "sessions": cand.get("sessions"),
                          "signature": cand.get("signature")}
        ex["fixture"] = fx

    tokens, unmapped = _capability_tokens(cand.get("capabilities") or [])
    inferred = False
    if not tokens:
        # 历史回扫出来的候选没有"调过哪个技能"的证据 → 按意图推断一份能力清单，
        # 但**必须如实告知**（草稿正文与返回值里都标"推断，需人工确认"）。
        tokens, unmapped = _capability_tokens(cand.get("suggested_capabilities") or [])
        inferred = bool(tokens)
    if not tokens:
        return {"ok": False, "gate": "capabilities", "candidate_id": cid,
                "error": "痕迹里的能力都无法映射到已登记能力：%s" % "、".join(unmapped or ["（空）"])}
    inputs_hint = dict((ex.get("fixture") or {}).get("input") or {})
    scope = _data_scope_for(cand, inputs_hint)
    defaults = memory_defaults()

    title = cand.get("title") or "会话技能草稿"
    skill_id = draft_mod._slug(title, fallback=cand.get("intent") or "skill")
    man = mf.SkillManifest(
        name=skill_id,
        title=title,
        description="从 %d 个会话 / %d 次成功运行自动封装：%s"
                    % (cand.get("sessions") or 0, cand.get("runs") or 0,
                       "、".join(cand.get("shape") or []) or title),
        version="0.1.0",
        status="draft",
        owner=actor,
        category="教学",
        icon="A",
        inputs=tuple(ex.get("inputs") or ()),
        outputs=tuple(ex.get("outputs") or ()),
        allowed_capabilities=tokens,
        data_scope=scope,
        tests=("fixture.json",),
        source="skill-create",
        session_id=str(ex.get("candidate_session") or ""),
        fixture="fixture.json",
        tags=("skill-create", "待人工编辑"),
        extra=(("candidate_id", cid),
               ("evidence_runs", str(cand.get("runs") or 0)),
               ("evidence_sessions", str(cand.get("sessions") or 0)),
               ("signature", str(cand.get("signature") or "")),
               ("capabilities_source", "inferred" if inferred else "observed"),
               ("defaults", ", ".join("%s=%s" % (k, v) for k, v in sorted(defaults.items())) or "")),
    )
    body = _build_body(cand, ex, man, defaults)
    fixture_txt = json.dumps(ex.get("fixture") or {}, ensure_ascii=False, indent=2)

    d = store.write_draft(man, body, files={"fixture.json": fixture_txt})   # allow_code=False
    store.write_fixture(skill_id, ex.get("fixture") or {})
    rep = perm_scan.scan(man, body, fixture_text=fixture_txt,
                         fixture_path=Path(d) / "fixture.json", skill_dir=d)

    miner.set_state(cid, "drafted", skill_id=skill_id, draft_dir=str(d),
                    drafted_at=time.time())
    audit.append("skill-create.draft", skill=skill_id, actor=actor, input_text=cid,
                 result={"ok": True, "code": "drafted", "dir": str(d),
                         "perm_scan": rep.verdict},
                 gates={"permission": "allow", "criteria": "R1-R6 pass",
                        "dedupe": "pass"},
                 extra={"candidate_id": cid, "evidence_runs": cand.get("runs"),
                        "unmapped_capabilities": unmapped})
    return {"ok": True, "candidate_id": cid, "id": skill_id, "dir": str(d),
            "manifest": man.to_dict(), "scan": rep.to_dict(),
            "defaults_from_memory": defaults,
            "capabilities_source": "inferred" if inferred else "observed",
            "warnings": list(ex.get("warnings") or []) + (
                ["能力清单来自**意图推断**（历史痕迹没有技能调用记录）："
                 "请人工确认 %s 是否就是它真正用到的能力" % "、".join(tokens)] if inferred else []) + (
                ["未映射的能力（未写进 allowed_capabilities）：%s" % "、".join(unmapped)]
                if unmapped else []),
            "next_steps": ["跑 /api/skill-create/verify 做 L1+L2 检验",
                           "人工编写 plugin.py（模板在 templates/plugin_template.py）",
                           "通过后 /api/skill-create/submit 提交审核"]}


def _derive_description(body: str) -> str:
    """手写草稿常常只有正文没有 description —— 从正文里取一句（优先「## 功能」下面那句）。"""
    lines = [ln.strip() for ln in str(body or "").splitlines()]
    start = 0
    for i, ln in enumerate(lines):
        if ln.startswith("##") and "功能" in ln:
            start = i + 1
            break
    for ln in lines[start:]:
        if ln and not ln.startswith("#") and not ln.startswith(">"):
            return ln[:200]
    for ln in lines:
        if ln and not ln.startswith("#"):
            return ln[:200]
    return ""


def import_draft(text: str, *, actor: str = "E", confirm: bool = False,
                 description: str = "") -> dict:
    """**导入手写的 SKILL.md**（YAML 块状 front-matter 也认）到市场草稿区。

    与 `make_draft()` 的分工：那个是"从跑通的会话自动生成"，这个是"人工写好了直接送进来"。
    两者之后的流程完全一样（L1 → L2 → L3 → 提交审核），也**同样过 `skill_create_draft` 闸门**、
    同样不产可执行代码（plugin.py 由人工补）。
    """
    man, body = mf.from_document(text)
    if not man.name:
        return {"ok": False, "error": "SKILL.md 缺少 name 字段"}
    # 补默认值（手写草稿常缺 description/title）
    fixes: dict = {}
    if not str(man.description or "").strip():
        fixes["description"] = description or _derive_description(body) or man.name
    if not str(man.title or "").strip():
        fixes["title"] = man.name
    if not str(man.source or "").strip() or man.source == "session":
        fixes["source"] = "manual"
    if fixes:
        import dataclasses

        man = dataclasses.replace(man, **fixes)

    errs = man.errors(strict_caps=True)
    if errs:
        return {"ok": False, "gate": "manifest", "error": "；".join(errs), "errors": errs}

    try:
        from edu_agent import permissions

        blocked = permissions.guard(ACTION, confirm=bool(confirm))
    except Exception as e:  # noqa: BLE001
        blocked = {"ok": False, "gate": "permission", "action": ACTION,
                   "error": "权限层不可用，已按拒绝处理：%s" % e}
    if blocked:
        audit.append("skill-create.import.blocked", skill=man.name, actor=actor,
                     input_text="import SKILL.md",
                     result={"ok": False, "code": "permission"},
                     gates={"permission": blocked.get("policy")})
        return blocked

    # 有 tests 块 → 生成匿名 fixture（L3 影子运行要用）
    fx = None
    if man.inline_tests:
        from edu_agent.skill_market import fixture as fixture_mod

        fx = fixture_mod.from_inline_tests(man.inline_tests, skill_id=man.name)
        val = fixture_mod.validate_fixture(fx)
        if not val["ok"]:
            return {"ok": False, "gate": "fixture", "error": "；".join(val["blocking"]),
                    "validation": val}
        if not man.fixture:
            import dataclasses

            man = dataclasses.replace(man, fixture="fixture.json")

    files = {"fixture.json": json.dumps(fx, ensure_ascii=False, indent=2)} if fx else {}
    d = store.write_draft(man, body, files=files)          # allow_code=False
    if fx:
        store.write_fixture(man.name, fx)
    # 导入 = 进入草稿态（若这个技能之前已发布，运行时那版照常服务，直到重新发布）
    store.write_state(man.name, status="draft", area="draft",
                      verify_state="", verification={}, imported_at=time.time())
    rep = perm_scan.scan(man, body, fixture_text=json.dumps(fx or {}, ensure_ascii=False),
                         fixture_path=(Path(d) / "fixture.json") if fx else None,
                         skill_dir=d)
    audit.append("skill-create.import", skill=man.name, actor=actor,
                 input_text="import SKILL.md",
                 result={"ok": True, "code": "imported", "dir": str(d),
                         "perm_scan": rep.verdict},
                 gates={"permission": "allow", "manifest": "pass"},
                 extra={"capabilities": list(man.allowed_capabilities)})
    return {"ok": True, "id": man.name, "dir": str(d), "manifest": man.to_dict(),
            "fixture": fx, "scan": rep.to_dict(),
            "derived": fixes,
            "next_steps": ["补 plugin.py（人工编写，@kernel_function）",
                           "POST /api/skill-create/verify 做 L1+L2",
                           "POST /api/skill-create/shadow 做 L3",
                           "POST /api/skill-create/submit 提交审核"]}


def _build_body(cand: dict, ex: dict, man: mf.SkillManifest, defaults: dict) -> str:
    """草稿正文：在 SKILL-02 的模板基础上，补"证据链"与"记忆默认值"两节。"""
    base = draft_mod.build_body(ex, allowed=man.allowed_capabilities, data_scope=man.data_scope)
    ev = "\n".join("- 会话 `%s` 第 %s 轮（run #%s，%s）"
                   % (e.get("session_id"), e.get("turn"), e.get("run_seq"), e.get("outcome"))
                   for e in (cand.get("evidence") or [])[:6]) or "- （无）"
    dft = "\n".join("- `%s` = %s" % (k, v) for k, v in sorted(defaults.items())) or "- （记忆里还没有稳定偏好）"
    cap_note = ("\n> ⚠️ 能力清单来自**意图推断**（这段历史没有技能调用记录），"
                "请人工确认后删掉多余的、补上漏掉的。\n"
                if cand.get("capabilities_source") == "inferred" else "")
    extra = f"""
## 证据链（自动封装依据）

- 意图签名：`{cand.get('signature')}`
- 成功运行 {cand.get('runs')} 次 / 跨 {cand.get('sessions')} 个会话，形状一致率 {cand.get('consistency')}
- 判据：{' '.join('%s=%s' % (k, '✓' if v else '✗') for k, v in (cand.get('criteria') or {}).items())}
{cap_note}
{ev}

## 默认参数（来自长期记忆，需人工确认）

{dft}

> 这些是从你过往对话里归纳的稳定偏好，写进技能后就不用每次重复交代；
> 若不对，直接改这一节与 front-matter 的 `defaults`。
"""
    return base.rstrip() + "\n" + extra
