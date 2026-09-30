"""用户上传 PDF：本地解析 -> 独立向量库（user_pdfs），支持按来源问答。"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import uuid
from pathlib import Path

from edu_agent.config import load_settings

ROOT = Path(__file__).resolve().parents[2]
# 所有运行态文件必须跟随 EDU_DATA_DIR，不能另起一个写死的 ROOT/data。
DATA_DIR = load_settings().data_dir
PDF_DIR = DATA_DIR / "pdfs"                 # 原件
VDB_DIR = DATA_DIR / "user_pdf_db"          # 独立向量库
META = DATA_DIR / "pdf_sources.json"
COLLECTION = "user_pdfs"
LIBRARIES = ("personal", "official")
_META_LOCK = threading.RLock()


def safe_filename(filename: str, suffix: str = ".pdf") -> str:
    """把 HTTP multipart 的 filename 变成仅用于展示的安全文件名。

    浏览器通常只发 basename，但该字段可由客户端伪造，绝不能直接参与路径拼接。
    """
    name = Path(str(filename or "").replace("\\", "/")).name
    stem = re.sub(r"[^\w\-\u4e00-\u9fff]+", "_", Path(name).stem).strip("_.") or "document"
    return stem[:80] + suffix


def stored_path(stored_name: str) -> Path:
    """返回 PDF_DIR 内的受控存储路径；拒绝旧元数据里的穿越路径。"""
    root = PDF_DIR.resolve()
    candidate = (root / Path(str(stored_name or "").replace("\\", "/")).name).resolve()
    if root not in candidate.parents:
        raise ValueError("非法 PDF 存储路径")
    return candidate


def list_sources() -> list[dict]:
    with _META_LOCK:
        if META.exists():
            try:
                return json.loads(META.read_text(encoding="utf-8"))
            except Exception:
                pass
    return []


def normalize_library(value: str | None) -> str:
    """把历史元数据补到“个人资料”；只允许两个明确的归属类型。"""
    library = str(value or "personal").strip().lower()
    if library not in LIBRARIES:
        raise ValueError("资料归属只能是 personal 或 official")
    return library


def _save_meta(items: list[dict]):
    with _META_LOCK:
        META.parent.mkdir(parents=True, exist_ok=True)
        temp = META.with_name(META.name + ".tmp-" + uuid.uuid4().hex[:8])
        temp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        temp.replace(META)


def _clean(text: str) -> str:
    text = re.sub(r"[\ue000-\uf8ff]", "", text)
    return text


def ingest_pdf(file_bytes: bytes, filename: str, max_pages: int | None = None,
               library: str = "personal") -> dict:
    """保存原件 -> pymupdf 抽文本 -> 按行切块(<=1000) -> 入 user_pdfs 库。"""
    import pymupdf

    from langchain_chroma import Chroma

    from edu_agent.config import get_embeddings

    library = normalize_library(library)
    display_name = safe_filename(filename)
    stem = Path(display_name).stem
    digest = hashlib.sha256(file_bytes).hexdigest()[:12]
    # 文件名只用于显示；真正的落盘名与向量 source 均由服务端生成，避免覆盖和路径穿越。
    source = f"{stem}-{digest}"
    existing = next((m for m in list_sources() if m.get("source") == source), None)
    if existing is not None:
        return {**existing, "deduplicated": True}

    stored_name = f"{uuid.uuid4().hex}.pdf"
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    VDB_DIR.mkdir(parents=True, exist_ok=True)
    orig = stored_path(stored_name)
    orig.write_bytes(file_bytes)

    try:
        doc = pymupdf.open(stream=file_bytes, filetype="pdf")
    except Exception:
        # 解析失败的伪造/损坏 PDF 不应在运行目录留下不可见的孤儿文件。
        orig.unlink(missing_ok=True)
        raise
    n_pages = doc.page_count
    if max_pages is not None and n_pages > max_pages:
        doc.close()
        orig.unlink(missing_ok=True)
        raise ValueError(f"PDF 页数 {n_pages} 超过上限 {max_pages}")

    from edu_agent.chunking import semantic_chunks

    chunks: list[dict] = []
    try:
        for i in range(n_pages):
            txt = _clean(doc[i].get_text())
            for body in semantic_chunks(txt, target_chars=700, overlap_chars=90):
                chunks.append({"page": i + 1, "text": body})
    finally:
        doc.close()

    if not chunks:
        orig.unlink(missing_ok=True)
        raise ValueError("PDF 未提取到可索引文本")

    from langchain_core.documents import Document

    docs, ids = [], []
    total_chunks = len(chunks)
    for k, c in enumerate(chunks, 1):
        chunk_id = hashlib.sha256((source + ":" + str(k)).encode("utf-8")).hexdigest()[:24]
        md = {"chunk_id": chunk_id, "parent_id": source, "chunk_index": k,
              "chunk_count": total_chunks, "content_hash": hashlib.sha256(c["text"].encode("utf-8")).hexdigest(),
              "source": source, "kind": "pdf", "subject": "user-pdf", "book": display_name,
              "chapter": "", "section": "", "heading": "", "page": c["page"],
              "page_start": c["page"], "page_end": c["page"], "anchor": ""}
        docs.append(Document(page_content=c["text"], metadata=md))
        ids.append(chunk_id)

    emb = get_embeddings()
    if VDB_DIR.exists() and (VDB_DIR / "chroma.sqlite3").exists():
        store = Chroma(persist_directory=str(VDB_DIR), embedding_function=emb, collection_name=COLLECTION)
        store.add_documents(documents=docs, ids=ids)
        count = store._collection.count()
    else:
        store = Chroma.from_documents(documents=docs, embedding=emb, ids=ids,
                                      persist_directory=str(VDB_DIR), collection_name=COLLECTION)
        count = store._collection.count()

    with _META_LOCK:
        meta = [m for m in list_sources() if m.get("source") != source]
        meta.append({"source": source, "file": display_name, "stored_file": stored_name,
                     "pages": n_pages, "chunks": len(chunks), "total": count,
                     "library": library})
        _save_meta(meta)
    return {"source": source, "file": display_name, "pages": n_pages, "chunks": len(chunks),
            "total": count, "library": library}


def set_library(source: str, library: str) -> dict:
    """把已入库资料在“个人资料”和“官方教材”之间移动，不重建向量。"""
    library = normalize_library(library)
    with _META_LOCK:
        items = list_sources()
        hit = next((item for item in items if item.get("source") == source), None)
        if hit is None:
            return {"ok": False, "error": "资料不存在"}
        for item in items:
            if item.get("source") == source:
                item["library"] = library
        _save_meta(items)
    return {"ok": True, "source": source, "library": library}


def ask_pdf_summary():
    return list_sources()
