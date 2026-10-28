"""``[multimodal]`` in ``jarvis.toml``: where the local media models are, and the limits.

Read straight from the TOML file instead of through ``JarvisConfig`` so the
section stays optional and self-contained: an install without it gets the
defaults below, which auto-detect a local ``llama-server`` and use the
faster-whisper model Jarvis's speech input already downloaded.

Example::

    [multimodal]
    # "" = find a running llama-server on this computer that can see / hear.
    server_url = "http://127.0.0.1:11439"
    # Extra loopback ports probed on top of the usual llama.cpp ports.
    extra_ports = [11439]
    # auto | whisper | server
    transcriber = "auto"
    whisper_model = "small"
    allowed_dirs = ["D:/Media"]
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

#: The port the decision doc's ``llama-server`` command uses for the media
#: model, next to the owner's Qwen (11437) and Gemma (11438) servers.
DEFAULT_MEDIA_PORT = 11439

TRANSCRIBERS = ("auto", "whisper", "server")


@dataclass(frozen=True)
class MediaConfig:
    enabled: bool = True
    #: A loopback URL of an OpenAI-compatible server with image/audio input.
    #: Empty: auto-detect (``jarvis.brain.local_servers.scan``).
    server_url: str = ""
    #: The model id sent in requests. Empty: the first model the server lists.
    model: str = ""
    #: What the configured server can take when it cannot say so itself
    #: (``llama-server`` reports it; Ollama and LM Studio do not).
    server_modalities: tuple[str, ...] = ()
    extra_ports: tuple[int, ...] = (DEFAULT_MEDIA_PORT,)
    transcriber: str = "auto"
    #: Empty: ``[stt].model`` when it is downloaded, else ``[stt].wake_model``.
    whisper_model: str = ""
    whisper_device: str = "cpu"
    #: Folders a tool may read besides the defaults (``files.default_roots``).
    allowed_dirs: tuple[str, ...] = ()
    max_file_mb: float = 500.0
    max_image_mb: float = 40.0
    max_audio_seconds: float = 900.0
    max_video_seconds: float = 1200.0
    #: The longest clip an audio model takes in one piece (Gemma 4: 30 s).
    audio_chunk_seconds: float = 30.0
    video_frames: int = 8
    max_video_frames: int = 16
    image_max_side: int = 1024
    frame_max_side: int = 768
    max_tokens: int = 1024
    timeout_seconds: float = 240.0
    extra: dict[str, Any] = field(default_factory=dict, compare=False)


def _coerce(name: str, value: Any, default: Any) -> Any:
    if isinstance(default, bool):
        return bool(value)
    if isinstance(default, int):
        return int(value)
    if isinstance(default, float):
        return float(value)
    if isinstance(default, tuple):
        items = value if isinstance(value, list | tuple) else [value]
        if name == "extra_ports":
            return tuple(int(item) for item in items)
        return tuple(str(item).strip().lower() if name == "server_modalities" else str(item)
                     for item in items if str(item).strip())
    return str(value).strip()


def from_mapping(raw: dict[str, Any] | None) -> MediaConfig:
    """A :class:`MediaConfig` from the ``[multimodal]`` table; bad values keep defaults."""
    defaults = MediaConfig()
    values: dict[str, Any] = {}
    unknown: dict[str, Any] = {}
    known = {f.name: getattr(defaults, f.name) for f in fields(MediaConfig) if f.name != "extra"}
    for key, value in (raw or {}).items():
        if key not in known:
            unknown[key] = value
            continue
        try:
            values[key] = _coerce(key, value, known[key])
        except (TypeError, ValueError):
            log.warning("[multimodal].%s = %r is not valid; using the default", key, value)
    if values.get("transcriber", "auto") not in TRANSCRIBERS:
        log.warning("[multimodal].transcriber must be one of %s; using auto", TRANSCRIBERS)
        values["transcriber"] = "auto"
    if values.get("video_frames", 1) < 1:
        values["video_frames"] = defaults.video_frames
    return MediaConfig(**values, extra=unknown)


def load_media_config(path: Path | None = None) -> MediaConfig:
    """``[multimodal]`` from the active ``jarvis.toml``; defaults when absent or unreadable."""
    if path is None:
        from jarvis.core.config import resolve_config_path

        path = resolve_config_path()
    try:
        with path.open("rb") as handle:
            data = tomllib.load(handle)
    except FileNotFoundError:  # no config file: every default applies
        return MediaConfig()
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log.warning("multimodal: %s could not be read (%s); using defaults", path, exc)
        return MediaConfig()
    section = data.get("multimodal")
    return from_mapping(section if isinstance(section, dict) else None)


def stt_whisper_candidates(path: Path | None = None) -> list[str]:
    """``[stt].model`` then ``[stt].wake_model``: the Whisper checkpoints Jarvis already uses."""
    try:
        from jarvis.core.config import load_config

        stt = load_config(path).stt if path else load_config().stt
    except Exception as exc:  # noqa: BLE001 — a broken main config only costs the default names
        log.info("multimodal: speech config unavailable (%s)", exc)
        return ["small", "base"]
    names = [str(getattr(stt, "model", "") or ""), str(getattr(stt, "wake_model", "") or "")]
    return [name for name in dict.fromkeys(names + ["small", "base"]) if name]
