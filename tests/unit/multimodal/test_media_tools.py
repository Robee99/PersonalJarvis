"""The three media tools end to end against a stand-in server, and their MCP registration.

Decoding is real (Pillow, PyAV on generated files); the model server is a
stand-in that records requests (``conftest.StandIn``). Whisper is replaced by
a fake where a test needs a transcript without a downloaded checkpoint.
"""

from __future__ import annotations

import base64
import io
from pathlib import Path
from typing import Any

import mcp.types as types
import pytest

from jarvis.multimodal import decode, files, tools, transcribe
from jarvis.multimodal.config import from_mapping

from .conftest import StandIn, install, make_png, make_video, make_wav, only_port


def _cfg(media_dir: Path, **extra: Any):
    return from_mapping(
        {"allowed_dirs": [str(media_dir)], "server_url": "http://127.0.0.1:11439", **extra}
    )


@pytest.fixture
def no_whisper(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(transcribe, "whisper_model_name", lambda cfg: None)


@pytest.fixture
def fake_whisper(monkeypatch: pytest.MonkeyPatch) -> list[bytes]:
    heard: list[bytes] = []

    def _fake(pcm: bytes, model: str, device: str, language: str | None):
        heard.append(pcm)
        return transcribe.Transcript(
            text="the meeting moves to thursday",
            engine=f"faster-whisper {model} ({device})",
            language="en",
            segments=[(0.0, 1.5, "the meeting"), (1.5, 3.0, "moves to thursday")],
        )

    monkeypatch.setattr(transcribe, "whisper_model_name", lambda cfg: "small")
    monkeypatch.setattr(transcribe, "_whisper_sync", _fake)
    return heard


# ------------------------------------------------------------ decoding


def test_sample_times_are_evenly_spread_inside_the_video() -> None:
    assert decode.sample_times(10.0, 4) == [1.25, 3.75, 6.25, 8.75]
    assert decode.sample_times(0.0, 4) == [0.0]
    assert decode.clock(73.4) == "1:13" and decode.clock(3725) == "1:02:05"


def test_frames_are_sampled_across_a_real_video(media_dir: Path) -> None:
    video = make_video(media_dir / "rgb.mp4", seconds=3.0)
    info = decode.probe_video(video)
    assert 2.5 < info.seconds < 3.5 and info.has_audio and (info.width, info.height) == (160, 96)
    frames = decode.sample_frames(video, decode.sample_times(info.seconds, 3), 768)
    from PIL import Image

    colours = [Image.open(io.BytesIO(f.jpeg)).convert("RGB").getpixel((80, 48)) for f in frames]
    dominant = [max(range(3), key=lambda i: c[i]) for c in colours]
    assert dominant == [0, 1, 2], colours  # red, green, blue thirds
    stamps = [f.seconds for f in frames]
    assert stamps == sorted(stamps) and stamps[0] < 1.0 < stamps[1] < 2.0 < stamps[2] < 3.0


def test_audio_is_decoded_to_16k_mono_and_long_files_are_refused(media_dir: Path) -> None:
    sound = make_wav(media_dir / "tone.wav", 2.0)
    decoded = decode.decode_audio(sound, 60)
    assert decoded is not None and abs(decoded.seconds - 2.0) < 0.05
    assert len(decoded.pcm) == int(decoded.seconds * 16_000) * 2
    wav = decode.pcm_to_wav(decoded.pcm)
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    with pytest.raises(decode.DecodeError, match="max_audio_seconds"):
        decode.decode_audio(sound, 1.0)


def test_images_are_scaled_and_reencoded_as_jpeg(media_dir: Path) -> None:
    image = make_png(media_dir / "wide.png", size=(2400, 1200))
    prepared = decode.prepare_image(image, 1024)
    assert (prepared.width, prepared.height) == (1024, 512)
    assert prepared.original_size == (2400, 1200)
    assert prepared.data_url().startswith("data:image/jpeg;base64,/9j/")
    broken = media_dir / "broken.png"
    broken.write_bytes(b"not an image at all")
    with pytest.raises(decode.DecodeError, match="could not be read as an image"):
        decode.prepare_image(broken, 1024)


def test_audio_is_split_into_model_sized_clips() -> None:
    pcm = b"\0\0" * 16_000 * 70
    clips = transcribe.chunks(pcm, 30)
    assert [start for start, _ in clips] == [0.0, 30.0, 60.0]
    assert [len(clip) // 32_000 for _, clip in clips] == [30, 30, 10]


# ------------------------------------------------------------ the tools


async def test_describe_image_sends_the_picture_to_the_local_server(
    media_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    standin = StandIn(answer="A red square in the top left corner.")
    install(monkeypatch, only_port(11439, standin))
    image = make_png(media_dir / "shot.png")

    text, is_error = await tools.call(
        tools.DESCRIBE_IMAGE, {"path": str(image), "question": "What shape?"}, _cfg(media_dir)
    )

    assert not is_error, text
    assert "A red square in the top left corner." in text and "shot.png (320x200)" in text
    assert "127.0.0.1:11439" in text
    (request,) = standin.requests
    image_part, question = request["messages"][1]["content"]
    jpeg = base64.b64decode(image_part["image_url"]["url"].split(",", 1)[1])
    assert jpeg[:2] == b"\xff\xd8"
    assert question == {"type": "text", "text": "What shape?"}
    assert standin.hosts == {"127.0.0.1:11439"}


async def test_transcribe_uses_whisper_and_asks_the_audio_model_a_question(
    media_dir: Path, monkeypatch: pytest.MonkeyPatch, fake_whisper: list[bytes]
) -> None:
    standin = StandIn(answer="A calm male voice, no background noise.")
    install(monkeypatch, only_port(11439, standin))
    sound = make_wav(media_dir / "note.wav", 1.0)

    text, is_error = await tools.call(
        tools.TRANSCRIBE_AUDIO,
        {"path": str(sound), "question": "How does the speaker sound?"},
        _cfg(media_dir),
    )

    assert not is_error, text
    assert "faster-whisper small (cpu), language en" in text
    assert "[0:00] the meeting\n[0:02] moves to thursday" in text
    assert "A calm male voice" in text
    assert len(fake_whisper) == 1 and len(fake_whisper[0]) == 32_000
    (request,) = standin.requests
    assert request["messages"][1]["content"][0]["type"] == "input_audio"


async def test_transcribe_without_whisper_uses_the_audio_model_in_clips(
    media_dir: Path, monkeypatch: pytest.MonkeyPatch, no_whisper: None
) -> None:
    standin = StandIn(answer="hello there")
    install(monkeypatch, only_port(11439, standin))
    sound = make_wav(media_dir / "long.wav", 5.0)

    text, is_error = await tools.call(
        tools.TRANSCRIBE_AUDIO, {"path": str(sound)}, _cfg(media_dir, audio_chunk_seconds=2)
    )

    assert not is_error, text
    assert len(standin.requests) == 3  # 2 s + 2 s + 1 s
    assert "[0:00] hello there\n[0:02] hello there\n[0:04] hello there" in text


async def test_transcribe_with_nothing_set_up_is_an_honest_error(
    media_dir: Path, monkeypatch: pytest.MonkeyPatch, no_whisper: None
) -> None:
    install(monkeypatch, only_port(11439, StandIn({"vision": True, "audio": False})))
    sound = make_wav(media_dir / "note.wav", 0.5)

    text, is_error = await tools.call(tools.TRANSCRIBE_AUDIO, {"path": str(sound)}, _cfg(media_dir))

    assert is_error
    assert "No local speech recognizer is set up" in text and "does not accept audio" in text


async def test_understand_video_sends_frames_and_the_transcript(
    media_dir: Path, monkeypatch: pytest.MonkeyPatch, fake_whisper: list[bytes]
) -> None:
    standin = StandIn(answer="Red, then green, then blue screens.")
    install(monkeypatch, only_port(11439, standin))
    video = make_video(media_dir / "rgb.mp4", seconds=3.0)

    text, is_error = await tools.call(
        tools.UNDERSTAND_VIDEO, {"path": str(video), "frames": 3}, _cfg(media_dir)
    )

    assert not is_error, text
    assert "Red, then green, then blue screens." in text
    assert "moves to thursday" in text and "faster-whisper small" in text
    (request,) = standin.requests
    parts = request["messages"][1]["content"]
    assert sum(p["type"] == "image_url" for p in parts) == 3
    assert parts[1]["text"].startswith("Frame at 0:")
    assert any("Transcript of the sound track" in p.get("text", "") for p in parts)


async def test_video_frames_are_capped_and_long_videos_refused(
    media_dir: Path, monkeypatch: pytest.MonkeyPatch, fake_whisper: list[bytes]
) -> None:
    standin = StandIn()
    install(monkeypatch, only_port(11439, standin))
    video = make_video(media_dir / "rgb.mp4", seconds=2.0, audio=False)

    text, is_error = await tools.call(
        tools.UNDERSTAND_VIDEO,
        {"path": str(video), "frames": 99},
        _cfg(media_dir, max_video_frames=4),
    )
    assert not is_error, text
    assert sum(p["type"] == "image_url" for p in standin.requests[0]["messages"][1]["content"]) == 4
    assert "no sound track" in text and fake_whisper == []

    text, is_error = await tools.call(
        tools.UNDERSTAND_VIDEO, {"path": str(video)}, _cfg(media_dir, max_video_seconds=1)
    )
    assert is_error and "max_video_seconds" in text


async def test_a_path_outside_the_allowed_folders_never_reaches_a_model(
    tmp_path: Path, media_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    standin = StandIn()
    install(monkeypatch, only_port(11439, standin))
    # The system temp directory may itself be inside an allowed user folder.
    monkeypatch.setattr(files, "default_roots", lambda: [])
    outside = make_png(tmp_path / "private.png")

    text, is_error = await tools.call(tools.DESCRIBE_IMAGE, {"path": str(outside)}, _cfg(media_dir))

    assert is_error and "outside the folders" in text
    assert standin.requests == [] and standin.hosts == set()


async def test_switched_off_and_unknown_tools_refuse(media_dir: Path) -> None:
    text, is_error = await tools.call(
        tools.DESCRIBE_IMAGE, {"path": "x"}, from_mapping({"enabled": False})
    )
    assert is_error and "switched off" in text
    text, is_error = await tools.call("media-nope", {}, from_mapping({}))
    assert is_error and "Unknown media tool" in text


def test_the_tools_are_read_only_safe_tier_with_a_required_path() -> None:
    assert tools.RISK_TIER == "safe"
    assert tools.TOOL_NAMES == {
        "media-describe-image",
        "media-transcribe-audio",
        "media-understand-video",
    }
    for spec in tools.TOOLS:
        assert spec.input_schema["required"] == ["path"]
        assert "Read-only" in spec.description and "local" in spec.description


# ------------------------------------------------------------ MCP registration


async def test_the_mcp_server_lists_and_routes_the_media_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.mcp import jarvis_tools_server as server

    monkeypatch.setattr(server, "_gateway", lambda: None)
    seen: list[tuple[str, dict[str, Any]]] = []

    async def fake_call(name: str, args: dict[str, Any] | None) -> tuple[str, bool]:
        seen.append((name, dict(args or {})))
        return "described", False

    monkeypatch.setattr(tools, "call", fake_call)
    built = server.build_server()

    listed = await built.request_handlers[types.ListToolsRequest](
        types.ListToolsRequest(method="tools/list")
    )
    by_name = {tool.name: tool for tool in listed.root.tools}
    for name in tools.TOOL_NAMES:
        assert by_name[name].annotations.readOnlyHint is True
        assert by_name[name].annotations.destructiveHint is False
        assert by_name[name].inputSchema["required"] == ["path"]
        assert "confirm_token" not in by_name[name].inputSchema["properties"]

    response = await built.request_handlers[types.CallToolRequest](
        types.CallToolRequest(
            params=types.CallToolRequestParams(
                name="media-describe-image", arguments={"path": "C:/x.png"}
            )
        )
    )
    assert response.root.isError is False and response.root.content[0].text == "described"
    assert seen == [("media-describe-image", {"path": "C:/x.png"})]


async def test_society_and_task_sessions_do_not_get_the_media_tools(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.mcp import jarvis_tools_server as server

    for session in ("society:seat-1", "task-abc"):
        token = server.CHAT_SESSION_REF.set(session)
        try:
            assert server._media_offered(set()) == []
        finally:
            server.CHAT_SESSION_REF.reset(token)
    assert {spec.name for spec in server._media_offered(set())} == tools.TOOL_NAMES
    assert server._media_offered({"media-describe-image"})[0].name != "media-describe-image"


def test_hermes_is_allowed_the_media_tools() -> None:
    from jarvis.cli_ctl.free_voice import HERMES_JARVIS_TOOLS, hermes_jarvis_entry

    assert tools.TOOL_NAMES <= set(HERMES_JARVIS_TOOLS)
    entry = hermes_jarvis_entry("http://127.0.0.1:8765")
    assert tools.TOOL_NAMES <= set(entry["tools"]["include"])
