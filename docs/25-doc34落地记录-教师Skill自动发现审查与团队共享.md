# doc 34 落地记录：教师 Skill 自动发现、审查与团队共享

> 来源：`md/34-教师Skill自动发现、审查与团队共享实施方案.md`
> 结论：**大部分能力此前已实现**（模块3 SKILL-01~06 + skill-create R1~R6），本次按 doc 34
> 的模块划分与 API 合同**补齐缺口并对齐命名**，没有另起一套平行系统。

---

## 0. 一页对照：doc 34 要求 vs 现状

| doc 34 | 现状 | 本次动作 |
|---|---|---|
| `skill_candidates.py` 候选检测（30天≥3会话、修订模式、质量、隐私、去重） | `miner.py` + `runs.py` 已有 R1~R6（跨会话、形状一致、纠正次数、隐私、能力） | ✅ 阈值改由 `.env` 驱动（`SKILL_CANDIDATE_*`），默认对齐 doc：窗口 30 天、**≥3 会话**、冷却 30 天 |
| `skill_studio.py` 草稿/版本/审批/安装/归档 | `lifecycle.py` 状态机 + `store.py` 四区/版本 | 🆕 新增 `skill_studio.py`：**审批记录（绑定内容哈希）**、**安装五条闸门**、**撤回重改**、**同步队列**、目录视图 |
| `skill_studio_models.py` Pydantic 模型 | 现有接口用 dict body | ⏸ 未做（现有 `BaseModel` 只在 chat 入口；接口层保持 dict，避免为形式改造全部端点） |
| `skill_privacy.py` 脱敏/风险/导出策略 | 散在 `audit.redact` / `contract.guard_payload` / `perm_scan` | 🆕 新增 `skill_privacy.py` 收口：`scan_text` / `redact` / `risk_level` / `export_policy` / `scrub_markdown` |
| 状态机 `detected→draft→in_review→approved→installed`(+rejected/superseded) | `draft→submitted→approved→published→disabled→rolled_back` | ✅ 显式映射（`STATE_MAP`/`STATE_ALIAS`），不新增第二套状态字段 |
| 批准绑定内容哈希，改内容即失效 | 有 `draft_digest`（verify 后改动 → stale） | 🆕 `reviews/<id>.json` 记录 `content_hash` + 资源清单；安装前强校验 |
| `data/skill_studio/{candidates,drafts,reviews,audit,archive,sync_outbox}` | `data/skill_market/{candidates,drafts,submitted,published,audit,archive,fixtures,...}` | 🆕 新增 `reviews/`、`sync_outbox/`；其余提供**别名视图** `studio_dirs()`；`SKILL_STUDIO_DIR` 可整体改根目录（默认不动，保护历史数据） |
| `/api/skill-studio/*` 11 端点 | 已有 28+23+14 个等价端点 | 🆕 新增 `studio_api.py` 按 doc 合同做**薄映射**（不重写业务） |
| `.env`：`SKILL_STUDIO_*` / `TENCENT_MEMORY_*` | `skill_create.json` + `mode.json` | 🆕 `.env.example` 增加配置段 + `skill_studio.config()` 读取并同步阈值 |
| §8.1 九条单元测试 | 多数有等价物 | 🆕 `tests/test_skill_studio.py` 19 个用例，逐条对应 |
| §8.2 端到端验收 | 已有 `docs/23` 的实验 | 🆕 `TestEndToEndAcceptance`：4 次相近对话 → 候选 → 草稿 → 改 → 重批 → 安装 → 同步 |
| §1.3「第一版不自动执行 Skill 内脚本」 | L3 影子运行**会**执行（子进程 + 记录桩） | ⚠️ **冲突点**：新增开关 `SKILL_STUDIO_EXECUTE_PLUGIN`（默认 `1`=真跑，验证更强；`0`=静态核对，符合 doc 第一版口径）——**待你拍板默认值** |
| §5.6 Skill Studio 前端 | 无（前几轮明确"UI 交别人"） | ⏸ **未做**（只提供 JSON 接口） |
| §6 第二阶段 TencentDB 适配 | 无 | ⏸ 按 doc 先不做 P5；`TENCENT_MEMORY_*` 配置项与 `sync_outbox` 队列已就绪 |

---

## 1. 本次新增/改动文件

```
src/edu_agent/skill_market/
├── skill_privacy.py     🆕 脱敏 / 风险分级 / 导出策略（收口）
├── skill_studio.py      🆕 审批记录 + 安装闸门 + 撤回重改 + 同步队列 + 目录视图
├── studio_api.py        🆕 doc §5.5 的 11 个端点 + 同步/总览/导出策略
├── lifecycle.py         ✏️ 新增 approved→revise→draft 转移 + revise()；publish 注释写明红线
├── shadow.py            ✏️ L3 缓存 + 静态核对模式 + 禁止写 .pyc
├── paths.py             ✏️ market_root() 支持 SKILL_STUDIO_DIR
└── ...
tests/test_skill_studio.py   🆕 19 个用例（doc §8.1 九条 + §8.2 端到端）
.env.example                 ✏️ Skill Studio 配置段
```

---

## 2. 关键设计取舍（都踩过坑）

### 2.1 内容哈希为什么只哈希正文、且排除 fixture 与 `__pycache__`

批准绑定哈希的目标是"**教师批准的内容**被改了就失效"。但直接把整个目录做 sha256 会连环失效：

| 坑 | 现象 | 处理 |
|---|---|---|
| front-matter 里的流水线字段 | 发布时 `publish()` 改写 `version/status/review/updated` → 安装后哈希必变 | 只哈希 front-matter **之后的正文** |
| `fixture.json` | `install_to_runtime()` 刻意不把它复制进运行时目录 → 草稿区与运行时的文件集合不同 | 从内容哈希里排除 `manifest.fixture` 指向的文件 |
| `__pycache__/plugin.cpython-311.pyc` | **L3 影子运行 import 插件时 Python 顺手写的** → 提交一次哈希就变一次 | ① `content_hash`/`skill_files` 跳过构建产物；② 影子 runner 设 `sys.dont_write_bytecode=True` + 子进程 `PYTHONDONTWRITEBYTECODE=1` |

### 2.2 为什么补 `approved → revise → draft`

原状态机只有 `submitted → reject → draft`。审核通过后教师又发现要改，就**没有合法路径** ——
只能硬改文件或重新导入，两条都绕过状态机与审计。补上 `revise` 之后流程闭合：

```
draft ──submit──► in_review ──approve──► approved ──install──► installed
  ▲                   │                      │                     │
  └────reject─────────┘                      └────revise───────────┘
  ▲                                                                │
  └──────────────── new_draft（线上版本继续服务）───────────────────┘
```

### 2.3 安装五条闸门（doc §5.4）

```python
checks = {
  "approval_exists": 有教师批准记录,
  "hash_matches":    批准哈希 == 当前内容哈希（改内容即失效）,
  "overwrite_ok":    同名已存在时必须 allow_overwrite=true（覆盖前自动备份到 archive/）,
  "package_ok":      包检查：manifest/路径/体积/可执行文件/权限扫描,
  "permission":      permissions 闸门（skill_publish）,
}
```

### 2.4 团队同步队列（doc §6 / §8.1-9）

`enqueue_sync()` 用 `skill_privacy.export_policy()` 一次判完：**状态已安装 ∧ 批准哈希一致 ∧
无敏感内容**。任一条不满足 → 拒绝入队并写审计（`skill-studio.sync.refused`）。
队列落地为 `sync_outbox/<id>/{技能文件..., sync.json}`，第二阶段适配器直接从这里取。

---

## 3. 接口清单（无前端）

```powershell
# 总览（配置/目录映射/状态映射/候选数/队列长度）
Invoke-RestMethod "http://127.0.0.1:5174/api/skill-studio/overview"

# ① 候选箱（脱敏证据：只给会话编号、次数、评分、冷却）
Invoke-RestMethod "http://127.0.0.1:5174/api/skill-studio/candidates"
# ② 候选详情 → 生成草稿
Invoke-RestMethod "http://127.0.0.1:5174/api/skill-studio/candidates/<cid>" 
Invoke-RestMethod "http://127.0.0.1:5174/api/skill-studio/candidates/<cid>/generate-draft" -Method Post `
  -ContentType 'application/json' -Body '{"confirm":true}'
# ③ 编辑草稿（已批准/已发布会自动回到可编辑草稿态）
Invoke-RestMethod "http://127.0.0.1:5174/api/skill-studio/drafts/<id>" -Method Patch `
  -ContentType 'application/json' -Body '{"body":"...新正文..."}'
# ④ 结构校验 → ⑤ 提交审查（内部强制 L1+L2+L3）
Invoke-RestMethod "http://127.0.0.1:5174/api/skill-studio/drafts/<id>/validate" -Method Post
Invoke-RestMethod "http://127.0.0.1:5174/api/skill-studio/drafts/<id>/submit-review" -Method Post
# ⑥ 批准（绑定内容哈希）/ 驳回
Invoke-RestMethod "http://127.0.0.1:5174/api/skill-studio/reviews/<id>/approve" -Method Post `
  -ContentType 'application/json' -Body '{"reviewer":"G","note":"同意"}'
Invoke-RestMethod "http://127.0.0.1:5174/api/skill-studio/reviews/<id>/reject" -Method Post `
  -ContentType 'application/json' -Body '{"reviewer":"G","reason":"场景太泛"}'
# ⑦ 安装（五条闸门）
Invoke-RestMethod "http://127.0.0.1:5174/api/skill-studio/drafts/<id>/install" -Method Post `
  -ContentType 'application/json' -Body '{"confirm":true,"allow_overwrite":false}'
# ⑧ 审计 / 导出策略 / 同步队列
Invoke-RestMethod "http://127.0.0.1:5174/api/skill-studio/audit?limit=20"
Invoke-RestMethod "http://127.0.0.1:5174/api/skill-studio/export-policy/<id>"
Invoke-RestMethod "http://127.0.0.1:5174/api/skill-studio/sync/<id>/enqueue" -Method Post
Invoke-RestMethod "http://127.0.0.1:5174/api/skill-studio/sync/outbox"
```

---

## 4. 待你拍板的三件事

1. **`SKILL_STUDIO_EXECUTE_PLUGIN` 默认值**：现在 `1`（L3 真跑插件，验证更强，但违反 doc §1.3
   "第一版不自动执行 Skill 内脚本"）。改成 `0` 则 L3 退化为静态核对（AST 读返回键 + 能力核对）。
2. **Skill Studio 前端**（doc §5.6）：本轮只做了 JSON 接口。要我写页面就说一声（此前你说 UI 交别人）。
3. **目录命名**：现在实际用 `data/skill_market`（历史数据都在那）。要不要按 doc 迁到
   `data/skill_studio`？（`SKILL_STUDIO_DIR` 已支持，但**换目录不会自动迁移旧数据**。）

---

## 5. 验收证据

```
tests/test_skill_studio.py     19 个用例 OK（doc §8.1 九条 + §8.2 端到端）
全量 5 个测试文件               见提交说明（含模块3/skill-create/catalog/mode/studio）
```

| doc §8.1 | 用例 | 结果 |
|---|---|---|
| 1 三次相似对话生成一个候选 | `test_three_similar_sessions_make_candidate` | ✅ |
| 2 两次不相似对话不生成候选 | `test_two_dissimilar_sessions_make_nothing` | ✅ |
| 3 含学生姓名/成绩的候选被脱敏 | `test_sensitive_candidate_is_scrubbed` | ✅ |
| 4 编辑草稿后批准失效 | `test_edit_after_approval_invalidates_it` | ✅ |
| 5 未批准时安装被拒绝 | `test_install_requires_approval` | ✅ |
| 6 同名覆盖前创建可恢复备份 | `test_overwrite_creates_backup` | ✅ |
| 7 拒绝后冷却期不重复提醒 | `test_reject_sets_cooldown` | ✅ |
| 8 团队服务不可用时本地不出错 | `test_team_service_down_local_still_works` | ✅ |
| 9 未批准 Skill 不允许进入同步队列 | `test_unapproved_cannot_enqueue_sync` | ✅ |

---

# 第二轮：补齐 doc 34 剩余条款（§5.3 / §5.4 / §5.5 / §6）

> 第一轮做完 P0~P2 后，重新**通读全文**（首次阅读时中间一段被截断），发现 4 处未落地条款。

## A. §5.5 路径与文件名白名单 —— 原实现**违规**

> 文档原文："**不得接受客户端传入的本机绝对路径**；所有文件名、Skill ID 和资源路径均需服务端白名单验证。"

原 `POST /api/skill-create/import` 直接 `read_text(客户端给的 path)` —— 等于把**任意文件读取**暴露出去
（一个导入请求就能读走 `.env` / 凭据 / 私钥）。

**新增 `safe_import.py`**，三条都管住，且一律 fails closed：

| 维度 | 规则 |
|---|---|
| 导入路径 | 只允许**导入根之内**：`EDU_SKILL_IMPORT_ROOTS`（默认 `<repo>/skill-src` + `<data_dir>/skill_inbox`）；相对路径按导入根解析；`..`/symlink 先解析再判定 |
| 资源文件名 | 单层文件名 + 后缀白名单（`.py/.md/.json/.txt/.yml/.yaml/.csv/.tsv`）+ 禁隐藏文件 + 禁 `.exe/.cmd/.ps1/.pem/.env` 等 |
| Skill ID | `^[a-z0-9][a-z0-9_-]{1,47}$`（与 manifest 同口径） |

接入点：`/api/skill-create/import`（path 形式）、`PATCH /api/skill-studio/drafts/{id}`（`files` 形式）；
新增 `GET /api/skill-studio/safe-import` 暴露白名单快照供前端展示。

## B. §5.3 草稿最小结构 —— 原模板**不符**

> 文档规定 `SKILL.md` 必须用稳定的七段结构。

原 `draft.build_body()` 产出的是"触发场景/输入/步骤/…"另一套段落。现改为 doc 规定的七段
（`适用场景`/`必要输入`/`工作流程`/`输出格式`/`教师确认点`/`质量与隐私约束`/`版本来源`），
原有信息全部归并进去不丢；front-matter 的来源标记改为 **`source: conversation_candidate`**。

同时在 `skill_studio.validate_draft()` 增加段位校验 —— **只对 Agent 自动生成的草稿强制**
（`conversation_candidate` / `skill-create` / `session`），手写与导入的技能不强制：
不能一刀切，否则会把已有技能全拒了。

## C. §5.4 `superseded` —— 原来只有映射没有实现

发布新版时把旧版本标 `superseded`：在旧版本快照目录写 `SUPERSEDED.json`
（**不删文件、不改历史**）+ 审计记 `skill.supersede`；
`GET /api/skill-studio/versions/{id}` 展示"当前版本 / 被替代版本（历史保留但不激活）"。

## D. §6.2 团队适配器 `tencent_memory_adapter.py` —— 新增

| doc 职责 | 实现 |
|---|---|
| 读取本地 `sync_outbox` | `outbox()` |
| 已批准 Skill → 团队资产 | `to_team_asset()`（**双重闸门**：`export_policy` + 逐文件 `scan_text`） |
| 拉取团队 Skill 元数据 | `pull_team_metadata()` |
| 远程资产导入为**本地 draft** | `import_team_asset()`（只写草稿区，**绝不 publish**；文件名过白名单、内容过隐私扫描） |
| 记录本地/远程 ID、版本、时间、失败原因 | `sync_log.jsonl` |
| 不处理未脱敏学生数据 | 出站/入站双向扫描；`SYNC_MATRIX` 把 §6.3 的同步矩阵写进代码 |

**默认关闭（`TENCENT_MEMORY_ENABLED=0`）时零远程调用** —— `connection_test()` 直接返回"跳过"，
本地对话/教案/课件/Skill 完全不受影响（doc §6.1 的硬要求）。

## E. 顺手修掉一个自己埋的真 bug：配置读取的隐藏副作用

`shadow_run()`（L3 影子运行）每次都会调 `skill_studio.config()`，而那个函数当时**顺手**
`runs.set_config(min_sessions=3)` —— 于是**每次影子运行都会悄悄改掉全局候选阈值**。
表现：`skill-create` 自检里显式设了 `min_sessions=2`，跑完 L3 后变成 3 →
候选 R1 判据不齐 → 半自动提示不出现 → 自检报 `p1=False`。

**修法**：`skill_studio.config()` 改为**纯读取**；阈值优先级在 `runs.config()` 里统一解析为
**「显式 set_config 写进文件 > 环境变量（.env）> 内置默认」**。修完自检回到 **39/39**。

> 教训：**读配置的函数不要写全局状态**。它会在你完全想不到的调用点（比如影子运行）触发。

## F. 验收证据（第二轮）

```
tests/test_skill_studio.py     33 个用例 OK（原 19 + 新增 14：白名单 4 / 七段结构 3 / superseded 1 / 团队同步 6）
全量 5 个技能测试文件           221 个用例 OK
模块3 自检                     37/37
skill-create 自检              39/39
```

## G. 剩余未做（明确记录）

| 项 | 状态 |
|---|---|
| §5.6 Skill Studio 前端（4 个页面） | ⏸ 未做（此前明确"UI 交别人"） |
| `skill_studio_models.py`（Pydantic 模型） | ⏸ 未做（接口层保持 dict，避免为形式改造全部端点） |
| §6.4 TencentDB 独立部署与真实同步验证 | ⏸ 需要独立 Docker/WSL2 环境；本地侧已就绪（队列 + 适配器 + 连接测试接口） |
| §8.3「原有聊天、教案、课件通过回归测试」 | 需跑非技能模块的回归（本次只跑了 5 个技能测试文件） |

---

# 第三轮：`skill_studio_models.py`（§5.1）+ §8.3 全平台回归

## A. 新增 `skill_studio_models.py`（doc §5.1 明确要求的 Pydantic 层）

原来 `studio_api.py` 直接用 `dict` 收 body —— 客户端把 `reviewer` 拼成 `review` 时**不会报错**，
而是按默认值走成"匿名批准"。现在入参一律过模型：

| 模型 | 用途 | 关键约束 |
|---|---|---|
| `ApproveBody` | 审核通过 | `reviewer` 必填（批准要绑定哈希，必须有人负责） |
| `RejectBody` | 驳回 | `reviewer` + `reason` 都必填（冷却期要用理由） |
| `InstallBody` | 安装 | `allow_overwrite` 显式开关 |
| `PatchDraftBody` | 编辑草稿 | `files` ≤32 个、单文件 ≤200KB |
| `GenerateDraftBody` / `ActorBody` / `ImportBody` / `TeamImportBody` / `ModeBody` / `McpUserServiceBody` | 其余变更接口 | 长度、范围、字面量（`transport: http\|mcp`） |
| `MutationEnvelope` | 响应信封 | doc §5.5 的 `{ok, candidate_id, state, content_hash, updated_at}` |

* 入参基类 `extra="forbid"`：**多传/拼错的字段直接报错**，不再被静默吞掉；
* 响应模型只做形状校正（补默认/剔多余），**不改变业务值**；
* 新增 `GET /api/skill-studio/schemas` 输出契约快照，前端不用读代码；
* 未引入新依赖（FastAPI 自带 Pydantic v2）。

> 踩坑记录：`validate_or_error` 最初写成 `model(body)` —— Pydantic v2 的 `BaseModel.__init__`
> **只接受关键字参数**，会抛 `takes 1 positional argument but 2 were given`；已改 `model(**payload)`。
> 另：Pydantic 宽松模式会把 `"yes"` 正常转成 `True`（友好行为，不是漏洞），
> 测试改为用真正非法的结构（dict/list）验证拒绝。

## B. §8.3 全平台回归 —— 发现并修掉一个**import 副作用**

跑 `unittest discover`（926 用例）时出现 2 条技能测试失败，而**单独跑同样的测试却是全绿**：

```
FAIL: test_scheduler_disabled_when_interval_zero   → r.get("disabled") is None
FAIL: test_scheduler_run_once_updates_board        → status()["runs"] == 2（期望 1）
```

**根因**：`web_server.py` 在**模块级**调用了 `_sc_scheduler.start()` —— 也就是说
"**import web_server**" 这个动作本身会 spawn 一个后台线程。而有 10+ 个测试文件 import 它
（`test_courseware`、`test_design_*`、`test_kg`、`test_lesson_*`、`test_plan_*` …），
于是跑全量时：调度线程活着 → `start()` 走"already running"短路分支（不返回 `disabled` 键）；
线程的循环又调过 `run_once()` → 全局计数器 `runs` 不再是 0。

**修法（两处）**：

1. `web_server`：把 `_sc_scheduler.start()` 从模块级移进 `__main__`（真正起服务时才拉起）
   —— **import 不该有副作用**。实测：`import edu_agent.web_server` 之后
   `scheduler.status()` → `running=False, runs=0` ✅；而服务进程里仍照常启动
   （线上实测 `interval_hours=24.0, running=True, next_run=2026-10-04T13:08`）✅
2. `scheduler.reset()`：新增显式重置入口（停线程 + 计数器归零），
   调度器测试在 `setUp` 里调用，**不依赖"谁先跑谁后跑"**。

修完：全量 926 用例里**技能相关模块 0 失败**。

## C. §8.3 现状（如实记录）

```
unittest discover -s tests -p "test_*.py"
    Ran 926 tests in 251s
    FAILED (failures=19, errors=8, skipped=35)
    失败分布：test_slide_deck 8 · test_slide_media 7 · test_slide_objects 3 ·
              test_slide_fill 2 · test_slide_template 2 · test_slide_boxes 2 ·
              test_slide_upload 2 · test_slide_edit 1
    技能相关（skill_market/create/catalog/mode/studio）：0 失败 ✅
```

**结论**：这 27 个失败**全部集中在 `test_slide_*`（课件/PPT 模块）**，是该分支与
`fork/feat/ppt-view` 合并时就存在的历史失败（断言旧实现），**与 doc 34 的改动无关**
（本轮改动前后都是这 27 条，我只把其中混进来的 2 条技能失败修掉了）。

因此 §8.3 的"原有聊天、教案、**课件**功能通过回归测试"**目前不满足** ——
课件模块需要单独一轮修复或重新基线化，这是独立于 Skill 生态的工作项。
