# 计划书（第一轮）· skill-create：自动技能封装

> 状态：**待评审**（第一轮范围已收敛，等 5 个决策点拍板后开工）
> 关联：`docs/20-模块3-Skill市场与Agent自动封装.md`（模块 3 已交付，本计划是它的上层触发器）
> 命名：代码里统一用 `skill_create`（模块名 `skill_market.skill_create`），
> 对外接口 `/api/skill-create/*`。若你更想要 `skill-creator`，说一声，改字符串即可。

---

## 1. 一句话目标

**让它自己发现"我老是这么干、而且每次都干成了"的流程，先验证跑得通，再问你要不要固化成技能。**

和现有能力的区别：

| 现有 | 触发 | 范围 | 检验 |
|---|---|---|---|
| `skills_local` 的「存成技能：xxx」 | 你**当场**明说 | 单轮对话 | 无（存了就能用） |
| 模块 3 的 SKILL-02 | 你**指定** `session_id` | 单个会话 | 有（匿名 fixture + 权限扫描） |
| **skill-create（本计划）** | **它自己发现** | **跨会话、看重复与成功率** | **有，且必须"跑通"才允许封装** |

三者最终汇到同一条流水线：`草稿 → 提交审核 → 发布/停用/回滚`（模块 3 的状态机），
**skill-create 不新开一条发布通道**，这是本计划的第一条红线。

---

## 2. 现状盘点（能复用的东西，不重写）

| 资产 | 位置 | 本计划怎么用 |
|---|---|---|
| 草稿生成 + 匿名 fixture | `skill_market/draft.py`（SKILL-02） | 封装器直接调用，不另写一套 |
| 静态权限扫描 | `skill_market/perm_scan.py`（SKILL-03） | 检验器的 **L1** |
| 审计（只追加/哈希链/脱敏） | `skill_market/audit.py`（SKILL-04） | 运行痕迹（run trace）沿用同一套写入与脱敏 |
| 状态机 / 权限闸门 | `skill_market/lifecycle.py`（SKILL-05） | 候选通过检验后走 `submit → approve → publish` |
| 会话级隔离 + SK 加载 | `skill_market/isolation.py`、`kernel_loader.py` | 回放检验在**隔离沙箱**里做 |
| 跨模块契约 | `skill_market/contract.py` | 只传标识，禁学生明文 |
| 长期记忆 | `edu_agent/memory.py`、`memory_auto.py` | 记住你的**偏好/习惯**，作为技能默认参数 |
| 跨会话检索 | `edu_agent/session_search.py`（FTS5+trigram） | 找"同一类意图"的历史会话 |
| 会话存储 | `host/store.py` | run trace 的原始素材来源 |
| 权限动作表 | `permissions.py` | 新增动作走同一套三态闸门 |

**结论：本计划真正要新写的只有三块** —— 运行痕迹埋点、工作流挖掘器、检验器（L1/L2）。
其余全是接线。

---

## 3. 范围

### 第一轮做（Round 1）

1. **运行痕迹（run trace）**：每次"成功完成"的会话轮次留下结构化痕迹。
2. **工作流挖掘器（Workflow Miner）**：跨会话聚合出**候选工作流**，带证据与评分。
3. **封装器（Packager）**：候选 + 证据 → SKILL.md 草稿（复用 SKILL-02）。
4. **检验器（Verifier）L1+L2**：静态权限扫描 + **fixture 回放**（形状一致性）。
5. **接口 + 候选看板数据 + 自检脚本 + 文档**。

### 第一轮不做（明确排除，避免范围失控）

- ❌ **自动发布**（任何情况下都要人点"发布"，走 `skill_publish` 闸门）
- ❌ 自动修改已发布技能
- ❌ 影子运行（shadow run）检验 → 放第二轮
- ❌ 前端页面（沿用模块 3 的分工：UI 交 F）
- ❌ 跨用户共享技能（仍然"默认不共享"）

---

## 4. 总体架构

```
                    ┌─────────────────────── 每次对话结束 ───────────────────────┐
                    │                                                            │
   会话库(host.store) │  ① 埋点：run trace（只追加、脱敏、哈希链）                  │
   session_search ───┼─▶ skill_market/runs/<yyyy-mm>.jsonl                       │
                    │                                                            │
                    └────────────────────────────┬───────────────────────────────┘
                                                 │
                          ② 挖掘（每日/手动）     ▼
                    ┌────────────────────────────────────────────────┐
                    │ Workflow Miner：签名聚合 → 候选工作流            │
                    │ 判据 R1~R6（见 §5.2）→ skill_market/candidates/ │
                    └────────────────────────────┬───────────────────┘
                                                 │ ③ 你确认（或半自动）
                                                 ▼
                    ┌────────────────────────────────────────────────┐
                    │ Packager：候选 + 证据 → SKILL.md 草稿（SKILL-02）│
                    │ 匿名 fixture + 记忆里的默认参数                  │
                    └────────────────────────────┬───────────────────┘
                                                 │ ④ 检验（必须全过）
                                                 ▼
                    ┌────────────────────────────────────────────────┐
                    │ Verifier：L1 静态权限扫描（perm_scan）           │
                    │           L2 fixture 回放（沙箱，形状一致性）     │
                    └────────────────────────────┬───────────────────┘
                                                 │ ⑤ 全过 → 状态 verified
                                                 ▼
                    ┌────────────────────────────────────────────────┐
                    │ 模块3 状态机：submit → approve → publish         │
                    │ （人工审核 + permissions 闸门，不可绕过）         │
                    └────────────────────────────────────────────────┘
```

---

## 5. 核心机制

### 5.1 运行痕迹（run trace）—— 一切判定的原始证据

**为什么先做它**：没有痕迹，"跑通"就只能靠嘴说。痕迹是**可复核**的：谁、什么时候、
拿什么输入、调了哪些能力、产出什么形状、有没有被人工改过。

存储：`<data_dir>/skill_market/runs/<yyyy-mm>.jsonl`（**只追加**，沿用 `audit.py` 的
哈希链与 `redact()` 脱敏，不新写存储）。

```json
{
  "seq": 42,
  "ts": "2026-10-02T09:14:03+0800",
  "session_id": "00b2f49b5250",
  "turn": 3,
  "intent_signature": "graph-node-question-draft",   // §5.2 的签名
  "trigger_text": "按图谱节点 K1024 生成一道题，要带解析",   // 脱敏后
  "inputs": {"knowledge_id": "K1024", "stage": "高中", "difficulty": "基础"},
  "steps": [
    {"kind": "skill", "id": "kg-read", "args_digest": "sha256:1a2b…"},
    {"kind": "llm", "model": "deepseek-v4-flash", "ms": 1830},
    {"kind": "skill", "id": "question-draft", "args_digest": "sha256:9c8d…"}
  ],
  "output": {"shape": ["stem", "answer", "analysis", "knowledge_id"],
             "digest": "sha256:7f3e…(len=412)", "chars": 412},
  "outcome": "success",                  // success | failed | abandoned | corrected
  "human_correction": false,             // 用户随后是否纠正/重问
  "latency_ms": 4120,
  "prev_hash": "…", "hash": "…"
}
```

**埋点位置**（三处，都不改现有语义）：

1. `web_server.py` 的 `/api/chat` 收尾处（会话轮次结束时）；
2. 技能调用收尾处（`capabilities.run_skill()` 的返回路径）——记录"调了哪个技能"；
3. 人工修正检测：同一会话内，用户在下一条消息里否定/重问上一轮 → 回填
   `human_correction=true`（用 `care.py`/`greet.py` 同款的轻量正则，不引入模型判断）。

> 隐私：`trigger_text` 走 `audit.redact()`；`inputs` 只允许 `contract.ALLOWED_KEYS` 里的标识；
> 学生作答一律只留 digest（复用模块 3 已有闸门，不新增规则）。

### 5.2 工作流挖掘器（Workflow Miner）

**签名（intent_signature）**：把一轮任务压成一个稳定字符串，用于跨会话聚合。
不引入模型，用确定性规则拼：

```
signature = slug(意图动词 + 主要能力序列 + 产出形状)
例：draft-question + [kg.read, question.draft] + [stem, answer, analysis] → "draft-question|kg.read+question.draft|3field"
```

**聚合窗口**：默认最近 30 天、最多 200 个会话（`session_search` 的 FTS 用来找同族，
签名用来判同一模式）。

**候选评分**（用于排序与阈值，全部可配）：

| 因子 | 含义 | 权重（默认） |
|---|---|---|
| `runs` | 成功次数 | 0.35 |
| `sessions` | 跨越的**不同会话数**（防"一次会话里刷 5 次"） | 0.25 |
| `consistency` | 产出形状一致率 | 0.20 |
| `recency` | 最近一次距今（越近越高） | 0.10 |
| `reuse_gain` | 预估省下的时间（步骤数 × 次数） | 0.10 |

### 5.3 「跑通」的可操作定义（R1~R6）

这是本计划最该被评审的一节 —— **把"跑通"变成可测的**：

| 编号 | 判据 | 阈值（默认，可配） |
|---|---|---|
| **R1** | 在**至少 N 个不同会话**里成功完成（不是同一会话里重复） | N = 2 |
| **R2** | 每次都有完整 run trace（输入标识 / 步骤 / 产出形状齐备） | 100% |
| **R3** | 用匿名 fixture **回放**，产出字段级形状一致 | 100% |
| **R4** | 无隐私：fixture 匿名化通过 + 契约白名单通过 | 必须 |
| **R5** | `perm_scan` 静态扫描 `verdict = pass` | 必须 |
| **R6** | 无人为修正（`human_correction=false`），或修正已被吸收进步骤 | 允许 ≤1 次且已记录 |

**R1~R6 全过 = "跑通"**，才允许生成草稿。任何一条不过 → 候选留在看板上，
标明"差哪一条"，**不生成草稿**（避免污染草稿区）。

### 5.4 封装器（Packager）

复用 `draft.create_draft_from_session()` 的通道，额外做三件事：

1. **记忆融合**：从 `memory.py` 取该用户的稳定偏好（学段、学科、默认难度、称呼），
   写成技能的默认参数 —— 这是"熟悉你"的落地方式，而不是让技能自己猜；
2. **去重**：与市场已有技能（`store.market_index()`）和已否决候选比相似度
   （签名 + 描述 trigram 重合率），重合 ≥ 阈值 → 合并进已有候选或抑制；
3. **来源链**：草稿 front-matter 里写 `source: skill-create`、`candidate_id`、
   `evidence_runs: <条数>`（进审计，不进提示词）。

产物仍然只有 `SKILL.md` + `fixture.json`，**不产可运行 .py**（模块 3 的硬约束不变，
插件由人工按 `templates/plugin_template.py` 编写）。

### 5.5 检验器（Verifier）

| 级别 | 做什么 | 第一轮 |
|---|---|---|
| **L1 静态** | `perm_scan.scan(..., for_publish=False)`：令牌白名单、网络/文件/执行、越界库表、语义 data_scope | ✅ |
| **L2 回放** | 在**隔离沙箱**里用 fixture 跑一遍：只允许调用声明的能力（`isolation` + 假 transport），断言产出字段集与 R3 一致 | ✅ |
| **L3 影子** | 发布前接一次真实请求，只记录不生效，人工比对 | ⏳ 第二轮 |
| **L4 回归** | 发布后每次调用都写 run trace，成功率跌破阈值 → 自动提示停用 | ⏳ 第二轮 |

L2 的实现要点：**不真的调用外部模块**。用 `contract.ModuleClient(transport=fake)` 注入
假传输，按 fixture 里声明的期望返回；技能代码若试图访问未声明的能力（网络/文件/执行），
L1 已经拦下，L2 再断言一次"实际调用集合 ⊆ 声明集合"。这样回放**不会产生副作用**。

### 5.6 触发时机（三个入口）

| 入口 | 何时 | 行为 |
|---|---|---|
| 自动扫描 | 每日一次（或会话数累计 ≥ 10 时） | 只更新**候选看板**，不生成草稿 |
| 半自动 | 会话结束时本次会话自身已满足 R1~R6 | 会话里追加一句："这套流程要不要固化成技能？"（一次会话最多问一次） |
| 手动 | 你显式调用 | `/api/skill-create/candidates`、`/api/skill-create/draft` |

**绝不自动发布**：草稿生成后停在 `draft`，要你（或团队审核人）走
`submit → approve → publish`，其中 `publish` 还要过 `permissions.guard("skill_publish")`。

---

## 6. 数据模型（新增两张表/目录）

```
<data_dir>/skill_market/
    runs/<yyyy-mm>.jsonl          运行痕迹（只追加、脱敏、哈希链）
    candidates/<signature>.json   候选工作流（可更新：计数/评分/状态）
    candidates/_rejected.jsonl    被否决的候选（冷却名单，只追加）
```

候选结构：

```json
{
  "candidate_id": "draft-question|kg.read+question.draft|3field",
  "signature": "draft-question|kg.read+question.draft|3field",
  "title": "按图谱节点生成题目草稿",
  "first_seen": "2026-09-28T…", "last_seen": "2026-10-02T…",
  "runs": 5, "sessions": 3, "success_rate": 1.0,
  "consistency": 1.0, "score": 0.87,
  "criteria": {"R1": true, "R2": true, "R3": true, "R4": true, "R5": true, "R6": true},
  "blocking": [],
  "evidence": [{"session_id": "…", "ts": "…", "run_seq": 42, "outcome": "success"}],
  "state": "candidate",          // candidate | verified | drafted | rejected | promoted
  "skill_id": ""                 // promoted 后指向市场里的技能
}
```

---

## 7. 接口契约（新增，全部 JSON，无 UI）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/skill-create/candidates?state=&limit=` | 候选看板（含证据、评分、差哪条判据） |
| POST | `/api/skill-create/scan` | 手动触发一次挖掘（可给时间窗/最小次数） |
| POST | `/api/skill-create/verify` | 对某候选跑 L1+L2，返回逐项结论 |
| POST | `/api/skill-create/draft` | 候选 → 草稿（要求 `verified`；仍停在 draft） |
| POST | `/api/skill-create/reject` | 否决候选（进冷却名单，不再提议） |
| GET | `/api/skill-create/runs?signature=&skill=` | 运行痕迹查询（只读） |
| GET | `/api/skill-create/stats` | 挖掘观测：候选数、通过率、被否率（评估阈值是否合理） |

权限：新增 `permissions.py` 动作 **`skill_create_draft`**（默认 `ask`）——
"生成草稿"本身有副作用（占草稿区、可能触发半自动提示），过闸门更稳妥。

---

## 8. 第一轮里程碑与排期

| 里程碑 | 内容 | 交付物 | 估时 |
|---|---|---|---|
| **M1** | run trace 埋点（三处）+ 脱敏 + 只追加存储 | `skill_market/runs.py`、`web_server` 接线 | 1 天 |
| **M2** | 工作流挖掘器 + 候选存储 + R1~R6 判定 | `skill_market/miner.py`、`candidates/` | 1.5 天 |
| **M3** | 封装器（复用 SKILL-02）+ 去重 + 记忆融合 | `skill_market/packager.py` | 1 天 |
| **M4** | 检验器 L1+L2（含 fixture 回放沙箱） | `skill_market/verifier.py` | 1.5 天 |
| **M5** | 7 个接口 + 自检脚本 + 文档 + 单测 | `api.py` 扩展、`verify-skill-create.py` | 1 天 |
| **合计** | | | **约 6 个工作日** |

依赖：M2 依赖 M1；M4 依赖 M3（要有草稿才能扫/回放）；M5 依赖 M2~M4。
可与 F 的 UI 并行（接口先冻结，UI 后接）。

---

## 9. 验收标准（第一轮）

**正向**（用真实场景，不用我造的假数据）：

1. 你连续 2 个不同会话里完成"按图谱节点生成题目草稿" → 挖掘器产出候选，评分 > 0.6；
2. 候选看板上能看到：出现了几次、跨几个会话、产出形状一致率、R1~R6 逐条结论；
3. 点"生成草稿" → 草稿区出现 `SKILL.md` + `fixture.json`，**没有 .py**；
4. 跑检验 → L1 `verdict=pass`、L2 形状一致率 100% → 状态 `verified`；
5. 提交审核 → 状态机走到 `submitted`（**不会自动发布**）。

**反向**（自检脚本里必须报错的项）：

| 场景 | 期望 |
|---|---|
| 只有 1 个会话里成功过 | ❌ 不产生候选（R1 不过），看板标"还差 1 个会话" |
| 同一会话里重复 5 次 | ❌ 不计入 `sessions`，仍 R1 不过 |
| 有失败/被纠正的运行 | ❌ R6 不过 → 不生成草稿 |
| fixture 里含学生姓名/手机号 | ❌ R4 不过 + 匿名校验拒绝 |
| 技能正文要联网但没声明能力 | ❌ L1 block |
| 回放时产出字段少了 | ❌ R3 不过，报告指出缺哪个字段 |
| 与已有技能签名重合 | ❌ 抑制或合并，不重复建草稿 |
| 你否决过的候选 | ❌ 冷却期内不再出现在看板顶部 |

---

## 10. 风险与对策

| 风险 | 影响 | 对策 |
|---|---|---|
| **噪音**：什么都想封装 | 草稿区被垃圾淹没 | 阈值 + 跨会话要求 + 冷却名单 + 单次会话最多提示一次 + 看板按分数排序 |
| **误判"跑通"** | 固化了一个坏流程 | R3 回放是硬门槛；R6 要求无人工修正；阈值先保守（N=2、一致性 100%）再按观测放宽 |
| **隐私泄露** | 严重 | 沿用模块 3 全部闸门（`redact`/`contract`/fixture 匿名校验），run trace 里学生数据只留 digest |
| **自动执行风险** | 误操作 | 第一轮**绝不自动发布**；`skill_create_draft` 也要过 `permissions` 闸门 |
| **开销** | 埋点拖慢对话 | 埋点异步、只写一行 JSON、失败静默（绝不影响回答）；`/api/chat` 关键路径不 await |
| **与「存成技能」重复** | 概念混淆 | 文档里明确分工（§1 表格）；两者最终汇到同一状态机 |
| **阈值拍脑袋** | 要么太吵要么不触发 | `/api/skill-create/stats` 提供观测；阈值全部可配（`data/skill_create.json`） |

---

## 11. 测试计划

单测（新增 `tests/test_skill_create.py`，目标 ≥ 40 个用例）：

- **痕迹**：写入只追加、脱敏（手机/邮箱/姓名/作答 → 无明文）、哈希链可校验、失败静默；
- **挖掘**：签名稳定性（同模式同签名、不同模式不同签名）、跨会话计数、R1~R6 逐条正反用例、
  评分单调性（次数多分更高、时间久分更低）；
- **封装**：只产 SKILL.md+fixture、去重命中时不重复建、记忆默认参数被写进草稿；
- **检验**：L1 复用模块 3 的用例、L2 假传输下断言调用集合 ⊆ 声明集合、字段缺失能定位；
- **反向**：§9 那张表的 8 条全部落成用例。

端到端（真实 HTTP，沿用模块 3 的做法）：造 2 个成功会话 → scan → verify → draft →
submit → 断言市场状态；跑完清理，不留垃圾会话。

**自检脚本**：`.tools/verify-skill-create.cmd`（同 `verify-module3.cmd` 风格），
正向 + 反向共约 30 项，失败即非零退出。

---

## 12. 待你拍板（5 个决策点）

| # | 问题 | 我的建议 |
|---|---|---|
| 1 | 触发时机：会话结束即扫 / 每日定时 / 只手动？ | **每日一次 + 手动**，会话结束时只做"半自动提示"（省开销、少打扰） |
| 2 | "跑通"的最低门槛：2 个会话还是 3 个？ | **2 个**（先跑起来看噪音，再调；配置项可改） |
| 3 | 允许自动生成草稿吗？ | **允许**，但**绝不自动提交审核**，也绝不自动发布 |
| 4 | 命名：`skill-create` 还是 `skill-creator`？ | 代码 `skill_create`，接口 `/api/skill-create/*` |
| 5 | run trace 埋点范围：全量会话 / 只埋技能调用？ | **全量轮次**（否则"跑通"缺证据），但只存摘要不存正文 |

---

## 13. 一句话总结

**第一轮只做四件事：记下每一次跑通的痕迹 → 跨会话找出重复的模式 → 拿证据回放验证 →
生成草稿交人工审核。** 发布权、审核权、否决权全部留在人手里。
