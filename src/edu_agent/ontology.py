"""教材本体：保存可审核的结构与关系，绝不保存教材原文。

本体由 ``rj_textbooks`` 的标题结构自动生成最小层级（版本/学科/册别/章/节）。
跨学科概念、前置知识和同义关系应写入人工维护的关系文件后再合并，避免模型猜关系。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

from edu_agent.rj_textbooks import SourceDocument, discover, _heading_units

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PATH = ROOT / "content" / "ontology" / "rj_textbooks.json"
ALLOWED_RELATIONS = {"包含", "前置知识点", "相关", "同义版本", "应用于"}


def _id(kind: str, value: str) -> str:
    digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:16]
    return f"{kind}-{digest}"


def _node(node_id: str, node_type: str, name: str, **metadata: str) -> dict:
    return {"id": node_id, "type": node_type, "name": name, **{
        key: value for key, value in metadata.items() if value
    }}


def build(source_root: Path) -> dict[str, Any]:
    """从教材标题构建保守的本体骨架。

    自动层只建立能从文件直接验证的 ``包含`` 关系；不自动臆测“前置知识点”。
    """
    nodes: dict[str, dict] = {}
    edges: set[tuple[str, str, str]] = set()
    for source in discover(source_root):
        edition_key = source.edition
        edition_id = _id("edition", edition_key)
        nodes[edition_id] = _node(edition_id, "edition", source.edition)
        subject_key = f"{source.edition}/{source.subject}"
        subject_id = _id("subject", subject_key)
        nodes[subject_id] = _node(subject_id, "subject", source.subject, edition=source.edition)
        edges.add((edition_id, subject_id, "包含"))
        book_key = f"{subject_key}/{source.source_id}"
        book_id = _id("book", book_key)
        nodes[book_id] = _node(book_id, "book", source.book, subject=source.subject,
                               edition=source.edition, source=source.source_id,
                               source_path=source.source_path)
        edges.add((subject_id, book_id, "包含"))
        chapter_id = ""
        seen_sections: set[str] = set()
        for chapter, section, heading, _ in _heading_units(source):
            if chapter:
                chapter_key = f"{book_id}/{chapter}"
                chapter_id = _id("chapter", chapter_key)
                nodes[chapter_id] = _node(chapter_id, "chapter", chapter, book=source.book,
                                          subject=source.subject, edition=source.edition,
                                          source=source.source_id)
                edges.add((book_id, chapter_id, "包含"))
            if section and chapter_id and section not in seen_sections:
                section_key = f"{chapter_id}/{section}"
                section_id = _id("section", section_key)
                nodes[section_id] = _node(section_id, "section", heading, section=section,
                                          chapter=chapter, book=source.book,
                                          subject=source.subject, edition=source.edition,
                                          source=source.source_id)
                edges.add((chapter_id, section_id, "包含"))
                seen_sections.add(section)
    return {
        "meta": {
            "schema_version": 1,
            "description": "人教版教材本体骨架；仅含可从教材目录验证的结构与关系。",
            "source_policy": "raw_text_stays_in_rag",
        },
        "nodes": sorted(nodes.values(), key=lambda node: (node["type"], node["name"], node["id"])),
        "edges": [
            {"source": source, "target": target, "relation": relation, "evidence": "heading"}
            for source, target, relation in sorted(edges)
        ],
    }


def write(source_root: Path, output: Path = DEFAULT_PATH) -> dict[str, Any]:
    data = build(source_root)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"path": str(output), "nodes": len(data["nodes"]), "edges": len(data["edges"])}


class Ontology:
    """面向路由与审核的只读查询，不承担向量检索。"""
    def __init__(self, data: dict[str, Any]) -> None:
        self.nodes = {str(node["id"]): dict(node) for node in data.get("nodes") or []}
        self.edges = [dict(edge) for edge in data.get("edges") or []]
        self.validate()

    @classmethod
    def from_file(cls, path: Path = DEFAULT_PATH) -> "Ontology":
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def validate(self) -> None:
        if len(self.nodes) != len({node["id"] for node in self.nodes.values()}):
            raise ValueError("本体节点 ID 重复")
        for edge in self.edges:
            if edge.get("source") not in self.nodes or edge.get("target") not in self.nodes:
                raise ValueError("本体存在悬空关系")
            if edge.get("relation") not in ALLOWED_RELATIONS:
                raise ValueError(f"未知本体关系：{edge.get('relation')}")
        graph: dict[str, list[str]] = defaultdict(list)
        indegree = {node_id: 0 for node_id in self.nodes}
        for edge in self.edges:
            if edge["relation"] == "前置知识点":
                graph[edge["source"]].append(edge["target"])
                indegree[edge["target"]] += 1
        queue = deque(node_id for node_id, degree in indegree.items() if degree == 0)
        visited = 0
        while queue:
            node_id = queue.popleft()
            visited += 1
            for target in graph[node_id]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    queue.append(target)
        if visited != len(indegree):
            raise ValueError("本体前置知识关系存在环")

    def search(self, query: str, limit: int = 12) -> list[dict]:
        terms = [term for term in re.split(r"\s+", query.strip().lower()) if term]
        ranked: list[tuple[int, dict]] = []
        for node in self.nodes.values():
            haystack = " ".join(str(node.get(key) or "") for key in (
                "name", "subject", "edition", "book", "chapter", "section")).lower()
            score = sum(3 if term == str(node.get("name") or "").lower() else 1
                        for term in terms if term in haystack)
            if score:
                ranked.append((score, node))
        ranked.sort(key=lambda item: (-item[0], item[1]["type"], item[1]["name"]))
        return [node for _, node in ranked[:limit]]


def main() -> int:
    parser = argparse.ArgumentParser(description="由人教版 Markdown 建立教材本体骨架")
    parser.add_argument("--source-root", required=True, help="包含 markdown/ 的教材根目录")
    parser.add_argument("--output", default=str(DEFAULT_PATH), help="本体 JSON 输出路径")
    args = parser.parse_args()
    print(json.dumps(write(Path(args.source_root), Path(args.output)), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
