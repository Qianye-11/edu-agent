# stage-workbench —— 个性化工作台技能

给课本教练加一个「**按学段定制工作台**」的技能：传入**初中**或**高中**，
返回该学段专属的工作台界面配置（结构化 JSON，前端直接渲染）。

用 **Semantic Kernel** 组织实现：每个能力都是一个带 `name` / `description` 的 KernelFunction。

## 目录结构

```
edu-agent/skills/stage-workbench/
├── SKILL.md                     技能入口（front-matter 决定 Skill 市场里显示的卡片）
├── README.md                    本文件
└── scripts/
    ├── stage_config.py          数据层：初中/高中的模块表与主题（改这里就能改界面配置）
    ├── workbench_plugin.py      SK 插件层：@kernel_function 方法（带 description）
    ├── workbench.py             编排/入口：Kernel → 插件 → 调用 → JSON
    └── workbench.cmd            给人用的包装（纯 ASCII，自动找 venv 解释器）
```

## 在课本教练里怎么用

1. 打开 <http://127.0.0.1:5174/> → 左侧 **Skill 市场** → 分类 **通用能力** → **个性化工作台**
2. 详情页底部「**工作台预览**」：点「初中」或「高中」，直接看该学段的工作台模块配置被渲染出来的样子
3. 该技能在「MCP 服务」页有开关；关掉后接口返回 `gate: capability`

## 命令行

```bat
cd D:\ruanjian\deep code\edu-agent
skills\stage-workbench\scripts\workbench.cmd --stage 初中          :: 初中配置
skills\stage-workbench\scripts\workbench.cmd --stage 高中          :: 高中配置
skills\stage-workbench\scripts\workbench.cmd --list-stages         :: 支持的学段
skills\stage-workbench\scripts\workbench.cmd --diff                :: 两学段差异
skills\stage-workbench\scripts\workbench.cmd --describe            :: 函数与描述清单
skills\stage-workbench\scripts\workbench.cmd --selftest            :: 自检
```

## 接口

```
POST http://127.0.0.1:5174/api/skill/run
Body: {"skill": "stage-workbench", "args": {"stage": "初中"}}
返回: {"ok": true, "engine": "semantic-kernel 1.44.1", "config": {...}}
```

`/api/skill/run` 是**通用**技能调用口：`args` 里的键值会被转成命令行参数（`{"stage":"初中"}` → `--stage 初中`），
所以任何放在 `skills/` 下的技能都能被它调起来，不需要为每个技能写接口。

## 为什么用 Semantic Kernel

这层包装不是摆设，有两个实际好处：

1. **自描述**：`kernel.plugins[...].functions[...]` 里能读到每个函数的 name/description，
   将来若要交给模型做「该调哪个函数」的选择，不需要另外维护一份文档；
2. **统一编排**：`Kernel.invoke(plugin_name, function_name, **kwargs)` 与
   「直接调方法」两条路径返回完全一致的结果，方便对照排查（`--engine direct` 就是后者）。

## 已知边界

- 目前覆盖**初中/高中**两个学段；加「小学」只需在 `stage_config.py` 里加别名与配置项。
- 配置是**静态表**（不是模型生成），所以稳定、可预期；想让它「按学生画像动态生成」，
  可以在插件里再加一个 KernelFunction，用一句话描述需求后交给模型产出模块列表。
