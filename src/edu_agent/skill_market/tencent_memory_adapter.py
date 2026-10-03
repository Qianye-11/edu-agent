# -*- coding: utf-8 -*-
"""TencentDB Agent Memory 适配器（doc 34 §6.2）。

> 第二阶段能力。**默认关闭**（`TENCENT_MEMORY_ENABLED=0`），关闭时本模块
> **零网络调用**、也不要求任何依赖 —— 本地对话/教案/课件/Skill 全部照常（doc §6.1）。

职责（逐条对应 doc §6.2）：

| doc 要求 | 实现 |
|---|---|
| 读取本地 `sync_outbox` | `outbox()` |
| 将已批准 Skill 转换为团队资产 | `to_team_asset()` |
| 拉取团队 Skill 元数据 | `pull_team_metadata()` |
| 将远程资产**导入为本地 draft，而非直接安装** | `import_team_asset()`（只写草稿区，绝不 publish） |
| 记录本地 ID、远程 ID、版本、同步时间和失败原因 | `_log_sync()` → `<market>/sync_log.jsonl` |
| 不处理未脱敏的学生原始数据 | 出站前一律过 `skill_privacy.export_policy()` + `scan_text()` |

安全默认（doc §6.1/§6.3）：**只同步教师已批准的 Skill 包**；原始对话、学生作品/成绩/姓名
永不自动同步；任何一步失败都只记日志，**不影响本地功能**。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

from edu_agent.skill_market import paths, skill_privacy, skill_studio

# 同步矩阵（doc §6.3）：写进代码，避免"实现时顺手多同步了点东西"
SYNC_MATRIX: dict[str, str] = {
    "teacher_conversation": "never",       # 教师原始对话：不同步
    "student_artifact": "never",           # 学生作品/成绩/姓名：永不自动同步
    "approved_skill": "manual",            # 已批准 Skill：教师手动选择后可同步
    "skill_metadata": "auto",              # 元数据/版本/哈希：可同步
    "team_skill": "draft_only",            # 团队 Skill：导入为本地草稿，需教师批准
    "approval_record": "local_only",       # 审批记录：默认本地保留
}

DEFAULT_TIMEOUT = 10.0


# ---------------------------------------------------------------- 配置与连通性
def config() -> dict:
    """读 `TENCENT_MEMORY_*`（doc §7.1）。密钥只从环境读，不落配置文件。"""
    import os

    return {
        "enabled": (os.getenv("TENCENT_MEMORY_ENABLED") or "0").strip().lower()
                   in ("1", "true", "yes", "on"),
        "api_base": (os.getenv("TENCENT_MEMORY_API_BASE") or "").strip().rstrip("/"),
        "team_id": (os.getenv("TENCENT_MEMORY_TEAM_ID") or "").strip(),
        "timeout": float(os.getenv("TENCENT_MEMORY_TIMEOUT") or DEFAULT_TIMEOUT),
        "token_env": "TENCENT_MEMORY_TOKEN",
    }


def _token() -> str:
    import os

    return (os.getenv(config()["token_env"]) or "").strip()


def connection_test() -> dict:
    """连接测试（doc §6.4 第 6 步）。关闭时**不发任何请求**。"""
    cfg = config()
    if not cfg["enabled"]:
        return {"ok": True, "connected": False, "skipped": True,
                "reason": "TENCENT_MEMORY_ENABLED=0：本地模式，不做任何远程调用",
                "config": {k: v for k, v in cfg.items() if k != "token_env"}}
    if not cfg["api_base"]:
        return {"ok": False, "connected": False,
                "error": "已启用但未配置 TENCENT_MEMORY_API_BASE",
                "config": cfg}
    if not _token():
        return {"ok": False, "connected": False,
                "error": "已启用但环境变量 %s 为空" % cfg["token_env"], "config": cfg}
    started = time.time()
    try:
        got = _http("GET", "/health", None, cfg)
        return {"ok": True, "connected": True, "ms": int((time.time() - started) * 1000),
                "remote": got, "config": cfg}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "connected": False,
                "error": "%s: %s" % (type(e).__name__, e), "config": cfg}


def _http(method: str, path: str, payload: dict | None, cfg: dict) -> dict:
    url = cfg["api_base"] + path
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json; charset=utf-8",
        "Authorization": "Bearer " + _token(),
    })
    with urllib.request.urlopen(req, timeout=cfg["timeout"]) as resp:  # noqa: S310
        raw = resp.read().decode("utf-8", "replace")
    try:
        return json.loads(raw) if raw.strip() else {}
    except ValueError:
        return {"raw": raw[:500]}


# ---------------------------------------------------------------- 本地队列
def outbox() -> dict:
    """读取本地同步队列（doc §6.2 第 1 条）——`skill_studio.enqueue_sync()` 的产物。"""
    return skill_studio.outbox()


def sync_log_file() -> Path:
    return paths.market_root() / "sync_log.jsonl"


def _log_sync(entry: dict) -> None:
    """记录 本地 ID / 远程 ID / 版本 / 时间 / 失败原因（doc §6.2 第 5 条）。"""
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "ts": time.time(), **entry}
    try:
        sync_log_file().parent.mkdir(parents=True, exist_ok=True)
        with sync_log_file().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def sync_log(limit: int = 50) -> dict:
    p = sync_log_file()
    rows: list[dict] = []
    if p.exists():
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, limit):]:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return {"ok": True, "count": len(rows), "entries": rows}


# ---------------------------------------------------------------- 出站：已批准 Skill → 团队资产
def to_team_asset(skill_id: str) -> dict:
    """把技能转换成团队资产载荷（**只含元数据与哈希，不含对话/学生数据**）。

    出站前二道闸门：
    1. `skill_privacy.export_policy()` —— 状态已安装 ∧ 批准哈希一致 ∧ 无敏感内容；
    2. 逐文件 `scan_text()` —— 命中任何敏感模式直接拒绝（doc §6.3 的 "永不自动同步"）。
    """
    sid = str(skill_id or "").strip()
    pol = skill_privacy.export_policy(sid)
    if not pol.get("allowed"):
        return {"ok": False, "gate": "export_policy", "skill": sid,
                "error": "不允许同步到团队库", "reasons": pol.get("reasons")}
    blobs: dict[str, str] = {}
    for f in skill_studio.skill_files(sid):
        p = Path(f)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        hit = skill_privacy.scan_text(text)
        if hit["sensitive"]:
            return {"ok": False, "gate": "privacy", "skill": sid,
                    "error": "文件 %s 含敏感信息，拒绝出站：%s"
                             % (p.name, "、".join(h["label"] for h in hit["hits"]))}
        blobs[p.name] = text
    meta = skill_studio.resource_manifest(sid)
    asset = {
        "kind": "edu_agent.skill",
        "skill_id": sid,
        "version": (json.loads(skill_studio.review_file(sid).read_text(encoding="utf-8"))
                    .get("version", "") if skill_studio.review_file(sid).exists() else ""),
        "content_hash": skill_studio.content_hash(sid),
        "risk": pol.get("risk"),
        "resources": meta,
        "files": blobs,                    # 只含技能包文本（已逐个过隐私扫描）
        "team_id": config().get("team_id") or "",
        "synced_from": "local-skill-studio",
    }
    return {"ok": True, "asset": asset}


def push_approved(skill_id: str) -> dict:
    """把**已批准**的技能推到团队库。未启用 → 不联网，如实返回原因。"""
    cfg = config()
    sid = str(skill_id or "").strip()
    if not cfg["enabled"]:
        _log_sync({"direction": "push", "skill_id": sid, "remote_id": "",
                   "ok": False, "reason": "TENCENT_MEMORY_ENABLED=0（本地模式）"})
        return {"ok": False, "connected": False, "skill": sid,
                "error": "团队库未启用（TENCENT_MEMORY_ENABLED=0）：本地模式不发起任何同步",
                "note": "本地功能不受影响；要启用请按 doc 34 §6.4 在独立环境部署后配置 API_BASE"}
    asset = to_team_asset(sid)
    if not asset.get("ok"):
        _log_sync({"direction": "push", "skill_id": sid, "remote_id": "",
                   "ok": False, "reason": asset.get("error")})
        return asset
    try:
        got = _http("POST", "/v1/skills", asset["asset"], cfg)
    except Exception as e:  # noqa: BLE001
        _log_sync({"direction": "push", "skill_id": sid, "remote_id": "",
                   "version": asset["asset"].get("version"), "ok": False,
                   "reason": "%s: %s" % (type(e).__name__, e)})
        return {"ok": False, "connected": True, "skill": sid,
                "error": "推送失败（本地不受影响）：%s: %s" % (type(e).__name__, e)}
    remote_id = str((got or {}).get("id") or (got or {}).get("skill_id") or "")
    _log_sync({"direction": "push", "skill_id": sid, "remote_id": remote_id,
               "version": asset["asset"].get("version"), "ok": True, "reason": ""})
    return {"ok": True, "skill": sid, "remote_id": remote_id, "remote": got,
            "content_hash": asset["asset"]["content_hash"]}


# ---------------------------------------------------------------- 入站：团队元数据 → 本地草稿
def pull_team_metadata() -> dict:
    """拉取团队 Skill 元数据（不含内容）。未启用 → 空列表 + 原因。"""
    cfg = config()
    if not cfg["enabled"]:
        return {"ok": True, "connected": False, "count": 0, "skills": [],
                "reason": "TENCENT_MEMORY_ENABLED=0（本地模式）"}
    try:
        got = _http("GET", "/v1/skills?team_id=%s" % cfg["team_id"], None, cfg)
    except Exception as e:  # noqa: BLE001
        _log_sync({"direction": "pull", "skill_id": "", "remote_id": "",
                   "ok": False, "reason": "%s: %s" % (type(e).__name__, e)})
        return {"ok": False, "connected": True,
                "error": "拉取失败（本地不受影响）：%s: %s" % (type(e).__name__, e)}
    rows = got.get("skills") if isinstance(got, dict) else got
    rows = rows if isinstance(rows, list) else []
    return {"ok": True, "connected": True, "count": len(rows), "skills": rows}


def import_team_asset(remote: dict, *, actor: str = "team-sync") -> dict:
    """把团队资产导入为**本地草稿**（doc §6.2：「而非直接安装」）。

    三条硬约束：
    1. **只写草稿区**，绝不 publish、绝不装进运行时目录；
    2. 过 `safe_import.validate_resource_name()` 白名单与 `skill_privacy` 扫描；
    3. 记录 本地 ID / 远程 ID / 版本 / 时间（`sync_log.jsonl`）。
    """
    import dataclasses

    from edu_agent.skill_market import manifest as mf, safe_import, store

    remote_id = str((remote or {}).get("skill_id") or (remote or {}).get("id") or "")
    files = (remote or {}).get("files") or {}
    if not isinstance(files, dict) or not files:
        return {"ok": False, "error": "远程资产没有 files", "remote_id": remote_id}
    md = str(files.get("SKILL.md") or "")
    if not md.strip():
        return {"ok": False, "error": "远程资产缺少 SKILL.md", "remote_id": remote_id}

    man, body = mf.from_document(md)
    ok_id, why_id, sid = safe_import.validate_skill_id(man.name)
    if not ok_id:
        return {"ok": False, "error": "远程技能名不合法：%s" % why_id, "remote_id": remote_id}

    clean: dict[str, str] = {}
    for name, text in files.items():
        ok_n, why_n, norm = safe_import.validate_resource_name(str(name))
        if not ok_n:
            return {"ok": False, "error": "远程资产含不允许的资源：%s" % why_n}
        hit = skill_privacy.scan_text(str(text))
        if hit["sensitive"]:
            return {"ok": False, "error": "远程资产 %s 含敏感信息，拒绝导入：%s"
                                          % (norm, "、".join(h["label"] for h in hit["hits"]))}
        clean[norm] = str(text)

    # 打上团队来源标记（可追溯本地 ID / 远程 ID）
    man = dataclasses.replace(
        man, status="draft", source="team_import",
        extra=tuple(list(man.extra) + [("remote_id", remote_id),
                                       ("remote_hash", str((remote or {}).get("content_hash") or ""))]))
    store.write_draft(man, body, files={k: v for k, v in clean.items() if k != "SKILL.md"},
                      allow_code=True)
    store.write_state(sid, status="draft", area="draft", actor=actor,
                      teacher_id=actor, file_path=str(paths.drafts_dir() / sid / "SKILL.md"))
    _log_sync({"direction": "import", "skill_id": sid, "remote_id": remote_id,
               "version": str((remote or {}).get("version") or ""), "ok": True, "reason": ""})
    return {"ok": True, "skill": sid, "remote_id": remote_id, "state": "draft",
            "note": "已导入为本地草稿：**需教师走完 L1/L2/L3 并批准后才会安装**"}


def status() -> dict:
    """给运维/自检看的适配器状态（不做网络调用）。"""
    cfg = config()
    return {"ok": True, "enabled": cfg["enabled"], "api_base": cfg["api_base"] or "(未配置)",
            "team_id": cfg["team_id"] or "(未配置)",
            "outbox_count": outbox()["count"],
            "sync_matrix": SYNC_MATRIX,
            "note": ("本地模式：本适配器不做任何远程调用，本地功能与团队库完全解耦"
                     if not cfg["enabled"] else "已启用：同步失败只记日志，不影响本地功能")}
