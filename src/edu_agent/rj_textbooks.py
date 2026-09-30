"""人教版教材语料的发现、规范化和独立 RAG 入库。

这个模块刻意不复制原始教材，也不触碰 ``edu_textbook``：源目录中的 Markdown
仍是唯一原文，向量库 ``edu_rj_textbooks`` 只保存可检索的文本分块和定位元数据。
"""
from __future__ import annotations

import argparse
from functools import lru_cache
import hashlib
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[2]
COLLECTION = "edu_rj_textbooks"
# 全科教材体量远大于首发数学教材，单独目录避免与正在服务的主教材库争用本地 HNSW
# 写入器；查询侧显式传入此目录，不依赖环境变量。
VDB_DIR = ROOT / "data" / "rj_textbook_rag"
_IMAGE_LINE = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$")
_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_CHAPTER = re.compile(r"第[一二三四五六七八九十百0-9]+章\s*(.*)")
# 教材正文通常写作“第一节……”，少量资料才会用 1.1 / 1.2 这类编号。
# 两种格式都保留，便于同一套导航兼容不同学科和版本。
_SECTION = re.compile(r"第([一二三四五六七八九十百0-9]+)节\s*(.*)")
_NUMBERED_SECTION = re.compile(r"(?<!\d)(\d+(?:\.\d+){1,3})(?!\d)")
_NOISE = re.compile(r"(?:微信扫码|免费下载|最新教材配套资源)")


@dataclass(frozen=True)
class SourceDocument:
    path: Path
    source_id: str
    source_path: str
    subject: str
    edition: str
    book: str


@dataclass(frozen=True)
class CorpusChunk:
    chunk_id: str
    text: str
    metadata: dict


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "-", value).strip("-").lower()
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:10]
    return f"{cleaned[:50] or 'item'}-{digest}"


def _subject(folder: str, title: str) -> str:
    for item in ("数学", "语文", "英语", "物理", "化学", "生物", "政治", "历史", "地理"):
        if item in folder or item in title:
            return item
    return "未分类"


def _edition(folder: str) -> str:
    match = re.search(r"人教[AB]版", folder)
    return match.group(0) if match else "人教版"


def discover(source_root: Path) -> list[SourceDocument]:
    """发现转换器产生的 Markdown；ID 只依赖相对路径，重跑保持稳定。"""
    root = Path(source_root).resolve()
    markdown_root = root / "markdown"
    if not markdown_root.is_dir():
        raise FileNotFoundError(f"找不到 markdown 目录：{markdown_root}")
    documents: list[SourceDocument] = []
    for path in sorted(markdown_root.rglob("*.md")):
        rel = path.relative_to(markdown_root).as_posix()
        title = path.stem.replace("电子课本", "").strip()
        folder = path.parent.parent.name if path.parent.parent != markdown_root else path.parent.name
        documents.append(SourceDocument(
            path=path,
            source_id="rj-" + hashlib.sha1(rel.encode("utf-8")).hexdigest()[:16],
            source_path=rel,
            subject=_subject(folder, title),
            edition=_edition(folder),
            book=title,
        ))
    return documents


def _clean_lines(text: str) -> list[str]:
    return [line.rstrip() for line in text.splitlines()
            if not _IMAGE_LINE.match(line) and not _NOISE.search(line)]


def _heading_units(source: SourceDocument) -> Iterable[tuple[str, str, str, str]]:
    """按标题划出可追溯单元，返回 chapter/section/heading/body。"""
    lines = _clean_lines(source.path.read_text(encoding="utf-8", errors="replace"))
    current_chapter = ""
    current_section = ""
    current_heading = "导言"
    body: list[str] = []

    def emit():
        text = "\n".join(body).strip()
        if text:
            yield current_chapter, current_section, current_heading, text

    for line in lines:
        match = _HEADING.match(line)
        if not match:
            body.append(line)
            continue
        yield from emit()
        body = []
        title = match.group(2).strip().replace("　", " ")
        chapter = _CHAPTER.search(title)
        if chapter:
            current_chapter = title
            current_section = ""
        section = _SECTION.search(title) or _NUMBERED_SECTION.search(title)
        if section:
            current_section = section.group(1)
        current_heading = title
    yield from emit()


def chunks_of(source: SourceDocument, target_chars: int = 700,
              overlap_chars: int = 90) -> list[CorpusChunk]:
    """把一本教材切为 RAG chunk，不把图片链接或广告文本送去向量化。"""
    from edu_agent.chunking import semantic_chunks

    chunks: list[CorpusChunk] = []
    ordinal = 0
    for chapter, section, heading, body in _heading_units(source):
        for part_index, body_part in enumerate(
                semantic_chunks(body, target_chars=target_chars, overlap_chars=overlap_chars), 1):
            text = body_part.strip()
            if len(text) < 24:
                continue
            ordinal += 1
            chunk_id = f"{source.source_id}-{ordinal:05d}"
            chunks.append(CorpusChunk(
                chunk_id=chunk_id,
                text=text,
                metadata={
                    "chunk_id": chunk_id,
                    "parent_id": source.source_id,
                    "chunk_index": ordinal,
                    "chunk_count": 0,  # 下方统一回填，避免边切边统计。
                    "source": source.source_id,
                    "source_path": source.source_path,
                    "source_name": source.book,
                    "subject": source.subject,
                    "edition": source.edition,
                    "book": source.book,
                    "chapter": chapter,
                    "section": section,
                    "heading": heading,
                    "kind": "textbook",
                    "content_role": "source",
                    "license_scope": "local_learning_only",
                    "content_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                },
            ))
    total = len(chunks)
    return [CorpusChunk(c.chunk_id, c.text, {**c.metadata, "chunk_count": total}) for c in chunks]


def corpus(source_root: Path, subjects: set[str] | None = None) -> tuple[list[SourceDocument], list[CorpusChunk]]:
    sources = discover(source_root)
    if subjects:
        sources = [source for source in sources if source.subject in subjects]
    chunks = [chunk for source in sources for chunk in chunks_of(source)]
    return sources, chunks


def plan(source_root: Path, subjects: set[str] | None = None) -> dict:
    sources, chunks = corpus(source_root, subjects)
    by_subject: dict[str, int] = {}
    for source in sources:
        by_subject[source.subject] = by_subject.get(source.subject, 0) + 1
    return {"collection": COLLECTION, "sources": len(sources), "chunks": len(chunks),
            "by_subject": by_subject, "source_root": str(Path(source_root).resolve())}


def _ingest_chroma_legacy(source_root: Path, subjects: set[str] | None = None,
                          batch_size: int = 64) -> dict:
    """全量重建独立集合，成功校验后原子切换；失败不影响任何现有库。"""
    import chromadb
    from langchain_chroma import Chroma
    from langchain_core.documents import Document

    from edu_agent.config import get_embeddings
    from edu_agent.retrieve import clear_caches

    sources, chunks = corpus(source_root, subjects)
    if not chunks:
        raise ValueError("没有可入库的教材文本块")
    build_name = f"{COLLECTION}_build_{uuid.uuid4().hex[:12]}"
    docs = [Document(page_content=c.text, metadata=c.metadata) for c in chunks]
    ids = [c.chunk_id for c in chunks]
    store = None
    try:
        embedding = get_embeddings()
        first = min(max(1, batch_size), len(docs))
        store = Chroma.from_documents(docs[:first], embedding=embedding, ids=ids[:first],
                                      persist_directory=str(VDB_DIR),
                                      collection_name=build_name,
                                      collection_metadata={"hnsw:space": "cosine"})
        for start in range(first, len(docs), max(1, batch_size)):
            stop = start + max(1, batch_size)
            store.add_documents(docs[start:stop], ids=ids[start:stop])
        if store._collection.count() != len(docs):
            raise RuntimeError("教材 RAG 临时集合数量校验失败")
        # 在写入进程内实际查询一次，确认 HNSW 已可读后才允许切换名称。仅 count()
        # 会漏掉“数据量正确、索引尚未完成落盘”的状态。
        probe = store.similarity_search(chunks[0].text[:120], k=1)
        if not probe:
            raise RuntimeError("教材 RAG 临时集合检索校验失败")
        # Chroma 1.5 的本地 HNSW 写入由后台线程完成；若让解释器直接退出，SQLite
        # 中的数量会正确、但 HNSW 的 *.bin 尚未刷盘。显式 close 后重新打开验证。
        store._client.close()
        store = Chroma(persist_directory=str(VDB_DIR), embedding_function=embedding,
                       collection_name=build_name)
        reopened_probe = store.similarity_search(chunks[0].text[:120], k=1)
        if not reopened_probe:
            raise RuntimeError("教材 RAG 重开索引检索校验失败")
        client = chromadb.PersistentClient(path=str(VDB_DIR))
        try:
            old = client.get_collection(COLLECTION)
        except Exception:
            old = None
        backup = f"{COLLECTION}_backup_{time.strftime('%Y%m%d_%H%M%S')}"
        if old is not None:
            old.modify(name=backup)
        try:
            client.get_collection(build_name).modify(name=COLLECTION)
        except Exception:
            if old is not None:
                old.modify(name=COLLECTION)
            raise
        client.close()
    except Exception:
        try:
            chromadb.PersistentClient(path=str(VDB_DIR)).delete_collection(build_name)
        except Exception:
            pass
        raise
    clear_caches()
    return {**plan(source_root, subjects), "collection_count": len(docs), "status": "ready"}


# ---------------------------------------------------------------- flat persistent index
#
# Chroma 1.5.9 currently fails to persist a large HNSW segment on this Windows host.
# A dense matrix is compact (about 73 MB for this corpus) and gives deterministic local
# cosine retrieval without a database service.  It intentionally keeps the same chunk
# metadata contract as the rest of the application.
_VECTOR_FILE = VDB_DIR / "vectors.npy"
_CHUNK_FILE = VDB_DIR / "chunks.jsonl"
_MANIFEST_FILE = VDB_DIR / "manifest.json"
_SEARCH_LOCK = threading.Lock()
_SEARCH_CACHE: tuple[object, list[CorpusChunk]] | None = None


def _atomic_write_text(path: Path, text: str) -> None:
    temp = path.with_name(path.name + ".tmp-" + uuid.uuid4().hex[:8])
    temp.write_text(text, encoding="utf-8")
    temp.replace(path)


def _save_flat(chunks: list[CorpusChunk], vectors) -> None:
    import numpy as np

    VDB_DIR.mkdir(parents=True, exist_ok=True)
    temp_vectors = VDB_DIR / ("vectors-" + uuid.uuid4().hex[:8] + ".npy")
    np.save(temp_vectors, np.asarray(vectors, dtype=np.float32))
    temp_vectors.replace(_VECTOR_FILE)
    _atomic_write_text(_CHUNK_FILE, "".join(
        json.dumps({"chunk_id": chunk.chunk_id, "text": chunk.text, "metadata": chunk.metadata},
                   ensure_ascii=False) + "\n" for chunk in chunks))


def _load_flat() -> tuple[object, list[CorpusChunk]]:
    import numpy as np

    if not _VECTOR_FILE.is_file() or not _CHUNK_FILE.is_file():
        raise FileNotFoundError("人教版全科教材 RAG 尚未构建")
    vectors = np.load(_VECTOR_FILE, mmap_mode="r")
    chunks = []
    for line in _CHUNK_FILE.read_text(encoding="utf-8").splitlines():
        item = json.loads(line)
        chunks.append(CorpusChunk(item["chunk_id"], item["text"], item["metadata"]))
    if len(vectors) != len(chunks) or not chunks:
        raise ValueError("人教版全科教材 RAG 索引与元数据不一致")
    return vectors, chunks


def ingest(source_root: Path, subjects: set[str] | None = None, batch_size: int = 64) -> dict:
    """构建可跨进程重开的本地 dense RAG 索引；不修改现有 Chroma 集合。"""
    import numpy as np
    from edu_agent.config import get_embeddings

    sources, chunks = corpus(source_root, subjects)
    if not chunks:
        raise ValueError("没有可入库的教材文本块")
    embedding = get_embeddings()
    vectors: list[list[float]] = []
    step = max(1, batch_size)
    for start in range(0, len(chunks), step):
        batch = chunks[start:start + step]
        vectors.extend(embedding.embed_documents([chunk.text for chunk in batch]))
        if (start // step + 1) % 20 == 0 or start + step >= len(chunks):
            print(f"  embedding: {min(start + step, len(chunks))}/{len(chunks)}", flush=True)
    matrix = np.asarray(vectors, dtype=np.float32)
    if matrix.ndim != 2 or len(matrix) != len(chunks):
        raise RuntimeError("教材 RAG 向量数量校验失败")
    _save_flat(chunks, matrix)
    # 新进程等价验证：清缓存后重新 mmap 文件并检索，防止半写入索引被宣布可用。
    global _SEARCH_CACHE
    with _SEARCH_LOCK:
        _SEARCH_CACHE = None
    probe = search(chunks[0].text[:120], top_k=1)
    if not probe:
        raise RuntimeError("教材 RAG 持久化检索校验失败")
    manifest = {**plan(source_root, subjects), "collection_count": len(chunks),
                "vector_dim": int(matrix.shape[1]), "backend": "numpy-cosine",
                "status": "ready"}
    _atomic_write_text(_MANIFEST_FILE, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return manifest


def stats() -> dict:
    if not _MANIFEST_FILE.is_file():
        return {"status": "missing", "collection": COLLECTION, "blocks": 0}
    return json.loads(_MANIFEST_FILE.read_text(encoding="utf-8"))


def catalog() -> list[dict]:
    """按原始教材文件输出书目；每项都可作为独立 RAG 检索范围。"""
    with _SEARCH_LOCK:
        global _SEARCH_CACHE
        if _SEARCH_CACHE is None:
            _SEARCH_CACHE = _load_flat()
        _, chunks = _SEARCH_CACHE
    books: dict[str, dict] = {}
    for chunk in chunks:
        md = chunk.metadata
        source = str(md.get("source") or "")
        if not source:
            continue
        item = books.setdefault(source, {
            "source": source,
            "title": str(md.get("source_name") or md.get("book") or source),
            "subject": str(md.get("subject") or "未分类"),
            "edition": str(md.get("edition") or "人教版"),
            "source_path": str(md.get("source_path") or ""),
            "blocks": 0,
        })
        item["blocks"] += 1
    for item in books.values():
        item["pdf_available"] = bool(official_pdf_path(item["source"]))
    return sorted(books.values(), key=lambda item: (
        item["subject"], item["edition"], item["title"], item["source"]
    ))


def official_pdf_path(source: str) -> Path | None:
    """返回一册公共教材对应的原始 PDF；只从 manifest 指定的教材根目录取文件。"""
    manifest = stats()
    root_text = str(manifest.get("source_root") or "").strip()
    if not root_text:
        return None
    root = Path(root_text).resolve()
    if not root.is_dir():
        return None
    try:
        with _SEARCH_LOCK:
            global _SEARCH_CACHE
            if _SEARCH_CACHE is None:
                _SEARCH_CACHE = _load_flat()
            _, chunks = _SEARCH_CACHE
        source_path = next(
            str(chunk.metadata.get("source_path") or "")
            for chunk in chunks if str(chunk.metadata.get("source") or "") == source
        )
        parts = Path(source_path).parts
        if len(parts) < 2:
            return None
        # 转换目录格式为 “原文件.pdf-UUID”；从中恢复原始 PDF 名称。
        container = parts[1]
        match = re.match(r"^(.*\.pdf)-[0-9a-f-]{36}$", container, re.I)
        if not match:
            return None
        candidate = (root / parts[0] / match.group(1)).resolve()
        return candidate if candidate.is_file() and candidate.suffix.lower() == ".pdf" and root in candidate.parents else None
    except (StopIteration, OSError, ValueError):
        return None


def _source_markdown_path(source: str) -> Path | None:
    """定位转换后 Markdown，只接受已入库 source ID。"""
    root_text = str(stats().get("source_root") or "").strip()
    if not root_text:
        return None
    root = Path(root_text).resolve()
    try:
        with _SEARCH_LOCK:
            global _SEARCH_CACHE
            if _SEARCH_CACHE is None:
                _SEARCH_CACHE = _load_flat()
            _, chunks = _SEARCH_CACHE
        rel = next(str(chunk.metadata.get("source_path") or "") for chunk in chunks
                   if str(chunk.metadata.get("source") or "") == source)
        path = (root / "markdown" / rel).resolve()
        return path if path.is_file() and root in path.parents else None
    except (StopIteration, OSError, ValueError):
        return None


def _norm_heading(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", str(text or ""), flags=re.UNICODE).lower()


def _layout_page_texts(layout_path: Path) -> list[str]:
    """读取转换器 layout.json 的页级文字，用于把目录标题定位回 PDF 页。"""
    data = json.loads(layout_path.read_text(encoding="utf-8"))
    pages = data.get("pdf_info") or []
    result: list[str] = []
    for page in pages:
        spans: list[str] = []
        for block in list(page.get("preproc_blocks") or []) + list(page.get("discarded_blocks") or []):
            for line in block.get("lines") or []:
                for span in line.get("spans") or []:
                    content = str(span.get("content") or "")
                    if content:
                        spans.append(content)
        result.append("\n".join(spans))
    return result


@lru_cache(maxsize=96)
def chapters(source: str) -> list[dict]:
    """从一册教材 Markdown + layout 页坐标生成“章 → 节 → PDF 页”导航。"""
    markdown = _source_markdown_path(source)
    if markdown is None:
        return []
    seen: set[tuple[str, str]] = set()
    output: list[dict] = []
    current: dict | None = None
    for line in _clean_lines(markdown.read_text(encoding="utf-8", errors="replace")):
        match = _HEADING.match(line)
        if not match:
            continue
        title = match.group(2).strip().replace("　", " ")
        if _CHAPTER.search(title):
            key = ("chapter", title)
            if key in seen:
                continue
            seen.add(key)
            current = {"title": title, "page": None, "sections": []}
            output.append(current)
            continue
        section = _SECTION.search(title) or _NUMBERED_SECTION.search(title)
        if section and current is not None:
            key = ("section", title)
            if key not in seen:
                seen.add(key)
                current["sections"].append({"no": section.group(1), "title": title, "page": None})
    if not output:
        return []
    layout = markdown.parent / "layout.json"
    if not layout.is_file():
        return output
    try:
        page_texts = [_norm_heading(text) for text in _layout_page_texts(layout)]
    except (OSError, ValueError, json.JSONDecodeError):
        return output
    # 目录通常位于前置页；正文定位从目录之后开始，避免跳回目录页。
    toc_pages = [i for i, text in enumerate(page_texts) if "目录" in text]
    start = (max(toc_pages) + 1) if toc_pages else 0

    def locate(title: str, minimum: int) -> int | None:
        needle = _norm_heading(title)
        if not needle:
            return None
        for index in range(minimum, len(page_texts)):
            if needle in page_texts[index]:
                return index + 1  # PDF viewer uses 1-based pages.
        return None

    for chapter in output:
        chapter["page"] = locate(chapter["title"], start)
        section_start = (chapter["page"] - 1) if chapter["page"] else start
        for section in chapter["sections"]:
            section["page"] = locate(section["title"], section_start)
    return output


def search(query: str, top_k: int = 3, source: str | None = None) -> list:
    """搜索全科教材库，返回与 ``retrieve.Hit`` 相同的结果形状。"""
    import numpy as np
    from edu_agent.config import get_embeddings
    from edu_agent.retrieve import Hit

    with _SEARCH_LOCK:
        global _SEARCH_CACHE
        if _SEARCH_CACHE is None:
            _SEARCH_CACHE = _load_flat()
        vectors, chunks = _SEARCH_CACHE
    query_vector = np.asarray(get_embeddings().embed_query(query), dtype=np.float32)
    denom = np.linalg.norm(vectors, axis=1) * max(float(np.linalg.norm(query_vector)), 1e-12)
    scores = (vectors @ query_vector) / np.maximum(denom, 1e-12)
    candidate_indices = np.arange(len(chunks))
    if source:
        candidate_indices = np.asarray([
            index for index, chunk in enumerate(chunks)
            if str(chunk.metadata.get("source") or "") == source
        ], dtype=int)
    if not len(candidate_indices):
        return []
    count = max(1, min(int(top_k), len(candidate_indices)))
    order = candidate_indices[np.argsort(-scores[candidate_indices])[:count]]
    return [Hit(chunk_id=chunks[int(i)].chunk_id, text=chunks[int(i)].text,
                metadata=dict(chunks[int(i)].metadata), score=float(1 - scores[int(i)]))
            for i in order]


def main() -> int:
    parser = argparse.ArgumentParser(description="导入人教版 Markdown 到独立教材 RAG 库")
    parser.add_argument("--source-root", required=True, help="包含 markdown/ 的教材根目录")
    parser.add_argument("--subject", action="append", dest="subjects", help="只导入指定学科；可重复")
    parser.add_argument("--dry-run", action="store_true", help="只统计来源与分块，不写向量库")
    parser.add_argument("--batch-size", type=int, default=64)
    args = parser.parse_args()
    subjects = set(args.subjects or []) or None
    result = plan(Path(args.source_root), subjects) if args.dry_run else ingest(
        Path(args.source_root), subjects, args.batch_size)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
