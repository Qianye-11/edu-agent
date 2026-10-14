"""Agent A · 多轮答疑壳（官方 tool wrapper + checkpointer 模式）。

外层：LangGraph create_react_agent + 会话记忆（checkpointer），
把现有无状态 ask() 包成工具 ask_textbook：Agent 必须先检索教材再回答，
引用仍来自当轮检索（防幻觉不变），历史仅用于理解追问语境。

独立性：本模块只服务 Agent A，不触碰 Agent B/planner。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.prebuilt import create_react_agent

from edu_agent.config import get_chat_llm

SYSTEM = """你是课本教练（Agent A），面对学生多轮追问。
规则：
1. 每次回答前，必须调用 ask_textbook 工具检索教材并作答；不得凭记忆自答。
2. 学生可能说“刚才/这题/它/那……”等指代——结合本会话历史理解指代，把完整问题传给工具（必要时代入上一轮语境）。
3. 只依据工具返回作答（含页码引用 [n]）；越界/未收录由工具判定并诚实说明。
4. 语气：中学老师，讲清是什么为什么怎么用。"""


@tool
def ask_textbook(question: str) -> str:
    """检索本册教材并按教材作答（含页码引用）。多轮中若问题含指代，请先补全为完整问题。"""
    from edu_agent.generate import ask

    try:
        rec = ask(question)
    except Exception as e:
        return "检索/生成失败：" + type(e).__name__ + ": " + str(e)
    parts = [rec.answer_md]
    if rec.citations:
        parts.append("【引用】")
        for c in rec.citations[:4]:
            loc = (c.chapter + " " + (c.section or "") + " " + (c.heading or "")).strip()
            parts.append("[" + str(c.index) + "] " + loc + " p" + str(c.page))
    if rec.coverage == "low":
        parts.append("（本册教材未收录，按诚实降级处理）")
    return "\n".join(parts)


_checkpointer = InMemorySaver()
_agent = None
_agent_sig = None


def _skill_snapshot():
    """当前可用的技能集合（用于判断 agent 缓存是否要重建）。"""
    from edu_agent.skill_market import agent_tools

    try:
        skills = agent_tools.loadable_skills()
    except Exception:                                        # noqa: BLE001
        skills = []
    sig = tuple((s["skill"], tuple(f["name"] for f in s["functions"])) for s in skills)
    return skills, sig


def _get_agent():
    """构建/复用 Agent；**技能集合变了就重建**（装了新技能不用重启服务）。"""
    global _agent, _agent_sig
    skills, sig = _skill_snapshot()
    if _agent is None or sig != _agent_sig:
        from edu_agent.skill_market import agent_tools

        tools = [ask_textbook] + agent_tools.build_tools()
        prompt = SYSTEM + agent_tools.prompt_block(skills)
        _agent = create_react_agent(model=get_chat_llm(), tools=tools, prompt=prompt)
        _agent_sig = sig
    return _agent


def ask_chat(user_text: str, thread_id: str = "default") -> dict:
    """多轮入口：同一 thread_id 延续会话（checkpointer 记忆）。

    先走**确定性快路**（技能在 SKILL.md 里声明了 `intents:` 且话里参数齐全 → 直接调技能），
    没命中才交给 ReAct agent（它同样挂了已安装技能的工具，但本机小模型的
    function-calling 不稳，所以"说一句就出图"这类请求走快路更可靠）。
    """
    from edu_agent.generate import plainify_math
    from edu_agent.skill_market import agent_tools

    fast = None
    try:
        fast = agent_tools.match_intent(str(user_text or ""))
    except Exception:                                        # noqa: BLE001  快路失败不影响主链路
        fast = None
    if fast:
        body = str(fast.get("text") or "")
        head = "（由技能 **%s** 生成）" % fast["title"]
        out = {"thread_id": thread_id, "answer": body, "skill": fast["skill"],
               "function": fast["function"], "args": fast["args"], "source": "skill_fastpath"}
        if body.strip():
            out["answer"] = head + "\n\n" + body
        return out

    config = {"configurable": {"thread_id": thread_id}}
    result = _get_agent().invoke({"messages": [HumanMessage(content=user_text)]}, config=config)
    msgs = result.get("messages", [])
    last = next((m for m in reversed(msgs) if isinstance(m, AIMessage) and m.content), None)
    answer = plainify_math(last.content) if last else ""
    return {"thread_id": thread_id, "answer": answer, "source": "agent"}


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("q", help="学生问题")
    ap.add_argument("--thread", default="demo1")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    out = ask_chat(args.q, thread_id=args.thread)
    print(out["answer"])
