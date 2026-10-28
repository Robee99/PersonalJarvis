"""Speech in a recording, as text: faster-whisper first, the local audio model second.

faster-whisper is the recognizer Jarvis's own local speech input uses
(``jarvis/plugins/stt/fwhisper.py``), so on the owner's PC its checkpoint is
usually already downloaded. It handles long recordings in one pass and gives
timestamps. This module builds its OWN short-lived ``WhisperModel`` instead of
borrowing the live voice one: that instance is shared with the wake-word loop
and refuses a second caller (``TranscribeBusy``), and a file transcription must
not make Jarvis deaf. The model is loaded from the local cache only
(``local_files_only``), never downloaded mid-call, and released afterwards so
a machine with little free RAM gets it back.

Without Whisper, an audio-capable ``llama-server`` model (Gemma 4 E4B,
Qwen2.5-Omni, Voxtral) transcribes the recording in clips of at most
``[multimodal].audio_chunk_seconds`` (Gemma 4 takes 30 s at a time).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from jarvis.multimodal import backend
from jarvis.multimodal.config import MediaConfig, stt_whisper_candidates
from jarvis.multimodal.decode import SAMPLE_RATE, clock, pcm_to_wav

log = logging.getLogger(__name__)

#: One transcription at a time: a second Whisper engine next to the voice one
#: is already the most this machine's RAM should carry.
_LOCK = asyncio.Lock()


class TranscriberUnavailable(RuntimeError):
    """Neither Whisper nor an audio model is available; ``str(exc)`` says how to fix it."""


@dataclass
class Transcript:
    text: str
    engine: str
    language: str = ""
    segments: list[tuple[float, float, str]] = field(default_factory=list)

    def timed(self, limit: int = 400) -> str:
        """The transcript with ``[m:ss]`` stamps per segment when there are any."""
        if not self.segments:
            return self.text
        lines = [f"[{clock(start)}] {text.strip()}" for start, _end, text in self.segments[:limit]]
        return "\n".join(line for line in lines if line.strip())


def whisper_model_name(cfg: MediaConfig) -> str | None:
    """The first Whisper checkpoint on disk: the configured one, else the speech ones."""
    from jarvis.speech.local_models import runtime_installed, whisper_model_cached

    if not runtime_installed("faster-whisper"):
        return None
    names = [cfg.whisper_model] if cfg.whisper_model else stt_whisper_candidates()
    for name in names:
        if name and whisper_model_cached(name):
            return name
    return None


def _whisper_sync(pcm: bytes, model_name: str, device: str, language: str | None) -> Transcript:
    import numpy as np

    from jarvis.plugins.stt.fwhisper import inference_only_import_shield

    with inference_only_import_shield():
        from faster_whisper import WhisperModel

    compute = "int8" if device == "cpu" else "int8_float16"
    model: Any = WhisperModel(
        model_name, device=device, compute_type=compute, local_files_only=True
    )
    try:
        audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments_iter, info = model.transcribe(
            audio,
            language=language or None,
            beam_size=5,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        segments = [(float(s.start), float(s.end), str(s.text)) for s in segments_iter]
    finally:
        del model
    text = " ".join(seg[2].strip() for seg in segments).strip()
    return Transcript(
        text=text,
        engine=f"faster-whisper {model_name} ({device})",
        language=str(getattr(info, "language", "") or ""),
        segments=segments,
    )


def chunks(pcm: bytes, seconds: float) -> list[tuple[float, bytes]]:
    """``(start seconds, pcm)`` clips of at most ``seconds`` each."""
    size = max(2, int(seconds * SAMPLE_RATE) * 2)
    return [(offset / 2 / SAMPLE_RATE, pcm[offset : offset + size])
            for offset in range(0, len(pcm), size)]


async def _server_transcript(cfg: MediaConfig, pcm: bytes, language: str | None) -> Transcript:
    server = await backend.find_server(cfg, "audio")
    instruction = (
        "Transcribe the speech in this audio clip word for word"
        + (f" (language: {language})" if language else "")
        + ". Output only the transcript. If there is no speech, output: [no speech]"
    )
    pieces: list[tuple[float, float, str]] = []
    for start, clip in chunks(pcm, cfg.audio_chunk_seconds):
        if len(clip) < SAMPLE_RATE // 5:  # under 0.1 s: nothing to hear
            continue
        text = await backend.complete(
            server,
            [backend.audio_part(pcm_to_wav(clip)), backend.text_part(instruction)],
            max_tokens=cfg.max_tokens,
            timeout_s=cfg.timeout_seconds,
        )
        if text.strip().lower().strip(".") != "[no speech]":
            pieces.append((start, start + len(clip) / 2 / SAMPLE_RATE, text.strip()))
    return Transcript(
        text=" ".join(p[2] for p in pieces).strip(),
        engine=f"{server.model or 'local audio model'} at {server.root}",
        segments=pieces if len(pieces) > 1 else [],
    )


async def transcribe(cfg: MediaConfig, pcm: bytes, language: str | None = None) -> Transcript:
    """The speech in ``pcm`` (mono 16 kHz s16) as text, by the configured transcriber."""
    async with _LOCK:
        whisper = None
        if cfg.transcriber in ("auto", "whisper"):
            whisper = await asyncio.to_thread(whisper_model_name, cfg)
        if whisper is not None:
            device = cfg.whisper_device or "cpu"
            try:
                return await asyncio.to_thread(_whisper_sync, pcm, whisper, device, language)
            except Exception as exc:  # noqa: BLE001 — engine/CUDA failures are varied
                if device == "cpu":
                    raise
                log.warning("multimodal: Whisper on %s failed (%s); retrying on CPU", device, exc)
                return await asyncio.to_thread(_whisper_sync, pcm, whisper, "cpu", language)
        if cfg.transcriber == "whisper":
            raise TranscriberUnavailable(
                "faster-whisper is not installed or no Whisper model is downloaded. Turn on local "
                "speech input in Jarvis (it downloads the model), or set [multimodal].transcriber "
                "= \"auto\" to use a local audio model."
            )
        try:
            return await _server_transcript(cfg, pcm, language)
        except backend.BackendUnavailable as exc:
            reason = str(exc)
            raise TranscriberUnavailable(
                "No local speech recognizer is set up: faster-whisper has no downloaded model "
                "(turn on local speech input in Jarvis), and " + reason[:1].lower() + reason[1:]
            ) from exc
