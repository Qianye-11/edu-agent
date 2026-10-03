# -*- coding: utf-8 -*-
"""教师工作台：教师身份配置 + 课程表 + 学校校历（JSON 持久化，本地读写）。

数据落 <data_dir>/workbench.json。纯标准库，无外部依赖。
供 apple-ui（5181）的「教师工作台」视图调用。

数据形状：
  {
    "profile": {"name","school","grade_level","grade","subject"},
    "courses": [{"id","name","subject","weekday"(1-7),"period","grade","room","color"}],
    "calendar": {"term","start","events":[{"id","date","title","type"}]}
  }
"""
from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path

from edu_agent.config import load_settings

_LOCK = threading.Lock()

DEFAULT_PROFILE = {
    "name": "",
    "school": "",
    "grade_level": "高中",
    "grade": "高一",
    "subject": "数学",
}

GRADE_LEVELS = ["小学", "初中", "高中"]
GRADES = {
    "小学": ["一年级", "二年级", "三年级", "四年级", "五年级", "六年级"],
    "初中": ["初一", "初二", "初三"],
    "高中": ["高一", "高二", "高三"],
}
SUBJECTS = [
    "语文", "数学", "英语", "物理", "化学", "生物",
    "政治", "历史", "地理", "信息技术", "体育", "音乐", "美术",
]
PERIODS = ["第1节", "第2节", "第3节", "第4节", "第5节",
           "第6节", "第7节", "第8节"]
WEEKDAYS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# 科目默认配色（用于课程卡片）
_SUBJECT_COLORS = {
    "语文": "#e8735a", "数学": "#4a90d9", "英语": "#3fb27f",
    "物理": "#8a6cd9", "化学": "#d97bb0", "生物": "#3fb27f",
    "政治": "#d9a03f", "历史": "#c98a4a", "地理": "#58b368",
    "信息技术": "#5a9bc9", "体育": "#d96b6b", "音乐": "#b48ad9",
    "美术": "#e0a458",
}


def _path() -> Path:
    return load_settings().data_dir / "workbench.json"


def _default() -> dict:
    return {
        "profile": dict(DEFAULT_PROFILE),
        "courses": [],
        "calendar": {
            "term": "2026 秋季学期",
            "start": "2026-09-01",
            "events": [],
        },
    }


def _load() -> dict:
    p = _path()
    if not p.exists():
        return _default()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        d = _default()
        d.update({k: data.get(k, d[k]) for k in ("profile", "courses", "calendar")})
        return d
    except Exception:
        return _default()


def _save(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- 教师身份 ----------
def get_profile() -> dict:
    with _LOCK:
        d = _load()
        return d["profile"]


def set_profile(profile: dict) -> dict:
    with _LOCK:
        d = _load()
        cur = d["profile"]
        for k in DEFAULT_PROFILE:
            if k in profile:
                cur[k] = str(profile[k]).strip()
        # 身份变更时同步已有课程里的年级字段为空的历史课程（可选）
        _save(d)
        return {"ok": True, "profile": cur}


# ---------- 课程表 ----------
def list_courses() -> list[dict]:
    with _LOCK:
        return _load()["courses"]


def add_course(c: dict) -> dict:
    with _LOCK:
        d = _load()
        course = {
            "id": uuid.uuid4().hex[:10],
            "name": str(c.get("name") or "").strip(),
            "subject": str(c.get("subject") or "").strip(),
            "weekday": int(c.get("weekday") or 1),
            "period": int(c.get("period") or 1),
            "grade": str(c.get("grade") or "").strip(),
            "room": str(c.get("room") or "").strip(),
            "color": str(c.get("color") or "").strip(),
        }
        if not course["name"]:
            return {"ok": False, "error": "课程名称不能为空"}
        if not 1 <= course["weekday"] <= 7:
            return {"ok": False, "error": "星期不合法"}
        if not 1 <= course["period"] <= len(PERIODS):
            return {"ok": False, "error": "节次不合法"}
        if not course["color"]:
            course["color"] = _SUBJECT_COLORS.get(course["subject"], "#4a90d9")
        d["courses"].append(course)
        _save(d)
        return {"ok": True, "course": course}


def del_course(cid: str) -> dict:
    with _LOCK:
        d = _load()
        before = len(d["courses"])
        d["courses"] = [c for c in d["courses"] if c["id"] != cid]
        if len(d["courses"]) == before:
            return {"ok": False, "error": "未找到课程"}
        _save(d)
        return {"ok": True}


# ---------- 学校校历 ----------
def get_calendar() -> dict:
    with _LOCK:
        d = _load()
        return d["calendar"]


def set_term(term: str, start: str) -> dict:
    with _LOCK:
        d = _load()
        cal = d["calendar"]
        if term:
            cal["term"] = str(term).strip()
        if start:
            cal["start"] = str(start).strip()
        _save(d)
        return {"ok": True, "calendar": cal}


def add_event(e: dict) -> dict:
    with _LOCK:
        d = _load()
        ev = {
            "id": uuid.uuid4().hex[:10],
            "date": str(e.get("date") or "").strip(),
            "title": str(e.get("title") or "").strip(),
            "type": str(e.get("type") or "event").strip(),
        }
        if not ev["date"] or not ev["title"]:
            return {"ok": False, "error": "日期与标题不能为空"}
        d["calendar"].setdefault("events", []).append(ev)
        _save(d)
        return {"ok": True, "event": ev}


def del_event(eid: str) -> dict:
    with _LOCK:
        d = _load()
        evs = d["calendar"].get("events", [])
        before = len(evs)
        d["calendar"]["events"] = [e for e in evs if e["id"] != eid]
        if len(d["calendar"]["events"]) == before:
            return {"ok": False, "error": "未找到事件"}
        _save(d)
        return {"ok": True}


def snapshot() -> dict:
    """工作台完整数据 + 可选项（供前端一次拉取）。"""
    with _LOCK:
        d = _load()
    return {
        "profile": d["profile"],
        "courses": d["courses"],
        "calendar": d["calendar"],
        "options": {
            "grade_levels": GRADE_LEVELS,
            "grades": GRADES,
            "subjects": SUBJECTS,
            "periods": PERIODS,
            "weekdays": WEEKDAYS,
        },
    }
