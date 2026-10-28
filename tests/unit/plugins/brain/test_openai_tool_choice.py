"""The ``tool_choice`` request hint reaches an OpenAI-compatible server.

The tool loop sends ``tool_choice="required"`` on the retry after a model
announced an action in words and called nothing; llama.cpp then enforces a
call with a grammar. Without tools there is nothing to choose, so the hint
is not sent.
"""

from __future__ import annotations

from typing import Any

import pytest

from jarvis.core.protocols import BrainMessage, BrainRequest
from jarvis.plugins.brain import _openai_base
from jarvis.plugins.brain._openai_base import stream_complete

_TOOL = {"name": "system_volume", "description": "volume", "input_schema": {}}


@pytest.fixture(autouse=True)
def _fresh_adaptation_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_openai_base, "_PARAM_ADAPTATION_CACHE", {})


class _EmptyStream:
    def __aiter__(self) -> _EmptyStream:
        return self

    async def __anext__(self) -> Any:
        raise StopAsyncIteration


class _RecordingClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        chat = type("Chat", (), {})()
        chat.completions = type("Completions", (), {})()
        chat.completions.create = self._create
        self.chat = chat

    async def _create(self, **kwargs: Any) -> _EmptyStream:
        self.calls.append(kwargs)
        return _EmptyStream()


async def _send(**kw: Any) -> dict[str, Any]:
    client = _RecordingClient()
    req = BrainRequest(messages=(BrainMessage(role="user", content="mute"),), **kw)
    async for _ in stream_complete(client, "gemma-4b-qat", req):
        pass
    return client.calls[0]


async def test_required_is_forwarded_with_the_tools() -> None:
    call = await _send(tools=(_TOOL,), tool_choice="required")
    assert call["tool_choice"] == "required"


async def test_no_hint_leaves_the_choice_to_the_model() -> None:
    call = await _send(tools=(_TOOL,))
    assert "tool_choice" not in call


async def test_no_tools_sends_no_tool_choice() -> None:
    call = await _send(tool_choice="required")
    assert "tool_choice" not in call
