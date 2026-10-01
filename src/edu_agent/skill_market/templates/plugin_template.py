# -*- coding: utf-8 -*-
"""**人工编辑用的模板** —— Semantic Kernel 插件（`@kernel_function`）。

用法（SKILL-02 不会替你生成这个文件，必须人工写）：

1. 把本文件复制成 `plugin.py`，放进你的技能目录（草稿目录里允许放 .md/.json，
   **提交审核时**才把它一并提交，见 `store.DRAFT_BANNED_SUFFIX` 的说明）；
2. 在 SKILL.md 的 front-matter 里写 `plugin: plugin.py`；
3. 改掉下面的 TODO：技能 id、函数名、参数、以及真正要调用的模块 API；
4. 跑 `POST /api/skill-market/scan` 拿到 `verdict=pass`，再走提交流程。

三条铁律（审核会查）：

* **只传标识**：`knowledge_id` / `teaching_context` / `mastery_snapshot_id`。
  绝不接收或传递学生姓名、学号、作答明文 —— `contract.guard_payload()` 会在出站前拦。
* **不碰别人的库**：不 import 题库/学情的内部模型，不直连 SQLite/向量库，只调对外 API。
* **只产草稿**：生成的题目/内容一律标 `draft`，入库与审核由模块 2 负责。

函数签名里的中文说明会进 SK 的 function schema（SK 用 docstring / Annotated 生成描述），
所以**写清楚参数含义**，Agent 才知道怎么调你。
"""
from __future__ import annotations

from typing import Annotated

from semantic_kernel.functions import kernel_function

# 模块3 提供的跨模块契约（只传标识；出站前自动做隐私与白名单校验）
from edu_agent.skill_market.contract import ModuleClient, TeachingContext

SKILL_ID = "graph-node-question-draft"          # TODO: 改成你的技能 id（= SKILL.md 的 name）


class GraphNodeQuestionDraft:
    """按知识图谱节点生成题目草稿（TODO：改成你的技能名与说明）。"""

    def __init__(self, client: ModuleClient | None = None) -> None:
        # 允许注入（测试/审计用）；默认自己建一个只走 HTTP 的客户端
        self.client = client or ModuleClient()

    @kernel_function(
        name="draft_question",
        description="按图谱节点生成一道题目草稿（含答案与解析），只产草稿不入库。",
    )
    def draft_question(
        self,
        knowledge_id: Annotated[str, "知识图谱节点标识，例如 K1234"],
        stage: Annotated[str, "学段：初中 / 高中"] = "高中",
        difficulty: Annotated[str, "难度：基础 / 提高 / 拔高"] = "基础",
    ) -> dict:
        """按图谱节点生成题目草稿。

        Args:
            knowledge_id: 知识图谱节点标识（只传 id，不传学生信息）
            stage: 学段
            difficulty: 难度

        Returns:
            {"ok": bool, "draft": {...}, "knowledge_id": str}
        """
        ctx = TeachingContext(
            knowledge_id=knowledge_id,
            teaching_context={"stage": stage, "subject": "数学"},
            stage=stage, subject="数学",
        )
        errs = ctx.validate()
        if errs:
            return {"ok": False, "error": "；".join(errs)}

        # TODO: 换成你真正要调的模块2 对外 API；这里演示"读节点 → 生成草稿 → 交回模块2"
        node = self.client.read_knowledge_node(ctx)
        draft = {
            "kind": "question_draft",              # 永远只产草稿
            "stem": "（TODO：题目题干）",
            "answer": "（TODO：答案）",
            "analysis": "（TODO：解析）",
            "knowledge_id": knowledge_id,
            "difficulty": difficulty,
            "stage": stage,
        }
        saved = self.client.create_question_draft(ctx, draft)
        return {"ok": True, "draft": draft, "knowledge_id": knowledge_id,
                "node": node, "handed_to_module2": saved,
                "calls": self.client.summary()}

    @kernel_function(
        name="emit_card",
        description="把生成结果作为卡片推给教师工作台（模块4 的对外 API）。",
    )
    def emit_card(
        self,
        knowledge_id: Annotated[str, "知识图谱节点标识"],
        title: Annotated[str, "卡片标题"] = "题目草稿",
        summary: Annotated[str, "卡片摘要"] = "",
    ) -> dict:
        """把结果推给模块4 教师工作台（只传标识与卡片内容，不传学生数据）。"""
        ctx = TeachingContext(knowledge_id=knowledge_id)
        return self.client.emit_workbench_card(
            ctx, {"kind": "question_draft_card", "title": title, "summary": summary})
