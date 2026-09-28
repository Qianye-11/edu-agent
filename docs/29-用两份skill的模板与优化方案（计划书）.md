# 29 · 用两份 skill 的「模板与优化方案」计划书（2026-09-28）

> **起因**：老师要「让同事内嵌在教案中心的 agent 用这两个 skill 里的**模板与优化方案**，去生成/优化预览里的 PPT」。
> **先纠一个前提**（`docs/24` §7）：预览里的 PPT 不是谁"画"出来的，是服务端按 `slides` 数据**现渲染的 SVG**。
> 所以"用它们的模板"＝**把它们的模板编译成我们的主题与版式**（预览立刻可选、导出仍走同一条路）。
> **本文只讲怎么做、红线在哪、先做哪一步。**

---

## 1. 资产盘点（实读 `.deps/ppt-master` 与只读参考副本）

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

## 3. 优化方案的接法（"能改"之外还要"能评")

| 来源 | 怎么用 | 现状 |
|---|---|---|
| `svg_quality_checker.py` | 导出前必跑，面板结果条显示 `status` 与"怎么修" | ✅ 已用 |
| `pptx_intake.py` + `beautify_inventory.py` + `beautify_identity.py` | 对**导入的旧稿/模板稿**做体检 → 台账 → 保守统一 → 零丢失校验 | ✅ 已封装（`ppt_polish.py`） |
| `pptx_template_import.py` · `template_validation.py` · `template_text_slots.py` | 把"别人的品牌稿"收成规格、并校验规格是否完整 | ⏳ **待接**（§2.2/§2.3 的地基） |
| guizang `validate-swiss-deck.mjs` 的阈值 | 已进 `slide_audit.py` | ✅ |
| guizang **P0 对齐四法则** + 22 版式结构规则 | 变成我们的**新审计检查**（可测的那几条）与**新版式** | ⏳ 待做（§4.3） |
| `tools/slide_audit.py --gate` + `tools/full_gate.py` | 每条新模板/版式都要过闸门（error 0） | ✅ 已有流程 |

---

## 4. 红线（照 `docs/22` D11/D12 与 `docs/24` §7.3）

1. **AGPL 隔离**：guizang 的模板/脚本/HTML **不入库、不进产品链路**；只借数值与规则，并在 tokens/代码注释里标 `source`；
2. **MIT 也要守规矩**：`ppt-master` 只在**外面调用**，**不许改 `.deps/ppt-master` 下任何文件**（启动有完整性校验）；它 **不入库**，所以我们编译产物必须**自包含**（不能运行时去读它的文件当依赖，读一次生成 JSON 即可）；
3. **上游会漂**：commit `481e057e` 已锁；升级前重跑 `docs/22` §6.2 四条硬验收；
4. **预览 = 导出不能破**：任何新版式都必须"同一份 SVG 既预览又导出"（不许为预览另写一套 HTML）；
5. **落盘**：中间件只写 `data/tmp/ppt_jobs/`、产物只写 `data/ppt_out/`、模板原稿只读、**`content/plans/` 一个字不写**；
6. **判成功看报告 `status`**，不看退出码（`passed-with-warnings` 仍 exit 1）。

---

## 5. 分期（每期都能单独验收，建议先做 R27）

| 期 | 做什么 | 验收 |
|---|---|---|
| **R27 · 版式第一批** | 版式编译器 + `presentation_core` 里最常用的 **6 个**：`title_content` / `two_content` / `comparison` / `section_header` / `content_caption` / `hero_statement`（我们已有的 `bullets` / `two-col` / `section` / `cover` / `end` 保留不删，做映射） | 样张对比页（6 版式 × 3 主题）；`slide_audit --gate` error 0；导出 pptx 过 §6.2 四条硬验收；面板里能选到新版式且**页内编辑照旧** |
| **R28 · 主题与品牌** | 主题编译器 + 8 套 style 的"内容策略" + 3–5 个品牌色板（先挑教育场景常用的） | 每套 tokens 过 `tests/test_ppt_tokens.py`（字号在区间、字体本机已装、禁项扫描）；样张 3 页/套 |
| **R29 · 模板直通** | 接 `pptx_template_import` / `template_validation`，把 1 个族 + 1 套成品变成可导入模板 | 导入 → 填充 → 预览 → 导出全通；内容零丢失校验通过 |
| **R30 · 优化方案补全** | guizang 的 P0 对齐四法则做成审计新检查（可测部分）+ 面板"一键按建议修" | 负样本自证（`slide_audit --selftest` 扩例）；面板能一键应用 |
| R31+ · 剩余版式 | 其余 14 个 `presentation_core` 版式 + `editorial_bleed` / `report_core` / `story_vertical` 等族 + guizang 22 版式里适合课件的若干 | 同上（每个版式都要过闸门） |

**为什么先做 R27**：老师要的是"预览里的 PPT 更好看"，而**版式**是观感的第一来源（`docs/22` §0 那三条难看的原因里两条与版式有关）；
主题只是换色，边际收益小；模板直通会牺牲页内编辑，放后面让老师按需选。

---

## 6. 给同事 agent 的接口（与 `docs/24` §7 对齐）

* 新版式/新主题一旦登记，agent **不用改代码**：`GET /api/slide/fill/plan` 与 `GET /api/slide/render` 的响应里会多出可选 `layout` 与 `themes`；
* agent 想用某个品牌风格：`theme_id` 给我们的新 id（例：`brand-google`）；
* agent 想"整页自由排"：仍走 `objects[]`（`docs/24` §6）；
* **待补**（`docs/24` §7.4）：`GET /api/slide/spec`（规格包）与 `POST /api/slide/audit`（自查）——
  这两个接口让 agent 不必"盲写"，R27 一起做掉。
