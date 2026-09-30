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

### 1.2 guizang-ppt-skill（**AGPL-3.0** ⇒ 只借规则与数值，**不许抄模板/脚本**；只读副本在 `.deps/refs/guizang-ppt-skill`）

> **副本在哪、算不算"项目里的"**（2026-09-28 明确）：两份 skill 都在**本机磁盘**上有**完整副本**，但**都不进 git 仓库**（`.gitignore`：`.deps/`、`data/`）——
> * `ppt-master`：`.deps/ppt-master`（**210.7 MB / 13,258 文件**，完整 git 仓库 @ `481e057`），运行时**外部调用**它；
> * `guizang-ppt-skill`：`.deps/refs/guizang-ppt-skill`（**11.2 MB / 70 文件**，完整 git 仓库 @ `c91369c`），**只读参考**；
> * 换机器复原：跑 `deploy/setup_skills.cmd`（先装 ppt-master，再按钉住的 commit 克隆 guizang 参考副本；
>   原先它躺在 `data/tmp/refs/`（临时目录，一句清理就没了），**已挪到 `.deps/refs/`** 与 ppt-master 同级）。
> * 编译产物必须**自包含**：R27 的注册表要能在两份 skill **不在场**时照常跑（原始副本只用于"重新编译"）。

> **2026-09-28 复查**：上游 `op7418/guizang-ppt-skill` 的 HEAD 仍是 `c91369c`（2026-08-07）**没有更新**；
> 但第一次盘点**漏了几块大资产**（下面标 🆕），它们比阈值更值钱 —— 逐条补齐。

| 资产 | 形态 | 我们怎么用 |
|---|---|---|
| `references/themes.md`（Style A 电子杂志×电子墨水 **5 套** ink/paper 主题，含实测色值） | 数值 | **可引用**：写成我们的主题 tokens（标 `source`） |
| `references/themes-swiss.md`（Style B **4 套**单一锚点色 + 统一灰阶） | 数值 | 同上；实测值：`--paper:#fafaf8` `--ink:#0a0a0a` 灰阶 `#f0f0ee/#d4d4d2/#737373`，锚点 `#002FA7` 克莱因蓝 · `#FFD500` 柠檬黄 · `#C5E803` 柠檬绿 · `#FF6B35` 安全橙（`--accent-on` 随之给白或黑） |
| `references/layouts.md`（Style A **10 个版式**）· `layouts-swiss.md`（Style B **22 个登记版式** + **P0 对齐四法则** + 组件必选/可选）· `swiss-layout-lock.md`（Golden Source 规则：正文页必须来自登记版式，`<section>` 上写 `data-layout="Sxx"`） | **结构规则（散文）** | **按规则自己实现**：在我们 `ppt_svg.py` 里写等价版式（自己的想法+代码），**不许复制它的 HTML/CSS** |
| 🆕 `references/checklist.md`（**36.8KB / 45 个具名检查项**：P0 硬错 · P1 节奏 · P2 打磨 · P3 操作细节） | **规则清单** | **这是它的"优化方案"本体**，我们只采纳了阈值。可测的条目值得逐条搬进 `slide_audit.py`，例如：kicker 必须在大标题**上方**；大字号双约束 `min(Xvw, Yvh)` 中 **Y ≥ X×1.6**；图片混排"直角、同高、只做证据"；底部分页安全区；**字号不得小到看不清 + 字重阶梯**；Hero 页与密集页**交替**（整册节奏）；同一概念中英文用法统一；页码一致；标题与正文间距 |
| 🆕 `references/presenter-mode.md`（12 节：`SPEAKER_NOTES` 数据结构 / 稳定页面 ID / 时间控制与**排练** / 自动翻页 / 激光笔与观众屏 / 演前检查与快捷键 / 生成后验证）+ `scripts/validate-presenter-mode.mjs` | **行为契约** | 我们只有"全屏放映 + 方向键 + 备注"。**演讲者视图**（当前页 + 下一页 + 讲稿 + 计时）对老师上课是真需求 —— 按它的契约自己实现 |
| 🆕 `references/screenshot-framing.md`（截图装框：优先级 / 语义参数 / 风格映射 / 背景强度规则）+ `assets/screenshot-backgrounds/`（Style A 5 张 + Style B 4 张 webp） | 规则 + **AGPL 图片资产** | 规则可借（截图按风格配上背景强度）；**9 张背景图不许进仓库**，要就自己生成 |
| 🆕 `references/image-prompts.md`（**7 类配图提示词**：人文纪实 / 杂志信息图 / 流程 / 对比 / 系统关系 / 截图再设计 / 数据大字报 + 比例选择 + "**先选目标槽位、再生成图片**"） | 规则 | 正好接我们待定的 **AI 配图**能力；"先定槽位再定图片比例"与我们的 `media` 版式图位天然契合 |
| 🆕 `references/components.md` + `swiss-map-component.md`（组件库；**地图组件** S08：必须有 `+`/`-`/`DRAG`，默认禁滚轮缩放以免触发翻页） | 规则 | 地图组件对地理课有用，但工作量大 —— 记进候选 |
| `scripts/validate-swiss-deck.mjs`（静态 + 渲染两级检查；实测阈值：标题间距 `isLocal ? 14 : 32`、安全线 `H×0.93`、`bottomGap>170 && activeRatio<0.74` 判 warn） | 规则与阈值 | ✅ 阈值已采纳进 `src/edu_agent/slide_audit.py`；**清单条目与对齐法则尚未采纳** |
| `assets/template.html`(102KB) · `template-swiss.html`(163KB) · `assets/motion.min.js`(62.7KB) · `assets/ppt-skill-showcase.png`(3.5MB) | **AGPL 资产** | ❌ **一个字都不许进本仓库或本产品链路**（含 9 张背景图） |
| `SKILL.md`(42.7KB) · `docs/unification-plan.md` · `.github/workflows/presenter-runtime-sync.yml` | 说明与内部演进 | 读思路即可（它的 HTML deck 运行时我们**不引入**） |

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

### 2.4 「不固化」怎么实现：风格=参数、版式=数据、特殊页=逃生口（2026-09-28 老师明确要求）

老师的要求是：**通识 agent 收到一句话要求后，能按"直选内置模板"那条路去调控预览里的 PPT，而不是我们预先烘死几个固定主题**。

先说清两边的事实（这决定了做法）：

| | ppt-master 的做法 | 我们现在的做法 |
|---|---|---|
| 风格 | `references/visual-styles/*.md` 是**散文纪律**（"宣纸底 + 墨黑字 + 单一朱红印色 + 大片留白"），**故意不写 HEX** —— 由模型**即席按纪律发挥** | **3 套 tokens JSON**（数值），面板下拉选 |
| 版式 | `templates/layouts/**` 86 个**槽位化 SVG** | `ppt_svg.py` 里 **6 个硬编码 Python 函数**（`LAYOUTS` + `_RENDERERS`） |
| 可调性 | 模型每次生成都能变 | 只能在我们给的 3×6 里挑 |

⇒ 要做到"agent 可调控、不固化"，必须把三样东西变成**数据**，并且在**不破交付底线**的前提下开放参数：

1. **风格 = 参数集**（不是一张张烘死的主题）
   `tokens` 其实**已经是数据驱动的**（`canvas / palette / type / ranges / weights / fonts / geometry / density`）。
   所以把 18 套 visual style 翻成"**纪律 + 参数默认值**"（`style_rules`：禁项/留白倾向/装饰倾向/配对图像 + 一组可覆盖的默认数值），
   agent 每次请求可以给 `overrides`（改主色、改密度、改留白），**渲染代码不用改**。
2. **版式 = 数据**（不是 Python 函数）
   现状：`LAYOUTS = ("cover","section","bullets","two-col","media","end")` 是写死的六个函数。
   目标：版式定义落成 `content/ppt_templates/layouts/<id>.json`（**槽位表 + 装饰图元 + 排版规则**），
   渲染器只认这份数据 → 86 个版式与 agent 自定义版式都是"加一条数据"，`LAYOUTS` 白名单改成"读注册表 + 审计校验"。
3. **特殊页 = 逃生口**（真·不固化，但只放开一小块）
   允许 agent 对**个别页**给"这一页用我给的 SVG / 我给的自由层对象"：
   * 自由层 `objects[]` **已经支持**（形状/文本框/图片，绝对坐标）；
   * 整页 SVG 覆盖是**新增**（`slides[i].svg_override`）：预览直接用它、导出把它交给 `svg_to_pptx`。
   好处：90% 的页仍是可编辑的槽位页，10% 的"设计页"可以完全自由。

**同时必须锁死的东西**（"不固化"≠"不设底线"，这些破了交付就废）：
画布 1280×720 · 安全边距 · 字号必须落在阶梯（偏离只 warn 不拦）· 白名单元素（rect/line/path/circle/text/polyline，禁渐变/阴影/外链图）·
预设形状的规范组格式 · **预览 = 导出**（同一份 SVG）· 审计 error 0 · `content/plans/` 一个字不写 · `.deps` 只读。

**这样"调控"就成立**：agent 拿到要求 → 读我们的**规格目录**（视觉风格 18 + 模式 5 + 品牌 21 + 版式族 7/86 + 硬规则）→
挑风格/模式/版式并给覆盖参数 → 调预览接口 → 老师立刻看到；不满意就再调一次（**每次都是一次请求，不是重新编译**）。

---

## 3. 两种使用方式（老师要的"两个都要"，落成两条产品路径）

### 3.1 方式一：直接选用内置模板 —— **由 agent 调控，不是一张烘死的清单**

**老师的要求（2026-09-28 明确）**：通识 agent 收到要求后，能按"直选内置模板"这条**去调控**预览里的 PPT，**而不是固化成固定的东西**。

所以这一条的产品形态是**三层**（同一套能力，谁是发起者不同）：

| 层 | 谁发起 | 粒度 | 用途 |
|---|---|---|---|
| **L1 面板直选** | 老师 | 视觉风格（18）· 沟通模式（5）· 品牌（21）· 版式（7 族 86 个）各一个下拉 | 老师自己想换风格/版式 |
| **L2 agent 指定** | 通识 agent | 一次请求带 `design`：`{visual_style, mode, brand, layout_family, per_page:[{layout}], overrides:{...}}` | **Agent 按老师那句话去调**（如"要水墨风、8 页、带数据页"） |
| **L3 agent 自定义** | 通识 agent | 逐页指定版式 + **自由层 `objects[]`** +（新增）**整页 SVG 覆盖** `slides[i].svg_override` | 常规版式做不出来的那一页 |

**它能"调控"什么**（都是参数，渲染代码不用改）：

| 可调 | 取值来自 | 落地形式 |
|---|---|---|
| 视觉风格 | `references/visual-styles/*.md` **18 套**（散文纪律） | 我们给每套配一份"纪律 + 默认数值"（`style_rules` + tokens 默认值），**agent 可覆盖** |
| 沟通模式 | `references/modes/*.md` **5 种** | 影响内容策略与页序建议（喂给 agent 的规则，不硬绑版式） |
| 品牌 | `templates/brands/*/design_spec.md` **21 家**（带色值） | 色板参数（primary/secondary/accent/text/bg） |
| 版式 | `templates/layouts/**` **7 族 86 个**槽位化 SVG | 编译成"版式数据"（槽位表 + 装饰图元）供逐页选择 |
| 数值覆盖 | agent 的 `overrides` | `palette.accent` / `type.*` / `density.blocks_max` / `canvas.margin` / 几何禁项…（**越界会被夹 + 记 warn**） |
| 配图 | 风格里写的"配对图像渲染" | 接 `image-renderings` 21 / `image-palettes` 15 / `image-type-templates` 12 与我们的联网取图 / AI 生图 |

**为什么不能只做"18 个写死的主题"**：`visual-styles` 明确**不含 HEX**（原文："HEX values come from confirmation `e`; this style only governs … discipline — it names no colors"）。
所以正确做法是**把纪律变成参数 + 给一套默认值**：agent 可以在纪律范围内调（换主色、调密度、选版式），
但**改不出破底线的结果**（越界夹回 + 审计 warn，见 §2.4 的"锁死清单"）。

**样张先行**（这一步不变）：每套风格先出 3 页样张（封面 / 要点 / 图文）给老师看；
但样张只是**默认值**的可视化，不是天花板 —— agent 之后仍可逐次覆盖。

**给 agent 的接口（R27 一起做）**：

```jsonc
// ① 读规格目录（风格纪律 + 品牌色板 + 版式清单 + 硬规则）
GET /api/slide/design/catalog
// → { visual_styles:[{id:"ink-wash", rules:{...}, defaults:{palette,type,density}} …],
//     modes:[…5], brands:[{id:"huawei", palette:{…}} …],
//     layout_families:[{id:"presentation_core", layouts:[{id:"title_content", slots:[…]}]} …],
//     locked:{canvas:"1280x720", safe_margin:72, type_steps:[…], elements:[…]} }

// ② 用这份规格出一版预览（立刻看到，不落盘）
POST /api/slide/design/preview
{ "session_id":"…", "design":{ "visual_style":"ink-wash", "mode":"instructional",
    "overrides":{ "palette":{"accent":"#9E2B25"}, "density":{"blocks_max":4} },
    "per_page":[{"layout":"section_header"},{"layout":"title_content"},{"layout":"ink_quote"}] } }
// → { ok, pages:[SVG…], resolved:{…最终数值…}, warnings:[…], checks:[…] }

// ③ 满意了再出 pptx（同一份 SVG → 原生可编辑）
POST /api/slide/deck  { "session_id":"…", "design":{…}, "confirm":true }
```

> 与 `docs/24` 的关系：`design` 是**新增的可选字段**，不给就还是今天的行为（6 版式 + 主题）——**向后兼容**。

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
| **R27 · 方式一（agent 可调控的内置模板通道）** | 拆三步：**R27a** 设计规格注册表 + `GET /api/slide/design/catalog`（18 视觉风格"纪律+默认数值"、5 模式、21 品牌色板、7 族 86 版式、锁死清单）；**R27b** 版式数据化（把 `ppt_svg.py` 里 6 个硬编码版式改成数据定义 + 通用渲染器，白名单改读注册表）；**R27c** 规格驱动渲染接口 `POST /api/slide/design/preview` 与 `POST /api/slide/deck` 的 `design` 字段（含 `overrides` 与 `per_page`） | 「3 风格 × 6 版式」样张对比页；**同一个 agent 请求换风格/版式/主色能立刻出不同预览**（不是改代码）；`slide_audit --gate` error 0；导出过 `docs/22` §6.2 四条硬验收；面板里页内编辑照旧；不传 `design` 时行为与今天完全一致（向后兼容） |
| **R27a ✅ 已交付（2026-09-28）** | `tools/compile_design_registry.py` → `content/ppt_templates/_design/{visual_styles,modes,brands,layouts,_meta}.json`（**入库、自包含**：两份 skill 不在场也能跑）；`src/edu_agent/design_spec.py`（目录 + 解析器 + 越界夹取 + 锁死清单）；`GET /api/slide/design/catalog`（`?full=true` 带规则全文与版式几何）；`tests/test_design_spec.py` **16 例**；`full_gate` 新增 **[5b] 注册表与来源一致** | **实测**：目录返回 18/5/21/7/86（风格 `swiss-minimal`·`ink-wash`·`editorial` 标 `tuned`）；解析器实测 `radius=99→16`、`blocks_max=50→8`、`body=4→16` 各记一条 warn，非法色值忽略；`blueprint` 等暗色风格自动拿暗底；品牌 `huawei` 主色 `#CF0A2C` 落到 `accent`；`--only=registry,js,audit` 3/3 全过 |
| **R27b ✅ 已交付（2026-09-28）** | `src/edu_agent/layout_data.py`（数据版式渲染器）+ 接线：`render_slide` **核心 6 个走代码、其余走数据**；`slide_deck.LAYOUTS` = 核心 6 + **16:9 的注册表版式（37 个）**；审计白名单与"安静侧"适配；`catalog` 每个族/版式带 `usable` 标记 | **实测**：37 个 16:9 版式**逐个渲染冒烟全过**；5 页数据版式 deck（title_content / two_content / comparison / section_header / hero_statement）**审计 error 0 / warn 0**；核心 6 版式审计基线**不变**；踩掉三个坑：源版式边距 64px（要按内容包围盒映射进我们的安全盒、**贴顶**不居中）、源版式整幅背景被当面板画（顶 37px/底 675px 越线）、**多行用 `<tspan>` 会让审计量不到墨迹**（失衡判 100%） |
| **R27c ✅ 已交付（2026-09-28）** | `POST /api/slide/design/preview`（只读）+ `POST /api/slide/deck` 的 `design` 字段 + `slides[i].svg_override` 逃生口；**顺带补齐 `docs/24` §7.4 的两个接口**：`GET /api/slide/spec`（内容规格包）与 `POST /api/slide/audit`（自查，每条 findings 带 `fix`）；`tools/design_samples.py` 出**样张对比页**（内联 SVG，不依赖浏览器） | **实测**：同两份 slides 换风格 → `ink-wash`（`#F8F6F2`/`#2B2B2B`）与 `swiss-minimal`（`#FAFAF8`/`#0A0A0A`）SVG **不同**；`per_page` 生效；带 `design` 出 3 页 pptx **四条硬验收全过 + 交付体检 passed**；逃生口 5 类坏输入（script / 外链图 / 非 svg / 多根 / 缺 viewBox）全被丢弃；`spec` 实测 `total_usable=43`；`audit` 实测 theme=`design-swiss-minimal`、error 0；`tests/test_design_preview.py` **17 例 OK**；样张 6 页 SVG + `index.html` |
| **R28 · 方式二（导入自己的 pptx）** | 接 `pptx_template_import` / `register_template` / `template_validation`，把导入的 .pptx 抽成我们模板库里的登记项（母版/配色/字体/版式几何），生成时可复用 | 导入一份真实学校/答辩模板 → 填 → 预览（`pptx_to_svg`）→ 导出，**未改页面逐字节保留**、内容零丢失校验通过；原稿只读 |
| **R29 · 剩下 15 套视觉风格** | 按 R27 的流程批量数值化（memphis / dark-tech / blueprint / chalkboard / paper-cut / pixel-art / vintage-poster / zine …）+ 5 种沟通模式接进"内容策略" | 每套 3 页样张；tokens 单测（字号在区间/字体本机已装/禁项扫描）全绿 |
| **R30 · 其余版式族** | `report_core` / `editorial_bleed` / `story_vertical` / `presentation_core_43` 等族的常用版式继续编译（共 86 个，按需） | 同上（每个版式过闸门） |
| **R31 · 优化方案补全** | guizang 的 P0 对齐四法则做成审计新检查 + 面板"一键按建议修"；`svg_quality_checker` 的建议接进结果条 | 负样本自证（`slide_audit --selftest` 扩例） |
| **R32 · guizang 清单落地（复查后新增）** | 把 `checklist.md` 里**可测**的条目逐条搬进 `slide_audit.py`（kicker 位置 / 大字号双约束 `Y≥X×1.6` / 图片混排规则 / 底部分页安全区 / 字重阶梯 / **整册节奏**：Hero 与密集页交替 / 术语中英一致 / 页码一致），每条给"怎么修" | 每条都有正负样本；面板结果条能列出"哪一页、哪一条、怎么改" |
| **R33 · 演讲者视图（复查后新增）** | 按 `presenter-mode.md` 的契约做**演讲者视图**：当前页 + 下一页 + 讲稿 + 计时（含排练），观众屏仍是全屏放映 | 双屏/单屏都能用；讲稿不串页；Esc 退出恢复；面板检查覆盖 |

**为什么 R27 先上 3 套视觉风格 + 6 个版式**：这是老师真正会挑的粒度（风格=外观口味，版式=页面排法），
一套做完就能拿去选；一下子上 18 套 × 86 版式只会做出一堆没人看的样张。

---

## 7. 给同事 agent 的接口（与 `docs/24` §7 对齐）

* **新增（R27）**：`GET /api/slide/design/catalog`（规格目录）· `POST /api/slide/design/preview`（按规格出预览）·
  `POST /api/slide/deck` 的 `design` 字段（按规格出 pptx）· 可选 `slides[i].svg_override`（整页 SVG 逃生口）；
* 新版式/新风格/新品牌一旦进注册表，agent **不用改代码**就能用（目录接口里会列出来）；
* agent 想"整页自由排"：先用常规版式，再用 `objects[]`（`docs/24` §6）；还不行就 `svg_override`；
* **待补**（`docs/24` §7.4）：`GET /api/slide/spec`（把"规格目录"与"内容规格"合成一个入口）与
  `POST /api/slide/audit`（自查：交稿前后量一把，回 error/warn + 怎么修）——R27 一起做掉；
* ⚠️ **R26 起"每页被精简 / 建议拆页"的提示不再显示在老师界面上** ⇒ agent 必须读响应里的 `warnings[]` / `checks[]`。
