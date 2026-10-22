from __future__ import annotations

from jarvis.agent_chat.runner_api import _response_token_limit, fit_messages_to_context
from jarvis.core.protocols import BrainMessage


def test_keeps_history_unchanged_when_it_fits() -> None:
    messages = [
        BrainMessage(role="user", content="first"),
        BrainMessage(role="assistant", content="second"),
    ]

    result = fit_messages_to_context(messages, context_window=32_000)

    assert result == tuple(messages)


def test_drops_old_exchanges_before_recent_ones() -> None:
    messages = [
        BrainMessage(role="user", content="old " * 5000),
        BrainMessage(role="assistant", content="old answer " * 5000),
        BrainMessage(role="user", content="recent question"),
        BrainMessage(role="assistant", content="recent answer"),
    ]

    result = fit_messages_to_context(messages, context_window=1024)

    assert [m.content for m in result] == ["recent question", "recent answer"]


def test_keeps_tool_call_and_matching_result_as_one_round() -> None:
    messages = [
        BrainMessage(role="user", content="inspect"),
        BrainMessage(
            role="assistant",
            content=[{"type": "tool_use", "id": "call-1", "name": "Read", "input": {}}],
        ),
        BrainMessage(role="tool", content="result", tool_call_id="call-1", name="Read"),
        BrainMessage(role="assistant", content="The result is ready."),
    ]

    result = fit_messages_to_context(messages, context_window=1024)

    assistant_calls = [
        block["id"]
        for message in result
        if message.role == "assistant" and isinstance(message.content, list)
        for block in message.content
        if block.get("type") == "tool_use"
    ]
    tool_results = [message.tool_call_id for message in result if message.role == "tool"]
    assert assistant_calls == tool_results == ["call-1"]


def test_large_tool_result_is_clipped_without_changing_saved_message() -> None:
    original = BrainMessage(
        role="tool",
        content="begin " + ("x" * 30_000) + " end",
        tool_call_id="call-2",
        name="Read",
    )
    messages = [
        BrainMessage(role="user", content="read"),
        BrainMessage(
            role="assistant",
            content=[{"type": "tool_use", "id": "call-2", "name": "Read", "input": {}}],
        ),
        original,
        BrainMessage(role="assistant", content="done"),
    ]

    result = fit_messages_to_context(messages, context_window=1024)

    tool_message = next(message for message in result if message.role == "tool")
    assert len(tool_message.content) < len(original.content)
    assert tool_message.content.startswith("begin ")
    assert tool_message.content.endswith(" end")
    assert original.content == "begin " + ("x" * 30_000) + " end"


def test_unbounded_provider_window_does_not_trim() -> None:
    messages = [BrainMessage(role="user", content="x" * 30_000)]

    result = fit_messages_to_context(messages, context_window=0)

    assert result == tuple(messages)


def test_oversized_current_prompt_keeps_its_beginning_and_end() -> None:
    prompt = "begin " + ("p" * 30_000) + " end"
    result = fit_messages_to_context(
        [BrainMessage(role="user", content=prompt)], context_window=1024
    )

    assert len(result) == 1
    assert result[0].content.startswith("begin ")
    assert result[0].content.endswith(" end")
    assert len(result[0].content) < len(prompt)


def test_tool_arguments_are_never_clipped() -> None:
    arguments = {"path": "C:/" + ("folder/" * 100)}
    messages = [
        BrainMessage(role="user", content="read it"),
        BrainMessage(
            role="assistant",
            content=[{
                "type": "tool_use",
                "id": "call-3",
                "name": "Read",
                "input": arguments,
            }],
        ),
        BrainMessage(role="tool", content="x" * 20_000, tool_call_id="call-3", name="Read"),
    ]

    result = fit_messages_to_context(messages, context_window=1024)

    call = next(
        block
        for message in result
        if message.role == "assistant" and isinstance(message.content, list)
        for block in message.content
        if block.get("type") == "tool_use"
    )
    assert call["input"] == arguments
    assert [m.tool_call_id for m in result if m.role == "tool"] == ["call-3"]


def test_response_allowance_scales_to_model_window() -> None:
    assert _response_token_limit(128_000) == 8192
    assert _response_token_limit(4096) == 614
    assert _response_token_limit(1024) == 256
    assert _response_token_limit(0) == 8192
