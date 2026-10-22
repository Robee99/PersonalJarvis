"""Execution deadline and in-flight cancellation at the ``ToolExecutor`` boundary.

The approval timeout bounds how long a person may take to decide; these tests
cover the separate clock on how long an approved tool may RUN, and the cancel
token being honoured while the tool is running. An interrupted call has an
unknown outcome: it is reported as such, recorded as a started side effect,
and never run a second time.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import Any
from uuid import uuid4

import pytest

from jarvis.control.cancel import CancelToken
from jarvis.core.bus import EventBus
from jarvis.core.config import SafetyConfig
from jarvis.core.events import ActionExecuted
from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.safety import tool_executor as executor_mod
from jarvis.safety.approval import ApprovalWorkflow
from jarvis.safety.risk_tier import RiskTierEvaluator
from jarvis.safety.tool_executor import (
    DEFAULT_EXECUTION_TIMEOUT_S,
    EXECUTION_CANCELLED_PREFIX,
    EXECUTION_TIMEOUT_PREFIX,
    MAX_EXECUTION_TIMEOUT_S,
    OUTCOME_UNKNOWN,
    VOICE_CONFIRM_SENTINEL,
    ToolExecutor,
    execution_budget_s,
    recording_side_effects,
)


class _HangingAction:
    """A consequential tool that never returns on its own."""

    name = "send_report"
    risk_tier = "safe"
    is_action_tool = True
    schema: dict[str, Any] = {"type": "object", "properties": {}}
    execution_timeout_s = 0.2

    def __init__(self) -> None:
        self.calls = 0
        self.cancelled = False
        self.entered = asyncio.Event()

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        self.calls += 1
        self.entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return ToolResult(success=True, output="unreachable")


class _AskHangingAction(_HangingAction):
    risk_tier = "ask"


class _FastTool:
    name = "fast_tool"
    risk_tier = "safe"
    schema: dict[str, Any] = {"type": "object", "properties": {}}

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        await asyncio.sleep(0)
        return ToolResult(success=True, output="done")


class _ChildProcessTool:
    """Runs a real 30-second child and kills it when cancelled (cooperative cleanup)."""

    name = "child_runner"
    risk_tier = "safe"
    schema: dict[str, Any] = {"type": "object", "properties": {}}

    def __init__(self) -> None:
        self.pid: int | None = None
        self.started = asyncio.Event()

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", "import time; time.sleep(30)",
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
        self.pid = proc.pid
        self.started.set()
        try:
            await proc.wait()
        except asyncio.CancelledError:
            if proc.returncode is None:
                proc.kill()
                await proc.wait()
            raise
        return ToolResult(success=True, output=proc.returncode)


class _StubbornTool:
    """Ignores cancellation: the executor must still return promptly."""

    name = "stubborn"
    risk_tier = "safe"
    schema: dict[str, Any] = {"type": "object", "properties": {}}
    execution_timeout_s = 0.1

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.finished = False

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        while not self.release.is_set():
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                continue
        self.finished = True
        return ToolResult(success=True, output="late")


class _AutoApprove(ApprovalWorkflow):
    async def wait(self, trace_id: Any, timeout_s: float) -> tuple[bool, str]:  # type: ignore[override]
        return True, "user"


def _executor() -> tuple[ToolExecutor, list[ActionExecuted]]:
    bus = EventBus()
    seen: list[ActionExecuted] = []

    async def _capture(event: ActionExecuted) -> None:
        seen.append(event)

    bus.subscribe(ActionExecuted, _capture)
    executor = ToolExecutor(
        bus=bus,
        evaluator=RiskTierEvaluator(SafetyConfig()),
        approval=_AutoApprove(bus),
    )
    return executor, seen


def _assert_unknown_outcome(result: ToolResult, prefix: str) -> None:
    assert result.success is False
    assert result.error is not None and result.error.startswith(prefix)
    assert OUTCOME_UNKNOWN in result.error
    assert isinstance(result.output, dict)
    assert result.output["outcome"] == OUTCOME_UNKNOWN
    assert result.output["retry_safe"] is False
    # Never claims the action was undone.
    assert "undone" not in result.output["message"].lower()


async def test_hanging_tool_hits_the_deadline_on_the_normal_path() -> None:
    executor, seen = _executor()
    tool = _HangingAction()

    started = time.monotonic()
    result = await executor.execute(tool, {}, trace_id=uuid4())

    assert time.monotonic() - started < 3.0
    _assert_unknown_outcome(result, EXECUTION_TIMEOUT_PREFIX)
    assert tool.cancelled, "the tool task is cancelled, not left running"
    assert len(seen) == 1 and seen[0].success is False
    assert seen[0].error is not None and seen[0].error.startswith(EXECUTION_TIMEOUT_PREFIX)


async def test_hanging_tool_hits_the_deadline_on_the_confirmed_path() -> None:
    executor, seen = _executor()
    tool = _AskHangingAction()
    tid = uuid4()

    deferred = await executor.execute(
        tool, {}, config_snapshot={"voice_confirm": True}, trace_id=tid,
    )
    assert deferred.error == VOICE_CONFIRM_SENTINEL
    assert tool.calls == 0

    started = time.monotonic()
    result = await executor.execute_confirmed(tid)

    assert time.monotonic() - started < 3.0
    _assert_unknown_outcome(result, EXECUTION_TIMEOUT_PREFIX)
    assert tool.cancelled
    assert [event.trace_id for event in seen] == [tid]


async def test_cancel_token_during_execution_returns_promptly() -> None:
    executor, seen = _executor()
    tool = _HangingAction()
    tool.execution_timeout_s = 60.0
    token = CancelToken()

    call = asyncio.create_task(executor.execute(tool, {}, cancel_token=token))
    await asyncio.wait_for(tool.entered.wait(), timeout=3.0)
    started = time.monotonic()
    token.cancel("kill_switch")
    result = await asyncio.wait_for(call, timeout=3.0)

    assert time.monotonic() - started < 1.0
    _assert_unknown_outcome(result, EXECUTION_CANCELLED_PREFIX)
    assert "kill_switch" in (result.error or "")
    assert tool.cancelled
    assert len(seen) == 1 and seen[0].success is False


async def test_cancel_token_on_the_confirmed_path() -> None:
    executor, _seen = _executor()
    tool = _AskHangingAction()
    tool.execution_timeout_s = 60.0
    tid = uuid4()
    await executor.execute(tool, {}, config_snapshot={"voice_confirm": True}, trace_id=tid)
    token = CancelToken()

    call = asyncio.create_task(executor.execute_confirmed(tid, cancel_token=token))
    await asyncio.wait_for(tool.entered.wait(), timeout=3.0)
    token.cancel("user_stop")
    result = await asyncio.wait_for(call, timeout=3.0)

    _assert_unknown_outcome(result, EXECUTION_CANCELLED_PREFIX)


@pytest.mark.skipif(not sys.executable, reason="no Python executable to spawn")
async def test_cancellation_kills_a_cooperative_tools_child_process() -> None:
    executor, _seen = _executor()
    tool = _ChildProcessTool()
    token = CancelToken()

    call = asyncio.create_task(executor.execute(tool, {}, cancel_token=token))
    try:
        await asyncio.wait_for(tool.started.wait(), timeout=10.0)
    except (NotImplementedError, OSError, TimeoutError) as exc:
        call.cancel()
        pytest.skip(f"subprocesses unavailable here: {type(exc).__name__}")
    token.cancel("kill_switch")
    result = await asyncio.wait_for(call, timeout=5.0)

    _assert_unknown_outcome(result, EXECUTION_CANCELLED_PREFIX)
    assert tool.pid is not None
    assert not _process_alive(tool.pid), "the child must not outlive the cancelled call"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX shell path of run_shell")
async def test_cancelled_run_shell_does_not_leave_its_child_running() -> None:
    """The real shell tool kills its child when the executor cancels it."""
    psutil = pytest.importorskip("psutil")
    from jarvis.plugins.tool.run_shell import RunShellTool

    executor, _seen = _executor()
    me = psutil.Process()
    before = {child.pid for child in me.children(recursive=True)}
    token = CancelToken()
    command = f'exec "{sys.executable}" -c "import time; time.sleep(30)"'
    call = asyncio.create_task(
        executor.execute(RunShellTool(), {"command": command}, cancel_token=token),
    )
    new: set[int] = set()
    for _ in range(200):
        await asyncio.sleep(0.02)
        new = {child.pid for child in me.children(recursive=True)} - before
        if new:
            break
    if not new:
        call.cancel()
        pytest.skip("the shell child never appeared (subprocesses unavailable?)")
    token.cancel("kill_switch")
    result = await asyncio.wait_for(call, timeout=5.0)

    assert result.error is not None and result.error.startswith(EXECUTION_CANCELLED_PREFIX)
    assert not any(_process_alive(pid) for pid in new)


async def test_side_effecting_tool_is_never_called_twice_after_a_timeout() -> None:
    executor, _seen = _executor()
    tool = _HangingAction()

    with recording_side_effects() as started:
        result = await executor.execute(tool, {}, trace_id=uuid4())

    assert result.error is not None and result.error.startswith(EXECUTION_TIMEOUT_PREFIX)
    assert tool.calls == 1, "the executor never retries an interrupted call"
    # Still recorded as started, so the Brain will not replay the turn.
    assert started == ["send_report"]
    await asyncio.sleep(0.3)
    assert tool.calls == 1


async def test_a_tool_that_ignores_cancellation_does_not_hold_the_executor() -> None:
    executor, _seen = _executor()
    tool = _StubbornTool()

    started = time.monotonic()
    result = await executor.execute(tool, {}, trace_id=uuid4())

    elapsed = time.monotonic() - started
    assert elapsed < executor_mod.CANCEL_GRACE_S + 2.0
    _assert_unknown_outcome(result, EXECUTION_TIMEOUT_PREFIX)
    assert not tool.finished
    tool.release.set()
    for _ in range(50):
        if tool.finished:
            break
        await asyncio.sleep(0.01)
    assert tool.finished, "the abandoned task is kept alive and finishes on its own"


async def test_fast_tool_is_unaffected() -> None:
    executor, seen = _executor()

    result = await executor.execute(_FastTool(), {}, trace_id=uuid4())

    assert result == ToolResult(success=True, output="done")
    assert len(seen) == 1 and seen[0].success is True


async def test_tool_exception_still_reported_as_before() -> None:
    class _Raises(_FastTool):
        async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
            raise RuntimeError("boom")

    executor, seen = _executor()
    result = await executor.execute(_Raises(), {}, trace_id=uuid4())

    assert result.success is False and result.error == "boom"
    assert seen[0].error == "boom"


async def test_caller_cancellation_reaches_the_tool() -> None:
    executor, _seen = _executor()
    tool = _HangingAction()
    tool.execution_timeout_s = 60.0

    call = asyncio.create_task(executor.execute(tool, {}, trace_id=uuid4()))
    await asyncio.wait_for(tool.entered.wait(), timeout=3.0)
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    assert tool.cancelled


def test_budget_resolution_and_clamp() -> None:
    class _Plain:
        name = "plain"

    class _Static:
        name = "static"
        execution_timeout_s = 900

    class _PerArgs:
        name = "per_args"
        execution_timeout_s = 50

        def execution_timeout_for_args(self, args: dict[str, Any]) -> float | None:
            return args.get("t")

    class _Broken:
        name = "broken"
        execution_timeout_s = "soon"

        def execution_timeout_for_args(self, args: dict[str, Any]) -> float:
            raise ValueError("bad hook")

    assert execution_budget_s(_Plain(), {}) == DEFAULT_EXECUTION_TIMEOUT_S  # type: ignore[arg-type]
    assert execution_budget_s(_Static(), {}) == 900  # type: ignore[arg-type]
    assert execution_budget_s(_PerArgs(), {"t": 300}) == 300  # type: ignore[arg-type]
    assert execution_budget_s(_PerArgs(), {}) == 50  # type: ignore[arg-type]
    assert execution_budget_s(_PerArgs(), {"t": -1}) == 50  # type: ignore[arg-type]
    assert execution_budget_s(_PerArgs(), {"t": 10**9}) == MAX_EXECUTION_TIMEOUT_S  # type: ignore[arg-type]
    assert execution_budget_s(_Broken(), {}) == DEFAULT_EXECUTION_TIMEOUT_S  # type: ignore[arg-type]


def test_long_running_tools_declare_budgets_beyond_their_own_clocks() -> None:
    from jarvis.plugins.tool.dispatch_to_harness import DispatchToHarnessTool
    from jarvis.plugins.tool.run_shell import RunShellTool

    assert execution_budget_s(RunShellTool(), {"command": "x", "timeout_s": 300}) > 300
    assert execution_budget_s(RunShellTool(), {"command": "x"}) == 45.0
    # Queue allowance (2x) plus work (1x) of the harness's own timeout.
    assert execution_budget_s(
        DispatchToHarnessTool(), {"harness": "h", "prompt": "p", "timeout_s": 100},
    ) > 300


def _process_alive(pid: int) -> bool:
    try:
        import psutil
    except ImportError:
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    try:
        proc = psutil.Process(pid)
        return proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False
