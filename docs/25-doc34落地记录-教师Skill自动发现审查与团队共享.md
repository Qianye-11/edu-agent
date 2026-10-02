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
