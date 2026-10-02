# 验收测试报告 · 教材章节思维导图 Skill（textbook-mindmap）

| 项 | 内容 |
|---|---|
| 被测对象 | `SKILL.md`（市场清单）+ `TextbookMindMapSkill.py`（SK 插件） |
| 被测路径 | `edu-agent/data/skill_market/textbook-mindmap/` |
| 测试环境 | edu-agent `:5174`（教学平台）· 隔离市场目录 · Python 3.11 + Semantic Kernel |
| 测试方法 | 真实解析器 + 真实流水线 + 真实内核调用（非纸面推演） |
| 测试脚本 | `.tools/diag/acceptance-textbook-mindmap.py`（全流程）· `.tools/diag/edgecases-textbook-mindmap.py`（边界矩阵） |
| 结论 | **修正版 SKILL.md 全流程通过（L1/L2/L3 全绿 + 端到端出图）**；原始 `SKILL.md.txt` **完全不可用**；发现 5 项缺陷（2 项阻断级、3 项高危静默级） |

---

## 一、SKILL.md 静态校验

### 1.1 原始文件（`SKILL.md.txt`）—— ❌ 不可用

| 检查项 | 实测 | 判定 |
|---|---|---|
| 文件扩展名 | `SKILL.md.txt` | ❌ **市场扫描完全看不见它**（见缺陷 D1） |
| front-matter 起始 `---` | 首行为 `' '`（空格） | ❌ 无 |
| front-matter 结束 `\n---` | 不存在 | ❌ 无 |
| 列表符号 | 含 `•` ×5、`◦` ×2 | ❌ 被富文本渲染器改写 |
| markdown 标题 `#` | 0 个 | ❌ 被剥离 |
| 缩进 | 全部丢失 | ❌ |
| 解析出的 `name` | `''` | ❌ |

**解析器实测输出（6 项校验全挂）**：

```
✗ name 必须是 [a-z0-9][a-z0-9_-]{1,47}（当前 ''）
✗ description 不能为空（Skill 市场靠它做检索与展示）
✗ owner 不能为空（发布/回滚要有人负责）
✗ allowed_capabilities 不能为空（禁止声明「隐式全权限」）
✗ data_scope 不能为空（必须显式说明能碰哪些标识）
✗ tests 不能为空（验收要求：必须有匿名 fixture 测试）
```

> **根因**：文件经过了"Markdown 渲染 → 复制 → 粘贴"链路，front-matter 分隔符与结构被吃掉。
> 即使把名字改回 `SKILL.md`，这 6 项校验仍会拒绝它。

### 1.2 修正版（已交付 `SKILL.md`）—— ✅ 全部通过

| 字段 | 值 | 核对 |
|---|---|---|
| `name` | `textbook-mindmap` | ✅ 符合 `^[a-z0-9][a-z0-9_-]{1,47}$` |
| `title` / `category` / `icon` | 教材章节思维导图 / 教学 / M | ✅ 展示字段 |
| `version` / `status` / `owner` | `1.0.0` / `draft` / `test_agent` | ✅ 必填齐全 |
| `plugin` | `TextbookMindMapSkill.py` | ✅ 与目录内实际文件名**逐字一致**，文件存在 |
| `inputs` | `book_id`、`chapter_no` | ✅ 与函数签名 `(book_id, chapter_no)` 一致 |
| `outputs` | `ok, markdown` | ✅ 与返回字典键一致 |
| `allowed_capabilities` | `["textbook:read"]` | ✅ 在能力白名单里可解析（读本地教材库，只读） |
| `data_scope` | `["teaching_context"]` | ✅ 合法作用域 |
| `tests` | inline 1 条（`expect_contains: "- 章节思维导图"`、`expect_fields: [ok, markdown]`） | ✅ 非空 |
| `errors()` | **无** | ✅ 格式合法 |

### 1.3 plugin 文件名 / 函数名匹配核对（重点）

```
SKILL.md  plugin 字段          : TextbookMindMapSkill.py
目录内实际文件                 : TextbookMindMapSkill.py      ✅ 一致
AST 静态发现（不执行代码）      : class=TextbookMindMapSkill
                                function=generate_mindmap
                                params=['book_id', 'chapter_no']
                                async=false   kernel_function_count=1
Python 里的装饰器              : @kernel_function(name="generate_mindmap", ...)   ✅ 一致
SK 插件名转换                  : textbook-mindmap → textbook_mindmap
                                （SK 要求 ^[0-9A-Za-z_]+$，连字符必须转，平台已自动处理）
```

> ⚠️ **纠正一个常见误解**：`SKILL.md` **没有 `function_name` 字段**。
> 函数名由**调用请求**给出：`POST /api/skill-market/kernel/invoke`
> body = `{"session_id": "...", "skill": "textbook-mindmap", "function": "generate_mindmap", "args": {...}}`。
> 平台另有 `kernel_loader.describe_plugin_file()` 用 AST 静态列出函数（不执行代码），供市场详情页/审核页展示。
> 因此"函数名严格匹配"要核对的是 **`@kernel_function(name=...)` ↔ 调用方传的 `function` 值**，
> 而不是 SKILL.md 里的某个字段（该字段不存在，写了也会被忽略）。

---

## 二、模块3 完整流程测试（实测证据）

### ① 加载/启动阶段

**先纠正一个预期**：模块3 **不在项目启动时扫描市场**（`web_server` 启动路径里没有
`market_index()` / `list_area()` / `discover_skills()` 调用）。启动只做路由挂载；
扫描与插件加载是**按需**发生的（市场列表接口 / 会话初始化 / 发布时）。

| 测试项 | 预期结果 | 实测结果 |
|---|---|---|
| 启动挂载路由 | 4 个路由组挂载成功 | ✅ `/api/skill-market/*`、`/api/skill-create/*`、`/api/skill/*`、`/api/skill-studio/*` |
| 启动扫描市场 | **不发生**（设计如此） | ✅ 确认无启动期扫描 |
| 技能目录可发现 | 目录内 `SKILL.md` + 插件文件 | ✅ `['SKILL.md','SKILL.md.txt','TextbookMindMapSkill.py']` |
| SKILL.md 解析 | 字段齐全、errors 为空 | ✅ 见 1.2 |
| 插件函数发现 | 1 个 kernel function | ✅ `generate_mindmap` |

**预期日志（成功路径）**：
```
[INFO] 模块3 路由已挂载：/api/skill-market/* /api/skill-create/* /api/skill/* /api/skill-studio/*
[INFO] skill-create 调度器已启动（扫描间隔 24h，失败静默不阻塞回答）
```

**预期日志（SKILL.md 损坏时）**：
```
[WARN] 技能 textbook-mindmap 解析失败：name 必须匹配 ^[a-z0-9][a-z0-9_-]{1,47}$（当前 ''）
[WARN] 已跳过该技能（不中断服务）
```
**预期日志（文件名是 `SKILL.md.txt` 时）**：❗ **什么都没有** —— 扫描器根本不看它（最危险的情况）。

### ② 提交审核（draft → 待审）

> ⚠️ **命名纠正**：实现里待审状态叫 **`submitted`**，不是 `pending`。状态机取值：
> `draft → submitted → approved → published → disabled → rolled_back`。

| 步骤 | 预期 | 实测 |
|---|---|---|
| `import_draft`（导入 SKILL.md） | ok，进入草稿区 | ✅ `id=textbook-mindmap`，`market/drafts/textbook-mindmap/` |
| 补插件文件 | 插件需人工放入草稿目录 | ⚠️ `import_draft` **只吃 SKILL.md 文本**，插件须单独拷贝（见 D10） |
| `validate_draft`（结构校验） | problems 为空 | ✅ `problems=无`，`content_hash=sha256:45a84d2e…` |
| `submit`（三重校验） | L1+L2+L3 全过才能进待审 | ✅ `gates={'L1':'pass','L2':'pass','L3':'pass'}` |
| — L1 静态权限扫描 | pass（只声明 textbook:read） | ✅ `verdict=pass` |
| — L2 manifest 权限校验 | 声明式回放，无未声明调用 | ✅ `declared_calls=['textbook:read']`，`undeclared_calls=[]` |
| — L3 影子运行 | 子进程真跑 + 出站拦截 + 形状一致 | ✅ `executed=true`，`shape_match=1.0`，`actual/expected=['markdown','ok']/['ok','markdown']`，出站被拦：`http://127.0.0.1:5174/api/textbook/chapters` |
| 状态变更 | `draft → submitted` | ✅ `effective_status=submitted` |

**提交逻辑判定**：✅ 正确。三重校验**做在 submit 内部**，任何调用方、任何模式（单用户/多教师）都绕不过。

### ③ 审核 → 发布

| 步骤 | 预期 | 实测 |
|---|---|---|
| `approve`（审核通过） | 写批准记录并**绑定内容哈希** | ✅ `hash=sha256:45a84d2e…`，`hash_matches=true`（改内容即失效） |
| 状态变更 | `submitted → approved` | ✅ |
| `install/publish` | 五条闸门全过 → 复制落盘 + 改状态 | ✅ `installed_to=runtime/textbook-mindmap` |
| 状态变更 | `approved → published` | ✅ `effective_status=published` |
| 可加载性 | 可被会话加载 | ✅ `(True, '可加载')` |
| 注册表同步 | `status=published, installed=1` | ✅ |

**发布逻辑判定**：✅ 正确。publish 只做"完整复制落盘 + 改状态"，**不碰内核、不调 `isolation.bind`**（有回归测试钉死）。

### ④ 端到端调用

调用链：`isolation.assert_loadable` → `kernel_loader.build_kernel(session)` → `kernel_loader.invoke(kernel, skill, function, **args)`

| 用例 | 输入 | 预期 | 实测 |
|---|---|---|---|
| 会话加载 | 单用户模式 | 自动加载全部 published | ✅ `['textbook-mindmap']` |
| 建内核 | — | 装入该会话绑定且可加载的技能 | ✅ `funcs=['generate_mindmap']` |
| **A 正例** | `function=generate_mindmap`, `book_id="math_junior_1"`, `chapter_no=2` | `{"ok":true,"markdown":"- 章节思维导图…"}` | ✅ 返回键 `['markdown','ok']`，正文： |
| **B 反例** | 参数名写成 `chapter_num=2` | 期望报错 | ❌ **不报错**，返回 **第一章**（见 D3） |
| **C 反例** | 函数名写成 `mindmap` | 期望报错 | ✅ `KernelFunctionNotFoundError: Function 'mindmap' not found in plugin 'textbook_mindmap'` |

**A 正例的真实返回**（`book_id="math_junior_1"`, `chapter_no=2`）：

```markdown
- 章节思维导图
  - 必修第一册 · 第二章 一元二次函数、方程和不等式
    - 2.1 等式性质与不等式性质（p37）
    - 2.2 基本不等式（p44）
    - 2.3 二次函数与一元二次方程、不等式（p50）
    - 小结（p56）
    - 复习参考题2（p57）
```

返回格式检查：✅ `dict`、键为 `ok`/`markdown`、`ok=True`、`markdown` 首行为 `- 章节思维导图`、含小节与页码。

> 说明：`book_id="math_junior_1"` 在教材库里**匹配不到**（库里只有《必修第一册》），
> 平台按设计回退到默认课本 —— 这是正常的降级，但**调用方拿不到"已回退"的信号**（见 D4）。

---

## 三、边界与反例矩阵（11 例）

| # | 用例 | ok | 实际取到的章节 | 判定 |
|---|---|---|---|---|
| 1 | `chapter_no=2` | ✅ True | 第二章 一元二次函数、方程和不等式 | 正常 |
| 2 | **`chapter_num=2`（参数名错）** | ⚠️ **True** | **第一章** 集合与常用逻辑用语 | ❌ **静默给错结果** |
| 3 | 不传章节号 | ⚠️ True | 第一章（说明：未找到第 ? 章） | ⚠️ 静默回退 |
| 4 | `chapter_no=0` | ⚠️ True | 第一章（说明：未找到第 ? 章） | ⚠️ 静默回退 |
| 5 | `chapter_no="abc"` | ✅ **False** | — | ⚠️ SK 类型转换层拦截（插件兜不住，见 D6） |
| 6 | `chapter_no=-1` | ⚠️ True | 第一章（说明：未找到第 -1 章） | ⚠️ 静默回退 |
| 7 | `chapter_no=99` | ⚠️ True | 第一章（说明：未找到第 99 章） | ⚠️ 静默回退 |
| 8 | `book_id="no_such_book_xyz"` | ⚠️ True | 第二章（**无任何提示**） | ❌ 静默换课本 |
| 9 | `book_id=""` | ⚠️ True | 第二章 | 同上 |
| 10 | `chapter_no="2"`（字符串数字） | ✅ True | 第二章 | SK 自动转换成功 |
| 11 | `chapter_no=2.9`（浮点） | ✅ True | 第二章 | 截断为 2 |

---

## 四、缺陷清单

### D1 —— `SKILL.md.txt` 扩展名导致技能"完全不存在" 🔴 P0 阻断

**现象**：市场扫描器只认 `SKILL.md`。实测：
```
store.find_skill('only-txt-skill') → None
list_area('draft')                 → []
market_index()                     → []
```
**影响**：文件放进去但市场里看不到、也不报错 —— 排查成本极高（"我明明放了文件"）。
**修复**：重命名为 `SKILL.md`（本次已交付修正版）。

### D2 —— front-matter 分隔符丢失导致 6 项校验全挂 🔴 P0 阻断

**现象**：无 `---` → 所有字段读空 → `name`/`description`/`owner`/`allowed_capabilities`/`data_scope`/`tests` 六项报错。
**修复**：用纯文本编辑器保存，确保首行是 `---`、结尾有 `---`；列表用 `- ` 而非 `•`。

### D3 —— 参数名写错会静默返回**错误的章节** 🟠 P1 高危

**现象**：`chapter_num=2` → SK **忽略未知关键字**、`chapter_no` 取默认值 0 → 回退到第一章，
返回 `ok=True` + 一份看起来完全正常的导图。
**影响**：调用方（尤其 AI Agent）无法区分"第二章"和"第一章"，教学场景直接讲错内容。
**修复（择一或并用）**：
1. **文档层（已做）**：SKILL.md 的 `inputs` 描述里注明"参数名是 `chapter_no`，不是 `chapter_num`"；
2. **代码层（推荐）**：给插件加别名参数，把静默错误变成正确行为：
   ```python
   chapter_num: Annotated[int, "chapter_no 的别名（兼容调用方写法）"] = 0,
   # 函数体里：no = int(chapter_no or chapter_num or 0)
   ```
3. 调用侧统一传 `chapter_no`。

### D4 —— `book_id` 未匹配时静默换课本，且信号被丢弃 🟠 P1 高危

**现象**：`_pick_book()` 算出了 `matched=False`，但 `_render_mindmap()` 调用时**没有把它传进去**，
而返回字典按契约只有 `{ok, markdown}` —— 于是"用了别的课本"这件事**完全没有出口**。
**影响**：教师指定初一教材却拿到高中必修一，界面上看不出异常。
**修复**：未匹配时在 markdown 里补一行说明（不破坏 `{ok, markdown}` 契约）：
```
  - 说明：book_id "math_junior_1" 未匹配到课本，已使用《必修第一册》
```

### D5 —— 章节号非法一律静默回退，提示文案是"第 ? 章" 🟡 P2

**现象**：`chapter_no=0` → 说明写作"未找到第 **?** 章，已回退到第一章"（`no or "?"`）。
**修复**：文案改为"未指定章节号 / 章节号非法，已回退到第一章"。

### D6 —— "永不抛异常"的契约不成立 🟡 P2

**现象**：`chapter_no="abc"` 时，SK 在**参数类型转换阶段**就失败：
```
Function failed. Error: Parameter chapter_no is expected to be parsed to <class 'int'> but is not.
→ kernel_loader 捕获后返回 ok=False + KernelInvokeException
```
插件里的 `try: int(chapter_no)` **根本没机会执行**。
**影响**：不会崩溃（平台捕获了），但"任何失败都返回 `{ok:False, markdown:""}`"这条注释与实际不符 —— 实际是返回 `ok=False` 且**没有 markdown 键**。
**修复**：把注释改成准确表述，或在平台调用层统一把参数按签名类型做一次前置校验/转换。

### D7 —— 观察项：平台 MCP 调用在失败时抛 RuntimeWarning 🟡 P2（非本技能缺陷）

`mcp_tools.py:192`：
```python
return asyncio.run(asyncio.wait_for(_call_once(server_id, name, args or {}), timeout))
```
MCP 不可用时产生 `RuntimeWarning: coroutine 'wait_for' was never awaited`（返回值仍是 None，功能正常）。
**建议**：改成 `asyncio.run(_run())` 且 `_run` 内 `await asyncio.wait_for(...)`，消除噪声。

### D8 —— 流程摩擦：`import_draft` 只吃 SKILL.md，插件要人工补 🟡 P2

**现象**：导入后草稿目录只有 `SKILL.md` + `fixture.json`，必须再手动拷贝 `.py`，否则技能会以
**纯提示词技能**身份通过（L3 记 `skipped:no-plugin`），代码永远不执行。
**建议**：导入接口支持"整个文件夹"导入，或在 `validate_draft` 里对 `plugin` 字段声明了文件却不存在的情况**报错**（当前 `plugin` 为空才 skip，声明了却缺失会在 L3 阶段暴露，但提示可以更前置）。

---

## 五、答辩要点（可能被问到的问题）

**Q1：三重校验分别在防什么？**
L1 静态权限扫描（能力白名单/网络/文件/执行/越界库表/语义）防"偷偷联网或读不该读的"；
L2 manifest 校验（声明式回放：实际调用 ⊆ 声明能力）防"说的和做的不是一回事"；
L3 影子运行（子进程真跑 + 出站记录桩 + fixture 断言）防"纸面看着对、一跑就崩"。

**Q2：凭据/密钥会被技能读走吗？**
不会。技能只声明 `textbook:read`；出站调用统一走 `contract.ModuleClient`，参数过
`guard_payload()` 脱敏与键白名单；L3 影子运行把所有出站替换成记录桩，实测输出里能看到
`outbound_attempts` 只有被拦下的 URL。

**Q3：发布后改内容会怎样？**
批准时绑定 `content_hash`（正文 + 全部资源文件，排除 fixture 与 `__pycache__`）；
任何内容改动都会让哈希失配 → 安装接口以 `gate=stale` 拒绝，必须重新批准。

**Q4：为什么 `SKILL.md.txt` 这种错误在市场里不报警？**
这是本次发现的缺陷 D1：扫描器只匹配 `SKILL.md` 文件名，非该名字的文件不进入市场、也不产生日志。
建议在导入接口/前端加一道文件名与 front-matter 的前置校验。

**Q5：单用户模式会影响校验强度吗？**
不会。`SINGLE_USER_MODE` 只影响**会话加载**与**市场可见性**两处；
三重校验做在 `submit()` 内部，两种模式都要过（有专门的回归测试在 True/False 下各测一遍）。

---

## 六、结论

| 维度 | 结论 |
|---|---|
| SKILL.md 格式 | 修正版 ✅ 全绿；原始 `SKILL.md.txt` ❌ 6 项校验全挂且文件根本不可见 |
| plugin / 函数名匹配 | ✅ 严格一致（`TextbookMindMapSkill.py` ↔ `@kernel_function(name="generate_mindmap")`） |
| inputs / outputs / 能力声明 | ✅ 与签名、返回值、白名单一致；最小权限（只读） |
| 三重校验 | ✅ L1/L2/L3 全过，L3 形状 1.0、出站全被拦 |
| 状态机 | ✅ `draft → submitted → approved → published` 流转正确（注意不是 `pending`） |
| 端到端 | ✅ 正确参数下返回 `{ok:true, markdown}`，内容为第二章 5 小节带页码 |
| 上线建议 | **修正 SKILL.md 后可发布**；建议同时修 D3/D4（静默给错结果的两个高危项） |

**测试脚本**（可复现）：
```powershell
cd D:\ruanjian\deep code\edu-agent
.\.venv\Scripts\python.exe ..\.tools\diag\acceptance-textbook-mindmap.py   # 全流程
.\.venv\Scripts\python.exe ..\.tools\diag\edgecases-textbook-mindmap.py    # 边界矩阵
```
