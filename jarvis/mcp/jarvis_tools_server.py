"""Jarvis' own tools, offered outwards as an MCP server.

Every other MCP module in this package points INWARDS: Jarvis is the client
that mounts somebody else's server (``client.py``, ``loader.py``,
``registry.py``), or projects a connected plugin into a worker's config
(``claude_export.py``, ``marketplace/mcp_bridge.py``). This module is the one
that points OUT — it lets an agent session that Jarvis spawned reach back in
and drive Jarvis itself.

That is what turns the typed chat from "a coding CLI that happens to run in
Jarvis' window" into Jarvis with a keyboard instead of a microphone: the model
paying with the person's subscription runs its own agent loop, but its hands
are the hands the voice has — ``open-app``, ``google-calendar``,
``wiki-recall``, ``switch-provider``, every registered tool plugin.

**The safety seam is structural, not polite.** Tools are listed from
:class:`~jarvis.core.protocols.SupervisorToolGateway` and executed through it,
which is the only authorized execution path in the codebase (AP-3). A caller
here cannot reach a tool's ``execute`` directly, so risk tiers, the approval
workflow and the audit log apply exactly as they do on the voice path — there
is no second door to keep in sync. A caller without a chat session (the Hermes
brain, ADR-0042) gets one more lock on top: an ask-tier call runs only after
the user's own next turn said yes (``_outside_call``, ``spoken_consent``).

Transport is Streamable HTTP, mounted on the app's own web server
(``jarvis.ui.web.mcp_server_routes``) rather than a stdio child process. The
gateway lives in the running app's memory; a child process would only be able
to talk back over the same HTTP anyway, one hop later.
"""

from __future__ import annotations

import hashlib
import json
import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Final
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

log = logging.getLogger(__name__)

#: What ``SupervisorToolRequest.origin`` is stamped with. The risk evaluator and
#: the audit log use it to tell a chat-driven action from a spoken one.
CHAT_ORIGIN: Final[str] = "agent-chat"

#: The agent-chat session a request belongs to, set per HTTP request by
#: ``jarvis.ui.web.mcp_server_routes`` from the ``X-Jarvis-Chat-Session``
#: header the chat put into the CLI's MCP config. Read when a tool is
#: executed: with a session the executor is told the approval surface is the
#: chat's card (``approval_surface: interactive`` + the session's
#: ``approval_ref``), so an ask-tier tool the CLI reaches through here asks
#: the person in the chat instead of failing fast as unattended. Without one
#: the caller is an outside agent (Hermes) and ``_outside_call`` applies the
#: spoken-consent gate instead.
CHAT_SESSION_REF: ContextVar[str | None] = ContextVar("jarvis.mcp.chat_session_ref", default=None)

# A named, individually revocable credential for an outside MCP client. The
# token is carried in the request context and filters both discovery and calls.
TOOLS_TOKEN: ContextVar[Any | None] = ContextVar("jarvis.mcp.tools_token", default=None)

#: How long the chat's card may stay open for a gate reached over MCP.
CHAT_APPROVAL_TIMEOUT_S: Final[float] = 600.0

#: ``SupervisorToolRequest.origin`` for a caller that is NOT one of Jarvis's
#: own chat sessions: the Hermes brain (ADR-0042), which mounts this server as
#: ``mcp_servers.jarvis`` and sends no session header.
OUTSIDE_ORIGIN: Final[str] = "hermes-mcp"

#: The argument an outside caller adds when it retries a deferred ask-tier
#: call. Stripped before the tool sees its arguments.
CONFIRM_TOKEN_ARG: Final[str] = "confirm_token"  # noqa: S105 — an argument name, not a secret

_CONFIRM_TOKEN_SCHEMA: Final[dict[str, Any]] = {
    "type": "string",
    "description": (
        "Only when retrying a call that answered needs_confirmation: the "
        "confirm_token from that answer, sent after the user replied."
    ),
}


def outside_snapshot() -> dict[str, Any]:
    """The executor's ``config_snapshot`` for a call from an outside agent.

    ``conversational``: an ask-tier call is deferred (the executor keeps the
    tool and arguments out of band) instead of failing fast as unattended,
    and only the user's own next turn can release it (``_outside_call``).
    """
    return {"approval_surface": "conversational", "tool_origin": OUTSIDE_ORIGIN}


def approval_snapshot() -> dict[str, Any]:
    """The executor's ``config_snapshot`` for the current request's session."""
    session_id = CHAT_SESSION_REF.get()
    if not session_id:
        return {}
    from jarvis.core.task_agent import scope_for

    if scope_for(session_id) is not None:
        return {"approval_surface": "unattended", "approval_ref": f"agent-chat:{session_id}"}
    return {
        "approval_surface": "interactive",
        "approval_ref": f"agent-chat:{session_id}",
        "approval_timeout_s": CHAT_APPROVAL_TIMEOUT_S,
        "tool_origin": CHAT_ORIGIN,
    }


#: Tools never offered to a chat session, by name. A spawn vehicle would let the
#: session start a background worker that starts another one — the recursion the
#: router tiers exist to prevent (AP-5/AP-14). The session IS the heavy worker
#: here; it has no reason to hire one.
_WITHHELD: Final[frozenset[str]] = frozenset(
    {
        "spawn-worker",
        "spawn_worker",
        "multi-spawn",
        "multi_spawn",
        "spawn-subagents",
        "spawn_subagents",
        "dispatch-to-harness",
        "dispatch_to_harness",
        "dispatch-with-review",
        "dispatch_with_review",
        "dispatch-to-admin",
        "dispatch_to_admin",
    }
)

#: Native names stay unchanged. Imported MCP names use server/tool internally;
#: only this outgoing transport projects them to a portable wire name.
_NAME_OK = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")


def _usable_name(name: str) -> bool:
    return bool(name) and len(name) <= 128 and set(name) <= _NAME_OK


def _wire_name(name: str) -> str | None:
    if _usable_name(name):
        return name
    parts = name.split("/")
    if len(parts) != 2 or not all(_usable_name(part) for part in parts):
        return None
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:16]
    return f"{name.replace('/', '_')[:47]}_{digest}"


def _wire_catalog(entries: list[Any]) -> dict[str, Any]:
    """Reject ambiguous aliases instead of routing a call to the wrong tool."""
    result: dict[str, Any] = {}
    collisions: set[str] = set()
    for entry in entries:
        canonical = str(entry.name)
        wire = _wire_name(canonical)
        if wire is None or canonical in _WITHHELD or wire in collisions:
            continue
        if wire in result:
            del result[wire]
            collisions.add(wire)
            continue
        result[wire] = entry
    return result


async def _session_wire_catalog() -> dict[str, Any]:
    from jarvis.core.task_agent import scope_for

    session_id = CHAT_SESSION_REF.get()
    scoped = getattr(_gateway(), "session_catalog", None)
    entries = list(await scoped(session_id)) if session_id and callable(scoped) else offered_tools()
    task_scope = scope_for(session_id)
    if session_id and session_id.startswith("task-") and task_scope is None:
        return {}
    if task_scope is not None:
        entries = [entry for entry in entries if entry.name in task_scope.names]
    token = TOOLS_TOKEN.get()
    if token is not None:
        entries = _tools_token_filter(entries, token)
    return _wire_catalog(entries)


def _tools_token_filter(entries: list[Any], token: Any) -> list[Any]:
    """Apply a tools-surface credential's exact names and risk-tier limits."""
    from jarvis.mcp.agents.tokens import allows_jarvis_tool

    return [
        entry
        for entry in entries
        if allows_jarvis_tool(token, str(entry.name), str(entry.risk_tier))
    ]


def _gateway() -> Any | None:
    from jarvis.core import runtime_refs

    return runtime_refs.get_supervisor_tool_gateway()


def offered_tools() -> list[Any]:
    """The catalog entries a chat session may call, gateway order preserved."""
    gateway = _gateway()
    if gateway is None:
        return []
    try:
        catalog = gateway.catalog()
    except Exception:  # noqa: BLE001 — a broken catalog offers nothing, it never crashes the server
        log.warning("jarvis MCP: tool catalog unavailable", exc_info=True)
        return []
    return [
        entry
        for entry in catalog
        if _wire_name(str(entry.name)) is not None and str(entry.name) not in _WITHHELD
    ]


def _render(result: Any) -> str:
    """A ToolResult as the text the model reads back."""
    if result is None:
        return "The tool returned nothing."
    output = getattr(result, "output", None)
    error = getattr(result, "error", None)
    if not getattr(result, "success", False):
        return f"Tool failed: {error or 'no reason given'}"
    if output is None:
        return "Done."
    if isinstance(output, str):
        return output
    try:
        return json.dumps(output, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(output)


def _may_ask(entry: Any) -> bool:
    """Whether a call of this tool can reach the ask gate (statically or per call)."""
    return entry.risk_tier == "ask" or getattr(entry, "risk_tier_for_args", None) is not None


def _with_confirm_token(schema: dict[str, Any]) -> dict[str, Any]:
    properties = schema.get("properties")
    if not isinstance(properties, dict) or CONFIRM_TOKEN_ARG in properties:
        return schema
    return {**schema, "properties": {**properties, CONFIRM_TOKEN_ARG: _CONFIRM_TOKEN_SCHEMA}}


def _clip(value: Any, limit: int = 160) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


#: Plain-words phrasing for the deferred calls people meet most. Anything not
#: listed is described by its tool name and arguments.
_ACTION_PHRASES: Final[dict[tuple[str, str], str]] = {
    ("gmail", "send_message"): "send an email to {to} with the subject '{subject}'",
    ("gmail", "delete_message"): "permanently delete the email {message_id}",
    ("call-contact", ""): "phone {name}",
    ("camera", ""): "take a photo with the webcam",
}


def _consent_question(name: str, args: dict[str, Any], output: Any) -> str:
    """What the deferred call would do, in plain words, for the user to judge."""
    action = str(args.get("action") or "")
    phrase = _ACTION_PHRASES.get((name, action)) or _ACTION_PHRASES.get((name, ""))
    shown = {key: _clip(value) for key, value in args.items() if value not in (None, "", [], {})}
    if phrase is not None:
        try:
            what = "Jarvis will " + phrase.format_map(shown)
        except KeyError:  # an argument the phrase names is missing: describe it generically
            phrase = None
    if phrase is None:
        details = "; ".join(f"{key}: {value}" for key, value in shown.items())
        what = f"Jarvis will run its '{name}' tool" + (f" with {details}" if details else "")
    impact = output.get("impact") if isinstance(output, dict) else None
    if isinstance(impact, dict) and impact:
        effects = ", ".join(f"{key}: {_clip(value, 60)}" for key, value in impact.items())
        what += f" ({effects})"
    return what + "."


def _render_outside(result: Any) -> str:
    """``_render`` for an outside agent, honest about pictures it does not get.

    Image artifacts (the camera, screenshots) never cross this transport:
    ``_render`` sends only ``output``. Saying so keeps the model from
    describing a photo it never saw. Sending the picture would put it in front
    of whatever model Hermes chose, possibly a cloud one, which needs the
    user's cloud-vision consent this transport cannot check.
    """
    text = _render(result)
    images = [
        item
        for item in getattr(result, "artifacts", None) or ()
        if isinstance(item, dict) and item.get("type") == "image"
    ]
    if getattr(result, "success", False) and images:
        text += (
            f" ({len(images)} image(s) captured and kept on this computer; "
            "images are not sent over this connection.)"
        )
    return text


async def _cancel_deferred(gateway: Any, trace_id: UUID, reason: str) -> None:
    """Drop a deferred call; the executor records the ``ActionDenied``."""
    cancel = getattr(gateway, "cancel_pending", None)
    if not callable(cancel):
        return
    try:
        await cancel(trace_id, reason=reason)
    except Exception:  # noqa: BLE001 — the call stays unrun either way; only the audit row is lost
        log.warning("jarvis MCP: dropping a deferred call failed", exc_info=True)


@dataclass(frozen=True, slots=True)
class _GateAnswer:
    """What the consent gate says instead of a tool result: nothing ran."""

    text: str
    is_error: bool


def _refusal(status: str, message: str) -> _GateAnswer:
    body = {"needs_confirmation": False, "executed": False, "status": status, "message": message}
    return _GateAnswer(json.dumps(body, ensure_ascii=False), True)


async def _outside_call(gateway: Any, name: str, arguments: dict[str, Any]) -> Any:
    """One call from an outside agent, with the spoken-consent gate.

    Safe and monitor calls run as before, and block-tier calls stay blocked:
    the executor decides that. An ask-tier call without a valid consent never
    runs. The executor defers it, and the caller gets ``needs_confirmation``
    with a token. A retry with that token runs the deferred call ONCE, and
    only when the first real user turn after the question was a yes
    (``jarvis.safety.spoken_consent``). The model can carry the token but
    cannot produce that turn. Returns the gateway's ``ToolResult`` when the
    executor decided the call, else a :class:`_GateAnswer`.
    """
    from jarvis.core.protocols import SupervisorToolRequest
    from jarvis.safety import spoken_consent
    from jarvis.safety.tool_executor import VOICE_CONFIRM_SENTINEL

    for stale in spoken_consent.expire_stale():
        await _cancel_deferred(gateway, stale.trace_id, "consent_expired")

    token = arguments.pop(CONFIRM_TOKEN_ARG, None)
    if not token:
        request = SupervisorToolRequest(
            trace_id=uuid4(),
            origin=OUTSIDE_ORIGIN,
            user_utterance="",
            config_snapshot=outside_snapshot(),
        )
        result = await gateway.execute(name, arguments, request)
        if getattr(result, "error", None) != VOICE_CONFIRM_SENTINEL:
            return result
        consent = spoken_consent.mint(name, arguments, trace_id=request.trace_id)
        log.info("jarvis MCP: %s deferred until the user confirms it", name)
        body = {
            "needs_confirmation": True,
            "executed": False,
            "confirm_token": consent.token,
            "expires_in_s": int(spoken_consent.CONSENT_TTL_S),
            "question": _consent_question(name, arguments, getattr(result, "output", None)),
            "next_step": (
                "Nothing was done. Ask the user this question in their language and "
                "end your turn. When they answer, call this tool again with exactly "
                "the same arguments plus confirm_token. Jarvis itself checks that "
                "the user said yes."
            ),
        }
        return _GateAnswer(json.dumps(body, ensure_ascii=False), False)

    verdict = spoken_consent.consent_for(str(token), tool_name=name, args=arguments)
    deferred = verdict.request
    if verdict.status == "granted" and deferred is not None:
        log.info("jarvis MCP: %s confirmed by the user", name)
        resumed = SupervisorToolRequest(
            trace_id=deferred.trace_id,
            origin=OUTSIDE_ORIGIN,
            user_utterance="",
            config_snapshot=outside_snapshot(),
        )
        return await gateway.execute_confirmed(deferred.trace_id, resumed)
    if verdict.status == "awaiting":
        return _refusal(
            "awaiting_user",
            "Not done: the user has not answered since the question. Ask them, end "
            "your turn, and retry with the same confirm_token after they reply.",
        )
    if verdict.status == "unclear":
        return _refusal(
            "unclear",
            "Not done: the user's answer was not a clear yes or no. Ask again and "
            "retry with the same confirm_token after they reply.",
        )
    if deferred is not None:
        reason = {
            "denied": "user_declined",
            "expired": "consent_expired",
            "mismatch": "consent_mismatch",
        }.get(verdict.status, "consent_refused")
        await _cancel_deferred(gateway, deferred.trace_id, reason)
    messages = {
        "denied": "Not done: the user did not agree. Do not retry unless they ask again.",
        "expired": (
            "Not done: the confirmation expired. Call again without confirm_token "
            "to ask the user anew."
        ),
        "mismatch": (
            "Not done: the tool or arguments differ from what the user was asked "
            "about. Call again without confirm_token to ask the user anew."
        ),
    }
    return _refusal(
        verdict.status,
        messages.get(
            verdict.status,
            "Not done: this confirm_token is unknown or already used. Call again "
            "without confirm_token to ask the user anew.",
        ),
    )


def _brain_map_offered(taken: set[str]) -> list[Any]:
    """The read-only brain map tools (``jarvis/memory/brain_map/mcp_tools.py``).

    Answered in-process, not through the gateway: they only read the derived
    project index — no side effect, no model call, nothing a risk tier or an
    approval could apply to. An ordinary chat session and Hermes (an outside
    caller with no session, the brain that answers the person) get them; a
    society seat gets its roster-granted tool set, and a task grant names its
    tools. A gateway tool of the same name always wins.
    """
    from jarvis.core.task_agent import scope_for

    session_id = CHAT_SESSION_REF.get()
    if session_id and session_id.startswith(("task-", "society:")):
        return []
    if session_id and scope_for(session_id) is not None:
        return []
    from jarvis.memory.brain_map.mcp_tools import TOOLS

    return [spec for spec in TOOLS if spec.name not in taken]


def _media_offered(taken: set[str]) -> list[Any]:
    """The read-only media tools (``jarvis/multimodal/tools.py``).

    Answered in-process like the brain map: each call reads one local file and
    asks a model on this computer (loopback only) about it, so it changes
    nothing and sends nothing off the machine; risk tier safe, with folder,
    size and duration limits instead of an approval. Offered to the same
    callers as the brain map: an ordinary chat session and Hermes. A society
    seat or a task grant gets only its granted tools. A gateway tool of the
    same name always wins.
    """
    from jarvis.core.task_agent import scope_for

    session_id = CHAT_SESSION_REF.get()
    if session_id and session_id.startswith(("task-", "society:")):
        return []
    if session_id and scope_for(session_id) is not None:
        return []
    from jarvis.multimodal.tools import TOOLS

    return [spec for spec in TOOLS if spec.name not in taken]


def build_server() -> Any:
    """A low-level MCP server bound to the live tool gateway.

    Built per process, not per session: the handlers read the gateway on every
    call, so a tool that connects later (an MCP source, a marketplace plugin, a
    CLI that finished bootstrapping) shows up without a rebuild.
    """
    import mcp.types as types
    from mcp.server.lowlevel import Server

    server: Any = Server("jarvis")

    @server.list_tools()  # type: ignore[misc, no-untyped-call]
    async def _list_tools() -> list[Any]:
        tools: list[Any] = []
        outside = CHAT_SESSION_REF.get() is None
        for wire, entry in (await _session_wire_catalog()).items():
            schema = entry.input_schema
            if not isinstance(schema, dict) or not schema:
                schema = {"type": "object", "properties": {}}
            if outside and _may_ask(entry):
                schema = _with_confirm_token(schema)
            tools.append(
                types.Tool(
                    name=wire,
                    description=(
                        f"[{entry.name}] {entry.description or entry.name}"
                        if wire != entry.name
                        else str(entry.description or entry.name)
                    ),
                    inputSchema=schema,
                    annotations=types.ToolAnnotations(
                        readOnlyHint=entry.risk_tier == "safe" and not entry.is_action_tool,
                    ),
                )
            )
        for spec in _brain_map_offered({tool.name for tool in tools}):
            tools.append(
                types.Tool(
                    name=spec.name,
                    description=spec.description,
                    inputSchema=spec.input_schema,
                    annotations=types.ToolAnnotations(readOnlyHint=True),
                )
            )
        for spec in _media_offered({tool.name for tool in tools}):
            tools.append(
                types.Tool(
                    name=spec.name,
                    description=spec.description,
                    inputSchema=spec.input_schema,
                    annotations=types.ToolAnnotations(
                        readOnlyHint=True, destructiveHint=False, openWorldHint=False
                    ),
                )
            )
        return tools

    @server.call_tool()  # type: ignore[misc, no-untyped-call]
    async def _call_tool(name: str, arguments: dict[str, Any] | None) -> Any:
        from jarvis.core.protocols import SupervisorToolRequest, current_chat_turn

        def failed(message: str) -> Any:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=message)], isError=True
            )

        from jarvis.memory.brain_map.mcp_tools import TOOL_NAMES as brain_map_names

        if name in brain_map_names:
            try:
                taken = set((await _session_wire_catalog()).keys())
                if any(spec.name == name for spec in _brain_map_offered(taken)):
                    from jarvis.memory.brain_map.mcp_tools import call as brain_map_call

                    text, is_error = await brain_map_call(name, arguments)
                    return types.CallToolResult(
                        content=[types.TextContent(type="text", text=text)], isError=is_error
                    )
            except Exception as exc:  # noqa: BLE001 — a read failure is data for the model, not a crash
                log.warning("jarvis MCP: brain map tool %s raised", name, exc_info=True)
                return failed(f"Tool failed: {type(exc).__name__}")
        from jarvis.multimodal.tools import TOOL_NAMES as media_names

        if name in media_names:
            try:
                taken = set((await _session_wire_catalog()).keys())
                if any(spec.name == name for spec in _media_offered(taken)):
                    from jarvis.multimodal.tools import call as media_call

                    text, is_error = await media_call(name, arguments)
                    return types.CallToolResult(
                        content=[types.TextContent(type="text", text=text)], isError=is_error
                    )
            except Exception as exc:  # noqa: BLE001 — a media failure is data for the model, not a crash
                log.warning("jarvis MCP: media tool %s raised", name, exc_info=True)
                return failed(f"Tool failed: {type(exc).__name__}")
        gateway = _gateway()
        if gateway is None:
            return failed("Jarvis is still starting up.")
        if name in _WITHHELD:
            return failed(
                f"{name} is not available from the chat — you ARE the worker. Do the work yourself."
            )
        try:
            from jarvis.agent_chat.tool_context import required_turn_id, restore_turn

            entry = (await _session_wire_catalog()).get(name)
            if entry is None:
                return failed("Tool is no longer available in this chat.")
            name = str(entry.name)
            outside = CHAT_SESSION_REF.get() is None
            if outside:
                outcome = await _outside_call(gateway, name, dict(arguments or {}))
                if isinstance(outcome, _GateAnswer):
                    return types.CallToolResult(
                        content=[types.TextContent(type="text", text=outcome.text)],
                        isError=outcome.is_error,
                    )
                result = outcome
            else:
                with restore_turn(CHAT_SESSION_REF.get()):
                    turn = current_chat_turn.get()
                    if required_turn_id.get() is not None and turn is None:
                        return types.CallToolResult(
                            content=[types.TextContent(
                                type="text", text="The requesting chat turn has ended."
                            )],
                            isError=True,
                        )
                    from jarvis.core.task_agent import scope_for

                    task_scope = scope_for(CHAT_SESSION_REF.get())
                    if (CHAT_SESSION_REF.get() or "").startswith("task-") and task_scope is None:
                        return failed("The task grant has expired.")
                    if task_scope is not None and name not in task_scope.names:
                        return failed("Tool outside the task grant.")
                    trace_id = task_scope.trace_id if task_scope else uuid4()
                    if name == "society_browser" and turn is not None and task_scope is None:
                        try:
                            trace_id = UUID(str(turn.trace_id))
                        except ValueError:
                            # Older callers can omit the UUID, but the session/turn
                            # identity still keeps denial effective across retries.
                            trace_id = uuid5(
                                NAMESPACE_URL, f"jarvis:{turn.session_id}:{turn.turn_id}"
                            )
                    request = SupervisorToolRequest(
                        trace_id=trace_id,
                        origin=CHAT_ORIGIN,
                        user_utterance=turn.user_text if turn else "",
                        config_snapshot=approval_snapshot(),
                    )
                    result = await gateway.execute(name, dict(arguments or {}), request)
        except Exception as exc:  # noqa: BLE001 — a tool failure is data for the model, not a crash
            log.warning("jarvis MCP: %s raised", name, exc_info=True)
            return failed(f"Tool failed: {type(exc).__name__}: {exc}")
        render = _render_outside if outside else _render
        if name == "society_browser":
            success = bool(getattr(result, "success", False))
            text = render(result)
            if not success:
                # A partial browser run may already have submitted a form.
                # Preserve its observed outcome so a planner can inspect the
                # current page instead of blindly repeating a side effect.
                text = json.dumps(
                    {
                        "ok": False,
                        "error": getattr(result, "error", None),
                        "result": getattr(result, "output", None),
                        "retry": "Inspect current state before repeating any action.",
                    },
                    ensure_ascii=False,
                    default=str,
                )
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=text)], isError=not success
            )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=render(result))],
            isError=not bool(getattr(result, "success", False)),
        )

    return server


__all__ = [
    "CHAT_APPROVAL_TIMEOUT_S",
    "CHAT_ORIGIN",
    "CHAT_SESSION_REF",
    "CONFIRM_TOKEN_ARG",
    "OUTSIDE_ORIGIN",
    "approval_snapshot",
    "build_server",
    "offered_tools",
    "outside_snapshot",
]
