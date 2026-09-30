"""知识库索引清单与只读健康检查。"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from edu_agent.config import ROOT, load_settings


MANIFEST_NAME = "edu_textbook_manifest.json"
SCHEMA_VERSION = 2
REQUIRED_TEXTBOOK_META = {
    "chunk_id", "parent_id", "chunk_index", "chunk_count", "content_hash",
    "kind", "chapter", "heading", "is_sidebar", "fidelity", "has_figure",
    "content_role",
}


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def source_records(files: list[Path]) -> list[dict]:
    rows = []
    for path in sorted((Path(p).resolve() for p in files), key=str):
        try:
            name = str(path.relative_to(ROOT)).replace("\\", "/")
        except ValueError:
            name = path.name
        rows.append({"path": name, "bytes": path.stat().st_size, "sha256": _sha256(path)})
    return rows


def source_digest(rows: list[dict]) -> str:
    body = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _dimension(collection) -> int | None:
    try:
        peek = collection.peek(limit=1)
        embeddings = peek.get("embeddings")
        if embeddings is not None and len(embeddings):
            return len(embeddings[0])
    except Exception:
        pass
    return None


def write_textbook_manifest(chroma_dir: Path, source_files: list[Path]) -> dict:
    """根据已经切换成功的 edu_textbook 写原子 manifest。"""
    import chromadb

    directory = Path(chroma_dir)
    collection = chromadb.PersistentClient(path=str(directory)).get_collection("edu_textbook")
    got = collection.get(include=["metadatas"])
    metas = got.get("metadatas") or []
    settings = load_settings()
    sources = source_records(source_files)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "collection": "edu_textbook",
        "count": collection.count(),
        "embedding": {
            "provider": settings.embedding_provider,
            "model": settings.embedding_model,
            "dimension": _dimension(collection),
        },
        "chunking": {"strategy": "semantic", "target_chars": 700, "overlap_chars": 90},
        "sources": sources,
        "source_digest": source_digest(sources),
        "metadata_summary": {
            "parents": len({m.get("parent_id") for m in metas}),
            "sidebars": sum(bool(m.get("is_sidebar")) for m in metas),
            "has_figure": sum(bool(m.get("has_figure")) for m in metas),
            "low_fidelity": sum(m.get("fidelity") == "low" for m in metas),
            "content_roles": {
                role: sum(m.get("content_role") == role for m in metas)
                for role in sorted({str(m.get("content_role") or "unknown") for m in metas})
            },
        },
    }
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / MANIFEST_NAME
    temp = target.with_name(target.name + ".tmp-" + uuid.uuid4().hex[:8])
    temp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(target)
    return manifest


def read_manifest(chroma_dir: Path | None = None) -> dict | None:
    path = Path(chroma_dir or load_settings().chroma_db_dir) / MANIFEST_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def audit() -> dict:
    """只读审计教材、Wiki 与上传 PDF；不调用 embedding，也不修改索引。"""
    import chromadb

    from edu_agent import pdf_upload

    settings = load_settings()
    issues: list[dict] = []

    def issue(level: str, code: str, message: str) -> None:
        issues.append({"level": level, "code": code, "message": message})

    client = chromadb.PersistentClient(path=str(settings.chroma_db_dir))
    collections = {c.name: c for c in client.list_collections()}
    counts = {name: col.count() for name, col in collections.items()}
    builds = sorted(name for name in collections if name.startswith("edu_textbook_build_"))
    backups = sorted(name for name in collections if name.startswith("edu_textbook_backup_"))
    if builds:
        issue("warning", "unfinished_build", "存在未清理的临时教材 collection：" + ", ".join(builds))

    manifest = read_manifest(settings.chroma_db_dir)
    textbook = collections.get("edu_textbook")
    if textbook is None:
        issue("error", "textbook_missing", "缺少 edu_textbook collection")
        textbook_metas: list[dict] = []
    else:
        textbook_metas = textbook.get(include=["metadatas"]).get("metadatas") or []
        missing = {
            key: sum(1 for meta in textbook_metas if meta.get(key) in (None, ""))
            for key in REQUIRED_TEXTBOOK_META
        }
        bad = {key: count for key, count in missing.items() if count}
        if bad:
            issue("error", "metadata_incomplete", "教材元数据缺失：" + json.dumps(bad, ensure_ascii=False))

    if manifest is None:
        issue("error", "manifest_missing", f"缺少 {MANIFEST_NAME}")
    else:
        if textbook is not None and manifest.get("count") != textbook.count():
            issue("error", "count_mismatch", "manifest 数量与当前教材 collection 不一致")
        emb = manifest.get("embedding") or {}
        if emb.get("provider") != settings.embedding_provider or emb.get("model") != settings.embedding_model:
            issue("error", "embedding_mismatch", "当前 embedding 配置与建库配置不一致，需要重建索引")
        configured_files = (sorted(settings.content_dir.glob("*.md"))
                            + sorted(settings.auto_content_dir.glob("*.md")))
        current_sources = source_records(configured_files)
        if source_digest(current_sources) != manifest.get("source_digest"):
            issue("error", "source_changed", "结构化教材源文件已变化，但索引尚未重建")

    user_count = 0
    legacy_pdf_chunks = 0
    try:
        user_client = chromadb.PersistentClient(path=str(pdf_upload.VDB_DIR))
        user_col = user_client.get_collection(pdf_upload.COLLECTION)
        user_count = user_col.count()
        user_metas = user_col.get(include=["metadatas"]).get("metadatas") or []
        legacy_pdf_chunks = sum(not m.get("chunk_id") for m in user_metas)
        if legacy_pdf_chunks:
            issue("warning", "legacy_pdf_chunks",
                  f"{legacy_pdf_chunks} 个历史 PDF 块缺少 chunk_id；检索可兼容，但建议重新导入")
        meta_sources = {str(x.get("source") or "") for x in pdf_upload.list_sources()}
        vector_sources = {str(x.get("source") or "") for x in user_metas}
        if vector_sources - meta_sources:
            issue("warning", "orphan_pdf_vectors", "存在没有资料目录记录的 PDF 向量")
        if meta_sources - vector_sources:
            issue("error", "missing_pdf_vectors", "资料目录中存在没有向量的 PDF")
    except Exception:
        if pdf_upload.list_sources():
            issue("error", "pdf_collection_missing", "存在 PDF 资料记录，但 user_pdfs collection 不可用")

    return {
        "ok": not any(x["level"] == "error" for x in issues),
        "status": "healthy" if not issues else ("error" if any(x["level"] == "error" for x in issues) else "warning"),
        "collections": counts,
        "backups": backups,
        "textbook_chunks": textbook.count() if textbook is not None else 0,
        "wiki_chunks": counts.get("edu_wiki", 0),
        "user_pdf_chunks": user_count,
        "legacy_pdf_chunks": legacy_pdf_chunks,
        "manifest": manifest,
        "issues": issues,
    }
