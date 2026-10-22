"""The three media tools on Jarvis's MCP server, and what each call does.

Answered in-process by ``jarvis/mcp/jarvis_tools_server.py`` next to the
gateway catalog (like the brain map tools), offered with ``readOnlyHint``:

* risk tier **safe**: a call reads one local file the caller named and asks a
  model on this computer about it. It changes nothing, sends nothing off the
  machine, and starts no process, so there is nothing for a spoken yes to
  approve. The containment is structural instead: the folder allow-list in
  :mod:`jarvis.multimodal.files`, the loopback-only backend in
  :mod:`jarvis.multimodal.backend`, and the size and duration limits in
  ``[multimodal]``.
* every failure is text the model can relay (``is_error`` true), never an
  exception out of the MCP handler.

What comes back is a description or a transcript. The caller's own model
reads it, so a caller running on a cloud model receives that text (never the
file). That is the same as the user typing the description themselves.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis.multimodal import backend, decode, files
from jarvis.multimodal.config import MediaConfig, load_media_config
from jarvis.multimodal.transcribe import TranscriberUnavailable, transcribe

log = logging.getLogger(__name__)

DESCRIBE_IMAGE = "media-describe-image"
TRANSCRIBE_AUDIO = "media-transcribe-audio"
UNDERSTAND_VIDEO = "media-understand-video"

#: The tier the tools run at, for the docs and the tests. See the module docstring.
RISK_TIER = "safe"

#: Longest answer handed back to the caller.
MAX_RESULT_CHARS = 12_000

_PATH = {
    "type": "string",
    "description": (
        "Absolute path of the file on this computer (Pictures, Videos, Music, Downloads, "
        "Desktop, Documents, Hermes's media cache, or a Jarvis chat drop)."
    ),
}
_QUESTION = {
    "type": "string",
    "description": "Optional: what to find out. Without it you get a general description.",
}


@dataclass(frozen=True, slots=True)
class MediaTool:
    name: str
    description: str
    input_schema: dict[str, Any]


TOOLS: tuple[MediaTool, ...] = (
    MediaTool(
        DESCRIBE_IMAGE,
        "Look at a local image (photo, screenshot, scan) with a vision model running on this "
        "computer and get a text description, or the answer to a question about it. Use it "
        "whenever the user refers to a picture file you cannot see yourself. Read-only, local.",
        {
            "type": "object",
            "properties": {"path": _PATH, "question": _QUESTION},
            "required": ["path"],
        },
    ),
    MediaTool(
        TRANSCRIBE_AUDIO,
        "Transcribe a local audio recording (voice note, call, podcast) on this computer, with "
        "timestamps. With a question, an audio model on this computer also answers it about "
        "the sound itself (short clips). Read-only, local.",
        {
            "type": "object",
            "properties": {
                "path": _PATH,
                "question": _QUESTION,
                "language": {
                    "type": "string",
                    "description": "Optional spoken language code (en, de, ...); default: detect.",
                },
            },
            "required": ["path"],
        },
    ),
    MediaTool(
        UNDERSTAND_VIDEO,
        "Watch a local video on this computer: evenly spaced frames go to a local vision model "
        "together with the transcript of the sound track, and you get a text account of what "
        "happens (with times), or the answer to a question. Read-only, local.",
        {
            "type": "object",
            "properties": {
                "path": _PATH,
                "question": _QUESTION,
                "frames": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 16,
                    "description": "How many frames to look at (default 8).",
                },
                "include_audio": {
                    "type": "boolean",
                    "description": "Transcribe the sound track too (default true).",
                },
            },
            "required": ["path"],
        },
    ),
)

TOOL_NAMES: frozenset[str] = frozenset(tool.name for tool in TOOLS)

#: One media job at a time: each holds a decoded file and a model call, and the
#: owner's laptop is often short of free RAM.
_JOB = asyncio.Semaphore(1)


class _Refusal(Exception):
    """A failure whose text goes to the caller as is."""


def _mb(value: float) -> int:
    return int(value * 1_000_000)


def _check(cfg: MediaConfig, raw: Any, kind: files.Kind) -> files.MediaFile:
    limit = cfg.max_image_mb if kind == "image" else cfg.max_file_mb
    try:
        return files.check_file(
            str(raw or ""),
            want=(kind,),
            max_bytes=_mb(limit),
            roots=files.allowed_roots(cfg.allowed_dirs),
        )
    except files.MediaFileError as exc:
        raise _Refusal(str(exc)) from exc


def _footer(server: backend.MediaServer, seconds: float) -> str:
    return f"(local model {server.model or 'unknown'} at {server.root}, {seconds:.1f} s)"


async def describe_image(cfg: MediaConfig, args: dict[str, Any]) -> str:
    media = _check(cfg, args.get("path"), "image")
    prepared = await asyncio.to_thread(decode.prepare_image, media.path, cfg.image_max_side)
    question = str(args.get("question") or "").strip()
    prompt = question or (
        "Describe this image in detail: what it shows, any people, objects, setting, and all "
        "readable text verbatim."
    )
    answer, server, took = await backend.ask(
        cfg, "image", [backend.image_part(prepared.data_url()), backend.text_part(prompt)]
    )
    width, height = prepared.original_size
    return f"Image {media.path.name} ({width}x{height}):\n{answer}\n\n{_footer(server, took)}"


async def _decode_audio(cfg: MediaConfig, path: Path) -> decode.DecodedAudio | None:
    return await asyncio.to_thread(decode.decode_audio, path, cfg.max_audio_seconds)


async def transcribe_audio(cfg: MediaConfig, args: dict[str, Any]) -> str:
    media = _check(cfg, args.get("path"), "audio")
    audio = await _decode_audio(cfg, media.path)
    if audio is None or not audio.pcm:
        raise _Refusal(f"{media.path.name} has no sound to transcribe.")
    language = str(args.get("language") or "").strip() or None
    question = str(args.get("question") or "").strip()
    started = time.monotonic()
    lines = [f"Audio {media.path.name} ({decode.clock(audio.seconds)} long)"]
    if audio.truncated:
        lines[0] += f", only the first {decode.clock(audio.seconds)} were read"
    try:
        transcript = await transcribe(cfg, audio.pcm, language)
    except TranscriberUnavailable as exc:
        if not question:
            raise _Refusal(str(exc)) from exc
        transcript = None
        lines.append(f"No transcript: {exc}")
    if transcript is not None:
        lang = f", language {transcript.language}" if transcript.language else ""
        lines.append(f"Transcript ({transcript.engine}{lang}, {time.monotonic() - started:.1f} s):")
        lines.append(transcript.timed() or "[no speech]")
    if question:
        lines.append("")
        lines.append(await _audio_answer(cfg, audio, question))
    return "\n".join(lines)


async def _audio_answer(cfg: MediaConfig, audio: decode.DecodedAudio, question: str) -> str:
    """The audio model's answer about the first clip, or why there is none."""
    clip_s = cfg.audio_chunk_seconds
    try:
        server = await backend.find_server(cfg, "audio")
    except backend.BackendUnavailable as exc:  # reported in the answer: transcript only
        return (
            "No audio model is running on this computer, so the question was not put to one; "
            f"answer it from the transcript. ({exc})"
        )
    clip = audio.pcm[: int(clip_s * decode.SAMPLE_RATE) * 2]
    note = (
        f" (the model heard only the first {clip_s:.0f} s)" if len(clip) < len(audio.pcm) else ""
    )
    started = time.monotonic()
    answer = await backend.complete(
        server,
        [backend.audio_part(decode.pcm_to_wav(clip)), backend.text_part(question)],
        max_tokens=cfg.max_tokens,
        timeout_s=cfg.timeout_seconds,
    )
    took = time.monotonic() - started
    return f"Answer from the audio model{note}:\n{answer}\n\n{_footer(server, took)}"


async def understand_video(cfg: MediaConfig, args: dict[str, Any]) -> str:
    media = _check(cfg, args.get("path"), "video")
    info = await asyncio.to_thread(decode.probe_video, media.path)
    if info.seconds > cfg.max_video_seconds + 0.5:
        raise _Refusal(
            f"{media.path.name} is {decode.clock(info.seconds)} long; the limit is "
            f"{decode.clock(cfg.max_video_seconds)} ([multimodal].max_video_seconds)."
        )
    try:
        count = int(args.get("frames") or cfg.video_frames)
    except (TypeError, ValueError):  # a non-numeric frame count uses the default
        count = cfg.video_frames
    count = max(1, min(count, cfg.max_video_frames))
    # Find the vision model before decoding anything: no point in a transcript nobody reads.
    server = await backend.find_server(cfg, "image")
    times = decode.sample_times(info.seconds, count)
    frames = await asyncio.to_thread(decode.sample_frames, media.path, times, cfg.frame_max_side)

    transcript_text = ""
    transcript_note = "The video has no sound track."
    if info.has_audio and args.get("include_audio", True) is not False:
        try:
            audio = await _decode_audio(cfg, media.path)
            if audio is not None and audio.pcm:
                transcript = await transcribe(cfg, audio.pcm)
                transcript_text = transcript.timed()
                transcript_note = (
                    f"Sound track transcript ({transcript.engine}):\n"
                    + (transcript_text or "[no speech]")
                )
        except (TranscriberUnavailable, decode.DecodeError, backend.BackendError) as exc:
            # Reported in the answer; the frames alone still describe the video.
            transcript_note = f"The sound track was not transcribed: {exc}"
    elif info.has_audio:
        transcript_note = "The sound track was not requested."

    question = str(args.get("question") or "").strip()
    parts: list[dict[str, Any]] = [
        backend.text_part(
            f"These are {len(frames)} frames from a {decode.clock(info.seconds)} long video, "
            "in order, each labelled with its time."
        )
    ]
    for frame in frames:
        parts.append(backend.text_part(f"Frame at {decode.clock(frame.seconds)}:"))
        parts.append(backend.image_part(frame.data_url()))
    if transcript_text:
        parts.append(backend.text_part("Transcript of the sound track:\n" + transcript_text[:6000]))
    parts.append(
        backend.text_part(
            question
            or "Describe what happens in this video, in order, with times. Mention people, "
            "places, actions, on-screen text, and what is said."
        )
    )
    started = time.monotonic()
    answer = await backend.complete(
        server, parts, max_tokens=cfg.max_tokens, timeout_s=cfg.timeout_seconds
    )
    took = time.monotonic() - started
    stamps = ", ".join(decode.clock(frame.seconds) for frame in frames)
    return (
        f"Video {media.path.name} ({decode.clock(info.seconds)} long, {info.width}x{info.height}; "
        f"frames at {stamps}):\n{answer}\n\n{transcript_note}\n\n{_footer(server, took)}"
    )


_HANDLERS = {
    DESCRIBE_IMAGE: describe_image,
    TRANSCRIBE_AUDIO: transcribe_audio,
    UNDERSTAND_VIDEO: understand_video,
}


def _clip(text: str) -> str:
    if len(text) <= MAX_RESULT_CHARS:
        return text
    return text[: MAX_RESULT_CHARS - 40] + "\n[... cut to fit; ask a narrower question]"


async def call(
    name: str, args: dict[str, Any] | None, cfg: MediaConfig | None = None
) -> tuple[str, bool]:
    """``(text, is_error)`` for one tool call. Never raises."""
    handler = _HANDLERS.get(name)
    if handler is None:
        return f"Unknown media tool {name!r}.", True
    try:
        cfg = cfg or await asyncio.to_thread(load_media_config)
        if not cfg.enabled:
            return "Media tools are switched off ([multimodal].enabled = false).", True
        async with _JOB:
            text = await handler(cfg, dict(args or {}))
        return _clip(text), False
    except (
        _Refusal,
        files.MediaFileError,
        decode.DecodeError,
        backend.BackendUnavailable,
        backend.BackendError,
        TranscriberUnavailable,
    ) as exc:
        log.info("multimodal: %s refused: %s", name, exc)
        return str(exc), True
    except TimeoutError:
        log.warning("multimodal: %s timed out", name)
        return "The local model took too long; try a shorter clip or fewer frames.", True
    except Exception as exc:  # noqa: BLE001 — a tool failure is data for the model, not a crash
        log.warning("multimodal: %s raised", name, exc_info=True)
        return f"Media tool failed: {type(exc).__name__}: {exc}", True
