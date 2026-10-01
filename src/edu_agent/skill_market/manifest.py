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
    # ---- 结构化字段（YAML 块状 front-matter 用；手写草稿常这么写）----
    # inputs_spec: [{"name": "book_id", "type": "string", "description": "..."}]
    # inline_tests: [{"input": {...}, "expect_contains": "..."}] → 会转成 fixture 断言
    inputs_spec: tuple[dict, ...] = ()
    inline_tests: tuple[dict, ...] = ()
    source: str = "session"                     # session | manual | hand-written
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
        """序列化成 front-matter。

        * **简单字段全单行**（capabilities 的按行解析器要能读回 name/description/title/entry）；
        * 结构化字段（`inputs_spec` / `inline_tests`）写成 YAML 块 —— 手写草稿的
          `inputs:`（含 type/description）与 `tests:`（含 input/expect_contains）不会丢。
        """
        y = _yaml_module()
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

        blocks: list[str] = []
        if self.inputs_spec and y is not None:
            blocks.append("inputs:\n" + y.safe_dump(
                [dict(d) for d in self.inputs_spec], allow_unicode=True,
                sort_keys=False, default_flow_style=False).rstrip())
        elif self.inputs:
            rows.append(("inputs", _inline(self.inputs)))

        for f in ("outputs", "allowed_capabilities", "data_scope", "tags"):
            vals = getattr(self, f)
            if vals:
                rows.append((f, _inline(vals)))

        if self.inline_tests and y is not None:
            blocks.append("tests:\n" + y.safe_dump(
                [dict(d) for d in self.inline_tests], allow_unicode=True,
                sort_keys=False, default_flow_style=False).rstrip())
        elif self.tests:
            rows.append(("tests", _inline(self.tests)))

        rows += [("source", self.source), ("updated", self.updated or _now())]
        if self.session_id:
            rows.append(("session_id", self.session_id))
        if self.fixture:
            rows.append(("fixture", self.fixture))
        if self.review:
            rows.append(("review", self.review))
        rows += [(k, v) for k, v in self.extra]
        body = "\n".join("%s: %s" % (k, v) for k, v in rows if str(v) != "")
        if blocks:
            body += "\n" + "\n".join(blocks)
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
            "inputs_spec": [dict(d) for d in self.inputs_spec],
            "inline_tests": [dict(d) for d in self.inline_tests],
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


# ---------------------------------------------------------------- YAML 支持
def _yaml_module():
    """PyYAML 可选依赖：装了就支持块状 front-matter，没装就退回逐行解析。"""
    try:
        import yaml

        return yaml
    except Exception:  # noqa: BLE001
        return None


def parse_front_matter(text: str) -> dict[str, str]:
    """逐行解析 front-matter（与 capabilities/skills_local 同款规则）。

    只认单行 `key: value`；块状结构（`inputs:` 下面是 `- name: x`）由
    `parse_front_matter_full()` 用 YAML 解析。两者并存：
    **简单字段永远保持单行**，所以平台的按行解析器始终能读回 name/description/title/entry。
    """
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


def _front_matter_text(text: str) -> str:
    raw = str(text or "")
    if not raw.startswith("---"):
        return ""
    end = raw.find("\n---", 3)
    return raw[3:end] if end > 0 else ""


def parse_front_matter_full(text: str) -> dict:
    """完整解析（含块状结构）。YAML 不可用或解析失败时回落到逐行结果。"""
    fm = _front_matter_text(text)
    y = _yaml_module()
    if y is not None and fm.strip():
        try:
            data = y.safe_load(fm)
            if isinstance(data, dict):
                return {str(k).lower(): v for k, v in data.items()}
        except Exception:  # noqa: BLE001
            pass
    return parse_front_matter(text)


def split_document(text: str) -> tuple[dict[str, str], str]:
    """拆成 (front-matter dict, 正文)。逐行口径（简单字段）。"""
    raw = str(text or "")
    if not raw.startswith("---"):
        return {}, raw
    end = raw.find("\n---", 3)
    if end < 0:
        return {}, raw
    meta = parse_front_matter(raw)
    body = raw[end + 4:]
    return meta, body.lstrip("\n")


def _as_dict_tuple(value: object) -> tuple[dict, ...]:
    """把 YAML 里的「字典列表」规范化成 tuple[dict]。"""
    if not isinstance(value, (list, tuple)):
        return ()
    out: list[dict] = []
    for item in value:
        if isinstance(item, dict):
            out.append({str(k): v for k, v in item.items()})
    return tuple(out)


def from_document(text: str) -> tuple[SkillManifest, str]:
    """SKILL.md 文本 → (manifest, 正文)。

    **两种 front-matter 都认**：
    * 逐行 `key: value`（平台既有风格，capabilities 也按行读）；
    * YAML 块状（手写草稿常用，`inputs:` 下面是 `- name: ...`，`tests:` 是字典列表）。
    结构化字段落进 `inputs_spec` / `inline_tests`，简单字段照旧。
    """
    raw = str(text or "")
    body = raw[raw.find("\n---", 3) + 4:].lstrip("\n") if raw.startswith("---") and \
        raw.find("\n---", 3) > 0 else raw
    meta = parse_front_matter_full(raw)
    known = {
        "name", "title", "description", "version", "status", "owner", "category",
        "icon", "entry", "plugin", "inputs", "outputs", "allowed_capabilities",
        "data_scope", "tests", "tags", "source", "updated", "session_id",
        "fixture", "review",
    }

    inputs_spec = _as_dict_tuple(meta.get("inputs"))
    inline_tests = _as_dict_tuple(meta.get("tests"))
    # 简单字段仍取「字符串列表」口径
    def _list_of_str(key: str) -> tuple[str, ...]:
        v = meta.get(key)
        if isinstance(v, (list, tuple)):
            if all(isinstance(x, dict) for x in v) and v:
                return ()
            return _as_list([x for x in v if not isinstance(x, dict)])
        return _as_list(v)

    inputs = _list_of_str("inputs")
    if not inputs and inputs_spec:
        inputs = tuple(str(d.get("name")) for d in inputs_spec if d.get("name"))
    tests = _list_of_str("tests")
    if not tests and inline_tests:
        tests = ("inline-tests(%d)" % len(inline_tests),)

    extra = tuple((str(k), str(v)) for k, v in meta.items()
                  if k not in known and not isinstance(v, (dict, list)))

    # 手写 SKILL.md 常把技能名写在 H1 标题里（front-matter 只有 version/status/inputs…）。
    # 这里兜一下：没有 name 就用 H1 标题生成合法 id（平台 discover_skills 也是这个思路）。
    name = str(meta.get("name") or "").strip()
    title = str(meta.get("title") or "").strip()
    if not name or not title:
        h1 = re.search(r"^#\s+(.+)$", body, re.M)
        if h1:
            head = h1.group(1).strip()
            if not title:
                title = head
            if not name:
                name = _slugify(head)

    m = SkillManifest(
        name=name,
        description=str(meta.get("description") or "").strip(),
        version=str(meta.get("version") or "0.1.0").strip(),
        status=str(meta.get("status") or "draft").strip(),
        owner=str(meta.get("owner") or "").strip(),
        title=title,
        category=str(meta.get("category") or "通用能力").strip(),
        icon=str(meta.get("icon") or "S").strip()[:2],
        entry=str(meta.get("entry") or "").strip(),
        plugin=str(meta.get("plugin") or "").strip(),
        inputs=inputs, outputs=_list_of_str("outputs"),
        allowed_capabilities=_list_of_str("allowed_capabilities"),
        data_scope=_list_of_str("data_scope"), tests=tests, tags=_list_of_str("tags"),
        inputs_spec=inputs_spec, inline_tests=inline_tests,
        source=str(meta.get("source") or "session").strip(),
        session_id=str(meta.get("session_id") or "").strip(),
        fixture=str(meta.get("fixture") or "").strip(),
        review=str(meta.get("review") or "").strip(),
        updated=str(meta.get("updated") or "").strip(),
        extra=extra,
    )
    return m, body


def _slugify(text: str) -> str:
    """标题 → 合法技能 id（ASCII 化；含中文则退化成 skill-<hash>）。"""
    raw = " ".join(str(text or "").split())
    if raw and re.fullmatch(r"[A-Za-z0-9 _.-]+", raw):
        s = re.sub(r"[^a-zA-Z0-9]+", "_", raw).strip("_").lower()[:48]
        if re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,47}", s):
            return s
    import hashlib

    return "skill-" + hashlib.sha1((raw or "skill").encode("utf-8")).hexdigest()[:10]
