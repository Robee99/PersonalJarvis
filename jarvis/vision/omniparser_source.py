"""Optional local OmniParser perception adapter.

The adapter sends a screenshot to a loopback OmniParser service and translates
its normalized icon boxes into UI nodes. It never performs computer actions;
the existing approved controller remains the only action path. Set
``JARVIS_OMNIPARSER_URL=http://127.0.0.1:8000/parse/`` to enable it.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import logging
import os
import struct
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

from jarvis.core.protocols import CancelToken, UIANode

logger = logging.getLogger(__name__)

_MAX_SCREENSHOT_BYTES = 12 * 1024 * 1024
_MAX_PARSED_ICONS = 80
_MAX_LABEL_CHARS = 160


def validate_local_endpoint(endpoint: str) -> str:
    """Accept only plain HTTP endpoints on a numeric loopback address."""
    candidate = endpoint.strip()
    parsed = urlsplit(candidate)
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/parse"
    ):
        raise ValueError("OmniParser must use a loopback HTTP /parse/ endpoint")
    try:
        loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        port = parsed.port
    except ValueError:
        loopback = False
        port = None
    if not loopback or (port is not None and not 1 <= port <= 65535):
        raise ValueError("OmniParser endpoint must use a numeric loopback IP")
    return candidate


def png_dimensions(image: bytes) -> tuple[int, int] | None:
    """Read PNG dimensions without loading image libraries or decoding pixels."""
    if len(image) < 24 or image[:8] != b"\x89PNG\r\n\x1a\n" or image[12:16] != b"IHDR":
        return None
    width, height = struct.unpack(">II", image[16:24])
    return (width, height) if width > 0 and height > 0 else None


def nodes_from_response(
    payload: object,
    *,
    image_size: tuple[int, int],
    monitor_geom: tuple[int, int, int, int],
) -> tuple[UIANode, ...]:
    """Convert only normalized, detected icon regions into bounded UI nodes."""
    if not isinstance(payload, dict):
        return ()
    entries = payload.get("parsed_content_list")
    if not isinstance(entries, list):
        return ()
    image_width, image_height = image_size
    origin_x, origin_y, monitor_width, monitor_height = monitor_geom
    if min(image_width, image_height, monitor_width, monitor_height) <= 0:
        return ()

    result: list[UIANode] = []
    seen: set[tuple[int, int, int, int, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict) or str(entry.get("type", "")).casefold() != "icon":
            continue
        if entry.get("interactivity") is False:
            continue
        box = entry.get("bbox")
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            continue
        try:
            x1, y1, x2, y2 = (float(value) for value in box)
        except (TypeError, ValueError, OverflowError):
            continue
        if not all(0.0 <= value <= 1.0 for value in (x1, y1, x2, y2)):
            continue
        if x2 <= x1 or y2 <= y1:
            continue
        label = " ".join(str(entry.get("content", "")).split())[:_MAX_LABEL_CHARS]
        left = origin_x + round(x1 * monitor_width)
        top = origin_y + round(y1 * monitor_height)
        right = origin_x + round(x2 * monitor_width)
        bottom = origin_y + round(y2 * monitor_height)
        bounds = (left, top, right - left, bottom - top)
        if bounds[2] <= 0 or bounds[3] <= 0:
            continue
        key = (*bounds, label.casefold())
        if key in seen:
            continue
        seen.add(key)
        result.append(
            UIANode(
                role="Button",
                name=label or "Detected icon",
                automation_id=f"omniparser:{len(result) + 1}",
                bounds=bounds,
                enabled=True,
            )
        )
        if len(result) >= _MAX_PARSED_ICONS:
            break
    return tuple(result)


class OmniParserSource:
    """Bounded client for the official local OmniParser ``/parse/`` API."""

    def __init__(
        self,
        endpoint: str,
        *,
        timeout_s: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.endpoint = validate_local_endpoint(endpoint)
        self.timeout_s = max(0.5, min(float(timeout_s), 30.0))
        self._client = client or httpx.AsyncClient(
            timeout=self.timeout_s,
            trust_env=False,
        )
        self._owns_client = client is None

    @classmethod
    def from_environment(cls) -> OmniParserSource | None:
        endpoint = os.environ.get("JARVIS_OMNIPARSER_URL", "").strip()
        if not endpoint:
            return None
        try:
            return cls(endpoint)
        except ValueError as exc:
            logger.warning("Local OmniParser disabled: %s", exc)
            return None

    async def observe(
        self,
        screenshot_path: str | None,
        monitor_geom: tuple[int, int, int, int],
        *,
        cancel_token: CancelToken | None = None,
    ) -> tuple[UIANode, ...]:
        if not screenshot_path:
            return ()
        if cancel_token is not None and cancel_token.is_cancelled():
            raise RuntimeError(f"cancelled: {cancel_token.reason}")
        try:
            screenshot = Path(screenshot_path)
            size = await asyncio.to_thread(lambda: screenshot.stat().st_size)
            if size <= 0 or size > _MAX_SCREENSHOT_BYTES:
                logger.warning("Local OmniParser skipped a screenshot outside the 12 MiB limit")
                return ()
            image = await asyncio.to_thread(screenshot.read_bytes)
            if not image or len(image) > _MAX_SCREENSHOT_BYTES:
                logger.warning("Local OmniParser skipped a screenshot outside the 12 MiB limit")
                return ()
            if cancel_token is not None and cancel_token.is_cancelled():
                raise RuntimeError(f"cancelled: {cancel_token.reason}")
            dimensions = png_dimensions(image)
            if dimensions is None:
                logger.warning(
                    "Local OmniParser skipped a screenshot with an unsupported image format"
                )
                return ()
            response = await self._client.post(
                self.endpoint,
                json={"base64_image": base64.b64encode(image).decode("ascii")},
                timeout=self.timeout_s,
            )
            response.raise_for_status()
            payload: Any = response.json()
            return nodes_from_response(
                payload,
                image_size=dimensions,
                monitor_geom=monitor_geom,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # degrade to the screenshot/UIA path with a visible log
            if cancel_token is not None and cancel_token.is_cancelled():
                raise RuntimeError(f"cancelled: {cancel_token.reason}") from exc
            logger.warning(
                "Local OmniParser perception failed; keeping native vision result: %s",
                exc,
            )
            return ()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()


__all__ = [
    "OmniParserSource",
    "nodes_from_response",
    "png_dimensions",
    "validate_local_endpoint",
]
