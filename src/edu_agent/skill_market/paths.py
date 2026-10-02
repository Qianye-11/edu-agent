# -*- coding: utf-8 -*-
"""模块 3 · 路径与隔离边界（SKILL-01 的前置约定）。

**这个文件存在的唯一理由：把"草稿"和"能跑的东西"在磁盘上彻底分开。**

平台里有两个目录会被 agent 自动发现并加载：

    <repo>/skills/<id>/SKILL.md        ← capabilities.discover_skills() 扫这里
    <data_dir>/skills/<id>/SKILL.md    ← skills_local.list_skills() 扫这里

所以草稿**绝对不能**放进这两个目录里的任何一个 —— 放进去就等于"未审核的 Skill
已经被 Agent 调用了"。本模块把整个市场放在第三个目录：

    <data_dir>/skill_market/
        drafts/<id>/SKILL.md           草稿（可编辑，Agent 永远看不到）
        submitted/<id>/SKILL.md        已提交待审（只读，Agent 也看不到）
        published/<id>/<version>/      已发布的历史版本（不可变）
        archive/<id>/<version>/        停用/回滚后归档
        audit/<yyyy-mm>.jsonl          追加式审计日志（只增不改不删）
        fixtures/<id>.json             匿名 fixture
        sessions/<sid>.json            会话 → plugin 集合的隔离记录

只有 `lifecycle.publish()` 会把 SKILL.md（+ 人工写好的 plugin.py）**拷贝**进
`skills_local.skills_dir()`，那一步之后它才可能被 Agent 加载。这条单向箭头就是
"默认不安装"的物理保证，不靠约定、不靠自觉。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from edu_agent.config import load_settings

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,47}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


def market_root() -> Path:
    """市场根目录。

    取值顺序：`EDU_SKILL_MARKET_DIR`（测试/多实例隔离用）→ `SKILL_STUDIO_DIR`
    （doc 34 §7.1 的配置名，可把市场整体指到 `data/skill_studio`）→ `<data_dir>/skill_market`（默认）。

    默认**不动**：历史数据都在 `<data_dir>/skill_market`，改默认值等于让已有技能凭空消失。
    """
    for env in ("EDU_SKILL_MARKET_DIR", "SKILL_STUDIO_DIR"):
        v = (os.getenv(env) or "").strip()
        if v:
            p = Path(v)
            # 相对路径按 data 目录解析（doc 里写的就是 data/skill_studio 这种相对形式）
            if not p.is_absolute():
                p = Path(load_settings().data_dir).parent / p
            return p
    return Path(load_settings().data_dir) / "skill_market"


def drafts_dir() -> Path:
    return market_root() / "drafts"


def submitted_dir() -> Path:
    return market_root() / "submitted"


def published_dir() -> Path:
    return market_root() / "published"


def archive_dir() -> Path:
    return market_root() / "archive"


def audit_dir() -> Path:
    return market_root() / "audit"


def fixtures_dir() -> Path:
    return market_root() / "fixtures"


def sessions_dir() -> Path:
    """会话级 plugin 集合的落盘位置（进程重启后隔离关系不丢）。"""
    return market_root() / "sessions"


# ---------------------------------------------------------------- skill-create
def runs_dir() -> Path:
    """运行痕迹（run trace）：**只追加**，skill-create 判定"跑通"的唯一原始证据。"""
    return market_root() / "runs"


def candidates_dir() -> Path:
    """候选工作流（可更新：计数/评分/状态）。"""
    return market_root() / "candidates"


def rejected_file() -> Path:
    """被否决的候选（冷却名单，只追加）。"""
    return candidates_dir() / "_rejected.jsonl"


def config_file() -> Path:
    """skill-create 的阈值配置（可改，不用改代码）。"""
    return market_root() / "skill_create.json"


def runtime_skills_dir() -> Path:
    """**唯一**会被 Agent 自动发现的技能目录。

    默认沿用 `skills_local.skills_dir()`（= `<data_dir>/skills`，agent 自建技能的老约定），
    可用 `EDU_SKILL_RUNTIME_DIR` 整体改向。

    ⚠️ 一个容易踩的坑（实测）：平台的 `capabilities.discover_skills()` 扫的是
    **`<repo>/skills`**，而 `skills_local.list_skills()` 扫的是 `<data_dir>/skills`。
    也就是说：发布到默认目录的技能，`skills_local` 看得到、Skill 市场（capabilities）看不到，
    于是 `capabilities.run_skill()` 也调不动它。要让平台直接能调，就把运行时目录指到
    `<repo>/skills`：

        set EDU_SKILL_RUNTIME_DIR=D:\\...\\edu-agent\\skills

    `runtime_store_note()` 会把当前实际落点与"谁能看到"一并报出来，便于排查。
    """
    env = (os.getenv("EDU_SKILL_RUNTIME_DIR") or "").strip()
    if env:
        return Path(env)
    from edu_agent import skills_local

    return skills_local.skills_dir()


def runtime_store_note() -> dict:
    """当前运行时目录 + 它会被哪套扫描器看到（运维排查用）。"""
    d = runtime_skills_dir()
    repo_skills = Path(__file__).resolve().parents[3] / "skills"
    try:
        same = d.resolve() == repo_skills.resolve()
    except OSError:
        same = False
    return {
        "runtime_dir": str(d),
        "visible_to_capabilities": bool(same),
        "capabilities_scans": str(repo_skills),
        "skills_local_scans": str(d),
        "hint": ("运行时目录 = <repo>/skills：平台 Skill 市场与 capabilities.run_skill() 都能看到它"
                 if same else
                 "运行时目录不是 <repo>/skills：skills_local 能看到，但平台 Skill 市场看不到；"
                 "想让它进市场/被 run_skill() 调用，设 EDU_SKILL_RUNTIME_DIR=<repo>/skills"),
    }


def runtime_plugin_path(skill_id: str) -> Path:
    """发布后 Agent 侧的实际路径（SKILL.md 所在目录）。"""
    return runtime_skills_dir() / skill_id


def ensure_dirs() -> None:
    for d in (drafts_dir(), submitted_dir(), published_dir(), archive_dir(),
              audit_dir(), fixtures_dir(), sessions_dir(),
              runs_dir(), candidates_dir()):
        d.mkdir(parents=True, exist_ok=True)


def valid_id(skill_id: str) -> bool:
    return bool(_ID_RE.match(str(skill_id or "")))


def valid_version(version: str) -> bool:
    return bool(_VERSION_RE.match(str(version or "")))


def is_inside_runtime(skill_id: str) -> bool:
    """该 id 当前是否已经躺在"能被 Agent 发现"的目录里（安装状态查询用）。"""
    d = runtime_plugin_path(skill_id)
    try:
        return (d / "SKILL.md").exists()
    except OSError:
        return False


def assert_not_runtime_dir(path: Path) -> None:
    """防御性断言：市场自己的写操作不允许落到运行时技能目录里。

    有人（或未来的重构）误把市场目录配成 skills/ 时，这里直接炸掉，
    而不是安静地把未审核草稿变成可加载技能。
    """
    try:
        p = Path(path).resolve()
    except OSError:
        return
    for guarded in (runtime_skills_dir(), Path("skills")):
        try:
            g = guarded.resolve()
        except OSError:
            continue
        if p == g or g in p.parents:
            raise ValueError(
                "模块3 的市场目录不能位于运行时技能目录内（草稿会被 Agent 直接加载）：%s" % p)
