"""Hermes reaches Jarvis's tools over MCP, and ask-tier ones only with the user's yes.

The gate under test (``jarvis.mcp.jarvis_tools_server._outside_call`` with
``jarvis.safety.spoken_consent``): a caller without a chat session (the Hermes
brain) that calls an ask-tier tool gets ``needs_confirmation`` and a token,
and the tool does not run. A retry with the token runs it once, and only when
the first real user turn after the question was a yes. Everything runs
through the real ``BrainSupervisorToolGateway`` and ``ToolExecutor``, so the
audit events are the production ones.

Headless: no network, no model, no camera.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

import mcp.types as types
import pytest

from jarvis.brain.tool_gateway import BrainSupervisorToolGateway
from jarvis.core.bus import EventBus
from jarvis.core.chat_turn import ChatTurn, current_chat_turn
from jarvis.core.config import SafetyConfig
from jarvis.core.events import ActionDenied, ActionExecuted
from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.mcp import jarvis_tools_server as server
from jarvis.safety import spoken_consent
from jarvis.safety.approval import ApprovalWorkflow
from jarvis.safety.risk_tier import RiskTierEvaluator
from jarvis.safety.tool_executor import ToolExecutor


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def tick(self, seconds: float = 1.0) -> None:
        self.now += seconds


class _Mail:
    """A Gmail-shaped tool: reads are safe, a draft is monitored, sending asks."""

    name = "gmail"
    risk_tier = "ask"
    description = "Mail"
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "action": {"type": "string"},
            "to": {"type": "string"},
            "subject": {"type": "string"},
            "body": {"type": "string"},
        },
        "required": ["action"],
    }

    def __init__(self) -> None:
        self.ran: list[dict[str, Any]] = []

    def risk_tier_for_args(self, args: dict[str, Any]) -> str:
        return "safe" if args.get("action") == "list_messages" else "ask"

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        self.ran.append(dict(args))
        return ToolResult(success=True, output=f"ran {args.get('action')}")


class _Camera:
    name = "camera"
    risk_tier = "ask"
    description = "Webcam still"
    schema: dict[str, Any] = {"type": "object", "properties": {"reason": {"type": "string"}}}

    def __init__(self) -> None:
        self.ran = 0

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        self.ran += 1
        return ToolResult(
            success=True,
            output="Camera photo taken",
            artifacts=({"type": "image", "mime": "image/jpeg", "data": "AAAA"},),
        )


class _Wipe:
    name = "wipe-disk"
    risk_tier = "block"
    description = "Never"
    schema: dict[str, Any] = {"type": "object", "properties": {}}

    def __init__(self) -> None:
        self.ran = 0

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        self.ran += 1
        return ToolResult(success=True, output="wiped")


class _Manager:
    """What the gateway reads off the brain manager: the live tools and the executor."""

    def __init__(self, tools: dict[str, Any], executor: ToolExecutor) -> None:
        self._tools = tools
        self._tool_executor = executor


class _Rig:
    def __init__(self) -> None:
        self.bus = EventBus()
        self.mail = _Mail()
        self.camera = _Camera()
        self.wipe = _Wipe()
        executor = ToolExecutor(
            self.bus,
            RiskTierEvaluator(SafetyConfig()),
            ApprovalWorkflow(self.bus, timeout_s=1.0),
            default_timeout_s=1.0,
        )
        tools = {tool.name: tool for tool in (self.mail, self.camera, self.wipe)}
        self.gateway = BrainSupervisorToolGateway(_Manager(tools, executor))
        self.denied: list[ActionDenied] = []
        self.executed: list[ActionExecuted] = []

        async def _denied(event: ActionDenied) -> None:
            self.denied.append(event)

        async def _executed(event: ActionExecuted) -> None:
            self.executed.append(event)

        self.bus.subscribe(ActionDenied, _denied)
        self.bus.subscribe(ActionExecuted, _executed)
        self.server = server.build_server()

    async def call(self, name: str, arguments: dict[str, Any]) -> tuple[Any, bool]:
        request = types.CallToolRequest(
            params=types.CallToolRequestParams(name=name, arguments=arguments)
        )
        response = await self.server.request_handlers[types.CallToolRequest](request)
        text = response.root.content[0].text
        try:
            body: Any = json.loads(text)
        except json.JSONDecodeError:
            body = text
        return body, bool(response.root.isError)


SEND = {"action": "send_message", "to": "bob@example.org", "subject": "Hi", "body": "See you"}


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(spoken_consent, "_clock", fake)
    spoken_consent._reset_for_tests()
    yield fake
    spoken_consent._reset_for_tests()


@pytest.fixture
def rig(monkeypatch: pytest.MonkeyPatch, clock: _Clock) -> _Rig:
    built = _Rig()
    monkeypatch.setattr(server, "_gateway", lambda: built.gateway)
    assert server.CHAT_SESSION_REF.get() is None, "the outside caller sends no session header"
    return built


async def _ask(rig: _Rig, clock: _Clock, args: dict[str, Any] = SEND) -> str:
    body, is_error = await rig.call("gmail", dict(args))
    assert not is_error
    assert body["needs_confirmation"] is True
    assert body["executed"] is False
    clock.tick()
    return str(body["confirm_token"])


# ------------------------------------------------------------ the gate


async def test_ask_tier_without_token_asks_and_does_not_run(rig: _Rig, clock: _Clock) -> None:
    body, is_error = await rig.call("gmail", dict(SEND))

    assert not is_error
    assert body["needs_confirmation"] is True
    assert body["confirm_token"]
    assert "send an email to bob@example.org" in body["question"]
    assert rig.mail.ran == []
    assert rig.executed == []


async def test_retry_without_a_user_turn_is_refused(rig: _Rig, clock: _Clock) -> None:
    token = await _ask(rig, clock)

    body, is_error = await rig.call("gmail", {**SEND, "confirm_token": token})

    assert is_error
    assert body["status"] == "awaiting_user"
    assert rig.mail.ran == []


async def test_a_turn_before_the_question_does_not_count(rig: _Rig, clock: _Clock) -> None:
    # Another question is open, so turns are being noted.
    spoken_consent.mint("other", {}, trace_id=uuid4())
    spoken_consent.note_user_turn("yes, send it")  # the request itself, before the mint
    clock.tick()
    token = await _ask(rig, clock)

    body, is_error = await rig.call("gmail", {**SEND, "confirm_token": token})

    assert is_error and body["status"] == "awaiting_user"
    assert rig.mail.ran == []


async def test_yes_after_the_question_runs_it_once(rig: _Rig, clock: _Clock) -> None:
    token = await _ask(rig, clock)
    spoken_consent.note_user_turn("yes")
    clock.tick()

    body, is_error = await rig.call("gmail", {**SEND, "confirm_token": token})

    assert not is_error
    assert body == "ran send_message"
    assert rig.mail.ran == [SEND], "the confirm_token never reaches the tool"
    assert [event.tool_name for event in rig.executed] == ["gmail"], "audited by the executor"

    again, again_error = await rig.call("gmail", {**SEND, "confirm_token": token})
    assert again_error and again["status"] == "unknown"
    assert len(rig.mail.ran) == 1, "a token is single use"


@pytest.mark.parametrize("answer", ["ja, mach das", "sí, adelante", "Yeah, go ahead"])
async def test_yes_in_any_language_confirms(rig: _Rig, clock: _Clock, answer: str) -> None:
    token = await _ask(rig, clock)
    spoken_consent.note_user_turn(answer)

    _body, is_error = await rig.call("gmail", {**SEND, "confirm_token": token})

    assert not is_error
    assert len(rig.mail.ran) == 1


async def test_changed_arguments_are_refused(rig: _Rig, clock: _Clock) -> None:
    token = await _ask(rig, clock)
    spoken_consent.note_user_turn("yes")

    body, is_error = await rig.call(
        "gmail", {**SEND, "to": "mallory@example.org", "confirm_token": token}
    )

    assert is_error and body["status"] == "mismatch"
    assert rig.mail.ran == []
    # The token is spent and the deferred call dropped, with an audit row.
    retry, _ = await rig.call("gmail", {**SEND, "confirm_token": token})
    assert retry["status"] == "unknown"
    assert rig.mail.ran == []
    assert any(event.reason == "consent_mismatch" for event in rig.denied)


async def test_no_denies_and_never_runs(rig: _Rig, clock: _Clock) -> None:
    token = await _ask(rig, clock)
    spoken_consent.note_user_turn("no, don't send it")

    body, is_error = await rig.call("gmail", {**SEND, "confirm_token": token})

    assert is_error and body["status"] == "denied"
    assert rig.mail.ran == []
    assert any(event.reason == "user_declined" for event in rig.denied)


async def test_moving_on_denies(rig: _Rig, clock: _Clock) -> None:
    token = await _ask(rig, clock)
    spoken_consent.note_user_turn("what's the weather tomorrow")

    body, is_error = await rig.call("gmail", {**SEND, "confirm_token": token})

    assert is_error and body["status"] == "denied"
    assert rig.mail.ran == []


async def test_a_hedge_keeps_waiting(rig: _Rig, clock: _Clock) -> None:
    token = await _ask(rig, clock)
    spoken_consent.note_user_turn("hmm, wait")
    clock.tick()

    body, is_error = await rig.call("gmail", {**SEND, "confirm_token": token})
    assert is_error and body["status"] == "unclear"

    spoken_consent.note_user_turn("yes")
    _body, is_error = await rig.call("gmail", {**SEND, "confirm_token": token})
    assert not is_error and len(rig.mail.ran) == 1


async def test_expired_token_is_refused(rig: _Rig, clock: _Clock) -> None:
    token = await _ask(rig, clock)
    spoken_consent.note_user_turn("yes")
    clock.tick(spoken_consent.CONSENT_TTL_S + 1)

    body, is_error = await rig.call("gmail", {**SEND, "confirm_token": token})

    assert is_error and body["status"] in {"expired", "unknown"}
    assert rig.mail.ran == []
    assert any(event.reason == "consent_expired" for event in rig.denied)


async def test_a_made_up_token_is_refused(rig: _Rig, clock: _Clock) -> None:
    spoken_consent.note_user_turn("yes")

    body, is_error = await rig.call("gmail", {**SEND, "confirm_token": "the-user-said-yes"})

    assert is_error and body["status"] == "unknown"
    assert rig.mail.ran == []


async def test_safe_calls_run_and_block_stays_blocked(rig: _Rig, clock: _Clock) -> None:
    body, is_error = await rig.call("gmail", {"action": "list_messages"})
    assert not is_error and body == "ran list_messages"

    _body, is_error = await rig.call("wipe-disk", {})
    assert is_error
    assert rig.wipe.ran == 0
    assert spoken_consent._pending == {}, "a blocked call is never offered for consent"


async def test_camera_pictures_stay_on_this_computer(rig: _Rig, clock: _Clock) -> None:
    body, _ = await rig.call("camera", {})
    assert body["needs_confirmation"] is True and rig.camera.ran == 0
    clock.tick()
    spoken_consent.note_user_turn("yes")

    text, is_error = await rig.call("camera", {"confirm_token": body["confirm_token"]})

    assert not is_error and rig.camera.ran == 1
    assert "AAAA" not in text
    assert "not sent over this connection" in text


async def test_outside_listing_offers_the_confirm_token(rig: _Rig, clock: _Clock) -> None:
    listed = await rig.server.request_handlers[types.ListToolsRequest](types.ListToolsRequest())
    schemas = {tool.name: tool.inputSchema for tool in listed.root.tools}

    assert "confirm_token" in schemas["gmail"]["properties"]
    assert "confirm_token" in schemas["camera"]["properties"]
    assert "confirm_token" not in schemas["wipe-disk"]["properties"]


# ------------------------------------------------------------ who counts as the user


def test_only_a_person_s_turn_is_noted(clock: _Clock) -> None:
    spoken_consent.mint("gmail", SEND, trace_id=uuid4())
    clock.tick()

    spoken_consent.note_conversation_turn("yes", in_conversation=False)  # an internal prompt
    agent = current_chat_turn.set(ChatTurn("s", "t", "yes", False, "x"))
    try:
        spoken_consent.note_conversation_turn("yes", in_conversation=True)  # an agent message
    finally:
        current_chat_turn.reset(agent)
    assert list(spoken_consent._turns) == []

    person = current_chat_turn.set(ChatTurn("s", "t", "yes", True, "x"))
    try:
        spoken_consent.note_conversation_turn("yes", in_conversation=False)
    finally:
        current_chat_turn.reset(person)
    spoken_consent.note_conversation_turn("yes", in_conversation=True)  # voice
    assert [answer for _, answer in spoken_consent._turns] == ["confirm", "confirm"]


def test_an_idle_jarvis_keeps_no_turns(clock: _Clock) -> None:
    spoken_consent.note_user_turn("yes")
    assert list(spoken_consent._turns) == []
