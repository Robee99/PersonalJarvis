"""Round trip between a router tool and the app's own window.

The ``app_ui_*`` tools and ``navigate``'s ``then`` click act on the controls the
user sees in the Jarvis window: read them, click one, type into a field,
scroll. The window is the only place that knows what is on screen, so the
request travels there as an :class:`~jarvis.core.events.AppUiRequest` on the
bus (the WebSocket forwarder streams every bus event to the frontend), and the
main window posts its outcome back to ``POST /api/app-ui/result``
(``jarvis/ui/web/app_ui_routes.py``), which calls :func:`deliver_result`.

Lives in ``jarvis.core`` because both ends import it: the tools under
``jarvis/plugins/tool`` and the web route. It holds no state beyond the futures
of requests still in flight, keyed by a random ``request_id``.

No answer within the timeout means no main window is open (a detached solo
window ignores these requests on purpose), and the caller gets that as a plain
error instead of a hang.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from jarvis.core.events import AppUiRequest

log = logging.getLogger(__name__)

#: How long a request waits for the window. A click by label may itself wait up
#: to ``wait_ms`` for the control to render, so the two add up.
DEFAULT_TIMEOUT_S = 8.0

ACTIONS = frozenset({"snapshot", "click", "type", "scroll"})

NO_WINDOW_ERROR = (
    "The Jarvis app window did not answer, so nothing on screen was read or "
    "changed. The main Jarvis window is probably not open."
)

_pending: dict[str, asyncio.Future[dict[str, Any]]] = {}


@dataclass(frozen=True)
class AppUiReply:
    """What the window reported for one request."""

    ok: bool
    result: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    #: True when no window answered at all (as opposed to a refused action).
    no_window: bool = False


class AppUiBridge:
    """Publish an :class:`AppUiRequest` and wait for the window's answer."""

    def __init__(
        self,
        bus: Any,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        source_layer: str = "brain.tool.app_ui",
    ) -> None:
        self._bus = bus
        self._timeout_s = timeout_s
        self._source_layer = source_layer

    async def request(
        self,
        action: str,
        *,
        ref: str = "",
        label: str = "",
        text: str = "",
        submit: bool = False,
        direction: str = "",
        wait_ms: int = 0,
        timeout_s: float | None = None,
    ) -> AppUiReply:
        if action not in ACTIONS:
            return AppUiReply(ok=False, error=f"Unknown app UI action {action!r}.")
        if self._bus is None:
            return AppUiReply(ok=False, error=NO_WINDOW_ERROR, no_window=True)
        request_id = uuid4().hex
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        _pending[request_id] = future
        try:
            await self._bus.publish(
                AppUiRequest(
                    request_id=request_id,
                    action=action,
                    ref=ref,
                    label=label,
                    text=text,
                    submit=submit,
                    direction=direction,
                    wait_ms=max(0, int(wait_ms)),
                    source_layer=self._source_layer,
                )
            )
            payload = await asyncio.wait_for(
                future, timeout=self._timeout_s if timeout_s is None else timeout_s
            )
        except TimeoutError:
            log.info("app UI %s: no window answered request %s", action, request_id)
            return AppUiReply(ok=False, error=NO_WINDOW_ERROR, no_window=True)
        finally:
            _pending.pop(request_id, None)
        result = payload.get("result")
        return AppUiReply(
            ok=bool(payload.get("ok")),
            result=result if isinstance(result, dict) else {},
            error=str(payload.get("error") or ""),
        )


def _resolve(future: asyncio.Future[dict[str, Any]], payload: dict[str, Any]) -> None:
    if not future.done():
        future.set_result(payload)


def deliver_result(request_id: str, payload: dict[str, Any]) -> bool:
    """Hand the window's answer to the waiting request.

    Returns False when nothing waits under ``request_id`` any more: the request
    timed out, or another window already answered it (the first answer wins).
    """
    future = _pending.get(request_id)
    if future is None or future.done():
        return False
    loop = future.get_loop()
    try:
        running = asyncio.get_running_loop()
    except RuntimeError:  # A worker thread has no running loop; schedule on the future's owner.
        running = None
    if running is loop:
        _resolve(future, payload)
    else:
        loop.call_soon_threadsafe(_resolve, future, payload)
    return True


def pending_count() -> int:
    """Requests still waiting for a window (for tests and diagnostics)."""
    return len(_pending)


__all__ = [
    "ACTIONS",
    "DEFAULT_TIMEOUT_S",
    "NO_WINDOW_ERROR",
    "AppUiBridge",
    "AppUiReply",
    "deliver_result",
    "pending_count",
]
