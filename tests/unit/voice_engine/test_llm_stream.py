"""Streaming chat parsing against a scripted loopback Ollama."""

from __future__ import annotations

import pytest

from jarvis.voice_engine.llm import OllamaChat, OpenAIChat, make_chat, openai_messages
from tests.fakes.fake_ollama_chat_stream import FakeOllamaChatStream


def _done(**counters: int) -> dict:
    return {"message": {"content": ""}, "done": True, **counters}


def test_text_clauses_and_counters() -> None:
    with FakeOllamaChatStream() as fake:
        fake.script([
            {"message": {"content": "The moon is about "}, "done": False},
            {"message": {"content": "384,000 km away. "}, "done": False},
            {"message": {"content": "That is far."}, "done": False},
            _done(prompt_eval_count=120, prompt_eval_cached_count=100,
                  prompt_eval_duration=50_000_000, eval_count=20, eval_duration=200_000_000),
        ])
        result = OllamaChat("m", base_url=fake.base_url).chat([{"role": "user", "content": "?"}])
        body = fake.requests[0]["body"]
    assert result.error == ""
    assert result.clauses == ["The moon is about 384,000 km away.", "That is far."]
    assert result.prompt_tokens == 120 and result.prompt_cached_tokens == 100
    assert result.generation_tps == 100.0
    assert result.t_first_token is not None and result.t_first_clause is not None
    assert body["think"] is False and body["options"]["num_ctx"] == 8192
    assert body["stream"] is True


def test_tool_calls_are_collected() -> None:
    with FakeOllamaChatStream() as fake:
        fake.script([
            {"message": {"content": "", "tool_calls": [
                {"function": {"name": "set_timer", "arguments": {"minutes": 10}}}]}, "done": False},
            _done(),
        ])
        result = OllamaChat("m", base_url=fake.base_url).chat(
            [{"role": "user", "content": "timer"}], tools=[{"type": "function"}]
        )
    assert result.tool_calls == [{"name": "set_timer", "arguments": {"minutes": 10}}]
    assert result.text == ""


def test_models_without_a_thinking_switch_are_retried_without_it() -> None:
    with FakeOllamaChatStream() as fake:
        fake.script_error(400, '{"error":"\\"m\\" does not support thinking"}')
        fake.script([{"message": {"content": "Hi."}, "done": False}, _done()])
        chat = OllamaChat("m", base_url=fake.base_url)
        result = chat.chat([{"role": "user", "content": "hi"}])
        second = chat.chat([{"role": "user", "content": "again"}])
        bodies = [r["body"] for r in fake.requests]
    assert result.text == "Hi."
    assert "think" in bodies[0] and "think" not in bodies[1] and "think" not in bodies[2]
    assert second.error == ""


def test_server_errors_are_reported_not_raised() -> None:
    with FakeOllamaChatStream() as fake:
        fake.script_error(500, "boom")
        result = OllamaChat("m", base_url=fake.base_url).chat([{"role": "user", "content": "x"}])
    assert result.error.startswith("HTTP 500")


# ── OpenAI-compatible servers (llama-server, LM Studio, vLLM) ────────────
def _chunk(content: str = "", **delta: object) -> dict:
    return {"choices": [{"delta": {"content": content, **delta}}]}


def test_openai_stream_clauses_counters_and_no_thinking() -> None:
    with FakeOllamaChatStream() as fake:
        fake.script([
            _chunk("The moon is about "),
            _chunk("384,000 km away. "),
            _chunk("That is far."),
            {"choices": [], "timings": {"cache_n": 100, "prompt_n": 20, "prompt_ms": 50.0,
                                        "predicted_n": 20, "predicted_ms": 200.0}},
        ], sse=True)
        chat = OpenAIChat("qwen", base_url=f"{fake.base_url}/v1")
        result = chat.chat([{"role": "user", "content": "?"}])
        request = fake.requests[0]
    assert request["path"] == "/v1/chat/completions"
    assert result.error == ""
    assert result.clauses == ["The moon is about 384,000 km away.", "That is far."]
    assert result.prompt_tokens == 120 and result.prompt_cached_tokens == 100
    assert result.generation_tps == 100.0
    body = request["body"]
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["stream"] is True and body["model"] == "qwen"


def test_openai_tool_call_fragments_are_joined() -> None:
    def call(**function: str) -> dict:
        return _chunk(tool_calls=[{"index": 0, "function": function}])

    with FakeOllamaChatStream() as fake:
        fake.script([call(name="set_timer", arguments='{"minu'), call(arguments='tes": 10}')],
                    sse=True)
        result = OpenAIChat("m", base_url=fake.base_url).chat(
            [{"role": "user", "content": "timer"}], tools=[{"type": "function"}]
        )
    assert result.tool_calls == [{"name": "set_timer", "arguments": {"minutes": 10}}]


def test_openai_errors_are_reported_and_warm_needs_a_listed_model() -> None:
    with FakeOllamaChatStream() as fake:
        fake.script_error(500, "boom")
        result = OpenAIChat("m", base_url=fake.base_url).chat([{"role": "user", "content": "x"}])
        assert OpenAIChat("m", base_url=fake.base_url).warm() >= 0.0
        fake.models = []
        with pytest.raises(RuntimeError, match="lists no models"):
            OpenAIChat("m", base_url=fake.base_url).warm()
    assert result.error.startswith("HTTP 500")


def test_history_is_converted_to_the_openai_shape() -> None:
    history = [
        {"role": "user", "content": "timer"},
        {"role": "assistant", "content": "",
         "tool_calls": [{"function": {"name": "set_timer", "arguments": {"minutes": 10}}}]},
        {"role": "tool", "tool_name": "set_timer", "content": '{"success": true}'},
    ]
    _, assistant, tool = openai_messages(history)
    [call] = assistant["tool_calls"]
    assert call["function"] == {"name": "set_timer", "arguments": '{"minutes": 10}'}
    assert tool == {"role": "tool", "tool_call_id": call["id"], "content": '{"success": true}'}


def test_make_chat_picks_the_client_by_api() -> None:
    assert isinstance(make_chat("openai", "m", base_url="http://127.0.0.1:11435"), OpenAIChat)
    assert isinstance(make_chat("ollama", "m", base_url="http://127.0.0.1:11434"), OllamaChat)
