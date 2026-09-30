---
name: ollama-qa
title: 本机 Ollama 问答
description: 本机 Ollama 问答：提问即答，模型跑在本机，免 API Key、可离线
category: 通用能力
icon: Q
entry: scripts/ask.py
trial: ask
version: 1.0.0
author: 本工作区（本地技能，随仓库版本管理）
runtime: python3（仅标准库）+ 本机 Ollama（默认 http://127.0.0.1:11434）
default_model: qwen2.5:3b
---

# Ollama 问答 Skill（本机推理）

给课本教练加一个「**我提问 → AI 回答**」的能力，模型跑在**本机 Ollama** 上：
不需要任何 API Key、不消耗云端额度、断网也能用。

> front-matter 的 `description` 必须写成**单行** —— 平台的 `capabilities._skill_files()`
> 是按行读 `name:` / `description:` 的，写成 YAML 折叠块（`>`）会被读成字面量 `>`。

## 这个技能做什么

- 输入：一个问题（纯文本）
- 输出：本机模型的回答 + 用的模型名 + 耗时
- **不检索教材**：它和「答疑 Agent A」是两回事。A 是「依据教材作答」，
  这个技能是「通用问答」——闲聊、写作、代码、解释概念都可以，但答案不带教材引用。
- **不依赖 MCP**：不需要任何 MCP 服务，自己直接调 Ollama 的 HTTP 接口。

## 怎么被调用

平台侧（`web_server.py`）只做转发，执行全部封在本技能内：

```python
# capabilities.ask_ollama_qa(question) 等价于下面这条命令
python skills/ollama-qa/scripts/ask.py "<问题>" --json
# -> {"ok": true, "model": "qwen2.5:3b", "question": "...", "answer": "..."}
```

界面入口：**Skill 市场 → 通用能力 → 本机 Ollama 问答 → 提问试试**（详情页里有提问框）。

技能也能脱离平台单独跑（方便排查）：

```bat
skills\ollama-qa\scripts\ask.cmd "什么是导数"         一问一答
skills\ollama-qa\scripts\ask.cmd                      交互模式（q 退出）
skills\ollama-qa\scripts\ask.cmd --selftest            自检
skills\ollama-qa\scripts\ask.cmd --list-models         看本机模型
```

## 脚本参数

| 参数 | 说明 |
|---|---|
| `--model NAME` | 指定模型，默认自动挑 |
| `--system TEXT` | 系统提示词 |
| `--temperature F` | 采样温度，默认 0.6 |
| `--timeout S` | 单次超时，默认 300 秒（平台调用时传 600） |
| `--history FILE` | 多轮上下文文件，跨次调用续接对话 |
| `--json` | 机器可读输出（平台调用时使用） |
| `--interactive` | 交互模式 |
| `--list-models` | 列出本机模型（标出哪些是向量模型、不能对话） |
| `--selftest` | 自检：连通性 + 模型清单 + 一次真实问答 |

## 环境变量

| 变量 | 默认 | 说明 |
|---|---|---|
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Ollama 地址 |
| `OLLAMA_QA_MODEL` | 自动挑（优先 `qwen2.5:3b`） | 默认模型 |
| `OLLAMA_QA_SYSTEM` | 内置中文助手提示词 | 系统提示词 |
| `OLLAMA_QA_TIMEOUT` | `300` | 超时秒数 |

模型自动挑选顺序：`OLLAMA_QA_MODEL` → `qwen2.5:3b` → `qwen2.5:7b` → `qwen2.5:1.5b` →
`qwen3:4b` → `deepseek-r1:1.5b` → 任意非向量模型；**向量模型（bge-* / *-embed-* / rerank）会被跳过**。

## 故障与处理

| 现象 | 原因 | 处理 |
|---|---|---|
| `连不上 Ollama` | 服务没起 | 双击桌面「启动-Ollama」，或运行 `.tools\start-ollama.cmd` |
| `模型 X 不在本机` | 名字不对/没装 | `ask.py --list-models` 看现有模型，再 `--model` 指定 |
| 回答慢 | 纯 CPU 推理（本机无独显） | 换更小的模型，或调大 `--timeout` |
| 界面提示「技能已在 MCP 服务中关闭」 | 开关被关了 | 到「MCP 服务」或本页把开关打开 |

## 边界（写清楚，免得误用）

- 本技能**不联网**：问「今天的新闻」这类问题它只能靠模型自身记忆，容易编。
- 本机是 3B 小模型，**回答可能有事实错误**；要严谨结论请用答疑 A（教材依据）或换个更大的模型。
