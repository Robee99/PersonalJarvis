"""Bounded, transient webcam/display inputs for the existing Jarvis turn path."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import time
from contextlib import contextmanager
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from jarvis.core.protocols import ImageBlock


class LiveVisionFrame(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    source: Literal["screen", "webcam"]
    mime: Literal["image/jpeg"] = "image/jpeg"
    data_b64: str = Field(min_length=1, max_length=1_000_000)
    captured_at_ms: float = Field(gt=0)


def decode_frames(frames: list[LiveVisionFrame]) -> tuple[ImageBlock, ...]:
    """Validate at intake; never write pixels to disk or chat events."""
    from PIL import Image, UnidentifiedImageError

    if len(frames) > 2 or len({f.source for f in frames}) != len(frames):
        raise ValueError("At most one fresh frame per source is allowed")
    now = time.time() * 1000
    images = []
    for frame in frames:
        if not -2_000 <= now - frame.captured_at_ms <= 10_000:
            raise ValueError("Live image expired; capture a fresh frame")
        try:
            raw = base64.b64decode(frame.data_b64, validate=True)
            if len(raw) > 750_000:
                raise ValueError("Live image is too large")
            with Image.open(io.BytesIO(raw)) as image:
                if image.format != "JPEG" or max(image.size) > 2048 or min(image.size) < 1:
                    raise ValueError("Live inputs require a bounded JPEG image")
                image.verify()
        except (binascii.Error, OSError, UnidentifiedImageError) as exc:
            raise ValueError("Invalid live JPEG image") from exc
        images.append(
            ImageBlock(
                mime=frame.mime,
                data_b64=frame.data_b64,
                source_hash=f"{frame.source}:{hashlib.sha256(raw).hexdigest()}",
            )
        )
    return tuple(images)


def require_local_vision(provider: str) -> None:
    """Automatic visual work cannot fall back to a metered provider."""
    from jarvis.brain.background_policy import keyless_local

    if not keyless_local(provider):
        raise ValueError("Live eyes require a local model selected in this chat")


@contextmanager
def live_image_scope(brain, trace_id, images):
    if images:
        brain.inject_images_for_turn(trace_id, images)
    try:
        yield
    finally:
        if images:
            brain.discard_images_for_turn(trace_id)
