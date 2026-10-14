# -*- coding: utf-8 -*-
"""脱敏、风险分级与导出策略（doc 34 §5.1 的 `skill_privacy.py`）。

**为什么单独收口一个模块**：脱敏与隐私判断此前散在三处 ——
`audit.redact()`（写日志时）、`contract.guard_payload()`（跨模块出站时）、
`perm_scan` 的 fixture 扫描（审核时）。散着写的问题是：**规则会各自漂移**。
这里把"什么算敏感、什么风险等级、能不能出到团队库"三件事集中定义，其余模块调用它。

原则（doc 34 §1.2）：
* 默认**不**把教师原始对话、学生作品/姓名/成绩/联系方式/家庭信息同步出去；
* 本地功能必须能离线工作，隐私层**绝不**抛异常打断主链路（失败即保守拒绝）。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from edu_agent.skill_market import audit

# ---------------------------------------------------------------- 敏感模式（与 audit 同源口径）
_PATTERNS: tuple[tuple[str, re.Pattern, str], ...] = (
    ("student_name", re.compile(r"(学生姓名|学生名|姓名|学生)\s*[:：]\s*[\u4e00-\u9fa5]{2,4}"),
     "学生姓名"),
    ("student_id", re.compile(r"(学号|考号|座号)\s*[:：]\s*[A-Za-z0-9]{4,20}"), "学号/考号"),
    ("phone", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "手机号"),
    ("email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}"), "邮箱"),
    ("id_card", re.compile(r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])"
                           r"(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"), "身份证号"),
    ("score", re.compile(r"(成绩|得分|分数)\s*[:：]?\s*\d{1,3}(\.\d+)?\s*(分)?"), "成绩明细"),
    ("family", re.compile(r"(家长|监护人|家庭住址|父母)\s*[:：]"), "家庭/监护人信息"),
)

# 风险等级：按"技能声明了什么能力"判（不是按内容猜）
_RISK_HIGH = ("action:file_open", "action:pdf_ingest", "action:template_delete",
              "action:project_new")
_RISK_NETWORK = ("skill:web-image", "action:vision_analyze")


def scan_text(text: Any) -> dict:
    """扫一段文本里的敏感信息（返回命中类型与计数，**不回显原文**）。"""
    probe = audit.strip_digests(text)          # 先剔自家摘要，避免误判身份证
    hits: list[dict] = []
    for code, rx, label in _PATTERNS:
        n = len(rx.findall(probe))
        if n:
            hits.append({"code": code, "label": label, "count": n})
    return {"ok": True, "sensitive": bool(hits), "hits": hits,
            "risk": "high" if hits else "low"}


def redact(text: Any) -> Any:
    """脱敏（复用审计层同一套规则，保证日志/草稿/导出三处一致）。"""
    return audit.redact(text)


def risk_level(manifest_or_row: dict) -> dict:
    """技能风险分级：依据 manifest 声明的能力（能力越"能动"等级越高）。"""
    caps = list((manifest_or_row or {}).get("allowed_capabilities") or [])
    reasons: list[str] = []
    level = "low"
    for c in caps:
        if c in _RISK_HIGH:
            level = "high"
            reasons.append("声明了本机写/删类动作：%s" % c)
        elif c in _RISK_NETWORK:
            level = max(level, "medium") if level != "high" else level
            reasons.append("声明了联网能力：%s" % c)
    if any(str(c).startswith("action:") for c in caps) and level == "low":
        level = "medium"
        reasons.append("声明了平台动作（需走权限闸门）")
    return {"ok": True, "level": level, "reasons": reasons, "capabilities": caps}


def export_policy(skill_id: str) -> dict:
    """能不能把这个技能导出/同步到团队库（doc 34 §6.3 同步矩阵）。

    只有**全部满足**才允许：状态已安装/已发布 ∧ 教师批准且哈希一致 ∧ 无敏感内容 ∧
    资源清单干净。任一条拿不准 → **拒绝**（保守默认）。
    """
    from edu_agent.skill_market import registry_db, session_bootstrap, skill_studio

    sid = str(skill_id or "").strip()
    reasons: list[str] = []
    row = registry_db.get(sid)
    if row is None:
        return {"ok": False, "allowed": False, "reasons": ["注册表里没有这个技能"]}
    if str(row.get("status")) != "published":
        reasons.append("状态是 %s：只有 published（已安装）才能出团队库" % row.get("status"))
    good, why = session_bootstrap.is_published(sid)
    if not good:
        reasons.append("本地不可加载：%s" % why)
    appr = skill_studio.approval_status(sid)
    if not appr.get("approved"):
        reasons.append("没有有效的教师批准记录（%s）" % appr.get("reason"))
    elif not appr.get("hash_matches"):
        reasons.append("批准后内容被改过：哈希不一致，须重新批准")

    # 内容扫描：SKILL.md + 插件 + fixture 全都要过
    for f in skill_studio.skill_files(sid):
        try:
            got = scan_text(Path(f).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if got["sensitive"]:
            reasons.append("文件 %s 含敏感信息：%s"
                           % (Path(f).name, "、".join(h["label"] for h in got["hits"])))
    risk = risk_level(_manifest_dict(row))
    return {"ok": True, "allowed": not reasons, "reasons": reasons,
            "risk": risk["level"], "risk_reasons": risk["reasons"], "skill": sid}


def _manifest_dict(row: dict) -> dict:
    try:
        return json.loads(row.get("manifest") or "{}")
    except ValueError:
        return {}


def scrub_markdown(text: str) -> str:
    """生成脱敏版 Markdown（给"仅保存草稿 / 对外展示"用）：命中处替换成占位符。"""
    out = str(text or "")
    for code, rx, label in _PATTERNS:
        out = rx.sub("【已脱敏：%s】" % label, out)
    return out
