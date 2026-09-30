"""细粒度检索评测：按概念、方法、旁栏、显式坐标分别输出 Recall@3/MRR。"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from edu_agent.retrieve import retrieve  # noqa: E402


DATA_PATH = Path(__file__).with_name("golden_questions_v2.json")


def matches(hit, expect: dict) -> bool:
    """expect 中给出的字段全部满足才算命中。"""
    md = hit.metadata
    if expect.get("chapter") and md.get("chapter") != expect["chapter"]:
        return False
    if expect.get("section") and str(md.get("section") or "") != str(expect["section"]):
        return False
    needle = str(expect.get("heading_contains") or "")
    if needle and needle not in str(md.get("heading") or ""):
        return False
    if expect.get("content_role") and md.get("content_role") != expect["content_role"]:
        return False
    return True


def metric(rows: list[dict]) -> tuple[float, float]:
    if not rows:
        return 0.0, 0.0
    recall = sum(row["rank"] is not None for row in rows) / len(rows)
    mrr = sum(1.0 / row["rank"] for row in rows if row["rank"]) / len(rows)
    return recall, mrr


def main() -> int:
    data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    rows: list[dict] = []
    groups: dict[str, list[dict]] = defaultdict(list)
    for index, item in enumerate(data["questions"], 1):
        hits = retrieve(item["q"], top_k=3)
        rank = next((r for r, hit in enumerate(hits, 1) if matches(hit, item["expect"])), None)
        top = "-"
        if hits:
            md = hits[0].metadata
            top = " ".join(filter(None, (str(md.get("chapter") or ""),
                                          str(md.get("section") or ""),
                                          str(md.get("heading") or ""))))
        row = {"index": index, "category": item["category"], "q": item["q"],
               "rank": rank, "top": top}
        rows.append(row)
        groups[item["category"]].append(row)
        print(f"{index:>2} {'✅' if rank else '❌'} [{item['category']:<10}] {item['q'][:24]:<24} -> {top}")

    recall, mrr = metric(rows)
    print(f"\n总体 Recall@3={recall:.3f} ({sum(r['rank'] is not None for r in rows)}/{len(rows)})  MRR={mrr:.3f}")
    category_floor = float(data["thresholds"]["category_recall_at_3"])
    category_ok = True
    for name, category_rows in groups.items():
        cat_recall, cat_mrr = metric(category_rows)
        category_ok = category_ok and cat_recall >= category_floor
        print(f"{name:<10} Recall@3={cat_recall:.3f} ({sum(r['rank'] is not None for r in category_rows)}/{len(category_rows)})  MRR={cat_mrr:.3f}")
    return 0 if recall >= float(data["thresholds"]["recall_at_3"]) and category_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
