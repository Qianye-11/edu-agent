---
name: textbook-mindmap
title: 教材章节思维导图
description: >
  读取本地教材库指定课本的某一章，生成该章节的 Markdown 思维导图。
  当用户说「做个思维导图」「梳理这一章」「把这章画成导图」时使用。
version: "1.0.0"
status: "draft"
owner: "test_agent"
category: 教学
icon: M
plugin: TextbookMindMapSkill.py
inputs:
  - name: book_id
    type: string
    description: 教材库里的书本唯一 id
  - name: chapter_no
    type: integer
    description: 章节序号（如 2 表示第二章）——参数名是 chapter_no，不是 chapter_num
outputs: ok, markdown
allowed_capabilities: ["textbook:read"]
data_scope: ["teaching_context"]
tests:
  - input: {"book_id": "math_grade7", "chapter_no": 2}
    expect_contains: "- 章节思维导图"
    expect_fields: ["ok", "markdown"]
---
# textbook-mindmap

## 功能

读取本地教材库指定课本的某一章，生成该章节 Markdown 思维导图。

## 调用入口（重要）

- 插件文件：`TextbookMindMapSkill.py`
- 类名：`TextbookMindMapSkill`
- **插件函数：`generate_mindmap`**（由 `@kernel_function(name="generate_mindmap")` 声明）
- SKILL.md **没有** `function_name` 字段：调用方通过
  `POST /api/skill-market/kernel/invoke` 的 `function` 字段传入 `generate_mindmap`；
  函数的参数名与描述由 `kernel_loader.describe_plugin_file()` 用 AST 静态读出（不执行代码）。

## 输入

- `book_id`：教材库里的书本唯一 id（匹配不到时**回退到默认课本**）
- `chapter_no`：章节序号（整数，如 2 表示第二章）；**不是 `chapter_num`** ——
  参数名写错时 Semantic Kernel 会忽略未知关键字、用默认值 0，从而静默返回第一章

## 输出

- `ok`：是否成功（布尔）
- `markdown`：思维导图正文（字符串，首行为 `- 章节思维导图`）

## 边界

只读教材库（`textbook:read`）；不写文件、不落库、不联网。
