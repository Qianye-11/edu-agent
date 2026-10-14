# -*- coding: utf-8 -*-
"""模块 3 · 能力令牌登记表（SKILL-01 的 `allowed_capabilities` / `data_scope` 词表）。

设计要点：**技能不许自己声明"我要联网/我要写文件"就完事** —— 它只能从这个白名单里
挑令牌，而白名单里的每一项都已经在平台里有对应实体：

| 令牌形状 | 对应实体 | 谁把关 |
|---|---|---|
| `kb.read` / `question.draft` … | 跨模块对外 API（模块2 题库、模块4 工作台） | 本文件登记 + contract.py 调用 |
| `skill:<id>` | `capabilities.all_skill_specs()` 里真实存在的技能 | capabilities.py |
| `mcp:<id>` | `capabilities.MCP_SPECS` 里真实存在的 MCP 服务 | capabilities.py |
| `action:<action_id>` | `permissions.ACTIONS` 里真实存在的动作 | permissions.py（三态闸门） |

所以 `perm_scan` 说"未声明的网络、文件、执行权限拒绝审核"时，判据是**可枚举的**：
令牌不在白名单 → 拒；令牌在白名单但平台里查无此物 → 拒（防止写个不存在的
`skill:whatever` 蒙混过关）。
"""
from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------- 跨模块 API 令牌
# 只允许"调用对方对外 API + 传标识"，禁止直接碰对方的库表（见 contract.py）。
CROSS_MODULE_CAPABILITIES: dict[str, str] = {
    "kg.read": "按 knowledge_id 读知识图谱节点（模块2 题库/图谱对外 API）",
    "kb.read": "按 knowledge_id 读题库题目与知识点（模块2 对外 API）",
    "question.draft": "生成题目草稿（**只产草稿**，不落库；模块2 负责入库与审核）",
    "mastery.read": "按 mastery_snapshot_id 读学情快照的**聚合指标**（禁止学生作答明文）",
    "lesson.read": "读课时/教案库条目（对外 API）",
    "workbench.emit": "向模块4 教师工作台推送卡片/面板数据（对外 API）",
    "session.read": "读取本会话的历史消息（仅用于 SKILL-02 草稿抽取，需 owner 显式触发）",
}

# ---------------------------------------------------------------- 平台自带能力
# 这些是**平台已经实现好的**只读能力，技能只做调用封装、不自己解析教材
# （约束：textbook:read 是系统已有能力，不要自己实现教材解析）。
PLATFORM_CAPABILITIES: dict[str, str] = {
    "textbook:read": "读本地教材库（只读）：/api/textbook、/api/textbook/chapters、"
                     "/api/textbook/toc、/api/textbook/stats、/api/knowledge-bases",
}


# ---------------------------------------------------------------- 出站目标映射
# 一个能力令牌对应"允许调到哪里"。影子运行（L3）用它判断"实际出站目标 ⊆ 声明范围"。
# 早先 shadow.py 硬编码 module2/module4，结果调平台只读接口（textbook:read → 127.0.0.1:5174）
# 被当成未声明目标、L3 误判失败（实测踩过）。这里改成由令牌驱动，加能力不用改 shadow。
CAPABILITY_TARGETS: dict[str, tuple[str, ...]] = {
    "kg.read": ("module2",),
    "kb.read": ("module2",),
    "question.draft": ("module2",),
    "mastery.read": ("module2",),
    "lesson.read": ("platform",),
    "workbench.emit": ("module4",),
    "session.read": ("platform",),
    "textbook:read": ("platform",),
}


def targets_for(tokens) -> set[str]:
    """一组能力令牌 → 允许的出站目标集合。

    `skill:<id>` / `mcp:<id>` / `action:<id>` 是**平台内部**能力（进程内或本机服务），
    归到 "local"，不需要外部目标声明。
    """
    out: set[str] = set()
    for t in tokens or []:
        t = str(t or "").strip()
        if not t:
            continue
        if t in CAPABILITY_TARGETS:
            out.update(CAPABILITY_TARGETS[t])
        elif t.startswith(("skill:", "mcp:", "action:")):
            out.add("local")
        else:
            # 跨模块令牌里带模块号的（如 xxx.module2）也认一下
            for m in ("module2", "module4", "platform"):
                if m in t:
                    out.add(m)
    return out


# 路径前缀 → 目标模块（环境变量没配时的兜底；比按端口猜可靠）
PATH_TARGETS: tuple[tuple[str, str], ...] = (
    ("/api/knowledge", "module2"),
    ("/api/question", "module2"),
    ("/api/mastery", "module2"),
    ("/api/workbench", "module4"),
    ("/api/lesson", "platform"),
    ("/api/textbook", "platform"),
    ("/api/knowledge-bases", "platform"),
    ("/api/sessions", "platform"),
    ("/api/session", "platform"),
)


def classify_url(url: str) -> str:
    """出站 URL → 目标名（module2 / module4 / platform / external）。

    判定顺序：环境变量里的模块地址 → 路径前缀表 → 本机地址算平台 → 其余算公网。
    """
    import os

    u = str(url or "").lower()
    for name, env in (("module2", "EDU_MODULE2_BASE"), ("module4", "EDU_MODULE4_BASE")):
        base = (os.getenv(env) or "").lower().rstrip("/")
        if base and base in u:
            return name
    for prefix, name in PATH_TARGETS:
        if prefix in u:
            return name
    plat = (os.getenv("EDU_PLATFORM_BASE") or "http://127.0.0.1:5174").lower().rstrip("/")
    if plat and plat in u:
        return "platform"
    if "127.0.0.1" in u or "localhost" in u or "[::1]" in u:
        return "platform"          # 本机默认算平台（教材库/会话/知识库都在平台里）
    return "external"


# ---------------------------------------------------------------- 数据范围词表
DATA_SCOPE_TOKENS: dict[str, str] = {
    "knowledge_id": "知识点/图谱节点标识",
    "teaching_context": "教学上下文（学段、学科、章节、课时）",
    "mastery_snapshot_id": "学情快照标识（只传 id，不传明细）",
    "class_id": "班级标识（不含学生个体信息）",
    "stage": "学段（初中/高中）",
    "subject": "学科",
    "session_id": "会话标识（仅审计溯源用）",
    "question_id": "题目标识",
    "lesson_id": "课时标识",
}

# 出现即**直接拒绝**的数据范围令牌：这些等于把学生隐私塞进 Skill
FORBIDDEN_DATA_TOKENS: tuple[str, ...] = (
    "student_name", "student_id", "student", "name", "real_name",
    "raw_answer", "answer", "answers", "answer_text", "transcript",
    "phone", "mobile", "email", "id_card", "idcard", "address",
    "score_detail", "wrong_answers", "homework_photo",
)


def _live_skill_tokens() -> dict[str, str]:
    """从 capabilities.py 现取技能 id（复用，不重写一份清单）。"""
    out: dict[str, str] = {}
    try:
        from edu_agent import capabilities

        for sid in sorted(capabilities.all_skill_specs()):
            out["skill:" + sid] = "平台技能：" + sid
        for spec in capabilities.MCP_SPECS:
            out["mcp:" + spec.id] = "MCP 服务：" + spec.name
    except Exception:  # noqa: BLE001  平台未装全时不能让市场整体不可用
        pass
    return out


def _permission_action_tokens() -> dict[str, str]:
    """从 permissions.py 现取动作 id（高风险动作必须走它的闸门）。"""
    out: dict[str, str] = {}
    try:
        from edu_agent import permissions

        for a in permissions.ACTIONS:
            out["action:" + a["id"]] = "平台动作（%s）：%s" % (a.get("risk", "?"), a.get("label", a["id"]))
    except Exception:  # noqa: BLE001
        pass
    return out


def allowed_capabilities() -> dict[str, str]:
    """完整白名单（每次现算，capabilities/permissions 新增能力后市场自动跟上）。"""
    merged: dict[str, str] = dict(CROSS_MODULE_CAPABILITIES)
    merged.update(PLATFORM_CAPABILITIES)
    merged.update(_live_skill_tokens())
    merged.update(_permission_action_tokens())
    return merged


def resolve_capability(token: str) -> tuple[bool, str]:
    """令牌是否可用；不可用时给出人话原因（进审核意见里）。"""
    t = str(token or "").strip()
    if not t:
        return False, "空令牌"
    table = allowed_capabilities()
    if t in table:
        return True, table[t]
    if t in CROSS_MODULE_CAPABILITIES:
        return True, CROSS_MODULE_CAPABILITIES[t]
    return False, ("未知能力令牌 %r：只能声明白名单里的能力（跨模块 API / skill:<id> / "
                   "mcp:<id> / action:<action_id>）" % t)


def resolve_data_scope(token: str) -> tuple[bool, str]:
    t = str(token or "").strip()
    if not t:
        return False, "空数据范围"
    if t.lower() in FORBIDDEN_DATA_TOKENS:
        return False, ("禁止声明学生隐私数据 %r：Skill 只能拿标识（knowledge_id / "
                       "teaching_context / mastery_snapshot_id），不能拿作答明文" % t)
    if t in DATA_SCOPE_TOKENS:
        return True, DATA_SCOPE_TOKENS[t]
    return False, "未知数据范围 %r（白名单：%s）" % (t, "、".join(sorted(DATA_SCOPE_TOKENS)))


def describe() -> dict[str, Any]:
    """给市场详情页（SKILL-06，前端由他人实现）用的词表快照。"""
    return {
        "capabilities": allowed_capabilities(),
        "cross_module": CROSS_MODULE_CAPABILITIES,
        "platform": PLATFORM_CAPABILITIES,
        "data_scope": DATA_SCOPE_TOKENS,
        "forbidden_data": list(FORBIDDEN_DATA_TOKENS),
    }
