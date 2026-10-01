# -*- coding: utf-8 -*-
"""SKILL-01 · Manifest 兼容扩展。

在现有 `SKILL.md` front-matter（`name/description/tags/source/updated`，以及
`capabilities.py` 认的 `title/category/icon/entry/trial/vendor`）之上，**新增**：

    version / status / inputs / outputs / allowed_capabilities / data_scope /
    tests / owner / plugin / fixture / session_id / review

**兼容性硬约束（踩过）**：`capabilities._parse_front_matter()` 与
`skills_local._parse()` 都是**逐行 split(':', 1)** 读的，它们不认 YAML 折叠块
（`description: >` 会读到字面量 `>`）。所以本模块：

1. 所有值都序列化成**单行**；列表用英文逗号内联（`inputs: knowledge_id, stage`）；
2. 解析时遇到不认识的行 → 丢进 `extra` 而不是报错（老技能文件照常能读）；
3. 序列化后**保证** `capabilities._parse_front_matter()` 能原样读回 name/description
   /title/category/icon/entry —— 有单元测试盯着这条（test_front_matter_roundtrip）。
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, replace

from edu_agent.skill_market import capability_registry as caps
from edu_agent.skill_market.paths import valid_id, valid_version

# 状态机里的合法状态（SKILL-05 的取值域；这里只做词法校验，流转规则在 lifecycle.py）
STATUSES: tuple[str, ...] = (
    "draft",        # 草稿：可编辑，Agent 绝不可加载
    "submitted",    # 已提交待审：冻结，Agent 绝不可加载
    "approved",     # 审核通过：等发布（仍未安装）
    "rejected",     # 审核驳回：退回草稿
    "published",    # 已发布：已安装到运行时技能目录
    "disabled",     # 已停用：从运行时目录撤下，但保留版本
    "rolled_back",  # 已回滚：回到上一个版本
)

# 必须存在的字段（缺一个就不允许提交审核）
REQUIRED_FIELDS: tuple[str, ...] = (
    "name", "description", "version", "status", "owner",
    "allowed_capabilities", "data_scope", "tests",
)

_LIST_FIELDS: tuple[str, ...] = (
    "inputs", "outputs", "allowed_capabilities", "data_scope", "tests", "tags",
)

# 值里不允许出现的字符（front-matter 单行 + 不给解析器添乱）
_BAD_VALUE = re.compile(r"[\r\n]")
_UNSAFE_PATH = re.compile(r"(^[/\\])|(^[A-Za-z]:)|(\.\.)")


def _as_list(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple, set)):
        items = [str(v) for v in value]
    else:
        items = str(value).split(",")
    out: list[str] = []
    for it in items:
        s = " ".join(it.split()).strip().strip('"').strip("'")
        if s:
            out.append(s)
    return tuple(out)


def _inline(items: tuple[str, ...]) -> str:
    return ", ".join(items)


@dataclass(frozen=True)
class SkillManifest:
    """一个 Skill 的 manifest。frozen：改状态用 dataclasses.replace 生成新对象。"""

    name: str                                   # 技能 id（= 目录名）
    description: str = ""
    version: str = "0.1.0"
    status: str = "draft"
    owner: str = ""
    title: str = ""
    category: str = "通用能力"
    icon: str = "S"
    entry: str = ""                             # 发布后平台调用入口（草稿期不生效）
    plugin: str = ""                            # SK 插件文件（**人工编写**，SKILL-02 不生成）
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    allowed_capabilities: tuple[str, ...] = ()
    data_scope: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()
    source: str = "session"                     # session | manual
    session_id: str = ""                        # 溯源（只进审计，不进提示词）
    fixture: str = ""                           # 匿名 fixture 相对路径
    review: str = ""                            # 审核意见 / 驳回原因
    tags: tuple[str, ...] = ()
    updated: str = ""
    extra: tuple[tuple[str, str], ...] = ()     # 未知字段原样保留（前向兼容）

    # ---------------------------------------------------------------- 校验
    def errors(self, *, strict_caps: bool = True) -> list[str]:
        """返回问题清单（空 = 可提交审核）。strict_caps=False 时只查词法不查白名单。"""
        errs: list[str] = []
        if not valid_id(self.name):
            errs.append("name 必须是 [a-z0-9][a-z0-9_-]{1,47}（当前 %r）" % self.name)
        if not str(self.description or "").strip():
            errs.append("description 不能为空（Skill 市场靠它做检索与展示）")
        if not valid_version(self.version):
            errs.append("version 必须是 x.y.z（当前 %r）" % self.version)
        if self.status not in STATUSES:
            errs.append("status 只能是 %s（当前 %r）" % ("/".join(STATUSES), self.status))
        if not str(self.owner or "").strip():
            errs.append("owner 不能为空（发布/回滚要有人负责）")
        if not self.allowed_capabilities:
            errs.append("allowed_capabilities 不能为空（禁止声明「隐式全权限」）")
        if not self.data_scope:
            errs.append("data_scope 不能为空（必须显式说明能碰哪些标识）")
        if not self.tests:
            errs.append("tests 不能为空（验收要求：必须有匿名 fixture 测试）")

        for f in ("entry", "plugin", "fixture"):
            v = str(getattr(self, f) or "")
            if v and _UNSAFE_PATH.search(v):
                errs.append("%s 必须是技能目录内的相对路径（当前 %r，禁止绝对路径/..）" % (f, v))

        for token in self.allowed_capabilities:
            if strict_caps:
                ok, why = caps.resolve_capability(token)
            else:
                ok, why = bool(token.strip()), ""
            if not ok:
                errs.append("allowed_capabilities: " + why)
        for token in self.data_scope:
            if strict_caps:
                ok, why = caps.resolve_data_scope(token)
            else:
                ok, why = bool(token.strip()), ""
            if not ok:
                errs.append("data_scope: " + why)

        for f in _LIST_FIELDS:
            for v in getattr(self, f):
                if _BAD_VALUE.search(v):
                    errs.append("%s 的值不能含换行（front-matter 按行解析）：%r" % (f, v))
        for f in ("name", "description", "version", "status", "owner", "title",
                  "category", "icon", "entry", "plugin", "fixture", "review",
                  "source", "session_id", "updated"):
            v = str(getattr(self, f) or "")
            if _BAD_VALUE.search(v):
                errs.append("%s 的值不能含换行：%r" % (f, v))
        return errs

    def ok(self, *, strict_caps: bool = True) -> bool:
        return not self.errors(strict_caps=strict_caps)

    # ---------------------------------------------------------------- 序列化
    def to_front_matter(self) -> str:
        """序列化成 front-matter（**全单行**，兼容 capabilities 的按行解析）。"""
        rows: list[tuple[str, str]] = [
            ("name", self.name),
            ("title", self.title or self.name),
            ("description", self.description),
            ("version", self.version),
            ("status", self.status),
            ("owner", self.owner),
            ("category", self.category),
            ("icon", self.icon),
        ]
        if self.entry:
            rows.append(("entry", self.entry))
        if self.plugin:
            rows.append(("plugin", self.plugin))
        for f in ("inputs", "outputs", "allowed_capabilities", "data_scope", "tests", "tags"):
            vals = getattr(self, f)
            if vals:
                rows.append((f, _inline(vals)))
        rows += [("source", self.source), ("updated", self.updated or _now())]
        if self.session_id:
            rows.append(("session_id", self.session_id))
        if self.fixture:
            rows.append(("fixture", self.fixture))
        if self.review:
            rows.append(("review", self.review))
        rows += [(k, v) for k, v in self.extra]
        body = "\n".join("%s: %s" % (k, v) for k, v in rows if str(v) != "")
        return "---\n" + body + "\n---\n"

    def to_document(self, body: str) -> str:
        """front-matter + 正文（正文就是给人和 Agent 读的 SKILL.md 主体）。"""
        text = str(body or "").rstrip() + "\n"
        return self.to_front_matter() + "\n" + text

    def to_dict(self) -> dict:
        return {
            "name": self.name, "title": self.title or self.name,
            "description": self.description, "version": self.version,
            "status": self.status, "owner": self.owner,
            "category": self.category, "icon": self.icon,
            "entry": self.entry, "plugin": self.plugin,
            "inputs": list(self.inputs), "outputs": list(self.outputs),
            "allowed_capabilities": list(self.allowed_capabilities),
            "data_scope": list(self.data_scope), "tests": list(self.tests),
            "source": self.source, "session_id": self.session_id,
            "fixture": self.fixture, "review": self.review,
            "tags": list(self.tags), "updated": self.updated,
        }

    def bumped(self, *, level: str = "patch", status: str | None = None,
               review: str | None = None) -> "SkillManifest":
        """出下一个版本号（发布时用）。level: patch | minor | major。"""
        try:
            a, b, c = (int(x) for x in self.version.split("."))
        except Exception:  # noqa: BLE001
            a, b, c = 0, 1, 0
        if level == "major":
            a, b, c = a + 1, 0, 0
        elif level == "minor":
            b, c = b + 1, 0
        else:
            c += 1
        return replace(self, version="%d.%d.%d" % (a, b, c),
                       status=status or self.status,
                       review=self.review if review is None else review,
                       updated=_now())


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M")


# ---------------------------------------------------------------- 解析
def parse_front_matter(text: str) -> dict[str, str]:
    """逐行解析 front-matter（与 capabilities/skills_local 同款规则）。"""
    lines = str(text or "").splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    meta: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip().lower()
        value = value.strip().strip('"').strip("'")
        if key:
            meta[key] = value
    return meta


def split_document(text: str) -> tuple[dict[str, str], str]:
    """拆成 (front-matter dict, 正文)。"""
    raw = str(text or "")
    if not raw.startswith("---"):
        return {}, raw
    end = raw.find("\n---", 3)
    if end < 0:
        return {}, raw
    meta = parse_front_matter(raw)
    body = raw[end + 4:]
    return meta, body.lstrip("\n")


def from_document(text: str) -> tuple[SkillManifest, str]:
    """SKILL.md 文本 → (manifest, 正文)。缺字段按默认值补，不抛异常。"""
    meta, body = split_document(text)
    known = {
        "name", "title", "description", "version", "status", "owner", "category",
        "icon", "entry", "plugin", "inputs", "outputs", "allowed_capabilities",
        "data_scope", "tests", "tags", "source", "updated", "session_id",
        "fixture", "review",
    }
    lists = {f: _as_list(meta.get(f)) for f in _LIST_FIELDS}
    extra = tuple((k, v) for k, v in meta.items() if k not in known)
    m = SkillManifest(
        name=(meta.get("name") or "").strip(),
        description=(meta.get("description") or "").strip(),
        version=(meta.get("version") or "0.1.0").strip(),
        status=(meta.get("status") or "draft").strip(),
        owner=(meta.get("owner") or "").strip(),
        title=(meta.get("title") or "").strip(),
        category=(meta.get("category") or "通用能力").strip(),
        icon=(meta.get("icon") or "S").strip()[:2],
        entry=(meta.get("entry") or "").strip(),
        plugin=(meta.get("plugin") or "").strip(),
        inputs=lists["inputs"], outputs=lists["outputs"],
        allowed_capabilities=lists["allowed_capabilities"],
        data_scope=lists["data_scope"], tests=lists["tests"], tags=lists["tags"],
        source=(meta.get("source") or "session").strip(),
        session_id=(meta.get("session_id") or "").strip(),
        fixture=(meta.get("fixture") or "").strip(),
        review=(meta.get("review") or "").strip(),
        updated=(meta.get("updated") or "").strip(),
        extra=extra,
    )
    return m, body
