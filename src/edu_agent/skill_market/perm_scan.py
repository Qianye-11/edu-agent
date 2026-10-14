# -*- coding: utf-8 -*-
"""SKILL-03 · 静态权限扫描：**没声明过的网络/文件/执行权限，一律拒绝审核。**

扫描分四层，逐层都能单独给出人话结论（进审核意见）：

1. **Manifest 层**：`allowed_capabilities` 里每个令牌必须能在
   `capability_registry` 里解析到真实实体；`data_scope` 不许出现学生隐私令牌。
2. **文本层**：扫 SKILL.md 正文（发布时还扫 `plugin.py` / `entry` 脚本）里的
   网络、写文件、执行外部程序、破坏性动作的**意图关键词**。命中某一类风险，
   而 manifest 又没声明对应的能力令牌 → `block`（"未声明的网络权限"）。
3. **越界层**：模块 3 严禁直接碰学情/题库的内部库表 —— 正文/脚本里出现
   `sqlite3`、`chromadb`、`SELECT …`、`from edu_agent.knowledge` 之类 → `block`。
4. **语义层**：正文提到"学情/掌握度"就必须把 `mastery_snapshot_id` 写进 data_scope；
   提到"图谱/节点"就必须有 `knowledge_id`；提到学段就必须有 `teaching_context`/`stage`。
   这条是为了防止"声明得很干净、实际在偷偷用学生数据"。

`for_publish=True` 时额外要求：`entry`/`plugin` 文件真实存在、`tests` 文件存在、
fixture 存在且已匿名化。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from edu_agent.skill_market import audit, capability_registry as caps
from edu_agent.skill_market import manifest as mf
from edu_agent.skill_market import paths

# ---------------------------------------------------------------- 风险关键词
# 每类风险：code → (正则, 人话说明)。命中后必须由 manifest 声明对应能力才放行。
RISK_PATTERNS: dict[str, list[tuple[str, re.Pattern, str]]] = {
    # 跨模块调用：**本机**模块 API（127.0.0.1/localhost）不是"联网"。
    # 早先所有 http:// 都按联网处理，结果任何调用模块2/模块4 对外 API 的插件
    # 都被判"未声明的网络访问"——等于把正常技能全挡在审核门外（实测踩过）。
    "module_api": [
        ("api.local_url", re.compile(r"https?://(?:127\.0\.0\.1|localhost|\[::1\])(?::\d+)?", re.I),
         "出现本机模块 API 地址"),
        ("api.module_client", re.compile(r"\bModuleClient\b"), "使用跨模块客户端 ModuleClient"),
    ],
    "network": [
        ("net.url", re.compile(r"https?://(?!127\.0\.0\.1|localhost|\[::1\])|ftp://", re.I),
         "出现公网地址（http/https/ftp）"),
        ("net.lib", re.compile(r"\b(requests|urllib|httpx|aiohttp|socket|websocket|curl|wget|fetch)\b", re.I),
         "出现网络库/命令调用"),
        ("net.api", re.compile(r"(api[_-]?key|bearer\s+[a-z0-9]|openai|deepseek|siliconflow)", re.I),
         "出现外部 API / 密钥字样"),
    ],
    "file_write": [
        ("fs.write", re.compile(r"(write_text|write_bytes|open\s*\([^)]*['\"][wa]|"
                                r"shutil\.|os\.remove|os\.rename|os\.mkdir|makedirs|unlink|rmtree)"),
         "出现写文件/删文件/建目录"),
        ("fs.path", re.compile(r"(/etc/|C:\\\\|D:\\\\|%USERPROFILE%|~/\.)", re.I),
         "出现本机绝对路径"),
    ],
    "exec": [
        ("exec.proc", re.compile(r"(subprocess|os\.system|os\.popen|Popen|shell\s*=\s*True|"
                                 r"powershell|cmd\.exe|bash\b)", re.I), "出现外部程序执行"),
        ("exec.dyn", re.compile(r"\b(eval|exec)\s*\(|__import__|importlib", re.I), "出现动态代码执行"),
    ],
    "destructive": [
        ("destroy.delete", re.compile(r"(drop\s+table|delete\s+from|truncate|format\s+[a-z]:)", re.I),
         "出现破坏性数据操作"),
    ],
}

# 每类风险需要 manifest 声明的能力令牌（命中任一即可）
RISK_REQUIRES: dict[str, tuple[str, ...]] = {
    # 本机模块 API 调用：跨模块令牌或平台只读能力（如 textbook:read）都算已声明
    "module_api": ("kg.read", "kb.read", "question.draft", "mastery.read",
                   "lesson.read", "workbench.emit", "session.read", "textbook:read"),
    "network": ("action:vision_analyze", "skill:web-image"),
    "file_write": ("action:export_docx", "action:export_pptx", "action:project_new",
                   "action:template_save", "action:pdf_ingest", "action:pdf_library_manage",
                   "action:file_open"),
    "exec": ("action:file_open",),
    "destructive": ("action:template_delete",),
}

RISK_LABEL = {"module_api": "跨模块调用", "network": "网络访问",
              "file_write": "文件写入", "exec": "执行外部程序",
              "destructive": "破坏性操作"}

# 越界：直接操作别的模块的内部库表/模型
BOUNDARY_PATTERNS: list[tuple[str, re.Pattern, str]] = [
    ("boundary.sqlite", re.compile(r"(sqlite3|\.db\b|edu_sessions|chroma|chromadb|faiss)", re.I),
     "出现本地库/向量库直连痕迹"),
    ("boundary.sql", re.compile(r"\b(select|insert\s+into|update\s+\w+\s+set|create\s+table)\b", re.I),
     "出现 SQL 语句"),
    ("boundary.import", re.compile(
        r"(from\s+edu_agent\s*\.\s*(knowledge|question_bank|mastery|kb|db)|"
        r"import\s+(knowledge|question_bank|mastery)\b)", re.I),
     "直接 import 其他模块内部模型"),
]

# 语义提示：正文提到这些词，data_scope 必须相应声明
SEMANTIC_HINTS: list[tuple[re.Pattern, tuple[str, ...], str]] = [
    (re.compile(r"(学情|掌握度|掌握情况|薄弱|得分率|正确率)"),
     ("mastery_snapshot_id",), "提到学情/掌握度 → data_scope 必须含 mastery_snapshot_id"),
    (re.compile(r"(图谱|节点|知识点|knowledge)"),
     ("knowledge_id",), "提到图谱/知识点 → data_scope 必须含 knowledge_id"),
    (re.compile(r"(学段|初中|高中|年级)"),
     ("teaching_context", "stage"), "提到学段 → data_scope 必须含 teaching_context 或 stage"),
]

# 否定语境守卫：技能文档里常见"**不**接触学情""**禁止**读取学情"这类声明，
# 那不是"要用学情数据"，不该触发语义检查（实测被自己的模板文案误伤过）。
_NEGATIONS: tuple[str, ...] = ("不", "禁止", "勿", "无需", "避免", "免", "不得", "别")
_NEG_WINDOW = 8


def _hint_hits(rx: re.Pattern, text: str) -> int:
    """统计**非否定语境**下的命中次数。"""
    n = 0
    body = str(text or "")
    for m in rx.finditer(body):
        pre = body[max(0, m.start() - _NEG_WINDOW):m.start()]
        if any(neg in pre for neg in _NEGATIONS):
            continue
        n += 1
    return n

# fixture 里不许出现的明文（匿名 fixture 的硬要求）
FIXTURE_FORBIDDEN: list[tuple[str, re.Pattern, str]] = [
    ("fixture.pii", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "fixture 里出现手机号"),
    ("fixture.email", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]{2,}"), "fixture 里出现邮箱"),
    ("fixture.idcard", re.compile(r"(?<!\d)[1-9]\d{5}(?:19|20)\d{2}(?:0[1-9]|1[0-2])"
                                  r"(?:0[1-9]|[12]\d|3[01])\d{3}[\dXx](?!\d)"),
     "fixture 里出现身份证号"),
    ("fixture.name", re.compile(r"(姓名|学生名|真实姓名|学号)\s*[:：]"), "fixture 里出现学生姓名/学号字段"),
]


@dataclass
class Finding:
    level: str          # block | warn
    code: str
    message: str
    where: str = ""

    def to_dict(self) -> dict:
        return {"level": self.level, "code": self.code, "message": self.message, "where": self.where}


@dataclass
class ScanReport:
    findings: list[Finding] = field(default_factory=list)
    declared: dict = field(default_factory=dict)
    stats: dict = field(default_factory=dict)

    @property
    def blocking(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "block"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warn"]

    @property
    def ok(self) -> bool:
        return not self.blocking

    @property
    def verdict(self) -> str:
        return "pass" if self.ok else "reject"

    def to_dict(self) -> dict:
        return {
            "ok": self.ok, "verdict": self.verdict,
            "blocking": [f.to_dict() for f in self.blocking],
            "warnings": [f.to_dict() for f in self.warnings],
            "declared": self.declared,
            "gated_actions": self.declared.get("gated_actions", []),
            "stats": self.stats,
            "review_note": self.review_note(),
        }

    def review_note(self) -> str:
        """给审核人看的一行结论（写进 manifest.review）。"""
        if self.ok:
            n = len(self.warnings)
            return "权限扫描通过（%d 项提示）" % n if n else "权限扫描通过"
        return "权限扫描未通过：%s" % "；".join(f.message for f in self.blocking[:3])


def _scan_text(text: str, where: str, declared: set[str], rep: ScanReport) -> None:
    """扫一段文本：风险类 → 是否已声明；越界 → 直接 block。"""
    body = str(text or "")
    if not body:
        return
    for risk, rules in RISK_PATTERNS.items():
        allowed = set(RISK_REQUIRES.get(risk, ()))
        for code, rx, msg in rules:
            if not rx.search(body):
                continue
            if declared & allowed:
                rep.findings.append(Finding("warn", code + ".declared",
                                            "%s（已声明对应能力，放行）" % msg, where))
            else:
                rep.findings.append(Finding(
                    "block", code,
                    "未声明的%s：%s；如确需，请在 allowed_capabilities 里声明 %s 之一"
                    % (RISK_LABEL.get(risk, risk), msg, " 或 ".join(sorted(allowed))), where))
    for code, rx, msg in BOUNDARY_PATTERNS:
        if rx.search(body):
            rep.findings.append(Finding(
                "block", code,
                "越界访问：%s。模块3 只能调用其他模块的**对外 API**（传 knowledge_id / "
                "teaching_context / mastery_snapshot_id），禁止直接操作对方库表或模型" % msg, where))
    for code, rx, msg in FIXTURE_FORBIDDEN:
        if code.startswith("fixture.") and where.startswith("fixture"):
            if rx.search(body):
                rep.findings.append(Finding("block", code, msg + "（匿名 fixture 不允许隐私明文）", where))


def scan(manifest: mf.SkillManifest, body: str = "", *,
         files: dict[str, str] | None = None,
         fixture_text: str = "",
         fixture_path: Path | None = None,
         skill_dir: Path | None = None,
         for_publish: bool = False) -> ScanReport:
    """静态扫描一个 Skill（草稿或待发布）。

    files: {相对路径: 文本}，用于把 plugin.py / entry 脚本一并纳入扫描。
    """
    rep = ScanReport()
    declared = set(manifest.allowed_capabilities)

    # ---- 1) manifest 层 ----
    for err in manifest.errors(strict_caps=True):
        rep.findings.append(Finding("block", "manifest", err, "SKILL.md front-matter"))

    gated: list[dict] = []
    for token in manifest.allowed_capabilities:
        if token.startswith("action:"):
            try:
                from edu_agent import permissions

                aid = token.split(":", 1)[1]
                pol = permissions.policy(aid)
                gated.append({"action": aid, "policy": pol,
                              "label": next((a["label"] for a in permissions.ACTIONS
                                             if a["id"] == aid), aid)})
                if pol == "deny":
                    rep.findings.append(Finding(
                        "warn", "action.denied",
                        "声明的高风险动作 %s 当前策略是 deny，调用时会被闸门拦下" % aid,
                        "permissions"))
            except Exception:  # noqa: BLE001
                pass

    # ---- 2) 文本层 + 3) 越界层 ----
    _scan_text(body, "SKILL.md 正文", declared, rep)
    for rel, text in (files or {}).items():
        _scan_text(text, rel, declared, rep)

    # ---- 4) 语义层 ----
    scope = set(manifest.data_scope)
    for rx, needed, msg in SEMANTIC_HINTS:
        if _hint_hits(rx, body) and not (scope & set(needed)):
            rep.findings.append(Finding("block", "semantic.scope", msg, "SKILL.md 正文"))

    # ---- 匿名 fixture ----
    if fixture_text:
        # 先剔掉自家摘要（sha256 十六进制），否则会被当成身份证号（实测踩过）
        probe = audit.strip_digests(fixture_text)
        _scan_text(probe, "fixture", declared, rep)
        for code, rx, msg in FIXTURE_FORBIDDEN:
            if not code.startswith("fixture."):
                continue
            if rx.search(probe):
                rep.findings.append(Finding("block", code,
                                            msg + "（匿名 fixture 不允许隐私明文）", "fixture"))

    # ---- 发布前额外要求 ----
    if for_publish:
        if not manifest.entry and not manifest.plugin:
            rep.findings.append(Finding(
                "warn", "publish.no_entry",
                "既没有 entry 也没有 plugin：发布后 Agent 无法调用它（纯提示词技能可以忽略）",
                "SKILL.md front-matter"))
        base = Path(skill_dir) if skill_dir else None
        for rel in ([manifest.entry] if manifest.entry else []) + \
                   ([manifest.plugin] if manifest.plugin else []):
            if base is not None and not (base / rel).exists():
                rep.findings.append(Finding("block", "publish.missing_file",
                                            "声明的文件不存在：%s" % rel, str(base)))
        for rel in manifest.tests:
            if base is not None and ("/" in rel or rel.endswith(".py") or rel.endswith(".json")):
                if not (base / rel).exists():
                    rep.findings.append(Finding("warn", "publish.missing_test",
                                                "tests 里声明的文件不存在：%s" % rel, str(base)))
        if fixture_path is not None and not Path(fixture_path).exists():
            rep.findings.append(Finding("block", "publish.missing_fixture",
                                        "缺少匿名 fixture（验收要求必须先过 fixture）",
                                        str(fixture_path)))
        if manifest.status == "draft":
            rep.findings.append(Finding("block", "publish.still_draft",
                                        "状态仍是 draft：必须走完 提交→审核 才能发布", "status"))

    rep.declared = {
        "capabilities": {t: caps.resolve_capability(t)[1] for t in manifest.allowed_capabilities},
        "data_scope": {t: caps.resolve_data_scope(t)[1] for t in manifest.data_scope},
        "gated_actions": gated,
    }
    rep.stats = {
        "body_chars": len(str(body or "")),
        "files_scanned": len(files or {}),
        "blocking": len(rep.blocking),
        "warnings": len(rep.warnings),
        "for_publish": bool(for_publish),
    }
    return rep


def scan_draft_dir(skill_dir: Path, *, for_publish: bool = False,
                   fixture_text: str = "") -> tuple[mf.SkillManifest, str, ScanReport]:
    """从磁盘上的技能目录直接扫（草稿目录 / 已提交目录 / 发布目录都能用）。"""
    d = Path(skill_dir)
    text = (d / "SKILL.md").read_text(encoding="utf-8", errors="replace")
    man, body = mf.from_document(text)
    files: dict[str, str] = {}
    for rel in [man.entry, man.plugin, "plugin.py", "scripts/ask.py"]:
        if not rel:
            continue
        p = d / rel
        if p.exists() and p.is_file() and rel not in files:
            files[rel] = p.read_text(encoding="utf-8", errors="replace")
    fx = fixture_text
    fx_path = (d / man.fixture) if man.fixture else None
    if not fx and fx_path is not None and fx_path.exists():
        fx = fx_path.read_text(encoding="utf-8", errors="replace")
    return man, body, scan(man, body, files=files, fixture_text=fx,
                           fixture_path=fx_path, skill_dir=d, for_publish=for_publish)
