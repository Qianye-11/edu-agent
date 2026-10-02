# -*- coding: utf-8 -*-
"""SKILL-05 · 审核 / 发布 / 回滚 状态机。

    draft ──submit──▶ submitted ──approve──▶ approved ──publish──▶ published
      ▲                   │                                          │  ▲
      └──────reject───────┘                          disable ◀───────┘  │
                                                       │               │
                                                       ▼          publish(重新启用)
                                                   disabled ───────────┘
                                                       │
                                          published ──rollback──▶ rolled_back ──publish──▶ published

三条不能绕过的闸门：

1. **草稿永远不可加载**：`draft/submitted/approved` 三个状态的技能都躺在市场区，
   而 `paths.runtime_skills_dir()` 里没有它们的文件 —— 加载器（isolation.py）还会
   二次核对状态，双保险。
2. **发布/停用/回滚走 permissions.py**：`skill_publish` / `skill_disable` /
   `skill_rollback` 三个动作登记在 permissions.ACTIONS 里，默认 `ask`，可被用户改成
   `allow` 或 `deny`。闸门不放行就返回 `{ok:False, gate:'permission'}`，前端弹窗后带
   `confirm=true` 重试。
3. **每一步都写审计**（成功与失败都写）：SKILL-04 要求"记录版本、操作者、输入摘要、
   执行结果"，所以连"被闸门拦下"这件事本身也进日志。
"""
from __future__ import annotations

import time
from dataclasses import replace as _replace
from pathlib import Path

from edu_agent.skill_market import audit, manifest as mf, perm_scan, shadow, store
from edu_agent.skill_market import paths, verifier

# 状态机： (当前状态, 动作) -> 目标状态
TRANSITIONS: dict[tuple[str, str], str] = {
    ("draft", "submit"): "submitted",
    ("submitted", "approve"): "approved",
    ("submitted", "reject"): "draft",
    # 审核通过后教师又发现要改（还没安装）：撤回草稿重改。
    # 少了这条，approved 就成了"死状态"——只能硬改文件，绕过状态机（实测踩过）。
    ("approved", "revise"): "draft",
    ("approved", "publish"): "published",
    ("published", "disable"): "disabled",
    ("disabled", "publish"): "published",          # 重新启用
    ("published", "rollback"): "rolled_back",
    ("rolled_back", "publish"): "published",
}

# 动作 → 需要走 permissions 闸门的动作 id（None = 不需要）
ACTION_GATE: dict[str, str | None] = {
    "submit": None,                 # 提交只是进队列，无副作用
    "approve": None,                # 审核是判断，不改运行时
    "reject": None,
    "revise": None,                 # 撤回重改：只动市场区，不碰运行时
    "publish": "skill_publish",     # 会让 Agent 能加载它 → 高风险
    "disable": "skill_disable",     # 会撤下正在被用的技能 → 高风险
    "rollback": "skill_rollback",   # 会换掉正在被用的版本 → 高风险
}

ACTION_LABEL = {"submit": "提交审核", "approve": "审核通过", "reject": "审核驳回",
                "revise": "撤回重改", "publish": "发布安装", "disable": "停用",
                "rollback": "回滚上一版"}


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M")


def _write_status(d: Path, man: mf.SkillManifest, body: str, status: str,
                  review: str | None = None) -> mf.SkillManifest:
    """把状态镜像写回 SKILL.md 的 front-matter（权威状态在 state.json）。"""
    new = _replace(man, status=status,
                   review=man.review if review is None else review,
                   updated=_now())
    (Path(d) / "SKILL.md").write_text(new.to_document(body), encoding="utf-8")
    return new


def _log(event: str, *, skill: str, version: str = "", actor: str = "",
         session_id: str = "", frm: str = "", to: str = "",
         input_text: str = "", result: dict | None = None,
         gates: dict | None = None, extra: dict | None = None) -> dict:
    return audit.append(event, skill=skill, version=version, actor=actor,
                        session_id=session_id, status_from=frm, status_to=to,
                        input_text=input_text, result=result, gates=gates, extra=extra)


def _fail(action: str, skill_id: str, actor: str, reason: str, *,
          frm: str = "", session_id: str = "", gates: dict | None = None,
          extra: dict | None = None) -> dict:
    _log("skill." + action + ".denied", skill=skill_id, actor=actor, frm=frm,
         input_text=reason, result={"ok": False, "code": "denied", "reason": reason},
         gates=gates, session_id=session_id, extra=extra)
    return {"ok": False, "action": action, "skill": skill_id, "error": reason,
            "status": frm or store.effective_status(skill_id)}


def _check_transition(skill_id: str, action: str) -> tuple[bool, str, str]:
    """状态机校验：返回 (是否允许, 当前状态, 目标状态)。"""
    cur = store.effective_status(skill_id) or "draft"
    nxt = TRANSITIONS.get((cur, action))
    if nxt is None:
        return False, cur, ""
    return True, cur, nxt


def _gate(action: str, skill_id: str, actor: str, confirm: bool,
          cur: str) -> dict | None:
    """高风险动作过 permissions.py 闸门（复用既有三态策略，不另写一套）。"""
    aid = ACTION_GATE.get(action)
    if not aid:
        return None
    try:
        from edu_agent import permissions

        blocked = permissions.guard(aid, confirm=bool(confirm))
    except Exception as e:  # noqa: BLE001  权限层异常按"拒绝"处理（安全优先）
        blocked = {"ok": False, "gate": "permission", "action": aid,
                   "error": "权限层不可用，已按拒绝处理：%s" % e}
    if blocked:
        _log("skill." + action + ".blocked", skill=skill_id, actor=actor, frm=cur,
             input_text=str(blocked.get("label") or aid),
             result={"ok": False, "code": "permission", "policy": blocked.get("policy")},
             gates={"permission": blocked.get("policy")})
        blocked.update({"skill": skill_id, "action": action, "status": cur})
        return blocked
    return None


# ---------------------------------------------------------------- 动作
def submit(skill_id: str, actor: str = "E", *, session_id: str = "") -> dict:
    """草稿 → 待审。**三重校验在这里强制执行，任何调用方、任何模式都绕不过**（铁律 2）。

        L1 静态权限扫描   perm_scan：能力白名单 / 网络 / 文件 / 执行 / 越界 / 语义
        L2 manifest 校验  verifier.verify：allowed_capabilities 必须映射到真实能力 +
                          声明式回放（实际调用 ⊆ 声明、产出形状一致）
        L3 影子运行       shadow.shadow_run：草稿的 plugin.py 在子进程真跑一次，
                          出站调用被记录桩拦下、fixture 断言必须过

    三项任一不过 → **拒绝提交**，并把逐项结论写进返回值与审计。
    注意：这里**不看 `SINGLE_USER_MODE`** —— 单用户调试只是"少一层教师隔离"，
    不是"少一层质量闸门"（铁律 2 明确禁止因为单用户就绕过）。
    纯提示词技能（没有 plugin.py）的 L3 会 skip，这是"没有代码可跑"而不是"跳过校验"。
    """
    ok, cur, nxt = _check_transition(skill_id, "submit")
    if not ok:
        return _fail("submit", skill_id, actor, "状态 %s 不能提交审核" % cur, frm=cur)
    got = store.read_area("draft", skill_id)
    if got is None:
        return _fail("submit", skill_id, actor, "草稿不存在", frm=cur)
    man, body, d = got
    fixture_path = Path(d) / (man.fixture or "fixture.json")
    if not fixture_path.exists():
        return _fail("submit", skill_id, actor,
                     "缺少匿名 fixture（%s）：验收要求先过 fixture 才能提交审核" % fixture_path.name,
                     frm=cur)

    # ---- L1 静态权限扫描 ----
    rep = perm_scan.scan(man, body, fixture_text=fixture_path.read_text(encoding="utf-8"),
                         fixture_path=fixture_path, skill_dir=d, for_publish=False)
    if not rep.ok:
        return _fail("submit", skill_id, actor, "L1 未过：" + rep.review_note(), frm=cur,
                     gates={"L1": rep.verdict, "L2": "skipped", "L3": "skipped"},
                     extra={"blocking": [f.to_dict() for f in rep.blocking]})

    # ---- L2 manifest 权限校验（含声明式回放）----
    v2 = verifier.verify(skill_id, actor=actor)
    if not v2.get("ok"):
        return _fail("submit", skill_id, actor,
                     "L2 未过：" + str(v2.get("L2", {}).get("undeclared_calls")
                                      or v2.get("L1", {}).get("blocking") or v2)[:200],
                     frm=cur, gates={"L1": rep.verdict, "L2": "fail", "L3": "skipped"},
                     extra={"L2": v2.get("L2")})

    # ---- L3 影子运行（没有 plugin.py 则 skip，不是跳过校验）----
    v3 = shadow.shadow_run(skill_id, actor=actor)
    l3_ok = bool(v3.get("ok")) or bool(v3.get("skipped"))
    if not l3_ok:
        return _fail("submit", skill_id, actor, "L3 未过：" + str(v3.get("error") or "")[:200],
                     frm=cur, gates={"L1": rep.verdict, "L2": "pass", "L3": "fail"},
                     extra={"L3": v3.get("L3")})

    gates = {"L1": rep.verdict, "L2": "pass",
             "L3": ("skipped:no-plugin" if v3.get("skipped") else "pass")}
    verification = {"l1": rep.verdict, "l2": True, "l3": bool(v3.get("ok")),
                    "draft_digest": v2.get("draft_digest"), "at": time.time()}
    moved = store.move_area(skill_id, "draft", "submitted")
    man = _write_status(moved, man, body, "submitted",
                        review="已提交待审：" + rep.review_note())
    store.write_state(skill_id, status="submitted", version=man.version,
                      actor=actor, area="submitted", scan=rep.verdict,
                      gates=gates, verification=verification)
    _log("skill.submit", skill=skill_id, version=man.version, actor=actor, frm=cur,
         to="submitted", session_id=session_id or man.session_id,
         input_text=man.description, result={"ok": True, "code": "submitted"},
         gates={**gates, "fixture": "pass"})
    return {"ok": True, "action": "submit", "skill": skill_id, "status": "submitted",
            "version": man.version, "dir": str(moved),
            "scan": rep.to_dict(), "L2": v2.get("L2"), "L3": v3.get("L3"),
            "gates": gates,
            "message": "三重校验通过，已提交待审：默认仍不安装、不共享、不联网"}


def approve(skill_id: str, reviewer: str = "G", *, note: str = "") -> dict:
    """待审 → 审核通过（含**发布前**的完整静态扫描）。"""
    ok, cur, nxt = _check_transition(skill_id, "approve")
    if not ok:
        return _fail("approve", skill_id, reviewer, "状态 %s 不能审核通过" % cur, frm=cur)
    got = store.read_area("submitted", skill_id)
    if got is None:
        return _fail("approve", skill_id, reviewer, "待审目录不存在", frm=cur)
    man, body, d = got
    if man.owner and man.owner.strip().lower() == str(reviewer).strip().lower():
        return _fail("approve", skill_id, reviewer,
                     "审核人不能是作者本人（职责分离）", frm=cur)
    rep = perm_scan.scan_draft_dir(d, for_publish=True)[2]
    if not rep.ok:
        return _fail("approve", skill_id, reviewer, rep.review_note(), frm=cur,
                     gates={"perm_scan": rep.verdict},
                     extra={"blocking": [f.to_dict() for f in rep.blocking]})

    man = _write_status(d, man, body, "approved",
                        review=(note or "审核通过（%s）：%s" % (reviewer, rep.review_note())))
    store.write_state(skill_id, status="approved", version=man.version,
                      actor=reviewer, area="submitted", reviewer=reviewer)
    _log("skill.approve", skill=skill_id, version=man.version, actor=reviewer,
         frm=cur, to="approved", input_text=note or "审核通过",
         result={"ok": True, "code": "approved"},
         gates={"perm_scan": rep.verdict, "separation_of_duty": "pass"})
    return {"ok": True, "action": "approve", "skill": skill_id, "status": "approved",
            "version": man.version, "scan": rep.to_dict(),
            "message": "审核通过；发布需再走 skill_publish 权限闸门"}


def reject(skill_id: str, reviewer: str = "G", *, reason: str = "") -> dict:
    """待审 → 驳回退回草稿（必须写理由，理由进审计与 front-matter）。"""
    ok, cur, _ = _check_transition(skill_id, "reject")
    if not ok:
        return _fail("reject", skill_id, reviewer, "状态 %s 不能驳回" % cur, frm=cur)
    if not str(reason or "").strip():
        return _fail("reject", skill_id, reviewer, "驳回必须写明理由（会进审计）", frm=cur)
    got = store.read_area("submitted", skill_id)
    if got is None:
        return _fail("reject", skill_id, reviewer, "待审目录不存在", frm=cur)
    man, body, d = got
    moved = store.move_area(skill_id, "submitted", "draft")
    man = _write_status(moved, man, body, "draft", review="驳回：%s（%s）" % (reason, reviewer))
    store.write_state(skill_id, status="draft", version=man.version, actor=reviewer,
                      area="draft", review=reason)
    _log("skill.reject", skill=skill_id, version=man.version, actor=reviewer,
         frm=cur, to="draft", input_text=reason,
         result={"ok": True, "code": "rejected"})
    return {"ok": True, "action": "reject", "skill": skill_id, "status": "draft",
            "version": man.version, "dir": str(moved), "reason": reason}


def publish(skill_id: str, actor: str = "E", *, confirm: bool = False,
            level: str = "patch", session_id: str = "") -> dict:
    """审核通过 → 发布（**唯一**让 Agent 能加载它的动作）。

    ## 发布只做三件事（约束 2）

    1. 把技能**完整复制落盘**到全局 `data/skills/<id>/`（`store.install_to_runtime`）；
    2. 状态改成 `published`（`state.json` + 注册表 `skill_registry.db` 同步）；
    3. 写审计。

    ## 明确不做的事（红线）

    * **不调用 `isolation.bind()`** —— 会话加载哪些技能是"会话初始化"的事
      （见 `session_bootstrap.ensure_session`）。发布是全局动作，跟任何会话无关；
      把 bind 写进发布接口会造成"发布即悄悄改掉某些会话的加载面"这种难查的问题。
    * **不写 `skill_installs`** —— "教师把技能装到自己名下"是多用户模式的独立动作，
      与发布解耦（单用户模式完全不用它）。
    * **不碰任何草稿区** —— 草稿依旧住在市场区，Agent 看不到。
    """
    ok, cur, nxt = _check_transition(skill_id, "publish")
    if not ok:
        return _fail("publish", skill_id, actor,
                     "状态 %s 不能发布（必须先 approve）" % cur, frm=cur)
    gated = _gate("publish", skill_id, actor, confirm, cur)
    if gated:
        return gated

    # 源：approved 在 submitted 区；disabled/rolled_back 从最近版本重新安装
    if cur == "approved":
        got = store.read_area("submitted", skill_id)
        if got is None:
            return _fail("publish", skill_id, actor, "待审目录不存在", frm=cur)
        man, body, src = got
        rep = perm_scan.scan_draft_dir(src, for_publish=True)[2]
        if not rep.ok:
            return _fail("publish", skill_id, actor, rep.review_note(), frm=cur,
                         gates={"perm_scan": rep.verdict})
        man = man.bumped(level=level, status="published", review=man.review)
        vdir = store.write_version(man, body, src)
        installed = store.install_to_runtime(man, body, vdir)
        store.archive_area(skill_id, "submitted", reason="published")
        scan_verdict = rep.verdict
    else:
        # 重新启用：拿最近一个版本重装（版本号不变）
        latest = store.latest_version(skill_id)
        if latest is None:
            return _fail("publish", skill_id, actor, "没有可用的历史版本", frm=cur)
        vdir = Path(latest["dir"])
        man, body = mf.from_document((vdir / "SKILL.md").read_text(encoding="utf-8",
                                                                  errors="replace"))
        man = _replace(man, status="published", updated=_now())
        installed = store.install_to_runtime(man, body, vdir)
        scan_verdict = "reused"

    # 状态 → published（state.json 与注册表一起更新；file_path 指向全局落盘目录）
    store.write_state(skill_id, status="published", version=man.version, actor=actor,
                      area="published", installed=True, dir=str(installed),
                      published_at=time.time())
    _log("skill.publish", skill=skill_id, version=man.version, actor=actor,
         frm=cur, to="published", session_id=session_id or man.session_id,
         input_text=man.description,
         result={"ok": True, "code": "published", "installed_to": str(installed)},
         gates={"permission": "allow", "perm_scan": scan_verdict})
    return {"ok": True, "action": "publish", "skill": skill_id, "status": "published",
            "version": man.version, "installed_to": str(installed),
            "message": "已发布：Agent 现在可加载它（会话下次初始化时自动带上，见 session_bootstrap）"}


def disable(skill_id: str, actor: str = "E", *, reason: str = "",
            confirm: bool = False) -> dict:
    """已发布 → 停用（从运行时目录撤下，版本保留，可再发布回来）。"""
    ok, cur, _ = _check_transition(skill_id, "disable")
    if not ok:
        return _fail("disable", skill_id, actor, "状态 %s 不能停用" % cur, frm=cur)
    gated = _gate("disable", skill_id, actor, confirm, cur)
    if gated:
        return gated
    moved = store.uninstall_from_runtime(skill_id, reason="disabled")
    store.write_state(skill_id, status="disabled", actor=actor, area="published",
                      installed=False, reason=reason)
    _log("skill.disable", skill=skill_id, version=store.get_state(skill_id).get("version", ""),
         actor=actor, frm=cur, to="disabled", input_text=reason,
         result={"ok": True, "code": "disabled", "archived_to": str(moved or "")})
    return {"ok": True, "action": "disable", "skill": skill_id, "status": "disabled",
            "archived_to": str(moved or ""),
            "message": "已停用：Agent 不再能加载它（历史版本保留在 published/）"}


def rollback(skill_id: str, actor: str = "E", *, to_version: str = "",
             confirm: bool = False, reason: str = "") -> dict:
    """已发布 → 回滚到上一个版本（换掉运行时目录里的实现，可审计）。"""
    ok, cur, _ = _check_transition(skill_id, "rollback")
    if not ok:
        return _fail("rollback", skill_id, actor, "状态 %s 不能回滚" % cur, frm=cur)
    gated = _gate("rollback", skill_id, actor, confirm, cur)
    if gated:
        return gated
    cur_version = str(store.get_state(skill_id).get("version") or "")
    target = None
    if to_version:
        target = {"version": to_version, "dir": str(store.publish_area(skill_id, to_version))}
        if not Path(target["dir"]).exists():
            return _fail("rollback", skill_id, actor, "目标版本不存在：%s" % to_version, frm=cur)
    else:
        target = store.latest_version(skill_id, exclude=cur_version)
    if target is None:
        return _fail("rollback", skill_id, actor, "没有更早的版本可回滚", frm=cur)

    vdir = Path(target["dir"])
    man, body = mf.from_document((vdir / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
    store.uninstall_from_runtime(skill_id, reason="rollback")
    installed = store.install_to_runtime(man, body, vdir)
    store.write_state(skill_id, status="published", version=man.version, actor=actor,
                      area="published", installed=True, rolled_back_from=cur_version,
                      dir=str(installed))
    _log("skill.rollback", skill=skill_id, version=man.version, actor=actor,
         frm=cur, to="published", input_text=reason or ("回滚 %s → %s" % (cur_version, man.version)),
         result={"ok": True, "code": "rolled_back", "from_version": cur_version,
                 "to_version": man.version, "installed_to": str(installed)})
    return {"ok": True, "action": "rollback", "skill": skill_id, "status": "published",
            "from_version": cur_version, "version": man.version,
            "installed_to": str(installed),
            "message": "已回滚到 %s（旧实现已归档，不是删除）" % man.version}


# ---------------------------------------------------------------- 查询
def revise(skill_id: str, actor: str = "E", *, reason: str = "") -> dict:
    """**审核通过后又发现要改** → 撤回草稿重改（approved → draft）。

    为什么需要它：少了这条转移，`approved` 就是个死状态 —— 教师改完内容只能
    "硬改文件"或"重新导入"，两种都会绕过状态机与审计（实测踩过）。
    已发布（published）的技能不走这里，走 `new_draft()`（线上版本继续服务）。
    """
    ok, cur, _ = _check_transition(skill_id, "revise")
    if not ok:
        return _fail("revise", skill_id, actor,
                     "状态 %s 不能撤回重改（已发布的请用 new_draft）" % cur, frm=cur)
    got = store.read_area("submitted", skill_id)
    if got is None:
        return _fail("revise", skill_id, actor, "待审/已批准目录不存在", frm=cur)
    man, body, d = got
    moved = store.move_area(skill_id, "submitted", "draft")
    man = _write_status(moved, man, body, "draft",
                        review="撤回重改：%s" % (reason or "内容需修改"))
    store.write_state(skill_id, status="draft", version=man.version, actor=actor,
                      area="draft", verify_state="", review=reason)
    _log("skill.revise", skill=skill_id, version=man.version, actor=actor,
         frm=cur, to="draft", input_text=reason,
         result={"ok": True, "code": "revised"})
    return {"ok": True, "action": "revise", "skill": skill_id, "status": "draft",
            "version": man.version, "dir": str(moved), "reason": reason}


def new_draft(skill_id: str, actor: str = "E", *, from_version: str = "",
              version: str = "", note: str = "") -> dict:
    """已发布/停用/回滚过的技能 → **派生一份新草稿**继续编辑。

    为什么需要它：`effective_status` 读的是权威 state.json，所以一个已发布的技能
    不能直接"改文件再提交"（状态仍是 published，状态机会拒绝）。想改就得先派生草稿 ——
    这一步不碰运行时目录，Agent 侧仍在跑老版本，改坏了也不影响线上。
    """
    cur = store.effective_status(skill_id)
    if cur in ("draft", "submitted", "approved"):
        return _fail("new_draft", skill_id, actor,
                     "当前状态 %s 已经是可编辑/在审状态，直接改草稿即可" % cur, frm=cur)
    target = None
    if from_version:
        d = store.publish_area(skill_id, from_version)
        target = {"version": from_version, "dir": str(d)} if d.exists() else None
    if target is None:
        target = store.latest_version(skill_id)
    if target is None:
        return _fail("new_draft", skill_id, actor, "没有可用的历史版本可派生", frm=cur)

    vdir = Path(target["dir"])
    man, body = mf.from_document((vdir / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
    man = _replace(man, status="draft", version=version or man.version,
                   review=note or ("派生自 %s" % target["version"]), updated=_now())
    files: dict[str, str] = {}
    for p in sorted(vdir.rglob("*")):
        if not p.is_file() or p.name == "SKILL.md":
            continue
        rel = str(p.relative_to(vdir)).replace("\\", "/")
        try:
            files[rel] = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue                       # 二进制文件不进草稿
    store.write_draft(man, body, files=files, allow_code=True)
    store.write_state(skill_id, status="draft", version=man.version, actor=actor,
                      area="draft", derived_from=target["version"])
    _log("skill.new_draft", skill=skill_id, version=man.version, actor=actor,
         frm=cur, to="draft",
         input_text="从 %s 派生新草稿" % target["version"],
         result={"ok": True, "code": "draft", "from_version": target["version"]})
    return {"ok": True, "action": "new_draft", "skill": skill_id, "status": "draft",
            "version": man.version, "from_version": target["version"],
            "dir": str(store.area_dir("draft", skill_id)),
            "message": "已派生新草稿：改完再走 submit → approve → publish；线上仍是老版本"}


def status(skill_id: str) -> dict:
    """给市场详情页/运维看的完整状态（数据面）。"""
    found = store.find_skill(skill_id)
    loadable, why = store.is_loadable(skill_id)
    st = store.get_state(skill_id)
    return {
        "ok": True, "skill": skill_id,
        "status": store.effective_status(skill_id),
        "version": st.get("version", ""),
        "area": found["area"] if found else "",
        "dir": found["dir"] if found else "",
        "installed": paths.is_inside_runtime(skill_id),
        "loadable": loadable, "loadable_reason": why,
        "versions": store.list_versions(skill_id),
        "allowed_transitions": sorted(
            a for (s, a) in TRANSITIONS if s == store.effective_status(skill_id)),
        "state": st,
    }


def history(skill_id: str, limit: int = 50) -> list[dict]:
    """该技能的全部审计记录（只读）。"""
    return audit.query(skill=skill_id, limit=limit)
