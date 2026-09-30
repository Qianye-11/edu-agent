from __future__ import annotations

from fastapi.testclient import TestClient

from edu_agent import lesson_library
from edu_agent.web_server import app


def _isolated_library(tmp_path, monkeypatch):
    root = tmp_path / "lesson_library"
    monkeypatch.setattr(lesson_library, "LIB_DIR", root)
    monkeypatch.setattr(lesson_library, "TRASH_DIR", root / "_trash")
    return root


def test_defaults_are_created_and_searchable(tmp_path, monkeypatch):
    _isolated_library(tmp_path, monkeypatch)

    items = lesson_library.list_entries(query="探究")

    assert len(items) == 1
    assert items[0]["id"] == "builtin-inquiry"
    assert items[0]["locked"] is True


def test_save_context_and_soft_delete(tmp_path, monkeypatch):
    root = _isolated_library(tmp_path, monkeypatch)
    saved = lesson_library.save({
        "title": "函数单调性规范课例",
        "subject": "高中数学",
        "grade": "高一",
        "lesson_type": "新授课",
        "category": "我的教案",
        "tags": ["函数", "单调性"],
        "html": "<h1>函数单调性</h1><p>用定义证明函数在区间上的单调性。</p>",
    })

    item = lesson_library.get(saved["id"])
    context, used = lesson_library.context([saved["id"]])
    result = lesson_library.delete(saved["id"])

    assert item and item["title"] == "函数单调性规范课例"
    assert "用定义证明" in context
    assert used[0]["id"] == saved["id"]
    assert result == {"ok": True, "id": saved["id"], "recoverable": True}
    assert lesson_library.get(saved["id"]) is None
    assert list((root / "_trash").glob("*.json"))


def test_lesson_library_http_api_round_trip(tmp_path, monkeypatch):
    _isolated_library(tmp_path, monkeypatch)
    client = TestClient(app)

    initial = client.get("/api/lesson-library")
    created = client.post("/api/lesson-library", json={
        "title": "跨学科项目教案",
        "subject": "科学",
        "grade": "八年级",
        "lesson_type": "项目课",
        "category": "我的教案",
        "tags": ["项目化", "多页"],
        "html": "<h1>跨学科项目</h1><div class=\"lesson-page-break\"></div><p>第二页</p>",
    })
    entry_id = created.json()["item"]["id"]
    fetched = client.get(f"/api/lesson-library/{entry_id}")
    deleted = client.delete(f"/api/lesson-library/{entry_id}")

    assert initial.status_code == 200
    assert initial.json()["count"] == 3
    assert created.status_code == 200
    assert fetched.status_code == 200
    assert "lesson-page-break" in fetched.json()["item"]["html"]
    assert deleted.json() == {"ok": True, "id": entry_id, "recoverable": True}
