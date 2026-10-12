"""Optional acoustic echo cancellation for the desktop microphone.

Raw PortAudio capture has no portable echo cancellation, which is why the
desktop voice surfaces lean on a stack of guards against hearing their own
voice: the barge-in detector's adaptive echo floor and output-envelope
correlation (``jarvis.audio.echo_reference``), the post-answer input
suppression window, and the self-echo text guard. They work, but they work by
making the microphone deaf or strict while the assistant talks, which is what
makes interrupting it hard on open speakers.

When the optional ``livekit`` package is installed in the Jarvis environment
(``pip install livekit``: Apache-2.0, wheels for Windows, macOS and Linux; it
is not a dependency of any install profile) and ``[audio].echo_cancellation``
is on (the default), its WebRTC audio processing module removes the
assistant's own voice from the microphone signal itself:

* every block ``AudioPlayer`` writes to the speakers is fed in as the far-end
  reference (:func:`feed_playback`, next to the envelope record), and
* every block a ``MicrophoneCapture`` receives is processed against it
  (:meth:`CaptureEchoCanceller.process`), in the capture callback, so every
  consumer of that stream (wake, VAD, barge-in) hears the cleaned signal.

The existing guards stay in place and simply see far less echo. Without the
package, or with the setting off, nothing here runs: :func:`open_capture`
returns ``None`` and capture is byte-for-byte unchanged. Local and free: no
network, no account, no model download.

Each capture stream owns its own processing module (one native engine per
caller, never shared, AP-24); the playback reference is broadcast to all of
them.
"""

from __future__ import annotations

import logging
import threading
import weakref
from typing import Any

import numpy as np

log = logging.getLogger("jarvis.audio.echo_canceller")

#: Sample rates the processing module accepts natively. Anything else (a
#: 22.05 / 24 / 44.1 kHz output device) is resampled to 16 kHz first.
_NATIVE_RATES = (8_000, 16_000, 32_000, 48_000)
_FALLBACK_RATE = 16_000
#: Delay hint bounds. The hint is the output-device latency plus a small
#: capture allowance; the canceller's own delay estimation refines it.
_CAPTURE_LATENCY_MS = 10
_MAX_DELAY_MS = 500

_lock = threading.Lock()
_enabled = False
_rtc: Any = None
_import_failed = False
_warm_thread: threading.Thread | None = None
_cancellers: weakref.WeakSet[CaptureEchoCanceller] = weakref.WeakSet()


def _load_rtc() -> Any:
    """Import ``livekit.rtc`` once; ``None`` when the package is not installed."""
    global _rtc, _import_failed
    with _lock:
        if _rtc is not None or _import_failed:
            return _rtc
    try:
        from livekit import rtc  # noqa: PLC0415 - optional package, lazy on purpose
    except Exception as exc:  # noqa: BLE001 - any import failure means "not available"
        with _lock:
            _import_failed = True
        log.info(
            "Echo cancellation is on but its optional engine is not installed "
            "(%s: %s); the microphone runs without it.",
            type(exc).__name__,
            exc,
        )
        return None
    with _lock:
        _rtc = rtc
    return rtc


def configure(enabled: bool) -> None:
    """Apply ``[audio].echo_cancellation``; captures opened afterwards follow it.

    Turning it on starts loading the optional engine in a background thread so
    the first microphone open does not pay the import.
    """
    global _enabled, _warm_thread
    with _lock:
        _enabled = bool(enabled)
        start_warm = _enabled and _rtc is None and not _import_failed and _warm_thread is None
        if start_warm:
            _warm_thread = threading.Thread(
                target=_load_rtc, name="echo-canceller-warm", daemon=True
            )
    if start_warm and _warm_thread is not None:
        _warm_thread.start()


def enabled() -> bool:
    """True when ``[audio].echo_cancellation`` asked for it (installed or not)."""
    with _lock:
        return _enabled


def running() -> bool:
    """True when it is on AND the engine loaded: new captures get cancelled."""
    with _lock:
        return _enabled and _rtc is not None


def is_active() -> bool:
    """True while at least one capture stream is being echo-cancelled."""
    with _lock:
        return len(_cancellers) > 0


def open_capture(sample_rate: int, channels: int) -> CaptureEchoCanceller | None:
    """A canceller for one capture stream, or ``None`` when it cannot run.

    May import the optional engine, so call it off the event loop.
    """
    with _lock:
        if not _enabled:
            return None
    if channels != 1 or sample_rate not in _NATIVE_RATES:
        return None
    rtc = _load_rtc()
    if rtc is None:
        return None
    try:
        canceller = CaptureEchoCanceller(rtc, sample_rate)
    except Exception as exc:  # noqa: BLE001 - a broken engine must not take the mic down
        log.warning("Echo cancellation unavailable for this microphone: %s", exc)
        return None
    with _lock:
        _cancellers.add(canceller)
    return canceller


def feed_playback(block: np.ndarray, sample_rate: int, output_latency_s: float) -> None:
    """Hand one played output block to every live canceller as far-end audio.

    ``block`` is the float32 block exactly as written to the device (frames x
    channels or mono). A no-op costing one lock when nothing is cancelling.
    """
    with _lock:
        targets = list(_cancellers)
    if not targets or block.size == 0 or sample_rate <= 0:
        return
    mono = block if block.ndim == 1 else block.mean(axis=1)
    if sample_rate not in _NATIVE_RATES:
        mono = _resample_linear(mono, sample_rate, _FALLBACK_RATE)
        sample_rate = _FALLBACK_RATE
    pcm = (np.clip(mono, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    delay_ms = int(round(max(0.0, output_latency_s) * 1000.0)) + _CAPTURE_LATENCY_MS
    for canceller in targets:
        try:
            canceller.feed_reverse(pcm, sample_rate, delay_ms)
        except Exception as exc:  # noqa: BLE001 - one broken stream must not stop playback
            log.warning(
                "Echo cancellation reference failed; disabling it for that microphone: %s",
                exc,
            )
            canceller.close()


def _resample_linear(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Cheap linear resampling; the far-end reference needs no studio quality."""
    if samples.size == 0 or src_rate == dst_rate:
        return samples
    n_out = max(1, int(round(samples.size * dst_rate / src_rate)))
    positions = np.linspace(0.0, samples.size - 1, n_out)
    return np.interp(positions, np.arange(samples.size), samples).astype(np.float32)


class CaptureEchoCanceller:
    """Echo cancellation for ONE microphone stream (16 kHz-class mono int16).

    The processing module consumes exact 10 ms frames, so both directions keep
    a sub-frame remainder between calls. Thread-safe: the capture callback and
    the player's writer thread call in concurrently.
    """

    def __init__(self, rtc: Any, sample_rate: int) -> None:
        self._rtc = rtc
        self._sample_rate = int(sample_rate)
        self._frame_samples = self._sample_rate // 100
        self._apm = rtc.AudioProcessingModule(echo_cancellation=True)
        self._lock = threading.Lock()
        self._capture_rest = b""
        self._reverse_rest = b""
        self._reverse_rate = 0
        self._delay_ms = _CAPTURE_LATENCY_MS
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def process(self, pcm: bytes) -> bytes:
        """Return the echo-cancelled version of captured int16 mono ``pcm``.

        Output is whole 10 ms frames, so a call may return up to one frame
        less (or more) than it was given; the stream as a whole is preserved.
        After :meth:`close` the audio passes through untouched.
        """
        with self._lock:
            if self._closed:
                rest, self._capture_rest = self._capture_rest, b""
                return rest + pcm
            data = self._capture_rest + pcm
            frame_bytes = self._frame_samples * 2
            usable = len(data) - (len(data) % frame_bytes)
            self._capture_rest = data[usable:]
            out = bytearray()
            for start in range(0, usable, frame_bytes):
                raw = data[start : start + frame_bytes]
                frame = self._rtc.AudioFrame(raw, self._sample_rate, 1, self._frame_samples)
                self._apm.set_stream_delay_ms(self._delay_ms)
                self._apm.process_stream(frame)
                cleaned = bytearray(bytes(frame.data))
                if not any(cleaned) and any(raw):
                    # Fully suppressed echo can round to exact digital zeros,
                    # which the capture's silence guard reads as "the OS is
                    # withholding the microphone". One LSB keeps that signal
                    # meaning what it says (-90 dBFS, inaudible to any model).
                    cleaned[0] = 1
                out += cleaned
            return bytes(out)

    def feed_reverse(self, pcm: bytes, sample_rate: int, delay_ms: int) -> None:
        """Feed played int16 mono audio at ``sample_rate`` as the far-end signal."""
        with self._lock:
            if self._closed:
                return
            self._delay_ms = max(0, min(_MAX_DELAY_MS, int(delay_ms)))
            if sample_rate != self._reverse_rate:
                self._reverse_rest = b""
                self._reverse_rate = sample_rate
            frame_samples = sample_rate // 100
            frame_bytes = frame_samples * 2
            data = self._reverse_rest + pcm
            usable = len(data) - (len(data) % frame_bytes)
            self._reverse_rest = data[usable:]
            for start in range(0, usable, frame_bytes):
                frame = self._rtc.AudioFrame(
                    data[start : start + frame_bytes], sample_rate, 1, frame_samples
                )
                self._apm.process_reverse_stream(frame)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            # Release the native module now rather than at interpreter exit,
            # when the engine's runtime may already be gone.
            self._apm = None
        with _lock:
            _cancellers.discard(self)


__all__ = [
    "CaptureEchoCanceller",
    "configure",
    "enabled",
    "feed_playback",
    "is_active",
    "running",
    "open_capture",
]
