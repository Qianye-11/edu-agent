# PPT 优化与幻灯片编辑（计划 + 落地）

> **本文由原 19 号《边框里的 PPT 优化计划书》、20 号《PPT 优化》落地说明、21 号《幻灯片可编辑 + 可导入模板计划书》合并而成**（2026-09-25）。
> 三份讲的是同一条线：**面板里的 PPT**。合并后：§1~§7 是"已有 .pptx 的优化"（**已交付**），§8~§12 是"幻灯片页签做成编辑器 + 模板"（**下一步**），§13~§17 是验收/风险/分期/待定。
>
> 相关：`docs/14-会话内文档预览.md`（面板与四页签）、`docs/22-预览版式与导出规格.md`（**版式与导出**的规格：数值、路线、指标闸门）。

---

## 0. 一页速览

| 问题 | 结论 |
|---|---|
| 选哪个开源方案做"读旧稿→美化"？ | **主选 `hugohe3/ppt-master`（MIT · Python-only）** —— 五个候选里唯一同时满足「读已有 .pptx」「母版/版式级全局美化」「非破坏性」「原生可编辑输出」四条硬要求的（§1） |
| 照搬它吗？ | **不整套照搬**。它是给强模型（Kimi K3 / Claude，~1M 上下文）用的 skill 工作流；我们只取**确定性脚本链**，AI 决策留到三期 |
| 一期实际交付了什么？ | **体检 / 逐页台账 / 内容零丢失校验（用它的脚本）+ 保守原地统一（我们自己写的 python-pptx）**；入口在面板「文档」视图 →【幻灯片】页签；skill `ppt-polish` 默认关（§3、§6） |
| 为什么不做"整套重排"？ | 它的 `beautify-pptx` 档明确**"不是补丁"**：文字逐字冻结 + 版式用 SVG 管线**重做**，那是**模型逐页手写 SVG** 的活，我们默认模型喂不饱（§2） |
| 下一步做什么？ | **幻灯片页签做成面板内编辑器**（版式 6 个 + 就地改）+ **主题做成可导入模板** + **会话内容替换模板文字**（§8~§12） |
| 版式"不好看"怎么办？ | 规格与数值在 `docs/22`（tokens 单一数值源 + SVG 单源渲染与导出 + 四项可验证指标） |

---

## 1. 五个候选逐项判定（为什么是 ppt-master）

| 候选 | 关键事实（已核实） | 许可证 | 能读旧 pptx？ | 判定 |
|---|---|---|---|---|
| **ppt-master**<br>`hugohe3/ppt-master` | 产出**原生 DrawingML**（真母版/版式继承、原生形状/连接线/图表/表格、OMML 公式、备注/动画/旁白）；三条附加路线：**从参考稿蒸馏模板**、**把新内容填进已有 .pptx 且保留设计**、**给成品加转场/动画/旁白**；`skills/ppt-master/scripts/` 是**确定性 CLI**（`pptx_intake.py` / `pptx_to_svg.py` / `pptx_template_import.py` / `svg_quality_checker.py` / `pptx_transitions.py` / `pptx_animations.py` / `svg_to_pptx.py` / `pptx_delivery_check.py`）；MIT；Python 3.10+ 即可 | **MIT** ✅ | ✅（"Edit Native PPTX owns source-preserving existing-deck edits"，未改动页**逐字节保留**） | **主选** |
| anthropics/skills | Claude 官方 skill 规范；含 `pptx` 子技能（python-pptx 级读写、排版检查规则） | 官方仓库 | 部分（OOXML 读写原语） | **参考**（规范与范式） |
| guizang-ppt-skill（归藏 PPT） | 单文件 HTML 幻灯片（瑞士网格 / 杂志风），可导出 pptx；**不能读已有 pptx** | 见仓库 | ❌ | **只借版式/校验清单** |
| dashi-ppt-skill | HTML 预览 + 一键导出 pptx；上千套版式；**不能读旧 pptx** | **AGPL-3.0** ❌ | ❌ | **不采用**（许可传染） |
| GordenPPTSkill | 21 套中文模板；**只换文字、不动排版**；渲染依赖 **LibreOffice + pdftoppm**；首次使用强制联网自更新；**明确「严禁任何商业用途」** | **非商用** ❌ | 只读"当模板用" | **不采用**（许可 + 依赖两处硬伤） |

> 星标数未独立核实（本机 `github.com` 被 hosts 指到 127.0.0.1），判断一律基于**能力 / 许可证 / 依赖**。

**只有 ppt-master 行得通的五条依据**：

1. **只有它读**：其余四个要么只能从大纲新建，要么只把旧稿当"模板"换文字；
2. **只有它做到母版/版式层**：`pptx_template_import.py` 保留 Master/Layout 继承链与文本槽，`pptx_to_svg.py` 连动画/转场都读得回来（写 `animations.json`）；
3. **只有它保证非破坏**：未改动页**逐字节保留**；
4. **只有它的许可能用**：MIT（Gorden 禁商用、dashi 是 AGPL）；
5. **只有它和我们的栈同构**：Python-only、CLI 确定性脚本、不需要 Office 插件、不需要 LibreOffice。

---

## 2. 开工实测修正（2026-09-23，已装 ppt-master `481e057e`）

| 以为 | 实际（读它的 workflow 后按事实改） |
|---|---|
| beautify 是"原地统一字体/配色/清动画"的补丁 | **不是**。`workflows/profiles/beautify-pptx.md` 写明**"不是补丁、不是填充"**：文字逐字冻结（1:1 页数/页序）+ **版式用 SVG 管线整套重做** → 产出**新的原生 pptx**；图表/表格**按数据重新生成** |
| 它有一批确定性脚本可当"美化引擎" | 确定性能力只有这几件：`pptx_intake.py`（拆包体检）、`beautify_inventory.py`（逐页台账 + `--summary/--page` + **`--verify` 内容零丢失校验**）、`beautify_identity.py`（主题/字体/字号/画布）、`pptx_to_svg.py`（视觉参考包）、`svg_quality_checker.py`（出框/字号校验）。**"重排"是模型逐页手写 SVG 的活** |
| `usage_contract.beautify` = 原地保留 | 它的语义是：**把原稿的文字/页序/页数/配色/字体/字号提升为"锁定约束"**（用户确认后）再重排 |

**因此一期改划**（入口、开关、落盘策略、验证方式不变）：

| 项 | 做什么 | 谁来做 |
|---|---|---|
| **体检报告** | 页数/画布/主题字体（含中文字体 `Hans`）/主题字号与层级/实际出现的字体与字号/表·图·图示·图片计数/**逐页台账**（页型、文字块、字符数、密度异常） | ppt-master 的 `pptx_intake.py` + `beautify_inventory.py`（确定性） |
| **内容零丢失校验** | 改稿后跑 `--verify`：逐字检查每个原串还在不在它那一页，并报"多出来的字符数" | 同上（确定性） |
| **保守原地统一** | `统一字体`（含 `<a:ea>`）、`统一字号层级`（归并到层级锚）、`清理动画/转场`（删 `p:timing`/`p:transition`） | **我们自己用 python-pptx 写** |
| **整套重排（真"美化"）** | 走它的 beautify 档：文字冻结 + SVG 重排 + 原生导出 | **二期**（要强模型；默认模型喂不饱，硬上只会更差） |

> 关键判断：**一期把"能确定性做对的事"做扎实，比接一个我们喂不饱的强工作流更有价值。**
> ppt-master 自己也写明「工具只承担流程，模型决定上限」。

---

## 3. 已交付：能力边界与怎么用

### 3.1 能力边界（先看这个）

| 能做（已交付，确定性） | 不能做（二期/别的工具） |
|---|---|
| 拆包**体检**：页数 / 画布 / 主题字体（含中文）/ 字号层级 / 表·图·图示·图片计数 / **逐页台账**（页型、文字块、字符数、密度异常） | ❌ 不"整套重排/美化"（模型逐页手写 SVG 的活） |
| **保守原地统一**：统一字体（含 `<a:ea>` 中文字体）/ 统一字号层级 / 清理动画与转场 | ❌ 不合并、不拆页、不改页序、不动文字内容 |
| **内容零丢失校验**：逐页逐串检查原内容还在不在，缺一个即失败 | ❌ 不认 PDF/Excel/Word 源（不引入 AGPL 的 PyMuPDF） |
| 直接改 **.pptx 原生对象**（不是图片），WPS / PowerPoint 里继续可编辑 | ❌ 不碰 WPS COM（走 python-pptx，与 WPS 开关无关） |

### 3.2 怎么用

1. **开开关**：设置 → Skill 市场 → **「PPT 优化」**（内置，**默认关**）；
2. 右侧侧边面板 → **「文档」** 圆钮 → **【幻灯片】** 页签 → 顶部「PPT 优化」区；
3. 点 **【导入 .pptx】**（或面板标题栏的【导入】，会自动分流）→ 出**体检报告**：
   文件 / 规模 / 画布 / 主题字体 / 字号层级 / 对象计数 / 需留意（稀疏页·密集页·待确认）；
4. **勾动作**（默认只勾「统一字体」——最保守）→ **【一键优化】**；
5. 结果行给「改了什么」+ **内容校验**结论（`✅ 逐页逐串都在`）→ **【下载优化稿】**（落 `data/ppt_out/`）。

### 3.3 三个动作分别改了什么

| 动作 | 做法 | 风险 / 说明 |
|---|---|---|
| **统一字体** | 遍历每页每个 run（含表格单元格），西文设 `latin`、中文补 `<a:ea typeface>`（python-pptx 不直接管东亚字体，要写 XML） | 默认：中文 **微软雅黑** / 西文 **Calibri**；只改字体名，不动字号与颜色 |
| **统一字号层级** | 把散落字号**归并到最近的层级锚**（默认 44/32/28/24/20/18/16/14/12），保留相对层级 | 容差 **0.6pt**（只吃浮点噪声）；容差 1.0 时 23pt 不会并到 24pt（实测踩过） |
| **清理动画 / 转场** | 删每页 XML 的 `<p:timing>` 与 `<p:transition>` | 一次性、不可逆（原稿仍只读，随时可重导） |

**报告字段从哪来**：`pptx_intake.py`（→ `source_profile.json` / `*.slide_library.json` / `*.identity.json`）+ `beautify_inventory.py`（→ `beautify_inventory.json`）。我们只**提炼**，不重写它们的解析。

---

## 4. 原稿只读 / 中间文件在哪（照实说）

| 位置 | 内容 | 生命周期 |
|---|---|---|
| `data/tmp/ppt_jobs/<job_id>/source.pptx` | 上传原稿的**副本**（原文件从不打开写） | 任务目录，默认 24h 后清理（`ppt_polish.cleanup_stale()`） |
| `data/tmp/ppt_jobs/<job_id>/analysis/` | 体检产物（`source_profile.json` 等） | 同上；`--verify` 要用它 |
| `data/tmp/ppt_jobs/<job_id>/polished.pptx` | 优化结果 | 同上；点【下载】才复制出去 |
| `data/ppt_out/优化_<job_id>.pptx` | 你下载过的成品 | 不自动删 |
| `content/plans/` | **一个字都不写**（单测锁住） | — |

- 体检报告与缩略图**只走内存 + 任务目录**，不写 `content/`；
- 面板上明示「优化会在任务目录产生中间文件，完成后自动清理」——**不装作无痕**。

---

## 5. 接口（都在后端一层收口）

| 方法 | 路径 | 状态 | 说明 |
|---|---|---|---|
| `GET` | `/api/ppt/status` | ✅ 已实现 | **不设门禁**：面板靠它决定按钮灰不灰。返回 `enabled` / `ready` / 安装自检（脚本、依赖、commit）/ 动作清单 |
| `POST` | `/api/ppt/intake` | ✅ 已实现 | 上传 `.pptx`（multipart）→ 落任务目录 → 体检报告 |
| `POST` | `/api/ppt/polish` | ✅ 已实现 | `{job_id, actions[], target_ea?, target_latin?, size_anchors?}` → 优化 + **自动跑内容校验** |
| `POST` | `/api/ppt/cancel` | ✅ 已实现 | 删任务目录（源稿副本 + 中间产物） |
| `GET` | `/api/ppt/download?job_id=` | ✅ 已实现 | 复制到 `data/ppt_out/` 后回传 |
| `GET` | `/api/ppt/templates` | 计划 | 模板清单（内置 + 导入） |
| `POST` | `/api/ppt/template/import` | 计划 | 上传 `.pptx` → 抽 theme/slots → 存模板（二期） |
| `POST` | `/api/ppt/template/delete` | 计划 | 删模板（软删到 `_trash/`，与教案模板同策略） |
| `POST` | `/api/ppt/fill` | 计划 | `{slides, template_id, on_missing_pages}` → 走 §10 填充器 → 返回任务/文件 |
| `POST` | `/api/session/doc-preview` | 扩展 | 已有；**加** `payload.slides` 原样回传并写进会话 `meta.doc` |
| `POST` | `/api/wps/export` | **不改行为** | 前端改成发 `payload.steps`（§8.2 的技术点 1） |

**门禁三档**：开关关 → `403 {gate:"capability"}`；没装 → `424 {gate:"install", hint:"跑 deploy/setup_ppt_master.cmd"}`；都过才干活。
`GET /api/mcp/status` 与 `/api/capabilities` 都带 **`ppt_polish_ready`**。

**两条链路互不影响**：`/api/wps/export`（我们的稿 → 落盘）与 `/api/ppt/*`（别人的稿 → 优化 → 落盘）完全独立，后者失败不影响前者。

---

## 6. 能力开关与 UI 门禁

- skill **`ppt-polish`**（名称「PPT 优化」，`builtin: true`，默认**关**）—— 与 `doc-session-preview` 同机制：
  关掉时面板不显示"优化"入口、`/api/ppt/*` 除 `status` 外一律 403 并说明原因；
- `ppt_polish_ready` = skill 开关 ∧ 安装自检通过；
- 「优化」按钮三态：可用 / 灰显（未安装，tooltip 给安装命令）/ 灰显（开关关闭）。

---

## 7. 安装与依赖

```cmd
:: deploy\setup_ppt_master.cmd（一次性；默认装项目内 .deps，不入库）
set DEST=<repo>\.deps\ppt-master
git clone https://gh-proxy.com/https://github.com/hugohe3/ppt-master.git "%DEST%"   :: 本机 github.com 被 hosts 指向 127.0.0.1，走 gh-proxy
"%VENV_PY%" -m pip install -r "%DEST%\requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple
"%VENV_PY%" "%DEST%\skills\ppt-master\scripts\pptx_intake.py" --help                 :: 自检
```

- **解析顺序**：环境变量 `PPT_MASTER_HOME` → **项目内 `.deps/ppt-master`** → 旧位置 `%LOCALAPPDATA%\edu-agent\deps\ppt-master`（2026-09-25 从 C 盘迁入项目盘）；
- **不引入**：PyMuPDF（AGPL，只有 PDF 源才需要）→ 不接 PDF 输入；`notes_to_audio`（TTS）不接；
- Windows 有**执行策略 / PATH** 要求，安装脚本里显式检查并给中文提示。

---

## 8. 下一步：幻灯片页签做成编辑器

> 老师原话：「我要的是在**幻灯片这一页显示的预览页可以编辑**，并且**直接显示的是合适的风格、排版**，而不是要下载、导入进行优化」→ 拍板四项：
> ① **PPT 优化按钮挪到「文档」页签的合适位置**；② **版式 6 个**；③ **主题做成可导入模板**；④ **会话生成的内容可以用来替换模板的文字**。

### 8.1 一句话

**幻灯片页签 = 面板内的幻灯片编辑器**：点开就是一套像样的版式与排版，**页内直接改**（标题/要点/页序/时长/版式），
**风格来自模板**（内置 3 套 + 你导入的学校/品牌 `.pptx`），**会话里生成的内容自动填进模板的文字槽**；改完的这份就是导出的那份。

### 8.2 两处**必须改**的技术点（不改则"改了也白改"）

| # | 问题 | 改法 |
|---|---|---|
| 1 | 手工改的幻灯片带不进导出：前端 `docpExport()` 只发 `{markdown, kind}`，后端于是从 Markdown 重新派生 | 导出时发 **`payload.steps`**（每页 `{name, minutes, bullets, layout, …}`）；后端 `wps_export._export_pptx()` 已支持 |
| 2 | 改了会被下一次派生覆盖 | slides 提升为**会话里的一等数据**：写进 `meta.doc.slides`，并加 `slides_edited` 标记 |

### 8.3 编辑能力（最小集）

| 能改 | 交互 | 落点 |
|---|---|---|
| 标题 / 要点文字 | 卡片内 `contenteditable` 直接改（与「文档」页签同一套习惯） | `slides[i].name` / `slides[i].bullets[]` |
| 加 / 删要点 | 卡片内 `＋ 要点` / 每条右侧 `×` | 同上 |
| 加 / 删页 | 底部 `＋ 新页`；页右上 `×` | `slides[]` |
| 换页序 | 页卡拖拽排序 | `slides[]` 顺序 |
| 每页时长 | 卡片角上 `约 __ 分钟` | `slides[i].minutes` |
| 换版式 | 每页右上角下拉（6 选 1） | `slides[i].layout` |
| 换风格 | 页签顶部下拉：模板（内置 3 + 导入的 N） | `payload.template_id` |

**即时反馈**：改完立刻按当前版式/模板重绘该页；**不重绘正在编辑的那一页**（否则光标跳 —— 「文档」页签已踩过）。

### 8.4 版式 6 个（含"槽位"定义）

每个版式声明自己有哪些**槽（slot）**；编辑器与"内容填模板"都按槽工作，模板与版式因此是同一套抽象。

| 版式 id | 名字 | 槽 | 用途 / 规则 |
|---|---|---|---|
| `cover` | 封面 | `title` `subtitle` `meta`(课题/学情/课时) | 第 1 页；主标题按 tokens 的封面字号左对齐 + 主色细线 |
| `section` | 章节过渡 | `index` `title` | 环节切换；大号序号 + 环节名，整页大留白 |
| `bullets` | 要点页 | `title` `bullets[]` | 主体；每页模块数与每模块字数上限见 tokens 的 `density`，超了给"拆页"提示 |
| `two-col` | 两栏 | `title` `left{heading,bullets[]}` `right{heading,bullets[]}` | 对比/并列；栏间距 = tokens 的 `canvas.gutter` × 2 |
| `media` | 图文 | `title` `image` `caption` `bullets[]` | 有图/有表；图占一侧，注释按 tokens 的注释字号 |
| `end` | 结尾 | `title` `bullets[]` `closing` | 小结 3 条 + 一句话收束 |

（初始版式规则：第 1 页 → `cover`，其余 → `bullets`，最后一页 → `end`；带"约 X 分钟"的环节名可一键转 `section`。）

### 8.5 排版规范（数值见 `docs/22` §3；本文**不复制数字**）

- **数值唯一权威**：`content/ppt_templates/_builtin/<主题>.json`（画布 / 安全边距 / 字号阶梯 / 配色占比 / 几何 / 字体 / 密度）。字段说明、三套主题取值表、px↔pt↔EMU 换算见 `docs/22` §3；
- 语义规则（不随数值变）：画布固定 16:9 设计坐标、面板内**等比缩放**；**一页只用一个主色做强调**，其余走灰阶；一律**左对齐 + 统一基线**（封面可选居中）；右下角**页码 `x/N`** 画在安全边距内；版式只用细线与留白做结构，**不套重卡片**；
- 三条内容规则：**每页模块数有上限 / 每模块字数有上限 / 一页只讲一件事**（上限值在 tokens 的 `density`；超出给提示，**绝不截断加省略号**）；
- ⚠️ 旧版这里写过的「安全边距 64px」「字号 40 / 32 / 22 / 15pt」**已作废**：正文 22pt ≈ 29px、注释 15pt ≈ 20px 都超出规范区间（正文 18–24px、注释 12–15px），一律以 tokens 为准；正文与注释的字号也**不再用 pt 表述**（画布 px 是唯一口径）。

### 8.6 目标 / 非目标

**目标**：① 每页可直接编辑；② 默认排版就像样；③ 风格可换、可导入；④ 会话内容替换模板文字；⑤ 改过的幻灯片能带走（存会话 + 导出用它）；⑥ 全程不依赖模型。

**非目标**：不做"导入旧稿 → 全局重排"（那是 §1~§7 那条线，保留但降级到「文档」页签的"高级"里）；
不在面板里做像素级自由排版（这是"教师做的演示稿"，不是设计工具）；不动教案正文与 Word 导出链路。

---

## 9. 模板体系：**主题 = 可导入模板**

**模板决定风格**（配色/字体/字号/母版/版式），**内容由会话给**。

### 9.1 模板库（与教案模板同构）

```
content/ppt_templates/
├─ index.json                 # 模板清单（id / name / 来源 / 页数 / 版式集 / 是否内置）
├─ _builtin/                  # 内置 3 套（纯 JSON，不需要原稿；数值与出处见 docs/22 §3）
│   ├─ tech-editorial.json    #   学术蓝（白底 + 蓝强调；上游 futuristic-tech-editorial）
│   ├─ illustration.json      #   插画暖（米白 + 雾蓝/脏橘；上游 modern-illustration-editorial）
│   ├─ hand-drawn.json        #   手绘墨（纸白 + 墨黑/靛；上游 japanese-hand-drawn-editorial）
│   └─ luxury-cream.json      #   备用第 4 套（米白棕金；上游 minimalist-luxury-branding）
└─ <id>/                      # 用户导入的模板
    ├─ template.pptx          #   原稿（只读，永不改写）
    ├─ theme.json             #   抽出来的：配色 / 字体（latin+ea）/ 字号层级 / 母版与版式清单
    └─ slots.json             #   抽出来的：每页的文本槽（角色、容量、几何）
```

模板描述（内置与导入统一形状）：
`{id, name, kind: "json"|"pptx", theme: {palette, fonts, sizes}, layouts: [...], slides: [{index, layout, slots: [{role, capacity, geo}]}]}`

### 9.2 导入一份 `.pptx` 当模板（二期）

- 抽取用 **ppt-master 的 `pptx_template_import.py`**（已验证存在）：保留 Master/Layout 继承链与文本槽；
  配色/字体/字号由 `beautify_identity.py` / `pptx_intake.py` 给出；
- 抽完写 `theme.json` + `slots.json` + 存 `template.pptx`（原稿只读），**不写模板库以外的地方**；
- 抽取失败 → 退化为"只用它的主题（配色/字体/字号）"，页面仍用我们的 6 个版式，并在面板说明。

### 9.3 与「教案模板」（.docx）的关系

两套模板**并列不混**：`.docx` 模板管教案正文排版（`content/templates/`，已有），`.pptx` 模板管演示风格（`content/ppt_templates/`，本次新增）。面板上分别标注"教案模板 / 演示模板"。

---

## 10. 会话内容 → 替换模板文字（填充器）

**输入**：会话里当前的 slides（§8.3 编辑后的那份）+ 选中的模板。**输出**：保留模板母版/版式的 `.pptx`，文字是会话内容。

### 10.1 映射规则（确定性）

| slides 里的东西 | 填到模板的哪 | 规则 |
|---|---|---|
| 封面页 `title/subtitle/meta` | 模板的 `cover` 页槽 | 按角色匹配；模板没有封面页 → 用第一张内容页，**不硬造封面** |
| 每页 `name` | 该页的 `title` 槽 | 逐页顺序对应 |
| 每页 `bullets[]` | 该页的 `bullets` 槽 | 按容量装；装不下 → **提示拆页**，不截断 |
| 页 `minutes` | 模板的页脚/时间槽（有则填） | 没有就不填 |
| 想插章节页 | 模板的 `section` 页 | 只在你手动把某页设为 `section` 时生成 |

### 10.2 页数不匹配怎么办

| 情况 | 做法 |
|---|---|
| slides 页数 **多于** 模板内容页 | **复制模板自己的"内容页"版式**生成新页（母版继承不变，视觉仍是这套模板） |
| 少于 | ① 留空页（保页数）② 删掉多余页（**默认**） |
| 模板有封面/结尾、slides 没有 | 只填能填的，**不硬造**（沿用 ppt-master 的"模板有什么角色用什么角色"） |

### 10.3 填完必须过两道校验

1. **内容零丢失**：`beautify_inventory.py --verify`（逐页逐串，缺一个即失败）；
2. **容量自查**：每个槽的"每行字数 × 行数" vs 填入文字，超出的页**列出来**（黄色提示，不阻断）——
   因为 ppt-master 的出框检测只在 SVG 域，pptx 侧用模板容量数据自查。

---

## 11. PPT 优化入口挪位（已定）

- **移出幻灯片页签顶部**，改放**「文档」页签底部**、正文之后一条**可折叠"高级"区**：

```
文档页签
├─ 正文（可直接编辑）
├─ ……
└─ ▸ 高级：PPT 优化（导入已有 .pptx → 体检 / 统一字体 / 清动画 / 校验）
        ↑ 默认收起；展开后就是现在那套（状态行 / 导入 / 体检报告 / 动作 / 一键优化 / 下载）
```

- 幻灯片页签**只剩**：模板下拉 + 页码/工具栏 + 16:9 预览与编辑 + 加页/排序；
- §3~§7 那条链路能力**一行不减**（后端与开关都不动），只是入口换位置；
- 结果条（`#docPanelRes`）继续共用 —— 优化结果与填充结果都显示在那里。

---

## 12. 会话里的数据形状（`session_docs` 表，向后兼容）

> **M4a 已落地（2026-09-25）**：这一段原计划写进消息 `meta.doc`，实际落成了**会话级的独立表**
> `session_docs(sid, doc, updated)`（`host/store.py`）——**加表不改表**，老库打开即自动补表、零迁移。
> 形状如下（`doc` 字段里存的 JSON）：

```jsonc
{
  "title": "3.3 幂函数",
  "slides": [                          // ← 一等数据，面板里编辑的就是它（渲染与导出都读这一份）
    {"name":"3.3 幂函数","layout":"cover","minutes":""},
    {"name":"情境导入","layout":"section","minutes":"5","bullets":["投影五个实例"]},
    {"name":"幂函数的概念","layout":"bullets","minutes":"12",
     "blocks":[{"label":"定义","text":"形如 y=x^α"},{"label":"关键","text":"α 为常数"}]},
    {"name":"小结","layout":"end","minutes":"3","bullets":["三条结论"]}
  ],
  "md_sig": "3f2a9c1b7d55",            // 存的时候"对着哪份教案正文改的"的指纹
  "slides_edited": true                // 手动改过
}
```

接口侧配套（`docs/22` §6.5 有细节）：

- `POST /api/slide/save` 存这份数据（`slide_deck.clean_slides()` 先收干净：只留认识的键、版式白名单、空页丢弃、单块 400 字上限）；
- `POST /api/session/doc-preview` 与 `POST /api/slide/render`、以及导出路径，**都优先用会话里改过的那份**；
- **不静默覆盖**：正文后来被重出过（`md_sig` 对不上）⇒ 这次按**新正文**出幻灯片，回 `slides_stale: true`，
  面板说明"你之前的修改还在会话里、只是没盖上来"——**老师的改动一个字都不删**；
- 删会话时顺手清掉这份数据（`SessionStore.delete`）。

**红线**：预览不落盘（编辑只改内存 + 会话 `session_docs` + localStorage 镜像）；
`content/ppt_templates/` 是**用户资产**（原稿只读、软删进 `_trash/`，导入的原稿建议 gitignore，仓库只留内置 3 套）；
导出仍走 `/api/wps/export`；填充器产物与优化产物都落 `data/ppt_out/`；`content/plans/` 与教案模板链路不动。

---

## 13. 测试与验收

### 13.1 已交付部分的离线单测

`tests/test_ppt_polish.py`（22 例，不联网、不调模型、不依赖真安装）：

1. `status` 在"未安装 / 已安装 / 脚本缺失"三种情形下的返回与 `ppt_polish_ready` 计算；
2. 能力开关：关掉 → `/api/ppt/*` 403 但 `/api/wps/export` 行为不变（**双向解耦**）；
3. 动作清单 → 原地动作正确（字体/字号锚/清 timing）；
4. 任务目录：创建/取消/超时清理，断言不碰 `content/plans/`；
5. 体检报告解析（用假脚本喂固定 JSON，验中文字体取值与台账字段）。

### 13.2 真机验收（一条命令）

```bash
# 后端离线单测 / 全量 / UI 检查 / 真机验收
.venv/Scripts/python.exe -m unittest tests.test_ppt_polish -v
.venv/Scripts/python.exe -m unittest tests.test_slide_deck tests.test_slide_audit tests.test_slide_edit   # 幻灯片：渲染 / 审计 / 编辑
.venv/Scripts/python.exe -m unittest discover -s tests -p "test_*.py"     # 410 例 OK（v1.16.0 R4）
cd tools/uicheck && npm i jsdom && node panel_check.js                    # 65 项检查
.venv/Scripts/python.exe tools/slide_audit.py --gate                      # 版式审计（模型内，0 error 才算过）
.venv/Scripts/python.exe tools/slide_audit.py --selftest                  # 闸门自证：4 个负样本必须被抓到
.venv/Scripts/python.exe tools/ppt_acceptance.py --with-wps               # 28 项：体检→优化→校验→包深检→WPS 真打开→不碰 plans→版式审计→新版式导出→WPS 再打开
```

> 提示：全量单测与 `panel_check.js` **别同时跑**（都在抢 CPU/磁盘，实测 45 秒会拖到 13 分钟）。

**2026-09-24 实跑记录（14/14 全过）**，源文件 = `content/plans/完整课时模板（默认）.pptx`（11 页 / 1310 字）：

| 步骤 | 结果 |
|---|---|
| [0] 安装自检 | ✅ ppt-master 就绪 `commit=481e057` |
| [1] 体检 | ✅ 11 页 · 1310 字 · 主题字体 宋体/宋体 · 字号层级 32/28/24/20 · 稀疏页 [2,4,6,7,8] · 密集页 [5] |
| [2] 优化 | ✅ 统一字体 **42 个 run**（`target_ea=微软雅黑` / `target_latin=Calibri`）；字号层级 changed=0；清动画 0 |
| [3] 内容零丢失校验 | ✅ `[VERIFY] passed: every frozen string is present on its page` |
| [4] 包完整性深检 | ✅ zip 无坏项 · 部件齐全 · 11 个 slide · 页数一致 · **文字多重集一致（42 段/42 段）** · 西文统一 Calibri · **`<a:ea>` 42 处** · 无 `<p:timing` |
| [5] **WPS 真打开** | ✅ 经 wps-office MCP `wps_ppt_open_presentation` → **「演示文稿打开成功！」** |
| [6] 没碰 `content/plans` | ✅ 前后目录清单一致 |

> 人工还需看一眼（机器验不了）：WPS 里**没有"需要修复"弹窗**、母版还在、**每个元素仍可单独选中编辑**、未勾的动作确实没被动。

**2026-09-25 实跑记录（v1.16.0 R3 后：28/28 全过）** —— 新增两段（详见 `docs/23` M2/M3）：

| 新增步骤 | 结果 |
|---|---|
| [7] 幻灯片版式审计（模型内，`docs/22` §7.1/§7.1b） | ✅ 三套主题示例 deck 全部 **error 0 / warn 0**（纵向占比 77–79% · 底部空白 76–84px · 主色 ≤0.2%）；4 个负样本（越界/白名单/字号阶梯/边距）**全部被抓到** |
| [8] 新版式导出（同一份 SVG → 原生可编辑 pptx） | ✅ `status=passed-with-warnings`；页数 3/3 · 51 个原生形状 · 包内 `fntdata` 0 个 · 含中文 run 19 个全部显式 `ea=Microsoft YaHei` · **内容零丢失**；产物 `data/ppt_out/教案_3.3 幂函数_*.pptx`（导出耗时约 4 秒） |
| [9] **WPS 真机打开新版式产物** | ✅ 经 wps-office MCP → **「演示文稿打开成功！」** |

> 新版式产物人工还需看一眼：WPS 里无"需要修复"提示、**每个文本框都能直接改字**、三套主题的颜色/字体与你挑的那套一致。

### 13.3 下一步（编辑器/模板）的测试计划

- 离线单测（新增 `tests/test_slide_edit.py` + 扩 `test_ppt_polish.py`）：版式槽位与渲染、编辑回流、**导出请求体带 `steps`**、会话留存与 `slides_edited`、模板加载与解析、填充器页数多/少、容量超出只提示不截断、不碰 `content/plans`；
- UI 检查（扩 `tools/uicheck/panel_check.js`）：页签可编辑且**编辑页不重绘**、模板切换生效、"高级：PPT 优化"默认收起；
- 真机（扩 `tools/ppt_acceptance.py`）：换模板填充一份 8 页稿 → WPS 打开：**母版/版式是模板的**、文字是会话的、每页可单独编辑。

---

## 14. 排错

| 现象 | 原因 / 处理 |
|---|---|
| 面板里看不到「PPT 优化」区 | 该区在**「文档」视图 → 【幻灯片】页签**顶部（挪位后改为「文档」页签底部"高级"）；先确认位置 |
| 状态行写「『PPT 优化』已关闭」 | 去 **设置 → Skill 市场 → PPT 优化** 打开（内置能力，默认关） |
| 状态行写「ppt-master 未就绪」 | 跑 `deploy\setup_ppt_master.cmd`；或设环境变量 `PPT_MASTER_HOME` |
| 点了【导入】选完文件没反应 | 已修（隐藏 input 的 `change` 绑定曾随视图重建被删）。再遇到请看控制台报错 |
| 【一键优化】报 403 / 424 | 403 = 开关关了；424 = ppt-master 没装（响应带 `hint`） |
| 下载的稿打不开 / WPS 提示修复 | **应该不会**：只改字体名、字号与删动画节点，不重写包结构。真遇到请留原稿与产物，这是要立刻查的回归 |
| 优化后文字变了？ | 不会 —— 由 `--verify` 逐页逐串把关，失败会明确报出来 |
| 想再改回原样 | 重新导入原稿即可（原稿永远只读，任务副本 24h 自动清） |
| 中间文件占地方 | 面板【取消】或等 24h；也可手工删 `data/tmp/ppt_jobs/*` |

---

## 15. 风险与回退

| 风险 | 影响 | 对策 |
|---|---|---|
| **模型能力不足**（它推荐 Kimi K3/Claude；我们默认 `deepseek-v4-flash`） | 路线 C / 真重排做不好 | 一期只做确定性；真重排放二期并允许接外部强模型 |
| 幻灯片与教案正文"不同步" | 谁为准不清 | 明确分工：**教案管 Word、幻灯片管 PPT**；面板标"已手动调整"；重出教案时**问**一声（保留/重置） |
| 模板版式与内容不匹配（槽太少/太多） | 排版难看 | 页数不匹配按 §10.2；槽不够 → 复制内容页；**不硬造封面/结尾** |
| 导入复杂模板抽不出槽 | 填充失败 | 退化为"只用它的主题"，页面仍用我们的 6 个版式，并在面板说明 |
| 编辑冲突（Agent 又出稿） | 手工修改被冲掉 | `slides_edited` 标记 + 确认弹窗；不静默覆盖 |
| 6 个版式不够用 | 表达受限 | 版式表是数据（`layouts` 数组），加版式 = 加一条 |
| 网络：`github.com` 被 hosts 指向 127.0.0.1 | 装不上 | 走 `gh-proxy.com` + pip 国内镜像；安装脚本失败给逐条中文指引 |
| **AGPL 组件（PyMuPDF）** | 许可污染 | 不接 PDF 输入；将来要接则隔离进程 + 分发时剔除 |
| 中间文件残留 | 磁盘涨、隐私 | 按 job 隔离 + 24h 清理 + 明示；**不写 `content/`** |
| 优化改坏了老师的稿 | 信任损失 | 原稿只读、产物另存；可只体检不改；改完必过 `--verify` |

---

## 16. 分期

| 期 | 内容 | 判定 |
|---|---|---|
| **已完成（v1.15.0）** | 安装自检 + 导入 .pptx + 体检报告 + **保守原地统一** + 内容零丢失校验 + 下载；skill `ppt-polish` 开关；接口 `/api/ppt/*`；真机验收脚本 | 单测 22 例 + 全量 320 例 + UI 23 项 + 真机 14/14 全绿 ✅ |
| **下一步（v1.16.0）** | slides 一等数据（会话 + 导出带 steps）、幻灯片页签可编辑、**版式 6**、**内置 3 套模板**、填充器（对内置模板）、**入口挪到「文档」页签底部**；并按 `docs/22` 落 tokens 与四项指标闸门；**排期 / 任务分解 / 验收命令以 `docs/23` 为准** | 单测 + UI 检查全绿；改完的幻灯片能导出且 WPS 打开正常 |
| **二期** | **导入 `.pptx` 当模板**（`pptx_template_import.py`）、模板管理、容量自查；真重排（接强模型走 beautify 档） | 用一份真学校模板跑通：保留母版、文字被替换、可编辑 |
| **三期（可选）** | AI 按场景（答辩/说课/汇报）**选版式 + 精简措辞 + 标图位** | 与手工稿对比评估 |

---

## 17. 交付物清单与待确认项

### 17.1 已交付（v1.15.0）

| # | 交付物 | 位置 |
|---|---|---|
| 1 | 后端链路 | `src/edu_agent/ppt_polish.py`、`web_server.py`（`/api/ppt/*`） |
| 2 | 能力开关 | `capabilities.py`（`ppt-polish`、`ppt_polish_ready`） |
| 3 | 前端入口 | `static/index.html`：幻灯片页签「PPT 优化」区；导入 accept 扩 `.pptx` |
| 4 | 安装脚本 | `deploy/setup_ppt_master.cmd`（装项目内 `.deps`） |
| 5 | 测试与验收 | `tests/test_ppt_polish.py`、`tools/ppt_acceptance.py`、`tools/uicheck/panel_check.js` |

### 17.2 下一步的交付物（v1.16.0 拟）

| # | 交付物 | 位置（拟） |
|---|---|---|
| 1 | 幻灯片编辑器（版式 6 + 就地编辑） | `static/index.html`（`docpRenderSlides` 换渲染层 + 编辑层） |
| 2 | slides 一等数据 + 导出带 steps | `static/index.html`、`web_server.py`（`meta.doc` 写入） |
| 3 | 模板库（内置 3 + 导入） | `content/ppt_templates/`、`src/edu_agent/ppt_templates.py`（新） |
| 4 | 填充器 | `src/edu_agent/ppt_fill.py`（新，复用 `pptx_native`/python-pptx） |
| 5 | 版式 tokens + 指标闸门 | `content/ppt_templates/_builtin/*.json`、`tools/slide_audit.mjs`（Edge 无头，见 `docs/22` §6–§7） |
| 6 | 入口挪位 | `static/index.html`：「文档」页签底部"高级：PPT 优化"折叠区 |
| 7 | 测试 | `tests/test_slide_edit.py`（新）、`tests/test_ppt_tokens.py`（新）、扩 `panel_check.js` / `ppt_acceptance.py` |

### 17.3 待你确认（合并原三份的待定项）

| # | 事项 | 状态 |
|---|---|---|
| 1 | 一期范围：只做"体检 + 确定性统一" | ✅ **已定**（已交付） |
| 2 | 美化默认动作：默认只勾「统一字体」 | ✅ **已定**（已实现） |
| 3 | 产物目录：`data/ppt_out/` | ✅ **已定**（已实现） |
| 4 | PPT 优化入口挪到「文档」页签底部（可折叠"高级"） | ⏳ 待做（§11） |
| 5 | 版式 6 个 · 主题做成可导入模板 · 会话内容替换模板文字 | ⏳ 待做（§8~§10） |
| 6 | 内置 3 套配色（学术蓝 `#1F4E79` / 简洁灰 `#374151` / 课件暖 `#C2410C`）——是否换成学校主色？ | ✅ **已定**（`docs/22` §1 D3：不自己编色，直接取上游 style-system 的 3 套现成数值 → `tech-editorial` / `illustration` / `hand-drawn`；学校主色改为"以后按同一结构加第 N 套"） |
| 7 | 每页要点上限（≤6 条 / 每条 ≤22 字）是否调整？ | ✅ **已定**（`docs/22` §3.3：**≤5 模块 / 每模块 ≤22 汉字**——条数按上游"3–5 模块"收紧，字数保留既有值，落在上游 16–32 汉字区间内） |
| 8 | 页数不匹配默认动作（少→删页、多→复制内容页） | ❓ 待你点头 |
| 9 | 导入的 `.pptx` 模板原稿不进仓库（gitignore） | ❓ 待你点头 |
| 10 | 二期（导入 `.pptx` 模板 / 真重排）现在上还是先验一期 | ❓ 建议先验一期 |
