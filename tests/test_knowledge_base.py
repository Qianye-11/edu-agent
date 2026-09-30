"""知识库颗粒度、混合召回和增量同步回归。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from edu_agent.chunking import semantic_chunks
from edu_agent.ingest import Chunk, _split_long_sections, infer_content_role, to_documents
from edu_agent import bm25_index, knowledge_health, retrieve, wiki_index


class TestSemanticChunking(unittest.TestCase):
    def test_content_role_inference(self):
        self.assertEqual(infer_content_role("一般地，我们把这个式子叫做幂函数。"), "concept")
        self.assertEqual(infer_content_role("例2 求函数的定义域。"), "example")
        self.assertEqual(infer_content_role("完成下列练习题。", heading="复习参考题5"), "exercise")
        self.assertEqual(infer_content_role("数学史材料", is_sidebar=True), "sidebar")
        self.assertEqual(infer_content_role("研究函数图象的变化规律。"), "explanation")

    def test_chunks_are_fine_grained_overlapping_and_nonempty(self):
        text = "\n".join(f"第{i}段说明函数性质，并给出判断方法。" + "甲" * 90 for i in range(12))
        chunks = semantic_chunks(text, target_chars=320, overlap_chars=60)
        self.assertGreater(len(chunks), 3)
        self.assertTrue(all(0 < len(c) <= 320 for c in chunks))
        # 上一块尾部语义会进入下一块，避免边界处丢上下文。
        self.assertTrue(any(a.splitlines()[-1] in b for a, b in zip(chunks, chunks[1:])))

    def test_section_chunks_have_parent_and_order_metadata(self):
        source = Chunk(chunk_id="math-1", kind="section", text="乙" * 1500,
                       subject="数学", book="必修一", version="v", chapter="第一章")
        chunks = _split_long_sections([source], limit=500, overlap=50)
        self.assertGreaterEqual(len(chunks), 3)
        self.assertEqual({c.parent_id for c in chunks}, {"math-1"})
        self.assertEqual([c.chunk_index for c in chunks], list(range(1, len(chunks) + 1)))
        self.assertTrue(all(c.chunk_count == len(chunks) for c in chunks))
        docs = to_documents(chunks)
        self.assertTrue(all(len(d.metadata["content_hash"]) == 64 for d in docs))
        self.assertTrue(all(d.metadata["content_role"] for d in docs))

    def test_damaged_subsection_marker_recovers_known_section(self):
        main = Chunk(chunk_id="main", kind="section", text="指数函数定义。",
                     subject="数学", book="必修一", version="v", chapter="第四章",
                     section="4.2", heading="指数函数")
        resumed = Chunk(chunk_id="insert", kind="section",
                        text="放射性材料。\n422!\n继续研究指数函数的图象和性质。",
                        subject="数学", book="必修一", version="v", chapter="第四章",
                        heading="阅读与思考")
        chunks = _split_long_sections([main, resumed], limit=300, overlap=30)
        recovered = next(c for c in chunks if c.parent_id == "insert")
        self.assertEqual(recovered.section, "4.2")
        self.assertEqual(recovered.heading, "指数函数")
        self.assertFalse(recovered.is_sidebar)


class TestKnowledgeManifest(unittest.TestCase):
    def test_source_digest_changes_with_source_content(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as d:
            source = Path(d) / "source.md"
            source.write_text("版本一", encoding="utf-8")
            first = knowledge_health.source_digest(knowledge_health.source_records([source]))
            source.write_text("版本二", encoding="utf-8")
            second = knowledge_health.source_digest(knowledge_health.source_records([source]))
        self.assertNotEqual(first, second)


class _FakeDb:
    def __init__(self, rows):
        self.rows = rows

    def similarity_search_with_score(self, *_args, **_kwargs):
        return self.rows


class TestRetrievalUnion(unittest.TestCase):
    def tearDown(self):
        retrieve.clear_caches()
        bm25_index.clear_cache()

    def test_legacy_pdf_chunks_without_chunk_id_do_not_collapse(self):
        rows = [
            (SimpleNamespace(page_content="第一页知识", metadata={"source": "s", "page": 1}), 0.2),
            (SimpleNamespace(page_content="第二页知识", metadata={"source": "s", "page": 2}), 0.3),
        ]
        key = ("fake-pdf", "user_pdfs")
        retrieve._DB_CACHE[key] = _FakeDb(rows)
        hits = retrieve.retrieve("知识", top_k=2, hybrid=False, prefer_main=False,
                                 persist_dir="fake-pdf", collection="user_pdfs")
        self.assertEqual(len(hits), 2)
        self.assertEqual(len({h.chunk_id for h in hits}), 2)

    def test_bm25_only_candidate_is_kept_in_hybrid_union(self):
        dense = SimpleNamespace(page_content="向量候选", metadata={"chunk_id": "dense", "section": "1.1"})
        # 使用默认库 key，避免触发真实 Chroma 初始化。
        from edu_agent.config import load_settings
        key = (str(load_settings().chroma_db_dir), "edu_textbook")
        retrieve._DB_CACHE[key] = _FakeDb([(dense, 0.4)])
        old_search, old_lookup = bm25_index.search, bm25_index.lookup
        try:
            bm25_index.search = lambda *_a, **_k: ["sparse"]
            bm25_index.lookup = lambda _ids: {"sparse": ("精确关键词", {"chunk_id": "sparse", "section": "1.2"})}
            hits = retrieve.retrieve("精确关键词", top_k=2, hybrid=True, prefer_main=False)
        finally:
            bm25_index.search, bm25_index.lookup = old_search, old_lookup
        self.assertEqual({h.chunk_id for h in hits}, {"dense", "sparse"})

    def test_metadata_boost_understands_heading_and_role(self):
        exact = retrieve._metadata_boost(
            "第一章复习参考题怎么解", {"chapter": "第一章", "heading": "复习参考题1", "content_role": "exercise"})
        generic = retrieve._metadata_boost(
            "第一章复习参考题怎么解", {"chapter": "第二章", "heading": "函数", "content_role": "explanation"})
        self.assertGreater(exact, generic)

    def test_explicit_section_and_chapter_sidebar_inject_candidates(self):
        old_select = bm25_index.select_metadata
        calls = []
        try:
            def fake_select(**kwargs):
                calls.append(kwargs)
                return ["matched"]
            bm25_index.select_metadata = fake_select
            self.assertEqual(retrieve._coordinate_candidate_ids("请解释教材4.2节"), ["matched"])
            self.assertEqual(retrieve._coordinate_candidate_ids("第二章复习参考题"), ["matched"])
        finally:
            bm25_index.select_metadata = old_select
        self.assertEqual(calls[0]["section"], "4.2")
        self.assertEqual(calls[1]["chapter"], "第二章")
        self.assertEqual(calls[1]["heading_contains"], "复习参考题2")

    def test_diverse_top_limits_one_parent_from_filling_results(self):
        hits = [
            retrieve.Hit(str(i), str(i), {"parent_id": "same" if i < 3 else "other"}, i / 10)
            for i in range(4)
        ]
        selected = retrieve._diverse_top(hits, top_k=3, max_per_parent=2)
        self.assertEqual([h.chunk_id for h in selected], ["0", "1", "3"])

    def test_neighbor_expansion_appends_context_without_reordering_hits(self):
        primary = retrieve.Hit(
            "parent-p2", "正文二",
            {"parent_id": "parent", "chunk_index": 2, "chunk_count": 4}, 0.2,
        )
        old_lookup = bm25_index.lookup
        try:
            bm25_index.lookup = lambda ids: {
                cid: ("相邻正文", {"chunk_id": cid, "parent_id": "parent"}) for cid in ids
            }
            expanded = retrieve.expand_neighbors([primary], max_extra=2)
        finally:
            bm25_index.lookup = old_lookup
        self.assertEqual([h.chunk_id for h in expanded], ["parent-p2", "parent-p3", "parent-p1"])
        self.assertTrue(all(h.metadata.get("context_neighbor") for h in expanded[1:]))


class _FakeWikiCollection:
    def __init__(self, chunk_id):
        self.chunk_id = chunk_id
        self.upserts = []

    def count(self):
        return 1

    def get(self, include=None):
        return {"ids": [self.chunk_id], "metadatas": [{"sha1": "old"}]}

    def delete(self, ids):
        pass

    def upsert(self, **kwargs):
        self.upserts.append(kwargs)


class TestWikiIncrementalSync(unittest.TestCase):
    def test_changed_existing_chunk_uses_upsert(self):
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as d:
            wiki = Path(d)
            concepts = wiki / "concepts"
            concepts.mkdir()
            page = concepts / "函数.md"
            page.write_text("---\nslug: 函数\ntitle: 函数\npages: [1]\n---\n## 定义\n函数的新定义正文。", encoding="utf-8")
            chunk = wiki_index.chunks_of(page)[0]
            fake = _FakeWikiCollection(chunk["id"])
            old_wiki, old_get, old_embed = wiki_index.WIKI, wiki_index.get_col, wiki_index.embed
            try:
                wiki_index.WIKI = wiki
                wiki_index.get_col = lambda rebuild=False: fake
                wiki_index.embed = lambda texts: [[0.1, 0.2] for _ in texts]
                result = wiki_index.sync(quiet=True)
            finally:
                wiki_index.WIKI, wiki_index.get_col, wiki_index.embed = old_wiki, old_get, old_embed
            self.assertEqual(result["added"], 1)
            self.assertEqual(len(fake.upserts), 1)


if __name__ == "__main__":
    unittest.main()
