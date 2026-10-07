"""Contract tests for the Paperclip escalation adapter, against a fake Paperclip.

The fake implements the five routes the adapter uses (companies, agents, create
issue, read issue, read comments, patch issue) with the field names of
Paperclip's own validators (``assigneeAgentId``, ``idempotencyKey``, statuses
``todo``/``in_progress``/``done``/``cancelled``). No network, no model.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.brain.paperclip_delegation import (
    DelegationRequest,
    DelegationStatus,
    PaperclipDelegate,
    build_issue_payload,
    delegate_from_config,
)
from jarvis.brain.route_policy import RouteFailure

COMPANY = "c-1"
AGENT = "a-claude"


@dataclass
class FakePaperclip:
    """In-memory Paperclip: the issue advances one status per poll."""

    statuses: list[str] = field(default_factory=lambda: ["todo", "in_progress", "done"])
    reply: str = "Here is the expert answer."
    fail_all: bool = False
    calls: list[tuple[str, str, Any]] = field(default_factory=list)
    issue: dict[str, Any] | None = None

    async def request(self, method: str, path: str, json: dict[str, Any] | None = None):
        self.calls.append((method, path, json))
        if self.fail_all:
            raise ConnectionError("connection refused")
        if (method, path) == ("GET", "/api/companies"):
            return 200, [{"id": COMPANY, "name": "Robee"}]
        if (method, path) == ("GET", f"/api/companies/{COMPANY}/agents"):
            return 200, [{"id": "a-dan", "name": "dan"}, {"id": AGENT, "name": "Claude"}]
        if (method, path) == ("POST", f"/api/companies/{COMPANY}/issues"):
            self.issue = {"id": "i-1", "identifier": "ROB-9", "status": self.statuses[0]}
            return 201, dict(self.issue)
        if (method, path) == ("GET", "/api/issues/i-1"):
            if len(self.statuses) > 1:
                self.statuses.pop(0)
            return 200, {"id": "i-1", "status": self.statuses[0]}
        if (method, path) == ("GET", "/api/issues/i-1/comments"):
            return 200, [
                {"body": "Picked up.", "authorAgentId": None, "authorType": "user"},
                {"body": self.reply, "authorAgentId": AGENT, "authorType": "agent"},
            ]
        if (method, path) == ("PATCH", "/api/issues/i-1"):
            self.statuses = [json["status"]]
            return 200, {"id": "i-1", "status": json["status"]}
        return 404, {"error": "not found"}


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds


@dataclass
class _Token:
    cancelled: bool = False

    def is_cancelled(self) -> bool:
        return self.cancelled


def _delegate(fake: FakePaperclip, clock: _Clock, *, deadline_s: float = 60.0) -> PaperclipDelegate:
    return PaperclipDelegate(
        fake, agent_name="claude", deadline_s=deadline_s, poll_interval_s=2.0,
        max_context_chars=50, clock=clock, sleep=clock.sleep,
    )


REQUEST = DelegationRequest(turn_id="t-42", task="Review this plan", context="x" * 500)


@pytest.mark.asyncio
async def test_completed_delegation_returns_the_agents_reply() -> None:
    fake, clock = FakePaperclip(), _Clock()
    result = await _delegate(fake, clock).delegate(REQUEST)

    assert result.status is DelegationStatus.COMPLETED
    assert result.text == "Here is the expert answer."
    assert result.issue_identifier == "ROB-9"
    assert result.failure is None


@pytest.mark.asyncio
async def test_issue_carries_turn_id_deadline_bounded_context_and_idempotency() -> None:
    fake, clock = FakePaperclip(), _Clock()
    await _delegate(fake, clock).delegate(REQUEST)

    [create] = [c for c in fake.calls if c[0] == "POST"]
    body = create[2]
    assert body["assigneeAgentId"] == AGENT
    assert body["idempotencyKey"] == "jarvis-turn-t-42"
    assert "Jarvis turn: t-42" in body["description"]
    assert "x" * 51 not in body["description"]  # context capped at 50 chars
    assert "Expected output:" in body["description"]


def test_payload_redacts_secrets() -> None:
    request = DelegationRequest(
        turn_id="t", task="Use key sk-ant-api03-ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 please",
    )
    body = build_issue_payload(request, agent_id=AGENT, max_context_chars=100)
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" not in body["description"]
    assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789" not in body["title"]


@pytest.mark.asyncio
async def test_deadline_cancels_the_issue_and_reports_timeout() -> None:
    fake, clock = FakePaperclip(statuses=["todo", "in_progress"]), _Clock()
    result = await _delegate(fake, clock, deadline_s=10.0).delegate(REQUEST)

    assert result.status is DelegationStatus.TIMEOUT
    assert result.failure is RouteFailure.TIMEOUT
    patch = [c for c in fake.calls if c[0] == "PATCH"]
    assert patch and patch[-1][2]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_token_marks_the_delegation_obsolete() -> None:
    fake, clock = FakePaperclip(statuses=["todo", "in_progress"]), _Clock()
    token = _Token()
    delegate = _delegate(fake, clock)

    async def _cancel_after_first_poll(seconds: float) -> None:
        token.cancelled = True
        await clock.sleep(seconds)

    delegate._sleep = _cancel_after_first_poll
    result = await delegate.delegate(REQUEST, cancel_token=token)

    assert result.status is DelegationStatus.CANCELLED
    assert [c for c in fake.calls if c[0] == "PATCH"][-1][2]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_task_cancellation_cancels_the_issue_then_reraises() -> None:
    fake, clock = FakePaperclip(statuses=["todo", "in_progress"]), _Clock()
    delegate = _delegate(fake, clock)
    started = asyncio.Event()

    async def _block(_seconds: float) -> None:
        started.set()
        await asyncio.sleep(3600)

    delegate._sleep = _block
    task = asyncio.create_task(delegate.delegate(REQUEST))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [c for c in fake.calls if c[0] == "PATCH"][-1][2]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_unreachable_paperclip_is_a_typed_unavailable() -> None:
    result = await _delegate(FakePaperclip(fail_all=True), _Clock()).delegate(REQUEST)
    assert result.status is DelegationStatus.UNAVAILABLE
    assert result.failure is RouteFailure.UNAVAILABLE


@pytest.mark.asyncio
async def test_missing_agent_is_unavailable_not_a_crash() -> None:
    fake, clock = FakePaperclip(), _Clock()
    delegate = PaperclipDelegate(
        fake, agent_name="nobody", deadline_s=5, poll_interval_s=1, max_context_chars=10,
        clock=clock, sleep=clock.sleep,
    )
    result = await delegate.delegate(REQUEST)
    assert result.status is DelegationStatus.UNAVAILABLE
    assert not [c for c in fake.calls if c[0] == "POST"]


@pytest.mark.asyncio
async def test_agent_cancelling_the_issue_is_a_delegation_failure() -> None:
    fake, clock = FakePaperclip(statuses=["todo", "cancelled"]), _Clock()
    result = await _delegate(fake, clock).delegate(REQUEST)
    assert result.status is DelegationStatus.FAILED
    assert result.failure is RouteFailure.DELEGATION_FAILED


@pytest.mark.asyncio
async def test_done_without_a_reply_is_not_reported_as_success() -> None:
    fake, clock = FakePaperclip(reply=""), _Clock()
    result = await _delegate(fake, clock).delegate(REQUEST)
    assert result.status is DelegationStatus.FAILED


def test_config_builder_needs_an_enabled_paperclip_connection() -> None:
    off = SimpleNamespace(enabled=False, via="paperclip")
    assert delegate_from_config(off, token_loader=lambda: None) is None
    on = SimpleNamespace(enabled=True, via="paperclip", agent="claude", deadline_s=30,
                         poll_interval_s=2, max_context_chars=100)
    assert delegate_from_config(on, token_loader=lambda: None) is None
    tokens = SimpleNamespace(access="pcp_board_x", extra={"instance_url": "http://127.0.0.1:3100"})
    assert isinstance(delegate_from_config(on, token_loader=lambda: tokens), PaperclipDelegate)
    other = SimpleNamespace(enabled=True, via="somewhere-else")
    assert delegate_from_config(other, token_loader=lambda: tokens) is None


@pytest.mark.asyncio
async def test_timeout_with_failing_polls_is_bounded_and_creates_one_issue() -> None:
    """Every poll drops: the deadline still ends it, with one issue and no reply."""
    fake, clock = FakePaperclip(statuses=["todo", "in_progress"]), _Clock()
    real_request = fake.request

    async def _polls_drop(method: str, path: str, json: dict[str, Any] | None = None):
        if (method, path) == ("GET", "/api/issues/i-1"):
            fake.calls.append((method, path, json))
            raise ConnectionError("poll dropped")
        return await real_request(method, path, json)

    delegate = PaperclipDelegate(
        SimpleNamespace(request=_polls_drop), agent_name="claude", deadline_s=10.0,
        poll_interval_s=2.0, max_context_chars=50, clock=clock, sleep=clock.sleep,
    )
    result = await delegate.delegate(REQUEST)

    assert result.status is DelegationStatus.TIMEOUT
    assert result.failure is RouteFailure.TIMEOUT
    assert result.text == ""
    assert sum(1 for c in fake.calls if c[0] == "POST") == 1
    polls = [c for c in fake.calls if c[:2] == ("GET", "/api/issues/i-1")]
    assert len(polls) <= 10.0 / 2.0 + 1
    assert clock.now <= 10.0 + 2.0
    assert [c for c in fake.calls if c[0] == "PATCH"][-1][2]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_retrying_a_timed_out_turn_replays_the_same_issue_key() -> None:
    """A retry of the same turn carries the same idempotency key, a new turn a new one."""
    fake, clock = FakePaperclip(statuses=["todo", "in_progress"]), _Clock()
    delegate = _delegate(fake, clock, deadline_s=6.0)

    first = await delegate.delegate(REQUEST)
    clock.now = 0.0
    second = await delegate.delegate(REQUEST)
    clock.now = 0.0
    other = await delegate.delegate(DelegationRequest(turn_id="t-43", task="Review this plan"))

    assert first.status is DelegationStatus.TIMEOUT
    # The replayed key lands on the issue the timeout cancelled: an honest
    # failure, never a second live issue and never a claimed completion.
    assert second.status is DelegationStatus.FAILED
    assert second.issue_id == first.issue_id
    assert all(r.text == "" for r in (first, second, other))
    keys = [c[2]["idempotencyKey"] for c in fake.calls if c[0] == "POST"]
    assert keys == ["jarvis-turn-t-42", "jarvis-turn-t-42", "jarvis-turn-t-43"]
