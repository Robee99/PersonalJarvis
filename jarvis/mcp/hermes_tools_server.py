"""Connected Jarvis apps for native Hermes, using per-call MCP consent.

The executor owns risk evaluation and parks consequential actions. MCP form
elicitation lets Hermes present that exact request on its native voice/chat
approval surface. Only the same RPC can resume the saved action, once; neither
a model argument nor a second public tool can manufacture an approval.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from uuid import uuid4

import anyio

from jarvis.core.protocols import SupervisorToolRequest, ToolResult
from jarvis.core.redact import redact_secrets, safe_preview
from jarvis.mcp import jarvis_tools_server as tools_server

log = logging.getLogger(__name__)

# Computer/browser/file control remains native to Hermes. These are Jarvis's
# existing account connections and memory, not a second agent or model router.
OWNED_TOOLS = frozenset(
    {
        "gmail",
        "google_calendar",
        "google_drive",
        "camera",
        "wiki-recall",
        "wiki-list",
        "wiki-page-read",
        "contact-lookup",
    }
)
_LOOP_SERVERS = frozenset({"jarvis", "jarvis-agents", "jarvis-hermes", "hermes"})
_SECRET_FIELDS = frozenset(
    {
        "access_token",
        "refresh_token",
        "auth_token",
        "authorization",
        "api_key",
        "client_secret",
        "password",
        "passwd",
        "secret",
        "private_key",
        "token",
        "bot_token",
        "api_server_key",
        "jarvis_control_key",
    }
)
CONSENT_TIMEOUT_S = 300.0


def _scrub(value: Any) -> Any:
    """Redact structured credentials as well as recognizable key shapes."""
    if isinstance(value, dict):
        return {
            str(key): "<redacted>"
            if str(key).lower().replace("-", "_") in _SECRET_FIELDS
            else _scrub(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_scrub(item) for item in value]
    return redact_secrets(value) if isinstance(value, str) else value


def _catalog() -> dict[str, Any]:
    # Connected marketplace/MCP adapters have portable server/tool names. They
    # retain their own live token provider and risk metadata behind the gateway.
    entries = []
    gateway = tools_server._gateway()
    if gateway is None:
        return {}
    try:
        catalog = gateway.catalog()
    except Exception as exc:  # noqa: BLE001 — metadata failure must not leak provider errors
        log.warning("Hermes MCP catalogue unavailable (%s)", type(exc).__name__)
        return {}
    for entry in catalog:
        name = str(entry.name)
        parts = name.split("/")
        if name in OWNED_TOOLS or (
            len(parts) == 2
            and parts[0].lower() not in _LOOP_SERVERS
            and parts[1] not in tools_server._WITHHELD
        ):
            entries.append(entry)
    return tools_server._wire_catalog(entries)


def _description(entry: Any) -> str:
    """Expose action-specific tiers without changing the executor's policy."""
    description = str(entry.description or entry.name)
    if tools_server._wire_name(str(entry.name)) != entry.name:
        description = f"[{entry.name}] {description}"
    hook = getattr(entry, "risk_tier_for_args", None)
    actions = (
        entry.input_schema.get("properties", {}).get("action", {}).get("enum", [])
        if isinstance(entry.input_schema, dict)
        else []
    )
    tiers = []
    if callable(hook) and isinstance(actions, list):
        for action in actions:
            try:
                tier = hook({"action": action})
            except Exception as exc:  # noqa: BLE001 — metadata is not an execution grant
                log.debug("Hermes MCP permission metadata unavailable (%s)", type(exc).__name__)
                continue
            if tier in {"safe", "monitor", "ask", "block"}:
                tiers.append(f"{action}: {tier}")
    permissions = "; ".join(tiers) or f"default: {entry.risk_tier}"
    return description + f"\nJarvis permissions ({permissions}); ask requires allow once."


def _message(entry: Any, arguments: dict[str, Any], result: Any) -> str:
    output = getattr(result, "output", None)
    impact = output.get("impact") if isinstance(output, dict) else None
    return safe_preview(
        f"Allow once: {entry.name}\n"
        + json.dumps(_scrub(impact or arguments), ensure_ascii=False, default=str),
        max_chars=2000,
    )


def build_server() -> Any:
    """A dedicated, stateful Streamable HTTP surface; no connection-wide grants."""
    import mcp.types as types
    from mcp.server.lowlevel import Server

    server: Any = Server("jarvis-hermes")

    def reply(
        result: Any, request: SupervisorToolRequest | None = None, tool_name: str = ""
    ) -> Any:
        safe = ToolResult(
            success=bool(getattr(result, "success", False)),
            output=_scrub(getattr(result, "output", None)),
            error=safe_preview(getattr(result, "error", None)),
        )
        return types.CallToolResult(
            content=[types.TextContent(type="text", text=tools_server._render(safe))],
            isError=not safe.success,
            _meta={
                "io.personaljarvis/trace_id": str(request.trace_id),
                "io.personaljarvis/tool": tool_name,
            }
            if request is not None
            else None,
        )

    def failed(message: str) -> Any:
        return reply(ToolResult(success=False, output=None, error=message))

    @server.list_tools()  # type: ignore[misc, no-untyped-call]
    async def list_tools() -> list[Any]:
        return [
            types.Tool(
                name=wire,
                description=_description(entry),
                inputSchema=entry.input_schema or {"type": "object", "properties": {}},
                annotations=types.ToolAnnotations(
                    readOnlyHint=entry.risk_tier == "safe" and not entry.is_action_tool,
                ),
            )
            for wire, entry in _catalog().items()
        ]

    @server.call_tool()  # type: ignore[misc, no-untyped-call]
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> Any:
        from jarvis.control.cancel import CancelToken
        from jarvis.safety.tool_executor import VOICE_CONFIRM_SENTINEL

        gateway = tools_server._gateway()
        entry = _catalog().get(name)
        if gateway is None:
            return failed("Jarvis is still starting up.")
        if entry is None:
            return failed("The connected tool is no longer available. Refresh the catalogue.")
        cancel = CancelToken()
        request = SupervisorToolRequest(
            trace_id=uuid4(),
            origin="hermes-mcp",
            user_utterance="",
            config_snapshot={"approval_surface": "conversational"},
            cancel_token=cancel,
        )
        # The receipt is private to this handler. It is never returned as a
        # callable tool, model argument, prompt token, or connection-wide grant.
        pending = False
        reason = "hermes_mcp_approval_unavailable"
        try:
            result = await gateway.execute(str(entry.name), dict(arguments or {}), request)
            if getattr(result, "error", None) != VOICE_CONFIRM_SENTINEL:
                return reply(result, request, str(entry.name))
            pending = True
            context = server.request_context
            session = context.session
            if not session.check_client_capability(types.ClientCapabilities(elicitation={})):
                return failed("This client cannot present approval. The action did not run.")
            async with asyncio.timeout(CONSENT_TIMEOUT_S):
                decision = await session.elicit_form(
                    message=_message(entry, dict(arguments or {}), result),
                    requestedSchema={"type": "object", "properties": {}},
                    related_request_id=context.request_id,
                )
            if decision.action != "accept":
                reason = (
                    "hermes_mcp_denied" if decision.action == "decline" else "hermes_mcp_cancelled"
                )
                return failed(
                    "Approval denied; the action did not run."
                    if decision.action == "decline"
                    else "Approval cancelled; the action did not run."
                )
            # A disconnect/Stop cancels the owning RPC. Check cancellation before
            # consuming its private receipt; the executor races it during work too.
            await asyncio.sleep(0)
            if cancel.is_cancelled():
                return failed("The request was cancelled; the action did not run.")
            if _catalog().get(name) != entry:
                return failed("The connected tool changed during approval. The action did not run.")
            return reply(
                await gateway.execute_confirmed(request.trace_id, request), request, str(entry.name)
            )
        except asyncio.CancelledError:
            cancel.cancel("hermes_mcp_cancelled")
            reason = "hermes_mcp_cancelled"
            raise
        except TimeoutError:
            reason = "hermes_mcp_approval_expired"
            return failed("Approval expired; the action did not run.")
        except Exception as exc:  # noqa: BLE001 — provider bodies must not reach logs or prompts
            log.warning("Hermes MCP call failed (%s)", type(exc).__name__)
            return failed("The connected tool could not complete. Check its connection in Jarvis.")
        finally:
            if pending:
                # MCP uses AnyIO cancellation scopes. Cleanup must survive an
                # already-cancelled scope, otherwise a stale action remains resumable.
                with anyio.CancelScope(shield=True):
                    await gateway.cancel_pending(request.trace_id, reason=reason)

    return server


__all__ = ["OWNED_TOOLS", "build_server"]
