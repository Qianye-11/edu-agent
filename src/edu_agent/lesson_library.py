"""教案中心的规范教案知识库。

条目使用独立 JSON 文件保存，便于备份、版本管理与后续接入向量检索。
本模块只负责本地持久化和上下文整理，不执行模型调用。
"""
from __future__ import annotations

import html
import json
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LIB_DIR = ROOT / "content" / "lesson_library"
TRASH_DIR = LIB_DIR / "_trash"
MAX_HTML = 2_000_000


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _clean_text(value: object, limit: int = 120) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _safe_id(value: object) -> str:
    raw = re.sub(r"[^0-9A-Za-z_-]+", "-", str(value or "")).strip("-")
    return raw[:80]


def _html_text(value: object, limit: int = 12000) -> str:
    source = str(value or "")
    source = re.sub(r"<(script|style|iframe|object|embed)[^>]*>.*?</\1>", " ", source,
                    flags=re.I | re.S)
    source = re.sub(r"<br\s*/?>", "\n", source, flags=re.I)
    source = re.sub(r"</(?:p|div|h[1-6]|li|tr|table)>", "\n", source, flags=re.I)
    source = re.sub(r"<[^>]+>", " ", source)
    return re.sub(r"[ \t]+", " ", html.unescape(source)).strip()[:limit]


def _preview_html(item: dict) -> str:
    """返回适合模板缩略图的轻量 HTML；大图不进入列表接口。"""
    source = str(item.get("html") or "")
    source = re.sub(r"<(script|style|iframe|object|embed|form)[^>]*>.*?</\1>", "", source,
                    flags=re.I | re.S)
    source = re.sub(r"<img\b[^>]*>", "<div class=\"import-image-note\">图片</div>", source,
                    flags=re.I | re.S)
    source = re.sub(r"\s+on[a-z]+\s*=\s*(['\"]).*?\1", "", source, flags=re.I | re.S)
    source = re.sub(r"\s+srcdoc\s*=\s*(['\"]).*?\1", "", source, flags=re.I | re.S)
    if len(source.encode("utf-8")) > 120_000:
        return _default_html(str(item.get("title") or "规范教案"),
                             str(item.get("lesson_type") or "新授课"))
    return source


def _entry_path(entry_id: str) -> Path:
    safe = _safe_id(entry_id)
    if not safe:
        raise ValueError("无效的教案条目 ID")
    return LIB_DIR / f"{safe}.json"


def _default_html(title: str, mode: str) -> str:
    process = {
        "探究课": "创设情境与提出问题；自主探究与合作交流；展示论证与追问；迁移应用与评价反思。",
        "习题课": "典例诊断；方法归纳；变式训练；错因辨析；分层巩固与课堂小结。",
        "新授课": "情境导入；概念形成；例题示范；巩固练习；课堂小结与作业。",
    }.get(mode, "导入；新知建构；活动与评价；巩固迁移；总结与作业。")
    return (
        f"<h1>{html.escape(title)}</h1>"
        "<table><tbody>"
        "<tr><td class=\"lp-label\">授课教师</td><td data-lp=\"teacher\"></td>"
        "<td class=\"lp-label\">授课班级</td><td data-lp=\"class\"></td>"
        "<td class=\"lp-label\">授课时间</td><td data-lp=\"time\"></td></tr>"
        "<tr><td class=\"lp-label\">科目</td><td data-lp=\"subject\"></td>"
        f"<td class=\"lp-label\">授课内容</td><td colspan=\"3\" data-lp=\"title\">{html.escape(title)}</td></tr>"
        "<tr><td class=\"lp-label\">课程分析</td><td colspan=\"5\" data-lp=\"analysis\">说明教材位置、内容结构、学情基础与学习障碍。</td></tr>"
        "<tr><td class=\"lp-label\">教学目标</td><td colspan=\"5\"><p><b>知识与技能：</b><span data-lp=\"knowledge\">明确可观察、可评价的学习结果。</span></p>"
        "<p><b>过程与方法：</b><span data-lp=\"method\">写清学生经历的学习过程与方法。</span></p>"
        "<p><b>情感态度与价值观：</b><span data-lp=\"value\">体现学科价值、态度与责任。</span></p></td></tr>"
        "<tr><td class=\"lp-label\">重点难点</td><td colspan=\"5\" data-lp=\"focus\">重点对应核心目标；难点对应学生认知障碍。</td></tr>"
        "<tr><td class=\"lp-label\">课前准备</td><td colspan=\"5\" data-lp=\"prepare\">课件、教具、学具与学习资源。</td></tr>"
        f"<tr><td class=\"lp-label lp-process-label\">教<br>学<br>过<br>程</td><td colspan=\"5\" data-lp=\"process\"><p>{html.escape(process)}</p><p>每个环节补充：教师活动、学生任务、设计意图、评价方式与时间。</p></td></tr>"
        "<tr><td class=\"lp-label\">板书与作业</td><td colspan=\"5\" data-lp=\"homework\">板书呈现知识主线；作业体现基础、提升与拓展分层。</td></tr>"
        "<tr><td class=\"lp-label\">课后反思</td><td colspan=\"5\" data-lp=\"reflection\">记录目标达成、课堂证据、问题与改进。</td></tr>"
        "</tbody></table>"
    )


def _ensure_defaults() -> None:
    LIB_DIR.mkdir(parents=True, exist_ok=True)
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    defaults = [
        ("builtin-standard", "中学课堂规范教案模板", "新授课", ["规范表格", "通用", "可评价目标"]),
        ("builtin-inquiry", "探究式课堂规范模板", "探究课", ["问题驱动", "学生活动", "过程评价"]),
        ("builtin-practice", "习题课规范教案模板", "习题课", ["变式训练", "错因分析", "分层作业"]),
    ]
    for entry_id, title, lesson_type, tags in defaults:
        path = _entry_path(entry_id)
        if path.exists():
            continue
        item = {
            "id": entry_id, "title": title, "subject": "通用", "grade": "中学",
            "lesson_type": lesson_type, "category": "规范模板", "tags": tags,
            "source": "built_in", "locked": True, "quality": "规范",
            "html": _default_html(title, lesson_type), "created_at": _now(), "updated_at": _now(),
        }
        path.write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")


def save(payload: dict) -> dict:
    _ensure_defaults()
    source = payload if isinstance(payload, dict) else {}
    title = _clean_text(source.get("title"), 100) or "未命名规范教案"
    raw_html = str(source.get("html") or "")
    if not raw_html.strip():
        raise ValueError("教案正文不能为空")
    if len(raw_html.encode("utf-8")) > MAX_HTML:
        raise ValueError("教案内容超过 2MB，请移除过大的内嵌图片后再收录")
    entry_id = _safe_id(source.get("id")) or f"lesson-{uuid.uuid4().hex[:12]}"
    if entry_id.startswith("builtin-"):
        raise ValueError("内置规范模板不可覆盖")
    existing = get(entry_id)
    if existing and existing.get("locked"):
        raise ValueError("内置规范模板不可覆盖")
    tags = source.get("tags") if isinstance(source.get("tags"), list) else []
    item = {
        "id": entry_id, "title": title,
        "subject": _clean_text(source.get("subject"), 40) or "未分类",
        "grade": _clean_text(source.get("grade"), 40) or "未设置",
        "lesson_type": _clean_text(source.get("lesson_type"), 40) or "规范教案",
        "category": _clean_text(source.get("category"), 40) or "我的教案",
        "tags": [_clean_text(x, 24) for x in tags if _clean_text(x, 24)][:8],
        "source": _clean_text(source.get("source"), 30) or "current",
        "locked": False, "quality": "规范", "html": raw_html,
        "created_at": str((existing or {}).get("created_at") or _now()), "updated_at": _now(),
    }
    _entry_path(entry_id).write_text(json.dumps(item, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary(item)


def get(entry_id: str) -> dict | None:
    _ensure_defaults()
    path = _entry_path(entry_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def summary(item: dict) -> dict:
    text = _html_text(item.get("html"), 260)
    return {key: item.get(key) for key in (
        "id", "title", "subject", "grade", "lesson_type", "category", "tags",
        "source", "locked", "quality", "created_at", "updated_at"
    )} | {"excerpt": text[:180], "preview_html": _preview_html(item)}


def list_entries(query: str = "", category: str = "") -> list[dict]:
    _ensure_defaults()
    q = _clean_text(query, 100).lower()
    cat = _clean_text(category, 40)
    items: list[dict] = []
    for path in LIB_DIR.glob("*.json"):
        item = get(path.stem)
        if not item:
            continue
        if cat and cat != "全部" and str(item.get("category")) != cat:
            continue
        haystack = " ".join(str(item.get(k) or "") for k in ("title", "subject", "grade", "lesson_type", "category"))
        haystack += " " + " ".join(str(x) for x in item.get("tags") or [])
        if q and q not in haystack.lower() and q not in _html_text(item.get("html"), 4000).lower():
            continue
        items.append(summary(item))
    items.sort(key=lambda x: (not bool(x.get("locked")), str(x.get("updated_at") or "")), reverse=False)
    return items


def delete(entry_id: str) -> dict:
    item = get(entry_id)
    if not item:
        return {"ok": False, "error": "教案条目不存在"}
    if item.get("locked"):
        return {"ok": False, "error": "内置规范模板不可删除"}
    source = _entry_path(entry_id)
    TRASH_DIR.mkdir(parents=True, exist_ok=True)
    target = TRASH_DIR / f"{source.stem}-{datetime.now().strftime('%Y%m%d%H%M%S')}.json"
    shutil.move(str(source), str(target))
    return {"ok": True, "id": entry_id, "recoverable": True}


def context(entry_ids: list[str], max_chars: int = 10000) -> tuple[str, list[dict]]:
    chunks: list[str] = []
    used: list[dict] = []
    remaining = max_chars
    for entry_id in entry_ids[:5]:
        item = get(entry_id)
        if not item:
            continue
        body = _html_text(item.get("html"), min(3800, remaining))
        if not body:
            continue
        block = f"参考教案《{item.get('title', '未命名')}》[{item.get('lesson_type', '教案')}]：\n{body}"
        if len(block) > remaining:
            block = block[:remaining]
        chunks.append(block)
        used.append(summary(item))
        remaining -= len(block)
        if remaining <= 200:
            break
    return "\n\n".join(chunks), used
