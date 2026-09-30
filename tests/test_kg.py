# -*- coding: utf-8 -*-
"""知识星图数据、关系语义与 API 回归测试。"""
from __future__ import annotations

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from edu_agent.kg import KnowledgeGraph, get_kg  # noqa: E402


class TestKnowledgeGraph(unittest.TestCase):
    def test_current_graph_is_valid_and_covers_book(self):
        graph = get_kg()
        report = graph.validate()
        self.assertTrue(report["ok"])
        self.assertGreaterEqual(report["nodes"], 50)
        chapters = graph.get_graph()["meta"]["chapter_counts"]
        for chapter in ("第一章", "第二章", "第三章", "第四章", "第五章"):
            self.assertGreater(chapters.get(chapter, 0), 0)

    def test_node_info_keeps_relation_types_separate(self):
        info = get_kg().get_node_info("function")
        self.assertIn("函数的概念", {item["name"] for item in info["contains"]})
        self.assertIn("指数函数与对数函数", {item["name"] for item in info["enables"]})
        self.assertNotIn("函数的概念", {item["name"] for item in info["enables"]})
        self.assertEqual(info["page"], 60)

    def test_wiki_and_page_metadata(self):
        info = get_kg().get_node_info("even_func")
        self.assertEqual(info["wiki_slug"], "偶函数")
        self.assertEqual(info["section"], "3.2")
        self.assertEqual(info["page"], 82)

    def test_search_uses_aliases(self):
        hits = get_kg().search("换底公式")
        self.assertEqual(hits[0]["id"], "log_rules")

    def test_related_subgraph_contains_focus(self):
        related = get_kg().get_related("trig_concept", hops=1)
        self.assertIn("trig_concept", {node["id"] for node in related["nodes"]})
        self.assertTrue(related["links"])

    def test_rejects_dangling_edge(self):
        with self.assertRaisesRegex(ValueError, "悬空端点"):
            KnowledgeGraph({"nodes": [{"id": "a", "name": "A", "chapter": "第一章"}],
                            "edges": [{"source": "a", "target": "missing", "relation": "包含"}]})

    def test_rejects_prerequisite_cycle(self):
        data = {
            "nodes": [{"id": "a", "name": "A", "chapter": "第一章"},
                      {"id": "b", "name": "B", "chapter": "第一章"}],
            "edges": [{"source": "a", "target": "b", "relation": "前置知识点"},
                      {"source": "b", "target": "a", "relation": "前置知识点"}],
        }
        with self.assertRaisesRegex(ValueError, "存在环"):
            KnowledgeGraph(data)

    def test_http_health_and_node_endpoints(self):
        from fastapi.testclient import TestClient
        from edu_agent.web_server import app

        client = TestClient(app)
        health = client.get("/api/kg/health")
        self.assertEqual(health.status_code, 200)
        self.assertTrue(health.json()["ok"])
        self.assertEqual(health.json()["nodes"], 58)
        node = client.get("/api/kg/node/function").json()
        self.assertEqual(node["section"], "3.1")
        self.assertTrue(node["contains"])


if __name__ == "__main__":
    unittest.main()
