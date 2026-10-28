"""Race and cleanup guards for the first-wins approval workflow."""

from __future__ import annotations

from uuid import uuid4

import pytest

from jarvis.core.bus import EventBus
from jarvis.safety.approval import ApprovalWorkflow


@pytest.mark.asyncio
async def test_armed_ticket_keeps_a_decision_that_arrives_before_wait() -> None:
    workflow = ApprovalWorkflow(EventBus())
    trace_id = uuid4()
    workflow.arm(trace_id)

    await workflow.approve(trace_id, who="test-user")
    approved, who = await workflow.wait(trace_id, timeout_s=0.01)

    assert approved is True
    assert who == "test-user"


@pytest.mark.asyncio
async def test_decision_before_arm_never_pre_authorizes_future_action() -> None:
    workflow = ApprovalWorkflow(EventBus())
    trace_id = uuid4()

    await workflow.approve(trace_id, who="too-early")
    approved, reason = await workflow.wait(trace_id, timeout_s=0.01)

    assert approved is False
    assert reason == "timeout"


@pytest.mark.asyncio
async def test_duplicate_arm_is_rejected() -> None:
    workflow = ApprovalWorkflow(EventBus())
    trace_id = uuid4()
    ticket = workflow.arm(trace_id)
    try:
        with pytest.raises(RuntimeError, match="already armed"):
            workflow.arm(trace_id)
    finally:
        ticket.close()


@pytest.mark.asyncio
async def test_first_decision_wins_and_replay_cannot_approve_another_call() -> None:
    workflow = ApprovalWorkflow(EventBus())
    first_id, next_id = uuid4(), uuid4()
    workflow.arm(first_id)
    await workflow.approve(first_id, who="test-user")
    await workflow.deny(first_id, reason="duplicate response")
    assert await workflow.wait(first_id, timeout_s=0.01) == (True, "test-user")

    next_ticket = workflow.arm(next_id)
    try:
        await workflow.approve(first_id, who="replayed response")
        assert next_ticket.peek() is None
        await workflow.deny(next_id, reason="declined")
        assert await workflow.wait(next_id, timeout_s=0.01) == (False, "declined")
    finally:
        next_ticket.close()
