# -*- coding: utf-8 -*-
"""模块 3 · 市场存储：草稿/待审/已发布/归档四区的读写与搬运。

**复用而不重写**：运行时的"技能目录"仍然由 `skills_local.skills_dir()` 定义，
技能清单的读回仍然走 `skills_local.list_skills()`；本文件只管市场自己的四区，
以及"发布 = 把文件搬进运行时目录"这一个动作。

搬运规则（每一步都会写审计，见 lifecycle.py）：

    drafts/<id>/        草稿（可编辑；Agent 看不到）
    submitted/<id>/     已提交待审（冻结；Agent 看不到）
    published/<id>/<v>/ 历史版本（不可变，回滚的来源）
    archive/<id>/<v>-<原因>-<时间>/   停用/回滚/删除的留档（不物理删）

**发布是唯一的单向箭头**：`drafts|submitted → published → runtime_skills_dir()`。
"""
from __future__ import annotations

import json
import re
import shutil
import threading
import time
from pathlib import Path

from edu_agent.skill_market import manifest as mf
from edu_agent.skill_market import paths

# 草稿区允许出现的文件（白名单）：**不允许可执行脚本**，这是"草稿不可运行"的第二道锁
DRAFT_ALLOWED_SUFFIX = (".md", ".json", ".yaml", ".yml", ".txt", ".csv")
DRAFT_BANNED_SUFFIX = (".py", ".pyc", ".exe", ".cmd", ".bat", ".ps1", ".sh", ".dll", ".so", ".js")


def area_dir(area: str, skill_id: str = "") -> Path:
    base = {
        "draft": paths.drafts_dir(),
        "submitted": paths.submitted_dir(),
        "published": paths.published_dir(),
        "archive": paths.archive_dir(),
    }.get(area)
    if base is None:
        raise ValueError("未知区域：%s（只能是 draft/submitted/published/archive）" % area)
    return base / skill_id if skill_id else base


def _safe_join(base: Path, rel: str) -> Path:
    """相对路径落盘（禁止绝对路径与 ..，防目录穿越）。"""
    r = str(rel or "").replace("\\", "/").lstrip("/")
    if not r or ".." in r.split("/"):
        raise ValueError("非法相对路径：%r" % rel)
    p = (base / r).resolve()
    if base.resolve() not in p.parents and p != base.resolve():
        raise ValueError("路径逃出技能目录：%r" % rel)
    return p


# ---------------------------------------------------------------- 草稿
def write_draft(man: mf.SkillManifest, body: str,
                files: dict[str, str] | None = None, *,
                allow_code: bool = False) -> Path:
    """写/覆盖草稿（SKILL.md + 附属文件）。返回草稿目录。

    `allow_code=False`（默认，也是 SKILL-02 自动路径用的）时**拒绝任何可执行文件** ——
    自动生成的草稿里绝不会有能跑的东西。
    人工编辑接口（`/api/skill-market/draft/save`）传 `allow_code=True`，允许把自己写好的
    `plugin.py` 放进草稿，再一并提交审核（这正是"人工编辑后才能加载"的流程）。
    """
    paths.ensure_dirs()
    if not paths.valid_id(man.name):
        raise ValueError("非法技能 id：%r" % man.name)
    d = area_dir("draft", man.name)
    paths.assert_not_runtime_dir(d)
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(man.to_document(body), encoding="utf-8")
    for rel, text in (files or {}).items():
        suffix = Path(rel).suffix.lower()
        if suffix in DRAFT_BANNED_SUFFIX and not allow_code:
            raise ValueError(
                "草稿区不允许放可执行文件（%s）：SKILL-02 只产 SKILL.md 草稿，"
                "插件代码必须人工编写并显式带 allow_code 提交" % rel)
        if suffix not in DRAFT_ALLOWED_SUFFIX and suffix not in DRAFT_BANNED_SUFFIX:
            raise ValueError("草稿区只允许 %s，收到 %s" % (DRAFT_ALLOWED_SUFFIX, rel))
        p = _safe_join(d, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(text), encoding="utf-8")
    return d


def _version_dirs(skill_id: str) -> list[Path]:
    """published/<id>/<version>/ 里所有**含 SKILL.md 的版本目录**（新→旧）。"""
    base = paths.published_dir() / skill_id
    if not base.exists():
        return []
    dirs = [d for d in base.iterdir() if d.is_dir() and (d / "SKILL.md").exists()]
    return sorted(dirs, key=lambda p: (p / "SKILL.md").stat().st_mtime, reverse=True)


def _skill_dir_in(area: str, skill_id: str) -> Path | None:
    """某区里该技能的实际目录。

    注意 published 区是**两层**（`published/<id>/<version>/`），其余三区是一层；
    早先按一层读，导致"发布后市场里什么都查不到"（实测踩过）。
    """
    if area == "published":
        dirs = _version_dirs(skill_id)
        return dirs[0] if dirs else None
    d = area_dir(area, skill_id)
    return d if (d / "SKILL.md").exists() else None


def read_area(area: str, skill_id: str) -> tuple[mf.SkillManifest, str, Path] | None:
    d = _skill_dir_in(area, skill_id)
    if d is None:
        return None
    man, body = mf.from_document((d / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
    return man, body, d


def list_area(area: str) -> list[dict]:
    """列出某区的技能（只读 SKILL.md 的 front-matter，不执行任何东西）。"""
    base = area_dir(area)
    if not base.exists():
        return []
    out: list[dict] = []
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        d = _skill_dir_in(area, child.name)
        if d is None:
            continue
        man, body = mf.from_document((d / "SKILL.md").read_text(encoding="utf-8", errors="replace"))
        row = man.to_dict()
        row.update({"area": area, "dir": str(d),
                    "installed": paths.is_inside_runtime(man.name),
                    "body_chars": len(body), "mtime": (d / "SKILL.md").stat().st_mtime})
        out.append(row)
    return sorted(out, key=lambda r: r["mtime"], reverse=True)


def find_skill(skill_id: str) -> dict | None:
    """在四区里找这个技能（草稿优先，便于编辑；详情页用）。"""
    for area in ("draft", "submitted", "published", "archive"):
        got = read_area(area, skill_id)
        if got is not None:
            man, body, d = got
            return {"area": area, "dir": str(d), "manifest": man, "body": body}
    return None


def move_area(skill_id: str, src: str, dst: str, *, tag: str = "") -> Path:
    """整目录搬运（src 区 → dst 区）。同区冲突时自动加时间戳后缀留档。"""
    s = area_dir(src, skill_id)
    if not s.exists():
        raise FileNotFoundError("源不存在：%s" % s)
    d = area_dir(dst, skill_id)
    if d.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        d = d.with_name("%s-%s%s" % (d.name, tag or "old", "-" + stamp))
    d.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(s), str(d))
    return d


def archive_area(skill_id: str, area: str, *, reason: str = "") -> Path:
    """把某区目录归档到 archive/<id>/<version>-<reason>-<ts>/（不物理删）。"""
    src = area_dir(area, skill_id)
    got = read_area(area, skill_id)
    version = got[0].version if got else "unknown"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    name = "%s-%s-%s" % (skill_id, version, (reason or "archived"))
    dest = paths.archive_dir() / skill_id / ("%s-%s" % (name, stamp))
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.exists():
        shutil.move(str(src), str(dest))
    return dest


# ---------------------------------------------------------------- 版本区
def publish_area(skill_id: str, version: str) -> Path:
    return paths.published_dir() / skill_id / version


def write_version(man: mf.SkillManifest, body: str, src_dir: Path) -> Path:
    """把一份技能**整目录**存成不可变版本（published/<id>/<version>/）。

    同一版本号重复写入时（例如实验重跑、回滚后又发同一版），加时间戳后缀留档，
    **不覆盖**旧版本目录。
    """
    dest = publish_area(man.name, man.version)
    if dest.exists():
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = dest.with_name("%s-%s" % (man.version, stamp))
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "SKILL.md").write_text(man.to_document(body), encoding="utf-8")
    for p in Path(src_dir).rglob("*"):
        if not p.is_file() or p.name == "SKILL.md":
            continue
        rel = p.relative_to(src_dir)
        if rel.parts and rel.parts[0] == "_archive":
            continue
        tgt = dest / rel
        tgt.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, tgt)
    return dest


def list_versions(skill_id: str) -> list[dict]:
    base = paths.published_dir() / skill_id
    if not base.exists():
        return []
    out = []
    for d in sorted(base.iterdir()):
        p = d / "SKILL.md"
        if not d.is_dir() or not p.exists():
            continue
        man, _ = mf.from_document(p.read_text(encoding="utf-8", errors="replace"))
        out.append({"version": d.name, "manifest_version": man.version,
                    "dir": str(d), "mtime": p.stat().st_mtime})
    return sorted(out, key=lambda r: r["mtime"], reverse=True)


def latest_version(skill_id: str, *, exclude: str = "") -> dict | None:
    for v in list_versions(skill_id):
        if v["version"] != exclude:
            return v
    return None


# ---------------------------------------------------------------- 安装 / 卸载
RUNTIME_COPY_SKIP = ("SKILL.md",)          # SKILL.md 单独写（要带最新 front-matter）
RUNTIME_BANNED = ("fixture",)              # fixture 不进运行时目录（省得被当技能内容读走）


def install_to_runtime(man: mf.SkillManifest, body: str, src_dir: Path) -> Path:
    """发布/回滚的**唯一**落地点：把技能拷进运行时技能目录（Agent 从此可发现）。

    只拷 SKILL.md + 声明的 entry/plugin + scripts/ 下的文件；fixture 与 tests 留在市场区。
    """
    dest = paths.runtime_plugin_path(man.name)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "SKILL.md").write_text(man.to_document(body), encoding="utf-8")
    skip_names = {"SKILL.md", Path(man.fixture).name if man.fixture else ""}
    for p in sorted(Path(src_dir).rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(src_dir)
        top = rel.parts[0] if rel.parts else ""
        if top in RUNTIME_BANNED or p.name in skip_names:
            continue
        if p.suffix.lower() in (".pyc",):
            continue
        tgt = dest / rel
        tgt.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, tgt)
    return dest


def uninstall_from_runtime(skill_id: str, *, reason: str = "") -> Path | None:
    """从运行时目录撤下（停用/回滚时用），目录进 archive，不物理删。"""
    src = paths.runtime_plugin_path(skill_id)
    if not src.exists():
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = paths.archive_dir() / skill_id / ("runtime-%s-%s" % (reason or "removed", stamp))
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    return dest


# ---------------------------------------------------------------- fixture
def write_fixture(skill_id: str, payload: dict) -> Path:
    """写匿名 fixture（市场区 fixtures/<id>.json）。"""
    paths.ensure_dirs()
    p = paths.fixtures_dir() / ("%s.json" % skill_id)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def read_fixture(skill_id: str) -> dict | None:
    p = paths.fixtures_dir() / ("%s.json" % skill_id)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except ValueError:
        return None


def fixture_text(skill_id: str) -> str:
    p = paths.fixtures_dir() / ("%s.json" % skill_id)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- 索引（市场列表）
_ID_OK = re.compile(r"^[a-z0-9][a-z0-9_-]{1,47}$")


def market_index(keyword: str = "") -> list[dict]:
    """市场索引：四区合并 + 安装状态 + 版本数（**数据面**，页面由他人实现）。"""
    rows: dict[str, dict] = {}
    for area in ("published", "submitted", "draft", "archive"):
        for row in list_area(area):
            cur = rows.get(row["name"])
            # 展示优先级：published > submitted > draft > archive
            if cur is None or _AREA_RANK[area] < _AREA_RANK[cur["area"]]:
                rows[row["name"]] = row
    out = list(rows.values())
    for r in out:
        # 权威状态覆盖 front-matter 里的镜像：published/<v>/SKILL.md 是**不可变快照**，
        # 它里面永远写着 published，但技能可能早已被停用/回滚（列表页显示错状态会误导人）。
        r["status"] = effective_status(r["name"]) or r["status"]
        r["versions"] = len(list_versions(r["name"]))
        r["fixture"] = bool(read_fixture(r["name"]))
        r["installed"] = paths.is_inside_runtime(r["name"])
        r["loadable"], r["loadable_reason"] = is_loadable(r["name"])
    if keyword:
        k = keyword.strip().lower()
        out = [r for r in out
               if k in r["name"].lower() or k in (r.get("title") or "").lower()
               or k in (r.get("description") or "").lower()
               or k in " ".join(r.get("tags") or []).lower()]
    return sorted(out, key=lambda r: (_AREA_RANK[r["area"]], -r["mtime"]))


_AREA_RANK = {"published": 0, "submitted": 1, "draft": 2, "archive": 3}


# ---------------------------------------------------------------- 生命周期状态
# front-matter 里的 status 是**给人和市场看的镜像**；这里的 state.json 才是权威状态
# （版本目录要不可变，所以"停用/回滚"这类状态变更不能去改历史版本的 SKILL.md）。
def state_file() -> Path:
    return paths.market_root() / "state.json"


def read_state() -> dict:
    p = state_file()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def write_state(skill_id: str, **fields) -> dict:
    """更新某技能的生命周期状态（只动 state.json，不动历史版本）。

    同时**镜像进注册表（SQLite）**：`state.json` 继续是生命周期镜像，
    `skill_registry.db` 是"谁创建/什么状态/文件在哪/谁装了"的可查询存储。
    镜像失败不影响主流程（注册表是新增能力，不是新的依赖）。
    """
    paths.ensure_dirs()
    with _STATE_LOCK:
        st = read_state()
        row = dict(st.get(skill_id) or {})
        row.update({k: v for k, v in fields.items() if v is not None})
        row["updated"] = time.time()
        st[skill_id] = row
        tmp = state_file().with_suffix(".json.tmp")
        tmp.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(state_file())
    try:
        from edu_agent.skill_market import registry_db

        registry_db.sync_from_state(skill_id, row)
    except Exception:  # noqa: BLE001  注册表镜像失败不能拖垮状态机
        pass
    return row


def get_state(skill_id: str) -> dict:
    return dict(read_state().get(skill_id) or {})


def effective_status(skill_id: str) -> str:
    """权威状态：state.json 优先，缺失时回落到磁盘所在区。"""
    st = get_state(skill_id)
    if st.get("status"):
        return str(st["status"])
    found = find_skill(skill_id)
    return str(found["manifest"].status) if found else ""


def is_loadable(skill_id: str) -> tuple[bool, str]:
    """**Agent 现在能不能加载它**——这是模块 3 最该被问的那个问题。

    判据是"**运行时目录里确实装着一个版本**"，而不是"工作状态恰好是 published"。
    这个区别很关键：从已发布技能派生新草稿继续编辑时，工作状态变回 draft，
    但线上那个版本必须照常服务（否则"改个草稿把线上打挂"）。

    三个条件：已安装 ∧ 运行时文件存在 ∧ 能力开关没被关。
    """
    st = get_state(skill_id)
    status = effective_status(skill_id)
    installed = st.get("installed")
    if installed is None:                     # 老数据没有这个字段 → 回落到状态推断
        installed = (status == "published")
    if not installed:
        return False, ("未安装到运行时技能目录（当前状态 %s）：只有发布安装过的技能才能被 "
                       "Agent 加载" % (status or "未知"))
    if not paths.is_inside_runtime(skill_id):
        return False, "状态显示已安装，但运行时目录里没有它的文件（可能被手工删了）"
    try:
        from edu_agent import capabilities

        # 只有"平台确实认识这个技能、且用户把它关了"才算被闸门拦下。
        # 平台还没发现它（spec 为 None，例如运行时目录不是 <repo>/skills）时不能误判成"被关闭"。
        spec = capabilities.skill_spec(skill_id)
        if spec is not None and not capabilities.is_enabled("skill", skill_id):
            return False, "技能在「MCP 服务」里被关闭（capability 闸门）"
    except Exception:  # noqa: BLE001
        pass
    return True, "可加载"


_STATE_LOCK = threading.Lock()
