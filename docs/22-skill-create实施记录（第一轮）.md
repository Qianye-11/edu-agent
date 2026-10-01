# 实施记录 · skill-create 自动技能封装（第一轮）

> 计划书：`docs/21-计划书-skill-create自动技能封装.md`
> 代码：`src/edu_agent/skill_market/{runs,miner,packager,verifier,create_api}.py`
> 测试：`tests/test_skill_create.py`（37 个用例全绿）
> 自检：`.tools\verify-skill-create.cmd`（27 项，含 8 条反向用例）
> 接口：`/api/skill-create/*`（14 个端点，已挂进 5174）

第一轮按计划书的 M1~M5 全部做完，并**在真实会话历史上跑通**（回扫 21 个会话 / 48 轮）。
本文件记录：做了什么、和计划书的偏差、实测踩到的 6 个真问题、以及第二轮待办。

---

## 1. 交付清单（对应计划 M1~M5）

| 里程碑 | 文件 | 内容 |
|---|---|---|
| **M1 运行痕迹** | `runs.py`（20 KB） | 只追加 + 哈希链 + 脱敏；`record_turn` / `record_skill_call` / `begin_turn`；**回扫历史会话**（`sweep` / `sweep_session`，幂等）；人工纠正检测 |
| **M2 挖掘器** | `miner.py`（18.6 KB） | 意图签名（意图\|能力\|形状）、跨会话聚合、R1~R6 判定、评分、候选存储、否决+冷却、观测 |
| **M3 封装器** | `packager.py`（18 KB） | 候选+证据 → 草稿（复用 SKILL-02 通道）、去重、记忆融合、能力令牌映射、权限闸门 |
| **M4 检验器** | `verifier.py`（9 KB） | L1 静态扫描（复用 perm_scan）+ L2 声明式回放（不执行草稿代码）+ 检验后改动的完整性校验 |
| **M5 接口** | `create_api.py`（8.5 KB） | 14 个 `/api/skill-create/*` 端点 |
| 接线 | `web_server.py`、`capabilities.py` | `/api/chat` 开头补记上一轮 + 清缓冲；技能调用记进缓冲 |
| 闸门 | `permissions.py` | 新增动作 `skill_create_draft`（默认 ask） |
| 复用改造 | `audit.py` | 抽出 `chain_append()` / `verify_chain_in()`，运行痕迹与审计共用同一套"只追加+可校验" |

---

## 2. R1~R6 的最终口径

| 判据 | 实现 | 备注 |
|---|---|---|
| **R1** | `len(sessions) >= min_sessions`（默认 2） | 按**不同会话数**，同一会话刷次数不算 |
| **R2** | 每次成功运行都要有完整痕迹（签名 + 产出形状 + 步骤或输入） | 缺一即不过 |
| **R3** | 挖掘阶段用**形状一致率**做代理；真正的"回放一致"由 `verifier` L2 判定并写回 | 见 §3 偏差 1 |
| **R4** | 痕迹里不得残留未脱敏隐私（脱敏后不该命中手机/邮箱/身份证） | |
| **R5** | 挖掘阶段校验"调过的能力都能映射到已登记能力"；完整静态扫描由 L1 判定 | |
| **R6** | **纠正次数 ≤1**，且若为 1 次则其后仍有成功 | 收紧过一次，见 §4 问题 4 |

阈值全部可配（`GET/POST /api/skill-create/config`，落 `data/skill_market/skill_create.json`）：

```json
{"trace_enabled": true, "min_sessions": 2, "min_runs": 2, "window_days": 30,
 "consistency_min": 1.0, "score_min": 0.6, "reject_cooldown_days": 30,
 "dedupe_threshold": 0.6}
```

---

## 3. 与计划书的偏差（都是实现时发现更合理的做法）

1. **R3 分两段判定**：计划里 R3 就是"fixture 回放一致"，但回放必须等草稿（有 fixture）才能做，
   而"跑通"判定在挖掘阶段就要给出。所以挖掘阶段用**痕迹里的形状一致率**做代理，
   `verifier` 的 L2 做真正的回放并把结果写回候选的 `criteria.R3`。

2. **SC-N8 的口径改了**：对话里我写的是"跳过 verify 直接调 /draft → 拒绝"，实现后调整为
   —— `/draft` 要求 **R1~R6 全过**（"跑通"），`/submit` 要求 **state=verified**（检验过）。
   这样"跑通"和"检验"两道闸门各管一段，不互相矛盾。

3. **L2 不执行草稿代码**：计划里写"在隔离沙箱里用 fixture 跑一遍"。实现时改为
   **声明式回放**（核对痕迹里调过的能力 ⊆ manifest 声明的能力 + 形状一致 + AST 静态核对
   `plugin.py` 的返回键）。理由：草稿是**未审核**代码，执行它就等于绕过审核 ——
   与模块 3"静态列举不执行代码"的立场保持一致。

4. **历史回扫的能力清单是"推断"的**：回扫拿不到"当时调了哪个技能"（那时还没埋点），
   所以按意图给一份建议能力（`INTENT_DEFAULT_CAPABILITIES`），并在候选、草稿正文、
   返回值三处都标 `capabilities_source: inferred` + 警告 —— 不假装那是观测到的。

5. **新增"承接轮继承意图"**：用户第二轮常说"再来一道"，不重述意图。不处理的话这些轮次
   会掉进 `other` 签名组、从工作流里漏掉（实测踩到）。现在承接语继承上一轮意图。

---

## 4. 实测踩到的 6 个真问题（都修了 + 有回归测试）

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | "帮我按知识点**出个题**"与"生成一道题"分不到一组 | 意图规则只写了「出题」，认不出"出个题/出一道/重出一道题" | 正则放开口语说法：`(出|生成|重出|再出|换|来一|来几)[^。；\n]{0,6}?题` |
| 2 | 纠正后的轮次掉出工作流、R6 永远算不出纠正 | 承接/纠正语（"重出一道题"）被识别成 other → 另立签名组 | 同上 + **承接轮继承上一轮意图** |
| 3 | L2 形状一致率永远 0.8（差 `difficulty`） | SKILL-02 的 fixture 模板写的是题目草稿的**通用**字段，与这次挖掘到的实际产出不符 | 封装时用**痕迹里实际产出的形状**覆盖 fixture 的期望字段 |
| 4 | 反复被纠正的坏流程照样"跑通" | R6 早先写成"只要后面还有成功就算过" | 收紧为**纠正次数 ≤1**（与计划书口径一致） |
| 5 | 引用元数据（citations/coverage/第 2 段）被当成产出字段 | `output_shape` 把 A 链答复里的 JSON 元信息也捞进来了 | 加噪声表（citations/confidence/coverage/page/section… + "第 N 段"模式） |
| 6 | 自检脚本里负面场景污染后续正向前置 | 同一临时市场里跑完负面用例，候选已被判 R6 不过 | 自检脚本加"换一块干净市场"的 `reset_market()` |

另外记录一个**不是 bug 但值得知道**的行为：真实历史回扫后，48 轮里 46 轮是 `abandoned`
（没有结构化产出 —— 那些是问候/情绪/单轮问答），只有 2 轮成功。这**符合设计**：
一次性问答不是"工作流"，不该被封装。目前历史里唯一够格的两个候选都因"只出现在 1 个会话"
被 R1 正确拦下 —— 也就是说：**你要让某个流程出现两三次，它才会自己冒出来。**

---

## 5. 接口清单（14 个）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/skill-create/candidates` | 候选看板（评分/证据/差哪条判据/冷却标记） |
| POST | `/api/skill-create/scan` | 挖一次（默认先回扫历史会话） |
| POST | `/api/skill-create/sweep` | 只回扫历史会话（幂等） |
| POST | `/api/skill-create/verify` | L1+L2 检验 |
| POST | `/api/skill-create/draft` | 候选 → 草稿（过 `skill_create_draft` 闸门） |
| POST | `/api/skill-create/submit` | 提交审核（要求已检验且草稿未被改过） |
| POST | `/api/skill-create/reject` | 否决（进冷却名单，只追加） |
| GET | `/api/skill-create/runs` | 运行痕迹查询 |
| GET | `/api/skill-create/suggest` | 半自动提示：这个会话够不够格封装 |
| POST | `/api/skill-create/trace` | 手工写一条痕迹（测试/演示/补录） |
| GET | `/api/skill-create/stats` | 观测（候选数、通过率、痕迹分布） |
| GET | `/api/skill-create/verify-chain` | 痕迹哈希链校验 |
| GET/POST | `/api/skill-create/config` | 阈值读写 |

---

## 6. 实测结果

**单元测试**：`Ran 37 tests ... OK`（另有模块 3 的 53 个，一起跑 89 个全绿）

**自检脚本**：27 项全过（含 SC-N1~N8 八条反向用例 + 痕迹防篡改 + 无删改接口 + 线上接口）

**真实历史挖掘**（5174 实测）：

```
回扫：21 个会话 → 新记 48 轮
挖掘：48 条痕迹 / 5 个签名 / 2 个候选（都因「只有 1 个会话」被 R1 拦下）
痕迹链：ok=True，48 条记录，0 断链
```

**端到端 HTTP（SC-E2E-01）**：

```
① 注入 3 轮痕迹（2 个会话）
② 挖掘：候选 1 个通过 R1~R6（runs=3 sessions=2 一致率 1.0 评分 0.7067）
④ 出草稿（无 confirm）→ ok=False gate=permission policy=ask   ← 闸门生效
④ 出草稿（confirm）  → skill-6aa705be8a（只有 SKILL.md + fixture.json）
⑤ 检验 → L1=pass L2=True shape_match=1.0 undeclared=[]
⑥ 提交审核 → status=submitted
⑦ 市场状态 → installed=False loadable=False                    ← 没有自动发布
⑨ 痕迹链 → ok=True records=51 broken=[]
```

---

## 7. 第二轮待办（计划里明确排除的）

1. **L3 影子运行**：发布前接一次真实请求，只记录不生效，人工比对；
2. **L4 发布后回归**：每次调用写痕迹，成功率跌破阈值 → 自动提示停用；
3. **半自动提示接进对话**：目前 `/suggest` 只给数据，还没在 `/api/chat` 里真的问出口；
4. **每日定时扫描**：现在要手动调 `/scan`（或由外部计划任务触发）；
5. **意图词典扩充**：目前 7 类意图，靠正则；后续可考虑用本地小模型辅助归类（但要留证据链）；
6. **市场 UI**（仍归 F）：候选看板、证据展示、一键出草稿的页面。
