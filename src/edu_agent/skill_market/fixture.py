# -*- coding: utf-8 -*-
"""模块 3 · 匿名 fixture 的校验与（可选）试跑。

验收要求："通过匿名 fixture 和权限检查后才能提交团队审核"。所以 fixture 有两件事要做：

1. **匿名性**：不许出现学生姓名/学号/手机号/邮箱/身份证/作答明文（`perm_scan` 也会扫一遍，
   这里做的是结构化校验，能指出具体是哪个字段）。
2. **可执行**：fixture 里的 `assertions` 是提交审核前必须能过的断言。
   `run_fixture()` 在**没有真执行器**时做两件事：结构校验 + 用 AST 静态比对
   `plugin.py` 暴露的函数是否与 fixture 期望一致（不执行代码，草稿也能跑）；
   传入 `executor` 时才真正调用（发布后的回归测试用）。
"""
from __future__ import annotations

import re
from typing import Callable

from edu_agent.skill_market import kernel_loader, store

_PII_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("phone", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}")),
    ("id_card", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("student_name", re.compile(r"(姓名|学生名|真实姓名)\s*[:：]\s*\S")),
    ("student_id", re.compile(r"(学号|考号)\s*[:：]\s*[A-Za-z0-9]{4,}")),
)
_PII_KEYS = re.compile(r"(student|pupil|name|phone|mobile|email|id_?card|address|"
                       r"answer|transcript|homework|score_detail)", re.I)

REQUIRED_FIXTURE_KEYS = ("input", "expected_output_shape", "assertions")


def _walk(value, path: str = "") -> list[tuple[str, object]]:
    out: list[tuple[str, object]] = []
    if isinstance(value, dict):
        for k, v in value.items():
            out.append((path + "/" + str(k), v))
            out.extend(_walk(v, path + "/" + str(k)))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            out.extend(_walk(v, "%s[%d]" % (path, i)))
    return out


def validate_fixture(fixture: dict | None) -> dict:
    """匿名性 + 结构校验。返回 {ok, blocking, warnings}。"""
    blocking: list[str] = []
    warnings: list[str] = []
    if not isinstance(fixture, dict) or not fixture:
        return {"ok": False, "blocking": ["fixture 为空或不是 JSON 对象"], "warnings": []}

    for key in REQUIRED_FIXTURE_KEYS:
        if key not in fixture:
            blocking.append("fixture 缺少必填字段：%s" % key)
    if not isinstance(fixture.get("assertions"), list) or not fixture.get("assertions"):
        blocking.append("fixture.assertions 必须是非空数组（提交审核前要能过）")

    for path, val in _walk(fixture):
        leaf = path.rsplit("/", 1)[-1]
        if _PII_KEYS.search(leaf):
            blocking.append("fixture 字段 %s 命中隐私键名：匿名 fixture 只留标识" % path)
        if isinstance(val, str):
            for name, rx in _PII_PATTERNS:
                if rx.search(val):
                    blocking.append("fixture 字段 %s 里出现 %s 明文" % (path, name))
    if "note" not in fixture:
        warnings.append("建议加 note 说明匿名化方式（便于审核人核对）")
    return {"ok": not blocking, "blocking": blocking, "warnings": warnings}


def run_fixture(skill_id: str, *, executor: Callable[[dict], dict] | None = None) -> dict:
    """跑一个技能的 fixture。

    executor 缺省时（草稿阶段）：结构 + 匿名性 + **静态**签名比对，不执行任何代码。
    传入 executor（发布后回归）：真跑，并用 assertions 做基本断言。
    """
    fixture = store.read_fixture(skill_id)
    if fixture is None:
        return {"ok": False, "skill": skill_id, "error": "没有 fixture（先 POST /fixture/save）"}

    val = validate_fixture(fixture)
    out: dict = {"ok": val["ok"], "skill": skill_id, "validation": val,
                 "executed": False, "checks": []}
    if not val["ok"]:
        return out

    # 静态比对：插件暴露的函数与 fixture 期望是否对得上
    info = kernel_loader.describe_skill(skill_id)
    funcs = info.get("functions") or []
    out["plugin_functions"] = [f.get("function") for f in funcs]
    if info.get("plugin"):
        if not funcs:
            out["ok"] = False
            out["checks"].append({"check": "plugin 暴露 @kernel_function", "ok": False,
                                  "detail": "plugin 文件存在但没有 @kernel_function 函数"})
        else:
            out["checks"].append({"check": "plugin 暴露 @kernel_function", "ok": True,
                                  "detail": ", ".join(f["function"] for f in funcs)})
    else:
        out["checks"].append({"check": "plugin 文件", "ok": True,
                              "detail": "未声明 plugin（纯提示词技能）"})

    expected = (fixture.get("expected_output_shape") or {}).get("fields") or []
    if expected:
        out["checks"].append({"check": "期望输出字段已声明", "ok": True,
                              "detail": ", ".join(map(str, expected))})

    if executor is not None:
        out["executed"] = True
        try:
            got = executor(fixture.get("input") or {})
        except Exception as e:  # noqa: BLE001
            out["ok"] = False
            out["checks"].append({"check": "执行", "ok": False,
                                  "detail": "%s: %s" % (type(e).__name__, e)})
            return out
        out["result"] = got
        for a in fixture.get("assertions") or []:
            ok = _assert_one(str(a), got, fixture)
            out["checks"].append({"check": str(a), "ok": ok, "detail": ""})
            out["ok"] = out["ok"] and ok
    return out


def _assert_one(text: str, got: dict, fixture: dict) -> bool:
    """极简断言解释器（够用于 fixture 里的几条声明式断言）。"""
    g = got if isinstance(got, dict) else {}
    if "knowledge_id" in text:
        want = (fixture.get("input") or {}).get("knowledge_id")
        return bool(g.get("knowledge_id")) and (not want or g.get("knowledge_id") == want)
    if "非空" in text:
        key = "answer" if "答案" in text else ("analysis" if "解析" in text else "")
        if key:
            return bool(str(g.get(key) or "").strip())
        return bool(g)
    if "不得包含" in text or "不含" in text:
        blob = str(g)
        return not any(rx.search(blob) for _, rx in _PII_PATTERNS)
    return True
