"""Focus sessions over HTTP: the FOCUS button on the rail and the debug probes.

The session itself lives in :mod:`jarvis.focus.session`; voice reaches the
same one through the brain manager's focus reflex.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from jarvis.focus.session import DEFAULT_MINUTES, MAX_MINUTES, FocusSession, get_session

router = APIRouter(prefix="/api/focus", tags=["focus"])


def _session(request: Request) -> FocusSession:
    """The shared session, with speech bound to this app's bus."""
    from jarvis.brain.persona_loader import persona_style
    from jarvis.core.events import AnnouncementRequested

    session = get_session()
    bus = getattr(request.app.state, "bus", None)
    config = getattr(request.app.state, "config", None)

    async def say(text: str) -> None:
        await bus.publish(AnnouncementRequested(text=text, source_layer="focus", kind="info"))

    honorific = persona_style(config)[1] if config is not None else None
    session.bind(say if bus is not None else None, honorific)
    return session


class StartBody(BaseModel):
    minutes: int = Field(default=DEFAULT_MINUTES, ge=1, le=MAX_MINUTES)
    goal: str = Field(default="", max_length=120)
    silent: bool = False


class GoalBody(BaseModel):
    goal: str = Field(default="", max_length=120)


async def _answer(session: FocusSession, text: str, silent: bool = False) -> dict:
    if not silent:
        await session.speak(text)
    return {"text": text, **session.snapshot()}


@router.get("")
def state() -> dict:
    return get_session().snapshot()


@router.post("/start")
async def start(body: StartBody, request: Request) -> dict:
    session = _session(request)
    return await _answer(session, session.start(body.minutes, body.goal), body.silent)


@router.post("/lock")
async def lock(request: Request) -> dict:
    session = _session(request)
    return await _answer(session, session.lock_here())


@router.post("/goal")
def goal(body: GoalBody, request: Request) -> dict:
    session = _session(request)
    return {"text": session.set_goal(body.goal), **session.snapshot()}


@router.post("/stop")
async def stop(request: Request) -> dict:
    session = _session(request)
    return await _answer(session, session.stop())


@router.get("/diag")
def diag() -> dict:
    """Booleans and statuses only, no window titles (?focusdebug=1 reads this)."""
    return get_session().diag()
