# -*- coding: utf-8 -*-
"""Skill Studio（doc 34 §5）：候选 → 草稿 → 审查 → 批准 → 安装 → 可选同步。

**这一层不重写流水线**，它是已有实现的"工作室视图"：

| doc 34 的模块 | 复用什么 |
|---|---|
| `skill_candidates.py` 候选检测 | `miner.py`（挖掘/评分/R1~R6）+ `runs.py`（对话证据） |
| `skill_studio.py` 草稿/版本/审批/安装/归档 | `lifecycle.py`（状态机）+ `store.py`（四区/版本）+ 本文件（审批记录/安装闸门/同步队列） |
| `skill_privacy.py` 脱敏/风险/导出 | 本文件旁边的 `skill_privacy.py` |

doc 的状态机 `detected → draft → in_review → approved → installed` 与已有状态机是**同一件事的
两套叫法**，这里做显式映射（不新增第二套状态字段）：

    detected    ↔ 候选（candidates/<sig>.json，state=candidate）
    draft       ↔ draft
    in_review   ↔ submitted
    approved    ↔ approved（且 reviews/<id>.json 记录了内容哈希）
    installed   ↔ published（文件已复制到全局 data/skills）
    rejected    ↔ rejected / draft（带理由）
    superseded  ↔ rolled_back（被新版替代）

**安装闸门**（doc §5.4，五条全过才允许）：
1. 存在有效的教师批准记录；2. 批准哈希与当前草稿**完全一致**；3. 同名覆盖已获明确允许；
4. 动作级权限策略允许本地写入；5. 包检查（路径/大小/前置元数据/资源清单）通过。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from edu_agent.skill_market import audit, lifecycle, manifest as mf, paths
from edu_agent.skill_market import perm_scan, registry_db, store

# doc 34 §7.1 的配置项（.env；缺省值就是文档里的默认值）
_ENV_DEFAULTS: dict[str, str] = {
    "SKILL_STUDIO_ENABLED": "1",
    "SKILL_STUDIO_DIR": "data/skill_studio",
    "SKILL_CANDIDATE_WINDOW_DAYS": "30",
    "SKILL_CANDIDATE_MIN_SESSIONS": "3",
    "SKILL_CANDIDATE_COOLDOWN_DAYS": "30",
    "SKILL_STUDIO_EXTRACTION_PROVIDER": "local",
    "SKILL_STUDIO_EXECUTE_PLUGIN": "1",     # 1=L3 影子运行真跑（更严）；0=只做静态核对（doc §1.3 第一版口径）
    "TENCENT_MEMORY_ENABLED": "0",
    "TENCENT_MEMORY_API_BASE": "",
}

STATE_MAP: dict[str, str] = {
    "detected": "candidate", "draft": "draft", "in_review": "submitted",
    "approved": "approved", "installed": "published", "rejected": "rejected",
    "superseded": "rolled_back",
}
STATE_ALIAS: dict[str, str] = {v: k for k, v in STATE_MAP.items() if v != "candidate"}


# ---------------------------------------------------------------- 配置
def config() -> dict:
    """读 doc 34 §7.1 的配置项（.env / 环境变量），并同步给挖掘器阈值。"""
    out = {}
    for k, dv in _ENV_DEFAULTS.items():
        out[k] = str(os.getenv(k) or dv)
    out["enabled"] = out["SKILL_STUDIO_ENABLED"] not in ("0", "false", "no", "off")
    out["execute_plugin"] = out["SKILL_STUDIO_EXECUTE_PLUGIN"] not in ("0", "false", "no", "off")
    try:
        from edu_agent.skill_market import runs

        runs.set_config(
            window_days=int(out["SKILL_CANDIDATE_WINDOW_DAYS"]),
            min_sessions=int(out["SKILL_CANDIDATE_MIN_SESSIONS"]),
            reject_cooldown_days=int(out["SKILL_CANDIDATE_COOLDOWN_DAYS"]),
        )
    except Exception:  # noqa: BLE001  配置读取失败不能打断主链路
        pass
    return out


# ---------------------------------------------------------------- 目录视图
def reviews_dir() -> Path:
    return paths.market_root() / "reviews"


def outbox_dir() -> Path:
    return paths.market_root() / "sync_outbox"


def studio_dirs() -> dict:
    """doc 34 §5.1 的目录名 → 实际落盘路径（**别名视图，不复制数据**）。"""
    return {
        "root": str(paths.market_root()),
        "candidates": str(paths.candidates_dir()),
        "drafts": str(paths.drafts_dir()),
        "reviews": str(reviews_dir()),
        "audit": str(paths.audit_dir()),
        "archive": str(paths.archive_dir()),
        "sync_outbox": str(outbox_dir()),
        # 已有实现额外用到的（保留）：
        "submitted": str(paths.submitted_dir()),
        "published": str(paths.published_dir()),
        "fixtures": str(paths.fixtures_dir()),
    }


def ensure_dirs() -> None:
    paths.ensure_dirs()
    reviews_dir().mkdir(parents=True, exist_ok=True)
    outbox_dir().mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- 内容哈希（批准绑定用）
def skill_files(skill_id: str) -> list[str]:
    """技能包里的全部文件（优先取已安装目录，其次草稿目录）。

    **跳过构建产物**：`__pycache__/` 与 `*.pyc` 是运行 L3 时 Python 顺手写下的，
    不是技能内容 —— 若算进内容哈希，"批准 → 提交（会跑 L3）→ 哈希变了" 就会连环失效（实测踩过）。
    """
    for d in (paths.runtime_plugin_path(skill_id), paths.drafts_dir() / skill_id):
        if Path(d).is_dir():
            return [str(p) for p in sorted(Path(d).rglob("*"))
                    if p.is_file() and _is_content_file(p)]
    found = store.find_skill(skill_id)
    if found:
        return [str(p) for p in sorted(Path(found["dir"]).rglob("*"))
                if p.is_file() and _is_content_file(p)]
    return []


def _is_content_file(p: Path) -> bool:
    """构建产物不算技能内容。"""
    if "__pycache__" in p.parts:
        return False
    return p.suffix.lower() not in (".pyc", ".pyo")


def content_hash(skill_id: str) -> str:
    """**内容哈希**：技能正文 + 全部资源文件的 sha256（doc §1.2 的"批准绑定哈希"）。

    三个关键取舍（都踩过）：

    * **SKILL.md 只哈希正文（front-matter 之后的 body）**，不含 front-matter ——
      发布时 `publish()` 会改写 `version/status/review/updated`，算进去的话
      "批准 → 安装"之后哈希必然失配，批准永远失效。
    * **排除 fixture（测试资产）**：`install_to_runtime()` 刻意不把 `fixture.json`
      复制进运行时目录（免得被当技能内容读走），若把它算进哈希，草稿区与运行时目录的
      文件集合不同 → 哈希同样必然失配。fixture 是内部测试用例，不是教师批准的内容。
    * **其余资源文件按 名字+字节 全量哈希** —— 插件、脚本改一个字节就失效。
    """
    import dataclasses

    fixture_name = ""
    try:
        found = store.find_skill(skill_id)
        if found:
            fixture_name = str(found["manifest"].fixture or "")
    except Exception:  # noqa: BLE001
        fixture_name = ""
    h = hashlib.sha256()
    for f in skill_files(skill_id):
        p = Path(f)
        if fixture_name and p.name == fixture_name:
            continue
        try:
            data = p.read_bytes()
        except OSError:
            continue
        if p.name == "SKILL.md":
            text = data.decode("utf-8", "replace")
            body = text.split("\n---", 1)[-1] if text.startswith("---") else text
            h.update(b"SKILL.md:body\0")
            h.update(body.strip().encode("utf-8"))
        else:
            h.update(p.name.encode("utf-8"))
            h.update(b"\0")
            h.update(data)
        h.update(b"\0")
    return "sha256:" + h.hexdigest()


def resource_manifest(skill_id: str) -> list[dict]:
    """资源清单（文件名 + 大小 + 单文件哈希）—— 给审查页展示与包检查用。"""
    out: list[dict] = []
    for f in skill_files(skill_id):
        p = Path(f)
        try:
            data = p.read_bytes()
        except OSError:
            continue
        out.append({"name": p.name, "bytes": len(data),
                    "sha256": hashlib.sha256(data).hexdigest()[:16]})
    return out


# ---------------------------------------------------------------- 审批记录
def review_file(skill_id: str) -> Path:
    return reviews_dir() / ("%s.json" % skill_id)


def record_approval(skill_id: str, reviewer: str, *, note: str = "") -> dict:
    """写教师批准记录（绑定**当前**内容哈希）。同一技能再次批准会覆盖本文件，
    但每次批准都进不可变审计（audit/ 只追加 + 哈希链）。"""
    ensure_dirs()
    sid = str(skill_id or "").strip()
    if not sid:
        return {"ok": False, "error": "缺少 skill_id"}
    if not str(reviewer or "").strip():
        return {"ok": False, "error": "批准人不能为空（doc §5.4 要求可追溯）"}
    h = content_hash(sid)
    rec = {"skill": sid, "reviewer": str(reviewer), "note": str(note)[:300],
           "content_hash": h, "resources": resource_manifest(sid),
           "approved_at": time.time(),
           "approved_at_text": time.strftime("%Y-%m-%dT%H:%M:%S")}
    p = review_file(sid)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
    audit.append("skill-studio.approve", skill=sid, actor=reviewer, input_text=note,
                 result={"ok": True, "code": "approved", "content_hash": h},
                 gates={"hash_binding": "ok"})
    return {"ok": True, **rec}


def approval_status(skill_id: str) -> dict:
    """批准是否**仍然有效**（内容一改就失效 —— doc §8.1 的验收点）。"""
    p = review_file(skill_id)
    if not p.exists():
        return {"ok": True, "approved": False, "hash_matches": False,
                "reason": "没有批准记录"}
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return {"ok": True, "approved": False, "hash_matches": False,
                "reason": "批准记录损坏"}
    cur = content_hash(skill_id)
    matches = str(rec.get("content_hash")) == cur
    return {"ok": True, "approved": True, "hash_matches": matches,
            "reviewer": rec.get("reviewer"), "approved_at": rec.get("approved_at_text"),
            "content_hash": rec.get("content_hash"), "current_hash": cur,
            "reason": "ok" if matches else "批准后内容被改过（哈希不一致）"}


# ---------------------------------------------------------------- 草稿校验与审查流转
def validate_draft(skill_id: str) -> dict:
    """结构校验（doc §5.3 的最小结构 + 包检查）：前置元数据 / 路径 / 大小 / 资源清单。"""
    sid = str(skill_id or "").strip()
    found = store.find_skill(sid)
    if found is None:
        return {"ok": False, "error": "找不到技能：%s" % sid}
    man = found["manifest"]
    problems: list[str] = []
    for err in man.errors():
        problems.append("manifest：" + err)
    if len(man.description) < 10:
        problems.append("description 太短（<10 字），教师无法判断用途")
    files = skill_files(sid)
    total = 0
    for f in files:
        p = Path(f)
        try:
            total += p.stat().st_size
        except OSError:
            problems.append("资源读不到：%s" % p.name)
        if p.suffix.lower() in (".exe", ".dll", ".bat", ".cmd", ".ps1", ".sh"):
            problems.append("包内含可执行文件（第一版禁止）：%s" % p.name)
    if total > 2 * 1024 * 1024:
        problems.append("包体积过大（>2MB）：%d 字节" % total)
    rep = perm_scan.scan_draft_dir(Path(found["dir"]))[2]
    if not rep.ok:
        problems.append("权限扫描未过：" + rep.review_note())
    return {"ok": not problems, "problems": problems, "skill": sid,
            "files": len(files), "bytes": total,
            "content_hash": content_hash(sid), "scan": rep.to_dict()}


def ensure_editable(skill_id: str, actor: str = "E") -> dict:
    """把技能**变成可编辑的草稿态**（doc 流程里"教师改草稿"这一步的前置）。

    | 当前状态 | 动作 |
    |---|---|
    | draft | 已经是草稿，直接改 |
    | approved | `lifecycle.revise()` 撤回重改（还没安装，最省事） |
    | published / disabled / rolled_back | `lifecycle.new_draft()` 派生新草稿（**线上版本继续服务**） |
    | submitted / rejected | 拒绝：在审的不能改（先驳回），驳回的已在草稿区 |
    """
    sid = str(skill_id or "").strip()
    cur = store.effective_status(sid)
    if cur == "draft":
        return {"ok": True, "already": True, "state": "draft", "skill": sid}
    if cur == "approved":
        r = lifecycle.revise(sid, actor, reason="内容需修改")
        return {**r, "state": "draft"}
    if cur in ("published", "disabled", "rolled_back"):
        r = lifecycle.new_draft(sid, actor, note="内容需修改")
        return {**r, "state": "draft"}
    return {"ok": False, "error": "状态 %s 不能直接编辑（在审的先驳回；已驳回的已在草稿区）"
                                  % (cur or "未知"), "state": cur, "skill": sid}


def submit_review(skill_id: str, actor: str = "E") -> dict:
    """draft → in_review（= 已有状态机的 submit，**内部强制 L1+L2+L3**）。"""
    v = validate_draft(skill_id)
    if not v.get("ok"):
        return {"ok": False, "gate": "validate", "error": "；".join(v["problems"]),
                "validation": v}
    r = lifecycle.submit(skill_id, actor)
    if r.get("ok"):
        r["studio_state"] = "in_review"
        r["content_hash"] = content_hash(skill_id)
    return r


def approve(skill_id: str, reviewer: str, *, note: str = "") -> dict:
    """in_review → approved：写批准记录（绑定哈希）**并**走已有状态机的 approve。"""
    cur = store.effective_status(skill_id)
    if cur != "submitted":
        return {"ok": False, "error": "状态 %s 不能批准（需先 submit-review）" % cur}
    if str(reviewer).strip().lower() == str((store.find_skill(skill_id) or {})
                                            .get("manifest", mf.SkillManifest(name="x")).owner
                                            or "").strip().lower():
        return {"ok": False, "error": "批准人不能是创建者本人（职责分离）"}
    r = lifecycle.approve(skill_id, reviewer, note=note)
    if not r.get("ok"):
        return r
    rec = record_approval(skill_id, reviewer, note=note)
    r.update({"studio_state": "approved", "approval": rec})
    return r


def reject(skill_id: str, reviewer: str, *, reason: str) -> dict:
    """in_review → rejected（回草稿 + 冷却期，doc §5.4）。"""
    r = lifecycle.reject(skill_id, reviewer, reason=reason)
    if r.get("ok"):
        r["studio_state"] = "draft"
    return r


def install(skill_id: str, actor: str = "E", *, confirm: bool = False,
            allow_overwrite: bool = False) -> dict:
    """approved → installed：**doc §5.4 的五条闸门全过**才安装。

    安装本身 = 调用已有的 `lifecycle.publish()`（复制落盘到全局 data/skills + status=published）。
    **不碰 kernel、不碰会话绑定**（那条铁律在这里同样成立）。
    """
    sid = str(skill_id or "").strip()
    checks: dict = {}

    appr = approval_status(sid)
    checks["approval_exists"] = bool(appr.get("approved"))
    checks["hash_matches"] = bool(appr.get("hash_matches"))
    if not checks["approval_exists"]:
        return {"ok": False, "gate": "approval", "checks": checks,
                "error": "没有教师批准记录：不能安装（doc §5.4 第 1 条）"}
    if not checks["hash_matches"]:
        return {"ok": False, "gate": "stale", "checks": checks,
                "error": "批准后内容被改过，须重新批准（doc §5.4 第 2 条）：%s"
                         % appr.get("reason")}

    exists = paths.is_inside_runtime(sid)
    checks["overwrite_ok"] = (not exists) or bool(allow_overwrite)
    if not checks["overwrite_ok"]:
        return {"ok": False, "gate": "overwrite", "checks": checks,
                "error": "同名技能已在运行时目录：需要明确允许覆盖（allow_overwrite=true）"}

    v = validate_draft(sid)
    checks["package_ok"] = bool(v.get("ok"))
    if not checks["package_ok"]:
        return {"ok": False, "gate": "package", "checks": checks,
                "error": "包检查未过：" + "；".join(v["problems"]), "validation": v}

    # 同名覆盖前先留可恢复备份（doc §8.1）
    backup = None
    if exists:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = paths.archive_dir() / sid / ("before-install-%s" % stamp)
        backup.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copytree(paths.runtime_plugin_path(sid), backup, dirs_exist_ok=True)
        except OSError:
            backup = None

    pub = lifecycle.publish(sid, actor, confirm=confirm)   # 权限闸门在里面（第 4 条）
    if not pub.get("ok"):
        pub["checks"] = checks
        return pub
    checks["permission"] = "allow"
    registry_db.set_status(sid, "published", file_path=str(
        Path(pub.get("installed_to") or "") / "SKILL.md"))
    pub.update({"studio_state": "installed", "checks": checks,
                "backup": str(backup) if backup else "",
                "content_hash": content_hash(sid)})
    return pub


# ---------------------------------------------------------------- 候选箱（脱敏视图）
def candidates(limit: int = 50) -> dict:
    """候选箱：doc §5.2 要求"只展示脱敏证据摘要、次数、共同约束"，**不回显原始对话**。"""
    from edu_agent.skill_market import miner, skill_privacy

    rows = miner.list_candidates()[:max(1, int(limit))]
    out: list[dict] = []
    for c in rows:
        ev = []
        for e in (c.get("evidence") or [])[:5]:
            ev.append({"session_id": e.get("session_id"), "turn": e.get("turn"),
                       "outcome": e.get("outcome")})          # 只留编号，不带原文
        title = str(c.get("title") or "")
        sens = skill_privacy.scan_text(title)
        out.append({
            "candidate_id": c.get("candidate_id"), "signature": c.get("signature"),
            "state": c.get("state", "candidate"),
            "studio_state": "detected" if c.get("state") == "candidate" else c.get("state"),
            "title": title if not sens["sensitive"] else skill_privacy.scrub_markdown(title),
            "runs": c.get("runs"), "sessions": c.get("sessions"),
            "score": c.get("score"), "criteria": c.get("criteria"),
            "blocking": c.get("blocking"), "evidence": ev,
            "cooldown_until": c.get("cooling_until") or 0,
            "sensitive_title": sens["sensitive"],
            "skill_id": c.get("skill_id") or "",
        })
    return {"ok": True, "count": len(out), "candidates": out}


# ---------------------------------------------------------------- 团队同步队列（doc §6）
def enqueue_sync(skill_id: str, actor: str = "E") -> dict:
    """把**已批准且已安装**的技能放进 `sync_outbox/`（待同步团队库）。

    doc §8.1 明确：**未批准的 Skill 不允许进入同步队列** —— 这里用
    `skill_privacy.export_policy()` 一次判完（状态/批准哈希/敏感内容/风险）。
    """
    from edu_agent.skill_market import skill_privacy

    sid = str(skill_id or "").strip()
    pol = skill_privacy.export_policy(sid)
    if not pol.get("allowed"):
        audit.append("skill-studio.sync.refused", skill=sid, actor=actor,
                     input_text="enqueue_sync",
                     result={"ok": False, "code": "policy", "reasons": pol.get("reasons")})
        return {"ok": False, "gate": "export_policy", "error": "不允许进入同步队列",
                "reasons": pol.get("reasons"), "risk": pol.get("risk")}

    ensure_dirs()
    dest = outbox_dir() / sid
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    for f in skill_files(sid):
        src = Path(f)
        try:
            shutil.copy2(src, dest / src.name)
        except OSError:
            continue
    meta = {"skill": sid, "version": (registry_db.get(sid) or {}).get("version", ""),
            "content_hash": content_hash(sid), "risk": pol.get("risk"),
            "enqueued_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "actor": str(actor),
            "resources": resource_manifest(sid)}
    (dest / "sync.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    audit.append("skill-studio.sync.enqueue", skill=sid, actor=actor,
                 input_text="enqueue_sync",
                 result={"ok": True, "code": "queued", "content_hash": meta["content_hash"]},
                 gates={"export_policy": "pass", "risk": pol.get("risk")})
    return {"ok": True, "queued": sid, "dir": str(dest), "meta": meta}


def outbox() -> dict:
    """同步队列内容（团队适配器从这里取，doc §6.2）。"""
    d = outbox_dir()
    items: list[dict] = []
    if d.exists():
        for child in sorted(d.iterdir()):
            meta_file = child / "sync.json"
            if not child.is_dir() or not meta_file.exists():
                continue
            try:
                items.append(json.loads(meta_file.read_text(encoding="utf-8")))
            except ValueError:
                continue
    return {"ok": True, "count": len(items), "items": items}


def overview() -> dict:
    """工作室总览（给接口/自检；**不含前端**）。"""
    cfg = config()
    return {"ok": True, "enabled": cfg["enabled"], "config": cfg,
            "dirs": studio_dirs(), "candidates": candidates()["count"],
            "outbox": outbox()["count"],
            "state_map": STATE_MAP, "state_alias": STATE_ALIAS}
