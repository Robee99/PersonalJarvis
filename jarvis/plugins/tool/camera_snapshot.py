"""camera tool: takes one still from the webcam as JPEG for Brain vision.

Risk tier: ask. A camera frame shows the person and their room, so every call
is confirmed unless the user whitelists the tool under
``[safety.whitelist].tools``. Nothing opens the camera at boot (AP-26): the
device is opened inside ``execute`` and released before it returns.

Backends, chosen per platform at call time:

* **Windows** - WinRT ``MediaCapture`` in video-only mode (no microphone),
  from the same MIT-licensed PyWinRT family the ``[desktop]`` extra already
  uses for the media session. The still is encoded to JPEG by Windows itself.
  The three namespace packages (:data:`WINDOWS_CAMERA_PACKAGES`) ship in
  ``[desktop]``, so the installer bundles them.
* **Linux** - OpenCV, only when the user installed it themselves. It is not a
  dependency: its wheels bundle FFmpeg under the LGPL.
* **macOS** - refused honestly. The bundle deliberately ships without
  ``NSCameraUsageDescription`` (jarvis/core/macos_privacy_strings.py), and an
  unbundled camera call would be killed by TCC, so there is no caller to wire.

The result has the same shape as the ``screenshot`` tool: one
``{"type": "image", "mime": "image/jpeg", "data": <base64>}`` artifact, scaled
and re-encoded through the same byte budget.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import sys
from collections.abc import Awaitable, Callable
from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.plugins.tool.screen_snapshot import _MAX_BYTES, _encode_with_budget

log = logging.getLogger(__name__)

#: A frame whose brightest channel average stays below this reads as a covered
#: lens or a closed privacy shutter, not a picture of anything.
_DARK_FRAME_MEAN = 6.0
#: Webcams adjust exposure over the first frames after opening; the first one
#: is often black or green.
_OPENCV_WARMUP_FRAMES = 5
#: ERROR_ACCESS_DENIED and its HRESULT (0x80070005), as PyWinRT may report either.
_ACCESS_DENIED = frozenset({5, -2147024891})

CaptureFn = Callable[[], Awaitable[bytes]]

#: PyWinRT namespaces the Windows backend imports (MIT, same family as the
#: media-session packages in ``[desktop]``).
WINDOWS_CAMERA_PACKAGES: tuple[str, ...] = (
    "winrt-Windows.Media.Capture",
    "winrt-Windows.Media.MediaProperties",
    "winrt-Windows.Storage.Streams",
)


class CameraUnavailable(RuntimeError):
    """The camera cannot be used here; ``str(exc)`` is the honest reason."""


async def _capture_windows() -> bytes:
    """One JPEG still through WinRT ``MediaCapture``; the device is always closed."""
    try:
        from winrt.windows.media.capture import (  # type: ignore[import-not-found]  # noqa: PLC0415
            MediaCapture,
            MediaCaptureInitializationSettings,
            StreamingCaptureMode,
        )
        from winrt.windows.media.mediaproperties import (  # type: ignore[import-not-found]  # noqa: PLC0415
            ImageEncodingProperties,
        )
        from winrt.windows.storage.streams import (  # type: ignore[import-not-found]  # noqa: PLC0415
            DataReader,
            InMemoryRandomAccessStream,
        )
    except ImportError as exc:
        raise CameraUnavailable(
            f"Missing dependency: {exc.name or exc}. Install the camera packages with "
            f"`pip install {' '.join(WINDOWS_CAMERA_PACKAGES)}`."
        ) from exc

    capture = MediaCapture()
    try:
        settings = MediaCaptureInitializationSettings()
        settings.streaming_capture_mode = StreamingCaptureMode.VIDEO
        try:
            await capture.initialize_with_settings_async(settings)
        except OSError as exc:
            if isinstance(exc, PermissionError) or getattr(exc, "winerror", None) in _ACCESS_DENIED:
                raise CameraUnavailable(
                    "Windows blocked the camera. Turn on Settings > Privacy & security > "
                    "Camera > Let desktop apps access your camera."
                ) from exc
            raise CameraUnavailable(f"No usable camera was found: {exc}") from exc
        stream = InMemoryRandomAccessStream()
        try:
            await capture.capture_photo_to_stream_async(
                ImageEncodingProperties.create_jpeg(), stream
            )
            size = int(stream.size)
            reader = DataReader(stream.get_input_stream_at(0))
            try:
                await reader.load_async(size)
                return bytes(reader.read_buffer(size))
            finally:
                reader.close()
        finally:
            stream.close()
    finally:
        capture.close()


def _grab_opencv() -> bytes:
    """One JPEG still through a user-installed OpenCV; blocking, run in a thread."""
    try:
        import cv2  # type: ignore[import-not-found]  # noqa: PLC0415
    except ImportError as exc:
        raise CameraUnavailable(
            "Missing dependency: cv2. The camera on this platform needs OpenCV "
            "(`pip install opencv-python-headless`), which Jarvis does not install."
        ) from exc

    device = cv2.VideoCapture(0)
    try:
        if not device.isOpened():
            raise CameraUnavailable("No usable camera was found.")
        frame = None
        for _ in range(_OPENCV_WARMUP_FRAMES):
            ok, grabbed = device.read()
            if ok:
                frame = grabbed
        if frame is None:
            raise CameraUnavailable("The camera opened but returned no frame.")
        ok, encoded = cv2.imencode(".jpg", frame)
        if not ok:
            raise CameraUnavailable("The camera frame could not be encoded.")
        return encoded.tobytes()
    finally:
        device.release()


async def _capture_default() -> bytes:
    if sys.platform == "win32":
        return await _capture_windows()
    if sys.platform == "darwin":
        raise CameraUnavailable(
            "The camera is not available on macOS: Personal Jarvis does not "
            "request camera access there."
        )
    return await asyncio.to_thread(_grab_opencv)


class CameraSnapshotTool:
    name: str = "camera"
    risk_tier: str = "ask"
    description: str = (
        "Takes one still photo from the user's webcam and returns it as an image "
        "for vision. Only when the user asks you to look through the camera; "
        "for anything on the screen use screenshot."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Why the photo is needed (for the log)",
            }
        },
        "required": [],
    }

    def __init__(self, capture: CaptureFn | None = None) -> None:
        self._capture = capture or _capture_default

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        reason = (args or {}).get("reason") or ""
        try:
            from PIL import Image, ImageStat  # noqa: PLC0415
        except ImportError as exc:  # the reason goes back to the user as the tool error
            return ToolResult(
                success=False, output=None, error=f"Missing dependency: {exc.name or exc}"
            )

        try:
            raw = await self._capture()
        except CameraUnavailable as exc:
            log.info("camera: unavailable (%s)", exc)
            return ToolResult(success=False, output=None, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - driver errors are varied
            log.warning("camera: capture failed", exc_info=True)
            return ToolResult(success=False, output=None, error=f"Camera capture failed: {exc}")

        try:
            image = Image.open(io.BytesIO(raw)).convert("RGB")
        except Exception as exc:  # noqa: BLE001 - Pillow raises several types
            log.warning("camera: frame could not be decoded", exc_info=True)
            return ToolResult(
                success=False, output=None, error=f"The camera frame could not be read: {exc}"
            )

        # A black frame is never a success: the model would describe darkness.
        if max(ImageStat.Stat(image).mean) < _DARK_FRAME_MEAN:
            return ToolResult(
                success=False,
                output=None,
                error=(
                    "The camera returned a black frame. The lens may be covered "
                    "or a privacy shutter closed."
                ),
            )

        jpeg = _encode_with_budget(image, _MAX_BYTES)
        output = f"Camera photo taken ({reason})" if reason else "Camera photo taken"
        return ToolResult(
            success=True,
            output=output,
            artifacts=(
                {"type": "image", "mime": "image/jpeg", "data": base64.b64encode(jpeg).decode()},
            ),
        )
