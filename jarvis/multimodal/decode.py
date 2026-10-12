"""Turning a local media file into what a local model takes: JPEG, 16 kHz WAV, frames.

* Images go through Pillow: any format Pillow reads (PNG, JPEG, WebP, GIF,
  BMP, TIFF), first frame, EXIF rotation applied, scaled down to a longest side
  and re-encoded as JPEG, because ``llama-server`` decodes with ``stb_image``
  (no WebP, no TIFF) and a 12-megapixel photo costs the model nothing but time.
* Audio goes through PyAV (FFmpeg in a wheel; already installed with Jarvis's
  voice stack, so nothing new to install on Windows): any container FFmpeg
  reads, mixed to mono 16 kHz 16-bit PCM, the format both Whisper and the
  audio encoders of llama.cpp models expect.
* Video goes through PyAV too: frames at evenly spaced times (seeking, not
  decoding the whole file) and the sound track as PCM.

All functions are blocking; callers run them with ``asyncio.to_thread``.
"""

from __future__ import annotations

import base64
import io
import logging
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000


class DecodeError(RuntimeError):
    """The file could not be decoded; ``str(exc)`` is the reason the model reads."""


def _pil() -> Any:
    try:
        from PIL import Image, ImageOps  # noqa: PLC0415
    except ImportError as exc:
        raise DecodeError("Pillow is not installed, so images cannot be read.") from exc
    return Image, ImageOps


def _av() -> Any:
    try:
        import av  # noqa: PLC0415
    except ImportError as exc:
        raise DecodeError(
            "PyAV is not installed, so audio and video cannot be read. Install it with "
            "`pip install av` in Jarvis's environment."
        ) from exc
    return av


def _jpeg(image: Any, max_side: int, quality: int = 85) -> bytes:
    Image, _ = _pil()
    image = image.convert("RGB")
    if max(image.size) > max_side:
        image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=quality)
    return out.getvalue()


@dataclass(frozen=True)
class PreparedImage:
    jpeg: bytes
    width: int
    height: int
    original_size: tuple[int, int]

    def data_url(self) -> str:
        return "data:image/jpeg;base64," + base64.b64encode(self.jpeg).decode("ascii")


def prepare_image(path: Path, max_side: int) -> PreparedImage:
    """``path`` as a JPEG no larger than ``max_side`` on its longest side."""
    Image, ImageOps = _pil()
    try:
        with Image.open(path) as opened:
            opened.seek(0)
            original = opened.size
            image = ImageOps.exif_transpose(opened) or opened
            jpeg = _jpeg(image, max_side)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise DecodeError(f"{path.name} could not be read as an image: {exc}") from exc
    with Image.open(io.BytesIO(jpeg)) as check:
        width, height = check.size
    return PreparedImage(jpeg=jpeg, width=width, height=height, original_size=original)


def pcm_to_wav(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(pcm)
    return out.getvalue()


def _duration_s(container: Any, stream: Any) -> float | None:
    if stream is not None and stream.duration is not None and stream.time_base is not None:
        return float(stream.duration * stream.time_base)
    if container.duration is not None:
        return float(container.duration) / 1_000_000.0
    return None


@dataclass(frozen=True)
class DecodedAudio:
    pcm: bytes
    seconds: float
    #: True when the source was longer than the limit and was cut there.
    truncated: bool


def decode_audio(path: Path, max_seconds: float) -> DecodedAudio | None:
    """The first audio stream of ``path`` as mono 16 kHz s16 PCM; ``None`` without one.

    Raises :class:`DecodeError` when the file is not readable media, or when its
    declared duration is over ``max_seconds`` (a limit, not a silent cut).
    """
    av = _av()
    try:
        container = av.open(str(path))
    except (av.error.FFmpegError, OSError) as exc:  # type: ignore[attr-defined]
        raise DecodeError(f"{path.name} could not be opened as media: {exc}") from exc
    try:
        if not container.streams.audio:
            return None
        stream = container.streams.audio[0]
        declared = _duration_s(container, stream)
        if declared is not None and declared > max_seconds + 0.5:
            raise DecodeError(
                f"{path.name} is {clock(declared)} long; the limit is "
                f"{clock(max_seconds)} ([multimodal].max_audio_seconds)."
            )
        resampler = av.AudioResampler(format="s16", layout="mono", rate=SAMPLE_RATE)
        limit = int(max_seconds * SAMPLE_RATE) * 2
        chunks: list[bytes] = []
        total = 0
        truncated = False

        def take(frames: Any) -> bool:
            nonlocal total, truncated
            for out in frames if isinstance(frames, list) else [frames]:
                if out is None:
                    continue
                data = bytes(out.planes[0])[: out.samples * 2]
                chunks.append(data)
                total += len(data)
                if total >= limit:
                    truncated = True
                    return False
            return True

        try:
            for frame in container.decode(stream):
                frame.pts = None
                if not take(resampler.resample(frame)):
                    break
            else:
                take(resampler.resample(None))
        except av.error.FFmpegError as exc:  # type: ignore[attr-defined]
            if not chunks:
                raise DecodeError(f"The sound in {path.name} could not be decoded: {exc}") from exc
            log.info("multimodal: audio decode stopped early in %s (%s)", path.name, exc)
        pcm = b"".join(chunks)[:limit]
    finally:
        container.close()
    return DecodedAudio(pcm=pcm, seconds=len(pcm) / 2 / SAMPLE_RATE, truncated=truncated)


@dataclass(frozen=True)
class Frame:
    seconds: float
    jpeg: bytes

    def data_url(self) -> str:
        return "data:image/jpeg;base64," + base64.b64encode(self.jpeg).decode("ascii")


@dataclass(frozen=True)
class VideoInfo:
    seconds: float
    width: int
    height: int
    has_audio: bool


def sample_times(duration: float, count: int) -> list[float]:
    """``count`` evenly spaced times inside ``[0, duration)``: the middle of each slice."""
    if duration <= 0 or count <= 0:
        return [0.0]
    count = max(1, count)
    step = duration / count
    return [round(step * (index + 0.5), 3) for index in range(count)]


def probe_video(path: Path) -> VideoInfo:
    av = _av()
    try:
        container = av.open(str(path))
    except (av.error.FFmpegError, OSError) as exc:  # type: ignore[attr-defined]
        raise DecodeError(f"{path.name} could not be opened as a video: {exc}") from exc
    try:
        if not container.streams.video:
            raise DecodeError(f"{path.name} has no video stream.")
        stream = container.streams.video[0]
        seconds = _duration_s(container, stream) or 0.0
        return VideoInfo(
            seconds=seconds,
            width=int(stream.codec_context.width or 0),
            height=int(stream.codec_context.height or 0),
            has_audio=bool(container.streams.audio),
        )
    finally:
        container.close()


def sample_frames(path: Path, times: list[float], max_side: int) -> list[Frame]:
    """One JPEG per requested time: the first decoded frame at or after it.

    Seeks to the keyframe before each time and decodes forward, so a long video
    costs a few keyframe intervals per sample instead of a full decode.
    """
    av = _av()
    frames: list[Frame] = []
    try:
        container = av.open(str(path))
    except (av.error.FFmpegError, OSError) as exc:  # type: ignore[attr-defined]
        raise DecodeError(f"{path.name} could not be opened as a video: {exc}") from exc
    try:
        stream = container.streams.video[0]
        stream.thread_type = "AUTO"
        base = stream.time_base
        for wanted in times:
            picked = None
            try:
                if base is not None:
                    container.seek(int(wanted / base), stream=stream, backward=True)
                for frame in container.decode(stream):
                    stamp = frame.time if frame.time is not None else wanted
                    picked = frame
                    if stamp + 1e-3 >= wanted:
                        break
            except av.error.FFmpegError as exc:  # type: ignore[attr-defined]
                log.info("multimodal: no frame at %.2fs in %s (%s)", wanted, path.name, exc)
            if picked is None:
                continue
            stamp = picked.time if picked.time is not None else wanted
            frames.append(Frame(seconds=float(stamp), jpeg=_jpeg(picked.to_image(), max_side)))
    finally:
        container.close()
    if not frames:
        raise DecodeError(f"No frame of {path.name} could be decoded.")
    return frames


def clock(seconds: float) -> str:
    """``73.4`` -> ``1:13``."""
    total = max(0, int(round(seconds)))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"
