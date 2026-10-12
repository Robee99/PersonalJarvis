"""Central tool-argument validation at the ``ToolExecutor`` boundary.

Arguments are checked against the tool's own JSON schema before anything is
evaluated, approved or run, and again when a deferred voice/chat confirmation
resumes. An invalid call never reaches the tool's handler.
"""
from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.config import SafetyConfig
from jarvis.core.events import ActionDenied, ActionExecuted
from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.safety.approval import ApprovalWorkflow
from jarvis.safety.risk_tier import RiskTierEvaluator
from jarvis.safety.tool_executor import (
    INVALID_ARGUMENTS_PREFIX,
    VOICE_CONFIRM_SENTINEL,
    ToolExecutor,
    validate_tool_args,
)

# Credential-shaped, assembled at runtime so no scanner mistakes it for a real key.
_LEAKY = "sk-" + "ant-api03-" + "Q7" * 24


class _SendMessage:
    """A side-effecting tool that records every call it receives."""

    name = "send_message"
    risk_tier = "safe"
    is_action_tool = True
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "to": {"type": "string"},
            "body": {"type": "string"},
            "priority": {"type": "string", "enum": ["low", "high"]},
            "retries": {"type": "integer"},
            "delay_s": {"type": "number"},
            "cc": {"type": "array", "items": {"type": "string"}},
            "thread": {"type": ["string", "null"]},
        },
        "required": ["to", "body"],
        "additionalProperties": False,
    }

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        self.calls.append(dict(args))
        return ToolResult(success=True, output="sent")


class _AskSendMessage(_SendMessage):
    risk_tier = "ask"


class _OpenSchemaTool(_SendMessage):
    """Same fields, but extra ones are not forbidden."""

    name = "open_schema"
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {"to": {"type": "string"}},
        "required": ["to"],
    }


class _SchemalessTool(_SendMessage):
    name = "schemaless"
    schema: Any = None


class _BrokenSchemaTool(_SendMessage):
    name = "broken_schema"
    schema: dict[str, Any] = {"type": "object", "properties": {"x": {"type": "float"}}}


class _AutoApprove(ApprovalWorkflow):
    async def wait(self, trace_id: Any, timeout_s: float) -> tuple[bool, str]:  # type: ignore[override]
        return True, "user"


def _executor() -> tuple[ToolExecutor, list[Any]]:
    bus = EventBus()
    events: list[Any] = []

    async def _capture(event: Any) -> None:
        events.append(event)

    bus.subscribe(ActionDenied, _capture)
    bus.subscribe(ActionExecuted, _capture)
    executor = ToolExecutor(
        bus=bus,
        evaluator=RiskTierEvaluator(SafetyConfig()),
        approval=_AutoApprove(bus),
    )
    return executor, events


_VALID = {"to": "sam", "body": "hello"}

_INVALID_CASES = [
    pytest.param({"to": "sam"}, "body", id="missing-required"),
    pytest.param({"to": 42, "body": "hello"}, "to", id="wrong-type"),
    pytest.param({**_VALID, "bcc": _LEAKY}, "bcc", id="forbidden-extra"),
    pytest.param({**_VALID, "priority": "urgent"}, "priority", id="enum"),
    pytest.param({**_VALID, "retries": 2.5}, "retries", id="non-integer"),
    pytest.param({**_VALID, "cc": ["a", 3]}, "cc", id="array-item-type"),
    pytest.param({**_VALID, "delay_s": True}, "delay_s", id="bool-is-not-a-number"),
    pytest.param({"to": None, "body": "hello"}, "to", id="null-required"),
]


@pytest.mark.parametrize(("args", "field"), _INVALID_CASES)
async def test_invalid_args_never_reach_the_handler(args: dict[str, Any], field: str) -> None:
    executor, events = _executor()
    tool = _SendMessage()

    result = await executor.execute(tool, args, trace_id=uuid4())

    assert tool.calls == []
    assert result.success is False
    assert result.error is not None and result.error.startswith(f"{INVALID_ARGUMENTS_PREFIX}:")
    assert field in result.error
    assert _LEAKY not in result.error and _LEAKY not in str(result.output)
    assert result.output["retryable"] is True
    assert [type(event) for event in events] == [ActionDenied]
    assert events[0].reason.startswith(INVALID_ARGUMENTS_PREFIX)


@pytest.mark.parametrize(("args", "field"), _INVALID_CASES)
async def test_invalid_args_are_refused_again_on_the_confirmed_path(
    args: dict[str, Any], field: str,
) -> None:
    executor, _events = _executor()
    tool = _AskSendMessage()
    tid = uuid4()
    # The deferral stashes valid arguments; a later change to the stash (or a
    # caller that re-supplies arguments) must not be trusted.
    deferred = await executor.execute(
        tool, dict(_VALID), config_snapshot={"voice_confirm": True}, trace_id=tid,
    )
    assert deferred.error == VOICE_CONFIRM_SENTINEL
    stashed_tool, _stashed_args = executor._pending_voice[tid]
    executor._pending_voice[tid] = (stashed_tool, dict(args))

    result = await executor.execute_confirmed(tid)

    assert tool.calls == []
    assert result.error is not None and result.error.startswith(f"{INVALID_ARGUMENTS_PREFIX}:")
    assert field in result.error
    assert not executor.has_pending_voice_confirm(tid), "the refused action is consumed"


async def test_invalid_args_are_refused_before_a_deferral() -> None:
    executor, _events = _executor()
    tool = _AskSendMessage()
    tid = uuid4()

    result = await executor.execute(
        tool, {"to": "sam"}, config_snapshot={"voice_confirm": True}, trace_id=tid,
    )

    assert result.error is not None and result.error.startswith(INVALID_ARGUMENTS_PREFIX)
    assert not executor.has_pending_voice_confirm(tid)


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(dict(_VALID), id="minimal"),
        pytest.param({**_VALID, "retries": "3", "delay_s": "1.5"}, id="numeric-strings"),
        pytest.param({**_VALID, "retries": 3.0, "delay_s": 2}, id="int-for-number"),
        pytest.param({**_VALID, "priority": None, "cc": None}, id="null-optional"),
        pytest.param({**_VALID, "thread": None}, id="nullable-union"),
        pytest.param({**_VALID, "cc": ["a", "b"], "priority": "high"}, id="full"),
    ],
)
async def test_valid_calls_still_run_unchanged(args: dict[str, Any]) -> None:
    executor, _events = _executor()
    tool = _SendMessage()

    result = await executor.execute(tool, args, trace_id=uuid4())

    assert result.success is True
    assert tool.calls == [args], "the handler receives the arguments as sent"


async def test_valid_call_runs_on_the_confirmed_path() -> None:
    executor, _events = _executor()
    tool = _AskSendMessage()
    tid = uuid4()
    await executor.execute(
        tool, dict(_VALID), config_snapshot={"voice_confirm": True}, trace_id=tid,
    )

    result = await executor.execute_confirmed(tid)

    assert result.success is True
    assert tool.calls == [_VALID]


async def test_extra_fields_pass_when_the_schema_does_not_forbid_them() -> None:
    executor, _events = _executor()
    tool = _OpenSchemaTool()

    result = await executor.execute(tool, {"to": "sam", "env": {"X": "1"}}, trace_id=uuid4())

    assert result.success is True
    assert len(tool.calls) == 1


@pytest.mark.parametrize("tool_cls", [_SchemalessTool, _BrokenSchemaTool])
async def test_schemaless_or_unusable_schema_passes_through(tool_cls: type) -> None:
    executor, _events = _executor()
    tool = tool_cls()

    result = await executor.execute(tool, {"anything": object()}, trace_id=uuid4())

    assert result.success is True
    assert len(tool.calls) == 1


def test_empty_schema_dict_passes_through() -> None:
    class _Empty:
        name = "empty"
        schema: dict[str, Any] = {}

    assert validate_tool_args(_Empty(), {"x": 1}) is None  # type: ignore[arg-type]


def test_reason_names_the_field_without_echoing_the_value() -> None:
    reason = validate_tool_args(_SendMessage(), {"to": _LEAKY, "body": 7})  # type: ignore[arg-type]

    assert reason is not None
    assert "body" in reason and "string" in reason
    assert _LEAKY not in reason


def test_range_limits_are_left_to_the_tool() -> None:
    class _Clamped:
        name = "clamped"
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {"timeout_s": {"type": "number", "minimum": 1, "maximum": 60}},
        }

    # The tool clamps 120 to its own maximum today; refusing it would break it.
    assert validate_tool_args(_Clamped(), {"timeout_s": 120}) is None  # type: ignore[arg-type]
