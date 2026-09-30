"""S5 实现：content/structured/*.md -> Document(metadata) -> Chroma 持久化。

产物格式契约见 content/README.md（v2）：
文件级 YAML front-matter + 逐条 `【概念|例题|习题】chunk_id|section|heading|page|anchor`。
结构化产物一段即一条 chunk，不做二次切分。

用法：
    python -m edu_agent.ingest            # 默认读 config 的 content_dir -> chroma_db_dir
"""
from __future__ import annotations

import re
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from edu_agent.config import load_settings  # noqa: E402

# kind 标记 -> metadata.kind
# 概念/例题/习题：人工精修细卡；整节：PDF 自动切节粗卡（content/structured_auto/）
KIND_MARKERS: dict[str, str] = {
    "概念": "concept",
    "例题": "example",
    "习题": "exercise",
    "整节": "section",
}

# 头行：^【概念|例题|习题|整节】chunk_id|section|heading|page|anchor
_HEADER_RE = re.compile(
    r"^【(?P<kind>概念|例题|习题|整节)】\s*(?P<cid>[^|]+)"
    r"\s*\|\s*(?P<section>[^|]*)"
    r"\s*\|\s*(?P<heading>[^|]*)"
    r"\s*\|\s*(?P<page>[^|]*)"
    r"\s*\|\s*(?P<anchor>[^|]*)\s*$"
)


@dataclass
class Chunk:
    chunk_id: str
    kind: str
    text: str
    subject: str
    book: str
    version: str
    chapter: str
    section: str = ""
    heading: str = ""
    page: int | None = None
    anchor: str = ""
    parent_id: str = ""
    chunk_index: int = 1
    chunk_count: int = 1
    is_sidebar: bool = False
    content_role: str = "explanation"


def infer_content_role(text: str, heading: str = "", kind: str = "section",
                       is_sidebar: bool = False) -> str:
    """推断知识块的教学角色，供召回意图加权与健康审计使用。"""
    if kind in {"concept", "example", "exercise"}:
        return kind
    title = str(heading or "").strip()
    body = str(text or "").strip()
    combined = title + "\n" + body
    if re.search(r"复习参考题|章末习题|课后习题|练习题|^\s*(?:练习|习题)\b", combined):
        return "exercise"
    if re.search(r"(?:^|[\n。；])\s*(?:例题?|典例)\s*\d*", combined):
        return "example"
    if re.search(r"什么叫|称为|叫做|定义为|概念|一般地[^。]{0,80}(?:称为|叫做)", combined):
        return "concept"
    if is_sidebar:
        return "sidebar"
    return "explanation"


def _parse_front_matter(block: str) -> dict[str, str]:
    """解析 YAML 子集 front-matter（key: value 行）。"""
    fm: dict[str, str] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, val = line.partition(":")
        fm[key.strip()] = val.strip()
    return fm


def _to_page(raw: str) -> int | None:
    s = raw.strip().lower().lstrip("p页:")
    if s.isdigit():
        return int(s)
    return None


def parse_file(path: Path) -> list[Chunk]:
    """解析单个结构化 md 文件 -> Chunk 列表（保持文件内顺序）。"""
    raw = Path(path).read_text(encoding="utf-8")
    # 拆 front-matter 与正文
    lines = raw.splitlines()
    fm: dict[str, str] = {}
    if lines and lines[0].strip() == "---":
        end = 1
        while end < len(lines) and lines[end].strip() != "---":
            end += 1
        fm = _parse_front_matter("\n".join(lines[1:end]))
        body = lines[end + 1:]
    else:
        body = lines

    chunks: list[Chunk] = []
    current: Chunk | None = None
    for line in body:
        m = _HEADER_RE.match(line.strip())
        if m:
            if current is not None:
                chunks.append(current)
            current = Chunk(
                chunk_id=m.group("cid").strip(),
                kind=KIND_MARKERS[m.group("kind")],
                text="",
                subject=fm.get("subject", ""),
                book=fm.get("book", ""),
                version=fm.get("version", ""),
                chapter=fm.get("chapter", ""),
                section=m.group("section").strip(),
                heading=m.group("heading").strip(),
                page=_to_page(m.group("page")),
                anchor=m.group("anchor").strip(),
            )
        else:
            if current is not None:
                current.text += line + "\n"
    if current is not None:
        chunks.append(current)
    for c in chunks:
        c.text = c.text.strip()
    return chunks


def _page_image_flags(pdf: Path | None) -> dict[int, bool]:
    """返回物理页是否含位图；PDF 不可用时安静降级。"""
    if not pdf or not Path(pdf).exists():
        return {}
    try:
        import pymupdf

        doc = pymupdf.open(str(pdf))
        try:
            return {i + 1: bool(doc[i].get_images(full=True)) for i in range(doc.page_count)}
        finally:
            doc.close()
    except Exception:
        return {}


def to_documents(chunks: list[Chunk], page_images: dict[int, bool] | None = None,
                 page_offset: int = 6):
    from langchain_core.documents import Document

    from edu_agent import figdetect
    import hashlib

    docs = []
    for c in chunks:
        physical_page = (c.page + page_offset) if c.page is not None else None
        info = figdetect.figure_info(
            c.text,
            page_has_images=bool((page_images or {}).get(physical_page, False)),
        )
        metadata = {
            "chunk_id": c.chunk_id,
            "parent_id": c.parent_id or c.chunk_id,
            "chunk_index": c.chunk_index,
            "chunk_count": c.chunk_count,
            "is_sidebar": c.is_sidebar,
            "content_role": c.content_role,
            "content_hash": hashlib.sha256(c.text.encode("utf-8")).hexdigest(),
            "kind": c.kind,
            "subject": c.subject,
            "book": c.book,
            "version": c.version,
            "chapter": c.chapter,
            "section": c.section,
            "heading": c.heading,
            "anchor": c.anchor,
            "has_figure": bool(info["has_figure"]),
            "fig_nums": info["fig_nums"],
            "fidelity": info["fidelity"],
        }
        if c.page is not None:
            metadata["page"] = c.page
        docs.append(
            Document(
                page_content=c.text,
                metadata=metadata,
            )
        )
    return docs


def _split_long_sections(chunks: list[Chunk], limit: int = 700,
                         overlap: int = 90) -> list[Chunk]:
    """把整节粗卡切成细粒度语义块，并记录稳定的父子关系。"""
    from dataclasses import replace

    from edu_agent.chunking import semantic_chunks

    section_headings = {c.section: c.heading for c in chunks if c.section and c.heading}
    damaged_subsection = re.compile(r"(?<!\d)([1-9])([1-9])([1-9])!")
    out: list[Chunk] = []
    for c in chunks:
        if c.kind != "section":
            is_sidebar = not bool(c.section)
            out.append(replace(c, parent_id=c.parent_id or c.chunk_id,
                               chunk_index=1, chunk_count=1,
                               is_sidebar=is_sidebar,
                               content_role=infer_content_role(
                                   c.text, c.heading, c.kind, is_sidebar)))
            continue
        blocks = semantic_chunks(c.text, target_chars=limit, overlap_chars=overlap)
        if not blocks:
            continue
        total = len(blocks)
        active_section = c.section
        for i, b in enumerate(blocks, 1):
            # PDF 艺术字小节号常被抽成“422!”“511!”。旁栏结束、主节恢复后，
            # toc 粗切不会产生新头行；在细块层识别并把后续块恢复到已知主节。
            if not c.section:
                marker = damaged_subsection.search(b)
                if marker:
                    candidate = f"{marker.group(1)}.{marker.group(2)}"
                    if candidate in section_headings:
                        active_section = candidate
            cid = c.chunk_id if total == 1 else f"{c.chunk_id}-p{i}"
            is_sidebar = not bool(active_section)
            out.append(replace(c, chunk_id=cid, parent_id=c.chunk_id,
                               chunk_index=i, chunk_count=total, text=b.strip(),
                               section=active_section,
                               heading=section_headings.get(active_section, c.heading),
                               is_sidebar=is_sidebar,
                               content_role=infer_content_role(
                                   b, section_headings.get(active_section, c.heading),
                                   c.kind, is_sidebar)))
    return out


def ingest(content_dir: Path | None = None, chroma_dir: Path | None = None,
           auto_content_dir: Path | None = None) -> int:
    """入库全部结构化 chunk，返回入库条数。

    来源双轨：
      content/structured/        人工精修细卡（concept/example/exercise）
      content/structured_auto/   PDF 自动整节粗卡（section）
    幂等：先构建临时 collection 并校验，再切换名称；保留旧 collection 作为回滚点。
    同一 Chroma 目录中的 edu_wiki 等其他 collection 不受影响。
    """
    from langchain_chroma import Chroma

    from edu_agent.config import get_embeddings

    s = load_settings()
    content_dir = Path(content_dir or s.content_dir)
    auto_dir = Path(auto_content_dir or s.auto_content_dir)
    chroma_dir = Path(chroma_dir or s.chroma_db_dir)

    md_files = sorted(content_dir.glob("*.md")) + sorted(auto_dir.glob("*.md"))
    if not md_files:
        raise FileNotFoundError(f"{content_dir} 与 {auto_dir} 下都没有 *.md 结构化产物")

    all_chunks: list[Chunk] = []
    for f in md_files:
        parsed = parse_file(f)
        if parsed:
            print(f"  {f.name}: {len(parsed)} chunks")
            all_chunks.extend(parsed)
    all_chunks = _split_long_sections(all_chunks)

    ids = [c.chunk_id for c in all_chunks]
    if len(set(ids)) != len(ids):
        dup = [i for i in ids if ids.count(i) > 1]
        raise ValueError(f"chunk_id 重复: {sorted(set(dup))}")

    chroma_dir.mkdir(parents=True, exist_ok=True)
    page_images = _page_image_flags(s.textbook_pdf)
    docs = to_documents(all_chunks, page_images=page_images)
    build_name = "edu_textbook_build_" + uuid.uuid4().hex[:12]
    store = None
    try:
        store = Chroma.from_documents(
            documents=docs,
            embedding=get_embeddings(),
            ids=ids,
            persist_directory=str(chroma_dir),
            collection_name=build_name,
            collection_metadata={"hnsw:space": "cosine"},
        )
        if store._collection.count() != len(docs):
            raise RuntimeError("临时教材 collection 数量校验失败")

        import chromadb

        client = chromadb.PersistentClient(path=str(chroma_dir))
        try:
            old = client.get_collection("edu_textbook")
        except Exception:
            old = None
        backup_name = ("edu_textbook_backup_" + time.strftime("%Y%m%d_%H%M%S")
                       + "_" + uuid.uuid4().hex[:4])
        new_col = client.get_collection(build_name)
        if old is not None:
            old.modify(name=backup_name)
        try:
            new_col.modify(name="edu_textbook")
        except Exception:
            if old is not None:
                old.modify(name="edu_textbook")
            raise
        # 仅保留最近一个可回滚版本，避免每次维护都无限放大二进制仓库。
        try:
            backups = sorted(
                (c for c in client.list_collections()
                 if c.name.startswith("edu_textbook_backup_")),
                key=lambda c: c.name,
                reverse=True,
            )
            for stale in backups[1:]:
                client.delete_collection(stale.name)
        except Exception:
            # 回滚版本轮换失败不应把已经成功切换的新索引报告成失败。
            pass
    except Exception:
        try:
            import chromadb
            chromadb.PersistentClient(path=str(chroma_dir)).delete_collection(build_name)
        except Exception:
            pass
        raise

    # 当前进程若曾检索过旧 collection，需要显式失效缓存。
    try:
        from edu_agent import bm25_index, retrieve

        retrieve.clear_caches()
        bm25_index.clear_cache()
    except Exception:
        pass
    try:
        from edu_agent.knowledge_health import write_textbook_manifest

        write_textbook_manifest(chroma_dir, md_files)
    except Exception as exc:
        # 新索引已经成功切换，manifest 写失败不能伪装成“索引构建失败”；
        # 健康检查会明确报告 manifest_missing，运维可单独修复。
        print(f"  警告：知识库 manifest 写入失败：{type(exc).__name__}: {exc}")
    return len(docs)


def backfill_content_roles(chroma_dir: Path | None = None,
                           collection_name: str = "edu_textbook",
                           batch_size: int = 200) -> dict[str, int]:
    """为旧索引原地补齐 content_role；不重算向量，也不改文档正文。"""
    import chromadb

    directory = Path(chroma_dir or load_settings().chroma_db_dir)
    collection = chromadb.PersistentClient(path=str(directory)).get_collection(collection_name)
    got = collection.get(include=["documents", "metadatas"])
    ids = got.get("ids") or []
    documents = got.get("documents") or []
    metadatas = got.get("metadatas") or []
    changed_ids: list[str] = []
    changed_metas: list[dict] = []
    counts: dict[str, int] = {}
    for cid, text, original in zip(ids, documents, metadatas):
        meta = dict(original or {})
        role = infer_content_role(
            text or "", str(meta.get("heading") or ""),
            str(meta.get("kind") or "section"), bool(meta.get("is_sidebar")),
        )
        counts[role] = counts.get(role, 0) + 1
        if meta.get("content_role") == role:
            continue
        meta["content_role"] = role
        changed_ids.append(cid)
        changed_metas.append(meta)
    for start in range(0, len(changed_ids), max(1, batch_size)):
        collection.update(
            ids=changed_ids[start:start + batch_size],
            metadatas=changed_metas[start:start + batch_size],
        )
    counts["updated"] = len(changed_ids)
    counts["total"] = len(ids)
    return counts


def main() -> None:
    s = load_settings()
    print(f"ingest: {s.content_dir} -> {s.chroma_db_dir}")
    n = ingest()
    print(f"入库完成：{n} 条 (ids 去重后 {n})")
    # S5 验收：抽查 3 条 metadata
    from langchain_chroma import Chroma

    from edu_agent.config import get_embeddings

    db = Chroma(
        persist_directory=str(s.chroma_db_dir),
        embedding_function=get_embeddings(),
        collection_name="edu_textbook",
    )
    samples = db.get(include=["metadatas"], limit=3)["metadatas"]
    for md in samples:
        print("  抽查:", {k: md.get(k) for k in ("chunk_id", "kind", "chapter", "page", "anchor")})


if __name__ == "__main__":
    main()
