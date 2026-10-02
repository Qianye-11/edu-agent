# -*- coding: utf-8 -*-
"""教材库读取 —— **单一来源**。

`web_server` 的 `/api/textbook*` 端点与 `mcp_servers/textbook_server.py`（MCP 教材服务）
都调这里，避免"两处各读一遍、日后各改一半"。这里**只做读取**：不解析 PDF 内容、
不生成任何东西（PDF 只用来数页数），也不碰向量库的写入。

数据来源（都是平台既有的产物）：

    content/structured_auto/*.md   整节教材 md（chapter/chapter_title/book + 【整节】行）
    content/toc_ranges.json        目录页码范围（scripts/build_toc_ranges.py 生成）
    chroma_db 的 edu_textbook      片段数（只读 count）
    <textbook_pdf>                 页数（只读 page_count）

约束 6 说的"已经连通的教材服务"就是这套 —— 思维导图 skill 通过 MCP 工具调它，
自己不实现任何教材解析。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from edu_agent.config import load_settings

ROOT = Path(__file__).resolve().parents[2]

_CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8,
           "九": 9, "十": 10, "十一": 11, "十二": 12}

_SECTION_RE = re.compile(r"^【整节】([^|\r\n]*)\|([^|]*)\|([^|]*)\|p?([0-9]+)?")
_CHAPTER_RE = re.compile(r"^chapter:\s*(.+)$", re.M)
_CHAPTER_TITLE_RE = re.compile(r"^chapter_title:\s*(.+)$", re.M)
_BOOK_RE = re.compile(r"^book:\s*(.+)$", re.M)


def auto_dir() -> Path:
    return ROOT / "content" / "structured_auto"


def toc_file() -> Path:
    return ROOT / "content" / "toc_ranges.json"


def toc_file_legacy() -> Path:
    return ROOT / "data" / "toc_ranges.json"


def _textbook_pdf() -> Path | None:
    try:
        p = load_settings().textbook_pdf
    except Exception:  # noqa: BLE001
        return None
    return p if p and Path(p).exists() else None


def info() -> dict:
    """内置教材文件信息（与 /api/textbook 同形状）。"""
    p = _textbook_pdf()
    return {"file": p.name if p else "",
            "offset": 6,                      # 印刷页码 → 物理页 的偏移（沿用平台既有口径）
            "exists": p is not None}


def toc() -> dict:
    """教材目录页码范围。文件缺失会抛 FileNotFoundError（调用方决定怎么报）。"""
    if not toc_file().exists() and toc_file_legacy().exists():
        return json.loads(toc_file_legacy().read_text(encoding="utf-8"))
    if not toc_file().exists():
        raise FileNotFoundError("toc 未生成：请跑 scripts/build_toc_ranges.py")
    return json.loads(toc_file().read_text(encoding="utf-8"))


def chapters() -> dict:
    """解析整节教材 md → 章/节结构（与 /api/textbook/chapters 同形状）。"""
    out: list[dict] = []
    d = auto_dir()
    if not d.exists():
        return {"ok": True, "chapters": out}
    for f in sorted(d.glob("*.md")):
        try:
            text = f.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        ch = _CHAPTER_RE.search(text)
        ct = _CHAPTER_TITLE_RE.search(text)
        bk = _BOOK_RE.search(text)
        sections = []
        for line in text.splitlines():
            m = _SECTION_RE.match(line)
            if m:
                sections.append({"id": m.group(1), "no": m.group(2),
                                 "name": m.group(3),
                                 "page": int(m.group(4)) if m.group(4) else None})
        out.append({"file": f.name,
                    "chapter": ch.group(1).strip() if ch else "",
                    "title": ct.group(1).strip() if ct else "",
                    "book": bk.group(1).strip() if bk else "",
                    "sections": sections})
    return {"ok": True, "chapters": out}


def stats() -> dict:
    """内置官方教材统计（章数/页数/片段数）。"""
    blocks = None
    pages = None
    try:
        from langchain_chroma import Chroma

        from edu_agent.config import get_embeddings

        db = Chroma(collection_name="edu_textbook",
                    embedding_function=get_embeddings(),
                    persist_directory=str(ROOT / "chroma_db"))
        blocks = db._collection.count()
    except Exception:  # noqa: BLE001
        blocks = None
    n_ch = len(list(auto_dir().glob("*.md"))) if auto_dir().exists() else 0
    p = _textbook_pdf()
    if p is not None:
        try:
            import pymupdf

            doc = pymupdf.open(str(p))
            pages = max(0, doc.page_count - info()["offset"])
            doc.close()
        except Exception:  # noqa: BLE001
            pages = None
    raw = ROOT / "content" / "raw"
    return {"book": "数学 · 必修第一册（人教A版2019课标版）",
            "chapters": n_ch, "pages": pages, "blocks": blocks,
            "source_pdf": str(raw) if raw.exists() else None}


# ---------------------------------------------------------------- 章节定位（技能用）
def chapter_no_of(chapter: str, index: int = 0) -> int:
    """「第二章」→ 2；认不出来就用它在列表里的位置。"""
    s = str(chapter or "")
    for cn, n in _CN_NUM.items():
        if s.endswith(cn + "章") or ("第" + cn + "章") in s:
            return n
    return index + 1


def pick_book(chapters_list: list[dict], book_id: str = "") -> tuple[str, bool]:
    """按 book_id 选书；匹配不到就用第一本，并如实返回 matched=False。"""
    books = [str(c.get("book") or "") for c in chapters_list]
    want = str(book_id or "").strip().lower()
    if want:
        for b in books:
            if want and (want in b.lower() or b.lower() in want):
                return b, True
    return (books[0] if books else ""), False


def chapter(chapter_no: int = 0, book_id: str = "") -> dict:
    """取某一章（含小节清单）。这是 MCP 教材服务 `textbook_chapter` 工具的实现。"""
    got = chapters()
    rows = list(got.get("chapters") or [])
    if not rows:
        return {"ok": False, "error": "教材库为空（content/structured_auto 下没有 md）",
                "chapters": []}
    book, matched = pick_book(rows, book_id)
    same = [c for c in rows if str(c.get("book") or "") == book] or rows
    same = [c for _, c in sorted(
        enumerate(same),
        key=lambda pair: chapter_no_of(str(pair[1].get("chapter") or ""), pair[0]))]
    try:
        no = int(chapter_no or 0)
    except (TypeError, ValueError):
        no = 0
    picked = None
    for i, c in enumerate(same):
        if chapter_no_of(str(c.get("chapter") or ""), i) == no:
            picked = c
            break
    note = ""
    if picked is None:
        picked = same[0] if same else {}
        note = "未找到第 %s 章，已回退到第一章" % (no or "?")
    return {"ok": True, "book": book, "book_matched": matched,
            "chapter": picked.get("chapter"), "title": picked.get("title"),
            "chapter_no": no, "sections": list(picked.get("sections") or []),
            "section_count": len(picked.get("sections") or []),
            "book_count": len({str(c.get("book") or "") for c in rows}),
            "chapter_count": len(same), "note": note, "source": "textbook:read"}


def mindmap_markdown(chapter_no: int = 0, book_id: str = "") -> dict:
    """把某一章渲染成 Markdown 思维导图（技能与 MCP 工具共用同一份渲染）。"""
    got = chapter(chapter_no, book_id)
    if not got.get("ok"):
        return {**got, "markdown": ""}
    lines = ["- 章节思维导图",
             "  - %s · %s %s" % (got.get("book") or "（未命名课本）",
                                 got.get("chapter") or "", got.get("title") or "")]
    for sec in got.get("sections") or []:
        name = str(sec.get("name") or sec.get("id") or "")
        no_ = str(sec.get("no") or "")
        page = sec.get("page")
        lines.append("    - %s %s%s" % (no_, name, "（p%s）" % page if page else ""))
    if not got.get("sections"):
        lines.append("    - （该章暂无小节数据）")
    return {**got, "markdown": "\n".join(lines) + "\n",
            "section_count": len(got.get("sections") or [])}
