# 课件生成接入（PPT Master）（docs/19）

> 状态：**已接入并实测通过**（2026-09-26）｜引擎：第三方 MIT 项目 [ppt-master](https://github.com/hugohe3/ppt-master)
> 相关代码：`scripts/wsl_ppt_bridge.py`（WSL 侧）、`src/edu_agent/ppt_master.py`（Windows 侧）、
> `capabilities.py` 的 `ppt-master` 技能条目、`tests/test_ppt_master.py`

---

## 一、它是什么、为什么接

老师的课件长期以来只有两条路：WPS MCP（要拉起本机 Office，改的是现成 pptx）和
「WSL 渲染后路」（用 python-pptx 自己写，样式贫弱）。**ppt-master** 提供第三条：
把「页面数据」编译成**原生可编辑 PPTX**——每个形状、每段文字在 PowerPoint/WPS 里都能单独
选中拖动，公式走 OMML、图表走原生 chart XML，全程纯 Python 写 OOXML，**不依赖装 Office**。

接入的取舍（重要，别误读）：

| 搬什么 | 决定 | 原因 |
|---|---|---|
| **编译器层**：`finalize_svg` + `svg_quality_checker`（质量门）+ `svg_to_pptx`（SVG→DrawingML） | **借** | 纯确定性代码；质量门是真能拦错的（本次接装被抓出 4 类错误） |
| **提示词层**：`SKILL.md` + `workflows/` + `references/`（277 份 md，最大 96KB） | **不借** | 它面向通用商业演讲；教学口径（课标/学情/习题）由我们自己定，且全量 token 成本会挤爆上下文 |
| 页面来源 | **自己渲染** | `wsl_ppt_bridge.py` 用固定版式（cover/section/cards/bullets）确定性生成 SVG，不让模型手写 SVG 去撞它的严格契约 |

---

## 二、架构：为什么还是走 WSL

与既有 `wsl_docx` 后路**同一个原因**：本机 Windows「智能应用控制」(SAC) 拦 lxml 的
`etree.pyd`，而 ppt-master 的导出链路（python-pptx / svg_quality / svg_to_pptx）全依赖它。
SAC 管不到 WSL，所以：

```
教育 agent（Windows）
  edu_agent.ppt_master.generate(pages=[...])          ← 只做编排：拼数据 / 换算路径 / 收结果
        │  subprocess: wsl.exe -d Ubuntu -- python3 scripts/wsl_ppt_bridge.py（stdio 一行一 JSON）
        ▼
  scripts/wsl_ppt_bridge.py（WSL，python3.12）
        ├─ 渲染页面 SVG（固定版式，字号全部登记在 spec_lock 里）
        ├─ 写 spec_lock.md（画布 / 配色 / 字号角色 / page_rhythm / flat 结构）
        ├─ finalize_svg.py            （图标/图片展开，本例无需处理）
        ├─ svg_quality_checker.py --canonical-authoring --stage final   ← 质量门（不通过就不导出）
        └─ svg_to_pptx.py            → exports/*.pptx（原生 DrawingML）
        ▼
  D:\hermes\education-ppt\<名字>_ppt169_<日期>\exports\*.pptx
```

引擎装在**仓库外**：`D:\hermes\ppt-master`（与 `wps-office` 的「第三方装在仓库外」惯例一致，
128MB / 13,087 文件绝不进 git）。用环境变量 `EDU_PPT_MASTER_ROOT` 可改。

**布局**（沿用 wsl_docx 的约定）：默认 WSL；`EDU_PPT_BACKEND=native` 才用本机解释器跑
（引擎也装在 Windows 侧时才可行）。

---

## 三、怎么用

```python
from edu_agent import ppt_master

ppt_master.available()          # -> (True, "就绪（python 3.12.3 / python-pptx 1.0.2）")
ppt_master.backend()            # -> "wsl"

pages = [
    {"layout": "cover",   "title": "牛顿第一定律", "subtitle": "人教版必修一 · 第四章"},
    {"layout": "section", "title": "第一课时", "subtitle": "惯性"},
    {"layout": "cards",   "title": "三个要点", "items": [
        {"heading": "惯性",   "body": ["物体保持原有运动状态", "直到外力作用"]},
        {"heading": "力",     "body": ["力是改变运动状态的原因"]},
        {"heading": "理想实验", "body": ["伽利略斜面外推"]}]},
    {"layout": "bullets", "title": "课堂练习", "items": ["...", "A．...", "B．..."]},
]

r = ppt_master.generate(pages, name="niudun", canvas="ppt169")   # 或 ppt43
r["ok"], r["pptx"], r["gate"]        # gate = {exit, errors, warnings, report}

ppt_master.verify(r["pptx"])         # 原生性自检
# -> {slides:3, shapes:29, pictures:0, media_files:0, east_asian_font:"Microsoft YaHei", native_editable:True}

ppt_master.compile_project(r"D:\...\项目目录")   # 对手写 SVG / 深改过的页面走同一条门与导出
```

**版式约束**（渲染层已实现，超出会被裁剪并在返回值 `dropped_items` 里报数）：

| layout | 用途 | 上限 |
|---|---|---|
| `cover`（别名 `title`） | 封面：主标题 + 副标题 | — |
| `section` | 章节页 | — |
| `cards` | 卡片页：每张一个标题 + 最多 3 行正文 | 3 张 |
| `bullets` | 要点/习题页 | 6 条 |

**垂直排布是自适应的**（2026-09-26 视觉复核后改）：`cards` 的卡片高度按实际正文行数算
（280~380px），整块在内容带里垂直居中；`bullets` 条目少时整块居中，不再全挤在上半部。
封面强调条用 `accent` 色（原用 `divider` 太浅，投屏对比不足）。
改版式只动 `scripts/wsl_ppt_bridge.py` 的 `render_*`，改完重新生成 + 用无头 Edge 截图复核。

**能力开关**：`capabilities.snapshot()["skills"]` 里新增 `ppt-master`（`builtin=True`），
带 `running/detail` 状态字段（首次探活会起一次 WSL 拿引擎版本，之后走 lru_cache）。
UI 关闭它只影响入口显示，不影响已有导出链路。

---

## 四、质量门到底拦什么（本次接装被拦的 4 类，都是真问题）

1. **根级 `<g>` 必须带 `data-pptx-bounds="x y w h"`** —— 少了直接报错（第一次就栽在这）。
2. **`pptx_structure.mode: flat` 下禁止 Master/Layout/placeholder 元数据**
   （`data-pptx-layer` / `data-pptx-master` / `data-pptx-placeholder` 等一律不许出现）。
3. **字号必须登记**：未登记字号出现第 3 次就报 `undeclared font-size` →
   `spec_lock.md` 的 `## typography` 要写全 `hero/title/card_title/body/section` 等角色。
4. **字体栈要与 lock 一致**：lock 写 `Microsoft YaHei`，SVG 里就不能是
   `Arial, Microsoft YaHei, sans-serif`（会被判 drift）；字体统一挂在根 `<svg>` 上。
   另外禁 HTML 具名实体（`&nbsp;` 之类），只允许 XML 自带转义。

页面角色词表（根 `data-pptx-page-role`）：`cover` / `toc` / `section` / `content` / `ending`。
结构角色（元素 `data-pptx-role`，需稳定 id）：`background` / `chrome` / `decoration` /
`footer` / `header` / `logo` / `page-number` / `watermark`。

---

## 五、排障

| 症状 | 原因 / 处理 |
|---|---|
| `找不到 ppt-master 引擎（SKILL.md）` | 引擎不在默认位置 → 设 `EDU_PPT_MASTER_ROOT` 指到 clone 目录 |
| `spec_lock.md is required for release SVG export` | 手工建的工程缺 lock；桥接会自动写，手写工程用 `compile_project` 前补齐 |
| `质量门未通过：...` | 看返回值 `gate.details` 与 `validation/svg_quality_report.json`，按上面第四节改 |
| 中文变豆腐块 | WSL 缺中文字体（`fc-list :lang=zh` 为 0）。本机已从 Windows 拷入微软雅黑到 `~/.local/share/fonts`；新机器照做 |
| `WSL 侧无有效输出` | WSL 没起 / 发行版名不对 → `EDU_WSL_DISTRO`；桥接脚本必须存在 |
| 页面里有文字但形状数是 0 | 说明导出退化成整页图片：用 `verify()` 看 `pictures`/`media_files` |

---

## 六、证据（2026-09-26 实测）

- WSL：Python 3.12.3、python-pptx 1.0.2、skia-pathops 0.9.2、uharfbuzz 0.56.1；
  中文字体经 `fc-list :lang=zh` 从 0 → 2 个家族（Microsoft YaHei / Microsoft YaHei UI）
- `attribution_guard.py` exit 0（引擎完整性门）
- 3 页课件：质量门 `errors=0 warnings=1`，导出成功；
  `verify` → slides=3 / shapes=29 / pictures=0 / media_files=0 / east_asian_font=Microsoft YaHei /
  `native_editable=true`；`slide1.xml` 内为 `<p:sp>` + `<a:prstGeom>`（形状 XML），无 `<p:pic>`
- 产物：`D:\hermes\education-ppt\niudun-diyi-dinglv_ppt169_20260926\exports\*.pptx`
- 单测：`bash scripts/run_tests.sh` → **322 例 OK（skipped=3）**；
  真链路用例 `EDU_TEST_PPT=1 python -m unittest tests.test_ppt_master` → 19 例 OK

---

## 七、还没做（下一步的边界，别当成已有能力）

1. **没接到对话链路**：目前是 Python API + 能力开关；要让「老师说要课件」自动触发，还需在
   生成/导出意图里加一处调用（谁调用、什么条件触发，属产品决策）。
2. **版式只有 4 种**，且不含图片/图表/公式排版（引擎支持，桥接层还没暴露）。
3. **未搬 ppt-master 的确认界面**（`confirm_ui`/`svg_editor`）——避免和现有 5174/3080 端口冲突。
4. 演示残留：引擎仓库内 `projects/` 有本次试跑生成的 `demo-jiaocai_20260926`、`probe-outside_20260926`
   两个目录（引擎是仓库外 clone，不影响本仓库）。
