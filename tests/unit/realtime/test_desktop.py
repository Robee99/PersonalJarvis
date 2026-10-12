from __future__ import annotations

import asyncio

import numpy as np
import pytest

from jarvis.audio.vad import VAD_FRAME_SAMPLES
from jarvis.realtime.desktop import (
    DesktopRealtimeBargeInDetector,
    DesktopRealtimePlayback,
)


class FakePlayer:
    def __init__(self) -> None:
        self.chunks = []
        self.stopped = 0

    async def play_chunks(self, chunks) -> None:
        async for chunk in chunks:
            self.chunks.append(chunk)

    def stop(self) -> None:
        self.stopped += 1


class FakeVadModel:
    def __init__(self, probabilities: list[float]) -> None:
        self.probabilities = list(probabilities)
        self.warmed = False

    def _ensure_model(self) -> None:
        self.warmed = True

    def _prob(self, _frame: np.ndarray) -> float:
        return self.probabilities.pop(0)


def _pcm_frames(*amplitudes: int) -> bytes:
    return np.concatenate(
        [np.full(VAD_FRAME_SAMPLES, value, dtype=np.dtype("<i2")) for value in amplitudes]
    ).tobytes()


def test_cpu_barge_detector_returns_prespeech_and_confirmed_user_audio() -> None:
    model = FakeVadModel([0.1, 0.2, 0.99, 0.99, 0.99])
    detector = DesktopRealtimeBargeInDetector(
        min_frame_rms=0.0,
        grace_s=0,
        consecutive_frames=3,
        pre_speech_frames=2,
        model=model,
    )
    detector.warmup()
    detector.start_output()

    detected = detector.feed(_pcm_frames(1, 2, 3, 4, 5))

    assert model.warmed is True
    assert detected == _pcm_frames(1, 2, 3, 4, 5)
    assert detector.active is False


def test_cpu_barge_detector_rejects_short_speaker_bleed() -> None:
    model = FakeVadModel([0.99, 0.1, 0.99, 0.99, 0.1])
    detector = DesktopRealtimeBargeInDetector(
        min_frame_rms=0.0,
        grace_s=0,
        consecutive_frames=3,
        model=model,
    )
    detector.warmup()
    detector.start_output()

    assert detector.feed(_pcm_frames(1, 2, 3, 4, 5)) is None
    assert detector.active is True


@pytest.mark.asyncio
async def test_turn_uses_one_stream_and_drains_in_order():
    player = FakePlayer()
    playback = DesktopRealtimePlayback(player)

    await playback.send_binary(b"\x01\x00" * 8)
    await playback.send_binary(b"\x02\x00" * 8)
    assert await playback.finish_turn() is True

    assert [chunk.pcm for chunk in player.chunks] == [
        b"\x01\x00" * 8,
        b"\x02\x00" * 8,
    ]
    assert all(chunk.sample_rate == 24_000 for chunk in player.chunks)


@pytest.mark.asyncio
async def test_cancel_stops_player_and_discards_queued_audio():
    gate = asyncio.Event()

    class SlowPlayer(FakePlayer):
        async def play_chunks(self, chunks) -> None:
            async for chunk in chunks:
                await gate.wait()
                self.chunks.append(chunk)

    player = SlowPlayer()
    playback = DesktopRealtimePlayback(player)
    await playback.send_binary(b"\x01\x00" * 8)

    await playback.cancel()

    assert player.stopped == 1
    assert player.chunks == []


@pytest.mark.asyncio
async def test_close_is_terminal_and_rejects_late_provider_audio() -> None:
    """A dead desktop surface cannot be reopened by a stale callback."""

    player = FakePlayer()
    playback = DesktopRealtimePlayback(player)

    await playback.send_binary(b"\x01\x00" * 8)
    await playback.finish_turn()
    await playback.close()
    await playback.send_binary(b"\x09\x00" * 8)
    await playback.finish_turn()

    assert [chunk.pcm for chunk in player.chunks] == [b"\x01\x00" * 8]
    assert player.stopped == 1


@pytest.mark.asyncio
async def test_cancel_can_interrupt_a_turn_already_draining() -> None:
    stop_event = asyncio.Event()

    class SlowPlayer(FakePlayer):
        def stop(self) -> None:
            super().stop()
            stop_event.set()

        async def play_chunks(self, chunks) -> None:
            async for _chunk in chunks:
                await stop_event.wait()
                return

    player = SlowPlayer()
    playback = DesktopRealtimePlayback(player)
    await playback.send_binary(b"\x01\x00" * 8)
    drain = asyncio.create_task(playback.finish_turn())
    await asyncio.sleep(0)

    await playback.cancel()
    assert await drain is False

    assert player.stopped == 1
    assert player.chunks == []


@pytest.mark.asyncio
async def test_cancel_during_drain_treats_stopped_stream_as_expected() -> None:
    stop_event = asyncio.Event()

    class AbortedStreamPlayer(FakePlayer):
        def stop(self) -> None:
            super().stop()
            stop_event.set()

        async def play_chunks(self, chunks) -> None:
            async for _chunk in chunks:
                await stop_event.wait()
                raise RuntimeError("Stream is stopped [PaErrorCode -9983]")

    player = AbortedStreamPlayer()
    playback = DesktopRealtimePlayback(player)
    await playback.send_binary(b"\x01\x00" * 8)
    drain = asyncio.create_task(playback.finish_turn())
    await asyncio.sleep(0)

    await playback.cancel()
    assert await drain is False

    assert player.stopped == 1


@pytest.mark.asyncio
async def test_finish_turn_still_surfaces_an_unrelated_playback_failure() -> None:
    class FailingPlayer(FakePlayer):
        async def play_chunks(self, chunks) -> None:
            async for _chunk in chunks:
                raise RuntimeError("output device disappeared")

    playback = DesktopRealtimePlayback(FailingPlayer())
    await playback.send_binary(b"\x01\x00" * 8)

    with pytest.raises(RuntimeError, match="output device disappeared"):
        await playback.finish_turn()


@pytest.mark.asyncio
async def test_finish_turn_does_not_confirm_missing_or_timed_out_audio() -> None:
    class StalledPlayer(FakePlayer):
        async def play_chunks(self, chunks) -> None:
            await asyncio.Event().wait()

    player = StalledPlayer()
    playback = DesktopRealtimePlayback(player)
    assert await playback.finish_turn() is False
    await playback.send_binary(b"\x01\x00" * 8)
    playback._finish_timeout_s = 0.01
    assert await playback.finish_turn() is False
    assert player.stopped == 1


@pytest.mark.asyncio
async def test_handshake_sample_rate_is_used_for_the_next_turn():
    player = FakePlayer()
    playback = DesktopRealtimePlayback(player)

    playback.set_sample_rate(16_000)
    await playback.send_binary(b"\x01\x00" * 8)
    await playback.finish_turn()

    assert [chunk.sample_rate for chunk in player.chunks] == [16_000]


@pytest.mark.asyncio
async def test_playback_waits_for_the_jitter_buffer_before_the_first_word() -> None:
    """A turn banks a reserve first, so a provider pause has something to eat.

    Live forensic 2026-07-27: gemini-live went quiet for 405-686 ms mid-reply
    ("the provider sent no audio for this span") and the speaker ran dry.
    """
    player = FakePlayer()
    # 24 kHz mono int16 = 48 bytes per millisecond; 100 ms of reserve = 4800 B.
    playback = DesktopRealtimePlayback(
        player, prebuffer_ms=100, prebuffer_timeout_ms=10_000
    )

    await playback.send_binary(b"\x01\x00" * 24)  # ~1 ms — nowhere near full
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert player.chunks == [], "playback must not start on a bare first chunk"

    await playback.send_binary(b"\x02\x00" * 2_400)  # tips it over 100 ms
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    await playback.finish_turn()
    assert [chunk.pcm for chunk in player.chunks] == [
        b"\x01\x00" * 24,
        b"\x02\x00" * 2_400,
    ], "the reserve delays playback; it never drops or reorders audio"


@pytest.mark.asyncio
async def test_short_reply_is_not_held_back_by_the_jitter_buffer() -> None:
    """A reply shorter than the reserve still plays — the sentinel releases it."""
    player = FakePlayer()
    playback = DesktopRealtimePlayback(
        player, prebuffer_ms=5_000, prebuffer_timeout_ms=10_000
    )

    await playback.send_binary(b"\x07\x00" * 8)
    await playback.finish_turn()

    assert [chunk.pcm for chunk in player.chunks] == [b"\x07\x00" * 8]


@pytest.mark.asyncio
async def test_jitter_buffer_wait_is_bounded_by_its_timeout() -> None:
    """A provider that trickles audio out still starts speaking on time."""
    player = FakePlayer()
    playback = DesktopRealtimePlayback(
        player, prebuffer_ms=5_000, prebuffer_timeout_ms=30
    )

    await playback.send_binary(b"\x05\x00" * 8)
    await asyncio.sleep(0.15)  # past the timeout, turn still open

    assert [chunk.pcm for chunk in player.chunks] == [b"\x05\x00" * 8]
    await playback.finish_turn()


@pytest.mark.asyncio
async def test_quick_resume_after_a_boundary_skips_the_jitter_buffer() -> None:
    """ChatGPT-Live announces no terminal item (probe-confirmed 2026-08-06),
    so any generation pause beyond the 1.2 s quiescence backstop closes the
    stream MID-ANSWER. Reopening within the resume grace must not rebuild the
    reserve — that rebuilt reserve WAS the audible seam."""
    player = FakePlayer()
    playback = DesktopRealtimePlayback(
        player, prebuffer_ms=5_000, prebuffer_timeout_ms=10_000
    )

    await playback.send_binary(b"\x01\x00" * 8)
    await playback.finish_turn()  # a natural boundary stamps the resume clock

    await playback.send_binary(b"\x02\x00" * 8)  # far below the 5 s reserve
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert [chunk.pcm for chunk in player.chunks] == [
        b"\x01\x00" * 8,
        b"\x02\x00" * 8,
    ], "the same-answer resume must play immediately, reserve skipped"
    await playback.finish_turn()


@pytest.mark.asyncio
async def test_resume_past_the_grace_rebuilds_the_reserve() -> None:
    player = FakePlayer()
    playback = DesktopRealtimePlayback(
        player,
        prebuffer_ms=5_000,
        prebuffer_timeout_ms=10_000,
        resume_without_prebuffer_s=0.0,  # grace disabled = every turn banks
    )

    await playback.send_binary(b"\x01\x00" * 8)
    await playback.finish_turn()

    await playback.send_binary(b"\x02\x00" * 8)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert all(chunk.pcm != b"\x02\x00" * 8 for chunk in player.chunks), (
        "past the grace the reserve applies again"
    )
    await playback.finish_turn()  # the sentinel releases the banked chunk
    assert player.chunks[-1].pcm == b"\x02\x00" * 8


@pytest.mark.asyncio
async def test_barge_in_cancel_does_not_arm_the_resume_grace() -> None:
    """After a barge-in the next reply is a genuinely NEW answer: it deserves
    its jitter reserve, so cancel() must not stamp the resume clock."""
    player = FakePlayer()
    playback = DesktopRealtimePlayback(
        player, prebuffer_ms=5_000, prebuffer_timeout_ms=10_000
    )

    await playback.send_binary(b"\x01\x00" * 8)
    await playback.cancel()

    await playback.send_binary(b"\x02\x00" * 8)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert all(chunk.pcm != b"\x02\x00" * 8 for chunk in player.chunks), (
        "a post-barge-in reply rebuilds its reserve"
    )
    await playback.finish_turn()


def test_energy_pre_gate_skips_onnx_for_quiet_frames() -> None:
    # BUG-062: quiet frames (silence / hiss / moderate speaker echo) must
    # never reach the Silero model — that is both the loop-load fix (stutter
    # on slow CPUs) and the self-barge-in damper on speakers+mic laptops.
    calls: list[int] = []

    class _CountingModel:
        def _ensure_model(self) -> None: ...

        def _prob(self, _frame) -> float:
            calls.append(1)
            return 1.0

    detector = DesktopRealtimeBargeInDetector(
        grace_s=0.0, consecutive_frames=1, model=_CountingModel()
    )
    detector.warmup()
    detector.start_output()
    # Amplitude 100/32768 ≈ 0.003 RMS — below the 0.010 floor.
    assert detector.feed(_pcm_frames(100, 100, 100)) is None
    assert calls == []  # ONNX never consulted


def test_energy_pre_gate_passes_loud_speech_through() -> None:
    calls: list[int] = []

    class _CountingModel:
        def _ensure_model(self) -> None: ...

        def _prob(self, _frame) -> float:
            calls.append(1)
            return 1.0

    detector = DesktopRealtimeBargeInDetector(
        grace_s=0.0, consecutive_frames=1, model=_CountingModel()
    )
    detector.warmup()
    detector.start_output()
    # Amplitude 2000/32768 ≈ 0.061 RMS — normal speaking volume.
    assert detector.feed(_pcm_frames(2000)) is not None
    assert calls  # the model ran and confirmed


# --- BUG-084: adaptive echo floor ----------------------------------------- #
# A FIXED RMS floor cannot cover every speaker/mic coupling: on built-in
# laptop speakers next to the built-in mic the assistant's own voice lands far
# above the static 0.010 gate, Silero (which cannot tell whose voice it hears)
# confirms, and the false barge truncates the answer and seeds the self-talk
# loop. The detector therefore calibrates a per-answer echo floor from the
# grace-window frames (pure speaker echo by construction) and keeps updating
# it from a LAGGED rolling window, so echo at the calibrated loudness is
# gated while a user speaking clearly above it still confirms.


class _AlwaysSpeechModel:
    """Silero stand-in that calls every frame 'speech' — mirrors the real
    model's behavior on the assistant's own voice, so ONLY the energy gate
    can tell echo and user apart."""

    def __init__(self) -> None:
        self.calls = 0

    def _ensure_model(self) -> None: ...

    def _prob(self, _frame) -> float:
        self.calls += 1
        return 1.0


def _echo_calibrated_detector(
    *, echo_amplitude: int, grace_frames: int = 30, **kwargs
) -> tuple[DesktopRealtimeBargeInDetector, _AlwaysSpeechModel]:
    model = _AlwaysSpeechModel()
    detector = DesktopRealtimeBargeInDetector(grace_s=60.0, model=model, **kwargs)
    detector.warmup()
    detector.start_output()
    # Grace window: playback echo only — calibration data, never preroll.
    assert detector.feed(_pcm_frames(*[echo_amplitude] * grace_frames)) is None
    # End the grace period deterministically (no sleeps in unit tests).
    detector._started_at = -1e9
    return detector, model


def test_adaptive_floor_gates_echo_louder_than_the_static_floor() -> None:
    # Echo at amplitude 2000 (~0.061 RMS) sails over the static 0.010 gate —
    # exactly the Mac speakers+mic case. After grace calibration the learned
    # floor (~1.4 × 0.061) must keep gating it: no ONNX call, no confirm.
    detector, model = _echo_calibrated_detector(
        echo_amplitude=2000, consecutive_frames=3
    )
    assert detector.feed(_pcm_frames(*[2000] * 20)) is None
    assert model.calls == 0  # pure echo never reached the model
    # A user speaking clearly above the echo still confirms.
    assert detector.feed(_pcm_frames(*[6000] * 5)) is not None
    assert model.calls >= 3


def test_adaptive_floor_lag_lets_sustained_user_speech_confirm() -> None:
    # Quiet room during grace → floor stays at the static minimum. Sustained
    # genuine speech must then confirm even though its own frames enter the
    # rolling history: the lag excludes them from their own baseline, so
    # speech can never raise its own bar before the confirm run completes.
    detector, model = _echo_calibrated_detector(
        echo_amplitude=100, consecutive_frames=12
    )
    assert detector.feed(_pcm_frames(*[2000] * 12)) is not None
    assert model.calls >= 12


def test_adaptive_floor_disabled_with_zero_min_rms() -> None:
    # min_frame_rms=0.0 is the explicit logic-test / opt-out hook: it must
    # disable the adaptive floor as well, not only the static gate.
    detector, model = _echo_calibrated_detector(
        echo_amplitude=2000, consecutive_frames=3, min_frame_rms=0.0
    )
    assert detector.feed(_pcm_frames(*[2000] * 3)) is not None
    assert model.calls >= 3


def test_start_output_resets_echo_calibration() -> None:
    detector, _model = _echo_calibrated_detector(
        echo_amplitude=2000, consecutive_frames=3
    )
    assert len(detector._rms_history) > 0
    detector.start_output()
    assert len(detector._rms_history) == 0


def test_synthesis_delay_cannot_consume_echo_calibration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The grace clock starts only once output is physically active."""
    now = 100.0
    output_is_active = False
    monkeypatch.setattr(
        "jarvis.realtime.desktop.time.monotonic", lambda: now
    )
    model = _AlwaysSpeechModel()
    detector = DesktopRealtimeBargeInDetector(
        grace_s=1.5,
        consecutive_frames=3,
        output_active=lambda: output_is_active,
        model=model,
    )
    detector.warmup()
    detector.start_output()

    # Surface synthesis and stream setup can exceed the nominal grace period.
    # Loud microphone frames during that lead-in must remain local without
    # arming Silero or aging the calibration clock.
    now = 102.0
    assert detector.feed(_pcm_frames(*[2000] * 20)) is None
    assert model.calls == 0
    assert len(detector._rms_history) == 0

    # Physical playback begins now. Its first 1.5 seconds are a fresh echo-only
    # calibration window even though start_output() happened two seconds ago.
    output_is_active = True
    assert detector.feed(_pcm_frames(*[2000] * 30)) is None
    assert model.calls == 0
    assert len(detector._rms_history) == 30

    now = 104.0
    assert detector.feed(_pcm_frames(*[2000] * 20)) is None
    assert model.calls == 0


def test_grace_frames_calibrate_but_never_become_preroll() -> None:
    detector, model = _echo_calibrated_detector(
        echo_amplitude=2000, grace_frames=10, consecutive_frames=3
    )
    # Calibration data was collected …
    assert len(detector._rms_history) == 10
    # … but nothing from the grace window is buffered as user preroll and
    # the model was never consulted during grace.
    assert len(detector._pre_buffer) == 0
    assert detector._candidate_frames == []
    assert model.calls == 0


# --- BUG-101: output-envelope correlation gate -------------------------------


def _ramp_reference(now: float, *, span_s: float = 2.5) -> list[tuple[float, float, float]]:
    """A played-output envelope rising linearly until ``now`` (60 ms blocks)."""
    block = 0.06
    count = int(span_s / block)
    return [
        (now - span_s + index * block, block, 0.001 + 0.002 * index)
        for index in range(count)
    ]


def _corr_detector(
    snapshot, probabilities: int = 12
) -> DesktopRealtimeBargeInDetector:
    model = FakeVadModel([0.99] * probabilities)
    detector = DesktopRealtimeBargeInDetector(
        min_frame_rms=0.0,
        grace_s=0,
        consecutive_frames=12,
        pre_speech_frames=2,
        echo_reference_snapshot=snapshot,
        model=model,
    )
    detector.warmup()
    detector.start_output()
    return detector


def test_echo_gate_suppresses_candidate_tracking_the_output_envelope() -> None:
    import time as _time

    now = _time.monotonic()
    reference = _ramp_reference(now + 0.5)
    # Candidate loudness rises linearly exactly like the played output — a
    # shifted linear ramp correlates ~1.0 at some lag in the search window.
    ramp = _pcm_frames(*[1000 * (index + 1) for index in range(12)])

    detector = _corr_detector(lambda _window: reference)
    assert detector.feed(ramp) is None
    # Suppressed as echo, but the detector stays armed for a real user.
    assert detector.active is True


def test_echo_gate_passes_candidate_uncorrelated_with_output() -> None:
    import time as _time

    now = _time.monotonic()
    reference = _ramp_reference(now + 0.5)
    # Alternating loud/quiet speech does not track the rising output ramp.
    alternating = _pcm_frames(
        *[9000 if index % 2 == 0 else 1500 for index in range(12)]
    )

    detector = _corr_detector(lambda _window: reference)
    detected = detector.feed(alternating)
    assert detected == alternating
    assert detector.active is False


def test_echo_gate_fails_open_without_reference_data() -> None:
    ramp = _pcm_frames(*[1000 * (index + 1) for index in range(12)])
    detector = _corr_detector(lambda _window: [])
    assert detector.feed(ramp) == ramp


def test_echo_gate_fails_open_when_snapshot_raises() -> None:
    def _broken(_window: float) -> list[tuple[float, float, float]]:
        raise RuntimeError("tap unavailable")

    ramp = _pcm_frames(*[1000 * (index + 1) for index in range(12)])
    detector = _corr_detector(_broken)
    assert detector.feed(ramp) == ramp


def test_echo_gate_suppression_keeps_scanning_for_real_speech() -> None:
    import time as _time

    now = _time.monotonic()
    reference = _ramp_reference(now + 1.0)
    ramp = _pcm_frames(*[1000 * (index + 1) for index in range(12)])
    alternating = _pcm_frames(
        *[9000 if index % 2 == 0 else 1500 for index in range(12)]
    )

    detector = _corr_detector(lambda _window: reference, probabilities=24)
    assert detector.feed(ramp) is None
    detected = detector.feed(alternating)
    assert detected is not None
    assert detector.active is False


# --- Interruption length: ~0.5 s of speech, natural dips bridged ---------- #
# A barge-in counts only after about half a second of user speech, so a cough
# or an echo blip never cuts the answer off. That speech is counted across the
# short dips between syllables and words: requiring one unbroken run meant a
# natural interruption ("wait, no, the other one") almost never confirmed.


def _length_detector(probabilities: list[float], **kwargs) -> DesktopRealtimeBargeInDetector:
    detector = DesktopRealtimeBargeInDetector(
        min_frame_rms=0.0,
        grace_s=0,
        model=FakeVadModel(probabilities),
        **kwargs,
    )
    detector.warmup()
    detector.start_output()
    return detector


def test_default_interruption_needs_half_a_second_of_speech() -> None:
    # 15 speech frames (~480 ms) are not enough; the 16th (~512 ms) confirms.
    detector = _length_detector([0.99] * 16)

    assert detector.feed(_pcm_frames(*([1000] * 15))) is None
    assert detector.active is True
    assert detector.feed(_pcm_frames(1000)) is not None
    assert detector.active is False


def test_short_dips_between_words_do_not_restart_the_count() -> None:
    # Speech with a two-frame dip after every five frames: 16 speech frames in
    # total, never 16 in a row.
    pattern = ([0.99] * 5 + [0.2] * 2) * 3 + [0.99]
    detector = _length_detector(pattern)

    detected = detector.feed(_pcm_frames(*range(1000, 1000 + len(pattern))))

    assert detected is not None
    # The bridged dips stay in the returned audio: it is the user's speech.
    assert len(detected) == len(pattern) * VAD_FRAME_SAMPLES * 2


def test_a_long_pause_restarts_the_count() -> None:
    # Ten frames of speech, a 200 ms pause, ten more: never 16 in one stretch.
    pattern = [0.99] * 10 + [0.1] * 6 + [0.99] * 10
    detector = _length_detector(pattern)

    assert detector.feed(_pcm_frames(*([1000] * len(pattern)))) is None
    assert detector.active is True


def test_scattered_blips_never_add_up_to_an_interruption() -> None:
    # Echo/noise blips shorter than a syllable are never bridged, however
    # many of them arrive.
    pattern = ([0.99] * 3 + [0.1]) * 10
    detector = _length_detector(pattern)

    assert detector.feed(_pcm_frames(*([1000] * len(pattern)))) is None
    assert detector.active is True


def test_bridging_is_capped_at_half_the_required_speech() -> None:
    # Five-frame runs separated by four-frame dips: each dip is allowed, but
    # after eight bridged frames the next dip ends the candidate.
    pattern = ([0.99] * 5 + [0.1] * 4) * 3 + [0.99] * 2
    detector = _length_detector(pattern)

    assert detector.feed(_pcm_frames(*([1000] * len(pattern)))) is None


# --- Time-aligned echo floor (echo_tracking, classic playback barge) -------- #
# Live 2026-10-12 (Lenovo LOQ, laptop speakers, no AEC): the adaptive floor is
# one number per window — 1.4 x the 90th percentile of the echo — and the echo
# of laptop speakers beside the built-in mic is louder than the user, so no
# interruption ever confirmed. The grace window calibrates lag and gain against
# the recorded output envelope; afterwards the gate follows the echo predicted
# for each instant, so the user is heard in Jarvis' pauses.

_TRACK_FRAME_S = VAD_FRAME_SAMPLES / 16_000.0
_TRACK_LAG_S = 0.1
_TRACK_BLOCK_S = 0.064
_TRACK_ECHO_RMS = 0.06


def _track_segments() -> list[tuple[float, float]]:
    """Loud (start, end) spans of an aperiodic word/pause pattern from t=990 s."""
    loud = (0.30, 0.22, 0.38, 0.26, 0.34, 0.20, 0.42, 0.28)
    quiet = (0.50, 0.62, 0.45, 0.70, 0.55, 0.48, 0.66, 0.58)
    spans = []
    t = 990.0
    index = 0
    while t < 1060.0:
        length = loud[index % len(loud)]
        spans.append((t, t + length))
        t += length + quiet[(index * 3) % len(quiet)]
        index += 1
    return spans


_TRACK_SPANS = _track_segments()


def _track_output_level(t: float) -> float:
    """Played output RMS at ``t``: loud words separated by pauses."""
    for start, end in _TRACK_SPANS:
        if start <= t < end:
            return _TRACK_ECHO_RMS
        if start > t:
            break
    return 0.0


def _track_played_level(t: float) -> float:
    """What the speakers emit at ``t``: the level of the block playing then."""
    block_start = 990.0 + ((t - 990.0) // _TRACK_BLOCK_S) * _TRACK_BLOCK_S
    return _track_output_level(block_start)


class _TrackClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now

    def snapshot(self, window_s: float) -> list[tuple[float, float, float]]:
        entries = []
        start = 990.0
        while start <= self.now:
            if start >= self.now - window_s:
                entries.append((start, _TRACK_BLOCK_S, _track_output_level(start)))
            start += _TRACK_BLOCK_S
        return entries


def _track_detector(
    monkeypatch: pytest.MonkeyPatch, *, echo_tracking: bool, snapshot=None
) -> tuple[DesktopRealtimeBargeInDetector, _TrackClock]:
    clock = _TrackClock()
    monkeypatch.setattr("jarvis.realtime.desktop.time.monotonic", clock.monotonic)
    detector = DesktopRealtimeBargeInDetector(
        grace_s=1.5,
        echo_tracking=echo_tracking,
        echo_reference_snapshot=snapshot or clock.snapshot,
        model=_AlwaysSpeechModel(),
    )
    detector.warmup()
    detector.start_output()
    return detector, clock


def _track_feed(
    detector: DesktopRealtimeBargeInDetector,
    clock: _TrackClock,
    seconds: float,
    *,
    user_amplitude: int = 0,
    echo_wobble: float = 0.0,
) -> bytes | None:
    """Feed one frame per tick: the speakers' echo plus an optional user.

    ``echo_wobble`` varies the echo loudness frame to frame (room acoustics,
    loudness inside a word) by up to that fraction around the calibrated level.
    """
    for tick in range(int(seconds / _TRACK_FRAME_S)):
        clock.now += _TRACK_FRAME_S
        wobble = 1.0 + echo_wobble * float(np.sin(tick * 1.7))
        echo = _track_played_level(clock.now - _TRACK_LAG_S) * 32768.0 * wobble
        detected = detector.feed(_pcm_frames(int(echo + user_amplitude + 20)))
        if detected is not None:
            return detected
    return None


def test_echo_tracking_hears_a_user_quieter_than_the_echo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detector, clock = _track_detector(monkeypatch, echo_tracking=True)
    assert _track_feed(detector, clock, 1.6) is None  # grace: pure echo
    # The user (~0.016 RMS) is far below the echo (~0.06): the one-number
    # floor can never hear them, the time-aligned one does in the pauses.
    assert _track_feed(detector, clock, 4.0, user_amplitude=500) is not None
    assert detector.stats()["echo_track"].startswith("lag=")


def test_without_echo_tracking_the_same_user_never_interrupts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detector, clock = _track_detector(monkeypatch, echo_tracking=False)
    assert _track_feed(detector, clock, 1.6) is None
    assert _track_feed(detector, clock, 4.0, user_amplitude=500) is None
    assert detector.stats()["echo_track"] == "off"


def test_echo_tracking_never_interrupts_on_echo_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detector, clock = _track_detector(monkeypatch, echo_tracking=True)
    assert _track_feed(detector, clock, 1.6) is None
    # Ten seconds of Jarvis talking with nobody else in the room, his loudness
    # swinging +-40 % around the calibrated echo level.
    assert _track_feed(detector, clock, 10.0, echo_wobble=0.4) is None
    assert detector.active is True


def test_echo_tracking_falls_back_to_adaptive_floor_without_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detector, clock = _track_detector(
        monkeypatch, echo_tracking=True, snapshot=lambda _window: []
    )
    assert _track_feed(detector, clock, 1.6) is None
    assert _track_feed(detector, clock, 4.0, user_amplitude=500) is None
    assert detector.stats()["echo_track"] == "uncalibrated"
