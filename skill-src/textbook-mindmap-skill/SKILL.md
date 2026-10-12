---
name: textbook-mindmap
title: 教材章节思维导图
description: 读取本地教材库指定课本的某一章，生成该章节的 Markdown 思维导图（按小节层级 + 页码定位）。 当用户说「做个思维导图」「梳理这一章」「把这章画成导图」时使用。
version: 1.0.5
status: published
owner: test_agent
category: 教学
icon: M
plugin: TextbookMindMapSkill.py
outputs: ok, markdown
allowed_capabilities: textbook:read
data_scope: teaching_context
source: manual
updated: 2026-10-10 18:42
fixture: fixture.json
review: 新增 intents 触发词（供聊天快路使用）
intents: 思维导图, 导图, 梳理这一章, 画成导图, 章节导图, mindmap
inputs:
- name: book_id
  type: string
  description: 教材库里的书本唯一 id
- name: chapter_no
  type: integer
  description: 需要生成思维导图的章节序号
tests:
- input:
    book_id: math_grade7
    chapter_no: 2
  expect_contains: '- 章节思维导图'
  expect_fields:
  - ok
  - markdown
---

# textbook-mindmap

## 功能

读取本地教材库指定课本的某一章，生成该章节 Markdown 思维导图。

## 输入

- `book_id`：教材库里的书本唯一 id
- `chapter_no`：章节序号（如 2 表示第二章）

## 输出

- `ok`：是否成功
- `markdown`：思维导图正文（首行为 `- 章节思维导图`）

## 边界

只读教材库（`textbook:read`）；不写文件、不落库、不联网。
- 若 book_id 匹配不到课本，会回退到默认课本（当前不会额外提示，调用方请注意）。
