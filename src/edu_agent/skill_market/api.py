# -*- coding: utf-8 -*-
"""模块 3 · 对外接口契约（**纯数据面，不含任何前端页面**）。

市场页面由 F 实现，本文件只提供它需要的数据与动作端点。所有响应都是 JSON，
所有端点都吞异常返回 `{ok: false, error: ...}`（与平台既有风格一致）。

挂载方式（web_server.py 里已接好）：

    from edu_agent.skill_market.api import router as skill_market_router
    app.include_router(skill_market_router)

端点分组：

    GET  /api/skill-market/registry               能力/数据范围词表（权限说明用）
    GET  /api/skill-market/index                  市场索引（搜索/列表）
    GET  /api/skill-market/detail?id=             详情（权限、安装状态、版本、审计尾）
    GET  /api/skill-market/success-sessions       可作为草稿来源的成功会话
    POST /api/skill-market/draft/from-session     SKILL-02 从会话生成草稿
    GET  /api/skill-market/draft?id=              读草稿原文（人工编辑用）
    POST /api/skill-market/draft/save             保存人工编辑后的草稿
    POST /api/skill-market/scan                   SKILL-03 静态权限扫描
    POST /api/skill-market/fixture/save           写匿名 fixture
    POST /api/skill-market/fixture/run            跑 fixture
    POST /api/skill-market/submit                 SKILL-05 提交审核
    POST /api/skill-market/approve                审核通过
    POST /api/skill-market/reject                 审核驳回
    POST /api/skill-market/publish                发布（过 permissions 闸门）
    POST /api/skill-market/disable                停用（过闸门）
    POST /api/skill-market/rollback               回滚（过闸门）
    GET  /api/skill-market/status?id=             状态机状态
    GET  /api/skill-market/history?id=            审计轨迹
    GET  /api/skill-market/audit                  审计查询
    GET  /api/skill-market/audit/verify           审计哈希链校验
    GET  /api/skill-market/session/{sid}/plugins  会话的 plugin 集合（隔离视图）
    POST /api/skill-market/session/bind           绑定技能到会话（显式给 id）
    POST /api/skill-market/session/unbind         解绑
    GET  /api/skill-market/session/{sid}/kernel-catalog  该会话 Kernel 会装哪些函数
    POST /api/skill-market/kernel/load            建会话 Kernel 并加载（草稿会被拒）
    POST /api/skill-market/kernel/invoke          调用已加载的 kernel function
    POST /api/skill-market/session/gc             回收闲置会话集合
"""
from __future__ import annotations

from fastapi import APIRouter

from edu_agent.skill_market import (audit, capability_registry, draft as draft_mod,
                                   fixture as fixture_mod, isolation, kernel_loader,
                                   lifecycle, manifest as mf, perm_scan, store)

router = APIRouter(prefix="/api/skill-market", tags=["skill-market"])


def _err(e: Exception) -> dict:
    return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


# ---------------------------------------------------------------- 词表 / 索引
@router.get("/registry")
def api_registry():
    """能力令牌 + 数据范围 + 禁止项（市场详情页的"权限说明"直接渲染它）。"""
    return {"ok": True, **capability_registry.describe(),
            "statuses": list(mf.STATUSES),
            "transitions": {"%s->%s" % k: v for k, v in lifecycle.TRANSITIONS.items()}}


@router.get("/index")
def api_index(q: str = "", teacher_id: str = ""):
    """市场索引：四区合并 + 安装状态 + 版本数 + fixture 有无。

    **可见性分支**（业务分支只影响"市场查询"和"会话加载"，不影响任何流水线环节）：

    * `SINGLE_USER_MODE=True`：展示**全部草稿 + 已发布**（只有你一个人，不需要隔离）；
    * `SINGLE_USER_MODE=False`：展示已发布 + **本人（create_teacher_id）创建的**草稿；
      注册表里查不到来源的技能按"只暴露已发布"处理（安全默认，不泄露来源不明的草稿）。
    """
    try:
        rows = store.market_index(keyword=q)
        from edu_agent.skill_market import mode, registry_db

        single = mode.single_user_mode()
        tid = mode.teacher_id(teacher_id)
        scope = "全部草稿 + 已发布" if single else "已发布 + 本人创建的草稿"
        if not single:
            visible = {r["skill_id"] for r in registry_db.visible_for(tid, single_user=False)}
            rows = [r for r in rows
                    if r["name"] in visible or r.get("area") == "published"]
        for r in rows:
            ok, why = store.is_loadable(r["name"])
            r["loadable"] = ok
            r["loadable_reason"] = why
            row = registry_db.get(r["name"]) or {}
            r["create_teacher_id"] = row.get("create_teacher_id", "")
        return {"ok": True, "skills": rows, "count": len(rows),
                "single_user_mode": single, "teacher_id": tid, "visibility": scope,
                "sessions": isolation.stats()}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/detail")
def api_detail(id: str = ""):
    """市场详情（数据面）：权限说明、安装状态、版本列表、审计尾、静态 plugin 函数。"""
    try:
        sid = str(id or "").strip()
        found = store.find_skill(sid)
        if found is None:
            return {"ok": False, "error": "技能不存在：%s" % sid}
        man = found["manifest"]
        rep = perm_scan.scan(man, found["body"],
                             fixture_text=store.fixture_text(sid),
                             fixture_path=None, skill_dir=None)
        return {"ok": True, "skill": sid, "area": found["area"], "dir": found["dir"],
                "manifest": man.to_dict(),
                "permissions": {"allowed_capabilities": {
                    t: capability_registry.resolve_capability(t)[1]
                    for t in man.allowed_capabilities},
                    "data_scope": {t: capability_registry.resolve_data_scope(t)[1]
                                   for t in man.data_scope},
                    "gated_actions": rep.declared.get("gated_actions", [])},
                "status": lifecycle.status(sid),
                "fixture": store.read_fixture(sid),
                "plugin_functions": kernel_loader.describe_skill(sid).get("functions", []),
                "audit_tail": audit.query(skill=sid, limit=10),
                "body": found["body"]}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/success-sessions")
def api_success_sessions(limit: int = 10):
    """可作为 SKILL-02 来源的成功会话候选。"""
    try:
        return {"ok": True, "sessions": draft_mod.list_success_sessions(limit=limit)}
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ---------------------------------------------------------------- SKILL-02
@router.post("/draft/from-session")
def api_draft_from_session(body: dict):
    """从成功会话生成 SKILL.md 草稿（**只产草稿，不产可运行 py**）。"""
    try:
        caps = body.get("allowed_capabilities") or ("kg.read", "question.draft")
        scope = body.get("data_scope") or ("knowledge_id", "teaching_context")
        r = draft_mod.create_draft_from_session(
            str(body.get("session_id") or ""),
            name_hint=str(body.get("name_hint") or ""),
            owner=str(body.get("owner") or "E"),
            description=str(body.get("description") or ""),
            allowed_capabilities=tuple(str(c) for c in caps),
            data_scope=tuple(str(c) for c in scope),
        )
        if r.get("ok"):
            r.pop("manifest", None)          # manifest 是对象，序列化前去掉（to_dict 已有）
        return r
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/draft")
def api_get_draft(id: str = "", area: str = "draft"):
    """读草稿原文（SKILL.md 文本，供人工编辑）。"""
    try:
        got = store.read_area(area, str(id or ""))
        if got is None:
            return {"ok": False, "error": "在 %s 区找不到技能 %s" % (area, id)}
        man, body, d = got
        return {"ok": True, "id": id, "area": area, "dir": str(d),
                "manifest": man.to_dict(), "text": man.to_document(body)}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/draft/save")
def api_save_draft(body: dict):
    """保存人工编辑后的草稿。`text` 给全文；或给 manifest 字段 + body 分片。"""
    try:
        sid = str(body.get("id") or "").strip()
        text = str(body.get("text") or "")
        if text:
            man, bd = mf.from_document(text)
            if not sid:
                sid = man.name
            if man.name != sid:
                man = __import__("dataclasses").replace(man, name=sid)
        else:
            cur = store.read_area("draft", sid)
            if cur is None:
                return {"ok": False, "error": "草稿不存在：%s" % sid}
            man, bd, _ = cur
            changes = {k: v for k, v in (body.get("manifest") or {}).items()
                       if k in man.to_dict()}
            if changes:
                import dataclasses

                for k in ("inputs", "outputs", "allowed_capabilities", "data_scope",
                          "tests", "tags"):
                    if k in changes and isinstance(changes[k], list):
                        changes[k] = tuple(changes[k])
                man = dataclasses.replace(man, **changes)
            if body.get("body") is not None:
                bd = str(body["body"])
        files = {k: str(v) for k, v in (body.get("files") or {}).items()}
        # 人工编辑接口允许放 plugin.py 等代码（自动路径 create_draft_from_session 不允许）
        d = store.write_draft(man, bd, files=files,
                              allow_code=bool(body.get("allow_code", True)))
        rep = perm_scan.scan_draft_dir(d, for_publish=False)
        return {"ok": True, "id": sid, "dir": str(d), "scan": rep[2].to_dict(),
                "errors": man.errors()}
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ---------------------------------------------------------------- SKILL-03
@router.post("/scan")
def api_scan(body: dict):
    """静态权限扫描（草稿/待审/已发布都能扫）。"""
    try:
        sid = str(body.get("id") or "").strip()
        area = str(body.get("area") or "draft")
        d = store.area_dir(area, sid)
        if not (d / "SKILL.md").exists():
            found = store.find_skill(sid)
            if found is None:
                return {"ok": False, "error": "技能不存在：%s" % sid}
            d = __import__("pathlib").Path(found["dir"])
        man, body_text, rep = perm_scan.scan_draft_dir(
            d, for_publish=bool(body.get("for_publish")), fixture_text=store.fixture_text(sid))
        return {"ok": True, "id": sid, "manifest": man.to_dict(), **rep.to_dict()}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/fixture/save")
def api_fixture_save(body: dict):
    """写匿名 fixture（会先做匿名性与结构校验）。"""
    try:
        sid = str(body.get("id") or "").strip()
        payload = body.get("fixture")
        if not isinstance(payload, dict):
            return {"ok": False, "error": "fixture 必须是 JSON 对象"}
        val = fixture_mod.validate_fixture(payload)
        if not val["ok"]:
            return {"ok": False, "gate": "fixture", **val}
        p = store.write_fixture(sid, payload)
        return {"ok": True, "id": sid, "path": str(p), "validation": val}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/fixture/run")
def api_fixture_run(body: dict):
    """跑 fixture（草稿阶段为静态校验；发布后可传真执行器）。"""
    try:
        return fixture_mod.run_fixture(str(body.get("id") or ""))
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ---------------------------------------------------------------- SKILL-05
@router.post("/new-draft")
def api_new_draft(body: dict):
    """从已发布版本派生新草稿（改已上线技能的唯一入口）。"""
    try:
        return lifecycle.new_draft(str(body.get("id") or ""),
                                   str(body.get("actor") or "E"),
                                   from_version=str(body.get("from_version") or ""),
                                   version=str(body.get("version") or ""),
                                   note=str(body.get("note") or ""))
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/submit")
def api_submit(body: dict):
    try:
        return lifecycle.submit(str(body.get("id") or ""), str(body.get("actor") or "E"),
                                session_id=str(body.get("session_id") or ""))
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/approve")
def api_approve(body: dict):
    try:
        return lifecycle.approve(str(body.get("id") or ""),
                                 str(body.get("reviewer") or "G"),
                                 note=str(body.get("note") or ""))
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/reject")
def api_reject(body: dict):
    try:
        return lifecycle.reject(str(body.get("id") or ""),
                                str(body.get("reviewer") or "G"),
                                reason=str(body.get("reason") or ""))
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/publish")
def api_publish(body: dict):
    """发布：会先过 `permissions.guard("skill_publish")`（默认 ask → 需 confirm）。"""
    try:
        return lifecycle.publish(str(body.get("id") or ""), str(body.get("actor") or "E"),
                                 confirm=bool(body.get("confirm")),
                                 level=str(body.get("level") or "patch"),
                                 session_id=str(body.get("session_id") or ""))
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/disable")
def api_disable(body: dict):
    try:
        return lifecycle.disable(str(body.get("id") or ""), str(body.get("actor") or "E"),
                                 reason=str(body.get("reason") or ""),
                                 confirm=bool(body.get("confirm")))
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/rollback")
def api_rollback(body: dict):
    try:
        return lifecycle.rollback(str(body.get("id") or ""), str(body.get("actor") or "E"),
                                  to_version=str(body.get("to_version") or ""),
                                  confirm=bool(body.get("confirm")),
                                  reason=str(body.get("reason") or ""))
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/status")
def api_status(id: str = ""):
    try:
        return lifecycle.status(str(id or ""))
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/history")
def api_history(id: str = "", limit: int = 50):
    try:
        return {"ok": True, "skill": id, "history": lifecycle.history(str(id or ""), limit=limit)}
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ---------------------------------------------------------------- SKILL-04
@router.get("/audit")
def api_audit(skill: str = "", event: str = "", limit: int = 50):
    """审计查询（只读；日志本身只追加、不可改删）。"""
    try:
        return {"ok": True, "records": audit.query(skill=skill, event=event,
                                                   limit=max(1, min(limit, 500)))}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/audit/verify")
def api_audit_verify():
    """校验审计哈希链（发现被改动/断链会列出来）。"""
    try:
        return {"ok": True, **audit.verify_chain()}
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ---------------------------------------------------------------- 会话隔离 / Kernel
@router.get("/session/{sid}/plugins")
def api_session_plugins(sid: str):
    """该会话绑定的 plugin 集合（隔离视图）。"""
    try:
        return {"ok": True, **isolation.loadable_for(sid), "bound": isolation.list_for(sid)}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/session/bind")
def api_session_bind(body: dict):
    """把技能绑到会话（**必须显式给 id**，没有"装载整个市场"的开关）。"""
    try:
        return isolation.bind(str(body.get("session_id") or ""),
                              [str(s) for s in (body.get("skills") or [])],
                              replace_all=bool(body.get("replace_all")))
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/session/unbind")
def api_session_unbind(body: dict):
    try:
        return isolation.unbind(str(body.get("session_id") or ""),
                                str(body.get("skill") or ""))
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/session/{sid}/kernel-catalog")
def api_kernel_catalog(sid: str):
    """该会话的 Kernel 里会有哪些 plugin/function（静态推导，不执行代码）。"""
    try:
        return kernel_loader.kernel_catalog(sid)
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/kernel/load")
def api_kernel_load(body: dict):
    """为该会话建 Kernel 并加载技能（草稿/未绑定会被明确拒绝）。"""
    try:
        r = kernel_loader.build_kernel(str(body.get("session_id") or ""),
                                       skills=[str(s) for s in (body.get("skills") or [])] or None)
        r.pop("kernel", None)
        return r
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/kernel/invoke")
def api_kernel_invoke(body: dict):
    """调用某个已加载技能的 kernel function（同步 def → FastAPI 丢线程池）。"""
    try:
        sid = str(body.get("session_id") or "")
        skill = str(body.get("skill") or "")
        ok, why = isolation.assert_loadable(sid, skill)
        if not ok:
            return {"ok": False, "gate": "isolation", "skill": skill, "error": why}
        built = kernel_loader.build_kernel(sid, skills=[skill])
        if not built.get("ok"):
            return built
        return kernel_loader.invoke(built["kernel"], skill,
                                    str(body.get("function") or ""),
                                    **(body.get("args") or {}))
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/session/gc")
def api_session_gc(body: dict | None = None):
    try:
        ttl = int((body or {}).get("ttl") or 0) or None
        return isolation.gc(ttl) if ttl else isolation.gc()
    except Exception as e:  # noqa: BLE001
        return _err(e)
