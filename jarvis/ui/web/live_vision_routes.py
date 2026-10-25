"""Local landmark assets and announcements through Jarvis's existing speech bus."""

import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from jarvis.vision.live_inputs import LiveVisionFrame, require_local_vision

router = APIRouter(prefix="/api/live-vision", tags=["live-vision"])
TRACKING_FILES = ("face_landmarker.task", "pose_landmarker_lite.task")
NOTICES = {
    "ears-off": "My ears are off — tap the microphone button when you want to talk.",
    "head-down": "A brief reminder to lift your head; your neck has filed a polite complaint.",
    "slouched": "Perhaps a small posture reset before the chair wins this negotiation.",
    "absent": "You seem to have stepped away. I will leave the desk in your capable absence.",
}


def asset_root() -> Path:
    from jarvis.core.paths import user_data_dir

    return user_data_dir() / "holo" / "vendor"


@router.get("/capabilities")
def capabilities() -> dict:
    root = asset_root()
    required = (*TRACKING_FILES, "vision_bundle.mjs", "wasm/vision_wasm_internal.wasm")
    missing = [name for name in required if not (root / name).is_file()]
    return {
        "tracking_ready": not missing,
        "missing": missing,
        "detail": "Local face/pose assets are unavailable; webcam photos still work."
        if missing
        else "Local tracking ready.",
    }


@router.get("/tracking/{name}", include_in_schema=False)
def tracking_asset(name: str):
    if name not in TRACKING_FILES or not (asset_root() / name).is_file():
        raise HTTPException(404, "Local landmark model is not installed")
    return FileResponse(asset_root() / name, media_type="application/octet-stream")


class RegistrationBody(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=100)
    client_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    sources: list[Literal["screen", "webcam"]] = Field(default_factory=list, max_length=2)
    sequence: int = Field(ge=0)
    quiet_for_ms: float = Field(default=0, ge=0, le=180_000, allow_inf_nan=False)


def _screen_policy(sources):
    if "screen" in sources:
        from jarvis.core.config import load_config

        policy = load_config().screen_context
        if not policy.enabled or policy.denylist or policy.sensitive_patterns:
            raise ValueError("Screen privacy settings prevent live sharing; use an appshot")


@router.post("/register")
def register(body: RegistrationBody, request: Request):
    from jarvis.ui.web.agent_chat_routes import _service
    from jarvis.vision.live_bridge import bridge

    service = _service(request)
    session = service.store.get_session(body.conversation_id)
    try:
        if body.sources:
            if session is None or session.surface != "jarvis":
                raise ValueError("No matching Jarvis conversation")
            require_local_vision(session.provider)
            _screen_policy(body.sources)
        bridge.register(
            body.conversation_id,
            body.client_id,
            tuple(body.sources),
            session.provider if session else "",
            session.model if session else "",
            sequence=body.sequence,
            quiet_for_ms=body.quiet_for_ms,
        )
        bridge.voice_session = lambda: service.voice_chat_id
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"registered": bool(body.sources)}


class FrameReply(BaseModel):
    request_id: str = Field(min_length=1, max_length=100)
    conversation_id: str = Field(min_length=1, max_length=100)
    client_id: str = Field(min_length=8, max_length=80)
    frames: list[LiveVisionFrame] = Field(default_factory=list, max_length=2)
    error: str = Field(default="", max_length=200)


@router.post("/frames")
def receive_frames(body: FrameReply):
    from jarvis.vision.live_bridge import bridge

    try:
        _screen_policy([frame.source for frame in body.frames])
        bridge.deliver(
            body.request_id, body.conversation_id, body.client_id, body.frames, body.error
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"received": True}


class NoticeBody(BaseModel):
    kind: Literal["ears-off", "head-down", "slouched", "absent"]
    silent: bool = False


class WatchBadgeBody(BaseModel):
    client_id: str = Field(min_length=8, max_length=80, pattern=r"^[A-Za-z0-9_-]+$")
    visible: bool
    dark: bool = False


_watch_leases: dict[str, float] = {}


@router.post("/watch-indicator")
async def watch_indicator(body: WatchBadgeBody, request: Request) -> dict:
    from jarvis.cu.indicator.controller import wire_cu_indicator

    bus = getattr(request.app.state, "bus", None)
    now = time.monotonic()
    for owner in list(_watch_leases):
        if _watch_leases[owner] <= now:
            _watch_leases.pop(owner, None)
    if body.visible:
        if len(_watch_leases) >= 100 and body.client_id not in _watch_leases:
            raise HTTPException(429, "Too many active visual windows")
        _watch_leases[body.client_id] = now + 12
    else:
        _watch_leases.pop(body.client_id, None)
    controller = wire_cu_indicator(bus) if bus is not None else None
    visible = (
        await controller.watch_badge(visible=bool(_watch_leases), dark=body.dark)
        if controller
        else False
    )
    return {
        "available": visible,
        "detail": "" if visible else "Desktop watch indicator is unavailable",
    }


@router.post("/notice")
async def notice(body: NoticeBody, request: Request) -> dict:
    from jarvis.core.events import AnnouncementRequested

    text = NOTICES[body.kind]
    bus = getattr(request.app.state, "bus", None)
    if bus is not None and not body.silent:
        await bus.publish(AnnouncementRequested(text=text, source_layer="vision.eyes", kind="info"))
    return {"text": text, "queued": bus is not None and not body.silent}
