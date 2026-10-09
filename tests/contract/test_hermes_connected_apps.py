"""Native MCP consent resumes only the exact action parked by ToolExecutor."""

from __future__ import annotations

import asyncio
import base64
import json
import socket
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import httpx
import mcp.types as types
import pytest
import pytest_asyncio
import uvicorn
from fastapi import FastAPI
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.lowlevel.server import request_ctx

from jarvis.brain.tool_gateway import BrainSupervisorToolGateway
from jarvis.core import runtime_refs
from jarvis.core.bus import EventBus
from jarvis.core.config import SafetyConfig
from jarvis.core.events import ActionDenied, ActionExecuted
from jarvis.core.protocols import ToolResult
from jarvis.safety.approval import ApprovalWorkflow
from jarvis.safety.risk_tier import RiskTierEvaluator
from jarvis.safety.tool_executor import ToolExecutor


@dataclass
class Connector:
    name: str
    risk_tier: str = "ask"
    description: str = "Fixture connected app: read records or send a fixture message."
    schema: dict[str, Any] = field(
        default_factory=lambda: {
            "type": "object",
            "properties": {"action": {"type": "string"}},
            "required": ["action"],
        }
    )
    calls: list[tuple[dict[str, Any], Any]] = field(default_factory=list)
    result: ToolResult = field(
        default_factory=lambda: ToolResult(success=True, output={"id": "fixture"})
    )

    def risk_tier_for_args(self, args: dict[str, Any]) -> str:
        return "safe" if args.get("action") in {"search", "read", "list"} else "ask"

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        self.calls.append((args, ctx))
        return self.result


class ConsentSession:
    def __init__(self, answer: str = "accept", *, supported: bool = True, parked: bool = False):
        self.answer = answer
        self.supported = supported
        self.parked = parked
        self.messages: list[str] = []
        self.ready = asyncio.Event()
        self.reply = asyncio.Event()

    def check_client_capability(self, _capability: Any) -> bool:
        return self.supported

    async def elicit_form(self, message: str, requestedSchema: Any, related_request_id: Any) -> Any:
        assert requestedSchema == {"type": "object", "properties": {}}
        assert related_request_id == 17
        self.messages.append(message)
        self.ready.set()
        if self.parked:
            await self.reply.wait()
        return types.ElicitResult(
            action=self.answer, content={} if self.answer == "accept" else None
        )


@pytest.fixture
def rig():
    bus = EventBus()
    events: list[Any] = []
    denied = asyncio.Event()

    async def record(event: Any) -> None:
        events.append(event)
        if isinstance(event, ActionDenied):
            denied.set()

    bus.subscribe(ActionExecuted, record)
    bus.subscribe(ActionDenied, record)
    executor = ToolExecutor(
        bus=bus, evaluator=RiskTierEvaluator(SafetyConfig()), approval=ApprovalWorkflow(bus)
    )
    tools = {
        name: Connector(name)
        for name in ("gmail", "google_calendar", "google_drive", "camera", "wiki-recall")
    }
    tools["spawn-worker"] = Connector("spawn-worker")
    tools["switch-provider"] = Connector("switch-provider")
    tools["calendar/read_event"] = Connector("calendar/read_event")
    tools["jarvis/gmail"] = Connector("jarvis/gmail")
    gateway = BrainSupervisorToolGateway(SimpleNamespace(_tools=tools, _tool_executor=executor))
    runtime_refs.set_supervisor_tool_gateway(gateway)
    yield SimpleNamespace(
        tools=tools, executor=executor, events=events, gateway=gateway, denied=denied
    )
    runtime_refs._reset_for_tests()


async def call(name: str, args: dict[str, Any], consent: ConsentSession) -> Any:
    from jarvis.mcp.hermes_tools_server import build_server

    server = build_server()
    token = request_ctx.set(SimpleNamespace(session=consent, request_id=17))
    try:
        answer = await server.request_handlers[types.CallToolRequest](
            types.CallToolRequest(
                params=types.CallToolRequestParams(name=name, arguments=args),
            )
        )
        return answer.root
    finally:
        request_ctx.reset(token)


@pytest.mark.asyncio
async def test_catalog_projects_connected_tools_and_permissions_without_recursive_routes(rig):
    from jarvis.mcp.hermes_tools_server import build_server

    server = build_server()
    listed = (
        await server.request_handlers[types.ListToolsRequest](types.ListToolsRequest())
    ).root.tools
    names = {item.name for item in listed}
    assert {"gmail", "google_calendar", "google_drive", "camera", "wiki-recall"} <= names
    assert "spawn-worker" not in names and "switch-provider" not in names
    assert not any("jarvis_gmail" in name for name in names)
    assert any("calendar/read_event" in item.description for item in listed)
    assert all(item.inputSchema and item.description and item.annotations for item in listed)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name,action",
    [
        ("gmail", "search"),
        ("gmail", "read"),
        ("google_calendar", "list"),
        ("google_drive", "search"),
        ("google_drive", "read"),
    ],
)
async def test_reads_use_real_gateway_without_consent(rig, name, action):
    consent = ConsentSession(supported=False)
    answer = await call(name, {"action": action}, consent)
    assert not answer.isError
    assert json.loads(answer.content[0].text) == {"id": "fixture"}
    assert len(rig.tools[name].calls) == 1
    assert consent.messages == []
    assert any(isinstance(event, ActionExecuted) for event in rig.events)


@pytest.mark.asyncio
async def test_write_is_parked_then_allowed_once_with_original_args(rig):
    consent = ConsentSession(parked=True)
    args = {"action": "send", "to": "fixture@example.test", "body": "fixture only"}
    task = asyncio.create_task(call("gmail", args, consent))
    await asyncio.wait_for(consent.ready.wait(), 2)
    assert rig.tools["gmail"].calls == []
    assert len(rig.executor._pending_voice) == 1
    args["body"] = "changed after dispatch"
    consent.reply.set()
    answer = await task
    assert not answer.isError
    assert rig.tools["gmail"].calls[0][0]["body"] == "fixture only"
    assert rig.tools["gmail"].calls[0][1].approved_by == "user"
    completed = next(event for event in rig.events if isinstance(event, ActionExecuted))
    assert answer.meta["io.personaljarvis/trace_id"] == str(completed.trace_id)
    assert answer.meta["io.personaljarvis/tool"] == "gmail"
    assert rig.executor._pending_voice == {}
    again = ConsentSession("decline")
    assert (await call("gmail", {"action": "send"}, again)).isError
    assert len(rig.tools["gmail"].calls) == 1, "once is never a standing grant"


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["decline", "cancel"])
async def test_denial_or_native_stop_never_executes(rig, answer):
    result = await call("gmail", {"action": "send"}, ConsentSession(answer))
    assert result.isError
    assert rig.tools["gmail"].calls == []
    assert rig.executor._pending_voice == {}
    assert any(isinstance(event, ActionDenied) for event in rig.events)
    assert not any(isinstance(event, ActionExecuted) for event in rig.events)


@pytest.mark.asyncio
async def test_cancelled_rpc_cleans_exact_pending_action_and_late_accept_cannot_resume(rig):
    consent = ConsentSession(parked=True)
    task = asyncio.create_task(call("gmail", {"action": "send"}, consent))
    await asyncio.wait_for(consent.ready.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    consent.reply.set()
    assert rig.executor._pending_voice == {}
    assert rig.tools["gmail"].calls == []


@pytest.mark.asyncio
async def test_client_without_approval_support_fails_closed_and_drops_pending(rig):
    result = await call(
        "gmail",
        {"action": "send", "approval_surface": "unattended"},
        ConsentSession(supported=False),
    )
    assert result.isError and "approval" in result.content[0].text.lower()
    assert rig.tools["gmail"].calls == [] and rig.executor._pending_voice == {}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        "Gmail is not connected. Connect it in Plugins.",
        "Your authorization expired. Reconnect Gmail.",
    ],
)
async def test_connector_unavailable_or_auth_expired_is_failed_evidence(rig, error):
    rig.tools["gmail"].result = ToolResult(success=False, output=None, error=error)
    answer = await call("gmail", {"action": "search"}, ConsentSession())
    assert answer.isError and error in answer.content[0].text
    assert not any(isinstance(event, ActionExecuted) and event.success for event in rig.events)


@pytest.mark.asyncio
async def test_secret_fields_and_credentials_are_redacted_in_results_and_confirmation(rig):
    rig.tools["gmail"].result = ToolResult(
        success=True,
        output={
            "subject": "Keep this content",
            "refresh_token": "opaque-refresh-fixture",
            "access_token": "opaque-access-fixture",
        },
    )
    consent = ConsentSession()
    answer = await call(
        "gmail",
        {
            "action": "send",
            "body": "password=fixture-super-secret",
            "refresh_token": "opaque-refresh-fixture",
        },
        consent,
    )
    combined = answer.content[0].text + " ".join(consent.messages)
    for secret in ("opaque-refresh-fixture", "opaque-access-fixture", "fixture-super-secret"):
        assert secret not in combined
    assert "Keep this content" in combined


@pytest.mark.asyncio
async def test_unknown_or_removed_tool_cannot_call_gateway(rig):
    assert (await call("switch-provider", {"action": "send"}, ConsentSession())).isError
    rig.tools.pop("gmail")
    assert (await call("gmail", {"action": "read"}, ConsentSession())).isError


@pytest.mark.asyncio
async def test_blacklist_remains_above_native_consent(rig):
    # A native accept cannot revive an executor refusal before dispatch.
    rig.executor._evaluator = RiskTierEvaluator(SafetyConfig(blacklist={"commands": ["gmail *"]}))
    consent = ConsentSession()
    result = await call("gmail", {"action": "send"}, consent)
    assert result.isError and rig.tools["gmail"].calls == [] and consent.messages == []


@pytest.mark.asyncio
async def test_two_pending_calls_have_separate_receipts_and_cancel_is_scoped(rig):
    first, second = ConsentSession(parked=True), ConsentSession(parked=True)
    a = asyncio.create_task(call("gmail", {"action": "send", "body": "first"}, first))
    b = asyncio.create_task(call("google_drive", {"action": "share", "file_id": "second"}, second))
    await asyncio.wait_for(asyncio.gather(first.ready.wait(), second.ready.wait()), 2)
    assert len(rig.executor._pending_voice) == 2
    a.cancel()
    with pytest.raises(asyncio.CancelledError):
        await a
    assert len(rig.executor._pending_voice) == 1
    second.reply.set()
    assert not (await b).isError
    assert rig.tools["gmail"].calls == []
    assert rig.tools["google_drive"].calls[0][0] == {"action": "share", "file_id": "second"}
    assert rig.executor._pending_voice == {}


@pytest.mark.asyncio
async def test_expired_consent_or_changed_connector_never_runs_saved_tool(rig, monkeypatch):
    from jarvis.mcp import hermes_tools_server

    monkeypatch.setattr(hermes_tools_server, "CONSENT_TIMEOUT_S", 0.02)
    result = await call("gmail", {"action": "send"}, ConsentSession(parked=True))
    assert result.isError and "expired" in result.content[0].text
    assert rig.tools["gmail"].calls == [] and rig.executor._pending_voice == {}
    monkeypatch.setattr(hermes_tools_server, "CONSENT_TIMEOUT_S", 2)
    consent = ConsentSession(parked=True)
    task = asyncio.create_task(call("gmail", {"action": "send"}, consent))
    await asyncio.wait_for(consent.ready.wait(), 1)
    rig.tools.pop("gmail")
    consent.reply.set()
    assert (await task).isError and rig.executor._pending_voice == {}


@pytest.mark.asyncio
async def test_owned_mail_calendar_and_drive_reads_keep_auth_out_of_model_results(rig):
    from jarvis.plugins.tool.drive_rest import DriveRestTool
    from jarvis.plugins.tool.gmail_rest import GmailRestTool
    from jarvis.plugins.tool.google_calendar_rest import GoogleCalendarRestTool

    oauth = "opaque-oauth-fixture-only"
    seen = []

    def mail(req):
        assert req.headers["authorization"] == f"Bearer {oauth}"
        seen.append(req.url.path)
        if req.url.path.endswith("/messages"):
            return httpx.Response(200, json={"messages": [{"id": "fixture-message"}]})
        return httpx.Response(
            200,
            json={
                "id": "fixture-message",
                "payload": {
                    "headers": [{"name": "Subject", "value": "Fixture mail"}],
                    "mimeType": "text/plain",
                    "body": {"data": base64.urlsafe_b64encode(b"Read mail fixture").decode()},
                },
            },
        )

    def drive(req):
        assert req.headers["authorization"] == f"Bearer {oauth}"
        if req.url.params.get("alt") == "media":
            return httpx.Response(200, text="Read Drive fixture")
        if req.url.path.endswith("/files"):
            return httpx.Response(
                200,
                json={
                    "files": [
                        {"id": "fixture-file", "name": "Fixture file", "mimeType": "text/plain"}
                    ]
                },
            )
        return httpx.Response(
            200, json={"id": "fixture-file", "name": "Fixture file", "mimeType": "text/plain"}
        )

    async def calendar(action, _args, token):
        assert action == "list_events" and token == oauth
        return {
            "ok": True,
            "data": {"events": [{"id": "fixture-event", "summary": "Fixture calendar"}]},
        }

    rig.tools["gmail"] = GmailRestTool(
        access_token_provider=lambda: oauth, transport=httpx.MockTransport(mail)
    )
    rig.tools["google_drive"] = DriveRestTool(
        access_token_provider=lambda: oauth, transport=httpx.MockTransport(drive)
    )
    rig.tools["google_calendar"] = GoogleCalendarRestTool(
        access_token_provider=lambda: oauth, node_runner=calendar
    )
    cases = [
        ("gmail", {"action": "list_messages", "query": "fixture"}, "fixture-message"),
        ("gmail", {"action": "get_message", "message_id": "fixture-message"}, "Read mail fixture"),
        ("google_calendar", {"action": "list_events"}, "Fixture calendar"),
        ("google_drive", {"action": "list_files", "search_text": "fixture"}, "Fixture file"),
        ("google_drive", {"action": "read_file", "file_id": "fixture-file"}, "Read Drive fixture"),
    ]
    for name, args, expected in cases:
        consent = ConsentSession(supported=False)
        result = await call(name, args, consent)
        assert not result.isError and expected in result.content[0].text
        assert oauth not in result.content[0].text and consent.messages == []
    assert len(seen) == 2


@pytest.mark.asyncio
async def test_owned_mail_expired_authorization_and_disconnect_fail_cleanly(rig):
    from jarvis.plugins.tool.gmail_rest import GmailRestTool

    async def no_refresh():
        return False

    def expired(_req):
        return httpx.Response(401, json={"error": "refresh_token=opaque-error-fixture"})

    rig.tools["gmail"] = GmailRestTool(
        access_token_provider=lambda: "expired-fixture",
        token_refresher=no_refresh,
        transport=httpx.MockTransport(expired),
    )
    result = await call("gmail", {"action": "list_messages"}, ConsentSession())
    assert result.isError and "connect" in result.content[0].text.lower()
    assert "opaque-error-fixture" not in result.content[0].text
    rig.tools["gmail"] = GmailRestTool(access_token_provider=lambda: None)
    result = await call("gmail", {"action": "list_messages"}, ConsentSession())
    assert result.isError and "connect" in result.content[0].text.lower()


@pytest.mark.asyncio
async def test_refresh_token_is_redacted_before_audit_and_native_confirmation(rig):
    consent = ConsentSession()
    await call(
        "gmail", {"action": "send", "body": "refresh_token=opaque-fixture-refresh-token"}, consent
    )
    assert "opaque-fixture-refresh-token" not in " ".join(consent.messages)


@pytest_asyncio.fixture
async def endpoint(monkeypatch, rig):
    """Real loopback HTTP transport; only fixture tools and a fixture control key."""
    from jarvis.core import control_key
    from jarvis.ui.web import mcp_server_routes as routes

    monkeypatch.setattr(control_key, "verify_control_key", lambda value: value == "fixture-control")
    surface = routes._Surface("hermes", routes._build_hermes_server, native_consent=True)
    monkeypatch.setitem(routes._SUFFIX_SURFACES, "hermes", surface)
    monkeypatch.setattr(routes, "_HERMES_SURFACE", surface)
    app = FastAPI()
    app.mount("/api/control/mcp", routes.build_mcp_asgi_app())
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    address = f"http://127.0.0.1:{sock.getsockname()[1]}/api/control/mcp/hermes"
    started = asyncio.Event()

    class ReadyServer(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            started.set()

    server = ReadyServer(uvicorn.Config(app, log_level="error", lifespan="off"))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        await asyncio.wait_for(started.wait(), 3)
        assert server.started
        yield address
    finally:
        if surface._task is not None:
            surface._task.cancel()
            await asyncio.gather(surface._task, return_exceptions=True)
        server.should_exit = True
        await asyncio.wait_for(task, 3)
        sock.close()


@pytest.mark.asyncio
async def test_real_mcp_handshake_and_approval_round_trip(endpoint, rig):
    requests = []

    async def consent(_context, params):
        requests.append(params.message)
        assert rig.tools["gmail"].calls == [], "no write before the native reply"
        return types.ElicitResult(action="accept", content={})

    async with httpx.AsyncClient(headers={"Authorization": "Bearer fixture-control"}) as http:
        assert (
            await http.post(endpoint, headers={"Authorization": "Bearer wrong"}, json={})
        ).status_code == 401
        async with streamable_http_client(endpoint, http_client=http) as (read, write, session_id):
            async with ClientSession(read, write, elicitation_callback=consent) as client:
                init = await client.initialize()
                assert init.serverInfo.name == "jarvis-hermes"
                assert session_id(), "approval replies require a real session"
                listed = await client.list_tools()
                assert {"gmail", "google_calendar", "google_drive"} <= {
                    tool.name for tool in listed.tools
                }
                assert not (await client.call_tool("gmail", {"action": "search"})).isError
                rig.tools["gmail"].calls.clear()
                written = await client.call_tool(
                    "gmail", {"action": "send", "to": "fixture@example.test"}
                )
                assert not written.isError and len(requests) == 1
                assert rig.tools["gmail"].calls[0][1].approved_by == "user"
    assert rig.executor._pending_voice == {}


@pytest.mark.asyncio
async def test_real_mcp_cancel_notification_retires_action_before_late_accept(endpoint, rig):
    ready, late = asyncio.Event(), asyncio.Event()

    async def consent(_context, _params):
        ready.set()
        await late.wait()
        return types.ElicitResult(action="accept", content={})

    async with httpx.AsyncClient(headers={"Authorization": "Bearer fixture-control"}) as http:
        async with streamable_http_client(endpoint, http_client=http) as (read, write, _):
            async with ClientSession(read, write, elicitation_callback=consent) as client:
                await client.initialize()
                request_id = client._request_id
                task = asyncio.create_task(client.call_tool("gmail", {"action": "send"}))
                await asyncio.wait_for(ready.wait(), 3)
                assert rig.tools["gmail"].calls == [] and len(rig.executor._pending_voice) == 1
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                # Jarvis's SDK 1.x does not emit a cancellation notification on
                # coroutine abandonment. Send the actual MCP cancellation here;
                # the native-Hermes contract separately exercises SDK 2.x's
                # automatic cancellation and Hermes's approval withdrawal.
                await client.send_notification(
                    types.ClientNotification(
                        types.CancelledNotification(
                            params=types.CancelledNotificationParams(requestId=request_id)
                        ),
                    )
                )
                await asyncio.wait_for(rig.denied.wait(), 3)
                assert rig.executor._pending_voice == {}
                late.set()
                assert not (await client.call_tool("google_calendar", {"action": "list"})).isError
    assert rig.tools["gmail"].calls == [] and rig.executor._pending_voice == {}
