# -*- coding: utf-8 -*-
"""服务端路径/文件名白名单（doc 34 §5.5）。

> "**不得接受客户端传入的本机绝对路径**；所有文件名、Skill ID 和资源路径均需服务端白名单验证。"

为什么必须收口：导入接口原本直接 `read_text(客户端给的 path)`，等于把"任意文件读取"暴露给调用方 ——
一个技能包的导入请求就能读走本机任意文件（含 `.env`、凭据）。这个模块把三条都管住：

| 维度 | 规则 |
|---|---|
| 导入路径 | 只允许**导入根目录之内**的文件（`EDU_SKILL_IMPORT_ROOTS`，默认 `<repo>/skill-src` + `<data_dir>/skill_inbox`）；相对路径按导入根解析；解析后（含 symlink/`..`）必须仍在根内 |
| 资源文件名 | 只允许**单层文件名**（不许有路径分隔符、`..`、盘符），后缀在白名单里，不许隐藏文件 |
| Skill ID | 必须匹配 `^[a-z0-9][a-z0-9_-]{1,47}$`（与 manifest 同一口径） |

所有拒绝都返回**人话原因**，便于前端直接展示；一律 fails closed（拿不准就拒绝）。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from edu_agent.config import load_settings

# Skill ID / 技能名：与 manifest 的校验口径完全一致
SKILL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,47}$")

# 技能包里允许出现的资源后缀（plugin.py / SKILL.md / fixture.json / 脚本 / 说明）
ALLOWED_RESOURCE_SUFFIXES: frozenset[str] = frozenset({
    ".py", ".md", ".json", ".txt", ".yml", ".yaml", ".csv", ".tsv",
})

# 明确禁止（即使后缀在白名单外也没机会，这里再挡一层"可执行/系统"类型）
DENIED_SUFFIXES: frozenset[str] = frozenset({
    ".exe", ".dll", ".sys", ".msi", ".bat", ".cmd", ".ps1", ".psm1", ".sh",
    ".com", ".scr", ".jar", ".pyd", ".so", ".vbs", ".js", ".mjs", ".cjs",
    ".pyc", ".pyo", ".pem", ".key", ".pfx", ".env",
})

MAX_IMPORT_BYTES = 512 * 1024          # 单个 SKILL.md 上限（防"传个 1GB 文件进来"）
MAX_RESOURCE_BYTES = 2 * 1024 * 1024   # 单个资源文件上限


def import_roots() -> list[Path]:
    """允许导入的根目录（可用 `EDU_SKILL_IMPORT_ROOTS` 覆盖，`;` 分隔）。"""
    env = (os.getenv("EDU_SKILL_IMPORT_ROOTS") or "").strip()
    if env:
        out = []
        for part in env.split(";"):
            part = part.strip()
            if part:
                out.append(Path(part).expanduser().resolve())
        return out
    # 本文件在 <repo>/src/edu_agent/skill_market/ 下，仓库根要往上数 4 层
    # （parents[2] 会算成 <repo>/src —— 实测踩过，导入根就变成 src/skill-src 了）
    root = Path(__file__).resolve().parents[3]          # edu-agent/
    data = Path(load_settings().data_dir)
    return [(root / "skill-src").resolve(), (data / "skill_inbox").resolve()]


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_import_path(raw: str) -> dict:
    """把客户端给的导入路径解析成**根内**的绝对路径。

    返回 `{"ok": bool, "path": str, "error": str, "root": str}`
    * 相对路径按第一个导入根解析（前端传 `my-skill/SKILL.md` 即可）；
    * 绝对路径必须落在某个导入根之内；`..` / symlink 一律先解析再判定；
    * 任何不确定 → 拒绝（fails closed）。
    """
    text = str(raw or "").strip()
    if not text:
        return {"ok": False, "error": "缺少 path"}
    if "\x00" in text:
        return {"ok": False, "error": "路径含非法字符"}
    roots = import_roots()
    if not roots:
        return {"ok": False, "error": "服务端未配置导入根（EDU_SKILL_IMPORT_ROOTS）"}

    cand = Path(text).expanduser()
    tried: list[str] = []
    if not cand.is_absolute():
        for r in roots:
            p = (r / cand).resolve()
            if _inside(p, r):
                tried.append(str(p))
                if p.is_file():
                    return {"ok": True, "path": str(p), "root": str(r), "error": ""}
        return {"ok": False, "path": "", "error": "导入根内找不到该文件：%s（找过：%s）"
                                                 % (text, "、".join(tried) or "无"),
                "roots": [str(r) for r in roots]}

    p = cand.resolve()
    for r in roots:
        if _inside(p, r):
            if not p.is_file():
                return {"ok": False, "path": str(p), "error": "文件不存在：%s" % p}
            return {"ok": True, "path": str(p), "root": str(r), "error": ""}
    return {"ok": False, "path": "",
            "error": "拒绝读取导入根之外的本机路径（doc 34 §5.5）：%s" % p,
            "roots": [str(r) for r in roots]}


def validate_skill_id(raw: str) -> tuple[bool, str, str]:
    """Skill ID 白名单。返回 (ok, why, normalized)。"""
    sid = str(raw or "").strip()
    if not sid:
        return False, "缺少 skill id", ""
    if not SKILL_ID_RE.match(sid):
        return False, ("skill id 必须是 kebab-case（^[a-z0-9][a-z0-9_-]{1,47}$，当前 %r）" % sid), ""
    return True, "ok", sid


def validate_resource_name(raw: str) -> tuple[bool, str, str]:
    """资源文件名白名单：单层文件名 + 后缀白名单 + 不许隐藏文件。"""
    name = str(raw or "").strip()
    if not name:
        return False, "文件名不能为空", ""
    if name != Path(name).name:
        return False, "资源名必须是单层文件名，不能带路径：%r" % name, ""
    if name in (".", "..") or ".." in name:
        return False, "资源名不能包含 ..：%r" % name, ""
    if name.startswith("."):
        return False, "不允许隐藏文件（以 . 开头）：%r" % name, ""
    suffix = Path(name).suffix.lower()
    if suffix in DENIED_SUFFIXES:
        return False, "禁止的文件类型：%s" % suffix, ""
    if suffix not in ALLOWED_RESOURCE_SUFFIXES:
        return False, ("不允许的后缀 %r（白名单：%s）"
                       % (suffix, "、".join(sorted(ALLOWED_RESOURCE_SUFFIXES)))), ""
    return True, "ok", name


def check_size(path: str | Path, limit: int = MAX_IMPORT_BYTES) -> tuple[bool, str]:
    """体积上限检查（避免把超大文件塞进市场）。"""
    try:
        n = Path(path).stat().st_size
    except OSError as e:
        return False, "读不到文件大小：%s" % e
    if n > limit:
        return False, "文件过大（%d 字节 > 上限 %d）" % (n, limit)
    return True, "ok"


def describe() -> dict:
    """给自检/运维看的白名单快照。"""
    return {"ok": True,
            "import_roots": [str(r) for r in import_roots()],
            "allowed_suffixes": sorted(ALLOWED_RESOURCE_SUFFIXES),
            "denied_suffixes": sorted(DENIED_SUFFIXES),
            "skill_id_regex": SKILL_ID_RE.pattern,
            "max_import_bytes": MAX_IMPORT_BYTES,
            "max_resource_bytes": MAX_RESOURCE_BYTES}
