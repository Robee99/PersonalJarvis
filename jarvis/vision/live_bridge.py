"""Request a fresh browser frame for a bound voice chat, without retaining pixels."""

from __future__ import annotations

import asyncio
import re
import threading
import time
from dataclasses import dataclass
from uuid import uuid4

from jarvis.brain.tool_switch import wants_camera
from jarvis.core.events import Event
from jarvis.core.protocols import ImageBlock
from jarvis.vision.live_inputs import LiveVisionFrame, decode_frames


@dataclass(frozen=True, slots=True)
class LiveVisionFrameRequested(Event):
    request_id: str = ""
    conversation_id: str = ""
    client_id: str = ""
    sources: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LiveVisionQuietRequested(Event):
    conversation_id: str = ""
    client_id: str = ""


class LiveVisionUnavailable(ValueError):
    """A requested live view ended, timed out, or failed validation."""


@dataclass(frozen=True)
class LiveVisualContext:
    images: tuple[ImageBlock, ...]
    sources: tuple[str, ...]
    provider: str
    model: str
    quiet: bool = False


class LiveVisionBridge:
    def __init__(self, timeout_s: float = 3.0):
        self.timeout_s = timeout_s
        self._clients: dict[tuple[str, str], dict] = {}
        self._pending: dict[str, dict] = {}
        self._lock = threading.RLock()
        self.voice_session = lambda: None

    @property
    def pending_count(self):
        with self._lock:
            return len(self._pending)

    def register(
        self, conversation, owner, sources, provider, model, sequence=None, quiet_for_ms=0
    ):
        now = time.monotonic()
        key = (conversation, owner)
        with self._lock:
            for expired in list(self._clients):
                if self._clients[expired]["expires"] <= now:
                    self._clients.pop(expired, None)
            old = self._clients.get(key)
            if sequence is not None and old is not None and sequence <= old["sequence"]:
                return
            if len(self._clients) >= 100 and key not in self._clients:
                raise ValueError("Too many visual clients")
            changed = old is None or old["sources"] != sources
            self._clients[key] = {
                "sources": sources,
                "provider": provider,
                "model": model,
                "expires": now + 12,
                "order": now if changed else old["order"],
                "sequence": sequence
                if sequence is not None
                else (old["sequence"] + 1 if old else 0),
                "quiet_until": now + min(180_000, max(0, quiet_for_ms)) / 1000,
            }
            for request in list(self._pending.values()):
                if request["key"] == key and (
                    not set(request["sources"]).issubset(sources)
                    or old is not None
                    and (old["provider"], old["model"]) != (provider, model)
                ):
                    self._resolve(
                        request["future"], error=LiveVisionUnavailable("Visual source changed")
                    )

    def is_quiet(self, conversation):
        with self._lock:
            return any(
                key[0] == conversation and value["quiet_until"] > time.monotonic()
                for key, value in self._clients.items()
            )

    def is_active(self, conversation, source):
        with self._lock:
            return any(
                key[0] == conversation
                and source in value["sources"]
                and value["expires"] > time.monotonic()
                for key, value in self._clients.items()
            )

    @staticmethod
    def _resolve(future, value=None, error=None):
        def finish():
            if not future.done():
                if error is not None:
                    future.set_exception(error)
                else:
                    future.set_result(value)

        future.get_loop().call_soon_threadsafe(finish)

    async def request(self, conversation, text, trace_id, bus):
        now = time.monotonic()
        with self._lock:
            for key in list(self._clients):
                if self._clients[key]["expires"] <= now:
                    self._clients.pop(key, None)
            choices = [
                (key, value)
                for key, value in self._clients.items()
                if key[0] == conversation and value["sources"]
            ]
            if not choices:
                # One person, one desktop: Eyes switched on in another chat
                # (or before the voice call opened its own) still sees for
                # this turn. Frames go to that client under its own chat.
                choices = [(key, value) for key, value in self._clients.items() if value["sources"]]
            if not choices:
                return None
            key, client = max(choices, key=lambda pair: pair[1]["order"])
            camera = wants_camera(text)
            screen = bool(re.search(r"\b(screen|desktop|window)\b", text, re.I))
            both = bool(re.search(r"\bboth\b", text, re.I)) or camera and screen
            sources = (
                ("screen", "webcam")
                if both
                else ("webcam",)
                if camera
                else ("screen",)
                if "screen" in client["sources"]
                else ()
            )
            quiet = bool(
                re.search(
                    r"\b(give me a minute|i need to do something important|"
                    r"pause (?:the )?(?:eyes|watch|nudges))\b",
                    text,
                    re.I,
                )
            )
            if quiet:
                sources = ()
                client["quiet_until"] = now + 180
            elif not sources:
                return None
            if camera and "webcam" not in client["sources"]:
                # Eyes off: the turn runs without live frames and the camera
                # tool takes one still instead (live 2026-10-26 "what am I
                # holding" otherwise ended at "that source is switched off").
                return None
            if any(source not in client["sources"] for source in sources):
                raise LiveVisionUnavailable("That visual source is switched off")
            if bus is None:
                raise LiveVisionUnavailable("The visual connection is unavailable")
            provider, model = client["provider"], client["model"]
            if not quiet:
                request_id = uuid4().hex
                future = asyncio.get_running_loop().create_future()
                self._pending[request_id] = {"key": key, "sources": sources, "future": future}
        if quiet:
            await bus.publish(
                LiveVisionQuietRequested(
                    conversation_id=key[0],
                    client_id=key[1],
                    trace_id=trace_id,
                    source_layer="vision.live",
                )
            )
            return LiveVisualContext((), (), provider, model, quiet=True)
        try:
            await bus.publish(
                LiveVisionFrameRequested(
                    request_id=request_id,
                    conversation_id=key[0],
                    client_id=key[1],
                    sources=sources,
                    trace_id=trace_id,
                    source_layer="vision.live",
                )
            )
            images = await asyncio.wait_for(future, self.timeout_s)
            return LiveVisualContext(images, sources, provider, model)
        except TimeoutError as exc:
            raise LiveVisionUnavailable(
                "No fresh live frame arrived; check the sharing controls"
            ) from exc
        finally:
            with self._lock:
                self._pending.pop(request_id, None)

    def deliver(self, request_id, conversation, owner, frames: list[LiveVisionFrame], error=""):
        with self._lock:
            request = self._pending.get(request_id)
            key = (conversation, owner)
            if request is None or request["key"] != key:
                raise ValueError("This live-frame request is not yours or has expired")
            client = self._clients.get(key)
            if client is None or client["expires"] <= time.monotonic():
                raise ValueError("The live visual source has ended")
            if error:
                self._resolve(request["future"], error=LiveVisionUnavailable(error[:200]))
                return
            if tuple(frame.source for frame in frames) != request["sources"]:
                raise ValueError("The requested visual sources do not match")
            self._resolve(request["future"], value=decode_frames(frames))


bridge = LiveVisionBridge()
