"""Classic-pipeline barge monitor: thinking-phase arming and the resume cutoff.

Live 2026-10-12 (Windows laptop, speakers + built-in mic, ``[voice].mode =
"pipeline"``): speaking while Jarvis thought (30-120 s of PROCESSING) or spoke
never interrupted him. The stall guard's thinking monitor built its detector
WITH the playback probe, and ``DesktopRealtimeBargeInDetector.feed`` returns
None for as long as nothing plays — the exact trap the realtime thinking
detector documents — so it could never fire during the silent wait.
"""

from __future__ import annotations

import asyncio
import time

import pytest

import jarvis.speech.pipeline as pipeline_mod
from jarvis.core.protocols import AudioChunk
from jarvis.speech.pipeline import SpeechPipeline


def _pipe() -> SpeechPipeline:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._input_device = None
    pipe._input_priority = ()
    pipe._muted = False
    pipe._input_suppressed_until_ns = 0
    return pipe


class _Detector:
    instances: list[_Detector] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.fed: list[bytes] = []
        self.starts = 0
        self.confirm_after = 3
        _Detector.instances.append(self)

    def warmup(self) -> None:
        return None

    def start_output(self) -> None:
        self.starts += 1

    def feed(self, pcm: bytes) -> bytes | None:
        self.fed.append(pcm)
        if len(self.fed) >= self.confirm_after:
            return b"\x00\x00" * 16_000  # one second of confirmed speech
        return None

    def stats(self) -> dict[str, object]:
        return {"frames": len(self.fed)}


class _Mic:
    def __init__(self, chunks: int) -> None:
        self._chunks = chunks

    async def __aenter__(self) -> _Mic:
        return self

    async def __aexit__(self, *_exc: object) -> bool:
        return False

    async def stream(self):
        for index in range(self._chunks):
            yield AudioChunk(
                pcm=bytes([index + 1, 0]) * 32,
                sample_rate=16_000,
                timestamp_ns=time.time_ns(),
            )


@pytest.fixture(autouse=True)
def _fake_detector(monkeypatch: pytest.MonkeyPatch) -> None:
    _Detector.instances = []
    monkeypatch.setattr(
        "jarvis.realtime.desktop.DesktopRealtimeBargeInDetector", _Detector
    )


@pytest.mark.asyncio
async def test_thinking_monitor_listens_without_the_playback_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipe = _pipe()
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", lambda **_kw: _Mic(10))
    monkeypatch.setattr(pipeline_mod.level_tap, "playback_active", lambda: False)

    before_ns = time.time_ns()
    assert await pipe._barge_monitor(
        grace_s=0.3, respect_input_suppression=True, thinking=True
    ) is True

    detector = _Detector.instances[0]
    # No probe: with one, feed() is dead for the whole silent wait.
    assert detector.kwargs["output_active"] is None
    assert not detector.kwargs.get("echo_tracking")
    assert len(detector.fed) == 3
    # The session's buffered mic resumes just before the interruption began:
    # about one second of confirmed speech plus the margin, not the whole wait.
    cutoff = pipe._input_suppressed_until_ns
    assert before_ns - 2_000_000_000 < cutoff < before_ns


@pytest.mark.asyncio
async def test_thinking_monitor_skips_audible_output_and_rearms(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipe = _pipe()
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", lambda **_kw: _Mic(10))
    monkeypatch.setattr(pipeline_mod, "_THINKING_BARGE_POST_OUTPUT_S", 0.0)
    playing = iter([True, True, True])
    monkeypatch.setattr(
        pipeline_mod.level_tap, "playback_active", lambda: next(playing, False)
    )

    assert await pipe._barge_monitor(
        grace_s=0.3, respect_input_suppression=True, thinking=True
    ) is True

    detector = _Detector.instances[0]
    # An instant ack playing mid-think never reaches the detector …
    assert detector.fed[0] == bytes([4, 0]) * 32
    # … and once it is over the detector re-arms with a fresh grace window.
    assert detector.starts == 2


@pytest.mark.asyncio
async def test_thinking_monitor_honours_the_post_tts_input_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipe = _pipe()
    pipe._input_suppressed_until_ns = time.time_ns() + 60_000_000_000
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", lambda **_kw: _Mic(10))
    monkeypatch.setattr(pipeline_mod.level_tap, "playback_active", lambda: False)

    assert await pipe._barge_monitor(
        grace_s=0.3, respect_input_suppression=True, thinking=True
    ) is False
    assert _Detector.instances[0].fed == []


@pytest.mark.asyncio
async def test_playback_monitor_keeps_the_probe_and_tracks_the_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipe = _pipe()
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", lambda **_kw: _Mic(10))

    assert await pipe._barge_monitor() is True

    detector = _Detector.instances[0]
    assert detector.kwargs["output_active"] is pipeline_mod.level_tap.playback_active
    assert detector.kwargs["echo_tracking"] is True
    assert pipe._input_suppressed_until_ns > 0


@pytest.mark.asyncio
async def test_monitor_without_barge_logs_a_diagnostics_line(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    pipe = _pipe()
    monkeypatch.setattr(pipeline_mod, "MicrophoneCapture", lambda **_kw: _Mic(2))

    with caplog.at_level("INFO", logger=pipeline_mod.log.name):
        assert await pipe._barge_monitor() is False

    assert any(
        "barge monitor (speaking) ended without interrupt: frames=2" in r.message
        for r in caplog.records
    )
    assert pipe._input_suppressed_until_ns == 0


def _chunk(pcm: bytes, timestamp_ns: int) -> AudioChunk:
    return AudioChunk(pcm=pcm, sample_rate=16_000, timestamp_ns=timestamp_ns)


@pytest.mark.asyncio
async def test_barge_cutoff_lets_the_session_skip_a_backlog_past_the_replay_window() -> None:
    """A barge after a long think must not end the session.

    The session stream is not read while Jarvis thinks or speaks; once the
    backlog outgrows the 30 s replay window the buffer refuses to skip it
    unless the post-TTS input lock covers the evicted frames. A barge arms no
    post-TTS lock, so before this fix a barge after a long think ended the
    call with "Voice input exceeded the 30-second replay window".
    """
    now_ns = time.time_ns()
    stale_ns = now_ns - 40_000_000_000

    async def _consume_after_backlog(pipe: SpeechPipeline) -> bytes:
        buffer = pipeline_mod._SessionInputBuffer(max_buffer_bytes=8)  # noqa: SLF001
        buffer.put(_chunk(b"turn", stale_ns))
        stream = buffer.stream(
            discard_before_ns=lambda: int(pipe._input_suppressed_until_ns)
        )
        assert (await stream.__anext__()).pcm == b"turn"
        # The thinking wait: frames pile up unread and evict each other.
        for index in range(6):
            buffer.put(_chunk(b"wait", stale_ns + index))
        buffer.put(_chunk(b"stop", time.time_ns() + 1_000_000_000))
        chunk = await asyncio.wait_for(stream.__anext__(), timeout=1.0)
        return chunk.pcm

    with pytest.raises(RuntimeError, match="30-second replay window"):
        await _consume_after_backlog(_pipe())

    pipe = _pipe()
    pipe._resume_session_input_at_barge(b"\x00\x00" * 16_000, "thinking")
    assert await _consume_after_backlog(pipe) in {b"wait", b"stop"}
