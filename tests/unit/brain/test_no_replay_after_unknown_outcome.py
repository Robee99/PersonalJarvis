"""A provider attempt that fails after an action started is never replayed.

If the model connection drops after a consequential tool already started, the
tool's outcome is unknown. Falling through to the next provider would run the
whole turn again and could repeat the action (send the message twice, create
two files). The Brain must stop the fallback and say honestly that it cannot
tell whether the action went through. Read-only tools are safe to repeat, so
an attempt that only read something still falls back as before.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest

from jarvis.brain import manager as manager_mod
from jarvis.brain.manager import BrainManager
from jarvis.brain.streaming import StreamingAggregate
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig, SafetyConfig
from jarvis.core.protocols import BrainDelta, BrainRequest, ExecutionContext, ToolResult
from jarvis.safety.approval import ApprovalWorkflow
from jarvis.safety.risk_tier import RiskTierEvaluator
from jarvis.safety.tool_executor import ToolExecutor, recording_side_effects
from jarvis.screen_context.turn import TurnScreenContext


class _FakeBrain:
    name = "fake"
    context_window = 8192
    supports_tools = True
    supports_vision = False

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        yield BrainDelta(content="ok")
        yield BrainDelta(finish_reason="stop")


class _SendNote:
    """A consequential tool: every run is a real side effect."""

    name = "send_note"
    risk_tier = "safe"
    is_action_tool = True
    schema: dict[str, Any] = {}

    def __init__(self) -> None:
        self.runs = 0

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        self.runs += 1
        return ToolResult(success=True, output="sent")


class _ReadClock:
    """A read-only tool: repeating it changes nothing."""

    name = "read_clock"
    risk_tier = "safe"
    schema: dict[str, Any] = {}

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        return ToolResult(success=True, output="12:00")


class _Dispatcher:
    """First provider runs ``tool`` through the real executor, then drops."""

    def __init__(self, executor: ToolExecutor, tool: Any | None) -> None:
        self.executor = executor
        self.tool = tool
        self.providers: list[str] = []

    def for_provider(self, name: str) -> _Dispatcher:
        self.providers.append(name)
        return self

    async def dispatch(self, user_text: str, **_kwargs: Any) -> StreamingAggregate:
        if len(self.providers) == 1:
            if self.tool is not None:
                await self.executor.execute(self.tool, {}, trace_id=uuid4())
            raise ConnectionError("stream reset by peer")
        agg = StreamingAggregate()
        agg.text = "second provider answer"
        agg.finish_reason = "stop"
        return agg


def _manager(tool: Any | None) -> tuple[BrainManager, _Dispatcher]:
    cfg = JarvisConfig()
    cfg.brain.primary = "first"
    bus = EventBus()
    mgr = BrainManager(config=cfg, bus=bus, tools={})
    executor = ToolExecutor(
        bus=bus, evaluator=RiskTierEvaluator(SafetyConfig()), approval=ApprovalWorkflow(bus),
    )
    disp = _Dispatcher(executor, tool)
    mgr._build_fallback_chain = lambda _l: [("first", "m1"), ("second", "m2")]  # type: ignore[method-assign]
    mgr._get_brain = lambda name, _m: _FakeBrain()  # type: ignore[method-assign]
    mgr._build_dispatcher = lambda brain, **_kw: disp.for_provider("next")  # type: ignore[method-assign]

    async def _no_screen(*_args: Any, **_kwargs: Any) -> TurnScreenContext:
        return TurnScreenContext(status="none")

    async def _no_images(**_kwargs: Any) -> tuple:
        return ()

    mgr._resolve_screen_context_turn = _no_screen  # type: ignore[method-assign]
    mgr._collect_vision_images = _no_images  # type: ignore[method-assign]
    mgr._resolve_turn_lang = lambda: "en"  # type: ignore[method-assign]
    return mgr, disp


@pytest.mark.asyncio
async def test_action_then_failure_is_not_replayed_on_the_next_provider() -> None:
    tool = _SendNote()
    mgr, disp = _manager(tool)

    answer = await mgr.generate("send the note to Sam", use_history=False)

    assert tool.runs == 1, "the action must not run a second time"
    assert len(disp.providers) == 1, "no second provider attempt after an action started"
    assert answer == manager_mod._UNKNOWN_OUTCOME_PHRASES["en"]


@pytest.mark.asyncio
async def test_read_only_tool_then_failure_still_falls_back() -> None:
    mgr, disp = _manager(_ReadClock())

    answer = await mgr.generate("what time is it", use_history=False)

    assert len(disp.providers) == 2
    assert answer == "second provider answer"


@pytest.mark.asyncio
async def test_failure_without_any_tool_still_falls_back() -> None:
    mgr, disp = _manager(None)

    answer = await mgr.generate("hello", use_history=False)

    assert len(disp.providers) == 2
    assert answer == "second provider answer"


@pytest.mark.asyncio
async def test_ledger_records_only_consequential_tools_and_closes() -> None:
    bus = EventBus()
    executor = ToolExecutor(
        bus=bus, evaluator=RiskTierEvaluator(SafetyConfig()), approval=ApprovalWorkflow(bus),
    )
    with recording_side_effects() as started:
        await executor.execute(_ReadClock(), {}, trace_id=uuid4())
        await executor.execute(_SendNote(), {}, trace_id=uuid4())
    assert started == ["send_note"]

    # Outside a block nothing is recorded and nothing breaks.
    await executor.execute(_SendNote(), {}, trace_id=uuid4())
    assert started == ["send_note"]
