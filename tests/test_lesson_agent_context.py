from types import SimpleNamespace

from edu_agent import planner
from edu_agent.web_server import PlanAssistantReq, _plan_assistant_instruction


def test_plan_assistant_request_has_isolated_history_lists():
    first = PlanAssistantReq()
    second = PlanAssistantReq()

    first.history.append({"role": "user", "text": "第一段对话"})

    assert second.history == []


def test_plan_assistant_instruction_combines_chat_memory_and_references():
    prompt = _plan_assistant_instruction(
        "把导入环节改成问题情境",
        history=[
            {"role": "user", "text": "这节课面向高一普通班"},
            {"role": "assistant", "text": "我会降低起点并增加支架。"},
        ],
        memory_prefix="教师偏好探究式课堂；每课时 45 分钟",
        reference_text="参考教案：先猜想，再验证。",
    )

    assert "【用户本次指令】" in prompt
    assert "教师：这节课面向高一普通班" in prompt
    assert "Agent B：我会降低起点并增加支架。" in prompt
    assert "教师偏好探究式课堂" in prompt
    assert "参考教案：先猜想，再验证。" in prompt


def test_plan_assistant_instruction_limits_history_and_message_size():
    history = [{"role": "user", "text": f"第{i}轮" + "很长" * 400} for i in range(14)]

    prompt = _plan_assistant_instruction("继续", history=history)

    assert "第0轮" not in prompt
    assert "第3轮" not in prompt
    assert "第4轮" in prompt
    assert len(prompt) < 6000


def test_revise_plan_stream_yields_deltas_before_validated_record():
    raw = (
        '{"title":"流式教案","objectives":["掌握概念"],"key_points":[],'
        '"flow_steps":[{"phase":"导入","minutes":5,"content":"情境提问"}],' 
        '"mistakes":[],"citations":[]}'
    )

    class FakeLlm:
        def stream(self, _messages):
            for part in (raw[:38], raw[38:91], raw[91:]):
                yield SimpleNamespace(content=part)

    original_retrieve = planner.retrieve
    original_messages = planner.build_messages
    original_llm = planner.get_chat_llm
    try:
        planner.retrieve = lambda *_args, **_kwargs: [SimpleNamespace(metadata={}, text="教材片段")]
        planner.build_messages = lambda *_args, **_kwargs: [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "user"},
        ]
        planner.get_chat_llm = lambda **_kwargs: FakeLlm()
        events = list(planner.revise_plan_stream(
            "函数", "生成教案", planner.PlanRecord(title="函数"), template={}
        ))
    finally:
        planner.retrieve = original_retrieve
        planner.build_messages = original_messages
        planner.get_chat_llm = original_llm

    assert [event["type"] for event in events].count("delta") == 3
    assert events[-1]["type"] == "done"
    assert events[-1]["record"].title == "流式教案"
    assert events[-1]["record"].flow_steps[0].content == "情境提问"


def test_stream_chunk_text_accepts_content_blocks():
    chunk = SimpleNamespace(content=[{"type": "text", "text": "第一段"}, "第二段"])

    assert planner._stream_chunk_text(chunk) == "第一段第二段"
