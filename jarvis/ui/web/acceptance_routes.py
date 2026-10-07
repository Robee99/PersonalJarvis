"""Acceptance hooks: drive the running app the way a person does, and time it.

``POST /api/acceptance/spoken-turn`` hands a sentence to the same brain and the
same call the speech pipeline makes after speech recognition
(``BrainManager.generate_stream`` with voice confirmation on), and reports the
reply with the time to its first text and to its end. It is what
``jarvis system acceptance`` uses for the spoken half of a scenario; the typed
half goes through the front page's chat routes. Nothing is faked: the turn
runs on the live brain and may act, so the route is marked dangerous.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/acceptance", tags=["acceptance"])


class SpokenTurn(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)


@router.post("/spoken-turn", openapi_extra={"x-jarvis-dangerous": True})
async def spoken_turn(body: SpokenTurn, request: Request) -> dict[str, Any]:
    """One turn through the voice brain, as if the person had said ``text``."""
    from jarvis.core import runtime_refs

    brain = getattr(request.app.state, "brain", None) or runtime_refs.get_brain_manager()
    if brain is None or not hasattr(brain, "generate_stream"):
        raise HTTPException(status_code=503, detail="The brain is still starting.")
    started = time.monotonic()
    first: float | None = None
    parts: list[str] = []
    async for chunk in brain.generate_stream(
        body.text.strip(), use_history=True, allow_voice_confirm=True
    ):
        if chunk and first is None:
            first = time.monotonic()
        parts.append(chunk or "")
    done = time.monotonic()
    return {
        "reply": "".join(parts).strip(),
        "first_text_ms": round((first - started) * 1000) if first is not None else None,
        "total_ms": round((done - started) * 1000),
    }
