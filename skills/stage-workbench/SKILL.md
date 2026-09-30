---
name: stage-workbench
title: 个性化工作台
description: 按学段（初中/高中）动态返回对应的工作台模块配置，结构化输出供前端渲染（Semantic Kernel 实现）
category: 通用能力
icon: W
entry: scripts/workbench.py
trial: workbench
version: 1.0.0
---

# 个性化工作台 Skill（Semantic Kernel 实现）

**一个学段参数进，一套工作台界面配置出**：初中返回初中专属界面，高中返回高中专属界面。
输出是**结构化 JSON**，前端拿到就能渲染；实现用 Semantic Kernel 的 KernelFunction 组织。

> front-matter 说明：`description` 必须**单行**（平台按行读取）；`entry` 指向该技能的入口脚本；
> `trial: workbench` 让平台知道「这个技能自带一个可交互的试用面板」。
> 只要这个目录放在 `<repo>/skills/<id>/` 下，**Skill 市场会自动加载它**（无需改平台代码）。

## 三个模块（代码分层）

| 文件 | 层 | 职责 |
|---|---|---|
| `scripts/stage_config.py` | **数据层** | 初中/高中的模块表、主题、侧边栏、快捷动作；纯数据 + 纯函数，不依赖 SK |
| `scripts/workbench_plugin.py` | **SK 插件层** | 用 `@kernel_function` 把能力包成带 name/description 的 KernelFunction |
| `scripts/workbench.py` | **编排/入口** | 建 Kernel → 注册插件 → 按参数调用函数 → 输出 JSON；含 `--engine direct` 降级路径 |

## KernelFunction 清单（都带 description）

| 函数 | 参数 | 作用 |
|---|---|---|
| `get_workbench_config` | `stage` | **主入口**：学段 → 完整工作台配置（模块/主题/侧边栏/快捷动作/布局建议） |
| `list_stages` | — | 支持的学段、年级范围、模块数、风格、别名 |
| `get_module` | `stage, module_id` | 单个模块详情（模块 id 或中文名都认） |
| `stage_summary` | `stage` | 一句话概述该学段重点（可做副标题） |
| `diff_stages` | — | 初中/高中模块差异与各自独有模块 |
| `compose_workbench` | `stage` | 组合返回：配置 + 概述 |
| `describe_plugin` | — | 插件自描述（函数与 description 清单） |

## 调用方式

```bat
:: 平台（网关）调用
python skills\stage-workbench\scripts\workbench.py --stage 初中 --json

:: 人工排查
skills\stage-workbench\scripts\workbench.cmd --stage 高中
skills\stage-workbench\scripts\workbench.cmd --list-stages
skills\stage-workbench\scripts\workbench.cmd --diff
skills\stage-workbench\scripts\workbench.cmd --describe
skills\stage-workbench\scripts\workbench.cmd --selftest
skills\stage-workbench\scripts\workbench.cmd --stage 初中 --engine direct   :: 不用 SK
```

接口形态（平台侧 `POST /api/skill/run`）：

```json
{"skill": "stage-workbench", "args": {"stage": "初中"}}
```

## 输出结构（前端按此渲染）

```json
{
  "ok": true,
  "skill": "stage-workbench",
  "function": "get_workbench_config",
  "engine": "semantic-kernel 1.44.1",
  "config": {
    "stage": "初中",
    "display_name": "初中",
    "grade_hint": "七 ~ 九年级",
    "theme": {"tone": "活泼明亮", "primary": "#3b82f6", "radius": "16px", "density": "comfortable"},
    "greeting": "同学你好呀！今天想先练几道题，还是把不会的题理一理？",
    "sidebar": [{"id": "home", "title": "今日首页", "icon": "H"}],
    "modules": [
      {"id": "daily-practice", "title": "每日一练", "icon": "P", "size": "large",
       "desc": "…", "tags": ["基础巩固"], "actions": [{"label": "开始练习", "kind": "practice"}]}
    ],
    "quick_actions": [{"id": "ask", "label": "问一道题", "icon": "?", "kind": "ask"}],
    "layout": {"columns": 3, "featured": ["daily-practice", "mistake-book"]},
    "notes": "初中端强调「基础巩固 + 兴趣 + 习惯」…"
  }
}
```

## 两个学段的差异（设计意图）

| | 初中 | 高中 |
|---|---|---|
| 风格 | 活泼明亮、圆角大（16px）、字号偏大 | 沉稳克制、圆角小（10px）、信息密度高 |
| 模块 | 每日一练、图形化错题本、知识点卡片、趣味数学闯关、几何直观实验室、学习习惯打卡 | 专题突破、压轴题精练、限时套卷、错题归因分析、知识网络图、高考倒计时 |
| 侧重 | 基础巩固、兴趣、习惯养成 | 专题化、得分效率、自我诊断 |

## 扩展方式

- 加模块 / 改文案 / 换配色：只改 `scripts/stage_config.py` 的数据表（`validate()` 会自检 id 合法性与必填字段）。
- 加函数：在 `workbench_plugin.py` 里加一个带 `@kernel_function(name=..., description=...)` 的方法，
  CLI 的 `--describe` 会自动列出来。
- 加学段（如「小学」）：在 `_STAGE_ALIASES` 与 `_CONFIGS` 各加一项即可，前缀无需改动。

## 依赖与降级

- 需要 `semantic-kernel`（已写进 `edu-agent\requirements.txt`）。
- **没装也能用**：插件层内置降级装饰器，CLI 自动切到 `--engine direct`（直接调方法），
  输出结构完全一致，只是少了 Kernel 那一层编排 —— 不会因为一个可选依赖拖垮整条链路。
