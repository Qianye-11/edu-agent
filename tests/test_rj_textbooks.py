from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from edu_agent.ontology import Ontology, build
from edu_agent.rj_textbooks import chunks_of, discover, plan


class RjTextbookCorpusTests(unittest.TestCase):
    def _root(self) -> Path:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        doc = (root / "markdown" / "人教A版高中数学【电子课本】" / "book-id")
        doc.mkdir(parents=True)
        (doc / "数学必修一.md").write_text(
            "# 数学\n\n![](images/cover.jpg)\n\n## 第一章 集合\n"
            "## 1.1 集合的概念\n集合由确定的对象组成，研究对象需要具有确定性，并且可以用恰当的方法表示。\n\n"
            "## 思考\n微信扫码 免费下载\n集合能否由描述法表示，并说明其中元素的共同特征？\n",
            encoding="utf-8",
        )
        return root

    def test_chunks_keep_rag_metadata_but_drop_image_and_advertising(self):
        source = discover(self._root())[0]
        chunks = chunks_of(source, target_chars=80, overlap_chars=0)
        self.assertGreaterEqual(len(chunks), 2)
        self.assertTrue(all("images/cover.jpg" not in c.text for c in chunks))
        self.assertTrue(all("微信扫码" not in c.text for c in chunks))
        target = next(c for c in chunks if c.metadata["section"] == "1.1")
        self.assertEqual(target.metadata["subject"], "数学")
        self.assertEqual(target.metadata["edition"], "人教A版")
        self.assertEqual(target.metadata["kind"], "textbook")
        self.assertEqual(target.metadata["chapter"], "第一章 集合")

    def test_plan_and_ontology_only_emit_verifiable_structure(self):
        root = self._root()
        self.assertEqual(plan(root)["sources"], 1)
        graph = build(root)
        ontology = Ontology(graph)
        matches = ontology.search("集合的概念")
        self.assertTrue(any(node["type"] == "section" for node in matches))
        self.assertTrue(all(edge["relation"] == "包含" for edge in ontology.edges))
        json.dumps(graph, ensure_ascii=False)
