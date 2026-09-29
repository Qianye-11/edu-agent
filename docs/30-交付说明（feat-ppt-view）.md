# 30 · 交付说明（feat/ppt-view：幻灯片编辑器 R1–R27 + 设计规格通道）

> 这份文档按 `docs/13-任意分支提交要求.md` 写：**环境 / 从零步骤 / 踩过的坑 / 一句话风险 / 边界**，
> 同时它就是那个分支 PR 的正文（PR 里"改了什么 / 怎么验的 / 有没有动别人的模块"三问的完整回答）。
>
> * 分支：`feat/ppt-view`（**替代** `feat/siderview`；后者已删，57 个提交全在本分支里）
> * 基线：`origin/main`（`a7060c0`）→ 领先 **67 个提交**
> * 开 PR：<https://github.com/Clayqi/edu-agent/compare/main...feat/ppt-view?expand=1>

---

## 一、这次交的是什么（改了什么）

把「教案中心 → 幻灯片」从"看一份排好的 PPT"做成 **WPS 式的页内编辑器**，把**两份外部 skill 的模板与
优化方案**接进来，并给外部 agent 开一条**能调控、不固化**的设计规格通道。分四块：

| 块 | 内容 | 代表提交 |
|---|---|---|
| **A · 编辑器主线 R14–R24** | 选中/拖动/8 手柄/吸附/微调/层级/删除/撤销（R14）· 属性与工具分组（R15）· 备注（R16）· 放映（R17）· 快捷键+多选+剪贴板+跨页查找替换（R18）· 缩略图多选拖动排序（R19）· 操作栈统一（R20）· 自由层文本框+8 预设形状（R21）· 图片上传（R22）· PNG/JPEG/PDF 导出+交付体检（R23）· 收口与全量闸门（R24） | `963caa3`…`577632a` |
| **B · 界面整改 R25/R26** | 下载菜单挂 body（几何裁剪，非 z-index）· 去掉对象条/本页属性卡/底部条 · 属性条搬到按钮下 · 翻页条与状态栏去重 · **内部术语撤出前端** · 槽位名说人话 | `906c63d`、`f9f83d1` |
| **C · 两份 skill 的模板与优化方案** | 实读盘点：ppt-master（MIT：21 品牌 / 14 风格工作区 / **18 视觉风格** / 5 模式 / 7 版式族 **86 版式** / 2 成品 deck）+ guizang（AGPL，只借数值与规则）；`docs/29` 计划书；`deploy/setup_skills.cmd` 安装入口 | `7667111`、`cd3487f`、`99d77c8`、`b50e351` |
| **D · R27 设计规格通道** | R27a 注册表+目录接口 · R27b 版式数据化 · R27c 规格驱动渲染 + 整页 SVG 逃生口 | `20a4179`、`ba4e02a`、`1fb644d` |

### D 块细说

* **R27a**：`tools/compile_design_registry.py` 读 `.deps/ppt-master`（pin `481e057`）编译出
  `content/ppt_templates/_design/*.json`（**入库、自包含** —— 两份 skill 不在场也能跑）。
  视觉风格原文**明确不含 HEX**，默认数值由我们给：`swiss-minimal`、`ink-wash`、`editorial` **三套人工调**
  （`tuned:true`），其余 15 套按纪律派生。解析器 `design_spec.resolve()`：越界**夹回并记 warn**、
  非法值忽略、未知风格/版式如实报错。接口 `GET /api/slide/design/catalog`（`?full=true` 给规则全文与版式几何）。
* **R27b**：核心 6 个版式**保持代码不动**；其余走数据（`layout_data.py`）。只有 **16:9** 的族可用（**37 个版式**），
  1:1/竖版/3:4 明确报错指路；`slide_deck.LAYOUTS` = 核心 6 + 这 37 个。
* **R27c**：`POST /api/slide/design/preview`（只读）按 `design` 出预览，回 `resolved/applied/warnings/locked`；
  `POST /api/slide/deck` 新增可选 `design`（**不给 = 与以前完全一致**）；`slides[i].svg_override` 整页逃生口；
  **另补齐 `docs/24` §7.4 的两个接口**：`GET /api/slide/spec`（内容规格包：认哪些键 / 上限 / 可用版式
  `total_usable=43` / 锁死清单）与 `POST /api/slide/audit`（自查：`errors/warnings/infos`，每条带 `fix`）。
  样张：`tools/design_samples.py` 出「3 视觉风格 × 2 数据版式」6 页 SVG + `index.html`（内联 SVG，不依赖浏览器）。

---

## 二、我的环境（复现前提）

| 项 | 值 |
|---|---|
| 操作系统 | Windows 10/11（本机 GitHub 走 hosts 加速器：`github.com` → `127.0.0.1`，**`api.github.com` 不可达**，所以 PR 用网页开） |
| Python | 3.13.2（`C:\Users\15000\python-sdk\python3.13.2`），虚拟环境 `F:\edu-agent3\.venv` |
| Node | v24.20.0（面板检查 `tools/uicheck/panel_check.js` 用 jsdom） |
| 浏览器 | Microsoft Edge（无头截图/导出 PNG・JPEG・PDF；`tools/slide_media.py`） |
| 本机应用 | WPS Office（导出验收 `--with-wps` 真机打开） |
| 外部依赖 1 | **ppt-master**（MIT）`481e057`（2026-09-23），装在 `.deps/ppt-master`（**不入库**） |
| 外部依赖 2 | **guizang-ppt-skill**（**AGPL-3.0**）`c91369c`（2026-08-07），只读参考在 `.deps/refs/`（**不入库**） |
| 上游探针 | ppt-master `680de11`（2026-09-27），sparse 副本 `.deps/refs/ppt-master-next`（不入库） |

---

## 三、从零复现步骤（每条都是真跑过的命令）

```cmd
:: 0) 仓库与虚拟环境
git clone https://github.com/Clayqi/edu-agent.git && cd edu-agent
git checkout feat/ppt-view
python -m venv .venv && .venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env            :: 填 DEEPSEEK_API_KEY

:: 1) 两份外部 skill（装项目内、但不入库）
deploy\setup_skills.cmd           :: ppt-master(481e057) + guizang 参考副本(c91369c)，末尾逐条自检

:: 2) 设计规格注册表（已入库；只有换了 skill 版本才需重编）
.venv\Scripts\python.exe tools\compile_design_registry.py
::   → 写出 content/ppt_templates/_design/{visual_styles,modes,brands,layouts,_meta}.json
::   → 盘点：视觉风格 18 · 模式 5 · 品牌 21 · 版式族 7 · 版式 86   来源 commit：481e057
.venv\Scripts\python.exe tools\compile_design_registry.py --check     :: 与来源一致（闸门 [5b]）

:: 3) 起服务
.venv\Scripts\python.exe -u src\edu_agent\web_server.py               :: http://127.0.0.1:5174
::   Ollama（向量）另起：set OLLAMA_MODELS=F:\ollama\models && ollama serve

:: 4) 看"有哪些可调"
curl http://127.0.0.1:5174/api/slide/design/catalog

:: 5) 按规格出预览（只读，不落盘）
curl -X POST http://127.0.0.1:5174/api/slide/design/preview -H "Content-Type: application/json" ^
  -d "{\"design\":{\"visual_style\":\"ink-wash\",\"overrides\":{\"palette\":{\"accent\":\"#9E2B25\"}}},\"slides\":[{\"name\":\"标题页\",\"cover_title\":\"3.3 幂函数\"},{\"name\":\"导入\",\"bullets\":[\"投影五个实例\"]}]}"

:: 6) 按规格出 pptx（落盘要 confirm）
curl -X POST http://127.0.0.1:5174/api/slide/deck -H "Content-Type: application/json" ^
  -d "{\"confirm\":true,\"lesson\":{\"title\":\"3.3 幂函数\",\"slides\":[{\"layout\":\"cover\",\"cover_title\":\"3.3 幂函数\"},{\"name\":\"导入\",\"bullets\":[\"投影五个实例\"]}]},\"design\":{\"visual_style\":\"ink-wash\",\"per_page\":[{\"layout\":\"hero_statement\"},{\"layout\":\"title_content\"}]}}"

:: 7) 闸门（九摊，硬项全绿才算能交）
.venv\Scripts\python.exe tools\full_gate.py
::   --quick 跳过单测与导出验收；--only=js,audit,panel 只跑某几摊；--no-wps 不开 WPS 真机
```

**我调了什么、返回什么（原文摘录）**

```
GET /api/slide/design/catalog
  ok=True  schema=edu-agent.design-catalog.v1
  counts: {"visual_styles":18,"modes":5,"brands":21,"layout_families":7,"layouts":86}
  视觉风格(前6): swiss-minimal* · soft-rounded · glassmorphism · dark-tech · blueprint · editorial*
  lock 项: canvas, safe_margin_min, type_steps_note, elements, forbidden, preview_equals_export, audit, no_write

POST /api/slide/design/preview（同一份 slides，两种风格）
  ink-wash      → accent=#9E2B25  页数=2 版式=hero_statement,two_content 盒子=1
  swiss-minimal → accent=#002FA7  首屏与前者不同=True

POST /api/slide/deck（带 design）
  ok=True 文件=教案_3.3 幂函数（设计规格实测）_20260929-005602.pptx 页数=3
  硬验收：✅ 页数一致 ✅ 原生文本（16 个形状）✅ 不嵌字体 ✅ 中文字体显式 Microsoft YaHei ✅ 内容零丢失
  交付体检：passed · error 0
```

---

## 四、怎么验的（可复核的实物）

| 验收 | 命令 | 结果 |
|---|---|---|
| 全量闸门（九摊，**独占机器跑**） | `tools\full_gate.py` | **9/9 全过**：内联语法 1 段 / 6656 行 · 版式审计 **error 0 · warn 0** · PPT 体检 **49/49** · 面板检查 **305 通过 / 0 失败** · 单元测试 **629 例 OK（跳过 2）** · 设计规格注册表与来源一致（5 份） · 导出验收 **35/35（含 WPS 真机）** · 产物交付体检 passed / passed-with-advisories · 任务目录校验 **error 0** |
| 面板检查 | `node tools\uicheck\panel_check.js` | 305 通过 / 0 失败 |
| 版式审计 | `tools\slide_audit.py --gate` | error 0 · warn 0（核心 6 版式基线不变） |
| R27 新增单测 | `-m unittest tests.test_design_spec tests.test_layout_data tests.test_design_preview` | **41 例 OK**（17 / 11 / 13） |
| 数据版式过审计 | 5 页数据版式 deck | error 0 / warn 0 |
| 版式冒烟 | 37 个 16:9 版式逐个渲染 | 全部画得出 |
| 样张 | `tools\design_samples.py`（3 视觉风格 × 2 数据版式 → PNG + 对比页） | 见 `data/ppt_out/design_samples/index.html` |

> **跑闸门的纪律（踩过两次才写下来）**
> ① **别同时跑第二个测试套件**：两个 `unittest discover` 并行时，`test_slide_deck` 的真导出用例出现过一次
> 「页数一致 ❌ + 内容零丢失 ❌」（单独跑绿、独占跑也绿 —— 导出中间件在共享的 `data/tmp/ppt_jobs`）；
> ② **别把日志写进仓库里的 `data/tmp`**：会撞上 `test_slide_template.test_import_does_not_touch_other_dirs`
> 的目录快照（它只允许写模板目录）—— 日志写 `%TEMP%`。

---

## 五、踩过的坑（这部分最值钱）

1. **下载菜单被卡片裁掉**：根因不是 z-index，而是 `.row-card{overflow:hidden}` 的**几何裁剪**
   （菜单底边 592 被切到 223），往上是 `.scroll{overflow:auto}` 与 `body{overflow:hidden}` 两道。
   解法：菜单**挂到 body** + `position:fixed` + 随按钮定位；z-index 取 50（盖得住卡片，低于全局导航 60 / 遮罩 80）。
2. **拿控件当"外壳在不在"的判据 ⇒ 栈溢出**：`docpRenderSlides` 原来用 `#dpTpl/#dpTheme` 判断，
   控件搬进"设计"组后判据永远为假 → 自己调自己（jsdom 直接 `RangeError`）。改用每组成员都在的锚点。
3. **数据版式三连坑**：源版式边距 64px（要按内容包围盒映射进我们的安全盒、**贴顶不居中**）；
   源版式**整幅背景**被当面板画（越安全线）；**多行用 `<tspan>` 会让审计量不到墨迹**（失衡判 100%，
   改成一行一个 `<text>`）。
4. **版式 id 跨族重名**（`cover`/`title_content` 在多个族都有）⇒ 去重优先 16:9 主力族；
   **只有 16:9 的族**能进我们的画布。
5. **`index.html` 里有 4 个 `</body>`**（3 个在 JS 拼接字符串里）⇒ 注入脚本必须插在**最后一个**前。
6. **测试的"单跑绿、全量红"**：新测试模块要在文件头把 `src/` 加进 `sys.path`（本仓库既有约定），
   否则 `unittest discover` 时 `ModuleNotFoundError: edu_agent`；
   另外"假 session_id 一定 400"的断言在**全量跑**时不成立（`get_session_doc` 会回落到当前会话），
   已改成两分支断言 —— **不是改测试迁就实现**，400 分支仍在断言里。
7. **并发跑测试会互相干扰**：两个套件同时跑时，真导出用例出现过一次「页数一致 ❌ + 内容零丢失 ❌」
   （单独跑/独占跑都绿）；日志写进 `data/tmp` 也会撞到"只许写模板目录"的守卫测试。

---

## 六、一句话风险

换台机器最容易卡在 **`.deps/` 两份 skill 没装**（跑 `deploy\setup_skills.cmd` 即可）和
**本机 hosts 把 `github.com` 指到 127.0.0.1** 导致 `git clone`/`api.github.com` 行为异常（PR 用网页开）。

---

## 七、边界（这份交付不做什么）

* **不做**动画 / 母版编辑 / 表格图表对象（`docs/26` 明确"暂不做"，各自写了替代做法与代价）；
* **不引入** guizang 的 HTML deck 运行时（AGPL），**不复制**它的模板 / 脚本 / 背景图；
* **不改** `.deps/ppt-master` 下任何文件（它启动有完整性校验），**不写** `content/plans/`（有测试锁）；
* **不支持** 1:1 / 竖版 / 3:4 的版式族（画布固定 1280×720），目录里标 `usable:false`；
* 18 套视觉风格里**只有 3 套是人工调的数值**，其余 15 套为派生值（等样张评审后逐套调）；
* 合并方式：**Squash and merge**（`docs/13` §一）。
