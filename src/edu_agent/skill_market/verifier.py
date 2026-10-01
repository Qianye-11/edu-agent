# -*- coding: utf-8 -*-
"""skill-create · M4 检验器：L1 静态权限扫描 + L2 回放。

**"必须检验"就落在这个文件上。** 两级都过才把候选标成 `verified`，
而 `verified` 是 `create_api.submit()` 放行的唯一凭据。

* **L1**：直接复用模块 3 的 `perm_scan`（令牌白名单 / 网络 / 文件 / 执行 / 越界库表 / 语义）。
* **L2 回放**：**不执行草稿里的代码**（草稿还没被审过，执行它就等于绕过审核）。做的是
  1. **声明式回放**：把运行痕迹里记录的步骤当作"该流程做过什么"，核对
     `实际调过的能力 ⊆ manifest 声明的能力` —— 声明得比实际用的少，就是漏审；
  2. **形状核对**：fixture 的 `expected_output_shape.fields` 与痕迹里记录的产出形状必须一致；
  3. **静态契约核对**：如果草稿里已经有人工写的 `plugin.py`，用 AST 读它返回的字典键
     （不 import、不执行），核对与期望形状是否对得上。

另外给"检验之后又被改过"留了把手：检验时记下 `SKILL.md` 的内容摘要，
`submit()` 会核对摘要 —— 改过就必须重新检验。
"""
from __future__ import annotations

import ast
import hashlib
import json
import time
from pathlib import Path

from edu_agent.skill_market import audit, kernel_loader, miner, perm_scan, runs, store


def _digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:16]


def _plugin_return_keys(path: Path) -> dict:
    """静态读 `plugin.py` 里 @kernel_function 函数返回的字典键（不执行）。"""
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, OSError) as e:
        return {"ok": False, "error": str(e), "functions": {}}
    out: dict[str, list[str]] = {}
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        decorated = any(
            (d.func if isinstance(d, ast.Call) else d).attr == "kernel_function"
            if isinstance(d.func if isinstance(d, ast.Call) else d, ast.Attribute)
            else getattr(d.func if isinstance(d, ast.Call) else d, "id", "") == "kernel_function"
            for d in (fn.decorator_list or []))
        if not decorated:
            continue
        keys: list[str] = []
        for node in ast.walk(fn):
            if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
                for k in node.value.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        if k.value not in keys:
                            keys.append(k.value)
        out[fn.name] = sorted(keys)
    return {"ok": True, "functions": out}


def verify(candidate_id: str, *, actor: str = "E") -> dict:
    """对某个已出草稿的候选跑 L1+L2。全过 → 候选 state=verified。"""
    cand = miner.get_candidate(candidate_id)
    if cand is None:
        return {"ok": False, "error": "候选不存在：%s" % candidate_id}
    cid = cand["candidate_id"]
    skill_id = str(cand.get("skill_id") or "")
    d = Path(str(cand.get("draft_dir") or "")) if cand.get("draft_dir") else None
    if not skill_id or d is None or not (d / "SKILL.md").exists():
        return {"ok": False, "gate": "draft", "candidate_id": cid,
                "error": "还没有草稿：先调 /api/skill-create/draft"}

    text = (d / "SKILL.md").read_text(encoding="utf-8", errors="replace")
    man, body, l1rep = perm_scan.scan_draft_dir(d, for_publish=False)

    # ---- L1 ----
    l1 = {"verdict": l1rep.verdict, "ok": l1rep.ok,
          "blocking": [f.to_dict() for f in l1rep.blocking],
          "warnings": [f.to_dict() for f in l1rep.warnings],
          "gated_actions": l1rep.declared.get("gated_actions", [])}

    # ---- L2 ----
    declared = set(man.allowed_capabilities)
    actual: list[str] = []
    for r in runs.runs():
        if r.get("intent_signature") != cand.get("signature") or r.get("outcome") != "success":
            continue
        for st in (r.get("steps") or []):
            if isinstance(st, dict) and st.get("id"):
                token = miner.capability_of_step(st)
                if token and token not in actual:
                    actual.append(token)

    def _declared_has(token: str) -> bool:
        for t in (token, "skill:" + token, "mcp:" + token):
            if t in declared:
                return True
        return False

    undeclared = [t for t in actual if not _declared_has(t)]

    fixture = store.read_fixture(skill_id) or {}
    expected = list(((fixture.get("expected_output_shape") or {}).get("fields")) or [])
    recorded = list(cand.get("shape") or [])
    matched = sorted(set(expected) & set(recorded))
    shape_match = (len(matched) / len(expected)) if expected else 0.0
    missing = sorted(set(expected) - set(recorded))

    plugin_path = (d / (man.plugin or "plugin.py"))
    plugin = {"declared": bool(man.plugin), "exists": plugin_path.exists(), "functions": {}}
    if plugin["exists"]:
        info = _plugin_return_keys(plugin_path)
        plugin["functions"] = info.get("functions", {})
        plugin["static_ok"] = bool(info.get("ok"))
        if info.get("functions"):
            keys = set()
            for ks in info["functions"].values():
                keys |= set(ks)
            plugin["return_keys"] = sorted(keys)
            plugin["shape_match"] = round(
                (len(keys & set(expected)) / len(expected)) if expected else 0.0, 4)
        else:
            plugin["note"] = "plugin.py 里没有找到 @kernel_function 函数"

    shape_min = float(runs.config().get("consistency_min") or 1.0)
    l2_ok = (not undeclared) and (shape_match >= shape_min) and (not plugin.get("declared")
                                                                 or plugin.get("exists"))
    l2 = {"ok": l2_ok, "executed": False,
          "method": "声明式回放（不执行草稿代码）",
          "declared_calls": sorted(declared),
          "actual_calls": actual,
          "undeclared_calls": undeclared,
          "expected_shape": expected,
          "recorded_shape": recorded,
          "matched": matched, "missing": missing,
          "shape_match": round(shape_match, 4),
          "plugin": plugin,
          "side_effects": "none"}

    ok = bool(l1["ok"] and l2["ok"])
    state = "verified" if ok else "drafted"
    miner.set_state(cid, state, verified_at=time.time() if ok else None,
                    verification={"l1": l1["verdict"], "l2": l2_ok,
                                  "draft_digest": _digest(text), "at": time.time()},
                    criteria={**{k: v for k, v in (cand.get("criteria") or {}).items()},
                              "R3": bool(shape_match >= shape_min),
                              "R5": bool(l1["ok"])})
    audit.append("skill-create.verify" if ok else "skill-create.verify.failed",
                 skill=skill_id, actor=actor, input_text=cid,
                 result={"ok": ok, "L1": l1["verdict"], "L2": l2_ok,
                         "undeclared": undeclared, "missing": missing},
                 gates={"L1": l1["verdict"], "L2": "pass" if l2_ok else "fail"})
    return {"ok": ok, "candidate_id": cid, "skill": skill_id, "state": state,
            "L1": l1, "L2": l2, "draft_digest": _digest(text),
            "message": ("检验通过：可以提交审核（submit 仍会过 permissions 闸门）" if ok else
                        "检验未通过：修完草稿后重新调 /api/skill-create/verify")}


def verification_state(candidate_id: str) -> dict:
    """`submit()` 用：候选是否已检验、以及草稿在检验后有没有被改过。"""
    cand = miner.get_candidate(candidate_id)
    if cand is None:
        return {"ok": False, "error": "候选不存在：%s" % candidate_id}
    if cand.get("state") != "verified":
        return {"ok": False, "gate": "verify", "candidate_id": cand.get("candidate_id"),
                "error": "候选未通过检验（state=%s）：先调 /api/skill-create/verify"
                         % cand.get("state")}
    d = Path(str(cand.get("draft_dir") or ""))
    p = d / "SKILL.md"
    if not p.exists():
        return {"ok": False, "gate": "draft", "candidate_id": cand.get("candidate_id"),
                "error": "草稿不见了：%s" % p}
    cur = _digest(p.read_text(encoding="utf-8", errors="replace"))
    want = str(((cand.get("verification") or {}).get("draft_digest")) or "")
    if want and cur != want:
        return {"ok": False, "gate": "stale", "candidate_id": cand.get("candidate_id"),
                "error": "草稿在检验之后被改过：请重新调 /api/skill-create/verify",
                "digest_now": cur, "digest_verified": want}
    return {"ok": True, "candidate_id": cand.get("candidate_id"),
            "skill_id": cand.get("skill_id"), "draft_digest": cur}
