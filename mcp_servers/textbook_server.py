# -*- coding: utf-8 -*-
"""MCP 教材服务（edu-textbook）—— 把平台**已经连通**的教材库暴露成 MCP 工具。

为什么要有它：技能不该自己去读文件、解析格式。教材库的读取逻辑抽在
`edu_agent/textbook_lib.py`（与 `/api/textbook*` 端点**同一份实现**），
本服务只是把它包成 MCP 工具，供任意技能/Agent 调用。

    .venv\\Scripts\\python.exe mcp_servers/textbook_server.py     # stdio transport

工具（都在 mcp_tools 的白名单里，未登记的工具调不动）：

    textbook_info()                       内置教材文件信息
    textbook_chapters()                   全部章/节结构
    textbook_chapter(chapter_no, book_id) 某一章（含小节清单）
    textbook_mindmap(chapter_no, book_id) 某一章的 Markdown 思维导图
    textbook_stats()                      章数 / 页数 / 片段数

**只读**：不写任何文件、不改向量库、不解析 PDF 内容（只用 PDF 数页数）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

# 让本文件既能被 MCP 客户端以子进程方式启动，也能被直接 import 做测试
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from edu_agent import textbook_lib  # noqa: E402

mcp = FastMCP("edu-textbook")


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False)


@mcp.tool()
def textbook_info() -> str:
    """内置教材文件信息（文件名 / 页码偏移 / 是否存在）。"""
    return _dump(textbook_lib.info())


@mcp.tool()
def textbook_chapters() -> str:
    """列出教材库全部章节（每章含小节清单：id/no/name/page）。"""
    return _dump(textbook_lib.chapters())


@mcp.tool()
def textbook_chapter(chapter_no: int = 0, book_id: str = "") -> str:
    """取指定章节（chapter_no 为章节序号，如 2 表示第二章；book_id 可留空）。

    返回：{ok, book, chapter, title, sections:[…], section_count, note}
    """
    return _dump(textbook_lib.chapter(int(chapter_no or 0), book_id))


@mcp.tool()
def textbook_mindmap(chapter_no: int = 0, book_id: str = "") -> str:
    """把指定章节渲染成 Markdown 思维导图（含「- 章节思维导图」根节点）。"""
    return _dump(textbook_lib.mindmap_markdown(int(chapter_no or 0), book_id))


@mcp.tool()
def textbook_stats() -> str:
    """内置教材统计：章数 / 页数 / 向量片段数。"""
    return _dump(textbook_lib.stats())


if __name__ == "__main__":
    # stdio transport 是默认；显式写明便于阅读
    mcp.run(transport="stdio")
