# 29 · 用两份 skill 的「模板与优化方案」计划书（2026-09-28）

> **起因**：老师要「让同事内嵌在教案中心的 agent 用这两个 skill 里的**模板与优化方案**，去生成/优化预览里的 PPT」。
> **先纠一个前提**（`docs/24` §7）：预览里的 PPT 不是谁"画"出来的，是服务端按 `slides` 数据**现渲染的 SVG**。
> 所以"用它们的模板"＝**把它们的模板编译成我们的主题与版式**（预览立刻可选、导出仍走同一条路）。
> **本文只讲怎么做、红线在哪、先做哪一步。**

---

## 1. 资产盘点（实读 `.deps/ppt-master` 与只读参考副本）

> **2026-09-28 补：内置模板的"两条轴"与真实数量**（老师提到 34 套 Bold / 12 套预设、水墨·毛玻璃·孟菲斯·杂志·财经·瑞士等）。
> 我们**实读了两份**：pin 的 `481e057`（2026-09-23）与上游最新 `680de11`（2026-09-27，拉在 `data/tmp/ppt-master-next`，隔离目录不动线上）。
> **结论**：老师点名的那些风格名**已经在装的这份里**，它们属于 `references/visual-styles/`（**视觉风格**），
> 与 `templates/styles/`（**沟通方法工作区**）是**两条不同的轴**；"34 / 12"这两个数字在仓库里对不上（真实数量见下表）。

### 1.0 真实目录（两份一致，均已实读）

| 轴 / 资产 | 位置 | 数量 | 形态 |
|---|---|---|---|
| **视觉风格**（决定"页面怎么呈现"） | `skills/ppt-master/references/visual-styles/*.md` | **18 套**：blueprint · brutalist · chalkboard · dark-tech · data-journalism · editorial · **glassmorphism** · ink-notes · **ink-wash** · **memphis** · paper-cut · photo-editorial · pixel-art · sketch-notes · soft-rounded · **swiss-minimal** · vintage-poster · zine | **散文规则**（形状/排版性格/用色纪律/纹理/配对图像），**明说"不含 HEX"** |
| **沟通模式**（决定"deck 怎么表达"） | `references/modes/*.md` | **5 种**：pyramid · narrative · instructional · showcase · briefing（+ `_index`） | 散文规则 |
| **风格工作区** | `templates/styles/<名>/templates/design_spec.md` | **14 套**：academic-research · consulting-decision · creative-pitch · duty-manual · incident-postmortem · investor-pitch · mbb-consulting · narrative-keynote · operating-review · product-launch · science-explainer · solution-proposal · technical-deepdive · workshop-teaching | 方法论散文（含 YAML 头：`style_id/kind/summary/keywords`） |
| **品牌** | `templates/brands/<名>/` | **21 家**（google · microsoft · tencent · alibaba · huawei · accenture · mckinsey · bcg · bain · deloitte · pwc · ibm · nvidia · aws · jpmorgan · anthropic …） | `design_spec.md` **带色值**（primary/secondary/accent/text/bg，标 `fact`/`approx`）+ logo/字标素材 |
| **版式族** | `templates/layouts/<族>/templates/*.svg` | **7 族 / 86 个版式**：presentation_core **20** · presentation_core_43 **16** · report_core **13** · editorial_bleed **10** · xiaohongshu_post **10** · story_vertical **9** · moments_square **8** | **槽位化 SVG**（`data-pptx-placeholder` / `-bounds` / `-layer` / `{{PAGE_TITLE}}`） |
| **成品 deck** | `templates/decks/` | **2 套**（中国电信 / 中汽研） | 每套 `templates/*.svg` + `design_spec.md` + `images/` |
| **图像三件套** | `references/{image-renderings,image-palettes,image-type-templates}/` | 21 / 15 / 12 | 配图风格与调色板（配"视觉风格"用） |
| **规格与脚手架** | `templates/scaffolds/` · `templates/schemas/` | `design_spec.md` · `spec_lock.md` + 两个 JSON Schema | 编译器的输入契约 |
| **入口** | `workflows/create-template.md`(40KB) · `generate-pptx.md`(46KB) · `edit-native-pptx.md`(19KB) · `routing.md` | 3 条主管线 + 路由 | `/create-template` 把 pptx/SVG/图片/PDF 变成 brand/style/layout/deck 工作区 |

**老师点名的名字 → 仓库里的真身**：中国水墨风 = `visual-styles/ink-wash`；毛玻璃 = `glassmorphism`；
孟菲斯 = `memphis`；瑞士极简 = `swiss-minimal`；杂志图文 = `editorial`；财经数据 = `data-journalism`；
另外还有 `dark-tech` / `blueprint` / `brutalist` / `chalkboard` / `paper-cut` / `pixel-art` / `vintage-poster` 等。
⇒ **不用为了这几个风格升级**（`481e057` 里就有）；升级另议（见 §4 红线 3）。

### 1.1 ppt-master（**MIT** ⇒ 可放心用；仓库不入库，`docs/22` D12 锁 commit `481e057e`）

| 资产 | 位置 | 数量 / 形态 | 对我们意味着什么 |
|---|---|---|---|
| **版式（真正的骨架）** | `skills/ppt-master/templates/layouts/<族>/templates/*.svg` | **7 个族**；每族一套 SVG（`presentation_core` 有 **20 个**：title_slide / title_content / section_header / two_content / comparison / title_only / blank / content_caption / picture_caption / hero_statement / editorial_split / three_card / kpi_dashboard / process_timeline / data_story / title_picture / two_picture_caption / screenshot_focus / chart_insight / table_summary） | **能直接编译成我们的版式** —— 它是**槽位化**的（见 §2.1 样例） |
| 版式目录索引 | `templates/layouts/layouts_index.json` | 每族的 `summary` / `canvas_format` / `page_count` / `page_types[]` | 我们的版式白名单来源 |
| **品牌规格** | `templates/brands/<21 家>/templates/design_spec.md` | **21 家**（google / microsoft / tencent / alibaba / huawei / accenture / mckinsey / bcg / bain / deloitte / pwc / ibm / nvidia / aws / jpmorgan / anthropic …）；YAML front-matter + 色表（如 `primary_color: "#4285F4"`，primary/secondary/accent/text/bg 带 `fact`/`approx` 标注）+ 字体与用法禁忌 | **能编译成我们的主题 tokens**（色值取品牌，字号/几何用我们的阶梯） |
| **风格规格** | `templates/styles/<8 套>/templates/design_spec.md` | 8 套：academic-research / consulting-decision / creative-pitch / duty-manual / incident-postmortem / investor-pitch / mbb-consulting / narrative-keynote。**内容是"方法论散文"**（沟通方法 / 页角色词表 / 证据要求 / 密度 / 装饰克制 / 配色行为 / 字体性格），**不含数值** | 不编译成数值，而是编译成**内容策略与页角色**（喂给 agent 的规则；我们的 token 数值另取） |
| **成品样例** | `templates/decks/<中国电信 / 中汽研>` | `templates/*.svg`（01_cover / 02_toc / 03_chapter / 04_content / 05_ending）+ `design_spec.md` + `images/` | 参照"完整一套长什么样"，也可当"模板直通"的样本 |
| 规格脚手架与 Schema | `templates/scaffolds/{design_spec,spec_lock}.md` + `templates/schemas/*.schema.json` | `design_spec.schema.json` 字段：`project_information / canvas_specification / visual_theme / typography_system / layout_principles / icon_usage / visualization_reference / image_resource_list / content_outline / speaker_notes` | 我们编译器的**输入契约**（用它的字段名，别自创） |
| **优化方案（改造类）** | 脚本 `pptx_intake.py` · `beautify_inventory.py` · `beautify_identity.py` | 体检 → 逐页台账 → 保守原地统一（字体/字号层级/清动画），改完可 `--verify` 做内容零丢失 | ✅ 已在用（`src/edu_agent/ppt_polish.py`，v1.15.0 的"PPT 优化"流程） |
| 优化方案（模板类） | 脚本 `pptx_template_import.py` · `template_text_slots.py` · `template_validation.py` · `apply_template.py` · `register_template.py` · `template_preview_pptx.py` | 把一份 .pptx 收成规格 / 读它的文字槽 / 校验规格 / 把规格套到项目上 / 登记 | ⏳ **未用** —— 这是"用它的模板"最直接的一排工具 |
| 质量方案 | `svg_quality_checker.py`（+ `--canonical-authoring --stage final`） | 出"最终质量报告"，判 `status`（**不判退出码**） | ✅ 已在用（导出前必跑） |
| 文档 | `docs/zh/templates-architecture.md`(31KB) · `templates-guide.md`(34KB) · `scripts/docs/template-tools.md`(18KB) | 模板体系怎么组织、怎么用、命令行怎么调 | 编译器的说明书，先读它再写代码 |

### 1.2 guizang-ppt-skill（**AGPL-3.0** ⇒ 只借规则与数值，**不许抄模板/脚本**；只读副本在 `data/tmp/refs/`）

| 资产 | 形态 | 我们怎么用 |
|---|---|---|
| `references/themes.md`（Style A 电子杂志×电子墨水 **5 套** ink/paper 主题，含实测色值） | 数值 | **可引用**：写成我们的主题 tokens（标 `source`） |
| `references/themes-swiss.md`（Style B 瑞士国际主义 **4 套**单一锚点色 + 统一灰阶） | 数值 | 同上（这就是 `docs/22` §2A 说的"第 4 套风格"来源） |
| `references/layouts.md`（Style A **10 个版式**）· `layouts-swiss.md`（Style B **22 个登记版式** + **P0 对齐四法则** + 组件必选/可选）· `swiss-layout-lock.md` | **结构规则（散文）** | **按规则自己实现**：在我们 `ppt_svg.py` 里写等价版式（自己的想法+代码），**不许复制它的 HTML/CSS** |
| `scripts/validate-swiss-deck.mjs`（静态 + 渲染两级检查） | 规则与阈值 | ✅ 阈值已采纳进 `src/edu_agent/slide_audit.py`（0.74 / 170px / 0.93 / 32·14px / 版式白名单 / 主色 <10%）；**P0 对齐四法则尚未采纳** → 见 §4.3 |
| `assets/template.html`(102KB) · `template-swiss.html`(163KB) | **AGPL 模板** | ❌ **一个字都不许进本仓库或本产品链路** |

---

## 2. 桥接设计：三个编译器

### 2.1 版式编译器（最高价值，先做）

ppt-master 的版式 SVG 本来就是**槽位化**的，样例（`presentation_core/02_title_content.svg`）：

```xml
<svg width="1280" height="720" viewBox="0 0 1280 720"
     data-pptx-master="presentation_core_master" data-pptx-layout="title_content">
  <rect id="master-background" … data-pptx-layer="master" data-pptx-editable="false"/>
  <rect id="title-content-rule" x="64" y="120" width="96" height="4" … data-pptx-layer="layout"/>
  <rect id="title-content-panel" x="64" y="152" width="1152" height="504" rx="12" …/>
  <g id="title-content-title-slot" data-pptx-placeholder="title" data-pptx-bounds="64 40 1152 72">
    <text data-pptx-carrier="true" x="64" y="90" font-size="36" font-weight="700">{{PAGE_TITLE}}</text>
  </g>
  <g id="title-content-object-slot" data-pptx-placeholder="object" data-pptx-idx="1"
     data-pptx-bounds="88 176 1104 456">
    <text data-pptx-carrier="true" font-size="22">{{CONTENT_AREA}}</text>
  </g>
</svg>
```

⇒ 一次读取就能拿到三样：**装饰（layer=master/layout 的 rect/line/path）**、**槽位（placeholder + bounds + 载体字号/字色）**、**画布与网格**。
编译进我们这边就是：

| 它 | 我们 |
|---|---|
| `data-pptx-placeholder="title"` + `data-pptx-bounds` | 我们的槽位名（`title` / `block:i` / `caption` / `left` / `right` …）+ 几何 |
| `data-pptx-layer="master|layout"` 的图元 | 我们版式的**装饰层**（我们自己用 rect/line/path 重画，禁渐变/阴影） |
| `{{PAGE_TITLE}}` / `{{CONTENT_AREA}}` | 我们的内容键（`cover_title` / `blocks[].text` …） |
| `data-pptx-layout="title_content"` | 登记进 `LAYOUTS` 白名单 + `QUIET_SIDE` + 审计豁免规则 |

**产出物**：`content/ppt_templates/layouts/<族>/<版式>.json`（一个新目录，只放**我们自己**编译出的几何与槽位，`source` 字段记 `ppt-master <族>/<版式> @481e057e`）。
**收益**：老师可选版式从 **6 → 6+N**（先上最常用的 6 个，见 §5），预览与导出**同时**受益，页内编辑全都保留。

### 2.2 主题/品牌编译器

* 输入：`brands/<品牌>/templates/design_spec.md`（YAML + 色表）或 `styles/<风格>/…`（散文）；
* 输出：`content/ppt_templates/_builtin/<id>.json`（我们的 tokens：`canvas / palette / type / geometry / density / fonts / source / notes`），
  **色值取品牌，字号与几何用我们已验收的阶梯**（`docs/22` §3.3），字体只允许本机已装（有单测锁）；
* 品牌禁忌（如"别每页都盖 logo"）写进 tokens 的 `notes`，并在面板上做成"提示"而不是硬拦。

### 2.3 模板直通（给"必须用真母版"的场景）

用未接的那排脚本把一个族/一套成品变成**可导入的 .pptx 模板**（`template_preview_pptx.py` / `apply_template.py`），
再走我们**已有**的路：`模板+ 导入 → 填充 → 预览（pptx_to_svg 出的 SVG）→ 导出（python-pptx 原地改）`。
拿的是"母版/版式继承"，代价是页内编辑退化成**槽位填充**（能改字、挪位置，不能自由加块）——
与 §2.1 那条路是两种取舍，让老师按页签选。

---

## 3. 两种使用方式（老师要的"两个都要"，落成两条产品路径）

### 3.1 方式一：直接选用内置模板（在我们的面板里选，不给老师添步骤）

**交互**：`设计` 组里给两个下拉（现有"主题 / 模板"两个控件升级为四个）——

| 控件 | 取值 | 来源 |
|---|---|---|
| **视觉风格** | 18 套（水墨 / 瑞士极简 / 杂志 / 财经数据 / 毛玻璃 / 孟菲斯 / 蓝图 / 黑板 / 纸雕 / 像素 / 复古海报 / Zine …） | `references/visual-styles/*.md` |
| **沟通模式** | 5 种（金字塔 / 叙事 / 教学 / 展示 / 简报） | `references/modes/*.md` |
| **品牌**（可选） | 21 家色板 | `templates/brands/*/templates/design_spec.md` |
| **版式族 / 版式** | 7 族 86 个版式里挑常用的进白名单 | `templates/layouts/*/templates/*.svg` |

**难点与解法（重要）**：`visual-styles` 是**散文规则、明确不含 HEX**（原文："HEX values come from confirmation `e`; this style only governs … discipline — it names no colors"）。
所以"直接选用"必须由**我们给每种风格配一套数值**（否则就是空壳）：

1. **数值化**：为每套视觉风格写一份 tokens（`content/ppt_templates/_builtin/<style>.json`）——
   色板按风格的纪律定（例：`ink-wash` 宣纸底 + 墨黑字 + **单一朱红印色**；`swiss-minimal` 灰阶 + 单一高饱和锚点 + 直角禁渐变），
   字号/边距/几何沿用我们已验收的阶梯，`source` 字段记 `ppt-master references/visual-styles/<名>.md @<commit>`；
2. **纪律落地**：把风格的"几何/装饰纪律"变成**版式装饰层**与**审计检查**（例：ink-wash 禁重复卡片网格、要大片留白；
   memphis 允许高饱和撞色几何块但禁阴影；swiss-minimal 直角纯色、禁渐变/阴影/圆角）；
3. **配图对接**：风格里写了"配对图像渲染"（如 ink-wash ↔ `ink-notes`/`watercolor`），接到我们的配图三件套
   （`image-renderings` 21 / `image-palettes` 15 / `image-type-templates` 12）与"联网取图 / AI 生图"。
4. **样张先行**：每种风格先出 3 页样张（封面 / 要点 / 图文）给老师挑，挑中的进正式主题库（照 `docs/22` 第 1 步的老规矩：**口味由老师定，数值由工程定**）。

### 3.2 方式二：导入自己的 .pptx 当模板（对齐它的 `/create-template`）

**现状**：我们已经有 `模板+`（导入 .pptx → 读主题色/字体/字号 + 推断槽位 → `填充` → 预览走 `pptx_to_svg` → 导出走 python-pptx **原地改，保留模板设计**）。
**要补的**是 `/create-template` 那套"**抽成可复用工作区**"的深度：

| 它做什么（`workflows/create-template.md`） | 我们对应做什么 |
|---|---|
| 从 .pptx/SVG/图片/PDF 抽取 → 归类成 **brand / style / layout / deck** 四类工作区（写 `design_spec.md` + `spec_lock.md`，过 Schema 校验） | 接 `pptx_template_import.py`（抽取）→ `register_template.py`（登记）→ `template_validation.py`（校验）→ 存进我们的模板库（`content/ppt_templates/`），面板"模板"下拉里直接可选 |
| 生成时**复用**这套工作区（母版 / 版式继承 / logo / 字体规范） | 生成时用 `apply_template.py` 套规格；导出仍走"python-pptx 原地改 + 内容零丢失校验" |
| **是 AI 工作流**（要 agent 读材料、写规格、过确认） | 两条子路都给：① **脚本直抽**（deterministic，快，覆盖配色/字体/母版/版式几何）② **深度抽取**（跑它的 create-template 流程，产出完整 design_spec；需要模型 + 上下文，做成一个可选的"深度抽取"按钮） |

**红线不变**：模板原稿**只读**（导入只在 `content/ppt_templates/` 建副本 + 回收站机制）；
`content/plans/` 一个字不写；`.deps/ppt-master` 只读。

---

## 4. 优化方案的接法（"能改"之外还要"能评")

| 来源 | 怎么用 | 现状 |
|---|---|---|
| `svg_quality_checker.py` | 导出前必跑，面板结果条显示 `status` 与"怎么修" | ✅ 已用 |
| `pptx_intake.py` + `beautify_inventory.py` + `beautify_identity.py` | 对**导入的旧稿/模板稿**做体检 → 台账 → 保守统一 → 零丢失校验 | ✅ 已封装（`ppt_polish.py`） |
| `pptx_template_import.py` · `template_validation.py` · `template_text_slots.py` | 把"别人的品牌稿"收成规格、并校验规格是否完整 | ⏳ **待接**（§2.2/§2.3 的地基） |
| guizang `validate-swiss-deck.mjs` 的阈值 | 已进 `slide_audit.py` | ✅ |
| guizang **P0 对齐四法则** + 22 版式结构规则 | 变成我们的**新审计检查**（可测的那几条）与**新版式** | ⏳ 待做（§4.3） |
| `tools/slide_audit.py --gate` + `tools/full_gate.py` | 每条新模板/版式都要过闸门（error 0） | ✅ 已有流程 |

---

## 5. 红线（照 `docs/22` D11/D12 与 `docs/24` §7.3）

1. **AGPL 隔离**：guizang 的模板/脚本/HTML **不入库、不进产品链路**；只借数值与规则，并在 tokens/代码注释里标 `source`；
2. **MIT 也要守规矩**：`ppt-master` 只在**外面调用**，**不许改 `.deps/ppt-master` 下任何文件**（启动有完整性校验）；它 **不入库**，所以我们编译产物必须**自包含**（不能运行时去读它的文件当依赖，读一次生成 JSON 即可）；
3. **上游会漂**：commit `481e057e` 已锁；升级前重跑 `docs/22` §6.2 四条硬验收；
4. **预览 = 导出不能破**：任何新版式都必须"同一份 SVG 既预览又导出"（不许为预览另写一套 HTML）；
5. **落盘**：中间件只写 `data/tmp/ppt_jobs/`、产物只写 `data/ppt_out/`、模板原稿只读、**`content/plans/` 一个字不写**；
6. **判成功看报告 `status`**，不看退出码（`passed-with-warnings` 仍 exit 1）。

---

## 6. 分期（每期都能单独验收；老师要求"两种使用方式都要"，故 R27/R28 各一条）

| 期 | 做什么 | 验收 |
|---|---|---|
| **R27 · 方式一（直选内置模板）** | 视觉风格编译器 + 版式编译器：先上 **3 套视觉风格**（`ink-wash` 水墨 / `swiss-minimal` 瑞士极简 / `editorial` 杂志）× **6 个版式**（`title_content` / `two_content` / `comparison` / `section_header` / `content_caption` / `hero_statement`）；`设计` 组里加"视觉风格"下拉 | 「3 风格 × 6 版式 × 3 主题」**样张对比页**给老师挑；`slide_audit --gate` error 0；导出 pptx 过 `docs/22` §6.2 四条硬验收；面板可选且**页内编辑照旧** |
| **R28 · 方式二（导入自己的 pptx）** | 接 `pptx_template_import` / `register_template` / `template_validation`，把导入的 .pptx 抽成我们模板库里的登记项（母版/配色/字体/版式几何），生成时可复用 | 导入一份真实学校/答辩模板 → 填 → 预览（`pptx_to_svg`）→ 导出，**未改页面逐字节保留**、内容零丢失校验通过；原稿只读 |
| **R29 · 剩下 15 套视觉风格** | 按 R27 的流程批量数值化（memphis / dark-tech / blueprint / chalkboard / paper-cut / pixel-art / vintage-poster / zine …）+ 5 种沟通模式接进"内容策略" | 每套 3 页样张；tokens 单测（字号在区间/字体本机已装/禁项扫描）全绿 |
| **R30 · 其余版式族** | `report_core` / `editorial_bleed` / `story_vertical` / `presentation_core_43` 等族的常用版式继续编译（共 86 个，按需） | 同上（每个版式过闸门） |
| **R31 · 优化方案补全** | guizang 的 P0 对齐四法则做成审计新检查 + 面板"一键按建议修"；`svg_quality_checker` 的建议接进结果条 | 负样本自证（`slide_audit --selftest` 扩例） |

**为什么 R27 先上 3 套视觉风格 + 6 个版式**：这是老师真正会挑的粒度（风格=外观口味，版式=页面排法），
一套做完就能拿去选；一下子上 18 套 × 86 版式只会做出一堆没人看的样张。

---

## 7. 给同事 agent 的接口（与 `docs/24` §7 对齐）

* 新版式/新主题一旦登记，agent **不用改代码**：`GET /api/slide/fill/plan` 与 `GET /api/slide/render` 的响应里会多出可选 `layout` 与 `themes`；
* agent 想用某个品牌风格：`theme_id` 给我们的新 id（例：`brand-google`）；
* agent 想"整页自由排"：仍走 `objects[]`（`docs/24` §6）；
* **待补**（`docs/24` §7.4）：`GET /api/slide/spec`（规格包）与 `POST /api/slide/audit`（自查）——
  这两个接口让 agent 不必"盲写"，R27 一起做掉。
