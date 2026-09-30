"""教材知识图谱服务。

图谱数据放在 ``content/kg/graph.json``，代码只负责校验、查询和关系解释。
这样教材知识结构可以独立审核，不再与服务代码绑死。
"""
from __future__ import annotations

from collections import defaultdict, deque
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
GRAPH_FILE = ROOT / "content" / "kg" / "graph.json"
ALLOWED_RELATIONS = {"包含", "前置知识点", "可推导", "相关"}


def _read_graph(path: Path = GRAPH_FILE) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"知识图谱数据不存在：{path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("知识图谱根节点必须是对象")
    return data


class KnowledgeGraph:
    def __init__(self, data: dict[str, Any] | None = None) -> None:
        source = data if data is not None else _read_graph()
        self.meta = dict(source.get("meta") or {})
        self.nodes = [dict(node) for node in source.get("nodes") or []]
        self.edges = [dict(edge) for edge in source.get("edges") or []]
        self.node_map = {str(node.get("id") or ""): node for node in self.nodes}
        self.validate()

    def validate(self) -> dict[str, Any]:
        """校验图数据；服务启动时即暴露重复 ID、悬空边和前置环。"""
        errors: list[str] = []
        ids = [str(node.get("id") or "").strip() for node in self.nodes]
        if any(not item for item in ids):
            errors.append("存在空节点 ID")
        duplicates = sorted({item for item in ids if ids.count(item) > 1})
        if duplicates:
            errors.append("重复节点 ID：" + "、".join(duplicates))
        for node in self.nodes:
            if not str(node.get("name") or "").strip():
                errors.append(f"节点 {node.get('id')} 缺少名称")
            if not str(node.get("chapter") or "").strip():
                errors.append(f"节点 {node.get('id')} 缺少章节")
        valid_ids = set(ids)
        for index, edge in enumerate(self.edges):
            source, target = str(edge.get("source") or ""), str(edge.get("target") or "")
            relation = str(edge.get("relation") or "")
            if source not in valid_ids or target not in valid_ids:
                errors.append(f"关系 {index} 含悬空端点：{source}->{target}")
            if source == target:
                errors.append(f"关系 {index} 不能自环：{source}")
            if relation not in ALLOWED_RELATIONS:
                errors.append(f"关系 {index} 类型无效：{relation}")
        cycle = self._prerequisite_cycle()
        if cycle:
            errors.append("前置知识关系存在环：" + " -> ".join(cycle))
        if errors:
            raise ValueError("；".join(errors))
        return {"ok": True, "nodes": len(self.nodes), "edges": len(self.edges)}

    def _prerequisite_cycle(self) -> list[str]:
        graph: dict[str, list[str]] = defaultdict(list)
        indegree = {item: 0 for item in self.node_map}
        for edge in self.edges:
            if edge.get("relation") != "前置知识点":
                continue
            source, target = str(edge.get("source")), str(edge.get("target"))
            if source in indegree and target in indegree:
                graph[source].append(target)
                indegree[target] += 1
        queue = deque(item for item, degree in indegree.items() if degree == 0)
        seen = 0
        while queue:
            current = queue.popleft()
            seen += 1
            for target in graph[current]:
                indegree[target] -= 1
                if indegree[target] == 0:
                    queue.append(target)
        return [] if seen == len(indegree) else [item for item, degree in indegree.items() if degree > 0]

    def _degree(self) -> dict[str, int]:
        degree: dict[str, int] = defaultdict(int)
        for edge in self.edges:
            degree[str(edge["source"])] += 1
            degree[str(edge["target"])] += 1
        return degree

    def _public_node(self, node: dict[str, Any], degree: dict[str, int] | None = None) -> dict[str, Any]:
        out = {
            "id": node["id"], "name": node["name"], "category": node["chapter"],
            "chapter": node["chapter"], "section": node.get("section") or "",
            "page": node.get("page"), "wiki_slug": node.get("wiki_slug") or "",
            "description": node.get("description") or "",
        }
        if degree is not None:
            out["degree"] = degree.get(str(node["id"]), 0)
        return out

    def get_graph(self) -> dict[str, Any]:
        degree = self._degree()
        chapters = defaultdict(int)
        for node in self.nodes:
            chapters[str(node["chapter"])] += 1
        return {
            "meta": {**self.meta, "node_count": len(self.nodes), "edge_count": len(self.edges),
                     "chapter_counts": dict(chapters)},
            "nodes": [self._public_node(node, degree) for node in self.nodes],
            "links": self.edges,
        }

    def _items(self, ids: set[str]) -> list[dict[str, Any]]:
        return [self._public_node(node) for node in self.nodes if str(node["id"]) in ids]

    def _related_ids(self, node_id: str, relation: str, incoming: bool) -> set[str]:
        if incoming:
            return {str(edge["source"]) for edge in self.edges
                    if edge["target"] == node_id and edge["relation"] == relation}
        return {str(edge["target"]) for edge in self.edges
                if edge["source"] == node_id and edge["relation"] == relation}

    def get_node_info(self, node_id: str) -> dict[str, Any]:
        node = self.node_map.get(node_id)
        if node is None:
            return {"id": node_id, "name": "", "chapter": "", "degree": 0,
                    "prerequisites": [], "enables": [], "contains": [], "belongs_to": [],
                    "derives": [], "derived_from": [], "related": [], "successors": []}
        related = self._related_ids(node_id, "相关", True) | self._related_ids(node_id, "相关", False)
        result = self._public_node(node, self._degree())
        result.update({
            "prerequisites": self._items(self._related_ids(node_id, "前置知识点", True)),
            "enables": self._items(self._related_ids(node_id, "前置知识点", False)),
            "contains": self._items(self._related_ids(node_id, "包含", False)),
            "belongs_to": self._items(self._related_ids(node_id, "包含", True)),
            "derives": self._items(self._related_ids(node_id, "可推导", False)),
            "derived_from": self._items(self._related_ids(node_id, "可推导", True)),
            "related": self._items(related),
        })
        # 兼容旧客户端；新版前端使用 enables，不再把“包含”误称为后继。
        result["successors"] = result["enables"]
        return result

    def get_related(self, node_id: str, hops: int = 1) -> dict[str, Any]:
        """返回指定节点周围的子图，供星图点击和搜索时聚焦。"""
        if node_id not in self.node_map:
            return {"nodes": [], "links": []}
        keep = {node_id}
        related_edges: list[dict[str, str]] = []
        for _ in range(max(1, min(hops, 3))):
            found: list[dict[str, str]] = []
            expanded = set(keep)
            for edge in self.edges:
                if edge["source"] in keep or edge["target"] in keep:
                    found.append(edge)
                    expanded.add(str(edge["source"]))
                    expanded.add(str(edge["target"]))
            related_edges = found
            keep = expanded
        degree = self._degree()
        return {"nodes": [self._public_node(node, degree) for node in self.nodes
                           if str(node["id"]) in keep],
                "links": related_edges}

    def search(self, query: str) -> list[dict[str, Any]]:
        query = query.strip().lower()
        if not query:
            return []
        ranked = []
        for node in self.nodes:
            name = str(node["name"]).lower()
            haystack = " ".join([str(node.get("id") or ""), name,
                                 str(node.get("section") or ""),
                                 " ".join(str(x) for x in node.get("aliases") or [])]).lower()
            if query not in haystack:
                continue
            score = 3 if query == name else 2 if name.startswith(query) else 1
            ranked.append((score, self._public_node(node)))
        ranked.sort(key=lambda item: (-item[0], str(item[1]["name"])))
        return [item for _, item in ranked[:12]]


_KG = KnowledgeGraph()


def get_kg() -> KnowledgeGraph:
    return _KG
