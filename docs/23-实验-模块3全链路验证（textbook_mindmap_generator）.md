# 实验记录 · 模块3 全链路验证（textbook_mindmap_generator）

> 目的：验证模块3 Skill 市场全链路（L1 静态扫描 → L2 权限校验 → L3 影子运行 → 审核发布 → 会话隔离加载 → L4 健康度）能正常工作。
> 测试 Skill：`textbook_mindmap_generator`（读本地教材库 → 生成章节 Markdown 思维导图）
> 脚本：`.tools\diag\exp-textbook-mindmap.py`（一键跑完并打印全部报告）
> 实测结论：**全链路通过**，技能已发布（版本 1.1.0），真实调用读到教材库第二章 5 个小节。

---

## 1. 复现步骤（照着手动跑）

```powershell
# 0) 前提：网关在跑（模块3 路由 + 定时扫描随它启动）
& "D:\ruanjian\deep code\.tools\start-edu-agent.cmd"

# 1) 一键跑完整个实验（真实市场、真实网关）
cd "D:\ruanjian\deep code\edu-agent"
.\.venv\Scripts\python.exe ..\.tools\diag\exp-textbook-mindmap.py
```

也可以纯 HTTP 复现（等价，逐个接口）：

```powershell
$B = "http://127.0.0.1:5174"
# ① 导入手写 SKILL.md（YAML 块状 front-matter 也认）
Invoke-RestMethod "$B/api/skill-create/import" -Method Post -ContentType 'application/json' `
  -Body (@{ path = "D:\ruanjian\deep code\.tools\diag\exp-skill.md"; confirm = $true } | ConvertTo-Json)
# ② L1 静态扫描
Invoke-RestMethod "$B/api/skill-market/scan" -Method Post -ContentType 'application/json' `
  -Body (@{ id = "textbook_mindmap_generator"; area = "draft" } | ConvertTo-Json)
# ③ L2 校验（candidate_id 或 skill_id 都行）
Invoke-RestMethod "$B/api/skill-create/verify" -Method Post -ContentType 'application/json' `
  -Body (@{ skill_id = "textbook_mindmap_generator" } | ConvertTo-Json)
# ④ L3 影子运行
Invoke-RestMethod "$B/api/skill-create/shadow" -Method Post -ContentType 'application/json' `
  -Body (@{ skill_id = "textbook_mindmap_generator" } | ConvertTo-Json)
# ⑤ 提交 → 审核 → 发布（发布要过 skill_publish 闸门，所以带 confirm）
Invoke-RestMethod "$B/api/skill-create/submit"  -Method Post -ContentType 'application/json' -Body (@{ skill_id="textbook_mindmap_generator"; actor="test_agent" } | ConvertTo-Json)
Invoke-RestMethod "$B/api/skill-market/approve" -Method Post -ContentType 'application/json' -Body (@{ id="textbook_mindmap_generator"; reviewer="G"; note="L1/L2/L3 全过" } | ConvertTo-Json)
Invoke-RestMethod "$B/api/skill-market/publish" -Method Post -ContentType 'application/json' -Body (@{ id="textbook_mindmap_generator"; actor="test_agent"; confirm=$true; level="minor" } | ConvertTo-Json)
# ⑥ 绑定到教师会话（会话级隔离）→ 看 Kernel 里有什么
Invoke-RestMethod "$B/api/skill-market/session/bind" -Method Post -ContentType 'application/json' -Body (@{ session_id="teacher-session-001"; skills=@("textbook_mindmap_generator") } | ConvertTo-Json)
Invoke-RestMethod "$B/api/skill-market/session/teacher-session-001/kernel-catalog"
# ⑦ L4 健康度
Invoke-RestMethod "$B/api/skill-create/health"
```

---

## 2. 解析 SKILL.md 得到的 kernel_function 插件

草稿 front-matter 是 **YAML 块状**格式（`inputs` 是字典列表、`tests` 是字典列表），
模块3 的解析器两种都认：结构化字段进 `inputs_spec` / `inline_tests`，简单字段仍保持单行
（平台的 `capabilities._parse_front_matter` 按行读，所以发布后市场照样能发现它）。

`tests:` 块被转成匿名 fixture：

```json
{
  "input": {"book_id": "math_grade7", "chapter_no": 2},
  "expected_output_shape": {"kind": "markdown", "fields": ["markdown"]},
  "assertions": ["输出必须包含：- 章节思维导图", "不得包含学生姓名/学号/作答明文"],
  "cases": [{"input": {"book_id": "math_grade7", "chapter_no": 2},
             "expect_contains": "- 章节思维导图"}]
}
```

插件源码见 `.tools\diag\exp-plugin.py`（人工编写，SKILL-02 不自动生成代码）。核心：

```python
from typing import Annotated
from semantic_kernel.functions import kernel_function
from edu_agent.skill_market.contract import ModuleClient      # 出站唯一通道


class TextbookMindmapGenerator:
    """按 book_id + chapter_no 生成章节 Markdown 思维导图（只读教材库）。"""

    def __init__(self, client: ModuleClient | None = None) -> None:
        self.client = client or ModuleClient({"platform": "http://127.0.0.1:5174"})

    @kernel_function(name="generate_mindmap",
                     description="读取本地教材库指定课本的某一章，生成该章节的 Markdown 思维导图。")
    def generate_mindmap(
        self,
        book_id: Annotated[str, "教材库里的书本唯一 id"] = "",
        chapter_no: Annotated[int, "章节序号，例如 2 表示第二章"] = 0,
        # ⚠️ SK 的坑：写 `= None` 会被当成必填，可选参数用空字典做默认值
        teaching_context: Annotated[dict, "教学上下文"] = {},
    ) -> dict:
        # 只做调用封装：读平台**已有**的只读教材接口（= manifest 里声明的 textbook:read）
        info = self.client.get("platform", "/api/textbook")
        res = self.client.get("platform", "/api/textbook/chapters")
        chapters = list((res or {}).get("chapters") or [])
        ...
        return {"ok": True, "markdown": md, "book": book, "chapter": chapter,
                "chapter_no": no, "title": title, "sections": len(sections),
                "source": "textbook:read", "calls": self.client.summary()}
```

**约束遵守**：不解析 PDF、不读 `content/` 目录，只调 `/api/textbook*`（`textbook:read`）；
出站只走 `ModuleClient`（参数过隐私/白名单闸门）；只读、无写文件、无执行外部程序、无公网。

---

## 3. L1 / L2 / L3 校验输出样例

### L1 静态权限扫描（`perm_scan`）

```
verdict = pass   ok = True
blocking = []
warnings = [
  {"level":"warn","code":"api.local_url.declared",
   "message":"出现本机模块 API 地址（已声明对应能力，放行）","where":"plugin.py"},
  {"level":"warn","code":"api.module_client.declared",
   "message":"使用跨模块客户端 ModuleClient（已声明对应能力，放行）","where":"plugin.py"}
]
declared.capabilities = {"textbook:read": "读本地教材库（只读）：/api/textbook、/api/textbook/chapters、…"}
declared.data_scope   = {"teaching_context": "教学上下文（学段、学科、章节、课时）"}
gated_actions = []
stats = {'body_chars': 84, 'files_scanned': 1, 'blocking': 0, 'warnings': 2, 'for_publish': False}
```

> **内网白名单**：`127.0.0.1` 的调用算 `module_api` 而不是 `network`，
> 声明了 `textbook:read` 就放行（否则所有调平台接口的插件都会被误拦）。

### L2 校验（能力声明必须映射到真实能力）

```
ok = True   state = verified
L1 = {"verdict": "pass", "ok": true, "blocking": [], "gated_actions": []}
L2 = {
  "ok": true, "method": "声明式回放（不执行草稿代码）",
  "declared_calls": ["textbook:read"], "actual_calls": [], "undeclared_calls": [],
  "expected_shape": ["markdown"], "recorded_shape": [], "shape_match": 1.0,
  "side_effects": "none"
}
能力令牌逐条解析：
  textbook:read    → 存在  读本地教材库（只读）：/api/textbook、/api/textbook/chapters、…
  teaching_context → 合法  教学上下文（学段、学科、章节、课时）
```

### L3 影子运行（`shadow.py`：子进程真跑，出站全被记录桩拦下）

```
ok = True   影子运行通过：真代码跑通、返回形状一致、出站调用都被拦下
{
  "executed": true, "ok": true, "ms": 5719, "exit_code": 0,
  "actual_fields": ["chapter", "markdown", "ok", "sections", "title"],
  "expected_fields": ["markdown"], "matched": ["markdown"], "shape_match": 1.0,
  "outbound_attempts": ["http://127.0.0.1:5174/api/textbook",
                        "http://127.0.0.1:5174/api/textbook/chapters"],
  "allowed_targets": ["platform"],
  "undeclared_targets": [],
  "kwargs_used": ["book_id", "chapter_no"],
  "plugin_class": "TextbookMindmapGenerator",
  "note": "出站调用已被记录桩拦截：未发网络、未写其它模块"
}
```

> 全程**绝对路径**（`runner.resolve()` / `plugin.resolve()` / `fixture.resolve()`），
> cwd 设成草稿目录也不会找不到脚本；出站目标由**能力令牌驱动**判断，不硬编码 module2/module4。

---

## 4. 模拟人工审核发布（状态变更）

```python
from edu_agent.skill_market import lifecycle, store

SKILL_ID = "textbook_mindmap_generator"

# ① 提交审核（要求已通过 L1+L2+L3；未检验会被 gate=verify/shadow 拦下）
sub = lifecycle.submit(SKILL_ID, "test_agent")
# → {'ok': True, 'status': 'submitted', 'version': '1.0.0'}

# ② 审核通过（审核人不能等于 owner —— 职责分离；owner 是 test_agent）
apr = lifecycle.approve(SKILL_ID, "G", note="L1/L2/L3 全过，教材库只读，同意发布")
# → {'ok': True, 'status': 'approved'}

# ③ 发布（过 permissions.guard("skill_publish")；policy=ask 时带 confirm 即通过）
pub = lifecycle.publish(SKILL_ID, "test_agent", confirm=True, level="minor")
# → {'ok': True, 'status': 'published', 'version': '1.1.0',
#    'installed_to': '...\\data\\skills\\textbook_mindmap_generator'}

# ④ 状态确认
lifecycle.status(SKILL_ID)
# → {'status': 'published', 'version': '1.1.0', 'installed': True,
#    'loadable': True, 'loadable_reason': '可加载', 'versions': [...]}
```

**已发布后想改**（不能直接改文件再提交）：

```python
lifecycle.new_draft(SKILL_ID, "test_agent", note="改一版")
# → status 回到 draft，**线上那版继续服务**；改完再走 submit → approve → publish
```

---

## 5. 教师工作台（模块4）调用示例

```python
from edu_agent.skill_market import isolation, kernel_loader, contract

# ① 会话隔离：只把这个技能绑到该教师的会话（单会话上限 8，别的会话拿不到）
isolation.bind("teacher-session-001", ["textbook_mindmap_generator"])
isolation.assert_loadable("teacher-session-002", "textbook_mindmap_generator")
# → (False, '该技能没有绑定到本会话（会话级隔离：不同会话不共享 plugin 集合）')

# ② 为该会话建 Kernel（每个会话一个 Kernel，只装它绑定的技能）
built = kernel_loader.build_kernel("teacher-session-001")     # 真 Semantic Kernel
# → loaded=['textbook_mindmap_generator']  failed=[]

# ③ 调用（模块4 传 teaching_context）
out = kernel_loader.invoke(built["kernel"], "textbook_mindmap_generator", "generate_mindmap",
                           book_id="math_grade7", chapter_no=2,
                           teaching_context={"stage": "高中", "subject": "数学"})
# → ok=True  book=必修第一册  chapter=第二章
#    title=一元二次函数、方程和不等式  sections=5  book_matched=False
#    （book_id 匹配不到就用默认课本，并如实标注 book_matched=False）

# ④ 结果作为卡片推给模块4（只传标识 + 卡片内容，隐私闸门拦学生数据）
ctx = contract.TeachingContext(knowledge_id="K-函数-02", stage="高中", subject="数学",
                               teaching_context={"chapter": "第二章 一元二次函数、方程和不等式"})
client = contract.ModuleClient({"module4": "http://127.0.0.1:5175"})
client.emit_workbench_card(ctx, {"kind": "mindmap_card",
                                 "title": "%s 章节思维导图" % out["value"]["chapter"],
                                 "summary": "共 %s 个小节" % out["value"]["sections"],
                                 "markdown": out["value"]["markdown"]})
# 实际请求：POST http://127.0.0.1:5175/api/workbench/card
# payload = {knowledge_id, teaching_context, stage, subject, card{...}}
# 塞 student_answer 会被拦：guard_payload → 载荷含禁止键 'student_answer'
```

真实产出的思维导图：

```markdown
- 章节思维导图
  - 必修第一册 · 第二章 一元二次函数、方程和不等式
    - 2.1 等式性质与不等式性质（p37）
    - 2.2 基本不等式（p44）
    - 2.3 二次函数与一元二次方程、不等式（p50）
    -  小结（p56）
    -  复习参考题2（p57）
```

---

## 6. L4 health 监控输出样例

```
失败调用：{"ts":"2026-10-01T18:38:46+0800","kind":"skill-run",
          "skill":"textbook_mindmap_generator","ok":false,"ms":1800,
          "error":"教材库接口 503：chapters 暂不可用","seq":67,"prev_hash":"4ebb…"}
成功调用：{"ts":"2026-10-01T18:38:46+0800","kind":"skill-run",
          "skill":"textbook_mindmap_generator","ok":true,"ms":420,"seq":68,"prev_hash":"7e16…"}
```

```json
{
  "ok": true,
  "attention_count": 1,
  "hint": "有 1 个技能成功率偏低：建议在「权限」里停用或回滚，系统不会自动停用它们",
  "config": {"health_min_calls": 5, "health_min_ok_rate": 0.8, "health_window_days": 30},
  "attention": [{
    "skill": "textbook_mindmap_generator",
    "calls": 34, "ok": 25, "failed": 9, "ok_rate": 0.7353,
    "latency_p50_ms": 420, "latency_p95_ms": 2150,
    "last_error": "教材库接口 503：chapters 暂不可用",
    "last_call": "2026-10-01T18:40:01+0800",
    "trend": -0.1765, "attention": true,
    "reason": "成功率 74% 低于阈值 80%",
    "installed": true
  }]
}
```

**只告警、不停用**：`installed=true`（技能仍在运行时目录）。要停用必须走人工闸门：

```powershell
Invoke-RestMethod "$B/api/skill-market/disable" -Method Post -ContentType 'application/json' `
  -Body (@{ id="textbook_mindmap_generator"; reason="接口不稳定"; confirm=$true } | ConvertTo-Json)
```

---

## 7. 审计与运行痕迹（脱敏 / 只追加）

```
审计轨迹（该技能，新→旧）：
  skill.publish        approved→published  {"ok": true, "code": "published", "installed_to": "…"}
  skill.approve        submitted→approved  {"ok": true, "code": "approved"}
  skill.submit         draft→submitted     {"ok": true, "code": "submitted"}
  skill-create.shadow  →                   {"ok": true, "code": "shadow", "shape_match": 1.0, "undeclared": []}
  skill-create.verify  →                   {"ok": true, "L1": "pass", "L2": true}
  skill-create.import  →                   {"ok": true, "code": "imported"}
  skill.new_draft      published→draft     {"ok": true, "code": "draft"}

审计链校验：{'ok': True, 'files': 1, 'records': 28, 'broken': []}
运行痕迹链：{'ok': True, 'files': 1, 'records': 54, 'broken': []}
审计文件隐私明文扫描（手机/邮箱/身份证）：0 命中
```

审计与运行痕迹都是 **`open(path, "a")` 只追加 + `prev_hash/hash` 哈希链**，
没有 update/delete 接口；`verify_chain()` 手改一行就能指出断在哪条。

---

## 8. 这次实验顺带修掉的 5 个真问题

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 9 | 你的草稿**根本进不了流水线** | front-matter 是 YAML 块状（`inputs`/`tests` 是字典列表），而解析器只按行读 | 加 YAML 解析（PyYAML 可选依赖，缺了就退回逐行）；结构化字段进 `inputs_spec`/`inline_tests`，简单字段仍单行保持兼容；没有 `name` 时从 H1 标题兜出来 |
| 10 | `textbook:read` 被判"未知能力令牌" | 白名单里没有平台只读能力 | 新增 `PLATFORM_CAPABILITIES` 注册 `textbook:read`，并把它算进 `module_api` 的允许声明 |
| 11 | **手写草稿没法检验** | `verifier.verify()` 只认 miner 生成的候选 | 加 `resolve_target()`：候选 id 与技能 id 都支持；状态分别记在候选 / 市场 state.json |
| 12 | L3 误判"未声明出站目标" | `shadow.py` 硬编码 module2/module4，不认识平台接口 | 出站目标改由**能力令牌驱动**（`CAPABILITY_TARGETS` + 路径前缀表 `PATH_TARGETS`） |
| 13 | 重复发布同一版本崩 | `store.write_version()` 里写了未定义的 `version`（只在版本目录已存在时触发） | 改成 `man.version` + 时间戳后缀留档；补回归测试 |
| 14 | `verification_state` 静默放行（**最危险**） | `mf` 没导入 → `NameError` 被 `except Exception: pass` 吞掉，L3 闸门被跳过 | 补导入；并把"判不出来"改成 **fail-closed**（返回 `gate=shadow-check` 拒绝） |

另外两个坑是**插件侧**的（不是平台 bug，但值得记）：

* **SK 把 `= None` 当必填**：`teaching_context: Annotated[dict, "…"] = None` →
  SK 的参数解析只看"默认值是不是 None"，于是它成了必填项，调用报
  `Parameter teaching_context is required`。可选参数要用 `= {}` / `= ""`。
* **不要在 `sort` 的 key 里调 `list.index()`**：排序过程中列表在变，会抛
  `ValueError: … is not in list`（我第一版插件就这么写的）。

---

## 9. 约束逐条对照

| 约束 | 落实 | 证据 |
|---|---|---|
| L3 全用绝对路径 | `runner/plugin/fixture` 全部 `.resolve()` | §3 L3 报告 `exit_code: 0` |
| 127.0.0.1 白名单不被网络扫描误拦 | `module_api` 风险类 + 路径前缀表 | §3 L1 `warnings`（declared 放行） |
| L4 只告警、禁止自动停用 | `health.py` 只返回 `attention`；停用走 `skill_disable` 闸门 | §6 `installed: true` |
| 审计脱敏、只追加、不可改删 | `audit.chain_append()` + `redact()`；无删改接口 | §7 链校验 + 0 命中 |
| 复用 `skills_local` / `capabilities` / `permissions`，不写 UI | 运行时目录 = `skills_local.skills_dir()`；能力白名单现算自 `capabilities`；发布走 `permissions` 闸门；模块3 全部端点只返回 JSON | 代码路径 |
| 会话隔离、不全局加载 | 每会话一个 Kernel + `bind()` 显式绑定 | §5 `teacher-session-002` 被拒 |
| 不自己实现教材解析 | 插件只调 `/api/textbook*` | §5 `calls: 2 → /api/textbook, /api/textbook/chapters` |

---

## 10. 清理（实验做完想撤下技能）

```powershell
# 停用（撤出运行时目录，版本保留、可再发布回来）
Invoke-RestMethod "$B/api/skill-market/disable" -Method Post -ContentType 'application/json' `
  -Body (@{ id="textbook_mindmap_generator"; confirm=$true } | ConvertTo-Json)
# 解绑会话
Invoke-RestMethod "$B/api/skill-market/session/unbind" -Method Post -ContentType 'application/json' `
  -Body (@{ session_id="teacher-session-001"; skill="textbook_mindmap_generator" } | ConvertTo-Json)
```
