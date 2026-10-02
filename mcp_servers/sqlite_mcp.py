# -*- coding: utf-8 -*-
"""sqlite MCP server：本地 SQLite 只读查询工具（stdio，自写，贴合项目风格）。

提供（全部只读，白名单收口）：
  list_tables   列出数据库中的表
  get_schema    查看指定表结构（列名/类型）
  query         执行只读 SELECT，返回结果（防御：非 SELECT 一律拒绝）

数据库文件默认 data/sqlite/edu.db（首次访问自动创建空库）。
启动（stdio，由 client 拉起）：
  .venv\\Scripts\\python.exe mcp_servers/sqlite_mcp.py
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("edu-sqlite")

_DB = Path(__file__).resolve().parents[1] / "data" / "sqlite" / "edu.db"


def _conn() -> sqlite3.Connection:
    _DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(_DB))
    c.row_factory = sqlite3.Row
    return c


@mcp.tool()
def list_tables() -> str:
    """列出当前 SQLite 数据库中的所有表。"""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        names = [r["name"] for r in rows]
        return "tables: " + (", ".join(names) if names else "(空库)")
    finally:
        conn.close()


@mcp.tool()
def get_schema(table: str) -> str:
    """查看指定表的列结构。参数 table: 表名。"""
    conn = _conn()
    try:
        rows = conn.execute(f"PRAGMA table_info({table!r})").fetchall()
        if not rows:
            return f"ERR: 表不存在: {table}"
        cols = [f"{r['name']}:{r['type']}" for r in rows]
        return "columns: " + ", ".join(cols)
    except Exception as e:
        return f"ERR: {e}"
    finally:
        conn.close()


@mcp.tool()
def query(sql: str) -> str:
    """执行只读 SQL 查询（仅允许 SELECT 开头），返回结果。参数 sql: SELECT 语句。"""
    stmt = sql.strip()
    if not stmt.lower().startswith("select"):
        return "ERR: 仅允许只读 SELECT 查询"
    conn = _conn()
    try:
        rows = conn.execute(stmt).fetchall()
        if not rows:
            return "(无结果)"
        cols = rows[0].keys()
        header = " | ".join(cols)
        lines = [header]
        for r in rows[:200]:
            lines.append(" | ".join(str(r[c]) for c in cols))
        if len(rows) > 200:
            lines.append(f"... (共 {len(rows)} 行，仅显示前 200)")
        return "\n".join(lines)
    except Exception as e:
        return f"ERR: {e}"
    finally:
        conn.close()


if __name__ == "__main__":
    mcp.run()
