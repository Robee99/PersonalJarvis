"""Nothing credential-shaped reaches the flight-recorder file or the bus errors.

The flight recorder writes every bus event to disk, including the raw tool
arguments of ``ActionProposed`` and free text. Credential shapes must be masked
on the way to disk, with the event's structure kept so replay still works. The
tool executor's ``ActionExecuted.error`` is a raw exception string and gets the
same treatment.
"""
from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import pytest

from jarvis.core.bus import EventBus
from jarvis.core.config import SafetyConfig
from jarvis.core.events import ActionExecuted, ActionProposed
from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.core.redact import redact_value
from jarvis.safety.approval import ApprovalWorkflow
from jarvis.safety.risk_tier import RiskTierEvaluator
from jarvis.safety.tool_executor import ToolExecutor
from jarvis.telemetry import FlightRecorder

SECRET = "sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"  # noqa: S105 - fake key shape


def test_redact_value_keeps_shape_and_masks_strings() -> None:
    value = {"cmd": f"curl -H 'x: {SECRET}'", "n": 3, "items": [SECRET, ("ok", SECRET)]}
    out = redact_value(value)
    assert set(out) == {"cmd", "n", "items"}
    assert out["n"] == 3
    assert isinstance(out["items"][1], tuple)
    assert SECRET not in json.dumps(out)


@pytest.mark.asyncio
async def test_recorded_tool_args_are_masked(tmp_path) -> None:
    bus = EventBus()
    rec = FlightRecorder(tmp_path, flush_interval_s=0)
    rec.attach(bus)

    await bus.publish(ActionProposed(
        trace_id=uuid4(), tool_name="run_shell",
        args={"command": f"export KEY={SECRET}"}, risk_tier="ask",
    ))
    await rec.flush()
    await rec.close()

    text = next(tmp_path.glob("*.jsonl")).read_text(encoding="utf-8")
    assert SECRET not in text
    record = json.loads(text.strip().splitlines()[0])
    assert "command" in record["payload"]["args"]


class _LeakyTool:
    name = "leaky"
    risk_tier = "safe"
    schema: dict[str, Any] = {}

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        raise RuntimeError(f"auth failed for {SECRET}")


@pytest.mark.asyncio
async def test_tool_error_on_the_bus_is_masked() -> None:
    bus = EventBus()
    seen: list[ActionExecuted] = []

    async def _cap(event: ActionExecuted) -> None:
        seen.append(event)

    bus.subscribe(ActionExecuted, _cap)  # type: ignore[arg-type]
    executor = ToolExecutor(
        bus=bus, evaluator=RiskTierEvaluator(SafetyConfig()), approval=ApprovalWorkflow(bus),
    )
    await executor.execute(_LeakyTool(), {}, trace_id=uuid4())

    assert seen and SECRET not in (seen[0].error or "")
    assert "auth failed" in (seen[0].error or "")
