"""Bounded task delegation to a Paperclip agent (the escalation tier).

When ``[brain.route_policy.escalation]`` is enabled with ``via = "paperclip"``,
this is the only way the Brain hands work to the configured delegate agent (for
example a Claude agent): it never opens a model client of its own. One
delegation is one Paperclip issue, created through Paperclip's REST API:

* ``POST /api/companies/{company}/issues`` with ``assigneeAgentId`` and an
  ``idempotencyKey`` derived from the turn, so a retry replays the same issue
  instead of creating a second one;
* ``GET /api/issues/{id}`` to poll its status, and
  ``GET /api/issues/{id}/comments`` for the agent's reply once it is done;
* ``PATCH /api/issues/{id}`` with ``status = "cancelled"`` when the turn is
  cancelled (barge-in, hang-up) or the deadline passes, so the agent stops
  working on an obsolete request.

The request carries only the redacted task text, a bounded redacted context
and the expected output, plus the turn id for correlation. Every outcome is a
typed ``DelegationResult``; nothing here raises to the caller except
``asyncio.CancelledError``, which is re-raised after the issue is cancelled.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from jarvis.brain.route_policy import RouteFailure
from jarvis.core.redact import redact_secrets

log = logging.getLogger("jarvis.brain.paperclip_delegation")

_DONE_STATUSES = frozenset({"done", "in_review"})
_FAILED_STATUSES = frozenset({"cancelled", "blocked"})
_TITLE_CHARS = 120


class DelegationStatus(StrEnum):
    COMPLETED = "completed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


_FAILURE_FOR_STATUS = {
    DelegationStatus.TIMEOUT: RouteFailure.TIMEOUT,
    DelegationStatus.CANCELLED: RouteFailure.CANCELLED,
    DelegationStatus.UNAVAILABLE: RouteFailure.UNAVAILABLE,
    DelegationStatus.FAILED: RouteFailure.DELEGATION_FAILED,
}


@dataclass(frozen=True)
class DelegationRequest:
    turn_id: str
    task: str
    context: str = ""
    expected_output: str = "A concise answer that can be read aloud."


@dataclass(frozen=True)
class DelegationResult:
    status: DelegationStatus
    text: str = ""
    issue_id: str | None = None
    issue_identifier: str | None = None
    elapsed_s: float = 0.0
    detail: str = ""

    @property
    def failure(self) -> RouteFailure | None:
        return _FAILURE_FOR_STATUS.get(self.status)


class PaperclipTransport(Protocol):
    async def request(
        self, method: str, path: str, json: dict[str, Any] | None = None
    ) -> tuple[int, Any]: ...


class HttpPaperclipTransport:
    """``httpx`` transport for a Paperclip instance and a board key."""

    def __init__(self, base_url: str, api_key: str, *, timeout_s: float = 15.0) -> None:
        self._base = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._timeout_s = timeout_s

    async def request(
        self, method: str, path: str, json: dict[str, Any] | None = None
    ) -> tuple[int, Any]:
        import httpx

        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            resp = await client.request(
                method, f"{self._base}{path}", json=json, headers=self._headers
            )
        try:
            body = resp.json()
        except ValueError:  # a non-JSON error page is still a usable body
            body = resp.text
        return resp.status_code, body


class _Unavailable(Exception):
    """Paperclip could not be reached or refused the request."""


def build_issue_payload(
    request: DelegationRequest, *, agent_id: str, max_context_chars: int
) -> dict[str, Any]:
    """The issue body: redacted, bounded, correlated to the turn."""
    task = redact_secrets(request.task.strip())
    context = redact_secrets(request.context.strip())[:max_context_chars]
    parts = [task]
    if context:
        parts.append(f"Context (trimmed):\n{context}")
    parts.append(f"Expected output: {request.expected_output}")
    parts.append(
        "Reply with a comment on this issue, then mark it done. "
        f"Jarvis turn: {request.turn_id}"
    )
    title = task.splitlines()[0] if task else "Jarvis request"
    return {
        "title": title[:_TITLE_CHARS] or "Jarvis request",
        "description": "\n\n".join(parts),
        "status": "todo",
        "assigneeAgentId": agent_id,
        "idempotencyKey": f"jarvis-turn-{request.turn_id}",
    }


class PaperclipDelegate:
    """Create, follow and cancel one Paperclip issue per delegated turn."""

    def __init__(
        self,
        transport: PaperclipTransport,
        *,
        agent_name: str,
        deadline_s: float,
        poll_interval_s: float,
        max_context_chars: int,
        company_id: str | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        on_progress: Callable[[], None] | None = None,
    ) -> None:
        self._transport = transport
        self._agent_name = agent_name.strip()
        self._deadline_s = deadline_s
        self._poll_s = poll_interval_s
        self._max_context = max_context_chars
        self._company_id = company_id
        self._clock = clock
        self._sleep = sleep
        self._on_progress = on_progress

    async def _call(self, method: str, path: str, json: dict[str, Any] | None = None) -> Any:
        try:
            status, body = await self._transport.request(method, path, json)
        except Exception as exc:  # noqa: BLE001 — any transport error is "unavailable"
            raise _Unavailable(f"{type(exc).__name__}: {exc}") from exc
        if status >= 400:
            raise _Unavailable(f"HTTP {status} on {method} {path}")
        return body

    async def _resolve_ids(self) -> tuple[str, str]:
        company = self._company_id
        if not company:
            companies = await self._call("GET", "/api/companies")
            if not isinstance(companies, list) or not companies:
                raise _Unavailable("no Paperclip company")
            company = str(companies[0].get("id") or "")
        agents = await self._call("GET", f"/api/companies/{company}/agents")
        wanted = self._agent_name.casefold()
        for agent in agents if isinstance(agents, list) else []:
            names = {str(agent.get(k) or "").casefold() for k in ("name", "urlKey")}
            if wanted and wanted in names:
                return company, str(agent["id"])
        raise _Unavailable(f"no Paperclip agent named {self._agent_name!r}")

    async def _agent_reply(self, issue_id: str) -> str:
        comments = await self._call("GET", f"/api/issues/{issue_id}/comments")
        replies = [
            str(c.get("body") or "").strip()
            for c in (comments if isinstance(comments, list) else [])
            if c.get("authorAgentId") or c.get("authorType") == "agent"
        ]
        replies = [r for r in replies if r]
        return replies[-1] if replies else ""

    async def _cancel(self, issue_id: str, reason: str) -> None:
        try:
            await self._call(
                "PATCH",
                f"/api/issues/{issue_id}",
                {"status": "cancelled", "comment": f"Jarvis stopped this request: {reason}."},
            )
        except _Unavailable as exc:
            log.warning("Paperclip issue %s could not be cancelled (%s)", issue_id, exc)

    async def delegate(
        self, request: DelegationRequest, *, cancel_token: Any = None
    ) -> DelegationResult:
        start = self._clock()

        def _result(status: DelegationStatus, **kw: Any) -> DelegationResult:
            return DelegationResult(status, elapsed_s=self._clock() - start, **kw)

        try:
            company, agent_id = await self._resolve_ids()
            payload = build_issue_payload(
                request, agent_id=agent_id, max_context_chars=self._max_context
            )
            issue = await self._call("POST", f"/api/companies/{company}/issues", payload)
        except _Unavailable as exc:
            log.warning("Paperclip delegation unavailable: %s", exc)
            return _result(DelegationStatus.UNAVAILABLE, detail=str(exc))
        issue_id = str(issue.get("id") or "")
        identifier = issue.get("identifier")
        log.info("Delegated turn %s to Paperclip issue %s", request.turn_id, identifier or issue_id)

        try:
            while True:
                if cancel_token is not None and cancel_token.is_cancelled():
                    await self._cancel(issue_id, "the turn was cancelled")
                    return _result(DelegationStatus.CANCELLED, issue_id=issue_id,
                                   issue_identifier=identifier)
                if self._clock() - start >= self._deadline_s:
                    await self._cancel(issue_id, "the deadline passed")
                    return _result(DelegationStatus.TIMEOUT, issue_id=issue_id,
                                   issue_identifier=identifier)
                try:
                    state = await self._call("GET", f"/api/issues/{issue_id}")
                    status = str((state or {}).get("status") or "")
                    if status in _DONE_STATUSES:
                        text = await self._agent_reply(issue_id)
                        if not text:
                            return _result(DelegationStatus.FAILED, issue_id=issue_id,
                                           issue_identifier=identifier,
                                           detail="finished without a reply")
                        return _result(DelegationStatus.COMPLETED, text=text,
                                       issue_id=issue_id, issue_identifier=identifier)
                    if status in _FAILED_STATUSES:
                        return _result(DelegationStatus.FAILED, issue_id=issue_id,
                                       issue_identifier=identifier, detail=f"issue {status}")
                except _Unavailable as exc:
                    # One poll failing is not the end of the delegation; the
                    # deadline above still bounds how long we keep trying.
                    log.info("Paperclip poll failed for %s: %s", issue_id, exc)
                if self._on_progress is not None:
                    self._on_progress()
                await self._sleep(self._poll_s)
        except asyncio.CancelledError:
            await asyncio.shield(self._cancel(issue_id, "the turn was interrupted"))
            raise


def delegate_from_config(
    escalation: Any,
    *,
    token_loader: Callable[[], Any] | None = None,
    on_progress: Callable[[], None] | None = None,
) -> PaperclipDelegate | None:
    """Build the delegate from the saved Paperclip connection, or None.

    The address and board key come from the marketplace connection the user
    made (``plugin_paperclip`` in the token store), never from the model.
    """
    if not bool(getattr(escalation, "enabled", False)):
        return None
    if str(getattr(escalation, "via", "") or "") != "paperclip":
        return None
    if token_loader is None:
        from jarvis.marketplace.token_store import TokenStore

        def token_loader() -> Any:
            return TokenStore().load("paperclip")

    try:
        tokens = token_loader()
    except Exception as exc:  # noqa: BLE001 — a broken store means "not connected"
        log.warning("Paperclip connection could not be read: %s", exc)
        return None
    if tokens is None:
        return None
    base_url = str((getattr(tokens, "extra", None) or {}).get("instance_url") or "")
    if not base_url:
        return None
    return PaperclipDelegate(
        HttpPaperclipTransport(base_url, str(getattr(tokens, "access", "") or "")),
        agent_name=str(getattr(escalation, "agent", "") or ""),
        deadline_s=float(getattr(escalation, "deadline_s", 180.0)),
        poll_interval_s=float(getattr(escalation, "poll_interval_s", 3.0)),
        max_context_chars=int(getattr(escalation, "max_context_chars", 4000)),
        on_progress=on_progress,
    )
