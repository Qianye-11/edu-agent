# -*- coding: utf-8 -*-
"""模块 3 · 跨模块契约：**只通过标识与对外 API 交互**。

这是本模块的"外交条例"，写成代码而不是口头约定：

* 允许传的东西：`knowledge_id`、`teaching_context`、`mastery_snapshot_id`、
  `question_id`、`lesson_id`、`stage`、`subject`、`class_id`（标识与上下文）。
* 禁止传的东西：学生姓名/学号/手机号/邮箱/身份证、原始作答明文、成绩明细。
  `TeachingContext.validate()` 与 `ModuleClient.call()` 都会在**出站前**拦下这些键。
* 禁止做的事：import 其他模块的内部模型或库（`edu_agent.knowledge`、题库 ORM、
  `sqlite3` 直连…）。对方模块只暴露 HTTP API，我们就只走 HTTP。

模块 2（题库）与模块 4（教师工作台）的地址来自环境变量，缺省即"未接线"：

    EDU_MODULE2_BASE=http://127.0.0.1:5180     # 题库/图谱
    EDU_MODULE4_BASE=http://127.0.0.1:5175     # 教师工作台
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

# 出站载荷里出现这些键 → 直接拒绝（原始**学生**数据不许进 Skill，也不许出 Skill）
# 注意区分：题目的 `answer`（正确答案）是合法内容，学生作答要叫 student_answer/raw_answer。
FORBIDDEN_PAYLOAD_KEYS: tuple[str, ...] = (
    "student_answer", "raw_answer", "answers", "answer_text", "answer_sheet",
    "student_name", "student_id", "pupil", "real_name", "transcript",
    "phone", "mobile", "email", "id_card", "idcard", "address",
    "wrong_answers", "score_detail", "homework_photo",
)

# 允许的键（白名单；比黑名单更可靠）：标识、上下文，以及**题目草稿内容**字段
ALLOWED_KEYS: tuple[str, ...] = (
    "knowledge_id", "knowledge_ids", "teaching_context", "mastery_snapshot_id",
    "class_id", "stage", "subject", "chapter", "lesson_id", "question_id",
    "difficulty", "question_type", "question_count", "count", "text", "query",
    "skill", "session_id", "trace_id", "outputs", "draft", "payload", "cards",
    "card", "title", "summary", "html", "markdown",
    # 题目草稿内容（模块2 的入库接口要吃这些）
    "kind", "stem", "answer", "analysis", "solution", "options", "node",
    "question", "questions", "items", "draft_id", "result",
    # 教材库只读（textbook:read）：书本与章节标识
    "book_id", "chapter_no", "chapter_title", "book", "sections", "toc", "pages",
)


@dataclass(frozen=True)
class TeachingContext:
    """跨模块传递的**唯一**教学上下文对象（模块2/模块4 共用同一形状）。"""

    knowledge_id: str = ""
    teaching_context: dict = field(default_factory=dict)
    mastery_snapshot_id: str = ""
    stage: str = ""
    subject: str = ""
    class_id: str = ""
    lesson_id: str = ""

    def to_payload(self) -> dict:
        out: dict[str, Any] = {}
        if self.knowledge_id:
            out["knowledge_id"] = self.knowledge_id
        if self.teaching_context:
            out["teaching_context"] = dict(self.teaching_context)
        if self.mastery_snapshot_id:
            out["mastery_snapshot_id"] = self.mastery_snapshot_id
        if self.stage:
            out["stage"] = self.stage
        if self.subject:
            out["subject"] = self.subject
        if self.class_id:
            out["class_id"] = self.class_id
        if self.lesson_id:
            out["lesson_id"] = self.lesson_id
        return out

    @classmethod
    def from_payload(cls, payload: dict | None) -> "TeachingContext":
        p = dict(payload or {})
        tc = p.get("teaching_context")
        return cls(
            knowledge_id=str(p.get("knowledge_id") or ""),
            teaching_context=dict(tc) if isinstance(tc, dict) else {},
            mastery_snapshot_id=str(p.get("mastery_snapshot_id") or ""),
            stage=str(p.get("stage") or (tc.get("stage") if isinstance(tc, dict) else "") or ""),
            subject=str(p.get("subject") or (tc.get("subject") if isinstance(tc, dict) else "") or ""),
            class_id=str(p.get("class_id") or ""),
            lesson_id=str(p.get("lesson_id") or ""),
        )

    def validate(self) -> list[str]:
        """出站前自检：键必须在白名单里，且不许出现隐私键。"""
        errs: list[str] = []
        payload = self.to_payload()
        for k in payload:
            if str(k).lower() in FORBIDDEN_PAYLOAD_KEYS:
                errs.append("载荷含禁止键 %r：Skill 之间只传标识，不传学生隐私/作答明文" % k)
            elif k not in ALLOWED_KEYS:
                errs.append("载荷含未登记键 %r（如需新增，先加进 contract.ALLOWED_KEYS 并过审）" % k)
        if not (self.knowledge_id or self.teaching_context or self.mastery_snapshot_id):
            errs.append("至少要有一个标识：knowledge_id / teaching_context / mastery_snapshot_id")
        return errs


def guard_payload(payload: dict | None) -> list[str]:
    """通用出站检查（模块4 卡片、题目草稿等自定义载荷也走它）。"""
    errs: list[str] = []
    for k, v in dict(payload or {}).items():
        kl = str(k).lower()
        if kl in FORBIDDEN_PAYLOAD_KEYS:
            errs.append("载荷含禁止键 %r" % k)
        elif kl not in ALLOWED_KEYS and not kl.startswith("x-"):
            errs.append("载荷含未登记键 %r" % k)
        if isinstance(v, dict):
            errs.extend(guard_payload(v))
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    errs.extend(guard_payload(item))
    return errs


def _default_transport(url: str, payload: dict, timeout: float) -> dict:
    """默认传输：stdlib urllib（不引入新依赖）。测试可注入假传输。"""
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310  只连本机模块
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": "HTTP %s" % e.code,
                "body": e.read().decode("utf-8", "replace")[:500]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {"ok": True, "data": data}
    except ValueError:
        return {"ok": True, "raw": raw[:2000]}


def _default_get_transport(url: str, params: dict, timeout: float) -> dict:
    """默认 GET 传输（只读查询：教材库章节、目录…）。测试可注入假传输。"""
    import urllib.parse

    qs = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v not in (None, "")})
    full = url + (("?" + qs) if qs else "")
    req = urllib.request.Request(full, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310  只连本机模块
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": "HTTP %s" % e.code,
                "body": e.read().decode("utf-8", "replace")[:500]}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {"ok": True, "data": data}
    except ValueError:
        return {"ok": True, "raw": raw[:2000]}


class ModuleClient:
    """跨模块调用客户端：**只有 HTTP**，没有第二条路。

    bases 可传任意模块名（module2 / module4 / platform…），未传的从环境变量取：

        EDU_MODULE2_BASE / EDU_MODULE4_BASE / EDU_PLATFORM_BASE（默认 http://127.0.0.1:5174）
    """

    def __init__(self, bases: dict[str, str] | None = None, *,
                 transport: Callable[[str, dict, float], dict] | None = None,
                 get_transport: Callable[[str, dict, float], dict] | None = None,
                 timeout: float = 20.0):
        given = {str(k): str(v) for k, v in (bases or {}).items() if v}
        self.bases = {
            "module2": given.get("module2") or os.getenv("EDU_MODULE2_BASE", ""),
            "module4": given.get("module4") or os.getenv("EDU_MODULE4_BASE", ""),
            "platform": given.get("platform") or os.getenv(
                "EDU_PLATFORM_BASE", "http://127.0.0.1:5174"),
        }
        # 其它自定义模块名原样保留（插件可以传任意键）
        for k, v in given.items():
            self.bases.setdefault(k, v)
        self.transport = transport or _default_transport
        self.get_transport = get_transport or _default_get_transport
        self.timeout = timeout
        self.calls: list[dict] = []          # 供审计取"输入摘要/执行结果"

    def call(self, module: str, path: str, payload: dict | None = None) -> dict:
        base = (self.bases.get(module) or "").rstrip("/")
        if not base:
            return {"ok": False, "error": "模块 %s 未接线（设 EDU_%s_BASE）" % (module, module.upper()),
                    "module": module, "path": path}
        body = dict(payload or {})
        errs = guard_payload(body)
        if errs:
            return {"ok": False, "gate": "contract", "module": module, "path": path,
                    "error": "；".join(errs)}
        url = base + path
        rec = {"module": module, "path": path, "payload": body, "method": "POST"}
        try:
            out = self.transport(url, body, self.timeout)
        except Exception as e:  # noqa: BLE001
            out = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        rec["result"] = out
        self.calls.append(rec)
        return out

    def get(self, module: str, path: str, params: dict | None = None) -> dict:
        """只读查询（GET）。参数同样过隐私/白名单闸门。"""
        base = (self.bases.get(module) or "").rstrip("/")
        if not base:
            return {"ok": False, "error": "模块 %s 未接线（设 EDU_%s_BASE）" % (module, module.upper()),
                    "module": module, "path": path}
        q = {k: v for k, v in dict(params or {}).items() if v not in (None, "")}
        errs = guard_payload(q)
        if errs:
            return {"ok": False, "gate": "contract", "module": module, "path": path,
                    "error": "；".join(errs)}
        url = base + path
        rec = {"module": module, "path": path, "payload": q, "method": "GET"}
        try:
            out = self.get_transport(url, q, self.timeout)
        except Exception as e:  # noqa: BLE001
            out = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        rec["result"] = out
        self.calls.append(rec)
        return out

    # ---- 模块2：题库 / 图谱 ----
    def read_knowledge_node(self, ctx: TeachingContext) -> dict:
        return self.call("module2", "/api/knowledge/node", ctx.to_payload())

    def create_question_draft(self, ctx: TeachingContext, draft: dict) -> dict:
        """把题目**草稿**交给模块2（入库与审核是模块2 的职责，模块3 不落库）。"""
        payload = ctx.to_payload()
        payload["draft"] = dict(draft or {})
        return self.call("module2", "/api/question/draft", payload)

    def read_mastery_summary(self, ctx: TeachingContext) -> dict:
        """只读聚合指标（不取学生明细）。"""
        return self.call("module2", "/api/mastery/summary", ctx.to_payload())

    # ---- 模块4：教师工作台 ----
    def emit_workbench_card(self, ctx: TeachingContext, card: dict) -> dict:
        payload = ctx.to_payload()
        payload["card"] = dict(card or {})
        return self.call("module4", "/api/workbench/card", payload)

    def summary(self) -> dict:
        """给审计用的一行摘要（脱敏后由 audit.py 再处理）。"""
        return {"calls": len(self.calls),
                "modules": sorted({c["module"] for c in self.calls}),
                "paths": [c["path"] for c in self.calls][:10]}
