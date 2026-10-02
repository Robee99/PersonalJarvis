"""CameraSnapshotTool: one honest webcam still, never a black frame, never a held device.

The WinRT and OpenCV layers are replaced by small fake modules in
``sys.modules``; nothing here touched a real camera. The fakes record what was
opened and closed, which is the proof that the device is released on every path.
"""

from __future__ import annotations

import base64
import io
import sys
import types
from types import SimpleNamespace

import pytest
from PIL import Image

from jarvis.plugins.tool import camera_snapshot
from jarvis.plugins.tool.camera_snapshot import CameraSnapshotTool, CameraUnavailable


def _jpeg(color: tuple[int, int, int], size: tuple[int, int] = (64, 48)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="JPEG")
    return buf.getvalue()


def _tool_returning(data: bytes | Exception) -> CameraSnapshotTool:
    async def capture() -> bytes:
        if isinstance(data, Exception):
            raise data
        return data

    return CameraSnapshotTool(capture=capture)


def test_tool_contract() -> None:
    tool = CameraSnapshotTool()
    assert tool.name == "camera"
    assert tool.risk_tier == "ask"
    assert tool.schema["required"] == []


async def test_photo_is_an_image_artifact_scaled_to_the_screenshot_budget() -> None:
    result = await _tool_returning(_jpeg((120, 90, 60), size=(1920, 1080))).execute(
        {"reason": "user asked"}, SimpleNamespace()
    )

    assert result.success
    assert result.output == "Camera photo taken (user asked)"
    [artifact] = result.artifacts
    assert artifact["type"] == "image" and artifact["mime"] == "image/jpeg"
    image = Image.open(io.BytesIO(base64.b64decode(artifact["data"])))
    assert max(image.size) == 1280


async def test_black_frame_is_an_honest_failure() -> None:
    result = await _tool_returning(_jpeg((0, 0, 0))).execute({}, SimpleNamespace())

    assert not result.success
    assert "black frame" in result.error


async def test_unavailable_camera_reason_reaches_the_model() -> None:
    result = await _tool_returning(CameraUnavailable("No usable camera was found.")).execute(
        {}, SimpleNamespace()
    )

    assert not result.success
    assert result.error == "No usable camera was found."


async def test_driver_error_is_reported_not_raised() -> None:
    result = await _tool_returning(RuntimeError("device busy")).execute({}, SimpleNamespace())

    assert not result.success
    assert result.error == "Camera capture failed: device busy"


async def test_garbage_bytes_are_reported() -> None:
    result = await _tool_returning(b"not a jpeg").execute({}, SimpleNamespace())

    assert not result.success
    assert "could not be read" in result.error


async def test_macos_refuses_without_touching_a_device(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "cv2", None)

    result = await CameraSnapshotTool().execute({}, SimpleNamespace())

    assert not result.success
    assert "macOS" in result.error


# --- OpenCV (Linux) ---------------------------------------------------------


class _FakeVideoCapture:
    instances: list[_FakeVideoCapture] = []

    def __init__(self, index: int, *, opened: bool = True, frames: int = 9) -> None:
        self.index = index
        self.opened = opened
        self.frames = frames
        self.reads = 0
        self.released = False
        _FakeVideoCapture.instances.append(self)

    def isOpened(self) -> bool:  # noqa: N802 - OpenCV's spelling
        return self.opened

    def read(self):
        self.reads += 1
        if self.reads > self.frames:
            return False, None
        return True, f"frame-{self.reads}"

    def release(self) -> None:
        self.released = True


def _install_cv2(monkeypatch: pytest.MonkeyPatch, **capture_kwargs) -> list[str]:
    encoded: list[str] = []
    _FakeVideoCapture.instances.clear()

    def imencode(ext: str, frame: str):
        encoded.append(frame)
        return True, SimpleNamespace(tobytes=lambda: _jpeg((200, 180, 160)))

    fake = types.ModuleType("cv2")
    fake.VideoCapture = lambda index: _FakeVideoCapture(index, **capture_kwargs)
    fake.imencode = imencode
    monkeypatch.setitem(sys.modules, "cv2", fake)
    monkeypatch.setattr(sys, "platform", "linux")
    return encoded


async def test_opencv_keeps_the_last_warmed_up_frame_and_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    encoded = _install_cv2(monkeypatch)

    result = await CameraSnapshotTool().execute({}, SimpleNamespace())

    assert result.success
    [device] = _FakeVideoCapture.instances
    assert device.index == 0
    assert device.reads == camera_snapshot._OPENCV_WARMUP_FRAMES
    assert encoded == [f"frame-{camera_snapshot._OPENCV_WARMUP_FRAMES}"]
    assert device.released


async def test_opencv_without_a_camera_releases_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_cv2(monkeypatch, opened=False)

    result = await CameraSnapshotTool().execute({}, SimpleNamespace())

    assert result.error == "No usable camera was found."
    assert _FakeVideoCapture.instances[0].released


async def test_opencv_with_no_frames_releases_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_cv2(monkeypatch, frames=0)

    result = await CameraSnapshotTool().execute({}, SimpleNamespace())

    assert result.error == "The camera opened but returned no frame."
    assert _FakeVideoCapture.instances[0].released


async def test_missing_opencv_names_the_package(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "cv2", None)
    monkeypatch.setattr(sys, "platform", "linux")

    result = await CameraSnapshotTool().execute({}, SimpleNamespace())

    assert not result.success
    assert "opencv-python-headless" in result.error


# --- WinRT (Windows) --------------------------------------------------------


class _WinRT:
    """Records the MediaCapture lifecycle; ``init_error`` makes initialize fail."""

    def __init__(self, init_error: BaseException | None = None) -> None:
        self.init_error = init_error
        self.events: list[str] = []
        self.payload = _jpeg((90, 140, 200))

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        rt = self

        class MediaCaptureInitializationSettings:
            streaming_capture_mode = None

        class MediaCapture:
            async def initialize_with_settings_async(self, settings) -> None:
                rt.events.append(f"init:{settings.streaming_capture_mode}")
                if rt.init_error is not None:
                    raise rt.init_error

            async def capture_photo_to_stream_async(self, props, stream) -> None:
                rt.events.append(f"photo:{props}")
                stream.data = rt.payload

            def close(self) -> None:
                rt.events.append("capture-closed")

        class InMemoryRandomAccessStream:
            data = b""

            @property
            def size(self) -> int:
                return len(self.data)

            def get_input_stream_at(self, position: int):
                return self.data[position:]

            def close(self) -> None:
                rt.events.append("stream-closed")

        class DataReader:
            def __init__(self, source: bytes) -> None:
                self.source = source

            async def load_async(self, count: int) -> int:
                return count

            def read_buffer(self, length: int) -> bytes:
                return self.source[:length]

            def close(self) -> None:
                rt.events.append("reader-closed")

        capture = types.ModuleType("winrt.windows.media.capture")
        capture.MediaCapture = MediaCapture
        capture.MediaCaptureInitializationSettings = MediaCaptureInitializationSettings
        capture.StreamingCaptureMode = SimpleNamespace(VIDEO="video")
        props = types.ModuleType("winrt.windows.media.mediaproperties")
        props.ImageEncodingProperties = SimpleNamespace(create_jpeg=lambda: "jpeg")
        streams = types.ModuleType("winrt.windows.storage.streams")
        streams.DataReader = DataReader
        streams.InMemoryRandomAccessStream = InMemoryRandomAccessStream
        for name, module in {
            "winrt": types.ModuleType("winrt"),
            "winrt.windows": types.ModuleType("winrt.windows"),
            "winrt.windows.media": types.ModuleType("winrt.windows.media"),
            "winrt.windows.media.capture": capture,
            "winrt.windows.media.mediaproperties": props,
            "winrt.windows.storage": types.ModuleType("winrt.windows.storage"),
            "winrt.windows.storage.streams": streams,
        }.items():
            monkeypatch.setitem(sys.modules, name, module)
        monkeypatch.setattr(sys, "platform", "win32")


async def test_windows_takes_a_video_only_still_and_closes_everything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rt = _WinRT()
    rt.install(monkeypatch)

    result = await CameraSnapshotTool().execute({}, SimpleNamespace())

    assert result.success
    # Video-only: a camera still must never open the microphone.
    assert rt.events == [
        "init:video",
        "photo:jpeg",
        "reader-closed",
        "stream-closed",
        "capture-closed",
    ]


def _access_denied_hresult() -> OSError:
    # ``winerror`` only exists on Windows builds of OSError; set it by hand here.
    error = OSError("Access is denied.")
    error.winerror = -2147024891
    return error


@pytest.mark.parametrize(
    "error",
    [PermissionError("denied"), _access_denied_hresult()],
)
async def test_windows_privacy_block_names_the_setting(
    monkeypatch: pytest.MonkeyPatch, error: OSError
) -> None:
    rt = _WinRT(init_error=error)
    rt.install(monkeypatch)

    result = await CameraSnapshotTool().execute({}, SimpleNamespace())

    assert not result.success
    assert "Let desktop apps access your camera" in result.error
    assert rt.events[-1] == "capture-closed"


async def test_windows_without_a_camera_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    rt = _WinRT(init_error=OSError("No capture devices are available."))
    rt.install(monkeypatch)

    result = await CameraSnapshotTool().execute({}, SimpleNamespace())

    assert result.error.startswith("No usable camera was found")
    assert rt.events[-1] == "capture-closed"


async def test_windows_without_the_packages_names_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "winrt.windows.media.capture", None)
    monkeypatch.setattr(sys, "platform", "win32")

    result = await CameraSnapshotTool().execute({}, SimpleNamespace())

    assert not result.success
    assert "pip install winrt-Windows.Media.Capture" in result.error
