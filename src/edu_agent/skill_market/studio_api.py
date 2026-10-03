# -*- coding: utf-8 -*-
"""Skill Studio 接口（doc 34 §5.5 的 API 合同）—— **薄适配层，不重写业务**。

    GET    /api/skill-studio/candidates                     候选箱
    GET    /api/skill-studio/candidates/{id}                候选详情（脱敏）
    POST   /api/skill-studio/candidates/{id}/generate-draft 候选 → 草稿
    PATCH  /api/skill-studio/drafts/{id}                    编辑草稿
    POST   /api/skill-studio/drafts/{id}/validate           结构校验
    POST   /api/skill-studio/drafts/{id}/submit-review      draft → in_review（内含 L1+L2+L3）
    POST   /api/skill-studio/reviews/{id}/approve           in_review → approved（绑定内容哈希）
    POST   /api/skill-studio/reviews/{id}/reject            in_review → rejected
    POST   /api/skill-studio/drafts/{id}/install            approved → installed（五条闸门）
    GET    /api/skill-studio/audit                          审计查询

    另加（doc §6 的团队同步，属第二阶段但队列先可用）：
    GET    /api/skill-studio/overview                       总览（配置/目录/状态映射）
    POST   /api/skill-studio/sync/{id}/enqueue               已批准技能入同步队列
    GET    /api/skill-studio/sync/outbox                     队列内容

**没有前端页面**：本文件只返回 JSON（Skill Studio 的 UI 由他人实现，doc §5.6）。
所有变更类接口按 doc 要求返回 `{ok, candidate_id, state, content_hash, updated_at}`。
"""
from __future__ import annotations

import time

from fastapi import APIRouter

from edu_agent.skill_market import (audit, lifecycle, miner, packager, registry_db,
                                   safe_import, skill_privacy, skill_studio, store,
                                   tencent_memory_adapter)
from edu_agent.skill_market import skill_studio_models as models

router = APIRouter(prefix="/api/skill-studio", tags=["skill-studio"])


def _err(e: Exception) -> dict:
    return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


def _check(body, model) -> tuple[bool, dict, object]:
    """入参校验（doc §5.1 的 Pydantic 层）：形状不对就早退，别让它带着错字段往下走。"""
    return models.validate_or_error(model, body)


def _envelope(result: dict, *, skill_id: str = "", candidate_id: str = "") -> dict:
    """按 doc §5.5 补齐变更类接口的固定字段（并用响应模型收口一次）。"""
    out = dict(result or {})
    sid = skill_id or str(out.get("skill") or out.get("skill_id") or "")
    out.setdefault("candidate_id", candidate_id or out.get("candidate_id") or "")
    if sid:
        out.setdefault("skill", sid)
        try:
            out.setdefault("state", skill_studio.STATE_ALIAS.get(
                store.effective_status(sid), store.effective_status(sid)))
            out.setdefault("content_hash", skill_studio.content_hash(sid))
        except Exception:  # noqa: BLE001
            pass
    out.setdefault("updated_at", time.strftime("%Y-%m-%dT%H:%M:%S"))
    # 响应模型只做"形状校正"：多余字段剔除、缺失字段补默认，**不改变值**
    try:
        env = models.MutationEnvelope(**{k: v for k, v in out.items()
                                         if k in models.MutationEnvelope.model_fields})
        merged = env.model_dump()
        for k, v in out.items():                 # 业务特有字段（scan/L3/validation…）原样保留
            merged.setdefault(k, v)
        return merged
    except Exception:  # noqa: BLE001
        return out


@router.get("/schemas")
def api_schemas():
    """列出 Studio 的入参/出参模型（给前端与联调用的**契约快照**，无需读代码）。"""
    try:
        out = {}
        for name in dir(models):
            obj = getattr(models, name)
            fields = getattr(obj, "model_fields", None)
            if fields and getattr(obj, "__module__", "").startswith("edu_agent."):
                out[name] = {k: str(v.annotation) for k, v in fields.items()}
        return {"ok": True, "models": out,
                "envelope": list(models.MutationEnvelope.model_fields),
                "note": "doc §5.1 的 skill_studio_models.py；入参一律 extra=forbid（拼错字段会报错）"}
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ---------------------------------------------------------------- 候选箱
@router.get("/candidates")
def api_candidates(limit: int = 50):
    """候选箱：只给脱敏证据摘要（来源会话编号、次数、共同约束、评分、冷却）。"""
    try:
        return skill_studio.candidates(limit=limit)
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/candidates/{candidate_id}")
def api_candidate_detail(candidate_id: str):
    try:
        c = miner.get_candidate(candidate_id)
        if c is None:
            return {"ok": False, "error": "候选不存在：%s" % candidate_id}
        title = str(c.get("title") or "")
        sens = skill_privacy.scan_text(title)
        return {"ok": True, "candidate": {**c,
                "title": skill_privacy.scrub_markdown(title) if sens["sensitive"] else title,
                "evidence": [{"session_id": e.get("session_id"), "turn": e.get("turn"),
                              "outcome": e.get("outcome")}
                             for e in (c.get("evidence") or [])]},
                "privacy": sens,
                "studio_state": "detected" if c.get("state") == "candidate" else c.get("state")}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/candidates/{candidate_id}/generate-draft")
def api_generate_draft(candidate_id: str, body: dict | None = None):
    """候选 → 草稿（复用 skill-create 的封装器；R1~R6 必须全过）。"""
    try:
        ok_b, err_b, mb = _check(body, models.GenerateDraftBody)
        if not ok_b:
            return err_b
        r = packager.make_draft(candidate_id, actor=mb.actor or "E",
                                confirm=mb.confirm, force=mb.force)
        r.pop("manifest", None)
        return _envelope(r, skill_id=str(r.get("id") or ""), candidate_id=candidate_id)
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ---------------------------------------------------------------- 草稿
@router.patch("/drafts/{skill_id}")
def api_patch_draft(skill_id: str, body: dict | None = None):
    """编辑草稿：`text`（整篇 SKILL.md）或 `body`/`files` 分片。

    已批准/已发布的技能会自动先回到可编辑草稿态（approved → 撤回；published → 派生新草稿，
    **线上版本继续服务**），再写入编辑内容 —— 见 `skill_studio.ensure_editable`。

    doc 34 §5.5：skill id 与资源文件名都走服务端白名单（单层文件名 + 后缀白名单）。
    """
    try:
        ok_b, err_b, mb = _check(body, models.PatchDraftBody)
        if not ok_b:
            return err_b
        ok_id, why_id, _norm = safe_import.validate_skill_id(skill_id)
        if not ok_id:
            return {"ok": False, "gate": "skill_id", "error": why_id}
        files: dict[str, str] = {}
        for name, content in (mb.files or {}).items():
            ok_n, why_n, norm = safe_import.validate_resource_name(str(name))
            if not ok_n:
                return {"ok": False, "gate": "resource_name", "error": why_n, "name": str(name)}
            files[norm] = str(content)
        ed = skill_studio.ensure_editable(skill_id, mb.actor or "E")
        if not ed.get("ok"):
            return {"ok": False, "gate": "state", "error": ed.get("error"), "state": ed.get("state")}
        text = str(mb.text or "")
        if text:
            man, bd = _mf_from_text(text, skill_id)
        else:
            cur = store.read_area("draft", skill_id)
            if cur is None:
                return {"ok": False, "error": "找不到草稿：%s" % skill_id}
            man, bd, _d = cur
            if mb.body is not None:
                bd = str(mb.body)
        store.write_draft(man, bd, files=files, allow_code=bool(mb.allow_code))
        v = skill_studio.validate_draft(skill_id)
        return _envelope({"ok": True, "validation": v, "editable": ed}, skill_id=skill_id)
    except Exception as e:  # noqa: BLE001
        return _err(e)


def _mf_from_text(text: str, skill_id: str):
    import dataclasses

    from edu_agent.skill_market import manifest as mf

    man, bd = mf.from_document(text)
    if man.name != skill_id and skill_id:
        man = dataclasses.replace(man, name=skill_id)
    return man, bd


@router.post("/drafts/{skill_id}/validate")
def api_validate_draft(skill_id: str):
    """结构校验：前置元数据 / 路径 / 大小 / 资源清单 / 权限扫描。"""
    try:
        return _envelope(skill_studio.validate_draft(skill_id), skill_id=skill_id)
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/drafts/{skill_id}/submit-review")
def api_submit_review(skill_id: str, body: dict | None = None):
    """draft → in_review。内部**强制** L1 静态扫描 + L2 manifest 校验 + L3 影子运行。"""
    try:
        ok_b, err_b, mb = _check(body, models.ActorBody)
        if not ok_b:
            return err_b
        r = skill_studio.submit_review(skill_id, mb.actor or "E")
        return _envelope(r, skill_id=skill_id)
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ---------------------------------------------------------------- 审查
@router.post("/reviews/{skill_id}/approve")
def api_approve(skill_id: str, body: dict | None = None):
    """in_review → approved：写批准记录（**绑定当前内容哈希**）。改内容即失效。"""
    try:
        ok_b, err_b, mb = _check(body, models.ApproveBody)
        if not ok_b:
            return err_b
        r = skill_studio.approve(skill_id, mb.reviewer, note=mb.note)
        return _envelope(r, skill_id=skill_id)
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/reviews/{skill_id}/reject")
def api_reject(skill_id: str, body: dict | None = None):
    """in_review → rejected（回草稿 + 冷却期，理由必填）。"""
    try:
        ok_b, err_b, mb = _check(body, models.RejectBody)
        if not ok_b:
            return err_b
        r = skill_studio.reject(skill_id, mb.reviewer, reason=mb.reason)
        return _envelope(r, skill_id=skill_id)
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/reviews/{skill_id}")
def api_review_status(skill_id: str):
    """批准是否仍有效（哈希是否还一致）。"""
    try:
        return {"ok": True, "skill": skill_id,
                "approval": skill_studio.approval_status(skill_id),
                "state": store.effective_status(skill_id),
                "content_hash": skill_studio.content_hash(skill_id),
                "resources": skill_studio.resource_manifest(skill_id)}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/drafts/{skill_id}/install")
def api_install(skill_id: str, body: dict | None = None):
    """approved → installed：doc §5.4 五条闸门全过才安装（复制落盘 + status=published）。"""
    try:
        ok_b, err_b, mb = _check(body, models.InstallBody)
        if not ok_b:
            return err_b
        r = skill_studio.install(skill_id, mb.actor or "E",
                                 confirm=mb.confirm,
                                 allow_overwrite=mb.allow_overwrite)
        return _envelope(r, skill_id=skill_id)
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ---------------------------------------------------------------- 审计 / 总览 / 同步
@router.get("/audit")
def api_audit(skill: str = "", event: str = "", limit: int = 50):
    try:
        return {"ok": True, "records": audit.query(skill=skill, event=event,
                                                   limit=max(1, min(limit, 500)))}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/versions/{skill_id}")
def api_versions(skill_id: str):
    """版本历史：当前版本 + 被替代的版本（`superseded`，历史保留但不激活）。"""
    try:
        return skill_studio.versions(skill_id)
    except Exception as e:  # noqa: BLE001
        return _err(e)


# ---------------------------------------------------------------- 第二阶段：团队同步（doc §6）
@router.get("/team/status")
def api_team_status():
    """团队记忆库适配器状态（**不做网络调用**；默认关闭即完全离线）。"""
    try:
        return tencent_memory_adapter.status()
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/team/connection-test")
def api_team_connection_test():
    """连接测试（doc §6.4 第 6 步）。未启用时直接返回"跳过"，不发起请求。"""
    try:
        return tencent_memory_adapter.connection_test()
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/team/metadata")
def api_team_metadata():
    """拉取团队 Skill 元数据（不含内容）；未启用返回空列表。"""
    try:
        return tencent_memory_adapter.pull_team_metadata()
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/team/push/{skill_id}")
def api_team_push(skill_id: str):
    """把**已批准**的技能推到团队库（未批准/含敏感内容一律拒绝）。"""
    try:
        return tencent_memory_adapter.push_approved(skill_id)
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/team/import")
def api_team_import(body: dict | None = None):
    """把团队资产导入为**本地草稿**（绝不直接安装，仍需本地 L1/L2/L3 + 教师批准）。"""
    try:
        ok_b, err_b, mb = _check(body, models.TeamImportBody)
        if not ok_b:
            return err_b
        return tencent_memory_adapter.import_team_asset(mb.model_dump())
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/team/sync-log")
def api_team_sync_log(limit: int = 50):
    """同步日志：本地 ID / 远程 ID / 版本 / 时间 / 失败原因。"""
    try:
        return tencent_memory_adapter.sync_log(limit=limit)
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/safe-import")
def api_safe_import():
    """路径/文件名白名单快照（doc 34 §5.5）：导入根、允许后缀、禁止后缀、ID 规则。"""
    try:
        return safe_import.describe()
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/overview")
def api_overview():
    """总览：配置项 / 目录映射 / 状态映射 / 候选数与队列长度（无前端）。"""
    try:
        return skill_studio.overview()
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/registry")
def api_registry():
    try:
        return {"ok": True, **registry_db.stats(), "reconcile": registry_db.reconcile()}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.post("/sync/{skill_id}/enqueue")
def api_enqueue_sync(skill_id: str, body: dict | None = None):
    """把已批准且已安装的技能放进团队同步队列（未批准一律拒绝）。"""
    try:
        b = body or {}
        return skill_studio.enqueue_sync(skill_id, str(b.get("actor") or "E"))
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/sync/outbox")
def api_outbox():
    try:
        return skill_studio.outbox()
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/export-policy/{skill_id}")
def api_export_policy(skill_id: str):
    """能不能出团队库（状态 + 批准哈希 + 敏感内容 + 风险等级，逐条给理由）。"""
    try:
        return {"ok": True, **skill_privacy.export_policy(skill_id)}
    except Exception as e:  # noqa: BLE001
        return _err(e)


@router.get("/status/{skill_id}")
def api_status(skill_id: str):
    """doc 状态机 ↔ 已有状态机的对照视图。"""
    try:
        cur = store.effective_status(skill_id)
        return {"ok": True, "skill": skill_id, "state": cur,
                "studio_state": skill_studio.STATE_ALIAS.get(cur, cur),
                "status": lifecycle.status(skill_id),
                "approval": skill_studio.approval_status(skill_id)}
    except Exception as e:  # noqa: BLE001
        return _err(e)
