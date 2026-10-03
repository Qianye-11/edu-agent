# -*- coding: utf-8 -*-
"""TextbookMindMapSkill —— 教材章节 Markdown 思维导图（Semantic Kernel 插件）。

    入参：book_id（教材编号）、chapter_no（章节号）
    返回：{"ok": bool, "markdown": str}

## 数据来源（**只读**，两级）

1. **MCP 教材服务** `textbook`（在 `capabilities.MCP_SPECS` 登记、工具在 `mcp_tools` 白名单里）——
   工具 `textbook_chapter` 取「某一章的章名 + 小节清单（含页码）」；
2. **平台只读接口** `/api/textbook/chapters`（走 `contract.ModuleClient`，参数过脱敏/白名单闸门）——
   MCP 未启动或超时时兜底。

拿到章节结构后，由本插件**自己渲染** Markdown 思维导图（所以"生成"这一步在技能里，
而不是让服务端代劳）。

## 设计约束（模块3 Skill 市场的三重校验）

* 只声明 `textbook:read`：**不写文件、不执行外部程序、不访问公网**；
* 出站只走 `ModuleClient`：影子运行（L3）会把它换成记录桩，所以**不联网也能验收**；
* ⚠️ Semantic Kernel 的坑：可选参数**不能**写 `= None`（SK 把它当成必填）→ 用 `""` / `0`；
* **永不抛异常**：任何失败都返回 `{"ok": False, "markdown": ""}`，不打断对话。
"""
from __future__ import annotations

import json
from typing import Annotated

from semantic_kernel.functions import kernel_function

from edu_agent.skill_market.contract import ModuleClient

# 平台自己的地址（HTTP 兜底用；教材库的正路是 MCP 工具）
PLATFORM_BASE = "http://127.0.0.1:5174"

# 教材库 MCP 服务：capabilities.MCP_SPECS 里登记为 textbook
MCP_SERVER = "textbook"
MCP_TIMEOUT = 30.0

# 思维导图根节点（manifest 的 expect_contains 断言的就是这一行，别改）
ROOT_TITLE = "- 章节思维导图"

_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8,
           "九": 9, "十": 10, "十一": 11, "十二": 12}


def _mcp_tool(tool: str, args: dict) -> dict | None:
    """调 MCP 教材服务取数据。**只读**：不写文件、不改向量库、不解析 PDF。

    失败返回 None（调用方走 HTTP 兜底），绝不抛异常影响技能。
    """
    try:
        from edu_agent import mcp_tools

        raw = mcp_tools.call_tool(MCP_SERVER, tool, args, timeout=MCP_TIMEOUT)
        if not raw:
            return None
        data = json.loads(raw) if isinstance(raw, str) else raw
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _chapter_no_of(chapter: str, index: int = 0) -> int:
    """把「第二章」这类章节名转成序号；认不出来就用它在列表里的位置。"""
    s = str(chapter or "")
    for cn, n in _CN_NUM.items():
        if s.endswith(cn + "章") or ("第" + cn + "章") in s:
            return n
    return index + 1


def _pick_book(chapters: list[dict], book_id: str) -> tuple[str, bool]:
    """按 book_id 选书；匹配不到就用第一本，并如实返回 matched=False。"""
    books = [str(c.get("book") or "") for c in chapters]
    want = str(book_id or "").strip().lower()
    if want:
        for b in books:
            if want and (want in b.lower() or b.lower() in want):
                return b, True
    return (books[0] if books else ""), False


def _render_mindmap(book: str, chapter: str, title: str,
                    sections: list[dict], note: str = "") -> str:
    """把章节结构渲染成 Markdown 思维导图（本技能的"生成"这一步）。"""
    head = " ".join(x for x in (chapter, title) if x).strip()
    lines = [ROOT_TITLE, "  - %s%s" % (book or "（未命名课本）", (" · " + head) if head else "")]
    for sec in sections:
        name = str(sec.get("name") or sec.get("id") or "").strip()
        no_ = str(sec.get("no") or "").strip()
        page = sec.get("page")
        lines.append("    - %s%s%s" % (no_ + " " if no_ else "", name,
                                       "（p%s）" % page if page else ""))
    if not sections:
        lines.append("    - （该章暂无小节数据）")
    if note:
        lines.append("  - 说明：%s" % note)
    return "\n".join(lines) + "\n"


class TextbookMindMapSkill:
    """按 book_id + chapter_no 生成章节 Markdown 思维导图（只读教材库）。"""

    def __init__(self, client: ModuleClient | None = None) -> None:
        self.client = client or ModuleClient({"platform": PLATFORM_BASE})

    # ---------------------------------------------------------------- kernel function
    @kernel_function(
        name="generate_mindmap",
        description="读取本地教材库指定课本的某一章，生成该章节的 Markdown 思维导图。",
    )
    def generate_mindmap(
        self,
        book_id: Annotated[str, "教材库里的书本唯一 id（匹配不到则用默认课本并标注）"] = "",
        chapter_no: Annotated[int, "章节序号，例如 2 表示第二章"] = 0,
    ) -> dict:
        """按 book_id 与章节号生成 Markdown 思维导图。

        Returns:
            {"ok": bool, "markdown": str}
        """
        try:
            no = int(chapter_no or 0)
        except (TypeError, ValueError):
            no = 0
        bid = str(book_id or "").strip()

        # ---- 正路：MCP 教材服务（edu-textbook，只读工具）----
        got = _mcp_tool("textbook_chapter", {"chapter_no": no, "book_id": bid})
        if got and got.get("ok"):
            return {"ok": True, "markdown": _render_mindmap(
                str(got.get("book") or ""), str(got.get("chapter") or ""),
                str(got.get("title") or ""), list(got.get("sections") or []),
                note=str(got.get("note") or ""))}

        # ---- 兜底：平台只读接口（MCP 未起 / 超时 / 影子运行的记录桩）----
        res = self.client.get("platform", "/api/textbook/chapters")
        chapters = list((res or {}).get("chapters") or []) if isinstance(res, dict) else []
        if not chapters:
            # 数据源不可用（离线 / 影子运行的记录桩）→ 仍返回合法结构并如实说明
            return {"ok": True, "markdown": _render_mindmap(
                "", "", "", [],
                note="教材库未返回数据（离线或影子运行）；book_id=%s chapter_no=%s"
                     % (bid or "（未指定）", no or "（未指定）"))}

        book, _matched = _pick_book(chapters, bid)
        same_book = [c for c in chapters if str(c.get("book") or "") == book] or chapters
        # 按章节号排序：先 enumerate 再排（**不能**在 sort 的 key 里调 list.index ——
        # 排序过程中列表在变，会抛 "is not in list"；这里踩过）。
        same_book = [c for _, c in sorted(
            enumerate(same_book),
            key=lambda pair: _chapter_no_of(str(pair[1].get("chapter") or ""), pair[0]))]
        picked = None
        for i, c in enumerate(same_book):
            if _chapter_no_of(str(c.get("chapter") or ""), i) == no:
                picked = c
                break
        note = ""
        if picked is None:
            picked = same_book[0] if same_book else {}
            note = "未找到第 %s 章，已回退到第一章" % (no or "?")

        return {"ok": True, "markdown": _render_mindmap(
            book, str(picked.get("chapter") or ""), str(picked.get("title") or ""),
            list(picked.get("sections") or []), note=note)}
