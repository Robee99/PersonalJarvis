"""``POST /api/app-ui/result`` — the main window's answer to an ``AppUiRequest``.

The ``app_ui_*`` router tools publish an ``AppUiRequest`` on the bus; the main
window performs it on its own controls (``src/lib/appUi.ts``) and posts the
outcome here under the same ``request_id``. The route only hands the payload to
the waiting request (``jarvis.core.app_ui_bridge``); it changes nothing itself.

Same-origin UI route like the rest of ``/api/*``: the outer surface-security
middleware guards it, and an unknown or late ``request_id`` is simply not
accepted.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from jarvis.core.app_ui_bridge import deliver_result

router = APIRouter(prefix="/api/app-ui", tags=["app-ui"])


class AppUiResultBody(BaseModel):
    """The window's outcome for one in-app UI request."""

    request_id: str = Field(min_length=1, max_length=64)
    ok: bool
    result: dict[str, Any] | None = None
    error: str | None = Field(default=None, max_length=2000)


class AppUiResultAck(BaseModel):
    """Whether a request was still waiting for this answer."""

    accepted: bool


@router.post(
    "/result",
    response_model=AppUiResultAck,
    summary="Report the outcome of an in-app UI action to the waiting tool",
)
def report_app_ui_result(body: AppUiResultBody) -> AppUiResultAck:
    """Deliver a window's answer; ``accepted`` is False once nothing waits for it."""
    accepted = deliver_result(
        body.request_id,
        {"ok": body.ok, "result": body.result or {}, "error": body.error or ""},
    )
    return AppUiResultAck(accepted=accepted)


__all__ = ["router"]
