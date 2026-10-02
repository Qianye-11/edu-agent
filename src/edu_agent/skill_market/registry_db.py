# -*- coding: utf-8 -*-
"""技能注册表（SQLite）—— 单用户调试与多教师正式**共用同一套表结构**。

## 为什么要有这张表

现在的状态存在 `<market>/state.json`（JSON 文件）里，够单用户用，但多教师一定需要
「谁创建的、谁能看见、谁装了」这三件事的**可查询**存储。所以加一张表，而**不是**改文件结构：

* **表结构里 `skill_id` / `status` / `create_teacher_id` / `file_path` 四个字段必须保留**，
  即使单用户模式下 `create_teacher_id` 用不上（约束：字段不删，业务可以先不用）。
* `state.json` **继续写**（作为生命周期镜像）—— 现有代码一行不用改，两条记录同时更新，
  谁也不会因为这次改动而坏掉。

## 表结构

    skills                                 技能注册表（一行一个技能）
      skill_id           TEXT PK           技能 id（= 目录名）
      status             TEXT              draft | published | ...（**必留字段**）
      create_teacher_id  TEXT              创建者（**必留字段**；单用户模式写占位）
      file_path          TEXT              技能落盘路径（**必留字段**）
      title/description/version/area/installed/verify_state/published_at/created_at/updated_at/extra

    skill_installs                         教师安装关系（多用户模式用；单用户模式不使用但表先建）
      teacher_id + skill_id (PK)           installed_at / source

## 与"安装"的边界（约束 2）

`publish` **只做**：把技能完整复制到全局 `data/skills/<id>/` → `status='published'` → 更新本表。
它**不写** `skill_installs`，也**不碰**会话绑定（`isolation.bind`）——
安装关系是"教师把技能装到自己名下"，会话绑定是"某个会话这次加载哪些技能"，三件事各自独立。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

from edu_agent.config import load_settings
from edu_agent.skill_market import paths

# 五个**必须保留**的字段（铁律 1）；改表时不许删，只许加
REQUIRED_COLUMNS: tuple[str, ...] = ("skill_id", "status", "create_teacher_id",
                                     "manifest", "file_path")

STATUSES = ("draft", "submitted", "approved", "published", "disabled", "rolled_back", "rejected")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS skills (
  skill_id           TEXT PRIMARY KEY,
  status             TEXT NOT NULL DEFAULT 'draft',
  create_teacher_id  TEXT NOT NULL DEFAULT '',
  manifest           TEXT NOT NULL DEFAULT '{}',
  file_path          TEXT NOT NULL DEFAULT '',
  title              TEXT NOT NULL DEFAULT '',
  description        TEXT NOT NULL DEFAULT '',
  version            TEXT NOT NULL DEFAULT '0.1.0',
  area               TEXT NOT NULL DEFAULT 'draft',
  installed          INTEGER NOT NULL DEFAULT 0,
  verify_state       TEXT NOT NULL DEFAULT '',
  published_at       REAL NOT NULL DEFAULT 0,
  created_at         REAL NOT NULL DEFAULT 0,
  updated_at         REAL NOT NULL DEFAULT 0,
  extra              TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_skills_status  ON skills(status);
CREATE INDEX IF NOT EXISTS idx_skills_teacher ON skills(create_teacher_id, status);

-- 教师安装关系：多用户模式用；单用户模式不使用，但表结构先留着（铁律 1/业务分支）
CREATE TABLE IF NOT EXISTS skill_installs (
  teacher_id   TEXT NOT NULL,
  skill_id     TEXT NOT NULL,
  installed_at REAL NOT NULL DEFAULT 0,
  source       TEXT NOT NULL DEFAULT 'self',
  PRIMARY KEY (teacher_id, skill_id)
);
"""

# 老库补列（幂等）：表结构只增不减，绝不 DROP
_ADD_COLUMNS: tuple[tuple[str, str], ...] = (
    ("manifest", "TEXT NOT NULL DEFAULT '{}'"),
    ("title", "TEXT NOT NULL DEFAULT ''"),
    ("description", "TEXT NOT NULL DEFAULT ''"),
    ("version", "TEXT NOT NULL DEFAULT '0.1.0'"),
    ("area", "TEXT NOT NULL DEFAULT 'draft'"),
    ("installed", "INTEGER NOT NULL DEFAULT 0"),
    ("verify_state", "TEXT NOT NULL DEFAULT ''"),
    ("published_at", "REAL NOT NULL DEFAULT 0"),
    ("created_at", "REAL NOT NULL DEFAULT 0"),
    ("updated_at", "REAL NOT NULL DEFAULT 0"),
    ("extra", "TEXT NOT NULL DEFAULT '{}'"),
)

_lock = threading.RLock()


def db_path() -> Path:
    env = (os.getenv("EDU_SKILL_REGISTRY_DB") or "").strip()
    if env:
        return Path(env)
    return Path(load_settings().data_dir) / "skill_registry.db"


def _conn() -> sqlite3.Connection:
    p = db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(p), check_same_thread=False, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=5000")
    c.executescript(_SCHEMA)
    # 老库补列：表结构只增不减（**绝不 DROP / 绝不删字段**）
    have = {r["name"] for r in c.execute("PRAGMA table_info(skills)").fetchall()}
    for col, ddl in _ADD_COLUMNS:
        if col not in have:
            try:
                c.execute("ALTER TABLE skills ADD COLUMN %s %s" % (col, ddl))
            except sqlite3.OperationalError:
                pass
    c.commit()
    return c


def _exec(sql: str, params: tuple = ()) -> None:
    with _lock:
        c = _conn()
        try:
            c.execute(sql, params)
            c.commit()
        finally:
            c.close()


def _query(sql: str, params: tuple = ()) -> list[dict]:
    with _lock:
        c = _conn()
        try:
            return [dict(r) for r in c.execute(sql, params).fetchall()]
        finally:
            c.close()


def columns() -> list[str]:
    """当前表的列（自检/测试用：确认四个必留字段还在）。"""
    return [r["name"] for r in _query("PRAGMA table_info(skills)")]


def ensure_schema() -> dict:
    """建表（幂等）并返回结构快照。"""
    cols = columns()
    missing = [c for c in REQUIRED_COLUMNS if c not in cols]
    return {"ok": not missing, "db": str(db_path()), "columns": cols,
            "required": list(REQUIRED_COLUMNS), "missing_required": missing,
            "tables": [r["name"] for r in _query(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]}


# ---------------------------------------------------------------- 写入
def upsert(skill_id: str, **fields) -> dict:
    """插入或更新一行（只更新传进来的字段）。`skill_id` 必填。"""
    sid = str(skill_id or "").strip()
    if not sid:
        return {"ok": False, "error": "skill_id 不能为空"}
    allowed = {"status", "create_teacher_id", "manifest", "file_path", "title",
               "description", "version", "area", "installed", "verify_state",
               "published_at", "extra"}
    now = time.time()
    row = {k: v for k, v in fields.items() if k in allowed and v is not None}
    for k in ("extra", "manifest"):
        if k in row and not isinstance(row[k], str):
            row[k] = json.dumps(row[k], ensure_ascii=False)
    if "installed" in row:
        row["installed"] = 1 if row["installed"] else 0
    existing = get(sid)
    if existing is None:
        cols = ["skill_id", "created_at", "updated_at"] + list(row.keys())
        vals = [sid, now, now] + list(row.values())
        _exec("INSERT INTO skills(%s) VALUES(%s)" % (",".join(cols), ",".join("?" * len(cols))),
              tuple(vals))
    elif row:
        sets = ", ".join("%s=?" % k for k in row) + ", updated_at=?"
        _exec("UPDATE skills SET %s WHERE skill_id=?" % sets,
              tuple(list(row.values()) + [now, sid]))
    return {"ok": True, "skill_id": sid, "created": existing is None, "row": get(sid)}


def sync_from_state(skill_id: str, state: dict, *, file_path: str = "") -> dict:
    """把 `<market>/state.json` 里的一行镜像进注册表（`store.write_state` 会调它）。

    这样**不需要改任何现有代码路径**，注册表自动跟上。

    `create_teacher_id` 只在状态里真的带 `teacher_id` 时写（创建时写一次），
    之后 publish/disable 带的是 `actor`（操作者）—— 不能用操作者覆盖创建者，
    否则"谁创建的"会在发布那一刻被改掉（这是个很容易埋进去的坑）。
    """
    st = dict(state or {})
    # file_path 的取值顺序：显式传入 → 状态里的 file_path → 状态里的 dir（老写法）
    fp = str(file_path or st.get("file_path") or st.get("dir") or "")
    if fp:
        try:
            fp = str(Path(fp).resolve())        # 存绝对路径，换 cwd 也找得到
        except OSError:
            pass
    fields = {
        "status": str(st.get("status") or "draft"),
        "file_path": fp,
        "version": str(st.get("version") or "0.1.0"),
        "area": str(st.get("area") or "draft"),
        "installed": bool(st.get("installed")),
        "verify_state": str(st.get("verify_state") or ""),
        "published_at": float(st.get("published_at") or 0) or None,
    }
    tid = str(st.get("teacher_id") or "").strip()
    if tid:
        fields["create_teacher_id"] = tid
    # manifest：从 SKILL.md 现场解析出来存一份（铁律 1 要求 manifest 字段常驻）。
    # 解析失败不影响状态同步（manifest 只是快照，SKILL.md 才是真源）。
    mf_json = manifest_json(fp)
    if mf_json:
        fields["manifest"] = mf_json
    return upsert(skill_id, **fields)


def manifest_json(file_path: str) -> str:
    """读 SKILL.md 的 front-matter → 一行 JSON（失败返回空串）。"""
    p = Path(str(file_path or ""))
    if p.is_dir():
        p = p / "SKILL.md"
    if not p.exists():
        return ""
    try:
        from edu_agent.skill_market import manifest as mf

        man, _ = mf.from_document(p.read_text(encoding="utf-8", errors="replace"))
        return json.dumps(man.to_dict(), ensure_ascii=False)
    except Exception:  # noqa: BLE001
        return ""


def set_status(skill_id: str, status: str, **fields) -> dict:
    """只改状态（+ 可选字段）。publish/disable/rollback 走这里。"""
    return upsert(skill_id, status=str(status), **fields)


# ---------------------------------------------------------------- 读取
def get(skill_id: str) -> dict | None:
    rows = _query("SELECT * FROM skills WHERE skill_id=?", (str(skill_id or ""),))
    return rows[0] if rows else None


def list_skills(status: str = "", *, teacher_id: str = "", area: str = "") -> list[dict]:
    """按状态/创建者/区列出（`status` 支持逗号分隔的多值，如 `draft,submitted`）。"""
    sql = "SELECT * FROM skills WHERE 1=1"
    params: list = []
    if status:
        vals = [s.strip() for s in str(status).split(",") if s.strip()]
        sql += " AND status IN (%s)" % ",".join("?" * len(vals))
        params += vals
    if teacher_id:
        sql += " AND create_teacher_id=?"
        params.append(str(teacher_id))
    if area:
        sql += " AND area=?"
        params.append(str(area))
    sql += " ORDER BY updated_at DESC"
    return _query(sql, tuple(params))


def published() -> list[dict]:
    """全部已发布技能（**单用户模式的会话加载就用这个**）。"""
    return list_skills("published")


def installed_for(teacher_id: str) -> list[str]:
    """某教师已安装的 skill_id 列表（**多用户模式用**；单用户模式不使用）。"""
    rows = _query("SELECT skill_id FROM skill_installs WHERE teacher_id=? ORDER BY installed_at DESC",
                  (str(teacher_id or ""),))
    return [r["skill_id"] for r in rows]


def install(teacher_id: str, skill_id: str, *, source: str = "self") -> dict:
    """登记"某教师装了某技能"（多用户模式用；**publish 不会调它**）。"""
    if not str(teacher_id or "").strip() or not str(skill_id or "").strip():
        return {"ok": False, "error": "teacher_id 与 skill_id 都不能为空"}
    _exec("INSERT OR REPLACE INTO skill_installs(teacher_id,skill_id,installed_at,source)"
          " VALUES(?,?,?,?)",
          (str(teacher_id), str(skill_id), time.time(), str(source)))
    return {"ok": True, "teacher_id": teacher_id, "skill_id": skill_id}


def uninstall(teacher_id: str, skill_id: str) -> dict:
    _exec("DELETE FROM skill_installs WHERE teacher_id=? AND skill_id=?",
          (str(teacher_id), str(skill_id)))
    return {"ok": True}


# ---------------------------------------------------------------- 可见性（约束 4）
def visible_for(teacher_id: str, *, single_user: bool) -> list[dict]:
    """市场可见性分支。

    * 单用户调试：**全部草稿 + 已发布**（只有你一个人，不需要隔离）
    * 多教师正式：已发布（谁都能用）+ **本人创建的**草稿/在审
    """
    if single_user:
        return list_skills("draft,submitted,approved,published,disabled,rolled_back")
    tid = str(teacher_id or "").strip()
    rows = list_skills("published")
    if tid:
        mine = [r for r in list_skills("draft,submitted,approved,rejected")
                if str(r.get("create_teacher_id") or "") == tid]
        rows += mine
    return rows


# ---------------------------------------------------------------- 迁移与自检
def migrate_from_state_file() -> dict:
    """把已有的 `<market>/state.json` 一次性搬进注册表（幂等）。"""
    from edu_agent.skill_market import store

    st = store.read_state()
    n = 0
    for sid, row in (st or {}).items():
        if not isinstance(row, dict):
            continue
        if get(sid) is None:
            sync_from_state(sid, row)
            n += 1
    return {"ok": True, "imported": n, "total": len(st or {})}


def migrate_from_dirs() -> dict:
    """扫市场四区，把还没有登记的技能补进表（file_path 指向其 SKILL.md）。"""
    from edu_agent.skill_market import store

    n = 0
    for area in ("published", "submitted", "draft", "archive"):
        for row in store.list_area(area):
            sid = row["name"]
            if get(sid) is None:
                upsert(sid, status=str(row.get("status") or "draft"),
                       file_path=str(Path(row.get("dir") or "") / "SKILL.md"),
                       title=str(row.get("title") or ""),
                       description=str(row.get("description") or ""),
                       version=str(row.get("version") or "0.1.0"),
                       area=area,
                       installed=bool(row.get("installed")))
                n += 1
    return {"ok": True, "imported": n}


def reconcile() -> dict:
    """对账：检查每行的 file_path 是否还在磁盘上，同步 `installed` 标志。

    **不改 status** —— 状态是生命周期事实（谁在什么时候发布的），
    磁盘文件缺失是运维事实。两者分开记，才不会出现"程序偷偷把 published 改成 disabled"。
    """
    stale: list[dict] = []
    fixed = 0
    for row in list_skills():
        sid = str(row.get("skill_id") or "")
        fp = str(row.get("file_path") or "")
        exists = bool(fp) and Path(fp).exists()
        if bool(row.get("installed")) != exists:
            upsert(sid, installed=exists)
            fixed += 1
        if not exists and str(row.get("status")) == "published":
            stale.append({"skill": sid, "status": row.get("status"),
                          "file_path": fp, "why": "状态是 published 但文件不在了"})
    return {"ok": True, "checked": len(list_skills()), "installed_flag_fixed": fixed,
            "stale_published": stale}


def stats() -> dict:
    rows = _query("SELECT status, COUNT(*) AS n FROM skills GROUP BY status")
    by_status = {r["status"]: r["n"] for r in rows}
    installs = _query("SELECT COUNT(*) AS n FROM skill_installs")[0]["n"]
    return {"ok": True, "db": str(db_path()), "total": sum(by_status.values()),
            "by_status": by_status, "installs": installs,
            "schema": ensure_schema()}
