# 模块 3 · Skill 市场与 Agent 自动封装（交付文档）

> 负责人：E（Agent/Skill） · 协作：G（安全/运维）、B（接口与审计）、F（市场 UI）
> 代码：`src/edu_agent/skill_market/`（13 个文件） · 测试：`tests/test_skill_market.py`（53 个用例，全绿）
> 接口前缀：`/api/skill-market`（已挂进 `web_server.py`，5174 实测通过）

---

## 1. 模块 3 整体目标、输入输出、官方验收标准

### 1.1 一句话目标

**把"一次成功会话"变成"一个可审核、可发布、可回滚、且默认谁都装不上的 Skill"。**

三个"不"是硬指标，不是口号：**默认不安装、不共享、不联网**。代码层面的保证方式是：

* 草稿、待审、已审核三种状态的技能**只存在于市场区**（`<data_dir>/skill_market/`），
  而平台只会从**运行时技能目录**（`skills_local.skills_dir()`）自动发现技能；
* 只有 `lifecycle.publish()` 一个函数会把文件**拷贝**进运行时目录，它必须先过
  `permissions.guard("skill_publish")` 闸门；
* 加载器（`isolation.assert_loadable()`）还会二次核对"这个技能是否已安装到运行时"，
  所以就算有人手工把草稿文件挪过去，加载也会被拒。

### 1.2 输入 / 输出

| | 内容 |
|---|---|
| **输入** | `session_id`（或显式 Skill 草稿）+ manifest + 匿名 fixture |
| **输出** | 可审查的 `SKILL.md` + 测试结果 + 权限扫描结果 + 审计记录 |
| **不做** | 不生成可运行的 py 插件代码（人工编写）；不落题库；不碰学情明细；不做前端页面 |

### 1.3 官方验收标准（本模块的对照实现）

> 把一次"按图谱节点生成题目草稿"的成功会话封装为 Skill；通过匿名 fixture 和权限检查后才能提交团队审核，默认不安装、不共享、不联网。

| 验收点 | 实现 | 测试 |
|---|---|---|
| 从成功会话封装 | `draft.create_draft_from_session()` | `TestAcceptanceGraphNodeSkill` |
| 匿名 fixture | `fixture.validate_fixture()` / `run_fixture()` | `test_fixture_is_anonymous` |
| 权限检查后才可提交 | `lifecycle.submit()` 内先跑 `perm_scan` + 查 fixture | `test_submit_requires_fixture` |
| 默认不安装 | 草稿在 `drafts/`，运行时目录没有它 | `test_full_state_machine`（draft 阶段 `is_loadable=False`） |
| 默认不共享 | 会话级 plugin 集合，必须显式 bind | `TestIsolation`（跨会话拿不到） |
| 默认不联网 | 能力令牌白名单里没有网络令牌就发不出去 | `test_undeclared_network_blocked` |

### 1.4 目录布局（隔离边界的物理形态）

```
<data_dir>/skill_market/
    drafts/<id>/SKILL.md            草稿（可编辑，Agent 永远看不到）
    submitted/<id>/SKILL.md         已提交待审（冻结）
    published/<id>/<version>/       已发布的历史版本（不可变，回滚来源）
    archive/<id>/<version>-<原因>-<时间>/   停用/回滚/删除的留档（不物理删）
    audit/<yyyy-mm>.jsonl           追加式审计日志（只增不改不删）
    fixtures/<id>.json              匿名 fixture
    sessions/<sid>.json             会话 → plugin 集合（隔离关系）
    state.json                      权威生命周期状态（版本目录要保持不可变，状态放这里）

<data_dir>/skills/<id>/             ★ 运行时目录：只有发布过的技能会出现在这里
```

---

## 2. SKILL-01 ~ SKILL-06：实现思路、数据结构、核心代码

### SKILL-01 · Manifest 兼容扩展

**思路**：在现有 front-matter 上**加字段**，不换格式。

⚠️ 关键兼容约束（踩过）：`capabilities._parse_front_matter()` 与 `skills_local._parse()`
都是**逐行 `split(':', 1)`** 解析，它们不认 YAML 折叠块（`description: >` 会读到字面量 `>`）。
所以本模块所有值都序列化成**单行**，列表用英文逗号内联。

**数据结构**：`manifest.SkillManifest`（frozen dataclass）

```python
@dataclass(frozen=True)
class SkillManifest:
    name: str                    # 技能 id（= 目录名，[a-z0-9][a-z0-9_-]{1,47}）
    description: str = ""
    version: str = "0.1.0"       # x.y.z
    status: str = "draft"        # draft/submitted/approved/rejected/published/disabled/rolled_back
    owner: str = ""              # 负责人（发布/回滚要有人负责）
    title: str = ""; category: str = "通用能力"; icon: str = "S"
    entry: str = ""              # 发布后平台调用入口（草稿期不生效）
    plugin: str = ""             # SK 插件文件（**人工编写**）
    inputs: tuple[str, ...] = ()          # 需要哪些输入
    outputs: tuple[str, ...] = ()         # 产出什么
    allowed_capabilities: tuple[str, ...] = ()   # 只能声明白名单里的能力
    data_scope: tuple[str, ...] = ()      # 能碰哪些标识（禁学生隐私）
    tests: tuple[str, ...] = ()           # 验收要求：至少一个匿名 fixture 用例
    source: str = "session"               # session | manual
    session_id: str = ""                  # 溯源（只进审计，不进提示词）
    fixture: str = ""; review: str = ""; tags: tuple[str, ...] = ()
    extra: tuple[tuple[str, str], ...] = ()   # 未知字段原样保留（前向兼容）
```

核心校验（`errors()`，`strict_caps=True` 时逐令牌查白名单）：

```python
def errors(self, *, strict_caps: bool = True) -> list[str]:
    errs = []
    if not valid_id(self.name):            errs.append("name 必须是 [a-z0-9][a-z0-9_-]{1,47}")
    if not self.description.strip():       errs.append("description 不能为空")
    if not valid_version(self.version):    errs.append("version 必须是 x.y.z")
    if self.status not in STATUSES:        errs.append("status 非法")
    if not self.owner.strip():             errs.append("owner 不能为空")
    if not self.allowed_capabilities:      errs.append("allowed_capabilities 不能为空（禁止隐式全权限）")
    if not self.data_scope:                errs.append("data_scope 不能为空")
    if not self.tests:                     errs.append("tests 不能为空（必须有匿名 fixture 测试）")
    for f in ("entry", "plugin", "fixture"):     # 防目录穿越
        if _UNSAFE_PATH.search(getattr(self, f)): errs.append("%s 必须是技能目录内的相对路径" % f)
    for t in self.allowed_capabilities:          # 令牌必须在白名单里
        ok, why = caps.resolve_capability(t) if strict_caps else (bool(t), "")
        if not ok: errs.append("allowed_capabilities: " + why)
    for t in self.data_scope:
        ok, why = caps.resolve_data_scope(t) if strict_caps else (bool(t), "")
        if not ok: errs.append("data_scope: " + why)
    return errs
```

### SKILL-02 · 自动封装草稿

见第 6 节（单独展开）。

### SKILL-03 · 静态权限扫描

见第 5.1 节。

### SKILL-04 · 沙箱与审计

见第 5.2 节。

### SKILL-05 · 审核 / 发布 / 回滚

见第 4 节状态机。

### SKILL-06 · 市场页面（数据面）

**本模块不实现 UI**（交给 F）。提供的数据端点（`api.py`）：

| 端点 | 用途 |
|---|---|
| `GET /api/skill-market/index?q=` | 搜索/列表：四区合并 + 状态 + 安装状态 + 版本数 + fixture 有无 + `loadable` |
| `GET /api/skill-market/detail?id=` | 详情：权限说明（每个令牌的人话解释）、安装状态、版本列表、审计尾、**静态 plugin 函数表** |
| `GET /api/skill-market/registry` | 词表：能力令牌 / 数据范围 / 禁止项 / 状态枚举 / 状态机转移表 |
| `POST /api/skill-market/disable` | 停用（页面上的"停用提示"对应这里） |

`detail` 里"权限说明"直接由 `capability_registry` 生成，前端不需要硬编码文案：

```json
"permissions": {
  "allowed_capabilities": {"kg.read": "按 knowledge_id 读知识图谱节点（模块2 对外 API）"},
  "data_scope": {"knowledge_id": "知识点/图谱节点标识"},
  "gated_actions": [{"action": "export_pptx", "policy": "ask", "label": "导出教案为 PPT"}]
}
```

---

## 3. SKILL.md manifest 完整样例

真实文件：`<data_dir>/skill_market/drafts/graph-node-question-draft/SKILL.md`

```markdown
---
name: graph-node-question-draft
title: 图谱节点生成题目草稿
description: 按知识图谱节点生成题目草稿（含答案与解析、难度标注），只产草稿不入库
version: 0.1.0
status: draft
owner: E
category: 教学
icon: G
entry: plugin.py
plugin: plugin.py
inputs: knowledge_id, teaching_context, difficulty
outputs: question_draft, answer_outline, knowledge_id
allowed_capabilities: kg.read, question.draft
data_scope: knowledge_id, teaching_context
tests: fixture.json
source: session
updated: 2026-10-01 12:40
session_id: 00b2f49b5250
fixture: fixture.json
review: 权限扫描通过
tags: session-draft, 待人工编辑
---

# 图谱节点生成题目草稿

> **这是模块 3（SKILL-02）自动生成的草稿，尚未审核，Agent 不会加载它。**
...
```

字段说明：

| 字段 | 必填 | 含义 | 校验 |
|---|---|---|---|
| `name` | ✔ | 技能 id = 目录名 | `^[a-z0-9][a-z0-9_-]{1,47}$` |
| `description` | ✔ | 市场检索与展示用 | 非空、单行 |
| `version` | ✔ | 语义化版本 | `x.y.z`，发布时自动 bump |
| `status` | ✔ | 状态机当前态 | 枚举内 |
| `owner` | ✔ | 负责人 | 非空；**审核人不得等于 owner**（职责分离） |
| `inputs` / `outputs` | | 输入/产出槽位 | 单行逗号分隔 |
| `allowed_capabilities` | ✔ | 允许的能力令牌 | 必须能在白名单解析到真实实体 |
| `data_scope` | ✔ | 允许接触的数据标识 | 命中禁令牌直接拒 |
| `tests` | ✔ | 用例（至少匿名 fixture） | 非空；发布前查文件是否存在 |
| `entry` / `plugin` | | 平台入口脚本 / SK 插件 | 相对路径，禁 `..` 与绝对路径 |
| `session_id` / `fixture` / `review` | | 溯源、匿名 fixture、审核意见 | 只进审计，不进提示词 |

---

## 4. Skill 状态机完整逻辑

```python
TRANSITIONS = {
    ("draft",     "submit"):   "submitted",
    ("submitted", "approve"):  "approved",
    ("submitted", "reject"):   "draft",
    ("approved",  "publish"):  "published",
    ("published", "disable"):  "disabled",
    ("disabled",  "publish"):  "published",      # 重新启用
    ("published", "rollback"): "rolled_back",
    ("rolled_back", "publish"):"published",
}
```

```
                  submit            approve            publish(过闸门)
   draft ──────────────▶ submitted ──────────▶ approved ─────────────▶ published
     ▲                       │                                          │  ▲
     └────── reject ─────────┘                          disable ◀────────┘  │ publish
                                                            │              │ (重新启用)
                                                            ▼              │
                                                        disabled ──────────┘
                                                            │
                                              published ──rollback──▶ rolled_back ──▶ published
```

### 每个动作的闸门

| 动作 | 前置条件 | 权限闸门 | 产物 |
|---|---|---|---|
| `submit` | 状态=draft ∧ manifest 合法 ∧ **有匿名 fixture** ∧ 静态扫描 pass | 无（进队列无副作用） | 目录 `drafts/→submitted/`，审计 `skill.submit` |
| `approve` | 状态=submitted ∧ **审核人 ≠ owner** ∧ `for_publish=True` 扫描 pass | 无 | 状态 approved，审计 `skill.approve` |
| `reject` | 状态=submitted ∧ **必须写理由** | 无 | 退回 draft，理由写进 front-matter + 审计 |
| `publish` | 状态=approved（或 disabled/rolled_back 重新启用） | **`skill_publish`**（默认 ask） | 版本快照 + 装进运行时，审计 `skill.publish` |
| `disable` | 状态=published | **`skill_disable`** | 运行时目录归档，审计 `skill.disable` |
| `rollback` | 状态=published ∧ 有更早版本 | **`skill_rollback`** | 换回旧版本，审计 `skill.rollback` |
| `new_draft` | 状态∈{published,disabled,rolled_back} | 无（不碰运行时） | 派生新草稿继续改，**线上老版本照常服务** |

核心实现（`lifecycle.py`）：

```python
def publish(skill_id, actor="E", *, confirm=False, level="patch", session_id=""):
    ok, cur, nxt = _check_transition(skill_id, "publish")
    if not ok:
        return _fail("publish", skill_id, actor, "状态 %s 不能发布（必须先 approve）" % cur, frm=cur)
    gated = _gate("publish", skill_id, actor, confirm, cur)   # permissions.guard("skill_publish")
    if gated:
        return gated                                          # {ok:False, gate:'permission', policy:'ask', need:'confirm'}
    got = store.read_area("submitted", skill_id)
    man, body, src = got
    rep = perm_scan.scan_draft_dir(src, for_publish=True)[2]   # 发布前再扫一遍
    if not rep.ok:
        return _fail("publish", skill_id, actor, rep.review_note(), frm=cur)
    man = man.bumped(level=level, status="published")          # 版本号自动 +1
    vdir = store.write_version(man, body, src)                 # published/<id>/<v>/ 不可变快照
    installed = store.install_to_runtime(man, body, vdir)       # ★ 唯一让 Agent 能看到它的动作
    store.archive_area(skill_id, "submitted", reason="published")
    store.write_state(skill_id, status="published", version=man.version,
                      actor=actor, installed=True, dir=str(installed))
    _log("skill.publish", ...)                                 # 审计（成功也写、失败也写）
    return {"ok": True, "status": "published", "version": man.version, "installed_to": str(installed)}
```

**"草稿绝不可被调用"的三重保险**：

1. 草稿在 `skill_market/drafts/`，而 `capabilities.discover_skills()` / `skills_local.list_skills()`
   只扫运行时目录 → 平台根本看不见它；
2. `store.is_loadable()` 要求 `state.installed == True`（草稿永远是 False）；
3. `isolation.assert_loadable()` 再要求"该技能绑定到了这个会话"，而 `bind()` 只接受可加载的技能。

---

## 5. 静态权限扫描 + 沙箱审计

### 5.1 SKILL-03 静态权限扫描（`perm_scan.py`）

四层扫描，逐层给出人话结论（进审核意见）：

| 层 | 查什么 | 不通过 |
|---|---|---|
| 1 Manifest 层 | 每个 `allowed_capabilities` 令牌必须在白名单解析到**真实实体**；`data_scope` 不许含隐私令牌 | block |
| 2 文本层 | 正文 + `plugin.py`/`entry` 脚本里的**网络 / 写文件 / 执行外部程序 / 破坏性**意图 | 未声明 → block；已声明 → warn |
| 3 越界层 | `sqlite3`、`chromadb`、`SELECT …`、`from edu_agent.knowledge import …` | block |
| 4 语义层 | 正文提到"学情/掌握度"→ 必须声明 `mastery_snapshot_id`；提到"图谱/节点"→ 必须 `knowledge_id`；提到学段 → 必须 `teaching_context`/`stage` | block |

```python
RISK_REQUIRES = {
    "network":     ("action:vision_analyze", "skill:web-image"),
    "file_write":  ("action:export_docx", "action:export_pptx", "action:project_new",
                    "action:template_save", "action:pdf_ingest", "action:pdf_library_manage",
                    "action:file_open"),
    "exec":        ("action:file_open",),
    "destructive": ("action:template_delete",),
}

RISK_PATTERNS = {
    "network": [("net.url", re.compile(r"https?://|ftp://", re.I), "出现网络地址"),
                ("net.lib", re.compile(r"\b(requests|urllib|httpx|aiohttp|socket|curl|wget)\b", re.I), "出现网络库/命令"),
                ("net.api", re.compile(r"(api[_-]?key|bearer\s+[a-z0-9]|openai|deepseek)", re.I), "出现外部 API/密钥")],
    "file_write": [("fs.write", re.compile(r"(write_text|open\s*\([^)]*['\"][wa]|shutil\.|os\.remove|unlink|rmtree)"), "出现写/删文件")],
    "exec": [("exec.proc", re.compile(r"(subprocess|os\.system|Popen|shell\s*=\s*True|powershell|cmd\.exe)", re.I), "出现外部程序执行"),
             ("exec.dyn", re.compile(r"\b(eval|exec)\s*\(|__import__|importlib", re.I), "出现动态代码执行")],
    "destructive": [("destroy.delete", re.compile(r"(drop\s+table|delete\s+from|truncate)", re.I), "破坏性数据操作")],
}
```

扫描结果结构（`ScanReport.to_dict()`）：

```json
{"ok": true, "verdict": "pass", "review_note": "权限扫描通过",
 "blocking": [], "warnings": [{"level":"warn","code":"net.url.declared","message":"出现网络地址（已声明对应能力，放行）","where":"SKILL.md 正文"}],
 "declared": {"capabilities": {"kg.read": "..."}, "data_scope": {"knowledge_id": "..."},
              "gated_actions": [{"action":"export_pptx","policy":"ask","label":"导出教案为 PPT"}]},
 "stats": {"body_chars": 1580, "files_scanned": 1, "blocking": 0, "warnings": 1, "for_publish": false}}
```

> 实现细节（踩过的坑）：语义层加了**否定语境守卫** —— 技能文档里常写"**不**接触学情"
> "**禁止**读取学情"，这不是"要用学情数据"。守卫看命中词前 8 个字符里有没有
> `不/禁止/勿/无需/避免/免/不得/别`，有就跳过该次命中。

### 5.2 SKILL-04 沙箱审计（`audit.py`）

**三条硬规矩写在代码里**：只追加（无 update/delete API）、全程脱敏、哈希链可校验。

数据结构（`audit/<yyyy-mm>.jsonl`，每行一条）：

```json
{
  "seq": 7,
  "ts": "2026-10-01T13:02:11+0800",
  "event": "skill.publish",
  "skill": "skill-4c6e80a26a",
  "version": "0.1.1",
  "actor": "E",
  "session_id": "00b2f49b5250",
  "status": {"from": "approved", "to": "published"},
  "input": {"chars": 41, "digest": "sha256:9a1c…(len=41)", "preview": "按知识图谱节点生成题目草稿…"},
  "result": {"ok": true, "code": "published", "installed_to": "…\\data\\skills\\skill-4c6e80a26a"},
  "gates": {"permission": "allow", "perm_scan": "pass"},
  "prev_hash": "3f2b…",
  "hash": "8c01…"
}
```

脱敏规则（`redact()`）：

* 键名命中 `student|pupil|phone|mobile|email|id_card|address|raw|answer|transcript|homework|score|password|secret|token|api_key`
  → 值**只留** `{"redacted": true, "digest": "sha256:…(len=n)", "chars": n}`，原文不落盘；
* 自由文本：手机号 → `1**********`、邮箱 → `***@***`、身份证 → `******************`、
  `姓名：张三` → `姓名:***`、`学号：20230101` → `学号:***`；
* 超长文本截断并附摘要（可对账、不可还原）。

```python
def append(event, *, skill="", version="", actor="", session_id="",
           status_from="", status_to="", input_text="", result=None, gates=None, extra=None) -> dict:
    path = _month_file()
    with _lock:
        rec = {"seq": _next_seq(path), "ts": ..., "event": event, "skill": skill, ...,
               "input": summarize(input_text) if input_text else None,
               "result": redact(result or {}), "gates": redact(gates or {}),
               "prev_hash": _last_hash(path)}
        rec["hash"] = hashlib.sha256((rec["prev_hash"] + _canonical(rec)).encode()).hexdigest()
        with path.open("a", encoding="utf-8") as fh:      # 只追加
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush(); os.fsync(fh.fileno())
        return rec
```

`verify_chain()` 逐行重算哈希，手改一行就会报 `hash 不匹配（记录被修改）` / `prev_hash 断链`
（测试 `test_tamper_detected` 就干这件事）。

---

## 6. SKILL-02：从 `session_id` 生成 Skill 草稿

**流程**：读会话 → 挑"成功轮次" → 抽步骤/输入/输出/标识 → 生成匿名 fixture → 写 SKILL.md 草稿。

**产出物只有两个**：`SKILL.md` + `fixture.json`。**不生成任何可运行的 .py** —— 这条由
`store.write_draft()` 强制执行（默认 `allow_code=False`，`.py/.cmd/.ps1/.sh/.exe` 一律拒），
插件代码必须由人工依据 `templates/plugin_template.py` 编写，再走人工编辑接口提交。

```python
def create_draft_from_session(session_id, *, name_hint="", owner="E", description="",
                              allowed_capabilities=("kg.read", "question.draft"),
                              data_scope=("knowledge_id", "teaching_context"),
                              session_reader=None, write=True) -> dict:
    session = (session_reader or default_session_reader)(session_id)
    ex = extract_from_session(session, name_hint=name_hint, owner=owner)   # 纯函数，可单测
    if not ex.get("ok"):
        return {"ok": False, "error": ex["error"]}
    man = mf.SkillManifest(name=ex["skill_id"], title=ex["title"], version="0.1.0",
                           status="draft", owner=owner, entry="", plugin="",   # ← 人工填
                           inputs=tuple(ex["inputs"]), outputs=tuple(ex["outputs"]),
                           allowed_capabilities=tuple(allowed_capabilities),
                           data_scope=tuple(data_scope), tests=("fixture.json",),
                           source="session", session_id=session_id, fixture="fixture.json",
                           tags=("session-draft", "待人工编辑"))
    body = build_body(ex, allowed=man.allowed_capabilities, data_scope=man.data_scope)
    d = store.write_draft(man, body, files={"fixture.json": json.dumps(ex["fixture"], ...)})
    ...
```

**成功轮次的判据**（`is_successful()`）：助手答复 ≥40 字 ∧ 前 400 字里没有
`失败/报错/Traceback/无法完成/超时` ∧ 有结构化产出（步骤/解析/题目/示例/`##`/有序列表）。

**抽取**：

* 步骤：有序列表 → 二级标题 → "首先/然后/接着/最后" 连接词（最多 12 条）；
* 输入：`knowledge_id` / `teaching_context` / `mastery_snapshot_id` / `difficulty` /
  `question_count` / `question_type` 关键词命中；
* 标识：正则捞 `K1024`、`node-xxx`、`kp-xxx` 这类节点 id；
* 匿名 fixture：只保留标识与结构化槽位 + `assertions`（断言可执行，发布前要能过）。

**匿名化**（`anonymize()`）：手机号 / 邮箱 / 身份证 / `姓名：张三` / `学号：20230101` → 占位符。

> 踩坑记录：姓名正则一开始把冒号写成可选，结果"给高中**学生生成**一道题"里的"学生生成"
> 被当成姓名擦掉了。现在强制要求冒号。

**草稿正文里带一份"待人工编辑"清单**（这是"人工编辑后才能加载"的落地方式）：

```markdown
## 待人工编辑（提交审核前必须逐条完成）

- [ ] 校对上面的步骤与输入，删掉会话里偶发的、不可复用的部分
- [ ] 确认 allowed_capabilities：多了要去掉（最小权限），少了审核会拒
- [ ] 依据 skill_market/templates/plugin_template.py 人工编写 plugin.py
      （Semantic Kernel @kernel_function），并在 front-matter 里填 plugin: plugin.py
- [ ] 补 tests/ 下的用例文件（tests: 字段要指向真实存在的文件）
- [ ] 用匿名 fixture 跑一遍：POST /api/skill-market/fixture/run
- [ ] 跑静态权限扫描：POST /api/skill-market/scan，确认 verdict=pass
- [ ] 然后才提交审核：POST /api/skill-market/submit
```

---

## 7. 复用已有组件（不重写本地 Skill 存储）

| 已有组件 | 模块 3 怎么用 | 说明 |
|---|---|---|
| `skills_local.py` | `paths.runtime_skills_dir()` 直接返回 `skills_local.skills_dir()`；`list_skills()` 仍是运行时清单的唯一读法 | **没有**另起一套"运行时技能目录" |
| `/api/skill/save`、`/api/skills/local` | 保持不变（agent 自建技能的老路径）；模块 3 新增 `/api/skill-market/*` 走市场四区 | 两套并存、互不干扰 |
| `capabilities.py` | ① `all_skill_specs()`/`MCP_SPECS` 生成能力令牌白名单 `skill:<id>`/`mcp:<id>`；② `is_enabled("skill", id)` 作为"可加载"的第三道条件 | 白名单是**现算**的，平台新增技能市场自动跟上 |
| `permissions.py` | 新增 3 个动作 `skill_publish` / `skill_disable` / `skill_rollback`（默认 `ask`），发布/停用/回滚前调 `permissions.guard(...)` | 复用三态策略与 `confirm` 语义，前端弹窗后带 `confirm=true` 重试 |
| `host/store.py`（会话） | SKILL-02 只**读**会话（`SessionStore.get`），不写、不另存 | 会话是草稿来源，不是模块 3 的存储 |
| `web_server.py` | `app.include_router(skill_market_router)`（try/except 包裹，挂载失败不拖垮网关） | 5174 实测 200 |

高风险动作的闸门调用（`lifecycle._gate()`）：

```python
ACTION_GATE = {"submit": None, "approve": None, "reject": None,
               "publish": "skill_publish", "disable": "skill_disable", "rollback": "skill_rollback"}

def _gate(action, skill_id, actor, confirm, cur):
    aid = ACTION_GATE.get(action)
    if not aid:
        return None
    blocked = permissions.guard(aid, confirm=bool(confirm))     # allow → None；ask/deny → 闸门信息
    if blocked:
        _log("skill." + action + ".blocked", ...)               # 被拦也要进审计
        return blocked
    return None
```

---

## 8. 跨模块交互：只传标识

**唯一契约对象**：`contract.TeachingContext`（`knowledge_id` / `teaching_context` /
`mastery_snapshot_id` / `stage` / `subject` / `class_id` / `lesson_id`）。

**唯一通道**：`contract.ModuleClient`（只有 HTTP，默认 stdlib `urllib`，可注入假传输做测试）。

```python
FORBIDDEN_PAYLOAD_KEYS = ("student_answer", "raw_answer", "answers", "answer_text", "answer_sheet",
                          "student_name", "student_id", "pupil", "real_name", "transcript",
                          "phone", "mobile", "email", "id_card", "idcard", "address",
                          "wrong_answers", "score_detail", "homework_photo")
```

出站前 `guard_payload()` 双重校验：**禁令牌直接拒**、**白名单外的键也拒**（比黑名单可靠）。

> 一个刻意的区分：题目的 `answer`（正确答案）是**合法内容**，学生作答必须叫
> `student_answer`/`raw_answer`。测试 `test_question_answer_is_allowed` 锁住这条。

模块 2 / 模块 4 的接线（环境变量，缺省即"未接线"，不会误发请求）：

```
EDU_MODULE2_BASE=http://127.0.0.1:5180     # 题库 / 图谱
EDU_MODULE4_BASE=http://127.0.0.1:5175     # 教师工作台
```

**约束测试**：`test_no_internal_module_imports` 扫模块 3 全部源码，
出现 `import edu_agent.knowledge` / `import chromadb` / `import sqlite3` 之类即失败 ——
"严禁直接导入、操作学情、题库的内部数据库模型"这条由测试守着，不靠自觉。

---

## 9. 约束落实

| 约束 | 落实方式 |
|---|---|
| 只做后端业务逻辑与接口契约，不做前端 UI | `api.py` 只返回 JSON；市场页由 F 实现 |
| 默认不安装 | 草稿在 `skill_market/drafts/`，运行时目录没有它；`is_loadable=False` |
| 默认不共享 | `isolation.py`：每会话独立 plugin 集合，`bind()` 必须显式给 id，单会话上限 8 |
| 默认不联网 | 能力令牌白名单；未声明网络令牌的技能，扫描直接 block |
| 必须过扫描 + 审核才可加载 | `submit`/`approve` 都跑扫描；`publish` 前再跑一次 `for_publish=True` |
| `@kernel_function` 为最终形态 | `kernel_loader.py`：静态 AST 列举（不执行）→ 只有已发布技能才真 import 并 `add_plugin` |
| 会话之间 Skill 隔离 | 每个会话一个 `Kernel`（`build_kernel(session_id)`），没有"装载整个市场"的接口 |
| 草稿绝不可被调用 | 三重保险（见第 4 节末尾） |
| 审计只追加、脱敏 | `audit.py` 无 update/delete；`redact()` 全程脱敏；哈希链可校验 |

### 9.1 SK 动态加载的两个真实坑（都已修 + 有回归测试）

1. **plugin 名不允许连字符**：SK 的 `KernelFunctionMetadata.plugin_name` 正则 `^[0-9A-Za-z_]+$`，
   而技能 id 是 kebab-case，直接用会报
   `FunctionInitializationError: Failed to create KernelFunctionMetadata`。
   → `kernel_loader.plugin_name_for()` 做映射（`graph-node-question-draft` → `graph_node_question_draft`）。
2. **`published/<id>/<version>/` 是两层目录**：市场索引最初按一层读，导致"发布后市场里查不到"。
   → `store._skill_dir_in()` 版本感知。

---

## 10. 最小可运行后端 demo

### 10.1 启动

```powershell
# 网关（5174）已随课本教练启动；模块3 路由随 web_server 自动挂载
& "D:\ruanjian\deep code\.tools\start-edu-agent.cmd"
curl.exe -s http://127.0.0.1:5174/api/skill-market/registry      # 词表
```

### 10.2 全链路（实测输出，`.tools\diag\module3-live-demo.py`）

```
① 造成功会话           session_id=00b2f49b5250
② 生成草稿             ok=True  id=skill-4c6e80a26a  dir=…\skill_market\drafts\skill-4c6e80a26a
③ 权限扫描             ok=True  verdict=pass  review_note=权限扫描通过
④ 匿名 fixture        ok=True  executed=False
⑤ 人工补 plugin.py     ok=True  errors=[]
⑥ 提交审核             ok=True  status=submitted  version=0.1.0
⑦ 审核通过             ok=True  status=approved   version=0.1.0
⑧ 发布（不带 confirm）   ok=False gate=permission policy=ask      ← 权限闸门生效
⑨ 发布（confirm）       ok=True  status=published version=0.1.1 installed_to=…\data\skills\skill-4c6e80a26a
⑩ 市场索引             count=2  status=published  installed=True  loadable=True
⑪ 详情权限说明          caps=["kg.read","question.draft"]  scope=["knowledge_id","teaching_context"]  funcs=["draft_question"]
⑫ 绑定到会话           ok=True  skills=["skill-4c6e80a26a"]  rejected=[]
⑬ Kernel 目录         loadable=True  funcs=["draft_question"]
⑭ 加载进 Kernel        ok=True  已装 GraphNodeQuestionDraft（8 ms）
⑮ 审计轨迹             ["skill.publish","skill.publish.blocked","skill.approve","skill.submit"]
⑯ 审计链校验           ok=True  records=9  broken=[]
⑰ 停用演示技能          ok=True  status=disabled
⑱ 删除演示会话          ok=True
```

### 10.3 对接模块 2（题库）与模块 4（教师工作台）

```python
from edu_agent.skill_market.contract import ModuleClient, TeachingContext

client = ModuleClient({"module2": "http://127.0.0.1:5180",
                       "module4": "http://127.0.0.1:5175"})

ctx = TeachingContext(knowledge_id="K1024",
                      teaching_context={"stage": "高中", "subject": "数学", "chapter": "函数单调性"},
                      mastery_snapshot_id="ms-2026-10-01-01")     # 只传 id，不传明细

# —— 模块2：读节点 → 交回题目草稿（入库与审核是模块2 的职责）
node  = client.read_knowledge_node(ctx)                            # POST /api/knowledge/node
draft = {"kind": "question_draft", "stem": "…", "answer": "…", "analysis": "…",
         "knowledge_id": "K1024", "difficulty": "基础"}
saved = client.create_question_draft(ctx, draft)                   # POST /api/question/draft

# —— 模块2：读学情**聚合指标**（不取学生明细）
summary = client.read_mastery_summary(ctx)                         # POST /api/mastery/summary

# —— 模块4：把结果作为卡片推给教师工作台
client.emit_workbench_card(ctx, {"kind": "question_draft_card",
                                 "title": "函数单调性 · 题目草稿",
                                 "summary": "已生成 1 道基础题，待审核"})   # POST /api/workbench/card

print(client.summary())   # {'calls': 4, 'modules': ['module2','module4'], 'paths': [...]}
```

Skill 内部（人工编写的 `plugin.py`）同样只用这两个东西：

```python
from semantic_kernel.functions import kernel_function
from edu_agent.skill_market.contract import ModuleClient, TeachingContext

class GraphNodeQuestionDraft:
    @kernel_function(name="draft_question", description="按图谱节点生成题目草稿（只产草稿）")
    def draft_question(self, knowledge_id: str, stage: str = "高中", difficulty: str = "基础") -> dict:
        ctx = TeachingContext(knowledge_id=knowledge_id, stage=stage, subject="数学")
        if ctx.validate():
            return {"ok": False, "error": "载荷不合法"}
        node = ModuleClient().read_knowledge_node(ctx)
        return {"ok": True, "draft": {...}, "node": node}
```

### 10.4 跑测试

```powershell
cd "D:\ruanjian\deep code\edu-agent"
& .\.venv\Scripts\python.exe -m unittest tests.test_skill_market -v
# Ran 53 tests ... OK
```

覆盖：manifest 兼容/校验、扫描四层、审计追加+脱敏+防篡改、会话→草稿、状态机全流程、
职责分离、权限闸门、会话隔离、Kernel 加载（含**真** SK 的回归测试）、跨模块契约、
以及验收 case `TestAcceptanceGraphNodeSkill`。

---

## 11. PPT 汇报简述

### 模块 3：Skill 市场与 Agent 自动封装

**职责（一句话）**：把一次成功会话，变成可审核、可发布、可回滚、**默认谁都装不上**的 Skill。

**输入**：`session_id` / 显式草稿 + manifest + 匿名 fixture
**输出**：可审查 `SKILL.md` + 测试结果 + 权限扫描结论 + 审计记录

**六件事（SKILL-01~06）**

| | 交付 | 一句话 |
|---|---|---|
| 01 | Manifest 扩展 | front-matter 加 version/status/inputs/allowed_capabilities/data_scope/tests/owner，**保持逐行解析兼容** |
| 02 | 自动封装草稿 | 成功会话 → SKILL.md 草稿（**只产草稿，不产可运行 py**） |
| 03 | 静态权限扫描 | 未声明的网络/文件/执行 → 拒绝审核；越界碰题库库表 → 拒绝 |
| 04 | 沙箱与审计 | 只追加 + 哈希链 + 全程脱敏，连"被闸门拦下"也记 |
| 05 | 审核/发布/回滚 | 状态机 + 职责分离 + 三个权限动作 |
| 06 | 市场数据面 | 搜索/详情/权限说明/安装状态（**UI 由 F 做**） |

**与另外三个模块的协作**

```
        ┌──────────────── 模块3 · Skill 市场 ────────────────┐
        │  草稿区(Agent 看不到) → 提交 → 审核 → 发布(装进运行时) │
        └───┬───────────────────────┬───────────────────┬────┘
   只读会话 │                  只传标识 │            权限闸门 │
            ▼                       ▼                   ▼
     ┌──────────┐          ┌──────────────┐     ┌──────────────┐
     │ 模块2 题库 │◀────────▶│ 模块4 教师工作台│     │ G 安全/运维    │
     │ /api/...  │ knowledge_id          │ /api/...      │ permissions.py │
     └──────────┘ teaching_context      └──────────────┘  capabilities  │
                  mastery_snapshot_id                     └──────────────┘
                             ▲
                      B 接口与审计（/api/skill-market/*、audit）
                             ▲
                      F 市场 UI（消费本模块 JSON）
```

**三条红线（汇报时重点讲）**

1. **草稿绝不可被调用**：草稿在市场区、运行时目录里没有它、加载器二次核对状态 —— 三重保险。
2. **默认三不**：不安装 / 不共享（会话级 plugin 集合，单会话上限 8）/ 不联网（能力令牌白名单）。
3. **跨模块只传标识**：`knowledge_id`、`teaching_context`、`mastery_snapshot_id`；
   学生作答明文既不进 Skill 也不出 Skill，出站前有契约闸门拦。

**验收证据**：53 个单元测试全绿 + 5174 真实 HTTP 端到端 18 步跑通（含权限闸门拦截、审计链校验）。
