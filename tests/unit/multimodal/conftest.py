"""Small real media files for the media tool tests, made on the fly.

An image (Pillow), a tone as WAV (stdlib ``wave``), and a short MPEG-4 video
with a sound track (PyAV, the decoder the tools themselves use). No model, no
network: the local model server is a stand-in ``httpx.MockTransport``.
"""

from __future__ import annotations

import json
import math
import struct
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from jarvis.multimodal import backend


@pytest.fixture(autouse=True)
def _fresh_capability_cache() -> None:
    backend._reset_cache_for_tests()
    yield
    backend._reset_cache_for_tests()


@pytest.fixture
def media_dir(tmp_path: Path) -> Path:
    folder = tmp_path / "media"
    folder.mkdir()
    return folder


def make_png(path: Path, size: tuple[int, int] = (320, 200)) -> Path:
    from PIL import Image, ImageDraw

    image = Image.new("RGB", size, "white")
    ImageDraw.Draw(image).rectangle((20, 20, 120, 120), fill=(200, 20, 20))
    image.save(path)
    return path


def make_wav(path: Path, seconds: float, rate: int = 22_050) -> Path:
    frames = b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * n / rate)))
        for n in range(int(seconds * rate))
    )
    with wave.open(str(path), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(frames)
    return path


def make_video(path: Path, seconds: float = 3.0, fps: int = 10, audio: bool = True) -> Path:
    """Solid red, then green, then blue thirds; a quiet tone as the sound track."""
    av = pytest.importorskip("av")
    import numpy as np

    container = av.open(str(path), mode="w")
    video = container.add_stream("mpeg4", rate=fps)
    video.width, video.height, video.pix_fmt = 160, 96, "yuv420p"
    sound = container.add_stream("aac", rate=16_000) if audio else None
    total = int(seconds * fps)
    colors = [(220, 0, 0), (0, 200, 0), (0, 0, 220)]
    for index in range(total):
        rgb = np.zeros((96, 160, 3), dtype=np.uint8)
        rgb[:, :] = colors[min(2, index * 3 // total)]
        frame = av.VideoFrame.from_ndarray(rgb, format="rgb24")
        for packet in video.encode(frame):
            container.mux(packet)
    for packet in video.encode():
        container.mux(packet)
    if sound is not None:
        samples = (np.sin(np.arange(int(seconds * 16_000)) * 0.05) * 0.2).astype(np.float32)
        step = 1024
        for start in range(0, len(samples), step):
            chunk = samples[start : start + step].reshape(1, -1)
            frame = av.AudioFrame.from_ndarray(chunk, format="flt", layout="mono")
            frame.sample_rate = 16_000
            frame.pts = start
            for packet in sound.encode(frame):
                container.mux(packet)
        for packet in sound.encode():
            container.mux(packet)
    container.close()
    return path


class StandIn:
    """A stand-in ``llama-server``: answers ``/props``, ``/v1/models`` and chat calls.

    Not a model. It records each chat request so tests can check what Jarvis sent.
    """

    def __init__(
        self,
        modalities: dict[str, bool] | None = None,
        *,
        model: str = "gemma-4-E4B-it-Q4_0.gguf",
        answer: str | Callable[[dict[str, Any]], str] = "A red square on white.",
        status: int = 200,
    ) -> None:
        self.modalities = modalities if modalities is not None else {"vision": True, "audio": True}
        self.model = model
        self.answer = answer
        self.status = status
        self.requests: list[dict[str, Any]] = []
        self.hosts: set[str] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.hosts.add(f"{request.url.host}:{request.url.port}")
        path = request.url.path
        if path == "/props":
            return httpx.Response(200, json={"modalities": self.modalities})
        if path == "/v1/models":
            return httpx.Response(
                200, json={"data": [{"id": self.model, "owned_by": "llamacpp"}]}
            )
        if path == "/v1/chat/completions":
            body = json.loads(request.content)
            self.requests.append(body)
            if self.status != 200:
                return httpx.Response(self.status, json={"error": {"message": "boom context"}})
            text = self.answer(body) if callable(self.answer) else self.answer
            return httpx.Response(200, json={"choices": [{"message": {"content": text}}]})
        return httpx.Response(404)


def install(monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], Any]) -> None:
    """Route every backend HTTP call through ``handler``; a closed port refuses."""
    monkeypatch.setattr(
        backend,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False),
    )


def only_port(port: int, standin: StandIn) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.port != port:
            raise httpx.ConnectError("refused", request=request)
        return standin(request)

    return handler


@pytest.fixture(autouse=True)
def _no_hermes_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """Auto-detection must not read the developer's real Hermes config."""
    from jarvis.brain import local_servers

    monkeypatch.setattr(local_servers, "read_hermes_config", lambda: {})
