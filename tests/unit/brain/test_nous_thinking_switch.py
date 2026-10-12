"""Nous Portal: a fast turn asks the model to skip its reasoning pass.

Step 3.7 Flash thinks for many seconds before its first word. A request with
``reasoning_effort="none"``, or ``[brain.providers.nous].thinking_budget = 0``,
carries ``{"reasoning": {"enabled": false}}`` next to the required user tag; an
endpoint that refuses it gets the plain request once.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

import jarvis.plugins.brain.nous as nous_mod
from jarvis.core.protocols import BrainDelta, BrainMessage, BrainRequest
from jarvis.plugins.brain.nous import NousBrain


def _req(reasoning_effort: Any = None) -> BrainRequest:
    return BrainRequest(
        messages=(BrainMessage(role="user", content="hi"),),
        reasoning_effort=reasoning_effort,
    )


def _brain() -> NousBrain:
    brain = NousBrain("stepfun/step-3.7-flash:free")
    brain._client = object()
    return brain


def _recording(seen: list[tuple[Any, Any]], *, refuse: bool = False):
    async def fake(client: Any, model: str, req: BrainRequest, *, extra_body: Any = None,
                   **_: Any) -> AsyncIterator[BrainDelta]:
        seen.append((extra_body, req.reasoning_effort))
        if refuse and "reasoning" in (extra_body or {}):
            raise RuntimeError("400 unsupported parameter: reasoning")
        yield BrainDelta(content="ok")

    return fake


@pytest.fixture(autouse=True)
def _config_thinking_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nous_mod, "thinking_off_by_config", lambda: False)


def test_the_brain_declares_the_thinking_switch() -> None:
    assert NousBrain.supports_thinking_switch is True


@pytest.mark.asyncio
async def test_a_fast_turn_turns_reasoning_off_and_keeps_the_user_tag(monkeypatch) -> None:
    seen: list[tuple[Any, Any]] = []
    monkeypatch.setattr(nous_mod, "stream_complete", _recording(seen))
    [d async for d in _brain().complete(_req("none"))]
    assert seen == [({"tags": ["user=jarvis"], "reasoning": {"enabled": False}}, "none")]


@pytest.mark.asyncio
async def test_thinking_budget_zero_turns_it_off_on_every_turn(monkeypatch) -> None:
    seen: list[tuple[Any, Any]] = []
    monkeypatch.setattr(nous_mod, "stream_complete", _recording(seen))
    monkeypatch.setattr(nous_mod, "thinking_off_by_config", lambda: True)
    [d async for d in _brain().complete(_req())]
    assert seen[0][0]["reasoning"] == {"enabled": False}


@pytest.mark.asyncio
async def test_a_normal_turn_sends_only_the_user_tag(monkeypatch) -> None:
    seen: list[tuple[Any, Any]] = []
    monkeypatch.setattr(nous_mod, "stream_complete", _recording(seen))
    [d async for d in _brain().complete(_req())]
    assert seen == [({"tags": ["user=jarvis"]}, None)]


@pytest.mark.asyncio
async def test_a_refused_opt_out_retries_once_without_it(monkeypatch) -> None:
    seen: list[tuple[Any, Any]] = []
    monkeypatch.setattr(nous_mod, "stream_complete", _recording(seen, refuse=True))
    deltas = [d async for d in _brain().complete(_req("none"))]
    assert [d.content for d in deltas] == ["ok"]
    assert seen[-1] == ({"tags": ["user=jarvis"]}, None)
