"""Agent Web 网关（唯一 UI 后端，端口 5174，同源免 CORS）。

第一阶段收口后的定位（对照 DSH：host 与网关分工）：
  - 会话状态：host.store.SessionStore（SQLite 持久化），不再有模块级全局
    SESSIONS dict / JSON 全量覆写；旧 web_sessions.json 首次启动自动迁移。
  - 意图路由：routing.decide 单一来源；本文件不再自带关键词表。
  - 本文件只做：静态托管 + 参数校验 + 把请求分派给 Agent 层
    （generate/planner/supervisor），业务逻辑不在路由函数里长住。

路由面（与旧版完全一致，前端零改动）：
  GET  /                静态前端（static/index.html）
  /api/session/*         会话 CRUD（数据落 data/edu_sessions.db）
  /api/templates|upload|set  模板管理（复用 template_spec）
  /api/chat              流式（A 逐字 / S 总指挥派单 / B 整份教案 + HTML 落盘）
  另：/api/textbook、/api/pdfs、/api/pdf/ingest、/files、/static、/uploads、/textbook

运行：.venv\\Scripts\\python.exe src/edu_agent/web_server.py（或 deploy/start_web.cmd）
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.parse  # _export_media 里的下载地址要用（来自 feat/ppt-view）
import time
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
STATIC = ROOT / "static"

from fastapi import FastAPI, UploadFile, File, Form, Query  # noqa: E402
from fastapi.responses import StreamingResponse, HTMLResponse, Response, JSONResponse, FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402
from starlette.concurrency import run_in_threadpool  # noqa: E402

from edu_agent import kg, lesson_docx, lesson_library, logsetup, pdf_upload, plan_html, planner, routing  # noqa: E402
from edu_agent import capabilities, care, greet, wps_export  # noqa: E402  能力开关 + 问候/情绪短路 + WPS 导出
from edu_agent import ppt_polish  # noqa: E402  PPT 优化（体检 / 原地统一 / 内容校验）—— 端点来自 feat/ppt-view
from edu_agent import slide_deck  # noqa: E402  PPT 工作台：预览与导出共用同一份 SVG 版式
from edu_agent.config import load_settings  # noqa: E402
from edu_agent.host.store import SessionStore  # noqa: E402
from edu_agent.template_spec import (  # noqa: E402
    get_current, get_template, list_templates, recognize_docx, save_template, set_current,
    template_sketch,
)

log = logsetup.get_logger("web_server")

app = FastAPI(title="课本教练 x 教案 Agent")

# ---------- 模块3：Skill 市场与 Agent 自动封装（见 edu_agent/skill_market/） ----------
# 只挂路由（数据面），不含任何前端页面；市场 UI 由模块 F 另做。
# 挂载失败不能拖垮整个网关（例如某个可选依赖缺失），所以包了 try/except 并只记日志。
try:
    from edu_agent.skill_market.api import router as _skill_market_router
    from edu_agent.skill_market.create_api import router as _skill_create_router

    app.include_router(_skill_market_router)
    app.include_router(_skill_create_router)
except Exception as _e:  # noqa: BLE001
    log.warning("模块3（Skill 市场）路由未挂载：%s: %s", type(_e).__name__, _e)

# skill-create 定时扫描（docs/22 M9）：守护线程，默认 24 小时一次，只做"发现"
# （回扫会话 + 更新候选看板），**绝不自动出草稿、绝不自动发布**。
try:
    from edu_agent.skill_market import scheduler as _sc_scheduler

    _sc_scheduler.start()
except Exception as _e:  # noqa: BLE001
    log.warning("skill-create 定时扫描未启动：%s: %s", type(_e).__name__, _e)

# ---------- host：会话存储（进程内只保留这一个持久化入口） ----------
_settings = load_settings()
_store = SessionStore(path=_settings.sessions_db,
                      migrate_legacy=ROOT / "data" / "web_sessions.json")

# 长期记忆归属的 profile（分隔不同老师/学生的记忆；将来做多用户时按登录身份替换这里）
MEM_PROFILE = "default"


# ---------- 静态 ----------
app.mount("/files", StaticFiles(directory=str(ROOT / "content" / "plans")), name="plans")
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

# PDF 源：教材原文（hermes 目录，只读挂载）+ 用户上传
_txt = load_settings().textbook_pdf
_TXT_DIR = _txt.parent if _txt and _txt.exists() else None
if _TXT_DIR is not None:
    app.mount("/textbook", StaticFiles(directory=str(_TXT_DIR)), name="textbook")
_TXT_NAME = _txt.name if _txt else ""
_TXT_OFF = 6  # 印刷页码 -> PDF 物理页(+1) 的偏移
pdf_upload.PDF_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(pdf_upload.PDF_DIR)), name="uploads")


async def _read_upload_limited(file: UploadFile, limit: int) -> bytes:
    """分块读取上传，拒绝超过部署配置的文件，避免 `read()` 无上限占满内存。"""
    chunks: list[bytes] = []
    size = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        size += len(chunk)
        if size > limit:
            raise ValueError(f"文件超过上限（{limit // 1024 // 1024} MB）")
        chunks.append(chunk)


# 自选导出目录不一定在仓库内，因此下载接口不能靠「目录前缀」判断。
# 改为仅接受本进程刚刚生成并登记的随机一次性标识，15 分钟后自动失效。
_DOWNLOAD_TTL_SECS = 15 * 60
_downloads: dict[str, tuple[Path, float]] = {}
_downloads_lock = threading.Lock()


def register_download(path: str | Path) -> str:
    p = Path(path).resolve()
    if not p.is_file():
        raise FileNotFoundError(p)
    now = time.monotonic()
    with _downloads_lock:
        for key, (_, created) in list(_downloads.items()):
            if now - created > _DOWNLOAD_TTL_SECS:
                _downloads.pop(key, None)
        token = uuid.uuid4().hex
        _downloads[token] = (p, now)
    return token


def take_download(token: str) -> Path | None:
    now = time.monotonic()
    with _downloads_lock:
        entry = _downloads.pop(token, None)  # 一次性，避免 URL 被浏览器历史重复暴露
    if entry is None:
        return None
    path, created = entry
    if now - created > _DOWNLOAD_TTL_SECS or not path.is_file():
        return None
    return path


@app.get("/", response_class=HTMLResponse)
def index():
    return (STATIC / "index.html").read_text(encoding="utf-8")


# ---------- 知识星图 ----------
@app.get("/api/kg/graph")
def api_kg_graph():
    return kg.get_kg().get_graph()


@app.get("/api/kg/node/{node_id}")
def api_kg_node(node_id: str):
    return kg.get_kg().get_node_info(node_id)


@app.get("/api/kg/related/{node_id}")
def api_kg_related(node_id: str, hops: int = 1):
    return kg.get_kg().get_related(node_id, hops)


@app.get("/api/ontology/graph")
def api_ontology_graph():
    """全科教材本体；与仅覆盖数学必修一的知识星图保持独立。"""
    try:
        import json
        from edu_agent.ontology import DEFAULT_PATH, Ontology

        raw = json.loads(DEFAULT_PATH.read_text(encoding="utf-8"))
        graph = Ontology(raw)
        return {"ok": True, "meta": raw.get("meta") or {},
                "nodes": list(graph.nodes.values()), "links": graph.edges}
    except FileNotFoundError:
        return {"ok": False, "error": "全科教材本体尚未构建"}
    except Exception as exc:
        return {"ok": False, "error": f"本体读取失败：{type(exc).__name__}: {exc}"}


@app.get("/api/ontology/search")
def api_ontology_search(q: str, limit: int = 12):
    try:
        from edu_agent.ontology import Ontology

        return {"ok": True, "items": Ontology.from_file().search(q, limit=max(1, min(limit, 30)))}
    except FileNotFoundError:
        return {"ok": False, "error": "全科教材本体尚未构建", "items": []}
    except Exception as exc:
        return {"ok": False, "error": f"本体检索失败：{type(exc).__name__}: {exc}", "items": []}


@app.get("/api/kg/search")
def api_kg_search(q: str = ""):
    return {"results": kg.get_kg().search(q)}


@app.get("/api/kg/health")
def api_kg_health():
    graph = kg.get_kg()
    return {**graph.validate(), "meta": graph.get_graph().get("meta", {})}


# ---------- 模板 ----------
@app.get("/api/templates")
def api_templates():
    # 注意：current 必须走 get_current()（读 _current.json）；get_template("") 会直接回默认模板
    cur = get_current().template_id
    return {"templates": list_templates(), "current": cur}


class TplSet(BaseModel):
    template_id: str = "default"


@app.post("/api/template/set")
def api_tpl_set(body: TplSet):
    set_current(body.template_id)
    return {"ok": True, "current": body.template_id}


@app.post("/api/template/upload")
async def api_tpl_upload(file: UploadFile = File(...)):
    if not (file.filename or "").lower().endswith(".docx"):
        return {"ok": False, "error": "仅支持 .docx（Word 教案模板）"}
    tmp = _settings.data_dir / ("upload_" + uuid.uuid4().hex + ".docx")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp.write_bytes(await _read_upload_limited(file, _settings.upload_max_bytes))
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    try:
        spec = recognize_docx(tmp)
        save_template(spec, tmp)
        set_current(spec.template_id)
        return {"ok": True, "current": spec.template_id, "name": spec.name, "preview": template_sketch(spec)}
    finally:
        tmp.unlink(missing_ok=True)


# ---------- 富模板（教案中心：导入 -> 全量编辑 -> 保存 -> 导出） ----------
class TplRichSave(BaseModel):
    template: dict
    set_current: bool = True
    confirm: bool = False      # 权限确认：gate=permission 时前端重试带上（见 edu_agent/permissions.py）


@app.post("/api/template/import")
async def api_tpl_rich_import(file: UploadFile = File(...)):
    """导入自有 .docx 教案模板 -> 富模型（板块/段落/表格 + 格式），**先不落盘**供用户编辑。"""
    from edu_agent import template_rich

    if not (file.filename or "").lower().endswith(".docx"):
        return {"ok": False, "error": "仅支持 .docx（Word 教案模板）"}
    tmp = _settings.data_dir / ("tpl_" + uuid.uuid4().hex + ".docx")
    tmp.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp.write_bytes(await _read_upload_limited(file, _settings.upload_max_bytes))
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    try:
        from edu_agent import template_spec as _ts

        stem = (file.filename or "").rsplit(".", 1)[0] or "模板"
        rich = template_rich.docx_to_rich(tmp, template_id=_ts._slugify(stem), name=stem)
        rich["_upload_path"] = str(tmp)          # 保存时用它把原件备份到 orig/
        return {"ok": True, "template": rich, "stats": template_rich.stats(rich)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": type(e).__name__ + ": " + str(e)}
    finally:
        pass          # 原件要留到「保存」时备份，故不在此删除


@app.post("/api/template/upgrade")
def api_tpl_upgrade(body: dict):
    """把已有模板「升级」为可编辑富模板：解析它在 orig/ 里备份的原件 .docx。"""
    from edu_agent import template_rich

    tid = str(body.get("template_id") or "").strip()
    if not tid:
        return {"ok": False, "error": "缺少 template_id"}
    src = template_rich.ORIG_DIR / f"{tid}.docx"
    if not src.exists():
        return {"ok": False, "error": "没找到该模板的原件（content/templates/orig/），请直接「导入 .docx」"}
    try:
        rich = template_rich.docx_to_rich(src, template_id=tid, name=tid)
        # 注意：这里**绝不能**写 _upload_path —— 保存时会 unlink 该路径，
        # 而 src 是模板自己的原件（content/templates/orig/），删了就丢了。
        rich["_keep_source"] = True
        rich["source"] = f"orig/{tid}.docx"
        return {"ok": True, "template": rich, "stats": template_rich.stats(rich)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": type(e).__name__ + ": " + str(e)}


@app.get("/api/template/rich")
def api_tpl_rich_get(id: str = ""):
    """读富模板（供编辑器加载）；非富模板返回 ok=False 并附简单骨架预览。"""
    from edu_agent import template_rich

    if not id:
        return {"ok": False, "error": "缺少 id"}
    rich = template_rich.load_rich(id)
    if rich is None:
        spec = get_template(id)
        return {"ok": False, "error": "该模板不是富模板（只有板块骨架）",
                "preview": template_sketch(spec)}
    return {"ok": True, "template": rich, "stats": template_rich.stats(rich)}


@app.get("/api/template/canvas")
def api_tpl_canvas(id: str = "default"):
    """把任意模板转换为教案画布可直接渲染的富模型。

    模板中心同时允许两种存量格式：旧版 v1 只有板块骨架，v2 则保存段落、表格和
    字符格式。教案中心不应该把这种实现差异暴露给老师，因此这里统一返回 v2；
    旧骨架会在内存中转换，不改写用户原有模板文件。
    """
    from edu_agent import plan_sync, template_rich

    template_id = str(id or "default").strip() or "default"
    available = {str(item.get("template_id") or "") for item in list_templates()}
    if template_id not in available:
        return JSONResponse({"ok": False, "error": f"没有模板「{template_id}」"}, status_code=404)

    rich = template_rich.load_rich(template_id)
    source = "rich"
    if rich is None:
        original = template_rich.ORIG_DIR / f"{template_id}.docx"
        if original.exists():
            # v1 JSON 只是早期识别出的板块索引；真正的字体、段落和表格样式仍在原始
            # DOCX 中。教案中心加载时必须优先解析原稿，不能降级成纯标题骨架。
            rich = template_rich.docx_to_rich(
                original, template_id=template_id, name=get_template(template_id).name
            )
            source = "original-docx"
        else:
            rich = plan_sync.rich_from_skeleton(get_template(template_id))
            source = "skeleton"
    return {"ok": True, "template": rich, "source": source,
            "stats": template_rich.stats(rich)}


@app.post("/api/template/rich/save")
def api_tpl_rich_save(body: TplRichSave):
    """保存编辑后的富模板（落 content/templates/<id>.json，可同时设为 Agent B 的当前模板）。"""
    from edu_agent import permissions, template_rich

    blocked = permissions.guard("template_save", bool(getattr(body, "confirm", False)))
    if blocked:
        return blocked

    rich = dict(body.template or {})
    src = rich.pop("_upload_path", None)
    keep_source = bool(rich.pop("_keep_source", False))
    rich.pop("_sync", None)          # 填充报告只是界面态，别落进模板 JSON
    rich.pop("_sync_report", None)
    # 只有「上传到数据目录的临时件」才允许删；其余（如模板原件）一律保留
    tmp_path: Path | None = None
    if src and not keep_source:
        try:
            cand = Path(src).resolve()
            data_root = load_settings().data_dir.resolve()   # 跟随 EDU_DATA_DIR 配置
            if data_root in cand.parents:
                tmp_path = cand
        except Exception:
            tmp_path = None
    try:
        path = template_rich.save_rich(rich, docx_source=(None if keep_source else src),
                                       set_current=body.set_current)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": type(e).__name__ + ": " + str(e)}
    if tmp_path is not None:
        try:
            tmp_path.unlink(missing_ok=True)
        except Exception:
            pass
    saved = template_rich.load_rich(rich.get("template_id", "")) or {}
    return {"ok": True, "path": str(path), "template_id": saved.get("template_id"),
            "name": saved.get("name"), "spec_version": saved.get("spec_version"),
            "stats": template_rich.stats(saved), "current": get_current().template_id}


@app.post("/api/template/delete")
def api_tpl_delete(body: dict):
    """删除一个已保存的模板（**软删**：移到 content/templates/_trash/，可手工找回）。

    删的正是当前模板时，当前会自动切回内置 `default`——否则 Agent B 会指向一个不存在的模板。
    """
    from edu_agent import permissions, template_rich

    blocked = permissions.guard("template_delete", bool(body.get("confirm")))
    if blocked:
        return blocked
    try:
        return template_rich.delete_template(str(body.get("template_id") or ""))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


@app.get("/api/template/blank")
def api_tpl_blank(name: str = "新模板"):
    """空白富模板（编辑器「新建」用）。"""
    from edu_agent import template_rich

    rich = template_rich.blank_rich(name)
    return {"ok": True, "template": rich, "stats": template_rich.stats(rich)}


# ---------- 选项①：让教案 Agent 依据课题产出「教案模板」（2026-09-12 一期） ----------
class PlanTemplateReq(BaseModel):
    topic: str = ""             # 课题；不传则取会话里最近一份教案的课题
    goal: str = ""
    session_id: str = ""
    save: bool = True
    set_current: bool = False


def _last_plan_topic(sid: str) -> str:
    """取会话里最近一条教案回答的课题（`# 标题`）。"""
    sess = _store.get(sid) if sid else None
    if not sess:
        return ""
    for m in reversed(sess.get("messages") or []):
        if m.get("role") != "assistant":
            continue
        c = m.get("content") or ""
        if (m.get("meta") or {}).get("mode") == "B" or "Agent B" in c[:60]:
            mm = re.search(r"^#\s+(.+)$", c, re.M)
            if mm:
                return mm.group(1).strip()
    return ""


@app.post("/api/plan/template")
def api_plan_template(body: PlanTemplateReq):
    """产出一份可复用的教案模板：板块骨架 + 每板块填写提示 + 表格板块表头。"""
    from edu_agent import planner, template_rich

    topic = (body.topic or "").strip() or _last_plan_topic(body.session_id.strip())
    if not topic:
        return {"ok": False, "error": "缺少课题：先问一句或让教案 Agent 生成一份教案，再点「生成教案模板」"}
    try:
        rich = planner.make_template(topic, goal=(body.goal or "").strip())
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"模板生成失败：{type(e).__name__}: {e}"}
    if not (rich.get("blocks") or []):
        return {"ok": False, "error": "模型没有产出板块，请再点一次重试"}

    path = None
    if body.save:
        try:
            path = template_rich.save_rich(rich, set_current=body.set_current)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"模板保存失败：{type(e).__name__}: {e}"}
    return {"ok": True, "template": rich, "template_id": rich["template_id"],
            "name": rich["name"], "stats": template_rich.stats(rich),
            "skeleton": planner.template_to_markdown(rich),
            "path": str(path) if path else None}


# ---------- 选项③：把会话里的教案按板块填进模板（2026-09-12 二期） ----------
class PlanFromTemplateReq(BaseModel):
    markdown: str = ""          # 不传则取会话里最近一条教案（Agent B）
    session_id: str = ""
    template_id: str = ""       # 不传则用 Agent B 当前模板
    save: bool = False          # 默认不落盘：交给教案中心编辑器继续改，满意后再「保存为模板」


class PlanAssistantReq(BaseModel):
    """教案中心内 Agent B 的一次对话请求。"""
    instruction: str = ""
    plan: dict = {}
    reference_ids: list[str] = Field(default_factory=list)
    history: list[dict] = Field(default_factory=list)


def _plan_assistant_instruction(instruction: str, history: list[dict] | None = None,
                                memory_prefix: str = "", reference_text: str = "") -> str:
    """为 Agent B 组合当前指令、最近对话、长期记忆与用户选定的规范教案。"""
    blocks = ["【用户本次指令】\n" + str(instruction or "").strip()]
    recent = []
    for item in (history or [])[-10:]:
        if not isinstance(item, dict):
            continue
        role = "教师" if str(item.get("role") or "") == "user" else "Agent B"
        text = " ".join(str(item.get("text") or "").split()).strip()
        if text:
            recent.append(f"{role}：{text[:500]}")
    if recent:
        blocks.append("【最近对话（用于理解追问与连续修改）】\n" + "\n".join(recent))
    if str(memory_prefix or "").strip():
        blocks.append("【教师长期记忆（跨对话偏好，若与本次指令冲突则以本次为准）】\n"
                      + str(memory_prefix).strip())
    if str(reference_text or "").strip():
        blocks.append(
            "【规范教案参考】\n以下材料仅用于借鉴结构、课堂活动与评价方式，不要照抄无关课题。\n"
            + str(reference_text).strip()
        )
    return "\n\n".join(blocks)


def _design_to_record(plan: dict) -> planner.PlanRecord:
    """浏览器规范表格模型 -> Agent B 的结构化输入。"""
    src = plan if isinstance(plan, dict) else {}
    goals = src.get("goals") if isinstance(src.get("goals"), dict) else {}
    objectives = [str(goals.get(k) or "").strip() for k in ("knowledge", "method", "value")]
    objectives = [x for x in objectives if x]
    if str(src.get("objective") or "").strip():
        objectives.append(str(src.get("objective")).strip())
    flow = []
    for item in src.get("steps") or []:
        if not isinstance(item, dict):
            continue
        content = str(item.get("points") or "").strip()
        activity = str(item.get("activity") or "").strip()
        if activity:
            content += ("\n师生活动：" if content else "师生活动：") + activity
        try:
            minutes = max(1, int(item.get("minutes") or 5))
        except (TypeError, ValueError):
            minutes = 5
        flow.append(planner.FlowStep(phase=str(item.get("name") or "教学环节"),
                                     minutes=minutes, content=content))
    key_points = [str(src.get("focus") or "").strip()] if str(src.get("focus") or "").strip() else []
    return planner.PlanRecord(title=str(src.get("title") or "").strip(), objectives=objectives,
                              key_points=key_points, flow_steps=flow)


def _record_to_design(rec: planner.PlanRecord, previous: dict) -> dict:
    """Agent B 的结果 -> 浏览器规范表格模型，保留不在 Agent 契约内的表格字段。"""
    old = previous if isinstance(previous, dict) else {}
    goals = list(rec.objectives or [])
    old_steps = {str(x.get("name") or ""): x for x in (old.get("steps") or []) if isinstance(x, dict)}
    steps = []
    for step in rec.flow_steps:
        old_step = old_steps.get(step.phase, {})
        content = str(step.content or "")
        marker = "师生活动："
        points, activity = content, str(old_step.get("activity") or "")
        if marker in content:
            points, activity = content.split(marker, 1)
        steps.append({"name": step.phase or "教学环节", "minutes": max(1, int(step.minutes or 5)),
                      "points": points.strip(), "activity": activity.strip()})
    if not steps:
        steps = old.get("steps") or []
    old_goals = old.get("goals") if isinstance(old.get("goals"), dict) else {}
    return {
        "title": rec.title or str(old.get("title") or ""),
        "grade": str(old.get("grade") or ""), "mode": "table",
        "lessonType": str(old.get("lessonType") or "讲授"),
        "objective": "\n".join(goals),
        "goals": {
            "knowledge": goals[0] if len(goals) > 0 else str(old_goals.get("knowledge") or ""),
            "method": goals[1] if len(goals) > 1 else str(old_goals.get("method") or ""),
            "value": goals[2] if len(goals) > 2 else str(old_goals.get("value") or ""),
        },
        "focus": "；".join(rec.key_points or []) or str(old.get("focus") or ""),
        "homework": str(old.get("homework") or ""), "steps": steps,
    }


def _plan_assistant_result(rec: planner.PlanRecord, body: PlanAssistantReq,
                           references: list[dict]) -> dict:
    """把 Agent B 结果整理成同步与流式接口共用的最终事件。"""
    design = _record_to_design(rec, body.plan)
    message = (f"已参考 {len(references)} 份规范教案并更新当前教案。" if references else
               "已按你的要求更新当前教案；你可以继续追问或让我调整其中一个环节。")
    remembered = ""
    memory_state = {"enabled": False, "count": 0, "remembered": ""}
    try:
        from edu_agent import memory, memory_auto

        fact = memory_auto.explicit_fact(body.instruction)
        if fact and memory.add_fact(MEM_PROFILE, fact, kind="preference", source="user"):
            remembered = fact
        memory_auto.extract_async(body.instruction, message, MEM_PROFILE)
        memory_state = {"enabled": memory.memory_enabled(MEM_PROFILE),
                        "count": memory.stats(MEM_PROFILE).get("facts", 0),
                        "remembered": remembered}
    except Exception:  # noqa: BLE001 记忆落库失败不影响当前教案结果
        pass
    return {"ok": True, "plan": design, "title": rec.title,
            "steps": len(design.get("steps") or []), "message": message,
            "memory": memory_state, "references": references,
            "citations": [c.model_dump() for c in rec.citations]}


@app.post("/api/plan/assistant")
def api_plan_assistant(body: PlanAssistantReq):
    """Agent B 在教案中心按对话编辑表格；只返回数据，不落盘、不调用 WPS。"""
    instruction = (body.instruction or "").strip()
    if not instruction:
        return {"ok": False, "error": "请先输入希望 Agent B 怎么修改教案"}
    current = _design_to_record(body.plan)
    topic = current.title or instruction
    reference_text, references = lesson_library.context(body.reference_ids)
    memory_prefix = ""
    try:
        from edu_agent import memory

        memory_prefix = memory.profile_memory_prefix(MEM_PROFILE)
    except Exception:  # noqa: BLE001 记忆不可用不能阻断教案编辑
        pass
    agent_instruction = _plan_assistant_instruction(
        instruction, body.history, memory_prefix, reference_text
    )
    try:
        rec = planner.revise_plan(topic, agent_instruction, current)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"Agent B 未能完成：{e}"}
    return _plan_assistant_result(rec, body, references)


def _plan_assistant_stream_events(body: PlanAssistantReq):
    """教案中心 Agent B SSE：模型片段实时下发，完整校验通过后才发送 plan。"""
    instruction = (body.instruction or "").strip()
    if not instruction:
        yield {"t": "error", "text": "请先输入希望 Agent B 怎么修改教案"}
        return
    current = _design_to_record(body.plan)
    topic = current.title or instruction
    reference_text, references = lesson_library.context(body.reference_ids)
    memory_prefix = ""
    try:
        from edu_agent import memory

        memory_prefix = memory.profile_memory_prefix(MEM_PROFILE)
    except Exception:  # noqa: BLE001
        pass
    agent_instruction = _plan_assistant_instruction(
        instruction, body.history, memory_prefix, reference_text
    )
    try:
        for event in planner.revise_plan_stream(topic, agent_instruction, current):
            kind = event.get("type")
            if kind == "status":
                yield {"t": "status", "text": event.get("text", "正在生成教案…")}
            elif kind == "delta":
                yield {"t": "d", "text": event.get("text", "")}
            elif kind == "done" and event.get("record") is not None:
                result = _plan_assistant_result(event["record"], body, references)
                result["t"] = "done"
                yield result
                return
        yield {"t": "error", "text": "Agent B 未返回完整教案，请重试"}
    except Exception as e:  # noqa: BLE001
        yield {"t": "error", "text": f"Agent B 未能完成：{e}"}


@app.post("/api/plan/assistant/stream")
def api_plan_assistant_stream(body: PlanAssistantReq):
    return _sse(_plan_assistant_stream_events(body))


# ---------- 教案中心：规范教案知识库 ----------
class LessonLibraryReq(BaseModel):
    id: str = ""
    title: str = ""
    subject: str = ""
    grade: str = ""
    lesson_type: str = ""
    category: str = ""
    tags: list[str] = []
    source: str = "current"
    html: str = ""


@app.get("/api/lesson-library")
def api_lesson_library(query: str = "", category: str = ""):
    items = lesson_library.list_entries(query=query, category=category)
    categories = sorted({str(x.get("category")) for x in items if x.get("category")})
    return {"ok": True, "items": items, "categories": categories, "count": len(items)}


@app.get("/api/lesson-library/{entry_id}")
def api_lesson_library_item(entry_id: str):
    item = lesson_library.get(entry_id)
    if not item:
        return JSONResponse({"ok": False, "error": "教案条目不存在"}, status_code=404)
    return {"ok": True, "item": item}


@app.post("/api/lesson-library")
def api_lesson_library_save(body: LessonLibraryReq):
    try:
        item = lesson_library.save(body.model_dump())
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "item": item}


@app.delete("/api/lesson-library/{entry_id}")
def api_lesson_library_delete(entry_id: str):
    return lesson_library.delete(entry_id)


class LessonDocxReq(BaseModel):
    title: str = "教案"
    html: str = ""


@app.post("/api/lesson/export-docx")
def api_lesson_export_docx(body: LessonDocxReq):
    """把教案中心当前页面直接生成 DOCX；使用 python-docx，不调用 WPS。"""
    try:
        path = lesson_docx.export_html(body.html, body.title)
        token = register_download(path)
        return {"ok": True, "filename": path.name, "path": str(path),
                "download_url": "/api/wps/download?id=" + token,
                "engine": "python-docx", "detail": f"已生成 {path.name}（未调用 WPS）"}
    except (ValueError, OSError) as e:
        return {"ok": False, "error": str(e)}


def _last_plan_markdown(sid: str) -> str:
    """取会话里最近一条教案回答（Agent B / 总指挥出的）的原文。"""
    sess = _store.get(sid) if sid else None
    if not sess:
        return ""
    for m in reversed(sess.get("messages") or []):
        if m.get("role") != "assistant":
            continue
        text = m.get("content") or ""
        if (m.get("meta") or {}).get("mode") == "B" or "Agent B" in text[:60]:
            return _strip_agent_tag(text)
    return ""


@app.post("/api/plan/from-template")
def api_plan_from_template(body: PlanFromTemplateReq):
    """把一份教案正文按标题匹配填进所选模板的对应板块（返回填充后的富模板，默认不落盘）。

    与「选项② 按模板生成」的区别：② 是让模型**重新生成**一份教案；这里不花模型调用，
    而是把**已有**的教案正文灌进模板板块——老师可以在教案中心接着改，再「保存为模板」。
    """
    from edu_agent import plan_sync, template_rich

    md = (body.markdown or "").strip()
    if not md:
        md = (_last_plan_markdown(body.session_id.strip())
              or _last_plan_markdown(_store.current_id() or ""))
        if not md:                       # 兜底：扫最近几个会话里的教案
            for row in (_store.list_sessions() or [])[:10]:
                md = _last_plan_markdown(row.get("id") or "")
                if md:
                    break
    if not md:
        return {"ok": False, "error": "没拿到教案内容：请先在会话里让教案 Agent 生成一份教案"}

    tid = (body.template_id or "").strip() or get_current().template_id
    known = {t.get("template_id") for t in list_templates()}
    if tid not in known:
        return {"ok": False, "error": f"没有模板「{tid}」：它可能已被删除，请重新选一个"}
    rich = template_rich.load_rich(tid)
    source_kind = "rich"
    if rich is None:                     # 不是 v2 富模板
        src = template_rich.ORIG_DIR / f"{tid}.docx"
        if src.exists():                 # 有原件 -> 现场升级成富模板再填
            rich = template_rich.docx_to_rich(src, template_id=tid, name=tid)
            rich["_keep_source"] = True  # 原件不可删（见 api_tpl_rich_save）
            source_kind = "upgraded"
        else:                            # 骨架模板（含内置 default）-> 用板块名现搭结构
            rich = plan_sync.rich_from_skeleton(get_template(tid))
            source_kind = "skeleton"

    res = plan_sync.sync(md, rich)
    if not res.get("ok"):
        return res
    if body.save:
        try:
            template_rich.save_rich(res["template"], docx_source=None, set_current=False)
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"填充结果保存失败：{type(e).__name__}: {e}"}
    res["template_id"] = tid
    res["template_name"] = rich.get("name")
    res["template_source"] = source_kind
    res["template_upgraded"] = source_kind == "upgraded"
    res["stats"] = template_rich.stats(res["template"])
    res["blank_blocks"] = plan_sync.blank_blocks(res["template"])
    return res


# ---------- 三期：教案 PPT 预览（与真正生成共用同一解析，不写文件、不需要 WPS 能力） ----------
@app.post("/api/plan/preview")
def api_plan_preview(body: dict):
    """教案 Markdown -> PPT 页结构（预览用）。

    刻意**不查能力开关**：这只是看一眼会生成什么，不落盘、不碰 WPS。
    导出（`/api/wps/export`）仍然受开关约束。
    """
    md = str(body.get("markdown") or "").strip() or _last_plan_markdown(
        str(body.get("session_id") or "").strip() or (_store.current_id() or ""))
    if not md:
        return {"ok": False, "error": "没有可预览的教案内容：先让教案 Agent 生成一份教案"}
    try:
        data = wps_export.slides_from_markdown(md)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if not data.get("slides"):
        return {"ok": False, "error": "这份内容里没有解析出可成页的环节/板块"}
    return {"ok": True, "title": data.get("title") or "教案",
            "slides": data["slides"], "source": data.get("source"),
            "note": "每页 6 条要点；表格内容不进 PPT（留给 Word）"}


# ---------- 会话 ----------
@app.post("/api/session/new")
def api_sess_new():
    sid = _store.new()
    return {"id": sid, "title": "新会话"}


@app.post("/api/session/branch")
def api_sess_branch(body: dict):
    """分支：以现有会话的消息副本新建会话（原会话不动）。"""
    sid = body.get("id", "")
    src = _store.get(sid)
    if src is None:
        return {"ok": False, "error": "会话不存在"}
    nsid = _store.new()
    for m in (src.get("messages") or []):
        _store.append(nsid, m.get("role", "user") if m.get("role") in ("user", "assistant") else "user",
                      m.get("content") or "")
    title = (src.get("title") or "").strip()
    if title and title != "新会话":
        _store.rename(nsid, (title + "（分支）")[:18])
    return {"ok": True, "id": nsid}


@app.post("/api/project/new")
def api_project_new(body: dict):
    """新建项目文件夹（防路径穿越）。name=目录名, base=父目录（默认取 settings.fs_root）"""
    name = (body.get("name") or "").strip()
    if not name:
        return {"ok": False, "error": "项目名不能为空"}
    from edu_agent import permissions

    blocked = permissions.guard("project_new", bool(body.get("confirm")))   # 建目录 = 有副作用
    if blocked:
        return blocked
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    base = _fs_safe((body.get("base") or _FS_ROOT))
    if base is None:
        return {"ok": False, "error": "位置不在允许范围（仅本机 C/D/E 盘）"}
    target = (base / name).resolve()
    if base not in target.parents:
        return {"ok": False, "error": "非法路径"}
    if target.exists():
        if not target.is_dir():
            return {"ok": False, "error": "同名文件已存在"}
        return {"ok": True, "path": str(target), "exists": True}
    target.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "path": str(target), "exists": False}


def _drives() -> list[Path]:
    """探测本机存在的盘（C-Z），作为可浏览根。

    2026-09-10 修正：原写死 "CDEF"，项目迁到 F: 之后其它盘（如移动硬盘 / 网络盘）
    在选择器里根本看不到；改为全盘探测。
    """
    out = []
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        try:
            p = Path(f"{letter}:/")
            if p.exists():
                out.append(p.resolve())
        except Exception:
            continue
    return out


_FS_BAD = ("$RECYCLE", "System Volume", "Windows", "$WinRE", "Recovery",
           "Program Files", "Program Files (x86)", "ProgramData")

# 「新建项目 / 文件浏览」的默认根目录：来自 settings.fs_root（.env 的 EDU_FS_ROOT 可覆盖）
# 原先写死 "D:/hermes"，团队每人机器目录不同 → 现在默认 = 用户主目录；
# 若显式配置的目录不存在，退回用户主目录，保证前端一定能拿到一个可读的位置
_cfg_fs_root = _settings.fs_root
_FS_ROOT = str(_cfg_fs_root if _cfg_fs_root.exists() else Path.home())


def _fs_safe(p: str) -> Path | None:
    """解析路径并校验在已探测盘根内；过滤系统目录；非法返回 None。"""
    try:
        t = Path(p).resolve()
    except Exception:
        return None
    if not any(str(t).upper().startswith(str(d).upper()) for d in _drives()):
        return None
    if any(t.name.startswith(b) or t.name == b for b in _FS_BAD):
        return None
    return t


@app.get("/api/fs/drives")
def api_fs_drives():
    return {"ok": True, "drives": [str(d) for d in _drives()]}






@app.get("/api/fs/list")
def api_fs_list(path: str = ""):
    """列出目录（只读；限本机盘，系统目录过滤）。path 为空回落到 settings.fs_root。"""
    cur = _fs_safe(path or _FS_ROOT)
    if cur is None:
        return {"ok": False, "error": "路径不在允许范围"}
    try:
        dirs = sorted([d.name for d in cur.iterdir()
                       if d.is_dir() and not d.name.startswith((".", "$"))
                       and not any(d.name.startswith(b) or d.name == b for b in _FS_BAD)],
                      key=str.lower)
    except Exception:
        return {"ok": False, "error": "无法读取目录"}
    parent = None
    if cur != Path(cur.anchor):
        pp = cur.parent.resolve()
        if _fs_safe(str(pp)) is not None:
            parent = str(pp)
    return {"ok": True, "path": str(cur), "parent": parent,
            "dirs": dirs, "roots": [str(d) for d in _drives()]}


@app.post("/api/fs/mkdir")
def api_fs_mkdir(body: dict):
    """在指定目录下新建子文件夹。"""
    base = _fs_safe((body.get("path") or _FS_ROOT))
    name = (body.get("name") or "").strip()
    if base is None:
        return {"ok": False, "error": "路径不在允许范围"}
    if not name:
        return {"ok": False, "error": "名称不能为空"}
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    t = (base / name).resolve()
    if base not in t.parents:
        return {"ok": False, "error": "非法路径"}
    try:
        t.mkdir(parents=True, exist_ok=True)
        return {"ok": True, "path": str(t)}
    except Exception as e:
        return {"ok": False, "error": type(e).__name__ + ": " + str(e)}


@app.get("/api/sessions")
def api_sessions():
    return {"list": _store.list_sessions(), "current": _store.current_id()}


@app.post("/api/session/rename")
def api_sess_rename(body: dict):
    sid = body.get("id", "")
    title = (body.get("title") or "新会话")[:18]
    if _store.get(sid) is not None:
        _store.rename(sid, title)
    return {"ok": True}


# ---------- 对话 ----------
class ChatReq(BaseModel):
    text: str
    session_id: str | None = None
    preset: str = "A"          # S | A | B | auto
    template_id: str = "default"
    corpus: str = "textbook"   # textbook | rj_textbooks | pdf:<source>


def _code(preset: str) -> str:
    if "S" in (preset or "") or "总指挥" in (preset or "") or "监督" in (preset or ""):
        return "S"
    if "B" in (preset or ""):
        return "B"
    if "自动" in (preset or "") or preset == "auto":
        return "auto"
    return "A"


def _tag(mode: str) -> str:
    if mode == "S":
        return "**【总指挥 · 监督 Agent】**\n\n"
    return "**【Agent A · 课本教练（答疑）】**" if mode == "A" else "**【Agent B · 教案 Agent】**"


_TAG_PREFIX_RE = re.compile(r"^(?:\*\*)?\s*【\s*(?:Agent\s*[ABS]\b|总指挥)[^】]*】\s*(?:\*\*)?[ \t]*")


def _strip_agent_tag(md: str) -> str:
    """摘掉正文**开头**那个 Agent 标签（`_tag()` 加的「**【Agent B · 教案 Agent】**」）。

    会话里存的回答正文带这个标签，但它属于聊天装饰、不属于文档：不摘掉会渲染进右侧预览、
    跟着进导出文件，还会让预览标题退化成「未命名文档」（首行不是标题，取不到题名）。

    坑：mode B/A 的标签**后面没有换行**，和正文首个标题粘在同一行
    （`**【Agent B · 教案 Agent】**# 3.3 幂函数`），所以按"行首前缀"摘，而不是"整行匹配"；
    只认开头，且括号里必须是 Agent/总指挥，正文里的【…】不动。
    """
    s = str(md or "")
    head = re.match(r"[ \t\r\n]*", s)          # 跳过开头的空白行
    rest = s[head.end():]
    hit = _TAG_PREFIX_RE.match(rest)
    if not hit:
        return s
    return rest[hit.end():].lstrip("\r\n")


def _tagged(mode: str, md: str) -> str:
    """会话存储统一只保留一个 Agent 标签，兼容模型自己输出标签的情况。"""
    clean = str(md or "")
    while True:
        stripped = _strip_agent_tag(clean)
        if stripped == clean:
            break
        clean = stripped
    return _tag(mode) + clean



def _sse(gen, holder=None):
    """把生成器包成 SSE。holder 是 skill-create 的"半自动提示"通道（见 _chat_gen）：
    生成器在开头算好提示文案放进 holder，这里在收尾事件（t=done）上把那一句附到回答末尾
    —— 这样任何前端都能看见，不依赖前端新增事件类型。"""
    def stream():
        hint = ""
        for ev in gen:
            if holder and holder.get("hint"):
                hint = str(holder["hint"])
            if hint and isinstance(ev, dict) and ev.get("t") == "done" and not ev.get("suggest"):
                ev = dict(ev)
                ev["text"] = (str(ev.get("text") or "") + "\n\n" + hint).strip()
                ev["suggest"] = hint
            yield "data: " + json.dumps(ev, ensure_ascii=False) + "\n\n"
    return StreamingResponse(stream(), media_type="text/event-stream")


def _pick_session(req: ChatReq) -> str:
    """选中或新建一个会话（沿用旧语义：缺省取最早会话，无则新建）。"""
    sid = req.session_id or _store.current_id()
    if sid is None or _store.get(sid) is None:
        sid = _store.new()
    return sid


def _chat_gen(req: ChatReq, holder: dict | None = None):
    # 注意：这里不能用 logsetup.request（contextvars）包裹整个生成器——
    # SSE 经 Starlette 线程池逐段恢复，每次 next() 在不同 context 副本，
    # __exit__ 的 reset(token) 会抛 "created in a different Context"。
    # 请求级事件改由各 _stream_* 在生成器内直接 logsetup.event 记录（rid 缺省 "-"）。
    text = (req.text or "").strip()
    if not text:
        yield {"t": "error", "text": "输入为空"}
        return

    code = _code(req.preset)
    sid = _pick_session(req)
    # skill-create 埋点（2026-10-02，见 docs/21）：先把"上一轮"补成运行痕迹（幂等），
    # 再清空本轮的技能调用缓冲。包 try/except —— 埋点永远不能影响回答。
    try:
        from edu_agent.skill_market import runs as _runs

        _runs.sweep_session(sid, session_reader=lambda s: _store.get(s))
        _runs.begin_turn(sid)
    except Exception:  # noqa: BLE001
        pass
    # skill-create 半自动提示（docs/22 M8）：上一轮补记完成后，看看这个会话里跑过的流程
    # 有没有已经跨会话跑通的。**只提议**：文案挂在收尾事件上（_sse 负责附加），
    # 同一 (会话,候选) 只提示一次（miner._prompts.jsonl 只追加）。
    try:
        from edu_agent.skill_market import miner as _miner

        _pr = _miner.prompt_for_session(sid)
        if _pr.get("ask"):
            if holder is not None:
                holder["hint"] = _pr.get("text") or ""
                holder["candidate_id"] = _pr.get("candidate_id")
            yield {"t": "suggest", "text": _pr.get("text") or "",
                   "candidate_id": _pr.get("candidate_id")}
    except Exception:  # noqa: BLE001
        pass
    _store.append(sid, "user", text)
    _store.set_title_if_new(sid, text.splitlines()[0][:18])
    yield {"t": "session", "id": sid,
           "title": (_store.get(sid) or {}).get("title", "新会话")}

    # 纯问候短路（2026-09-27）：问候不是知识问题，不必走「检索教材 + 生成」。
    # 实测：只发一句「你好」时，检索会拿回几段毫不相关的课文，本地模型于是
    # 顺着它们编出一大段教材讲解（如「列车加速到 350km/h…S=350s…」整段复述）。
    # 规则很克制：只有「整条消息就是一句问候」才会走到这里（见 edu_agent/greet.py）。
    greet_text = greet.reply(text)
    if greet_text:
        yield {"t": "status", "text": "打个招呼～"}
        yield {"t": "d", "text": greet_text}
        _store.append(sid, "assistant", _tag("A") + greet_text,
                      meta={"mode": "A", "citations": []})
        yield {"t": "done", "mode": "A", "text": greet_text, "citations": []}
        return

    # 情绪关心（2026-09-27）：用户情绪低落时**先说人话**，而不是甩数学知识点。
    # 只有「纯情绪 / 宣泄 / 自我否定」才在这里短路（不检索、不调模型，毫秒级）；
    # 带具体学习请求的（如「好烦，这题还是不会，帮我讲讲单调性」）照常走答疑，
    # 但会额外注入一条「先共情再作答」的提示 —— 见 _stream_coach 里的 care.study_hint()。
    care_hit = care.reply(text)
    if care_hit:
        care_kind, comfort = care_hit
        yield {"t": "status", "text": "先陪你说两句…"}
        yield {"t": "d", "text": comfort}
        _store.append(sid, "assistant", _tag("A") + comfort,
                      meta={"mode": "A", "citations": [], "care": care_kind})
        yield {"t": "done", "mode": "A", "text": comfort, "citations": []}
        return

    if code == "auto":
        code = routing.decide(text)

    if code == "A":
        yield from _with_plan_options(_stream_coach(sid, req, text), req, text, code)
        return
    if code == "S":
        yield from _with_plan_options(_stream_supervisor(sid, req, text), req, text, code)
        return
    yield from _with_plan_options(_stream_planner(sid, req, text), req, text, code)


# ---------- 教案/PPT 联动：生成「生成教案模板 / 按模板生成」两个选项（2026-09-12 一期） ----------
def _plan_options_event(req: ChatReq, text: str, code: str, last: dict | None) -> dict | None:
    """是否需要给用户那两个选项。B（教案 Agent）恒给；A/S 看生成层带回的信号。"""
    if code == "B":
        reason = "plan_mode"
    else:
        po = (last or {}).get("plan_options") or {}
        if not po.get("suggest"):
            return None
        reason = po.get("reason") or "intent"
    try:
        templates = list_templates()
        current = get_current().template_id
    except Exception:
        templates, current = [], "default"
    # 有没有「可以填进模板的教案正文」：本轮就是教案，或会话里已经出过教案
    sid = (getattr(req, "session_id", "") or "").strip() or (_store.current_id() or "")
    has_plan = code == "B" or bool(_last_plan_markdown(sid))
    options = [
        {"id": "gen_template", "label": "生成教案模板",
         "hint": "把这节课抽成板块骨架（含填写提示与表格表头），存进模板库可反复套用"},
        {"id": "use_template", "label": "按模板生成",
         "hint": "选一个模板，教案 Agent 会严格按它的板块名称与顺序产出",
         "templates": templates, "current": current},
    ]
    if has_plan:
        options.append(
            {"id": "fill_center", "label": "按模板填入",
             "hint": "不重新生成：按标题填进模板对应板块，结果直接进右侧预览面板"})
    return {
        "t": "options", "kind": "plan", "reason": reason,
        "topic": (text or "").strip()[:40],
        "options": options,
    }


def _with_plan_options(gen, req: ChatReq, text: str, code: str):
    """把 Agent 流原样透传，收尾时按需补一个 options 事件。"""
    last = None
    for ev in gen:
        if isinstance(ev, dict) and ev.get("t") == "done":
            last = ev
        yield ev
    opt = _plan_options_event(req, text, code, last)
    if opt:
        yield opt


def _stream_coach(sid: str, req: ChatReq, text: str):
    """Agent A（课本教练）：检索 -> 流式逐字（event 面与旧版一致）。"""
    from edu_agent.generate import ask_stream
    from edu_agent.memory import profile_memory_prefix, record_sections

    sess = _store.get(sid) or {"messages": []}
    history = sess["messages"][:-1][-4:]
    # 长期记忆：所有模式都注入（原来只在 corpus=textbook 时注入，导致备课时记不住老师偏好）；
    # 关掉记忆开关时 profile_memory_prefix 自身返回空串（见 memory.memory_enabled）。
    prefix = profile_memory_prefix(MEM_PROFILE)
    # 2026-09-19 追加两路（都失败静默，绝不影响回答）：
    #   ① 自己积累的技能清单（经验→技能回路的"读回"端，见 skills_local）
    #   ② 相关历史对话片段（跨会话检索，见 session_search）
    try:
        from edu_agent import session_search, skill_catalog

        session_search.refresh()                       # 增量重建（只有新消息的会话才重做）
        # 技能：DSH 式「目录 + 按需加载」——目录只给名字+描述；用户点名或问题相关时把**正文**注入。
        # 写一个 SKILL.md 就等于创建技能（下次对话生效），见 edu_agent/skill_catalog.py。
        extra = [skill_catalog.prompt_block(text),
                 session_search.past_prefix(text, exclude_sid=sid)]
        prefix = "；".join([p for p in ([prefix] + extra) if p])
    except Exception:  # noqa: BLE001
        pass
    # 2026-09-22 追加第三路：知识库 wiki（"学会的讲义"，见 edu_agent/wiki_index.py）。
    #   与上面两路同一哲学：失败静默、绝不影响回答；命中时把概念页小节拼进前缀，
    #   并要求回答引用 [[页面名]]（wiki 页本身带 ^[pXX] 页码溯源）。
    wiki_evidence = ""
    if req.corpus == "textbook":
        try:
            from edu_agent import wiki_index

            wiki_evidence = wiki_index.prompt_block(text, k=2)
        except Exception:  # noqa: BLE001
            pass

    src = None
    pdir = None
    pcol = None
    if req.corpus != "textbook" and req.corpus.startswith("pdf:"):
        src = req.corpus.split(":", 1)[1]
        pdir = pdf_upload.VDB_DIR
        pcol = pdf_upload.COLLECTION
        yield {"t": "status", "text": "正在检索 PDF《" + src + "》并作答…"}
    elif req.corpus == "rj_textbooks" or req.corpus.startswith("rj_textbooks:"):
        from edu_agent.rj_textbooks import COLLECTION as RJ_TEXTBOOKS_COLLECTION, VDB_DIR as RJ_TEXTBOOKS_DIR

        pcol = RJ_TEXTBOOKS_COLLECTION
        pdir = RJ_TEXTBOOKS_DIR
        if req.corpus.startswith("rj_textbooks:"):
            src = req.corpus.split(":", 1)[1]
            yield {"t": "status", "text": "正在检索选定教材并作答…"}
        else:
            yield {"t": "status", "text": "正在检索人教版全科教材库并作答…"}
    else:
        yield {"t": "status", "text": "正在检索教材并作答…"}
    acc = ""
    final_answer = ""
    citations = []
    # 学生带着情绪提问时：**由平台侧先给出一句确定的安慰**，再照常答疑。
    # 不能只靠提示词 —— 实测本地小模型会完全无视「先共情再作答」的要求，张口就讲知识点。
    emp_line = care.empathy_line(text)
    emp_prefix = (emp_line + "\n\n") if emp_line else ""
    if emp_prefix:
        yield {"t": "d", "text": emp_prefix}
    plan_opt: dict = {}
    try:
        for ev in ask_stream(text, history=history, memory_prefix=prefix,
                             evidence_prefix=wiki_evidence, source=src,
                             persist_dir=pdir, collection=pcol,
                             empathy=care.study_hint(text)):
            ty = ev.get("type")
            if ty == "delta":
                acc += ev.get("text", "")
                yield {"t": "d", "text": ev.get("text", "")}
            elif ty == "done":
                final_answer = emp_prefix + (ev.get("answer_md") or acc)
                citations = ev.get("citations") or []
                plan_opt = ev.get("plan_options") or {}
                record_sections("default", citations)
    except Exception as e:
        yield {"t": "error", "text": "A 出错：" + type(e).__name__ + ": " + str(e)}
        return
    _store.append(sid, "assistant", _tagged("A", final_answer),
                  meta={"mode": "A", "citations": citations,
                        "pages": sorted({c.get("page") for c in citations if c.get("page")})})
    # 长期记忆（对齐 DeepSeek「记忆」）：
    #  ① 显式「记住：xxx」→ 立刻落库，并在流里回执给用户（让用户看得见、可去设置里删）
    #  ② 其余交给后台线程自动抽取，不阻塞回答、失败静默
    try:
        from edu_agent import memory as _mem, memory_auto as _ma, skills_local as _sk

        fact = _ma.explicit_fact(text)
        if fact and _mem.add_fact(MEM_PROFILE, fact, kind="preference", source="user"):
            yield {"t": "status", "text": "已记住：" + fact}
        _ma.extract_async(text, final_answer, MEM_PROFILE)
        # 经验→技能（2026-09-19）：用户说「存成技能：名字」→ 把这一轮的有效做法写成 SKILL.md，
        # 下次回答前会随技能清单注入提示词（这才是"自我迭代"的闭环）。
        hint = _sk.skill_intent(text)
        if hint is not None and final_answer.strip():
            saved = _sk.save_from_answer(text, final_answer, hint)
            if saved and saved.get("ok"):
                yield {"t": "status", "text": "已存成技能：" + saved["name"]}
        drop = _sk.delete_intent(text)
        if drop:
            gone = _sk.delete_skill(drop)
            if gone.get("ok"):
                yield {"t": "status", "text": "已归档技能：" + drop}
    except Exception:  # noqa: BLE001
        pass
    done = {"t": "done", "mode": "A", "text": final_answer, "citations": citations}
    if plan_opt.get("suggest"):
        done["plan_options"] = plan_opt
    if _deck_flag(text):                      # 显式「生成课件」→ 前端据此给导出入口
        done["deck"] = True
    yield done


def _stream_supervisor(sid: str, req: ChatReq, text: str):
    """总指挥（监督 Agent S）：先出任务计划，再派单 A 或 B（event 面同旧版）。"""
    from edu_agent.supervisor import supervise_stream

    sess_buf = []
    final_text = ""
    citations = []
    html_name = ""
    for ev in supervise_stream(text, template_id=req.template_id or ""):
        ty = ev.get("t")
        if ty == "plan":
            plan_lines = ["[总指挥] 任务计划："]
            for pp in ev.get("plan", []):
                plan_lines.append("- " + pp.get("step", "") + " → " + pp.get("assignee", ""))
            yield {"t": "status", "text": "\n".join(plan_lines)}
        elif ty == "d":
            sess_buf.append(ev.get("text", ""))
            yield {"t": "d", "text": ev.get("text", "")}
        elif ty == "done":
            final_text = ev.get("text") or "".join(sess_buf)
            citations = ev.get("citations") or []
            html_name = ev.get("html") or ""
            _store.set_last_html(sid, html_name)
        elif ty == "error":
            yield ev
            return
    # 总指挥如果产出了教案（html_name 非空），同样下发结构化 payload
    doc = (_doc_payload(final_text) if (html_name and capabilities.session_preview_ready()) else None)
    meta = {"mode": "S", "citations": citations,
            "pages": sorted({c.get("page") for c in citations if c.get("page")})}
    if doc:
        meta["doc"] = doc
    _store.append(sid, "assistant", _tagged("S", final_text), meta=meta)
    done = {"t": "done", "mode": "S", "agent": "A" if not html_name else "B",
            "text": final_text, "citations": citations, "html": html_name}
    if doc:
        done["doc"] = doc
    if _deck_flag(text):
        done["deck"] = True
    yield done


def _deck_flag(text: str) -> bool:
    """这句话是不是「显式要课件」——决定 done 事件要不要带 deck 标记。

    判定权在 courseware.wants_deck（只认「动作词 + 课件/PPT」相邻，疑问句不算），
    这里只做一层吞异常的包装：课件模块出问题也绝不能让对话链路出错。
    """
    try:
        from edu_agent import courseware

        return courseware.wants_deck(text)
    except Exception:  # noqa: BLE001
        return False


def _doc_payload(md: str, title: str = "", grade: str = "") -> dict:
    """把一份教案 Markdown 整理成「结构化文档 payload」（纯内存，不落盘、不调 WPS）。

    形状沿用 wps 导出 payload 的字段命名，前端 4 个 Tab 共用这一份数据源。
    """
    md = _strip_agent_tag(str(md or ""))
    slides: list[dict] = []
    title_from_md = ""
    if md.strip():
        try:
            data = wps_export.slides_from_markdown(md)
            slides = data.get("slides") or []
            title_from_md = str(data.get("title") or "")
        except Exception:  # noqa: BLE001 纯整理，解析失败不该让对话出错
            slides = []
    blocks = _plan_blocks(md) if md.strip() else []
    minutes = 0
    for s in slides:
        try:
            minutes += int(s.get("minutes") or 0)
        except (TypeError, ValueError):
            pass
    return {
        "title": str(title or "").strip() or title_from_md or "未命名文档",
        "grade": grade,
        "markdown": md,
        "slides": slides,
        "blocks": blocks,
        "stats": {"chars": len(md), "blocks": len(blocks),
                  "slides": len(slides), "minutes": minutes},
    }


def _stream_planner(sid: str, req: ChatReq, text: str):
    """Agent B（教案）：整份生成 + HTML 落盘（event 面同旧版）。"""
    yield {"t": "status", "text": "正在检索并生成教案（约 1~4 分钟）…"}
    try:
        if "|" in text:
            kp, goal = [x.strip() for x in text.split("|", 1)]
        else:
            kp, goal = text, ""
        rec = planner.make_plan(kp, goal or "", template=req.template_id or "")
        md = planner.to_markdown(rec)
        html_path = plan_html.save_html(md, rec.title)
        html_name = html_path.name
        _store.set_last_html(sid, html_name)
        # 结构化 payload：skill 开着才下发给前端（含落库，历史恢复时还能重载）
        doc = (_doc_payload(md, title=rec.title) if capabilities.session_preview_ready() else None)
        meta = {"mode": "B", "citations": [c.model_dump() for c in rec.citations],
                "pages": sorted({c.page for c in rec.citations if c.page})}
        if doc:
            meta["doc"] = doc
        _store.append(sid, "assistant", _tagged("B", md), meta=meta)
        done = {"t": "done", "mode": "B", "text": md, "html": html_name,
                "title": rec.title, "citations": [c.model_dump() for c in rec.citations]}
        if doc:
            done["doc"] = doc
        if _deck_flag(text):                  # 「帮我备一节…要课件」→ 出完教案直接给课件入口
            done["deck"] = True
        yield done
    except Exception as e:
        yield {"t": "error", "text": "B 出错：" + type(e).__name__ + ": " + str(e)}


@app.post("/api/chat")
def api_chat(req: ChatReq):
    holder: dict = {}
    return _sse(_chat_gen(req, holder), holder=holder)


@app.post("/api/session/last_html")
def api_last_html(body: dict):
    sess = _store.get(body.get("session_id", "")) or {}
    return {"html": sess.get("last_html", "")}


# ---------- 会话内文档预览（2026-09-12）----------
# 与「WPS 落盘导出」是**两条独立链路**：
#   · 本接口：payload / markdown → 结构化预览模型（标题 / 板块 / 幻灯片页 / 统计），
#             **纯内存计算：不落盘、不调用 WPS MCP、不查 WPS 能力开关**；
#   · /api/wps/export：同一份 payload / markdown → 落盘 docx / pptx，受 wps-office 开关约束。
# 前端渲染与否由 skill 开关 `doc-session-preview` 决定（见 capabilities.session_preview_ready）。
class DocPreviewReq(BaseModel):
    payload: dict | None = None      # 编排台结构（可选）
    markdown: str = ""               # 教案 Markdown（可选）
    session_id: str = ""


@app.post("/api/session/doc-preview")
def api_session_doc_preview(body: DocPreviewReq):
    """结构化文档 → 前端预览：**只透传**，无任何磁盘 IO、不调用 WPS MCP。

    与 /api/wps/export 是两条独立链路：
      · 本接口：payload → 原样回传（+ 派生页结构供幻灯片 Tab 用），内存计算；
      · 导出接口：同一份 payload → 落盘 docx/pptx，受 wps-office 开关约束。
    """
    payload = body.payload if isinstance(body.payload, dict) else {}
    # markdown 优先取请求体，其次取 payload.markdown；都没有再用会话里最近一份教案兜底
    md = _strip_agent_tag(str(body.markdown or payload.get("markdown") or ""))
    if not md.strip():
        md = _last_plan_markdown(str(body.session_id or "").strip()
                                 or (_store.current_id() or ""))

    # payload 自带的 slides/blocks 优先（Agent 已结构化），否则由 markdown 纯计算派生
    slides = payload.get("slides") if isinstance(payload.get("slides"), list) else []
    blocks = payload.get("blocks") if isinstance(payload.get("blocks"), list) else []
    title = str(payload.get("title") or "").strip()
    title_from_md = ""
    if md.strip():
        try:
            data = wps_export.slides_from_markdown(md)
            title_from_md = str(data.get("title") or "")
            if not slides:
                slides = data.get("slides") or []
        except Exception:  # noqa: BLE001 纯预览，解析失败不该 500
            slides = slides or []
        if not blocks:
            blocks = _plan_blocks(md)
    # 老师在面板里改过幻灯片 → 以改过的那份为准；但**正文后来被重出过**就不闷头用旧的：
    # 改用新正文，并把"你的修改还在会话里、只是没盖上来"这件事告诉前端（docs/19 §12 的不静默覆盖）。
    sid = str(body.session_id or "").strip() or (_store.current_id() or "")
    saved_doc = _store.get_session_doc(sid)
    slides_stale = False
    if saved_doc.get("slides"):
        saved_sig, cur_sig = saved_doc.get("md_sig") or "", (_md_sig(md) if md.strip() else "")
        if saved_sig and cur_sig and saved_sig != cur_sig:
            slides_stale = True
        elif not (payload.get("slides") if isinstance(payload.get("slides"), list) else None):
            slides = saved_doc["slides"]
            title = title or str(saved_doc.get("title") or "")
    slides_edited = bool(saved_doc.get("slides"))
    # 标题优先级：payload 标题 > 正文首个标题 > 占位
    # （历史会话兜底进来的正文原本带「**【Agent B · 教案 Agent】**」标签，
    #   已由 _strip_agent_tag 摘掉，否则这里只会取到标签、退化成「未命名文档」）
    if not title:
        title = title_from_md or "未命名文档"

    minutes = 0
    for s in slides:
        try:
            minutes += int(s.get("minutes") or 0)
        except (TypeError, ValueError):
            pass
    return {
        "ok": True,
        "enabled": capabilities.session_preview_ready(),   # 前端据此决定渲染 or 清空面板
        "title": title,
        "payload": payload,                                # 原样回传（结构 Tab 的原始 JSON）
        "markdown": md,                                    # 原样回传（Markdown Tab 的输入）
        "blocks": blocks,
        "slides": slides,
        "slides_edited": slides_edited,                    # 手动改过？前端据此提示，不静默覆盖
        "slides_stale": slides_stale,                      # 改过、但正文又变了（这次按新正文出）
        "stats": {"chars": len(md), "blocks": len(blocks),
                  "slides": len(slides), "minutes": minutes},
        "note": "内存预览：不落盘、不调用 WPS；需要落盘请走 /api/wps/export",
    }


@app.get("/api/slide/fill/plan")
def api_slide_fill_plan(template_id: str = Query(""), session_id: str = Query(""), title: str = Query("")):
    """**给外部 agent 的读端**：模板槽位清单 + 我们的内容 + 确定性草案（docs/24 的契约）。

    不做 skill、不设开关：这是"另一个会生成教案的 agent"接入我们出 PPT 的接口。
    """
    from edu_agent import slide_fill

    tid = str(template_id or "").strip()
    if not tid:
        return JSONResponse({"ok": False, "error": "缺少 template_id"}, status_code=400)
    slides = _store.get_session_doc(str(session_id or "").strip() or (_store.current_id() or "")).get("slides") or []
    try:
        return slide_fill.plan_payload(tid, slides, title=str(title or ""))
    except (FileNotFoundError, ValueError) as exc:
        return JSONResponse({"ok": False, "schema": slide_fill.SCHEMA, "error": str(exc)}, status_code=400)


class SlideDeckReq(BaseModel):
    """**外部 agent 的写端（一步到位）**：一份教案结构 → 直接出 pptx。"""

    lesson: dict | None = None            # {"title": "...", "slides": [{name, bullets[], minutes}]}
    template_id: str = ""                 # 给了就"按模板填充"；不给就按主题生成
    theme_id: str = ""
    outDir: str = ""
    confirm: bool = False
    session_id: str = ""
    #: R27c：**设计规格**（视觉风格 / 沟通模式 / 品牌 / 数值覆盖 / 逐页版式）。
    #: 不给 = 与以前完全一致（向后兼容）；给了就用它解析出的 tokens 渲染 **同一份 SVG**（预览=导出不变）。
    design: dict | None = None


class SlideDesignPreviewReq(BaseModel):
    """**R27c 的预览端**：给一份 `design`（可带 slides 或让服务端用会话里那份）→ 立刻出预览 SVG。

    只读、不落盘 —— 所以不需要 `confirm`（与 `/api/slide/render` 同一档）。
    """

    design: dict | None = None
    slides: list[dict] | None = None
    title: str = ""
    session_id: str = ""
    annotate: bool = True                 # 默认带可编辑盒子（预览要能点着改）


class SlideAuditReq(BaseModel):
    """**R27c 的自查端**：把一份 `slides` 跑一遍审计，回"哪一页、哪一条、怎么改"。

    只读、不落盘。给了 `design` 就用它解析出的数值审（与渲染同一份口径）。
    """

    slides: list[dict] | None = None
    session_id: str = ""
    design: dict | None = None
    theme_id: str = ""


@app.get("/api/slide/spec")
def api_slide_spec():
    """**给 agent 的规格包**（`docs/24` §7.4 的那一个）：我这边认什么、有什么可选、红线在哪。

    与 `/api/slide/design/catalog` 的区别：那个讲"**设计上可调什么**"（风格/品牌/版式），
    这个讲"**内容上怎么交**"（认哪些键、密度上限、版式白名单、返回里哪些字段要读）。
    """
    from edu_agent import design_spec as _ds
    from edu_agent import layout_data as _ld
    from edu_agent import slide_deck as _sd

    base = _ds.base_tokens()
    cat = _ds.catalog()
    usable = list(_ld.ids())          # 16:9 且**去重**（跨族重名只算一次，与 slide_deck.LAYOUTS 一致）
    return {
        "ok": True,
        "schema": "edu-agent.slide-spec.v1",
        "endpoints": {
            "spec": "GET /api/slide/spec（本文）",
            "design_catalog": "GET /api/slide/design/catalog?full=true（视觉风格/模式/品牌/版式 与它们的规则）",
            "preview": "POST /api/slide/design/preview（按 design 出预览，不落盘）",
            "audit": "POST /api/slide/audit（自查：error/warn + 怎么改）",
            "save": "POST /api/slide/save（把 slides 存回会话）",
            "deck": "POST /api/slide/deck（出 pptx，落盘要 confirm）",
            "fill_plan": "GET /api/slide/fill/plan（模板槽位 + 草案）",
            "fill": "POST /api/slide/fill（按 mapping 填模板）",
            "object": "POST /api/slide/object（自由层对象 add/patch/delete/up/down/set）",
            "image_attach": "POST /api/slide/image/attach（给个图片 URL 挂到某页）",
        },
        "accepted_keys": list(_sd.SLIDE_KEYS),
        "dropped_silently": ["不认识的键会被丢掉（不报错）—— 只认上面这些"],
        "limits": {"chars_per_block": 400, "notes_chars": 2000, "objects_per_page": 30,
                   "image_mb": 10, "svg_override_bytes": 200_000},
        "density": base["density"],
        "canvas": base["canvas"],
        "type_steps": {k: base["type"][k] for k in ("cover", "title", "label", "body", "caption")},
        "ranges": base["ranges"],
        "layouts": {"core": list(_sd.ppt_svg.LAYOUTS), "usable_from_registry": usable,
                    "total_usable": len(_sd.LAYOUTS),
                    "custom_prefix": "reg:<id>"},
        "must_read_in_response": ["warnings[]（越界夹回 / 密度提示 —— 界面不显示，必须自己读）",
                                 "checks[]（导出后的硬验收结论）",
                                 "adjustments[]（模板填充时的压缩留痕）"],
        "locked": _ds.locked(),
        "hints": ["先 GET /api/slide/spec 看认什么，再 GET /api/slide/design/catalog 挑风格与版式，",
                  "改完 POST /api/slide/audit 自查（error 必须为 0），最后 POST /api/slide/deck 出稿。"],
    }


@app.post("/api/slide/audit")
def api_slide_audit(body: SlideAuditReq):
    """把一份 `slides` 跑一遍审计（只读）：回 `errors/warnings/infos`，每条都带"怎么改"。"""
    from edu_agent import design_spec as _ds
    from edu_agent import slide_audit as _sa
    from edu_agent import slide_deck as _sd

    slides_in = body.slides
    if not slides_in:
        doc = _store.get_session_doc(str(body.session_id or "").strip() or (_store.current_id() or ""))
        slides_in = doc.get("slides") or []
    slides = _sd.clean_slides(slides_in)
    if not slides:
        return JSONResponse({"ok": False, "schema": "edu-agent.slide-audit.v1",
                             "error": "没有可审的页：给 slides，或先让会话里有一份教案"}, status_code=400)
    r = _ds.resolve(body.design) if isinstance(body.design, dict) and body.design else None
    if r:
        slides = _ds.apply_to_slides(slides, r)
    tokens = r.as_tokens() if r else None
    theme_id = r.visual_style if r else (body.theme_id or "tech-editorial")
    built = _sd.build_deck(theme_id, slides, tokens=tokens)
    rep = _sa.audit_pages(theme_id, built["pages"], layouts=built["layouts"], tokens=tokens)
    return {
        "ok": rep["ok"],
        "schema": "edu-agent.slide-audit.v1",
        "theme_id": rep.get("theme_id"),
        "stats": rep["stats"],
        "errors": rep["errors"], "warnings": rep["warnings"], "infos": rep["infos"],
        "pages": [{"page": i + 1, "layout": built["layouts"][i], "findings": row["findings"]}
                  for i, row in enumerate(rep["pages"])],
        "design": ({"visual_style": r.visual_style, "applied": r.applied, "warnings": r.warnings}
                   if r else None),
        "hints": ["error 必须清零再出稿；warning 可以留，但要逐条判断是不是有意的。",
                  "每条 findings 都带 `fix`（怎么改）—— 别拿「删内容」当解法，宁可拆页。"],
    }


@app.post("/api/slide/design/preview")
def api_slide_design_preview(body: SlideDesignPreviewReq):
    """按设计规格出**预览**（R27c）：`design` → 解析数值 → 渲染每页 SVG（不落盘）。

    返回 `{ok, pages, boxes, layouts, resolved, applied, warnings, checks}`：
    * `resolved` 是最终生效的数值（palette/type/geometry/density/逐页版式）—— agent 据此知道自己调成了什么；
    * `warnings` 里是"越界被夹/不认识的风格/版式"这类说明（**界面不显示，agent 必须读**）；
    * `pages` 与导出用的是**同一份渲染**（`slide_deck.build_deck`），所以"预览=导出"没被破坏。
    """
    from edu_agent import design_spec as _ds
    from edu_agent import slide_deck as _sd

    slides_in = body.slides
    if not slides_in:
        doc = _store.get_session_doc(str(body.session_id or "").strip() or (_store.current_id() or ""))
        slides_in = doc.get("slides") or []
    slides = _sd.clean_slides(slides_in)
    if not slides:
        return JSONResponse({"ok": False, "schema": "edu-agent.design-preview.v1",
                             "error": "没有可渲染的页：给 slides，或先让会话里有一份教案"},
                            status_code=400)
    r = _ds.resolve(body.design)
    slides = _ds.apply_to_slides(slides, r)
    tokens = r.as_tokens()
    built = _sd.build_deck(r.visual_style or body.title or "design", slides,
                           title=str(body.title or "").strip(), tokens=tokens,
                           annotate=bool(body.annotate))
    if not built.get("ok"):
        return JSONResponse({"ok": False, "schema": "edu-agent.design-preview.v1",
                             "error": built.get("error") or "渲染失败"}, status_code=200)
    return {"ok": True, "schema": "edu-agent.design-preview.v1",
            "pages": built["pages"], "layouts": built["layouts"],
            "boxes": built.get("boxes") or [], "canvas": built.get("canvas"),
            "fonts": built.get("fonts"), "stats": built.get("stats"),
            "resolved": r.summary(), "applied": r.applied,
            "warnings": list(r.warnings) + list(built.get("warnings") or []),
            "locked": _ds.locked()}


@app.post("/api/slide/deck")
def api_slide_deck(body: SlideDeckReq):
    """一步出稿：`lesson` → pptx（有模板走填充，没模板走我们的版式；`design` 给了就按规格渲染）。"""
    from edu_agent import permissions
    from edu_agent import slide_fill

    blocked = permissions.guard("export_pptx", bool(body.confirm))
    if blocked:
        return blocked
    lesson = body.lesson if isinstance(body.lesson, dict) else {}
    slides = [s for s in (lesson.get("slides") or []) if isinstance(s, dict)]
    title = str(lesson.get("title") or "").strip()
    if not slides:
        return JSONResponse({"ok": False, "schema": slide_fill.SCHEMA,
                             "error": "lesson.slides 为空：至少要有一个环节"}, status_code=400)
    out_dir = (body.outDir or "").strip() or None
    tid = str(body.template_id or "").strip()
    if tid:
        out = slide_fill.fill_pptx(tid, slides, title=title, out_dir=out_dir)
    else:
        tokens = None
        if isinstance(body.design, dict) and body.design:
            from edu_agent import design_spec as _ds
            from edu_agent import slide_deck as _sd

            r = _ds.resolve(body.design)
            slides = _ds.apply_to_slides(_sd.clean_slides(slides), r)
            tokens = r.as_tokens()
            out = _sd.export_deck_pptx(body.theme_id, slides, title=title, out_dir=out_dir,
                                       tokens=tokens)
            out["design"] = {"resolved": r.summary(), "applied": r.applied,
                             "warnings": list(r.warnings)}
        else:
            out = slide_deck.export_deck_pptx(body.theme_id, slides, title=title, out_dir=out_dir)
    # 契约（docs/24 §7）说成功响应里带 `checks[]`：导出内核把它放在 `verify.checks`，
    # 这里**拉平一层**（agent 不必知道我们的内部结构）。
    if isinstance(out, dict) and "checks" not in out:
        out["checks"] = (out.get("verify") or {}).get("checks") or []
    out["schema"] = slide_fill.SCHEMA
    return out


# ---------- 幻灯片渲染收口（docs/22 D13 / docs/23 M1） ----------
#   · /api/slide/themes：内置主题清单（面板的下拉用），不设门禁；
#   · /api/slide/render：会话数据 → 每页 SVG，**纯内存**；面板只做等比缩放与翻页。
#   渲染与导出共用同一份 SVG（导出见 M3 的 kind="pptx-deck"），所以"预览 = 导出"成立。
class SlideRenderReq(BaseModel):
    theme_id: str = ""               # 空 = 用默认主题
    template_id: str = ""            # 导入的演示模板（借它的配色/字体；给了就压过 theme_id）
    slides: list[dict] | None = None  # 会话里那份幻灯片数据（可为空 → 由 markdown 派生）
    title: str = ""
    markdown: str = ""               # 没给 slides 时的兜底来源
    session_id: str = ""
    annotate: bool = False           # True = 给文字槽打 data-slot 并回 boxes（**页内编辑用**；导出不传）


class SlideSlotReq(BaseModel):
    """页内编辑一格：改哪个槽、改成什么（槽位命名见 `slide_deck.apply_slot`）。

    `patch`（R14）是"这一格被拖动/改过大小/改过层级/被删掉"的样子：
    `{dx, dy, dw, dh, size, color, align, lh, z, hidden}` —— 与 `text` 可以一起给，也可以只给一个。
    """

    session_id: str = ""
    index: int = 0                   # 第几页（0 起）
    slot: str = ""                   # 如 title / block:1 / left:bullet:0
    text: str | None = None          # 不给 = 只改样子（拖动/改大小/层级/删除）
    patch: dict | None = None        # R14：几何/样式/层级/删除（只给 patch 就是"只挪字不改字"）
    slides: list[dict] | None = None  # 不给就取会话里那份
    title: str = ""


class SlideSaveReq(BaseModel):
    session_id: str = ""
    slides: list[dict] | None = None
    title: str = ""
    markdown: str = ""              # 存的时候记下"当时对着哪份教案正文改的"


class SlideFillReq(BaseModel):
    template_id: str = ""
    session_id: str = ""
    slides: list[dict] | None = None
    title: str = ""
    outDir: str = ""
    confirm: bool = False           # 落盘类操作，先过权限闸门


class SlideImageReq(BaseModel):
    """联网取图（R18 前置）：把一个图片地址取回本地并挂到某一页。

    `url` = 同事的 agent / 老师给的公网图片地址；`index` = 挂到第几页（0 起）；
    `slot` = 这一页的哪个位置（现在只有 `media` 版式有图位，默认就是它）。
    `action`：`attach`（取图并挂上）/ `detach`（卸下这一页的图）。
    """

    action: str = "attach"
    session_id: str = ""
    index: int = 0
    url: str = ""
    alt: str = ""
    credit: str = ""
    slides: list[dict] | None = None
    title: str = ""
    confirm: bool = False           # 取图要落盘（data/uploads/），先过权限闸门


def _md_sig(md: str) -> str:
    """教案正文的指纹：用来判断"改幻灯片之后，正文有没有被重出过"。"""
    return hashlib.sha1((md or "").encode("utf-8")).hexdigest()[:12]


class SlideFindReq(BaseModel):
    """跨页查找 / 替换（R18）：与页内编辑**同一条写路径**（`slide_deck.apply_slot`）。

    `scope`：`all`（整册，默认）/ `page`（只当前页）；`slots` 给了就只动那几个槽（"只替换这一处"）。
    `replace` 给空串 = 删除匹配（WPS 允许空替换）。`query` 按**字面**找，不当正则。
    """

    session_id: str = ""
    query: str = ""
    replace: str = ""
    index: int = 0
    scope: str = "all"
    slots: list[str] | None = None
    case_sensitive: bool = False
    do: str = "find"                # find | replace
    slides: list[dict] | None = None
    title: str = ""


@app.post("/api/slide/find")
def api_slide_find(body: SlideFindReq):
    """**跨页查找 / 替换**：查找只读；替换走 `slide_deck.replace_text`（内部用 `apply_slot`）后落库。

    为什么单开一个接口而不是前端循环调 `/api/slide/slot`：一次替换可能命中十几处、跨好几页，
    循环调用会写十几次会话（还可能中途失败留下半成品）。这里一次算完、一次落库。
    """
    slides = [s for s in (body.slides or []) if isinstance(s, dict)]
    sid = str(body.session_id or "").strip() or (_store.current_id() or "")
    if not slides:
        slides = [s for s in (_store.get_session_doc(sid).get("slides") or []) if isinstance(s, dict)]
    if not slides:
        return JSONResponse({"ok": False, "error": "没有可查找的幻灯片（先有教案内容）"}, status_code=400)
    query = str(body.query or "")
    if not query.strip():
        return {"ok": True, "hits": [], "changed": 0, "slides": slides, "query": "", "session_id": sid}
    pages = None if str(body.scope or "all") == "all" else [int(body.index or 0)]
    if str(body.do or "find").lower() != "replace":
        hits = slide_deck.find_text(slides, query, case_sensitive=bool(body.case_sensitive),
                                    pages=pages)
        return {"ok": True, "query": query, "hits": hits,
                "hit_pages": sorted({h["index"] for h in hits}), "changed": 0,
                "slides": slides, "session_id": sid}
    out = slide_deck.replace_text(slides, query, str(body.replace or ""),
                                  case_sensitive=bool(body.case_sensitive), pages=pages,
                                  slots=body.slots)
    cleaned = slide_deck.clean_slides(out["slides"])
    cur = dict(_store.get_session_doc(sid) or {})
    cur["slides"] = cleaned
    cur["slides_edited"] = True
    if str(body.title or "").strip():
        cur["title"] = str(body.title).strip()
    _store.set_session_doc(sid, cur)
    return {"ok": True, "query": query, "replace": str(body.replace or ""),
            "hits": out["hits"], "changed": out["changed"], "pages": out["pages"],
            "page_count": len(out["pages"]), "slides": cleaned, "session_id": sid}


@app.post("/api/slide/save")
def api_slide_save(body: SlideSaveReq):
    """把面板里改过的幻灯片**存回会话**（`session_docs` 表；老库不需要迁移）。

    存的只是一份数据（页 / 版式 / 文字），渲染仍由 `/api/slide/render` 现场出 SVG ——
    所以"改完预览与导出同时变"是结构保证，不需要额外同步。
    同时记下当时正文的指纹：正文后来被重出过的话，预览会改用新正文并把这件事告诉前端（见 doc-preview）。
    """
    sid = str(body.session_id or "").strip() or (_store.current_id() or "")
    if not sid:
        return JSONResponse({"ok": False, "error": "没有会话可存（先新建/选择会话）"}, status_code=400)
    slides = slide_deck.clean_slides(body.slides)
    md = str(body.markdown or "")
    doc = {"slides": slides, "title": str(body.title or "").strip(),
           "md_sig": _md_sig(md) if md.strip() else "",   # 没给正文就不记指纹（也就无从"过期"）
           "slides_edited": True}
    _store.set_session_doc(sid, doc)
    return {"ok": True, "session_id": sid, "slides": slides, "slides_edited": True,
            "warnings": slide_deck.length_warnings(slides), "pages": len(slides)}


@app.get("/api/slide/themes")
def api_slide_themes():
    """内置主题清单 + 本机字体是否可用（面板提前提示，不用等到导出才发现）。"""
    try:
        items = slide_deck.themes()
    except Exception as exc:  # noqa: BLE001 主题文件坏掉不该把面板打死
        return {"ok": False, "enabled": capabilities.session_preview_ready(),
                "error": f"读主题失败：{exc}", "default": "", "themes": []}
    return {"ok": True, "enabled": capabilities.session_preview_ready(),
            "default": slide_deck.default_theme_id(), "themes": items}


@app.get("/api/slide/template/align")
def api_slide_template_align(template_id: str = Query("")):
    """槽位对齐的**读端**：每种结构给一个代表页 + 每个文本框的当前角色（老师据此改）。"""
    from edu_agent import slide_template

    tid = str(template_id or "").strip()
    if not tid:
        return JSONResponse({"ok": False, "error": "缺少 template_id"}, status_code=400)
    return slide_template.align_overview(tid)


class AlignReq(BaseModel):
    template_id: str = ""
    signature: str = ""
    roles: dict = {}                      # {shape_index: title|body|decoration}


@app.post("/api/slide/template/align")
def api_slide_template_align_save(body: AlignReq):
    """槽位对齐的**写端**：老师标一次，所有同结构页都按它填。"""
    from edu_agent import slide_template

    tid = str(body.template_id or "").strip()
    if not tid or not str(body.signature or "").strip():
        return JSONResponse({"ok": False, "error": "缺少 template_id 或 signature"}, status_code=400)
    data = slide_template.save_align(tid, body.signature, body.roles or {})
    return {"ok": True, "template_id": tid, "signature": body.signature,
            "roles": (data.get("pages") or {}).get(body.signature) or {}}


@app.get("/api/slide/templates")
def api_slide_templates():
    """演示模板清单：内置（数值）+ 导入的 `.pptx`（**只借配色/字体/槽位**）。"""
    from edu_agent import slide_template

    try:
        items = slide_template.list_templates()
    except Exception as exc:  # noqa: BLE001 模板库坏了不该把面板打死
        return {"ok": False, "error": f"读模板库失败：{exc}", "templates": []}
    return {"ok": True, "enabled": capabilities.session_preview_ready(),
            "builtin": [t for t in items if t["kind"] == "json"],
            "imported": [t for t in items if t["kind"] == "pptx"]}


@app.post("/api/slide/template/delete")
def api_slide_template_delete(body: dict):
    """**软删**一份导入的演示模板（移进 `content/ppt_templates/_trash/`，老师能找回来）。

    为什么补这个入口：老师导错一份 .pptx 就再也去不掉了（R8 清理体检残留时只能直接调库函数），
    内置的三套数值模板**不允许删**。
    边界：只动 `content/ppt_templates/` 下这一个模板目录，原稿在 `_trash` 里保留。
    """
    from edu_agent import slide_template

    tid = str((body or {}).get("template_id") or "").strip()
    if not tid:
        return JSONResponse({"ok": False, "error": "没给模板 id"}, status_code=400)
    try:
        out = slide_template.delete_template(tid)
    except Exception as exc:  # noqa: BLE001 删失败要说清楚，别让按钮点了没反应
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return out


@app.post("/api/slide/template/import")
async def api_slide_template_import(file: UploadFile = File(...), name: str = Query("")):
    """导入一份 `.pptx` 当演示模板（原稿收进模板库、**只读**；抽不出槽位就退化成只借配色）。"""
    from edu_agent import slide_template

    fname = (file.filename or "template.pptx").lower()
    if not fname.endswith((".pptx", ".pptm", ".potx")):
        return JSONResponse({"ok": False, "error": "只支持 .pptx（含 .pptm/.potx）"}, status_code=400)
    tmp = Path(tempfile.mkdtemp(prefix="tplup_")) / (file.filename or "template.pptx")
    try:
        tmp.write_bytes(await file.read())
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"读上传文件失败：{exc}"}
    out = slide_template.import_pptx(tmp, name=name or Path(file.filename or "").stem)
    shutil.rmtree(tmp.parent, ignore_errors=True)
    return out


@app.post("/api/slide/fill")
def api_slide_fill(body: SlideFillReq):
    """把会话内容填进模板（产物在 `data/ppt_out/`；模板原稿只读）。"""
    from edu_agent import permissions
    from edu_agent import slide_fill

    blocked = permissions.guard("export_pptx", bool(body.confirm))
    if blocked:
        return blocked
    tid = str(body.template_id or "").strip()
    if not tid:
        return JSONResponse({"ok": False, "error": "先选一个导入的演示模板"}, status_code=400)
    slides = [s for s in (body.slides or []) if isinstance(s, dict)]
    if not slides:
        sid = str(body.session_id or "").strip() or (_store.current_id() or "")
        slides = (_store.get_session_doc(sid).get("slides") or [])
    if not slides:
        return JSONResponse({"ok": False, "error": "没有可填的内容（先有教案正文或幻灯片）"}, status_code=400)
    out = slide_fill.fill_pptx(tid, slides, title=str(body.title or ""),
                              out_dir=(body.outDir or "").strip() or None)
    return out


@app.post("/api/slide/render")
def api_slide_render(body: SlideRenderReq):
    """会话数据 → 每页 SVG。**不落盘、不联网、不依赖 WPS**。"""
    if not capabilities.session_preview_ready():
        return {"ok": True, "enabled": False, "pages": [], "warnings": [], "layouts": [],
                "theme_id": body.theme_id or "", "stats": {"pages": 0}}

    slides = [s for s in (body.slides or []) if isinstance(s, dict)]
    title = str(body.title or "").strip()
    if not slides:
        sid = str(body.session_id or "").strip() or (_store.current_id() or "")
        saved = _store.get_session_doc(sid)            # 老师在面板里改过 → 以改过的为准
        md_now = str(body.markdown or "")
        saved_sig, cur_sig = saved.get("md_sig") or "", (_md_sig(md_now) if md_now.strip() else "")
        if saved.get("slides") and not (saved_sig and cur_sig and saved_sig != cur_sig):
            slides = saved["slides"]
            title = title or str(saved.get("title") or "")
    if not slides:
        md = _strip_agent_tag(str(body.markdown or ""))
        if not md.strip():
            md = _last_plan_markdown(str(body.session_id or "").strip() or (_store.current_id() or ""))
        if md.strip():
            try:
                data = wps_export.slides_from_markdown(md)
                slides = data.get("slides") or []
                title = title or str(data.get("title") or "")
            except Exception:  # noqa: BLE001 纯预览，解析失败就当没有页
                slides = []
    try:
        tokens = None
        if str(body.template_id or "").strip():        # 导入模板：借它的配色/字体渲染
            from edu_agent import slide_template

            tokens = slide_template.theme_for(body.template_id)
        out = slide_deck.build_deck(body.theme_id, slides, title=title, tokens=tokens,
                                    annotate=bool(body.annotate))
    except (FileNotFoundError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    out["enabled"] = True
    out["note"] = "内存渲染：这份 SVG 就是导出要转换的那一份（同一份源）"
    return out


@app.post("/api/slide/slot")
def api_slide_slot(body: SlideSlotReq):
    """**页内编辑一格**：`{index, slot, text}` → 落进会话里那份 slides，返回收干净的整册。

    为什么不让前端自己改字段：槽位→字段的映射只在 `slide_deck.apply_slot` 里维护一份
    （两边各写一套迟早漂；改字段名时 Python 单测会红，前端只认 slot 字符串）。
    这里**不落盘**：写会话库与 `/api/slide/save` 是同一条路。
    """
    from edu_agent import slide_deck as _deck

    slides = [s for s in (body.slides or []) if isinstance(s, dict)]
    sid = str(body.session_id or "").strip() or (_store.current_id() or "")
    if not slides:
        slides = [s for s in (_store.get_session_doc(sid).get("slides") or []) if isinstance(s, dict)]
    if not slides:
        return JSONResponse({"ok": False, "error": "没有可改的幻灯片（先有教案内容）"}, status_code=400)
    idx = int(body.index if isinstance(body.index, int) else -1)
    if not (0 <= idx < len(slides)):
        return JSONResponse({"ok": False, "error": f"页号越界：第 {idx + 1} 页（共 {len(slides)} 页）"},
                            status_code=400)
    if body.text is None and not body.patch:
        return JSONResponse({"ok": False, "error": "要给我 text（改字）或 patch（改样子）"}, status_code=400)
    res: dict = {"ok": True, "slot": str(body.slot or "")}
    if body.text is not None:                  # ⚠️ 没给 text ≠ 给空 text：空 text 是"清空/删除这一格"
        res = _deck.apply_slot(slides[idx], str(body.slot or ""), str(body.text))
        if not res.get("ok"):
            return JSONResponse(res, status_code=400)
    if body.patch:
        # R14：拖动/改大小/层级/删除走 `slots` 覆盖（与改文字分开落，互不干扰）
        pres = _deck.apply_override(slides[idx], str(body.slot or ""), dict(body.patch))
        if not pres.get("ok"):
            return JSONResponse(pres, status_code=400)
        res["slots"] = pres.get("slots") or {}
    cleaned = _deck.clean_slides(slides)
    # ⚠️ 读-改-写：`set_session_doc` 是**整份替换**，直接塞 {"slides":…} 会把 title / markdown /
    #    md_sig / slides_edited 一起抹掉（"正文改过没"的判断就废了）。
    cur = dict(_store.get_session_doc(sid) or {})
    cur["slides"] = cleaned
    cur["slides_edited"] = True
    title = str(body.title or "").strip()
    if title:
        cur["title"] = title
    _store.set_session_doc(sid, cur)
    warnings = _deck.length_warnings(cleaned)
    return {"ok": True, "slot": res.get("slot"), "index": idx, "slides": cleaned,
            "pages": len(cleaned), "warnings": warnings, "session_id": sid}


@app.get("/api/slide/design/catalog")
def api_slide_design_catalog(full: bool = False):
    """R27a：**设计规格目录** —— 告诉 agent（与面板）"有哪些可调的东西"。

    内容：视觉风格 18（纪律 + 我们给的默认数值）· 沟通模式 5 · 品牌 21（色板）· 版式 7 族 86 个 ·
    可覆盖的数值键与硬边界 · **锁死清单**（画布/安全边距/元素白名单/预览=导出/审计 error 0/落盘红线）。

    `?full=true` 才带上规则全文与版式槽位几何（默认只给 id 与一句话，别让目录接口吐 200KB 出去）。
    注册表是**自包含**的（`content/ppt_templates/_design/*.json`，由 `tools/compile_design_registry.py`
    从 `.deps` 编译而来）—— 两份 skill 不在场也照常可用（`docs/29` §1.2 红线）。
    """
    from edu_agent import design_spec as _ds

    try:
        return _ds.catalog(full=bool(full))
    except FileNotFoundError as exc:                     # 注册表没编译过：说清怎么修，不 500
        return JSONResponse({"ok": False, "error": f"设计规格注册表未就绪：{exc}",
                             "hint": "跑 .venv\\Scripts\\python.exe tools\\compile_design_registry.py"},
                            status_code=200)


@app.get("/api/slide/image/providers")
def api_slide_image_providers():
    """联网取图的状态：先把"现在能不能搜图、缺什么"说清楚（不假装能搜）。"""
    from edu_agent import capabilities as _caps
    from edu_agent import slide_image as _si

    enabled = _caps.is_enabled("skill", "web-image")
    st = _si.provider_status()
    st.update({"enabled": enabled, "uploads": str(_si.uploads_root())})
    if not enabled:
        st["hint"] = "「联网取图」这个 skill 现在是关的 —— 到「Skill 市场 → 素材」打开它"
    return st


@app.post("/api/slide/image/attach")
def api_slide_image_attach(body: SlideImageReq):
    """**联网取图**：把 `url` 取回 `data/uploads/` 并挂到第 `index` 页（预览与导出都带图）。

    为什么要过权限闸门：这是**落盘 + 出网**的动作（老师要求"联网功能"要能看见边界）。
    红线：只写 `data/uploads/`（`content/plans/` 一个字不写）；只认公网 http/https（拒内网）；
    单张 ≤10MB；类型白名单。搜索类图源缺 key 时**如实报**，并提示"让同事的 agent 直接给 URL"。
    """
    from edu_agent import capabilities as _caps
    from edu_agent import slide_image as _si

    if not _caps.is_enabled("skill", "web-image"):
        return JSONResponse({"ok": False, "gate": "skill", "skill": "web-image",
                             "error": "「联网取图」这个 skill 是关的（到 Skill 市场打开它）"},
                            status_code=200)
    sid = str(body.session_id or "").strip() or (_store.current_id() or "")
    slides = [s for s in (body.slides or []) if isinstance(s, dict)]
    if not slides:
        slides = [s for s in (_store.get_session_doc(sid).get("slides") or []) if isinstance(s, dict)]
    if not slides:
        return JSONResponse({"ok": False, "error": "没有可挂图的幻灯片（先有教案内容）"}, status_code=400)
    idx = int(body.index if isinstance(body.index, int) else -1)
    if not (0 <= idx < len(slides)):
        return JSONResponse({"ok": False, "error": f"页号越界：第 {idx + 1} 页（共 {len(slides)} 页）"},
                            status_code=400)
    if str(body.action or "attach").lower() == "detach":
        slides[idx].pop("image", None)
        cleaned = slide_deck.clean_slides(slides)
        cur = dict(_store.get_session_doc(sid) or {})
        cur["slides"] = cleaned
        cur["slides_edited"] = True
        _store.set_session_doc(sid, cur)
        return {"ok": True, "action": "detach", "index": idx, "slides": cleaned, "session_id": sid}

    # 先把"这个地址本身行不行"说清楚（协议/主机），再谈权限与下载 —— 顺序反了会让空地址也走成权限提示
    check = _si.check_url(str(body.url or ""))
    if not check.get("ok"):
        return JSONResponse({"ok": False, "error": check.get("error")}, status_code=400)
    if not body.confirm:
        return JSONResponse({"ok": False, "gate": "permission", "action": "web_image_fetch",
                             "need": "confirm",
                             "hint": f"这一步会：联网把这张图取回来存到 data/uploads/ 并挂到第 {idx + 1} 页。"},
                            status_code=200)
    got = _si.fetch_url(str(body.url or ""), sid)
    if not got.get("ok"):
        return {"ok": False, "error": got.get("error") or "取图失败", "providers": _si.provider_status()}
    pic = {"src": got["path"]}
    if str(body.alt or "").strip():
        pic["alt"] = str(body.alt).strip()[:80]
    credit = str(body.credit or "").strip() or (("来源：" + str(body.url).split("/")[2]) if body.url else "")
    if credit:
        pic["credit"] = credit[:40]
    slides[idx]["image"] = pic
    cleaned = slide_deck.clean_slides(slides)
    cur = dict(_store.get_session_doc(sid) or {})
    cur["slides"] = cleaned
    cur["slides_edited"] = True
    if str(body.title or "").strip():
        cur["title"] = str(body.title).strip()
    _store.set_session_doc(sid, cur)
    return {"ok": True, "action": "attach", "index": idx, "image": pic, "slides": cleaned,
            "bytes": got.get("bytes"), "sha256": got.get("sha256"), "file": got.get("file"),
            "session_id": sid,
            "detail": f"已把这张图挂到第 {idx + 1} 页（{got.get('bytes', 0) // 1024} KB，存在 data/uploads/）"}


@app.post("/api/slide/image/upload")
async def api_slide_image_upload(file: UploadFile = File(...), session_id: str = Form(""),
                                 treat: str = Form("1")):
    """**本地上传一张图**（R22）：落到 `data/uploads/<会话>/`，太大就先缩一版（`image_treat --fit`）。

    与联网取图同一套红线：只写 uploads（`content/plans` 一个字不写）、类型白名单（按文件头兜底）、
    体积上限 20MB。返回的是**相对 uploads 的路径** —— 前端拿它去 `POST /api/slide/object`
    加一个 `kind:"image"` 的对象（这样图能像形状一样拖/缩放，而不是只能放"图文"版式的固定槽）。
    """
    from edu_agent import slide_image as _si

    data = await file.read()
    sid = str(session_id or "").strip() or (_store.current_id() or "")
    got = _si.save_upload(data, file.filename or "upload.png", sid)
    if not got.get("ok"):
        return JSONResponse({"ok": False, "error": got.get("error") or "上传失败"}, status_code=400)
    out = dict(got)
    out.pop("file", None)
    if str(treat or "1") not in ("0", "false", ""):
        opt = _si.optimize(got["path"])
        if opt.get("ok") and opt.get("path") != got["path"]:
            out.update({"path": opt["path"], "from": got["path"], "treated": opt.get("treated") or "",
                        "width": (opt.get("info") or {}).get("width", out.get("width")),
                        "height": (opt.get("info") or {}).get("height", out.get("height"))})
        if opt.get("note"):
            out["note"] = opt["note"]
    out.update({"session_id": sid, "detail": "图已收到（存在 data/uploads/）" + ("；" + out["note"] if out.get("note") else "")})
    return out


class SlideObjectReq(BaseModel):
    """自由层对象（R21）：插入 / 改 / 删 / 上下移一个**老师自己造的**文本框或形状。

    `action`：`add`（要 `kind`/`shape`）、`patch`（要 `id` + `patch`）、`delete`（要 `id`）、
    `up`/`down`（换数组位置 = 上移/下移一层）、`set`（整份对象数组，撤销/重做走这条）。
    几何是**绝对设计像素**（`x/y/w/h`），不是槽位那套增量；`theme_id` 只影响新对象的默认配色。
    """

    session_id: str = ""
    index: int = 0
    action: str = "add"
    id: str = ""
    kind: str = "shape"
    shape: str = "rect"
    patch: dict | None = None
    objects: list[dict] | None = None
    slides: list[dict] | None = None
    title: str = ""
    theme_id: str = ""
    src: str = ""                    # kind="image" 用：uploads 下的相对路径


@app.post("/api/slide/object")
def api_slide_object(body: SlideObjectReq):
    """**自由层**：插入 / 改 / 删 / 上下移一个文本框或形状（改完立刻整册存回会话）。

    为什么单独一个接口而不是塞进 `/api/slide/slot`：槽位是**版式给的**（`apply_slot` 只认那套名字），
    自由层对象是**老师自己造的**、几何是绝对值、还能增删 —— 混在一个接口里两边都会变糊。
    """
    slides = [dict(s) for s in (body.slides or []) if isinstance(s, dict)]
    sid = str(body.session_id or "").strip() or (_store.current_id() or "")
    if not slides:
        slides = [dict(s) for s in (_store.get_session_doc(sid).get("slides") or [])
                  if isinstance(s, dict)]
    if not slides:
        return JSONResponse({"ok": False, "error": "没有可编辑的幻灯片（先有教案内容）"}, status_code=400)
    idx = int(body.index or 0)
    if not (0 <= idx < len(slides)):
        return JSONResponse({"ok": False, "error": f"页号越界：第 {idx + 1} 页（共 {len(slides)} 页）"},
                            status_code=400)
    page = slides[idx]
    objs = slide_deck.clean_objects(page.get("objects"))
    act = str(body.action or "add").lower()
    detail, focus = "", ""
    if act == "add":
        oid = slide_deck.next_object_id(objs)
        focus = oid
        pic = None
        if str(body.kind) == "image":
            from edu_agent import slide_image as _si
            info = _si.probe(_si.uploads_root() / str(body.src or "x"))
            if info.get("width") and info.get("height"):
                pic = (float(info["width"]), float(info["height"]))
        objs = objs + [slide_deck.new_object(body.kind, body.shape, canvas=(1280, 720),
                                             theme=slide_deck.theme(body.theme_id), oid=oid,
                                             src=str(body.src or ""), pic=pic)]
        if len(objs) > slide_deck.MAX_OBJECTS:
            return {"ok": False, "error": f"一页最多 {slide_deck.MAX_OBJECTS} 个对象（先删几个）"}
        detail = ("加了一个" + {"text": "文本框", "image": "图片"}.get(str(body.kind), "形状"))
    elif act == "patch":
        got = slide_deck.patch_object(objs, body.id, body.patch)
        if not got.get("ok"):
            return {"ok": False, "error": got.get("error") or "改不动这个对象"}
        objs, focus = got["objects"], body.id
    elif act == "delete":
        keep = [o for o in objs if str(o.get("id")) != str(body.id)]
        if len(keep) == len(objs):
            return {"ok": False, "error": f"这一页没有对象 {body.id}"}
        objs = keep
        detail = "删掉了这个对象"
    elif act in ("up", "down"):
        ids = [str(o.get("id")) for o in objs]
        if body.id not in ids:
            return {"ok": False, "error": f"这一页没有对象 {body.id}"}
        i = ids.index(body.id)
        j = i + (1 if act == "up" else -1)
        if not (0 <= j < len(objs)):
            return {"ok": False, "error": "已经在最上面了" if act == "up" else "已经在最下面了"}
        objs[i], objs[j] = objs[j], objs[i]
        focus = body.id
        detail = "上移一层" if act == "up" else "下移一层"
    elif act == "set":
        objs = slide_deck.clean_objects(body.objects)
        detail = "对象已更新"
    else:
        return JSONResponse({"ok": False, "error": f"不认识的动作：{act}"}, status_code=400)

    if objs:
        page["objects"] = objs
    else:
        page.pop("objects", None)
    slides[idx] = page
    cleaned = slide_deck.clean_slides(slides)
    cur = dict(_store.get_session_doc(sid) or {})
    cur["slides"] = cleaned
    cur["slides_edited"] = True
    if str(body.title or "").strip():
        cur["title"] = str(body.title).strip()
    _store.set_session_doc(sid, cur)
    return {"ok": True, "action": act, "index": idx, "id": focus,
            "objects": (cleaned[idx].get("objects") or []), "slides": cleaned,
            "count": len(cleaned[idx].get("objects") or []), "session_id": sid, "detail": detail}


def _plan_blocks(md: str) -> list[dict]:
    """从教案 Markdown 里抽出板块清单（纯文本处理，不落盘）。"""
    out: list[dict] = []
    for raw in (md or "").splitlines():
        st = raw.strip()
        m = re.match(r"^\*\*(.+?)\*\*\s*$", st)
        if m:
            out.append({"title": m.group(1).strip(), "level": 2, "chars": 0})
            continue
        m = re.match(r"^(#{1,4})\s+(.+?)\s*$", st)
        if m:
            out.append({"title": m.group(2).strip(), "level": len(m.group(1)), "chars": 0})
            continue
        if out and st and not st.startswith("|"):
            out[-1]["chars"] += len(st)
    return out


def _clean_doc_meta(msg: dict) -> dict:
    """历史消息里已落库的 `meta.doc` 兜底清洗。

    为什么需要：`meta.doc` 是**当时**那份代码算出来的、直接存进了会话库。修复前生成的
    payload 会把正文开头的「**【Agent B · 教案 Agent】**」当成正文（标题因此退化成
    「未命名文档」），前端做历史重载时是原样复用的——不清洗，老会话永远带着这个瑕疵。
    只在真的带标签时才重算（不带标签时原对象返回，零成本）。
    """
    if not isinstance(msg, dict):
        return msg
    meta = msg.get("meta")
    doc = meta.get("doc") if isinstance(meta, dict) else None
    if not isinstance(doc, dict):
        return msg
    md = str(doc.get("markdown") or "")
    if _strip_agent_tag(md) == md:
        return msg
    title = str(doc.get("title") or "").strip()
    if title == "未命名文档":
        title = ""
    cleaned = dict(msg)
    cleaned["meta"] = dict(meta)
    cleaned["meta"]["doc"] = _doc_payload(_strip_agent_tag(md), title=title,
                                          grade=str(doc.get("grade") or ""))
    return cleaned


@app.post("/api/session/get")
def api_sess_get(body: dict):
    sess = _store.get(body.get("id", ""))
    if sess is None:
        return {"id": body.get("id", ""), "title": "新会话", "messages": [], "last_html": ""}
    return {"id": sess["id"], "title": sess["title"],
            "messages": [_clean_doc_meta(m) for m in sess["messages"]],
            "last_html": sess["last_html"]}


@app.post("/api/session/del")
def api_sess_del(body: dict):
    sid = body.get("id", "")
    if _store.get(sid) is not None:
        _store.delete(sid)
    return {"ok": True, "list": _store.list_sessions(), "current": _store.current_id()}


@app.get("/api/textbook")
def api_textbook():
    return {"file": _TXT_NAME, "offset": _TXT_OFF, "exists": _TXT_DIR is not None}


@app.get("/api/page_image")
def api_page_image(page: int = Query(..., ge=1, le=999), source: str = "", dpi: int = 150):
    """教材/上传 PDF 的某一页渲染成 PNG（引用卡「看原页」，2026-09-10 P0）。

    page   教材 = 引用里的**印刷页码**（内部 +_TXT_OFF 换算物理页）；
           上传件 = **物理页**（pdf_upload 入库记的就是物理页，offset=0）。
    source 上传资料标识（source 或文件名）；空 = 教材原文。
    只读：渲染产物写 data/page_cache/，不动 PDF 与向量库。
    """
    try:
        data, cache_file, phys = _render_page_png(page, source, dpi)
    except FileNotFoundError as e:
        return JSONResponse({"ok": False, "error": f"PDF 不可用：{e}"}, status_code=404)
    except IndexError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=404)
    except Exception as e:  # noqa: BLE001 —— 渲染失败不该把网关带崩
        log.warning("page_image failed page=%s: %s", page, e)
        return JSONResponse({"ok": False, "error": f"{type(e).__name__}: {e}"}, status_code=500)
    return Response(
        content=data,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=86400",
                 "X-Physical-Page": str(phys), "X-Cache-File": cache_file.name},
    )


def _render_page_png(page: int, source: str = "", dpi: int = 150):
    """解析受控 PDF 来源并渲染页面；原页展示与视觉分析共用同一安全入口。"""
    from edu_agent import page_image

    dpi = max(72, min(int(dpi or 150), 300))
    if source:
        hit = next((s for s in pdf_upload.list_sources()
                    if source in (str(s.get("source") or ""), str(s.get("file") or ""))), None)
        if hit is None:
            raise FileNotFoundError("上传资料不存在")
        # 原始展示名与真实落盘名分离；只经 stored_path 取受控 basename，
        # 这样“官方教材”直达原页和普通上传资料都能正确渲染。
        pdf = pdf_upload.stored_path(hit.get("stored_file") or hit.get("file") or "")
        off, prefix = 0, "up"
    else:
        s = load_settings()
        pdf = s.textbook_pdf if (s.textbook_pdf and s.textbook_pdf.exists()) else None
        off, prefix = _TXT_OFF, "tb"
    return page_image.get_page_image(pdf, page, offset=off, dpi=dpi, prefix=prefix)


class VisionPageReq(BaseModel):
    page: int = Field(ge=1, le=999)
    source: str = ""
    question: str = ""
    session_id: str = ""
    dpi: int = Field(default=200, ge=72, le=300)
    confirm: bool = False


def _vision_session(session_id: str, user_text: str, markdown: str, meta: dict) -> str:
    sid = str(session_id or "").strip()
    if not sid or _store.get(sid) is None:
        sid = _store.new()
    _store.append(sid, "user", user_text, meta={"mode": "V", **meta})
    _store.append(sid, "assistant", _tagged("A", markdown), meta={"mode": "V", **meta})
    _store.set_title_if_new(sid, user_text.splitlines()[0][:18])
    return sid


@app.get("/api/vision/status")
def api_vision_status():
    from edu_agent import vision

    ready, detail = vision.available()
    settings = load_settings()
    return {"ok": True, "ready": ready, "detail": detail,
            "model": settings.vision_model,
            "formats": ["JPEG", "PNG", "GIF", "WebP"],
            "max_bytes": settings.vision_max_bytes}


@app.post("/api/vision/analyze")
async def api_vision_analyze(file: UploadFile = File(...), question: str = Form(""),
                             session_id: str = Form(""), confirm: bool = Form(False)):
    """分析用户图片；原文件不落盘，发送远程模型前经过权限闸门。"""
    from edu_agent import permissions, vision

    blocked = permissions.guard("vision_analyze", confirm)
    if blocked:
        return blocked
    settings = load_settings()
    try:
        data = await _read_upload_limited(file, min(settings.upload_max_bytes,
                                                    settings.vision_max_bytes))
        result = await run_in_threadpool(
            vision.analyze_image, data, file.filename or "image", question,
        )
    except (ValueError, vision.VisionError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:  # noqa: BLE001
        log.exception("vision analyze failed")
        return JSONResponse({"ok": False, "error": f"视觉分析失败：{type(exc).__name__}"},
                            status_code=500)
    markdown = vision.to_markdown(result, file.filename or "image")
    user_text = "分析图片《" + (file.filename or "image") + "》"
    if question.strip():
        user_text += "：" + question.strip()
    sid = _vision_session(session_id, user_text, markdown, {
        "vision": {"filename": file.filename or "image",
                   "confidence": result.get("confidence"),
                   "image_type": result.get("image_type")},
    })
    return {"ok": True, "session_id": sid, "markdown": markdown, "result": result}


@app.post("/api/vision/page")
async def api_vision_page(body: VisionPageReq):
    """直接分析教材或已上传 PDF 的受控页面，不接收任意服务器路径。"""
    from edu_agent import permissions, vision

    blocked = permissions.guard("vision_analyze", body.confirm)
    if blocked:
        return blocked
    try:
        data, _cache_file, physical_page = await run_in_threadpool(
            _render_page_png, body.page, body.source, body.dpi,
        )
        result = await run_in_threadpool(
            vision.analyze_image, data, f"page-{body.page}.png", body.question,
        )
    except (FileNotFoundError, IndexError, ValueError, vision.VisionError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:  # noqa: BLE001
        log.exception("vision page analyze failed")
        return JSONResponse({"ok": False, "error": f"原页分析失败：{type(exc).__name__}"},
                            status_code=500)
    label = ("上传资料" if body.source else "教材") + f"第 {body.page} 页"
    markdown = vision.to_markdown(result, label)
    user_text = "分析" + label
    if body.question.strip():
        user_text += "：" + body.question.strip()
    sid = _vision_session(body.session_id, user_text, markdown, {
        "vision": {"source": body.source, "page": body.page,
                   "physical_page": physical_page,
                   "confidence": result.get("confidence"),
                   "image_type": result.get("image_type")},
    })
    return {"ok": True, "session_id": sid, "markdown": markdown,
            "page": body.page, "physical_page": physical_page, "result": result}


_AUTO_DIR = ROOT / "content" / "structured_auto"
_TOC_FILE = ROOT / "content" / "toc_ranges.json"        # 现行位置（随仓库走）
_TOC_FILE_LEGACY = ROOT / "data" / "toc_ranges.json"    # 旧位置（data/ 不入库，仅本地兼容）


@app.get("/api/textbook/toc")
def api_textbook_toc():
    """教材目录页码范围（由 scripts/build_toc_ranges.py 生成）。

    给「看原页」浏览器用：知道某个知识点在哪一节、这一章从第几页到第几页、全书多少页。
    数据源 = 向量库片段的 chapter/section/page 聚合 + PDF 章首页探测。
    """
    if not _TOC_FILE.exists() and _TOC_FILE_LEGACY.exists():
        return json.loads(_TOC_FILE_LEGACY.read_text(encoding="utf-8"))
    if not _TOC_FILE.exists():
        return JSONResponse({"ok": False, "error": "toc 未生成：请跑 scripts/build_toc_ranges.py"}, status_code=404)
    try:
        return json.loads(_TOC_FILE.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"toc 解析失败：{e}"}, status_code=500)


@app.get("/api/textbook/chapters")
def api_textbook_chapters():
    """解析整节教材 md -> 章/节结构（教材库章节浏览）。"""
    out = []
    if not _AUTO_DIR.exists():
        return {"ok": True, "chapters": out}
    for f in sorted(_AUTO_DIR.glob("*.md")):
        text = f.read_text(encoding="utf-8", errors="ignore")
        ch = re.search(r"^chapter:\s*(.+)$", text, re.M)
        ct = re.search(r"^chapter_title:\s*(.+)$", text, re.M)
        bk = re.search(r"^book:\s*(.+)$", text, re.M)
        sections = []
        for line in text.splitlines():
            m = re.match(r"^【整节】([^|\r\n]*)\|([^|]*)\|([^|]*)\|p?([0-9]+)?", line)
            if m:
                sections.append({"id": m.group(1), "no": m.group(2),
                                 "name": m.group(3), "page": int(m.group(4)) if m.group(4) else None})
        out.append({"file": f.name,
                    "chapter": ch.group(1).strip() if ch else "",
                    "title": ct.group(1).strip() if ct else "",
                    "book": bk.group(1).strip() if bk else "",
                    "sections": sections})
    return {"ok": True, "chapters": out}


def _main_textbook_stats() -> dict:
    """内置官方教材统计；多本上传教材则由 /api/knowledge-bases 统一列出。"""
    blocks = None
    pages = None
    try:
        from langchain_chroma import Chroma
        from edu_agent.config import get_embeddings
        db = Chroma(collection_name="edu_textbook",
                    embedding_function=get_embeddings(),
                    persist_directory=str(ROOT / "chroma_db"))
        blocks = db._collection.count()
    except Exception:
        blocks = None
    n_ch = 0
    if _AUTO_DIR.exists():
        n_ch = len(list(_AUTO_DIR.glob("*.md")))
    if _txt and _txt.exists():
        try:
            import pymupdf
            doc = pymupdf.open(str(_txt))
            pages = max(0, doc.page_count - _TXT_OFF)
            doc.close()
        except Exception:
            pages = None
    return {"book": "数学 · 必修第一册（人教A版2019课标版）",
            "chapters": n_ch, "pages": pages, "blocks": blocks,
            "source_pdf": str(ROOT / "content" / "raw") if (ROOT / "content" / "raw").exists() else None}


@app.get("/api/textbook/stats")
def api_textbook_stats():
    """兼容旧前端的内置教材统计。"""
    return {"ok": True, **_main_textbook_stats()}


def _source_library(item: dict) -> str:
    """旧上传件没有 library 字段时，安全地归入“个人资料”。"""
    try:
        return pdf_upload.normalize_library(item.get("library"))
    except ValueError:
        return "personal"


@app.get("/api/knowledge-bases")
def api_knowledge_bases():
    """知识库目录：内置教材 + 被标记为官方教材的 PDF 都可直达。"""
    primary = _main_textbook_stats()
    sources = [{**item, "library": _source_library(item)} for item in pdf_upload.list_sources()]
    official = [{
        "id": "textbook", "corpus": "textbook", "title": primary["book"],
        "kind": "built_in", "readonly": True, "chapters": primary["chapters"],
        "pages": primary["pages"], "blocks": primary["blocks"],
    }]
    try:
        from edu_agent.rj_textbooks import catalog as rj_textbook_catalog, stats as rj_textbook_stats

        rj_blocks = rj_textbook_stats().get("collection_count", 0)
        if rj_blocks:
            official.append({
                "id": "rj_textbooks", "corpus": "rj_textbooks", "title": "人教版高中全科教材",
                "kind": "built_in", "readonly": True, "chapters": None,
                "pages": None, "blocks": rj_blocks,
            })
            for book in rj_textbook_catalog():
                official.append({
                    "id": "rj_textbooks:" + book["source"],
                    "corpus": "rj_textbooks:" + book["source"],
                    "title": book["title"], "kind": "built_in", "readonly": True,
                    "subject": book["subject"], "edition": book["edition"],
                    "source": book["source"], "pages": None, "chapters": None,
                    "blocks": book["blocks"], "pdf_available": book["pdf_available"],
                })
    except Exception:
        pass
    for item in sources:
        if item["library"] != "official":
            continue
        official.append({
            "id": "pdf:" + str(item.get("source") or ""), "corpus": "pdf:" + str(item.get("source") or ""),
            "title": str(item.get("file") or item.get("source") or "官方教材"), "kind": "uploaded",
            "readonly": False, "source": str(item.get("source") or ""),
            "pages": item.get("pages"), "blocks": item.get("chunks"), "chapters": None,
        })
    personal = [item for item in sources if item["library"] == "personal"]
    return {"ok": True, "official_books": official, "personal_documents": personal}


@app.get("/api/official-books/{source}/pdf")
def api_official_book_pdf(source: str):
    """在浏览器中打开一册公共教材的本地原始 PDF，不接受任意文件路径。"""
    try:
        from edu_agent.rj_textbooks import official_pdf_path

        pdf = official_pdf_path(source)
        if pdf is None:
            return JSONResponse(status_code=404, content={"ok": False, "error": "该教材未找到可打开的 PDF"})
        return FileResponse(str(pdf), media_type="application/pdf", filename=pdf.name)
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": f"打开教材失败：{type(exc).__name__}"})


@app.get("/api/official-books/{source}/chapters")
def api_official_book_chapters(source: str):
    """公共教材的章/节导航及其对应 PDF 物理页。"""
    try:
        from edu_agent.rj_textbooks import chapters, official_pdf_path

        if official_pdf_path(source) is None:
            return JSONResponse(status_code=404, content={"ok": False, "error": "该教材未找到可打开的 PDF"})
        return {"ok": True, "chapters": chapters(source)}
    except Exception as exc:
        return JSONResponse(status_code=500, content={"ok": False, "error": f"读取教材目录失败：{type(exc).__name__}"})


@app.get("/api/knowledge-health")
def api_knowledge_health():
    """只读检查索引版本、源文件、embedding 配置和 PDF 目录一致性。"""
    try:
        from edu_agent.knowledge_health import audit

        return audit()
    except Exception as e:  # noqa: BLE001 健康接口本身也要给出可诊断结果
        return {"ok": False, "status": "error", "issues": [{
            "level": "error", "code": "audit_failed",
            "message": f"{type(e).__name__}: {e}",
        }]}


@app.get("/api/pdfs")
def api_pdfs():
    return {"sources": [{**item, "library": _source_library(item)}
                        for item in pdf_upload.list_sources()]}


class PdfLibraryReq(BaseModel):
    source: str
    library: str
    confirm: bool = False


@app.post("/api/pdf/library")
def api_pdf_library(body: PdfLibraryReq):
    """切换已上传 PDF 的知识库归属；索引不动，因此立即生效。"""
    from edu_agent import permissions

    blocked = permissions.guard("pdf_library_manage", body.confirm)
    if blocked:
        return blocked
    try:
        return pdf_upload.set_library(body.source.strip(), body.library)
    except ValueError as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/pdf/del")
def api_pdf_del(body: dict):
    """删除上传的 PDF 资料（原件 + meta + 向量）。"""
    source = (body.get("source") or "").strip()
    if not source:
        return {"ok": False, "error": "缺少 source"}
    meta = pdf_upload.list_sources()
    hit = next((s for s in meta if s.get("source") == source), None)
    keep = [s for s in meta if s.get("source") != source]
    if hit is None:
        return {"ok": False, "error": "资料不存在"}
    pdf_upload._save_meta(keep)
    try:
        # 新版元数据存服务端生成的 stored_file；旧版才回退 file，仍经过 basename 校验。
        f = pdf_upload.stored_path(hit.get("stored_file") or hit.get("file") or "")
        if f.exists():
            f.unlink()
    except Exception:
        pass
    try:
        from langchain_chroma import Chroma
        from edu_agent.config import get_embeddings
        db = Chroma(collection_name=pdf_upload.COLLECTION,
                    embedding_function=get_embeddings(),
                    persist_directory=str(pdf_upload.VDB_DIR))
        db.delete(where={"source": source})
    except Exception:
        pass
    return {"ok": True}


@app.post("/api/pdf/ingest")
async def api_pdf_ingest(file: UploadFile = File(...), confirm: bool = False,
                         library: str = "personal"):
    from edu_agent import permissions

    blocked = permissions.guard("pdf_ingest", confirm)     # 写本地向量库 = 有副作用，先过权限闸门
    if blocked:
        return blocked
    if not (file.filename or "").lower().endswith(".pdf"):
        return {"ok": False, "error": "仅支持 .pdf"}
    try:
        library = pdf_upload.normalize_library(library)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    try:
        data = await _read_upload_limited(file, _settings.upload_max_bytes)
    except ValueError as e:
        return {"ok": False, "error": str(e)}
    try:
        info = pdf_upload.ingest_pdf(data, file.filename or "doc.pdf",
                                     max_pages=_settings.upload_max_pdf_pages,
                                     library=library)
        return {"ok": True, **info}
    except Exception as e:
        return {"ok": False, "error": type(e).__name__ + ": " + str(e)}


# ---------- 能力（MCP 服务 / Skill）：开关 + 运行状态 ----------
@app.get("/api/capabilities")
def api_capabilities(deep: int = 0):
    """能力清单：enabled=开关（默认全开），running/detail=运行状态，tools=工具数（deep=1）。"""
    return capabilities.snapshot(deep=bool(deep))


@app.post("/api/capabilities/toggle")
def api_capabilities_toggle(body: dict):
    """开 / 关一个 MCP 服务或 Skill（落 data/capabilities.json 持久化）。"""
    return capabilities.set_enabled(str(body.get("kind") or ""),
                                    str(body.get("id") or ""),
                                    bool(body.get("enabled")))


# ---------- Skill 调用：本机 Ollama 问答（skills/ollama-qa） ----------
@app.post("/api/skill/ask")
def api_skill_ask(body: dict):
    """Skill「本机 Ollama 问答」：{"question": "..."} -> {"ok", "answer", "model", "ms"}。

    平台只做转发，执行完整封在技能自己的脚本里（capabilities.ask_ollama_qa -> skills/ollama-qa）。
    技能被关闭 -> {ok:false, gate:'capability'}；问题为空 -> {ok:false, error:'问题不能为空'}。
    本函数是同步 def，FastAPI 会放到线程池里跑，所以慢推理不会卡住事件循环。
    """
    try:
        return capabilities.ask_ollama_qa(str(body.get("question") or ""))
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": type(e).__name__ + ": " + str(e)}


@app.get("/api/mcp/status")
def api_mcp_status():
    """右栏「运行状态」面板用的轻量快照（不做工具握手）。"""
    snap = capabilities.snapshot(deep=False)
    return {"mcp": snap["mcp"], "skills": snap["skills"],
            "wps_export_ready": capabilities.wps_available()[0],
            # 会话内文档预览是否启用（skill: doc-session-preview；只影响会话页右侧预览面板）
            "session_doc_preview_ready": capabilities.session_preview_ready(),
            # PPT 优化可用性（skill: ppt-polish 开关 ∧ ppt-master 装好；见 docs/19-PPT优化与幻灯片编辑.md）
            "ppt_polish_ready": capabilities.ppt_polish_ready()}


# ---------- Skill 调用：通用入口（技能自带入口脚本，平台只转发） ----------
@app.post("/api/skill/run")
def api_skill_run(body: dict):
    """通用技能调用：{"skill": "<技能id>", "args": {...}} -> 技能输出的结构化结果。

    技能入口由各自 SKILL.md 的 `entry` 声明，所以**新增技能不需要新接口**
    （往 skills/<id>/ 放一个 SKILL.md 就会被动态发现并出现在 Skill 市场）。
    技能被关闭 -> {ok:false, gate:'capability'}。同步 def，FastAPI 丢线程池，慢技能不卡事件循环。
    """
    try:
        return capabilities.run_skill(str(body.get("skill") or ""), body.get("args") or {})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": type(e).__name__ + ": " + str(e)}


# ---------- WPS 导出（教案中心 -> .docx / .pptx） ----------
def _export_media(body: dict) -> dict:
    """导出 PNG / JPEG / PDF（R23）：**每页 SVG → Edge 无头截图 → Pillow 拼**。

    与 pptx 同源（`slide_deck.build_deck` 出的那份 SVG），所以"预览=导出"在图片/PDF 上也成立。
    产物落在 `data/ppt_out/<标题>_<kind>_<时间戳>/`（与 pptx 一个地方，老师找得到）。
    """
    from edu_agent import ppt_polish, slide_deck, slide_media

    kind = str(body.get("kind") or "png").lower()
    slides = [s for s in (body.get("slides") or []) if isinstance(s, dict)]
    theme_id = str(body.get("theme_id") or "").strip()
    title = str(body.get("title") or "").strip() or "教案"
    sid = str(body.get("session_id") or "").strip()
    if not slides and sid:
        saved = _store.get_session_doc(sid)
        if saved.get("slides"):
            slides = saved["slides"]
            title = str(body.get("title") or "").strip() or str(saved.get("title") or "") or title
    if not slides:
        return JSONResponse({"ok": False, "error": "没有可导出的幻灯片（先在面板里生成教案）"},
                            status_code=400)
    built = slide_deck.build_deck(theme_id or slide_deck.DEFAULT_THEME, slides, title)
    pages = built.get("pages") or []
    if not pages:
        return JSONResponse({"ok": False, "error": "这份稿没有可导出的页面"}, status_code=400)
    safe = re.sub(r'[\\/:*?"<>|\s]+', "_", title)[:40] or "教案"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = Path(str(body.get("outDir") or "").strip() or ppt_polish.outdir())
    out_dir = base / f"{safe}_{kind}_{stamp}"
    out_dir.mkdir(parents=True, exist_ok=True)
    shots = slide_media.render_png(pages, out_dir / "_png", prefix=safe)
    if not shots.get("ok"):
        return {"ok": False, "error": shots.get("error") or "截图失败",
                "edge": shots.get("edge") or ""}
    pngs = shots["files"]
    stats = [slide_media.png_stats(p) for p in pngs]
    sizes = {(s.get("width"), s.get("height")) for s in stats}
    inks = [float(s.get("ink") or 0) for s in stats]
    checks = [
        {"name": "每页尺寸 1280×720", "ok": sizes == {(1280, 720)}, "detail": str(sorted(sizes))},
        {"name": "每页都有内容（不是空白）", "ok": all(i > 0.005 for i in inks),
         "detail": "墨迹占比 " + "、".join(f"{i:.3f}" for i in inks)},
        {"name": "页数与幻灯片一致", "ok": len(pngs) == len(pages),
         "detail": f"{len(pngs)} 张 / {len(pages)} 页"},
    ]
    if kind == "jpeg":
        made = slide_media.make_jpegs(pngs, out_dir, prefix=safe)
        if not made.get("ok"):
            return {"ok": False, "error": made.get("error") or "转 JPEG 失败"}
        files, filename = made["files"], f"{safe}.jpg（{len(made['files'])} 张）"
        for p in pngs:                                   # JPEG 交付时不必留中间 PNG
            Path(p).unlink(missing_ok=True)
        try:
            (out_dir / "_png").rmdir()
        except OSError:
            pass
    elif kind == "pdf":
        made = slide_media.make_pdf(pngs, out_dir / f"{safe}.pdf")
        if not made.get("ok"):
            return {"ok": False, "error": made.get("error") or "拼 PDF 失败"}
        files, filename = [made["file"]], Path(made["file"]).name
    else:
        files, filename = pngs, f"{safe}.png（{len(pngs)} 张）"
    ok = all(c["ok"] for c in checks)
    out = {"ok": ok, "kind": kind, "filename": filename, "outDir": str(out_dir), "files": files,
           "pages": len(pages), "size": shots.get("size"), "checks": checks,
           "browser": Path(shots.get("edge") or "").name,
           "download_url": "/api/wps/download?path=" + urllib.parse.quote(str(out_dir)),
           "detail": (f"已导出 {len(pages)} 页（{kind.upper()} · 1280×720）· " +
                      "；".join(("✅ " if c["ok"] else "⚠️ ") + c["name"] for c in checks))}
    if str(body.get("delivery") or "") in ("1", "true", "yes"):
        out["delivery"] = _delivery_check(str(out_dir / f"{safe}.pptx"))
    return out


def _delivery_check(pptx_path: str) -> dict:
    """产物交付体检（R23）：ppt-master 的 `pptx_delivery_check.py`，**只读**。

    为什么值得跑：老师要拿去上课、发给学生；字体缺失/外链/媒体缺失这类问题在 WPS 里
    未必当场看得出来，这个脚本一次列出来。我们只把摘要写进结果条，**不阻断导出**。
    """
    if not pptx_path or not Path(pptx_path).is_file():
        return {"ok": False, "error": "没有 pptx 可体检"}
    from edu_agent import ppt_polish

    tool = ppt_polish.scripts_dir() / "pptx_delivery_check.py"
    if not tool.is_file():
        return {"ok": False, "error": "ppt-master 不在（跑不了交付体检）"}
    try:
        proc = subprocess.run([sys.executable, str(tool), str(pptx_path)],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=180)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"体检没跑成：{type(exc).__name__}"}
    raw = (proc.stdout or "").strip()
    data: dict = {}
    if raw:
        try:
            data = json.loads(raw[raw.find("{"):raw.rfind("}") + 1] or "{}")
        except Exception:                             # noqa: BLE001 不是 JSON 就只留原文尾巴
            data = {}
    risks = data.get("risks") or data.get("issues") or data.get("findings") or []
    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    return {"ok": True, "exit": proc.returncode, "risks": risks,
            "risk_count": len(risks) if isinstance(risks, list) else summary.get("total", 0),
            "summary": summary or {k: v for k, v in data.items()
                                   if k in ("status", "ok", "passed", "warnings", "errors")},
            "raw_tail": "" if data else raw[-400:]}


def _export_pptx_deck(body: dict) -> dict:
    """幻灯片导出：**同一份 SVG**（面板预览用的那份）→ 原生可编辑 pptx。

    `docs/22` D15 双轨：默认走 `slide_deck.export_deck_pptx`（ppt-master 转换）；
    转换没通过硬验收、或设了 `EDU_PPT_EXPORT=legacy` ⇒ **自动回退**旧的 `pptx_native`，
    并在响应里带 `fallback/reason`（面板会明示，不静默降级）。
    """
    from edu_agent import slide_deck

    slides = [s for s in (body.get("slides") or []) if isinstance(s, dict)]
    theme_id = str(body.get("theme_id") or "").strip()
    title = str(body.get("title") or "").strip()
    out_dir = (body.get("outDir") or "").strip() or None
    md = _strip_agent_tag(str(body.get("markdown") or ""))
    sid = str(body.get("session_id") or "").strip()
    if not slides and sid:                       # **明确给了会话**才用会话里那份（不猜"当前会话"）
        saved = _store.get_session_doc(sid)
        if saved.get("slides"):
            slides = saved["slides"]
            title = title or str(saved.get("title") or "")
    if not slides and not md.strip():            # 既没幻灯片也没正文：明确报错，别静默导出别人的稿
        return JSONResponse({"ok": False,
                             "error": "没有可导出的内容（既没有幻灯片，也没有教案正文；"
                                      "先在面板里生成教案，或带上 session_id）"},
                            status_code=400)

    def _legacy(reason: str) -> dict:
        from edu_agent import wsl_docx

        # 新版式这条线的产物目录是 data/ppt_out；回退时**别**把稿子写进老师的 content/plans
        # （旧版式导出从面板走时仍按它原来的默认行为，这里只改新版式的回退）
        fallback_dir = out_dir or str(ppt_polish.outdir())
        r = wsl_docx.export_best(body.get("payload") or {}, markdown=md, kind="pptx",
                                 out_dir=fallback_dir)
        r = dict(r or {})
        r["fallback"] = True
        r["reason"] = reason
        r["backend"] = f"legacy（{r.get('backend') or 'pptx_native'}）"
        if r.get("ok"):
            r["detail"] = (r.get("detail") or "已用旧版式导出") + f"　（回退原因：{reason}）"
        return r

    if slide_deck.export_backend() == "legacy":
        return _legacy("设置了 EDU_PPT_EXPORT=legacy")

    theme_ok = True
    try:                                  # 主题不合法（文件被改坏）也算回退理由，别直接 500
        slide_deck.theme(theme_id)
    except Exception as exc:  # noqa: BLE001
        theme_ok = False
        theme_reason = f"主题不可用：{exc}"

    if not theme_ok:
        return _legacy(theme_reason)

    out = slide_deck.export_deck_pptx(theme_id, slides, title=title, out_dir=out_dir)
    if not out.get("ok"):
        if out.get("gate") == "install":
            return {"ok": False, "gate": "install", "error": out.get("error") or "ppt-master 未就绪"}
        return _legacy(f"新版式导出没通过验收：{out.get('error') or '未知原因'}")

    v = out.get("verify") or {}
    notes_n = int(out.get("notes_written") or 0)
    dcheck = _delivery_check(out["file"])           # R23：产物交付体检（只读，不阻断导出）
    dc_note = ""
    if dcheck.get("ok"):
        n = int(dcheck.get("risk_count") or 0)
        dc_note = ("· 交付体检：无风险" if not n else f"· 交付体检：{n} 条待看")
        if n:
            dc_note += "（" + "、".join(str((r or {}).get("code") or (r or {}).get("name") or r)[:28]
                                      for r in (dcheck.get("risks") or [])[:3]) + "）"
    elif dcheck.get("error"):
        dc_note = "· 交付体检没跑成：" + str(dcheck["error"])[:40]
    return {"ok": True, "backend": "native", "filename": out["filename"], "outDir": out["outDir"],
            "file": out["file"], "pages": out["pages"], "theme_id": out["theme_id"],
            "fallback": False,
            "notes_written": notes_n, "postcheck": out.get("postcheck") or {},
            "delivery": dcheck,
            "detail": f"已导出 {out['pages']} 页（原生可编辑）" +
                      (f" · {notes_n} 页带讲稿备注" if notes_n else "") + " · " + "；".join(
                f"{'✅' if c['ok'] else '❌'} {c['name']}" for c in (v.get("checks") or [])) + dc_note,
            "verify": v, "download_url": "/api/wps/download?path=" + str(out["file"])}


@app.post("/api/wps/export")
def api_wps_export(body: dict):
    """教案 -> WPS 文件。payload=教案中心 DZ 模型；markdown=Agent B 教案时改用。

    body: {"payload": {...}, "markdown": "...", "kind": "docx"|"pptx"|"pptx-deck",
           "slides": [...], "theme_id": "...", "outDir": "F:\\导出目录"}
    · `pptx-deck` = **新版式导出**（面板「幻灯片」页签看到的那份，原生可编辑）；
    · `pptx` = 旧版式导出（保留，回退与兼容都用它）。
    能力关闭时返回 {ok:false, gate:'capability'}，与 MCP/skill 开关状态一致。
    """
    try:
        from edu_agent import permissions

        _kind = str(body.get("kind") or "docx").lower()
        blocked = permissions.guard("export_pptx" if _kind.startswith("ppt")
                                    or _kind in ("png", "jpeg", "jpg", "pdf") else "export_docx",
                                    bool(body.get("confirm")))      # 落盘 = 有副作用，先过权限闸门
        if blocked:
            return blocked
        if _kind in ("pptx-deck", "pptx-native"):
            return _export_pptx_deck(body)
        if _kind in ("png", "jpeg", "jpg", "pdf"):
            return _export_media(body)
        # WPS 可用 → 照旧；WPS 不可用且本机有 WSL 后路 → 改道 WSL 渲染（见 edu_agent/wsl_docx.py）
        from edu_agent import wsl_docx

        result = wsl_docx.export_best(body.get("payload") or {},
                                      markdown=str(body.get("markdown") or ""),
                                      kind=str(body.get("kind") or "docx").lower(),
                                      out_dir=(body.get("outDir") or "").strip() or None,
                                      template=body.get("template") or None)
        if result.get("ok") and result.get("path"):
            token = register_download(result["path"])
            result["download_url"] = "/api/wps/download?id=" + token
        return result
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": type(e).__name__ + ": " + str(e)}


@app.get("/api/wps/download")
def api_wps_download(id: str = ""):
    """下载本进程刚导出的 Office 文件；不接受客户端给出的任意本机路径。"""
    p = take_download(id)
    if p is None or p.suffix.lower() not in (".docx", ".pptx", ".doc", ".ppt", ".pdf"):
        return JSONResponse({"ok": False, "error": "下载链接无效或已过期，请重新导出"}, status_code=404)
    return FileResponse(str(p), filename=p.name)


# ---------- 课件生成（对话链路 → 原生 PPTX，2026-09-26）----------
class CoursewareReq(BaseModel):
    markdown: str = ""          # 会话里那份教案（前端把它贴回来）
    payload: dict | None = None # 教案中心 DZ 结构（可选，优先）
    title: str = ""
    session_id: str = ""
    outDir: str = ""
    confirm: bool = False       # 权限动作 ask 时，前端确认后带 true 重试


class SlideWorkbenchReq(BaseModel):
    """教案中心 PPT 面板的轻量合同：当前教案 → 同源 SVG 预览 / PPTX。"""

    slides: list[dict] | None = None
    title: str = ""
    theme_id: str = ""
    template_id: str = ""       # ppt-master 原生母版；为空时沿用画布主题导出
    confirm: bool = False


class SlideSlotEditReq(BaseModel):
    """原版工作台的文字槽位编辑请求（不落盘，由教案中心持有草稿）。"""

    slides: list[dict] | None = None
    index: int = 0
    slot: str = ""
    text: str | None = None
    patch: dict | None = None


class SlideObjectEditReq(BaseModel):
    """原版画布自由对象：文本框或形状的新增、修改、删除与层级调整。"""

    slides: list[dict] | None = None
    index: int = 0
    action: str = "add"
    id: str = ""
    kind: str = "shape"
    shape: str = "rect"
    patch: dict | None = None
    title: str = ""
    theme_id: str = ""
    src: str = ""  # kind=image 时：data/uploads 下的受限相对路径


@app.get("/api/slide/themes")
def api_slide_themes():
    """原版 PPT 工作台的主题目录；读取失败不影响教案编辑。"""
    try:
        return {"ok": True, "default": slide_deck.default_theme_id(),
                "themes": slide_deck.themes()}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "default": "", "themes": [],
                "error": f"主题读取失败：{type(exc).__name__}: {exc}"}


@app.get("/api/ppt-master/templates")
def api_ppt_master_templates():
    """公开当前已安装 ppt-master 的课堂可用母版，避免前端写死一个并不存在的模板。"""
    from edu_agent import ppt_master

    root = ppt_master.engine_root() / "skills" / "ppt-master" / "templates" / "layouts"
    catalog = [
        {"id": "presentation_core_43", "name": "PPT Master · 课堂母版（4:3）",
         "description": "16 个课堂与投影版式，含标题、对比、流程、图表、表格"},
        {"id": "presentation_core", "name": "PPT Master · 通用母版（16:9）",
         "description": "20 个通用演示版式，适合课堂、公开课与说课"},
    ]
    return {"ok": True, "templates": [item for item in catalog
            if (root / item["id"] / "templates" / "design_spec.md").is_file()]}


@app.get("/api/ppt/templates")
def api_ppt_templates():
    """教案中心的 PPT 模板库：视觉主题与真实 PPT Master 母版分开陈列。"""
    themes = []
    for item in slide_deck.themes():
        palette = item.get("palette") or {}
        imported = item.get("kind") == "pptx"
        themes.append({
            "id": f"theme:{item.get('id')}", "kind": "theme", "theme_id": item.get("id"),
            "master_id": "", "name": item.get("name") or item.get("id"),
            "description": ("原 PPTX 模板：保留母版、背景和版式，Agent 只填充文字槽"
                            if imported else "用于编辑器预览和普通可编辑导出"),
            "tags": (["原模板填充", f"{item.get('pages') or 0} 页原稿"] if imported
                     else ["视觉主题", "16:9"]),
            "source_pptx": imported,
            "palette": {"background": (item.get("canvas") or {}).get("bg", "#FFFFFF"),
                        "text": palette.get("text", "#172033"),
                        "accent": palette.get("accent", "#2563EB"),
                        "sub": palette.get("sub", "#64748B"),
                        "soft": palette.get("soft", "#EFF6FF"),
                        "line": palette.get("line", "#CBD5E1")},
        })
    master_response = api_ppt_master_templates()
    masters = master_response.get("templates", [])
    for item in masters:
        is_classroom = item["id"] == "presentation_core_43"
        themes.insert(0 if is_classroom else 1, {
            "id": f"master:{item['id']}", "kind": "master", "theme_id": "tech-editorial",
            "master_id": item["id"],
            "name": "课堂规范 4:3" if is_classroom else "通用演示 16:9",
            "description": item["description"],
            "tags": ["PPT Master 原生母版", "16 个版式" if is_classroom else "20 个版式"],
            "palette": {"background": "#FFFFFF", "text": "#1E293B", "accent": "#2563EB",
                        "sub": "#64748B", "soft": "#EFF6FF", "line": "#CBD5E1"},
        })
    return {"ok": True, "templates": themes}


@app.get("/api/ppt/templates/preview")
def api_ppt_template_preview(id: str = Query("", min_length=1, max_length=80)):
    """返回上传 PPTX 的第 1 页真实缩略图；内置主题没有原稿，不走这条路径。"""
    from edu_agent import ppt_polish, slide_fill, slide_template

    template = slide_template.get(id)
    if not template or template.get("kind") != "pptx":
        return JSONResponse({"ok": False, "error": "未找到可预览的上传模板"}, status_code=404)

    source = slide_template.root() / slide_template._slug(id) / "template.pptx"
    if not source.is_file():
        return JSONResponse({"ok": False, "error": "模板原稿不存在"}, status_code=404)

    # 转换器会写临时 SVG；读入第 1 页后立即清理，不能在模板目录留下任何派生文件。
    import shutil

    job = ppt_polish.workroot() / f"template-preview-{uuid.uuid4().hex}"
    try:
        job.mkdir(parents=True, exist_ok=False)
        pages = slide_fill.preview_pages(source, job, limit=1)
        if not pages:
            return JSONResponse({"ok": False, "error": "模板缩略图生成失败"}, status_code=503)
        return Response(content=pages[0], media_type="image/svg+xml",
                        headers={"Cache-Control": "private, max-age=300"})
    finally:
        shutil.rmtree(job, ignore_errors=True)


@app.delete("/api/ppt/templates/{template_id}")
def api_ppt_template_delete(template_id: str):
    """软删除老师上传的模板；内置主题与 PPT Master 母版始终只读。"""
    from edu_agent import slide_template

    template = slide_template.get(template_id)
    if not template or template.get("kind") != "pptx":
        return JSONResponse({"ok": False, "error": "只能删除自己上传的 PPTX 模板"}, status_code=404)
    result = slide_template.delete_template(template_id, soft=True)
    return JSONResponse(result, status_code=200 if result.get("ok") else 400)


@app.post("/api/ppt/templates/upload")
async def api_ppt_template_upload(file: UploadFile = File(...)):
    """上传一份 PPTX/POTX 并纳入本地模板库；原稿只保存副本，从不回写上传文件。"""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".pptx", ".potx"}:
        return JSONResponse({"ok": False, "error": "仅支持 .pptx 或 .potx 演示模板"}, status_code=400)
    tmp = _settings.data_dir / f"ppt_template_{uuid.uuid4().hex}{suffix}"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp.write_bytes(await _read_upload_limited(file, _settings.upload_max_bytes))
        if not zipfile.is_zipfile(tmp):
            return JSONResponse({"ok": False, "error": "文件不是有效的 Office 演示文稿包"}, status_code=400)
        with zipfile.ZipFile(tmp) as archive:
            entries = archive.infolist()
            if "ppt/presentation.xml" not in archive.namelist():
                return JSONResponse({"ok": False, "error": "文件不是有效的 PowerPoint 演示模板"}, status_code=400)
            if len(entries) > 20_000 or sum(item.file_size for item in entries) > _settings.upload_max_bytes * 8:
                return JSONResponse({"ok": False, "error": "模板解压后的内容过大，未导入"}, status_code=400)
        from edu_agent import slide_template

        result = slide_template.import_pptx(tmp, name=Path(file.filename or "导入模板").stem)
        return result
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:  # noqa: BLE001
        log.exception("PPT template import failed")
        return JSONResponse({"ok": False, "error": f"模板导入失败：{type(exc).__name__}: {exc}"}, status_code=500)
    finally:
        tmp.unlink(missing_ok=True)


@app.post("/api/slide/render")
def api_slide_render(body: SlideWorkbenchReq):
    """工作台预览：上传 PPTX 走原模板填充，普通主题走 SVG 渲染。"""
    try:
        slides = slide_deck.clean_slides(body.slides or [])
        # 上传的模板不能只借一组颜色：先填到原稿副本，再把那份真实 PPTX 转为 SVG。
        # 这保证预览里的母版、背景图和导出的 PPTX 来自同一个原模板。
        from edu_agent import slide_template

        imported = slide_template.get(body.theme_id)
        if imported and imported.get("kind") == "pptx":
            from edu_agent import slide_fill

            filled = slide_fill.fill_pptx(body.theme_id, slides, title=body.title,
                                           with_preview=True, preview_only=True, preview_limit=30)
            if not filled.get("ok"):
                return JSONResponse({"ok": False, "error": filled.get("error") or "原模板预览生成失败",
                                     "warnings": filled.get("warnings") or []}, status_code=400)
            pages = filled.get("preview") or []
            if not pages:
                return JSONResponse({"ok": False,
                                     "error": "已填充原模板，但本机无法生成预览图；请检查 PPT 预览组件",
                                     "warnings": filled.get("warnings") or []}, status_code=503)
            return {"ok": True, "theme_id": body.theme_id, "pages": pages,
                    "layouts": [p.get("layout") or "原模板版式" for p in (filled.get("plan") or {}).get("pages") or []],
                    "boxes": [], "warnings": filled.get("warnings") or [],
                    "template_mode": "source-pptx", "template_pages": len((filled.get("plan") or {}).get("pages") or []),
                    "stats": {"pages": len(pages), "chars": sum(len(str(x)) for s in slides for x in (s.get("bullets") or []))}}
        return {**slide_deck.build_deck(body.theme_id, slides, title=body.title,
                                        annotate=True), "enabled": True}
    except (FileNotFoundError, ValueError) as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    except Exception as exc:  # noqa: BLE001
        log.exception("PPT SVG render failed")
        return JSONResponse({"ok": False, "error": f"渲染失败：{type(exc).__name__}: {exc}"}, status_code=500)


@app.post("/api/slide/slot")
def api_slide_slot(body: SlideSlotEditReq):
    """把原版 SVG 的文字槽写回当前 PPT 草稿，再由同一引擎重排。"""
    slides = slide_deck.clean_slides(body.slides or [])
    if not slides:
        return JSONResponse({"ok": False, "error": "请先生成 PPT 页面"}, status_code=400)
    if body.index < 0 or body.index >= len(slides):
        return JSONResponse({"ok": False, "error": "页面编号无效"}, status_code=400)
    result: dict = {"ok": True, "slot": body.slot}
    if body.text is not None:
        changed = slide_deck.apply_slot(slides[body.index], body.slot, body.text)
        if not changed.get("ok"):
            return JSONResponse(changed, status_code=400)
        result.update(changed)
    if body.patch:
        changed = slide_deck.apply_override(slides[body.index], body.slot, body.patch)
        if not changed.get("ok"):
            return JSONResponse(changed, status_code=400)
        result.update(changed)
    if body.text is None and not body.patch:
        return JSONResponse({"ok": False, "error": "需要文字或画布位置修改"}, status_code=400)
    return {"ok": True, "slides": slide_deck.clean_slides(slides), **result}


@app.post("/api/slide/object")
def api_slide_object(body: SlideObjectEditReq):
    """为教案中心的 PPT 画布维护自由文本框 / 形状；结果只保存在当前草稿。"""
    slides = slide_deck.clean_slides(body.slides or [])
    if not slides:
        return JSONResponse({"ok": False, "error": "请先生成 PPT 页面"}, status_code=400)
    if body.index < 0 or body.index >= len(slides):
        return JSONResponse({"ok": False, "error": "页面编号无效"}, status_code=400)
    page = dict(slides[body.index])
    objects = slide_deck.clean_objects(page.get("objects"))
    action = str(body.action or "add").lower()
    focus = ""
    if action == "add":
        focus = slide_deck.next_object_id(objects)
        pic = None
        if str(body.kind) == "image":
            from edu_agent import slide_image

            info = slide_image.probe(slide_image.uploads_root() / str(body.src or ""))
            if info.get("width") and info.get("height"):
                pic = (float(info["width"]), float(info["height"]))
        objects.append(slide_deck.new_object(body.kind, body.shape, oid=focus,
                                              theme=slide_deck.theme(body.theme_id),
                                              src=str(body.src or ""), pic=pic))
    elif action == "patch":
        changed = slide_deck.patch_object(objects, body.id, body.patch)
        if not changed.get("ok"):
            return JSONResponse(changed, status_code=400)
        objects, focus = changed["objects"], str(body.id)
    elif action == "delete":
        objects = [obj for obj in objects if str(obj.get("id")) != str(body.id)]
    elif action in ("up", "down"):
        ids = [str(obj.get("id")) for obj in objects]
        if str(body.id) not in ids:
            return JSONResponse({"ok": False, "error": "找不到这个对象"}, status_code=400)
        position = ids.index(str(body.id))
        target = position + (1 if action == "up" else -1)
        if not (0 <= target < len(objects)):
            return JSONResponse({"ok": False, "error": "已经到达层级边界"}, status_code=400)
        objects[position], objects[target] = objects[target], objects[position]
        focus = str(body.id)
    else:
        return JSONResponse({"ok": False, "error": f"不支持的对象操作：{action}"}, status_code=400)
    if objects:
        page["objects"] = objects
    else:
        page.pop("objects", None)
    slides[body.index] = page
    cleaned = slide_deck.clean_slides(slides)
    return {"ok": True, "slides": cleaned, "objects": cleaned[body.index].get("objects", []),
            "id": focus, "index": body.index, "action": action}


@app.post("/api/slide/image/upload")
async def api_slide_image_upload(file: UploadFile = File(...)):
    """接收 PPT 本地配图；只写入受限 uploads 目录，返回可安全内联的相对路径。"""
    from edu_agent import slide_image

    try:
        data = await _read_upload_limited(file, 20 * 1024 * 1024)
        result = slide_image.save_upload(data, file.filename or "upload.png", "lesson-center")
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=400)
    if not result.get("ok"):
        return JSONResponse({"ok": False, "error": result.get("error") or "图片上传失败"}, status_code=400)
    return {"ok": True, "path": result["path"], "width": result.get("width"),
            "height": result.get("height"), "detail": "图片已加入当前 PPT 草稿"}


@app.post("/api/slide/deck")
def api_slide_deck(body: SlideWorkbenchReq):
    """导出原版工作台所预览的同一份原生可编辑 PPTX。"""
    try:
        from edu_agent import permissions

        blocked = permissions.guard("export_pptx", bool(body.confirm))
        if blocked:
            return blocked
        slides = slide_deck.clean_slides(body.slides or [])
        from edu_agent import slide_template

        imported = slide_template.get(body.theme_id)
        if imported and imported.get("kind") == "pptx":
            # 优先：老师上传的原稿。保留其母版、图片、背景与版式，只替换可填文字。
            from edu_agent import slide_fill

            out = slide_fill.fill_pptx(body.theme_id, slides, title=body.title, with_preview=False)
        elif body.template_id:
            # 走 ppt-master 的原生 Layout 原型，而非当前画布的轻量 SVG 版式。
            from edu_agent import ppt_master

            out = ppt_master.generate(slides, name=body.title or "课堂课件",
                                      template_id=body.template_id)
            if out.get("ok") and out.get("pptx"):
                out["file"] = out["pptx"]
                out["filename"] = Path(str(out["pptx"])).name
        else:
            out = slide_deck.export_deck_pptx(body.theme_id, slides, title=body.title)
        if out.get("ok") and out.get("file"):
            out["download_url"] = "/api/wps/download?id=" + register_download(out["file"])
        return out
    except Exception as exc:  # noqa: BLE001
        log.exception("PPT deck export failed")
        return {"ok": False, "error": f"PPTX 生成失败：{type(exc).__name__}: {exc}"}


@app.post("/api/ppt/courseware")
def api_ppt_courseware(req: CoursewareReq):
    """教案 / 会话内容 → **原生可编辑** .pptx（引擎在 WSL 里跑，实测约 15~30s，首次含冷启动）。

    两道闸门（与「导出 PPT · WPS」一致，不新造概念）：
      ① 能力开关 skill:ppt-master（关掉 → gate=capability）
      ② 权限动作 export_pptx（他当前是 ask → 第一次返回 gate=permission，前端确认后带 confirm=true）
    内容来源优先级：payload（教案中心）> markdown（请求体，前端贴回当前回答）> 会话里最近一份教案。
    交付沿用 WPS 导出那套**一次性下载令牌**，不接受客户端给任意路径。
    """
    try:
        if not capabilities.is_enabled("skill", "ppt-master"):
            return {"ok": False, "gate": "capability",
                    "error": "「课件生成」能力已关闭（右栏「运行状态」里可以打开）"}
        from edu_agent import permissions

        blocked = permissions.guard("export_pptx", bool(req.confirm))
        if blocked:
            return blocked
        from edu_agent import courseware

        md = str(req.markdown or "").strip()
        if not md and not (req.payload or {}).get("steps"):
            md = _last_plan_markdown(str(req.session_id or "").strip() or (_store.current_id() or ""))
        r = courseware.generate(markdown=md, payload=req.payload, title=req.title,
                                out_dir=(req.outDir or "").strip() or None)
        if r.get("ok") and r.get("pptx"):
            token = register_download(r["pptx"])
            r["download_url"] = "/api/wps/download?id=" + token
        return r
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": type(e).__name__ + ": " + str(e)}


# ---------- 长期记忆（对齐 DeepSeek「记忆」：跨会话 · 可见 · 可删 · 可关） ----------
@app.get("/api/memory")
def api_memory_get():
    """记忆面板数据：开关状态 + 全部条目 + 当前会注入的整段前缀（便于自证）。"""
    from edu_agent import memory

    return {"ok": True, "enabled": memory.memory_enabled(MEM_PROFILE), "profile": MEM_PROFILE,
            "facts": memory.list_facts(MEM_PROFILE),
            "stats": memory.stats(MEM_PROFILE),
            "conflicts": memory.list_conflicts(MEM_PROFILE),
            "prefix": memory.profile_memory_prefix(MEM_PROFILE)}


@app.post("/api/memory/add")
def api_memory_add(body: dict):
    from edu_agent import memory

    fid = memory.add_fact(MEM_PROFILE, str(body.get("text") or ""),
                          kind=str(body.get("kind") or "preference"),
                          source=str(body.get("source") or "user"))
    if not fid:
        return {"ok": False, "error": "内容为空，或与已有记忆重复", "facts": memory.list_facts(MEM_PROFILE)}
    return {"ok": True, "id": fid, "facts": memory.list_facts(MEM_PROFILE)}


@app.post("/api/memory/delete")
def api_memory_delete(body: dict):
    from edu_agent import memory

    try:
        fid = int(body.get("id") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "error": "id 非法"}
    ok = memory.delete_fact(MEM_PROFILE, fid)
    return {"ok": ok, "facts": memory.list_facts(MEM_PROFILE)}


@app.post("/api/memory/clear")
def api_memory_clear():
    from edu_agent import memory

    return {"ok": True, "removed": memory.clear_facts(MEM_PROFILE), "facts": []}


@app.post("/api/memory/toggle")
def api_memory_toggle(body: dict):
    """记忆总开关：关掉后不注入、不自动抽取（已存条目保留）。"""
    from edu_agent import memory

    memory.set_memory_enabled(MEM_PROFILE, bool(body.get("enabled", True)))
    return {"ok": True, "enabled": memory.memory_enabled(MEM_PROFILE)}


@app.post("/api/memory/extract")
def api_memory_extract(body: dict):
    """手动跑一次抽取（补记/调试）：显式规则 + 一次低温模型抽取。"""
    from edu_agent import memory, memory_auto

    added = memory_auto.extract_and_store(str(body.get("text") or ""),
                                         str(body.get("answer") or ""), MEM_PROFILE)
    return {"ok": True, "added": added, "facts": memory.list_facts(MEM_PROFILE)}


# ---------- 记忆治理 / 冲突确认（2026-09-19，见 edu_agent.memory 的治理段） ----------
@app.post("/api/memory/prune")
def api_memory_prune(body: dict):
    """整理记忆：合并近重复 + 过期衰减 + 超量裁剪（钉住的永不删）。"""
    from edu_agent import memory

    r = memory.prune(MEM_PROFILE,
                     cap=int(body.get("cap") or 30),
                     ttl_days=float(body.get("ttl_days") or 180))
    r.update({"ok": True, "stats": memory.stats(MEM_PROFILE),
              "facts": memory.list_facts(MEM_PROFILE)})
    return r


@app.post("/api/memory/pin")
def api_memory_pin(body: dict):
    """钉住/取消钉住某条记忆（钉住的不参与衰减与裁剪）。"""
    from edu_agent import memory

    ok = memory.set_pinned(MEM_PROFILE, int(body.get("id") or 0), bool(body.get("pinned", True)))
    return {"ok": ok, "facts": memory.list_facts(MEM_PROFILE)}


@app.post("/api/memory/conflict/resolve")
def api_memory_conflict_resolve(body: dict):
    """用户裁决冲突：keep='new' 用新说法，keep='old' 保留旧说法。"""
    from edu_agent import memory

    r = memory.resolve_conflict(MEM_PROFILE, int(body.get("id") or 0), str(body.get("keep") or "new"))
    r["facts"] = memory.list_facts(MEM_PROFILE)
    r["conflicts"] = memory.list_conflicts(MEM_PROFILE)
    r["stats"] = memory.stats(MEM_PROFILE)
    return r


# ---------- 经验→技能：agent 自己写 SKILL.md（见 edu_agent/skills_local.py） ----------
@app.get("/api/skills/local")
def api_skills_local():
    """本机自建技能（与第三方 wps-skills 分开列）。"""
    from edu_agent import skills_local

    return {"ok": True, "skills": skills_local.list_skills(),
            "dir": str(skills_local.skills_dir())}


@app.post("/api/skill/save")
def api_skill_save(body: dict):
    from edu_agent import skills_local

    r = skills_local.save_skill(str(body.get("id") or body.get("name") or ""),
                                str(body.get("name") or ""),
                                str(body.get("description") or ""),
                                str(body.get("body") or ""),
                                tags=[t for t in (body.get("tags") or []) if isinstance(t, str)])
    r["skills"] = skills_local.list_skills()
    return r


@app.get("/api/skill/get")
def api_skill_get(id: str = ""):
    from edu_agent import skills_local

    return {"ok": True, "id": id, "text": skills_local.read_skill(id)}


# ---------- DSH 式技能：发现 + 按需加载（见 edu_agent/skill_catalog.py） ----------
@app.get("/api/skill/catalog")
def api_skill_catalog():
    """技能目录：多根发现（含 rank 优先级）、frontmatter 校验结果、被丢弃的技能与原因。

    与 `/api/skills/local` 的区别：那个只列"agent 自建"那一个目录；这个扫**所有根**
    （`<repo>/skills` 100 → `<data_dir>/skills` 200 → `EDU_SKILLS_DIR` 300 → `~/.edu-agent/skills` 400），
    并且按 DSH 的口径校验 frontmatter（name/description 必填、两个开关严格布尔）。
    """
    from edu_agent import skill_catalog

    return skill_catalog.stats()


@app.get("/api/skill/load")
def api_skill_load(name: str = ""):
    """按名加载技能**正文**（每次现读盘 → 改了正文下次加载即生效，不用重启）。"""
    from edu_agent import skill_catalog

    return skill_catalog.load(name)


@app.get("/api/skill/prompt")
def api_skill_prompt(q: str = ""):
    """调试用：看某个问题会注入哪些技能（目录 + 命中正文），以及为什么。"""
    from edu_agent import skill_catalog

    name = skill_catalog.explicit_intent(q)
    hits = [r["name"] for r in skill_catalog.relevant(q)]
    return {"ok": True, "query": q, "explicit": name, "relevant": hits,
            "prompt": skill_catalog.prompt_block(q)}


@app.post("/api/skill/delete")
def api_skill_delete(body: dict):
    """删除=归档到 skills/_archive/（不物理删，可找回）。"""
    from edu_agent import skills_local

    r = skills_local.delete_skill(str(body.get("id") or ""))
    r["skills"] = skills_local.list_skills()
    return r


# ---------- 跨会话检索（FTS5+trigram，见 edu_agent/session_search.py） ----------
@app.get("/api/session/search")
def api_session_search(q: str = "", limit: int = 5, sid: str = ""):
    from edu_agent import session_search

    return {"ok": True,
            "hits": session_search.search(q, limit=max(1, min(limit, 20)),
                                          exclude_sid=sid or None)}


@app.post("/api/session/reindex")
def api_session_reindex():
    from edu_agent import session_search

    return {"ok": True, **session_search.refresh(force=True)}


# ---------- 操作权限（动作级 allow / ask / deny，见 edu_agent/permissions.py） ----------
@app.get("/api/permissions")
def api_permissions_get():
    """权限面板数据：所有可授权动作 + 当前策略（含未表态时的默认值）。"""
    from edu_agent import permissions

    return {"ok": True, "actions": permissions.list_policies()}


@app.post("/api/permissions/set")
def api_permissions_set(body: dict):
    """改一个动作的策略：allow（总是允许）/ ask（每次问）/ deny（禁止）。"""
    from edu_agent import permissions

    r = permissions.set_policy(str(body.get("id") or ""), str(body.get("policy") or ""))
    r["actions"] = permissions.list_policies()
    return r


# ---------- PPT 优化（2026-09-23，一期：体检 / 保守原地统一 / 内容零丢失校验）----------
# 与「WPS 落盘导出」是两条独立链路：
#   · 本组接口：读**别人的** .pptx → 体检 → 原地保守统一 → 逐页校验内容没被改 → 下载新文件；
#   · /api/wps/export：把**我们的**教案落到 docx/pptx。
# 共同点：真正落盘都在服务端，进程里不碰 WPS COM。
class PptPolishReq(BaseModel):
    job_id: str = ""
    actions: list[str] = []          # font / size / strip-anim
    target_ea: str = ""              # 中文字体（默认 微软雅黑）
    target_latin: str = ""           # 西文字体（默认 Calibri）
    size_anchors: list[float] = []   # 字号层级锚（默认 44/32/28/24/20/18/16/14/12）


def _ppt_gate():
    """门禁：skill 关 → 403；没装 → 424（并带上自检，前端好提示怎么装）。"""
    from edu_agent import capabilities

    if not capabilities.ppt_polish_enabled():
        return JSONResponse({"ok": False, "gate": "capability",
                             "error": "「PPT 优化」已关闭（设置 → Skill 市场 → PPT 优化）"}, status_code=403)
    st = ppt_polish.status()
    if not st.get("intake_ready"):
        return JSONResponse({"ok": False, "gate": "install", "error": "ppt-master 未就绪",
                             "hint": "跑 deploy/setup_ppt_master.cmd（或设 PPT_MASTER_HOME）",
                             "status": st}, status_code=424)
    return None


@app.get("/api/ppt/status")
def api_ppt_status():
    """安装自检 + 开关状态（**不设门禁**：面板要靠它决定按钮可不可点）。"""
    from edu_agent import capabilities

    st = ppt_polish.status()
    return {"ok": True, "enabled": capabilities.ppt_polish_enabled(),
            "ready": capabilities.ppt_polish_ready(), "install": st,
            "actions": [{"id": k, "label": v} for k, v in ppt_polish._ACTION_LABELS.items()]}


@app.post("/api/ppt/intake")
async def api_ppt_intake(file: UploadFile = File(...)):
    """上传一份 .pptx → 落任务目录（原文件只读）→ 体检报告。"""
    gate = _ppt_gate()
    if gate:
        return gate
    name = (file.filename or "deck.pptx").lower()
    if not name.endswith((".pptx", ".pptm", ".ppsx", ".potx")):
        return JSONResponse({"ok": False, "error": "只支持 .pptx（含 .pptm/.ppsx/.potx）"}, status_code=400)
    tmp = Path(tempfile.mkdtemp(prefix="pptup_")) / (file.filename or "deck.pptx")
    try:
        tmp.write_bytes(await file.read())
        job = ppt_polish.new_job(tmp)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"落盘失败：{type(e).__name__}: {e}"}, status_code=500)
    finally:
        shutil.rmtree(tmp.parent, ignore_errors=True)
    rep = ppt_polish.intake(job)
    rep["job_id"] = job["job_id"]
    return rep


@app.post("/api/ppt/polish")
def api_ppt_polish(body: PptPolishReq):
    """在任务目录里做保守原地统一（原稿不动），返回逐项报告。"""
    gate = _ppt_gate()
    if gate:
        return gate
    if not body.job_id:
        return JSONResponse({"ok": False, "error": "缺 job_id（先 /api/ppt/intake）"}, status_code=400)
    job = {"job_id": body.job_id, "dir": str(ppt_polish.job_dir(body.job_id)),
           "source": str(ppt_polish.job_dir(body.job_id) / "source.pptx"), "name": "source.pptx"}
    if not Path(job["source"]).exists():
        return JSONResponse({"ok": False, "error": "任务不存在或已过期"}, status_code=404)
    rep = ppt_polish.polish(job, body.actions, target_ea=body.target_ea,
                            target_latin=body.target_latin,
                            size_anchors=body.size_anchors or None)
    if rep.get("ok"):
        rep["verify"] = ppt_polish.verify(job, Path(rep["file"]))
    return rep


@app.post("/api/ppt/cancel")
def api_ppt_cancel(body: dict):
    """取消 = 删任务目录（源文件与中间产物一起清）。"""
    jid = str(body.get("job_id") or "")
    if not jid:
        return {"ok": False, "error": "缺 job_id"}
    ppt_polish.cleanup_job(jid)
    return {"ok": True, "job_id": jid}


@app.get("/api/ppt/download")
def api_ppt_download(job_id: str = ""):
    """下载优化后的文件（先复制到 data/ppt_out，保留给老师）。"""
    if not job_id:
        return JSONResponse({"ok": False, "error": "缺 job_id"}, status_code=400)
    src = ppt_polish.job_dir(job_id) / "polished.pptx"
    if not src.is_file():
        return JSONResponse({"ok": False, "error": "还没有优化结果"}, status_code=404)
    dst = ppt_polish.outdir() / f"优化_{job_id}.pptx"
    try:
        shutil.copy2(src, dst)
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"ok": False, "error": f"复制失败：{e}"}, status_code=500)
    return FileResponse(str(dst), filename=dst.name)


# ---------- 启动预热（自 api.py 移植：embedding + rerank 常驻） ----------
def _warmup() -> None:
    """后台预热 ollama bge-m3 + local rerank 模型（冷启动不卡首问；失败静默）。"""

    def _run():
        try:
            from edu_agent.config import get_embeddings

            get_embeddings().embed_query("课本教练预热")
        except Exception:
            pass
        s = load_settings()
        if s.rerank_on and s.rerank_provider == "local":
            try:
                from sentence_transformers import CrossEncoder

                CrossEncoder(str(s.rerank_model_dir))
            except Exception:
                pass

    threading.Thread(target=_run, daemon=True).start()


if __name__ == "__main__":
    import uvicorn

    # 日志收口：默认 data/logs/edu_agent.log（EDU_LOG_LEVEL / EDU_LOG_FILE 可覆盖）
    s = load_settings()
    logsetup.setup(level=os.getenv("EDU_LOG_LEVEL", "INFO"),
                   log_file=str(s.log_file))
    _warmup()
    # 端口：默认 5174；EDU_WEB_PORT 可覆盖（演示/多实例并存，如 EDU_WEB_PORT=5181）
    uvicorn.run(app, host=os.getenv("EDU_WEB_HOST", "127.0.0.1"),
                port=int(os.getenv("EDU_WEB_PORT", "5174")), log_level="warning")
