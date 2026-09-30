"""S6 实现：过滤检索 + top_k。

用法：
    hits = retrieve("函数的单调性怎么判断", top_k=3)
    hits = retrieve(q, kinds=["concept"])              # 只搜概念卡
    hits = retrieve(q, kinds=["example", "exercise"])  # 只搜题目类
    hits = retrieve(q, chapter="第一章")
当前库以 kind=section 自动整节块为主（细卡就绪后 kinds 过滤即生效）。
"""
from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass


@dataclass
class Hit:
    chunk_id: str
    text: str
    metadata: dict
    score: float
    fusion_score: float = 0.0


# 进程内缓存检索客户端：每次提问重建 Chroma 要 1~3s，缓存后同库热问降到亚秒。
# chromadb 的 local client 多线程并发 query 有风险，因此用锁把 query 串行化
# （embed 网络调用也在这把锁内；本系统单用户场景可接受）。
_CHROMA_LOCK = threading.Lock()
_CACHE_LOCK = threading.Lock()
_DB_CACHE: dict[tuple, object] = {}
_RERANK_CACHE: dict[str, object] = {}


def clear_caches() -> None:
    """索引重建后清理进程内客户端与 rerank 模型之外的检索状态。"""
    with _CACHE_LOCK:
        _DB_CACHE.clear()


def _stable_chunk_id(text: str, metadata: dict) -> str:
    """旧索引缺 chunk_id 时生成稳定 ID，避免多个 PDF 块坍缩成同一键。"""
    cid = str(metadata.get("chunk_id") or "").strip()
    if cid:
        return cid
    basis = "|".join((str(metadata.get("source") or ""),
                      str(metadata.get("page") or ""), text or ""))
    return "legacy-" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:20]


def _get_db(persist_dir=None, collection: str | None = None):
    from langchain_chroma import Chroma

    from edu_agent.config import get_embeddings, load_settings

    s = load_settings()
    pd = persist_dir or s.chroma_db_dir
    return Chroma(
        persist_directory=str(pd),
        embedding_function=get_embeddings(),
        collection_name=collection or "edu_textbook",
    )


def _is_main(md: dict) -> bool:
    """主节（带编号 4.2）vs 旁栏（阅读与思考/信息技术应用/小结 等 section 为空）。"""
    if "is_sidebar" in md:
        return not bool(md.get("is_sidebar"))
    return bool(re.match(r"^\d+\.\d+$", str(md.get("section") or "").strip()))


def _asks_sidebar(query: str) -> bool:
    return any(word in str(query or "") for word in (
        "小结", "复习题", "参考题", "阅读与思考", "探究与发现",
        "信息技术", "数学建模", "文献阅读",
    ))


def _metadata_boost(query: str, md: dict) -> float:
    """对用户明确写出的节号/旁栏标题做小幅确定性加权。"""
    q = re.sub(r"\s+", "", str(query or ""))
    heading = re.sub(r"\s+", "", str(md.get("heading") or ""))
    chapter = re.sub(r"\s+", "", str(md.get("chapter") or ""))
    section = str(md.get("section") or "").strip()
    role = str(md.get("content_role") or "")
    boost = 0.0
    if chapter and chapter in q:
        boost += 0.004
    if section and section in q:
        boost += 0.004
    if len(heading) >= 3 and heading in q:
        boost += 0.006
    for term in ("小结", "复习参考题", "阅读与思考", "探究与发现",
                 "信息技术应用", "数学建模", "文献阅读"):
        if term in q and term in heading:
            boost += 0.004
    if role in {"example", "exercise"} and any(
            term in q for term in ("例题", "练习", "习题", "怎么解", "求解", "计算", "证明")):
        boost += 0.0008
    if role == "concept" and any(
            term in q for term in ("什么是", "定义", "含义", "概念")):
        boost += 0.0005
    if "应用" in heading and any(term in q for term in ("实际问题", "实际应用", "解决实际")):
        boost += 0.003
    return boost


def _coordinate_candidate_ids(query: str, kinds=None, chapter: str | None = None) -> list[str]:
    """把用户明确写出的节号或“某章小结/复习参考题”直接注入候选池。"""
    try:
        from edu_agent import bm25_index, preprocess

        ref = preprocess.parse_refs(query)
        effective_chapter = chapter or ref.chapter
        ids: list[str] = []
        if ref.section:
            ids.extend(bm25_index.select_metadata(
                chapter=effective_chapter, section=ref.section, kinds=kinds,
            ))
        heading = None
        if effective_chapter and "复习参考题" in query:
            chapter_numbers = {
                preprocess.int_to_cn_chapter(i): str(i) for i in range(1, 100)
            }
            number = chapter_numbers.get(effective_chapter)
            heading = "复习参考题" + number if number else "复习参考题"
        elif effective_chapter and "小结" in query:
            heading = "小结"
        if heading:
            ids.extend(bm25_index.select_metadata(
                chapter=effective_chapter, heading_contains=heading, kinds=kinds,
            ))
        # 标题型旁栏没有节号，用户直接复述标题时也应进入确定性候选。
        sidebar_terms = (
            "集合中元素的个数", "函数概念的发展历程", "用计算机绘制函数图象",
            "对数的发明", "互为反函数", "建立函数模型", "三角学与天文学",
        )
        for term in sidebar_terms:
            if term in query:
                ids.extend(bm25_index.select_metadata(
                    chapter=effective_chapter, heading_contains=term, kinds=kinds,
                ))
        return list(dict.fromkeys(ids))
    except Exception:
        return []


def _diverse_top(hits: list[Hit], top_k: int, max_per_parent: int = 2) -> list[Hit]:
    """限制同一父块占位，避免细粒度分块把 Top-K 全部挤成同一段上下文。"""
    selected: list[Hit] = []
    counts: dict[str, int] = {}
    for hit in hits:
        parent = str(hit.metadata.get("parent_id") or hit.chunk_id)
        if counts.get(parent, 0) >= max_per_parent:
            continue
        selected.append(hit)
        counts[parent] = counts.get(parent, 0) + 1
        if len(selected) >= top_k:
            return selected
    if len(selected) < top_k:
        chosen = {h.chunk_id for h in selected}
        selected.extend(h for h in hits if h.chunk_id not in chosen)
    return selected[:top_k]


def expand_neighbors(hits: list[Hit], max_extra: int = 2) -> list[Hit]:
    """在已排序命中后追加相邻语义块，补足跨块定义或推导。

    仅追加可从教材 BM25 缓存可靠定位的同父块，不改变原命中的顺序与分数；因此召回
    排名指标仍以原 Top-K 为准。相邻块带 ``context_neighbor`` 标记，生成端仍可正常引用。
    """
    if not hits or max_extra <= 0:
        return list(hits)
    wanted: list[tuple[str, Hit]] = []
    seen = {
        str(getattr(h, "chunk_id", "") or _stable_chunk_id(
            getattr(h, "text", ""), getattr(h, "metadata", {}) or {}))
        for h in hits
    }
    for hit in hits:
        md = hit.metadata
        parent = str(md.get("parent_id") or "").strip()
        try:
            index = int(md.get("chunk_index") or 0)
            count = int(md.get("chunk_count") or 0)
        except (TypeError, ValueError):
            continue
        if not parent or count <= 1 or index < 1:
            continue
        # 优先补后文，再补前文：定义条件和推导结论更常延续到下一块。
        for neighbor_index in (index + 1, index - 1):
            if not 1 <= neighbor_index <= count:
                continue
            cid = f"{parent}-p{neighbor_index}"
            if cid in seen:
                continue
            wanted.append((cid, hit))
            seen.add(cid)
            if len(wanted) >= max_extra:
                break
        if len(wanted) >= max_extra:
            break
    if not wanted:
        return list(hits)
    try:
        from edu_agent import bm25_index

        docs = bm25_index.lookup([cid for cid, _ in wanted])
    except Exception:
        return list(hits)
    expanded = list(hits)
    for cid, anchor in wanted:
        row = docs.get(cid)
        if not row:
            continue
        text, metadata = row
        md = dict(metadata)
        md["context_neighbor"] = True
        md["neighbor_of"] = str(getattr(anchor, "chunk_id", "") or _stable_chunk_id(
            getattr(anchor, "text", ""), getattr(anchor, "metadata", {}) or {}))
        expanded.append(Hit(
            chunk_id=cid,
            text=text,
            metadata=md,
            score=anchor.score,
            fusion_score=anchor.fusion_score,
        ))
    return expanded


def _rrf(dense_ids: list[str], sparse_ids: list[str], k: int = 60) -> dict:
    """RRF 融合：score = sum 1/(k + rank)。"""
    score: dict[str, float] = {}
    for i, cid in enumerate(dense_ids, 1):
        score[cid] = score.get(cid, 0.0) + 1.0 / (k + i)
    for i, cid in enumerate(sparse_ids, 1):
        score[cid] = score.get(cid, 0.0) + 1.0 / (k + i)
    return score


def _rerank_local(query: str, hits: list[Hit]) -> list[Hit] | None:
    """本地 CrossEncoder（模型目录经 config 收口：RERANK_MODEL_DIR 可覆盖）。"""
    from edu_agent.config import load_settings

    local = str(load_settings().rerank_model_dir)
    try:
        from sentence_transformers import CrossEncoder

        with _CACHE_LOCK:
            model = _RERANK_CACHE.get(local)
            if model is None:
                model = CrossEncoder(local)
                _RERANK_CACHE[local] = model
        pairs = [[query, h.text[:800]] for h in hits]
        scores = model.predict(pairs)
        return [h for h, _ in sorted(zip(hits, scores), key=lambda z: z[1], reverse=True)]
    except Exception:
        return None


def _rerank_remote(query: str, hits: list[Hit]) -> list[Hit] | None:
    """硅基流动 bge-reranker API（key 经 config 收口）。"""
    from edu_agent.config import is_key_ready, load_settings

    s = load_settings()
    if not is_key_ready(s.siliconflow_api_key):
        return None
    try:
        import requests

        r = requests.post(
            s.siliconflow_base_url + "/rerank",
            headers={"Authorization": "Bearer " + s.siliconflow_api_key},
            json={"model": "BAAI/bge-reranker-v2-m3", "query": query,
                  "documents": [h.text[:800] for h in hits], "top_n": len(hits)},
            timeout=30,
        )
        r.raise_for_status()
        order = [d["index"] for d in r.json().get("results", [])]
        return [hits[i] for i in order if i < len(hits)]
    except Exception:
        return None


def rerank(query: str, hits: list[Hit]) -> list[Hit]:
    """第6层 Rerank 适配层（双通道，默认关）。

    启用：RERANK_ON=1 后按 RERANK_PROVIDER 尝试：
      1) local        -> 本地 model_cache/bge-reranker-v2-m3/（文件就绪时）
      2) siliconflow  -> 硅基流动 API（.env 配好 key 时）
    均不可用则原序返回（不影响主链路）。配置统一走 config.load_settings。
    """
    from edu_agent.config import load_settings

    s = load_settings()
    if not s.rerank_on or len(hits) < 2:
        return hits
    provider = s.rerank_provider
    out = None
    if provider == "local":
        out = _rerank_local(query, hits)
    elif provider == "siliconflow":
        out = _rerank_remote(query, hits)
    return out or hits


def retrieve(query: str, kinds: list[str] | None = None, top_k: int = 3,
             chapter: str | None = None, min_score: float | None = None,
             prefer_main: bool = True, hybrid: bool = True,
             source: str | None = None, persist_dir=None, collection: str | None = None) -> list[Hit]:
    """检索并返回命中 chunk（含坐标元数据），按相关度降序。

    第5层：多路召回（Dense 向量 + BM25 稀疏）经 RRF 融合；
    第6层：可选 rerank（rerank()）；主节（编号节）硬优先于旁栏。
    source/persist_dir/collection：用于用户上传 PDF 独立库（跳过 BM25）。
    """
    from edu_agent.config import load_settings

    # Chroma 客户端缓存（同库同 collection 复用实例）
    s = load_settings()
    cache_key = (str(persist_dir or s.chroma_db_dir), collection or "edu_textbook")
    with _CACHE_LOCK:
        if cache_key not in _DB_CACHE:
            _DB_CACHE[cache_key] = _get_db(persist_dir, collection)
        db = _DB_CACHE[cache_key]

    where: dict | None = {}
    if kinds:
        where["kind"] = {"$in": list(kinds)} if len(kinds) > 1 else kinds[0]
    if chapter:
        where["chapter"] = chapter
    if source:
        where["source"] = source
    if not where:
        where = None
    if persist_dir is not None:
        hybrid = False
    pool = max(top_k * 6, 24)
    # chromadb local client 并发 query 有风险：串行化（embed 网络调用含在内）
    with _CHROMA_LOCK:
        raw = db.similarity_search_with_score(query, k=pool, filter=where)
    dense_map: dict[str, Hit] = {}
    dense_ids: list[str] = []
    for d, score in raw:
        hit = Hit(
            chunk_id=_stable_chunk_id(d.page_content, d.metadata),
            text=d.page_content,
            metadata=dict(d.metadata),
            score=float(score),
        )
        dense_map[hit.chunk_id] = hit
        dense_ids.append(hit.chunk_id)

    if hybrid:
        try:
            from edu_agent import bm25_index

            sparse_ids = bm25_index.search(query, top_k=pool, kinds=kinds, chapter=chapter)
            coordinate_ids = _coordinate_candidate_ids(query, kinds=kinds, chapter=chapter)
            sparse_ids = coordinate_ids + [cid for cid in sparse_ids if cid not in coordinate_ids]
            sparse_docs = bm25_index.lookup(sparse_ids)
        except Exception:
            sparse_ids = []
            sparse_docs = {}
        candidates = dict(dense_map)
        for cid in sparse_ids:
            if cid in candidates or cid not in sparse_docs:
                continue
            text, md = sparse_docs[cid]
            candidates[cid] = Hit(chunk_id=cid, text=text, metadata=dict(md), score=1.0)
        fused = _rrf(dense_ids, sparse_ids)
        main_boost = 0.0 if _asks_sidebar(query) or not prefer_main else 0.0006
        coordinate_set = set(coordinate_ids)
        for cid, hit in candidates.items():
            hit.fusion_score = (fused.get(cid, 0.0)
                                + (main_boost if _is_main(hit.metadata) else 0.0)
                                + (0.02 if cid in coordinate_set else 0.0)
                                + _metadata_boost(query, hit.metadata))
        hits = sorted(candidates.values(), key=lambda h: (-h.fusion_score, h.score))
    else:
        hits = list(dense_map.values())
        # 主节只做轻量软偏置，不再把所有旁栏无条件压到候选末尾。
        if prefer_main and not _asks_sidebar(query):
            hits.sort(key=lambda h: h.score - (0.015 if _is_main(h.metadata) else 0.0))

    hits = rerank(query, hits[:pool])
    hits = _diverse_top(hits, top_k)
    if min_score is not None:
        hits = [h for h in hits if h.score <= min_score]
    return hits
