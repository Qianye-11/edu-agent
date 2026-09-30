# ollama-qa —— 课本教练的本机 Ollama 问答技能

在课本教练（edu-agent）里加一个「**我提问 → AI 回答**」的能力，模型跑在本机 Ollama 上：
**免 API Key、不联网、不花云端额度**。

## 目录结构（这就是本平台认可的 skill 封装形态）

```
edu-agent/skills/ollama-qa/
├── SKILL.md              ← 技能入口：front-matter 的 name / description 会被平台读走
│                            （capabilities._skill_files() 按行读取，description 必须单行）
├── README.md             ← 本文件
└── scripts/
    ├── ask.py            ← 全部实现：零依赖（只用标准库），封装 Ollama 调用
    └── ask.cmd           ← 给人手工调用用的包装（纯 ASCII，自动找 venv 解释器）
```

平台侧只做三件事：注册（`capabilities.SKILL_SPECS`）、开关（`/api/capabilities/toggle`）、
转发（`POST /api/skill/ask`）。**执行逻辑全部封在本技能内**，所以技能能独立演进、也能脱离平台单独跑。

## 在课本教练里怎么用

1. 打开 <http://127.0.0.1:5174/> → 左侧「Skill 市场」→ 分类 **通用能力** →
   **本机 Ollama 问答** → 点开详情页
2. 详情页底部有「**提问试试**」输入框：写问题 → 点「提问」（或 Ctrl+Enter）
3. 答案直接显示在下面，并标注**用的哪个模型**和**耗时**

「MCP 服务」页面里有同一个技能的开关，关掉后平台会直接短路（返回 `gate: capability`）。

## 命令行怎么用

```bat
cd D:\ruanjian\deep code\edu-agent
skills\ollama-qa\scripts\ask.cmd "什么是导数？用两句话说明"
skills\ollama-qa\scripts\ask.cmd                        :: 交互模式
skills\ollama-qa\scripts\ask.cmd --selftest             :: 自检
skills\ollama-qa\scripts\ask.cmd --list-models          :: 本机模型清单
```

## 接口（给别的程序调用）

```
POST http://127.0.0.1:5174/api/skill/ask
Body: {"question": "什么是导数", "skill": "ollama-qa"}
返回: {"ok": true, "answer": "...", "model": "qwen2.5:3b", "question": "...", "ms": 5231}
失败: {"ok": false, "error": "..."}；技能被关闭时带 "gate": "capability"
```

## 配置

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | Ollama 地址 |
| `OLLAMA_QA_MODEL` | 自动挑（优先 `qwen2.5:3b`） | 默认模型 |
| `OLLAMA_QA_SYSTEM` | 内置中文助手提示词 | 系统提示词 |
| `OLLAMA_QA_TIMEOUT` | `300` | 超时秒数（平台调用给 600） |

## 与「答疑 Agent A」的区别（重要）

| | 答疑 A | 本技能 |
|---|---|---|
| 依据 | 教材检索（Chroma + bge-m3） | 模型自身知识 |
| 引用 | 有（教材页码/片段） | 无 |
| 用途 | 严谨的教材答疑 | 通用问答：闲聊、写作、代码、概念解释 |
| 模型 | DeepSeek 云端（需 key） | 本机 Ollama（免 key） |

## 已知限制

- 本机是纯 CPU、默认 3B 小模型：**回答可能有事实错误**，重要结论请再核实。
- 不联网：问时事类问题只能靠模型记忆，容易编。
