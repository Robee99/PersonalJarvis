"""Desktop playback adapter for the transport-neutral realtime session."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from typing import Any

import numpy as np

from jarvis.audio.vad import VAD_FRAME_SAMPLES, SileroEndpointer
from jarvis.core.protocols import AudioChunk

log = logging.getLogger("jarvis.realtime.desktop")

# Silero frames are fixed 512 samples at the 16 kHz capture rate.
_VAD_FRAME_S = VAD_FRAME_SAMPLES / 16_000.0

# Jitter buffer: how much provider audio is banked before a turn starts playing.
#
# A realtime provider does not deliver its audio at a steady rate — it bursts,
# then goes quiet while it generates the next span. ``_note_audio_flow`` logged
# those quiet spans on 2026-07-27 as "the provider sent no audio for this span"
# at 405-686 ms, which the device buffer alone could not cover, so the speaker
# ran dry mid-sentence. Holding the first chunks back builds a reserve that the
# player eats through during such a pause instead of falling silent; the reserve
# refills on the provider's next burst.
#
# This is the mirror image of DEFAULT_OUTPUT_BUFFER_S in jarvis.audio.player,
# and the two cover different failures: audio banked HERE needs the event loop
# to reach the device, so it helps against a slow PROVIDER; audio already inside
# PortAudio plays without this process, so that is what covers an event-loop
# stall. Both are needed.
DEFAULT_PREBUFFER_MS = 180
# Ceiling on how long the reserve may be waited for, so this can only ever
# delay the first word by this much — a provider that trickles its audio out
# in real time starts speaking on time with whatever it has delivered.
DEFAULT_PREBUFFER_TIMEOUT_MS = 240


class DesktopRealtimeBargeInDetector:
    """Detect deliberate desktop barge-in on the existing microphone stream.

    Desktop realtime stays half-duplex at the provider boundary because raw
    PortAudio capture has no portable acoustic echo cancellation. Dropping the
    microphone entirely while the assistant speaks also makes interruption
    impossible, though. This detector therefore inspects those otherwise-dropped
    16 kHz frames locally with the bundled torch-free Silero ONNX model.

    Detection deliberately matches the conservative classic-pipeline policy:
    a startup grace period, a high speech probability, and sustained speech.
    "Sustained" means about half a second of confirmed speech, counted across
    the short dips natural speech has between syllables and words: a cough, a
    click or an echo blip never interrupts, a person talking over the
    assistant does without having to hold one unbroken vowel.
    The returned PCM contains a short pre-speech window plus the confirmed speech
    frames, allowing the caller to cancel output and forward the user's opening
    syllables to any realtime provider without making the provider/model itself
    responsible for desktop echo suppression.
    """

    # Frames quieter than this (float RMS, 1.0 = full scale) skip the Silero
    # inference entirely and count as non-speech. Two field problems on a
    # speakers+mic laptop (old Intel MacBook, BUG-062) share this one gate:
    # (a) per-frame ONNX on the audio-critical loop starved the 120 ms write
    # batches -> constant playback stutter; (b) the assistant's own speaker
    # echo fed the detector and false barge-ins truncated the answer. AP-27
    # empirics anchor the value: silence ghosts sit <= 0.0043, quiet real
    # speech reaches ~0.009 — 0.010 keeps deliberate interruptions (normal
    # speaking volume) while dropping silence, hiss, and moderate echo.
    # Trade-off (documented): whisper-quiet barge-in no longer triggers.
    _DEFAULT_MIN_FRAME_RMS = 0.010

    # Adaptive echo floor (BUG-084): a FIXED floor cannot cover every
    # speaker/mic coupling — on built-in laptop speakers next to the built-in
    # mic the assistant's own voice lands far above 0.010, so the static gate
    # passes it, Silero (which cannot tell whose voice it hears) confirms, and
    # the false barge truncates the answer AND opens the self-talk loop. The
    # only word-agnostic discriminator we own is the echo's measured loudness:
    # while output is active the detector keeps a rolling window of recent
    # frame RMS values and derives the gate floor from its 90th percentile
    # times a safety margin — i.e. "louder than the loudest sustained thing
    # the room currently produces", which during playback IS our own echo.
    # The newest ``_ADAPTIVE_FLOOR_LAG_FRAMES`` frames are EXCLUDED from the
    # baseline so a user starting to speak is judged against the pure-echo
    # past, never against their own rising voice (the lag must stay above
    # ``consecutive_frames`` or sustained genuine speech would raise its own
    # bar before it can confirm). The floor is clamped to the static minimum
    # below and ``_DEFAULT_ADAPTIVE_FLOOR_CAP`` above so a shouting user can
    # always break through. The cap must sit ABOVE any realistic echo RMS
    # (loud open speakers reach ~0.06-0.15) — a cap below the echo level
    # would re-open the false-barge hole it exists to close; 0.25 still
    # leaves loud close-range speech (>0.25) able to interrupt worst-case
    # coupling. ``min_frame_rms=0.0`` disables both gates (test hook /
    # explicit opt-out).
    _DEFAULT_ADAPTIVE_FLOOR_MARGIN = 1.4
    _DEFAULT_ADAPTIVE_FLOOR_CAP = 0.25
    _ADAPTIVE_FLOOR_LAG_FRAMES = 16
    _ADAPTIVE_FLOOR_WINDOW_FRAMES = 96
    _ADAPTIVE_FLOOR_MIN_BASELINE_FRAMES = 8

    # Output-envelope correlation gate (BUG-101): the energy floor above is a
    # LEVEL discriminator and leaks whenever the room's echo coupling beats the
    # calibrated margin (loud built-in speakers next to a built-in mic: the
    # assistant's own dynamics jump the 90th-percentile floor, Silero — which
    # cannot tell whose voice it hears — confirms, and the false barge both
    # truncates the answer and feeds the echo back as user input). The
    # remaining word-agnostic discriminator this process owns is the SHAPE of
    # what the speakers are emitting: ``AudioPlayer`` records a timestamped
    # RMS envelope of every played block, and a confirmed candidate whose own
    # envelope tracks that reference at some plausible device-latency lag is
    # our echo, not a user. Pearson correlation is scale-invariant, so mic
    # gain, distance, and volume do not matter; a genuine interruption is
    # dominated by the user's uncorrelated speech and falls well below the
    # threshold. Every guard fails OPEN (no reference, flat envelopes, poor
    # coverage → the barge stands), so classic surfaces without the tap and
    # tests keep today's behavior. The lag ceiling covers the largest reported
    # device output latency observed in the field (0.869 s, BUG-100) plus
    # acoustic path and capture buffering.
    _ECHO_CORR_THRESHOLD = 0.70
    _ECHO_CORR_MAX_LAG_S = 1.75
    _ECHO_CORR_LAG_STEP_S = 0.016
    _ECHO_CORR_MIN_FRAMES = 8
    _ECHO_CORR_MIN_COVERAGE = 0.8
    _ECHO_CORR_MIN_STD = 1e-4

    # Interruption length. ``consecutive_frames`` (the historical name) is the
    # number of high-probability frames a candidate must hold before it counts:
    # 16 x 32 ms ~= 0.5 s of user speech, the point below which a "barge-in"
    # is far more often a cough, a laugh or the assistant's own echo than a
    # person taking the floor. Brief dips inside a candidate are bridged
    # instead of restarting the count — a single frame between two words used
    # to throw away everything said so far, so a natural interruption almost
    # never confirmed — but only once the candidate holds one solid run
    # (``_BRIDGE_AFTER_FRAMES``, about a syllable), only for gaps up to
    # ``max_gap_frames`` long, and only up to half the required speech in
    # total, so scattered echo blips can never add up to an interruption.
    _DEFAULT_CONSECUTIVE_FRAMES = 16
    _DEFAULT_MAX_GAP_FRAMES = 4
    _BRIDGE_AFTER_FRAMES = 4

    # Time-aligned echo floor (opt-in ``echo_tracking``; the classic pipeline's
    # playback barge). The adaptive floor above is ONE number per window: the
    # loudest sustained echo times a margin. On a laptop whose speakers sit a
    # hand's width from the built-in mic the echo is louder than a user at arm's
    # length, so that single number sits above the user's voice for the whole
    # answer and speaking over Jarvis never interrupts (live 2026-10-12, Lenovo
    # LOQ: no barge-in in an entire session). The echo is not uniformly loud,
    # though: it follows what the speakers emit, syllable by syllable, and that
    # envelope is recorded (``jarvis.audio.echo_reference``). The grace window,
    # pure echo by construction, calibrates the device lag and the
    # speaker->mic gain against it; afterwards each frame is gated against the
    # echo PREDICTED for that instant (gain x the loudest output block within a
    # reverb-smeared window at that lag) times ``_ECHO_TRACK_MARGIN``. In the
    # gaps between Jarvis' words the gate drops toward the static floor, so a
    # user's speech there counts, while the echo itself still has to beat
    # twice its own predicted level. Conservative by construction: during
    # Jarvis' words the tracked gate (2x the echo predicted for that instant)
    # is STRICTER than the adaptive one (1.4x the 90th percentile), only his
    # pauses get the lower gate, a poor calibration (correlation below
    # ``_ECHO_TRACK_MIN_CORR``) or a frame outside the recorded reference
    # falls back to the adaptive floor, the full ~0.5 s of
    # high-probability speech is still required, frames masked by loud echo
    # are neutral for a bounded run only (they neither count as speech nor
    # break a candidate, ~0.75 s per run), and the envelope-correlation gate
    # still judges every confirmed candidate on its unmasked frames. Physics
    # sets the limit: without acoustic echo cancellation nobody can hear the
    # user UNDER a louder echo, so a quiet interruption lands in Jarvis'
    # pauses; ``[audio].echo_cancellation`` with its optional engine removes
    # that limit.
    _ECHO_TRACK_MIN_CORR = 0.5
    _ECHO_TRACK_MIN_FRAMES = 16
    _ECHO_TRACK_MARGIN = 2.0
    _ECHO_TRACK_SIGNIFICANT_FRACTION = 0.25
    _ECHO_TRACK_SMEAR_BACK_S = 0.15
    _ECHO_TRACK_SMEAR_FWD_S = 0.08
    _ECHO_TRACK_MAX_MASKED_RUN = 24
    _ECHO_TRACK_MAX_MASKED_PER_SPEECH_FRAME = 4
    _ECHO_TRACK_PAUSE_COVER_S = 2.0

    def __init__(
        self,
        *,
        grace_s: float = 1.5,
        speech_threshold: float = 0.97,
        consecutive_frames: int = _DEFAULT_CONSECUTIVE_FRAMES,
        pre_speech_frames: int = 10,
        max_gap_frames: int = _DEFAULT_MAX_GAP_FRAMES,
        min_frame_rms: float | None = None,
        adaptive_floor_margin: float | None = None,
        adaptive_floor_cap: float | None = None,
        output_active: Callable[[], bool] | None = None,
        echo_reference_snapshot: (
            Callable[[float], list[tuple[float, float, float]]] | None
        ) = None,
        echo_correlation_threshold: float | None = None,
        echo_tracking: bool = False,
        model: Any = None,
    ) -> None:
        self._grace_s = max(0.0, float(grace_s))
        self._echo_tracking = bool(echo_tracking)
        # (capture-time estimate, rms) of the grace frames — the echo-tracking
        # calibration data — and the calibrated (lag_s, gain), None when off,
        # not yet calibrated, or the calibration was not trustworthy.
        self._grace_samples: list[tuple[float, float]] = []
        self._track: tuple[float, float] | None = None
        self._track_attempted = False
        self._masked_run = 0
        self._masked_frames = 0
        self._masked_ts: set[float] = set()
        # Read-only diagnostics (``stats``); never feed a decision.
        self._stat_frames = 0
        self._stat_gated = 0
        self._stat_masked = 0
        self._stat_echo_suppressed = 0
        self._stat_best_speech = 0
        self._stat_peak_rms = 0.0
        self._stat_last_gate = 0.0
        self._speech_threshold = min(1.0, max(0.0, float(speech_threshold)))
        self._consecutive_frames = max(1, int(consecutive_frames))
        self._pre_speech_frames = max(1, int(pre_speech_frames))
        self._max_gap_frames = max(0, int(max_gap_frames))
        self._bridge_after_frames = min(
            self._BRIDGE_AFTER_FRAMES, self._consecutive_frames
        )
        self._max_bridged_frames = self._consecutive_frames // 2
        self._min_frame_rms = (
            self._DEFAULT_MIN_FRAME_RMS if min_frame_rms is None else max(0.0, float(min_frame_rms))
        )
        self._adaptive_floor_margin = (
            self._DEFAULT_ADAPTIVE_FLOOR_MARGIN
            if adaptive_floor_margin is None
            else max(1.0, float(adaptive_floor_margin))
        )
        self._adaptive_floor_cap = (
            self._DEFAULT_ADAPTIVE_FLOOR_CAP
            if adaptive_floor_cap is None
            else max(self._min_frame_rms, float(adaptive_floor_cap))
        )
        # Surface TTS can spend more than a second synthesizing and opening the
        # output stream after the response is logically marked SPEAKING. The
        # grace window must begin at physical playback, not at that early state
        # edge, or synthesis latency consumes the echo-calibration period and
        # the assistant's first sentence is misclassified as a barge-in. The
        # desktop pipeline supplies the AudioPlayer's process-local playback
        # probe; omitted keeps the detector usable in isolation and tests.
        self._output_active = output_active
        self._playback_started = False
        # Default reference is the process-local player tap; injectable for
        # tests and disabled entirely with a threshold <= 0.
        if echo_reference_snapshot is None:
            from jarvis.audio import echo_reference

            echo_reference_snapshot = echo_reference.snapshot
        self._echo_reference_snapshot = echo_reference_snapshot
        self._echo_corr_threshold = (
            self._ECHO_CORR_THRESHOLD
            if echo_correlation_threshold is None
            else float(echo_correlation_threshold)
        )
        # The lag must exceed the longest possible candidate (its speech plus
        # every bridged dip) so genuine sustained speech is always judged
        # against a baseline formed BEFORE it started.
        self._adaptive_floor_lag = max(
            self._consecutive_frames + self._max_bridged_frames + 4,
            self._ADAPTIVE_FLOOR_LAG_FRAMES,
        )
        self._rms_history: deque[float] = deque(
            maxlen=self._ADAPTIVE_FLOOR_WINDOW_FRAMES + self._adaptive_floor_lag
        )
        self._model = model or SileroEndpointer(
            speech_threshold=self._speech_threshold
        )
        self._ready = False
        self._active = False
        self._started_at = 0.0
        self._residual = np.empty(0, dtype=np.dtype("<i2"))
        # Buffered frames carry their capture-time estimate so a confirmed
        # candidate can be correlated against the timestamped output envelope.
        self._pre_buffer: deque[tuple[np.ndarray, float]] = deque(
            maxlen=self._pre_speech_frames
        )
        self._candidate_frames: list[tuple[np.ndarray, float]] = []
        self._speech_run = 0
        # Candidate bookkeeping: speech frames held, the current dip, the dip
        # frames bridged so far, and whether one solid run has been seen.
        self._speech_frames = 0
        self._gap_run = 0
        self._bridged_frames = 0
        self._solid_run_seen = False

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def active(self) -> bool:
        return self._active

    def warmup(self) -> None:
        """Load the CPU ONNX session outside the realtime audio callback."""

        self._model._ensure_model()
        self._ready = True

    def start_output(self) -> None:
        """Arm a fresh detector window for one assistant audio response."""

        self._active = True
        self._started_at = time.monotonic()
        self._playback_started = self._output_active is None
        self._reset_buffers()
        # Fresh echo calibration per response: the grace window (pure speaker
        # echo by construction) re-trains the adaptive floor for the volume /
        # coupling of THIS answer instead of trusting a stale estimate.
        self._rms_history.clear()
        self._grace_samples = []
        self._track = None
        self._track_attempted = False

    def stats(self) -> dict[str, Any]:
        """Read-only counters for the caller's end-of-window diagnostics log."""

        track = self._track
        return {
            "frames": self._stat_frames,
            "gated": self._stat_gated,
            "masked": self._stat_masked,
            "echo_suppressed": self._stat_echo_suppressed,
            "best_speech_frames": self._stat_best_speech,
            "peak_rms": round(self._stat_peak_rms, 4),
            "last_gate": round(self._stat_last_gate, 4),
            "echo_track": (
                f"lag={track[0] * 1000.0:.0f}ms gain={track[1]:.2f}"
                if track is not None
                else ("off" if not self._echo_tracking else "uncalibrated")
            ),
        }

    def stop_output(self) -> None:
        self._active = False
        self._playback_started = False
        self._reset_buffers()

    def feed(self, pcm16: bytes) -> bytes | None:
        """Return buffered user PCM once sustained speech is confirmed."""

        if not self._active or not self._ready or len(pcm16) < 2:
            return None

        if not self._playback_started:
            # Keep half-duplex input local while synthesis/stream setup is
            # pending, but do not let that silent lead-in age the grace clock.
            # The first frame observed during real playback starts a full,
            # fresh calibration window built only from speaker echo.
            if self._output_active is not None and not self._output_active():
                self._reset_buffers()
                return None
            self._playback_started = True
            self._started_at = time.monotonic()

        usable = len(pcm16) - (len(pcm16) % 2)
        samples = np.frombuffer(pcm16[:usable], dtype=np.dtype("<i2"))
        if self._residual.size:
            samples = np.concatenate([self._residual, samples])
        frame_count = samples.size // VAD_FRAME_SAMPLES

        if time.monotonic() - self._started_at < self._grace_s:
            # Never let speaker echo collected during the grace period become
            # user preroll once detection arms — but DO measure it: grace-time
            # frames are our own playback echo, the calibration data the
            # adaptive floor is built from (BUG-084).
            if frame_count:
                framed_samples = frame_count * VAD_FRAME_SAMPLES
                grace_frames = samples[:framed_samples].reshape(
                    frame_count, VAD_FRAME_SAMPLES
                )
                grace_now = time.monotonic()
                for index, frame in enumerate(grace_frames):
                    grace_rms = self._frame_rms(frame)
                    self._rms_history.append(grace_rms)
                    if self._echo_tracking:
                        self._grace_samples.append(
                            (
                                grace_now - (frame_count - 1 - index) * _VAD_FRAME_S,
                                grace_rms,
                            )
                        )
            self._reset_buffers()
            return None

        if frame_count == 0:
            self._residual = samples.copy()
            return None

        framed_samples = frame_count * VAD_FRAME_SAMPLES
        frames = samples[:framed_samples].reshape(frame_count, VAD_FRAME_SAMPLES)
        trailing = samples[framed_samples:].copy()

        # The batch just arrived from the live capture stream, so its last
        # frame ends roughly "now"; earlier frames stack backwards in fixed
        # VAD-frame steps. Millisecond-exact stamps are unnecessary — the
        # correlation gate searches lags far coarser than executor jitter.
        batch_now = time.monotonic()

        if self._echo_tracking and not self._track_attempted:
            self._track_attempted = True
            self._track = self._calibrate_echo_track()
        track_reference = (
            self._echo_track_reference(frame_count * _VAD_FRAME_S)
            if self._track is not None and self._min_frame_rms > 0.0
            else None
        )

        for index, frame in enumerate(frames):
            frame_ts = batch_now - (frame_count - 1 - index) * _VAD_FRAME_S
            normalized = frame.astype(np.float32) / 32768.0
            frame_rms = float(np.sqrt(np.mean(np.square(normalized))))
            # Energy pre-gate (BUG-062) + adaptive echo floor (BUG-084):
            # frames below the gate never reach the ONNX model — this is the
            # loop-load fix and the echo damper in one. The adaptive floor is
            # computed BEFORE this frame enters the history, so every frame is
            # judged against the lagged pure-echo past, never against itself.
            gate = self._effective_floor()
            tracked = (
                self._tracked_floor(track_reference, frame_ts)
                if track_reference is not None
                else None
            )
            if tracked is not None:
                gate = tracked
            # Masked: loud echo, not silence, is what holds this frame under
            # the gate, so it says nothing about whether the user is talking.
            masked = (
                tracked is not None
                and tracked > self._min_frame_rms
                and frame_rms < gate
            )
            if masked:
                self._masked_ts.add(frame_ts)
            self._rms_history.append(frame_rms)
            self._stat_frames += 1
            self._stat_last_gate = gate
            if frame_rms > self._stat_peak_rms:
                self._stat_peak_rms = frame_rms
            if gate > 0.0 and frame_rms < gate:
                probability = 0.0
                self._stat_gated += 1
            else:
                probability = float(self._model._prob(normalized))
            if probability >= self._speech_threshold:
                if self._speech_frames == 0:
                    self._candidate_frames = [
                        (part.copy(), part_ts) for part, part_ts in self._pre_buffer
                    ]
                self._candidate_frames.append((frame.copy(), frame_ts))
                self._speech_run += 1
                self._speech_frames += 1
                self._gap_run = 0
                self._masked_run = 0
                if self._speech_frames > self._stat_best_speech:
                    self._stat_best_speech = self._speech_frames
                if self._speech_run >= self._bridge_after_frames:
                    self._solid_run_seen = True
                if self._speech_frames >= self._consecutive_frames:
                    if self._candidate_matches_output_envelope():
                        # The confirmed "speech" tracks what the speakers are
                        # currently emitting — our own echo, not a user. Keep
                        # the detector armed; a real interruption on top of
                        # the echo decorrelates and still breaks through.
                        self._stat_echo_suppressed += 1
                        self._reset_buffers()
                        continue
                    tail_frames = frames[index + 1 :].reshape(-1)
                    parts = [part for part, _part_ts in self._candidate_frames]
                    if tail_frames.size:
                        parts.append(tail_frames.copy())
                    if trailing.size:
                        parts.append(trailing)
                    detected = np.concatenate(parts).astype(
                        np.dtype("<i2"), copy=False
                    )
                    self._active = False
                    self._reset_buffers()
                    return detected.tobytes()
                continue

            if (
                masked
                and self._speech_frames
                and self._solid_run_seen
                and self._masked_run < self._ECHO_TRACK_MAX_MASKED_RUN
                and self._masked_frames
                < self._ECHO_TRACK_MAX_MASKED_PER_SPEECH_FRAME * self._consecutive_frames
            ):
                # Jarvis' own loud syllable covers this frame: neutral. It
                # neither adds speech nor ends a candidate already under way,
                # so a user talking through the answer accumulates evidence in
                # the gaps between Jarvis' words. Bounded per run and in total.
                self._candidate_frames.append((frame.copy(), frame_ts))
                self._speech_run = 0
                self._masked_run += 1
                self._masked_frames += 1
                self._stat_masked += 1
                continue

            if (
                self._speech_frames
                and self._solid_run_seen
                and self._gap_run < self._max_gap_frames
                and self._bridged_frames < self._max_bridged_frames
            ):
                # A short dip between syllables or words of speech already
                # under way: keep the candidate and its count (see the
                # ``_DEFAULT_CONSECUTIVE_FRAMES`` rationale).
                self._candidate_frames.append((frame.copy(), frame_ts))
                self._speech_run = 0
                self._gap_run += 1
                self._bridged_frames += 1
                continue

            # A short high-probability burst is speaker bleed, not a barge-in.
            # Retain only the rolling pre-speech window for the next candidate.
            for candidate in self._candidate_frames:
                self._pre_buffer.append(candidate)
            self._clear_candidate()
            self._pre_buffer.append((frame.copy(), frame_ts))
            if masked:
                self._masked_ts.add(frame_ts)

        self._residual = trailing
        return None

    @staticmethod
    def _frame_rms(frame: np.ndarray) -> float:
        normalized = frame.astype(np.float32) / 32768.0
        return float(np.sqrt(np.mean(np.square(normalized))))

    def _candidate_matches_output_envelope(self) -> bool:
        """True when the confirmed candidate is the speakers' own envelope.

        Correlates the candidate's per-frame RMS series against the player's
        timestamped output envelope across the plausible device-latency lag
        window. Pearson correlation is scale-invariant, so speaker volume,
        mic gain, and distance drop out; only the temporal SHAPE of the
        loudness decides — word- and language-agnostic by construction.
        Every guard fails OPEN (returns False → the barge-in stands): no or
        flat reference data, too few frames, poor time coverage, or any
        snapshot error must never make interruption impossible.
        """
        threshold = self._echo_corr_threshold
        if threshold <= 0.0:
            return False
        if len(self._candidate_frames) < self._ECHO_CORR_MIN_FRAMES:
            return False
        try:
            window = (
                len(self._candidate_frames) * _VAD_FRAME_S
                + self._ECHO_CORR_MAX_LAG_S
                + 1.0
            )
            reference = list(self._echo_reference_snapshot(window))
        except Exception:  # noqa: BLE001 — the gate must never break barge-in
            log.debug("Echo-reference snapshot failed", exc_info=True)
            return False
        if len(reference) < 2:
            return False
        # Frames masked by loud echo (echo tracking) are the speakers' own
        # envelope by definition; judging the user's frames, not those, is
        # what tells an interruption over the answer from the answer itself.
        judged = [
            (frame, ts)
            for frame, ts in self._candidate_frames
            if ts not in self._masked_ts
        ]
        if len(judged) < self._ECHO_CORR_MIN_FRAMES:
            return False
        candidate = np.array(
            [self._frame_rms(frame) for frame, _ts in judged],
            dtype=np.float64,
        )
        times = np.array([ts for _frame, ts in judged], dtype=np.float64)
        if float(candidate.std()) < self._ECHO_CORR_MIN_STD:
            return False
        starts = np.array([entry[0] for entry in reference], dtype=np.float64)
        ends = starts + np.array(
            [entry[1] for entry in reference], dtype=np.float64
        )
        levels = np.array([entry[2] for entry in reference], dtype=np.float64)
        min_cover = max(
            self._ECHO_CORR_MIN_FRAMES,
            int(len(candidate) * self._ECHO_CORR_MIN_COVERAGE),
        )
        best_r = 0.0
        best_lag = 0.0
        lag = 0.0
        while lag <= self._ECHO_CORR_MAX_LAG_S:
            query = times - lag
            idx = np.searchsorted(starts, query, side="right") - 1
            idx_clamped = np.clip(idx, 0, len(starts) - 1)
            valid = (idx >= 0) & (query < ends[idx_clamped])
            if int(valid.sum()) >= min_cover:
                cand_values = candidate[valid]
                ref_values = levels[idx_clamped[valid]]
                if (
                    float(cand_values.std()) >= self._ECHO_CORR_MIN_STD
                    and float(ref_values.std()) >= self._ECHO_CORR_MIN_STD
                ):
                    r = float(np.corrcoef(cand_values, ref_values)[0, 1])
                    if math.isfinite(r) and r > best_r:
                        best_r = r
                        best_lag = lag
            lag += self._ECHO_CORR_LAG_STEP_S
        if best_r >= threshold:
            log.info(
                "Barge-in candidate suppressed as speaker echo "
                "(envelope correlation %.2f at %.0f ms output lag)",
                best_r,
                best_lag * 1000.0,
            )
            return True
        log.debug(
            "Barge-in candidate passed the echo gate (max correlation %.2f)",
            best_r,
        )
        return False

    @staticmethod
    def _reference_arrays(
        reference: list[tuple[float, float, float]],
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        if len(reference) < 2:
            return None
        starts = np.array([entry[0] for entry in reference], dtype=np.float64)
        ends = starts + np.array([entry[1] for entry in reference], dtype=np.float64)
        levels = np.array([entry[2] for entry in reference], dtype=np.float64)
        return starts, ends, levels

    def _smeared_reference(
        self, arrays: tuple[np.ndarray, np.ndarray, np.ndarray], query: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """Loudest output block audible around each query time, plus coverage.

        The window reaches ``_ECHO_TRACK_SMEAR_BACK_S`` into the past (room
        reverb keeps a loud syllable audible after the block ends) and a little
        ahead (block-boundary jitter). A query before the recorded reference,
        or longer than ``_ECHO_TRACK_PAUSE_COVER_S`` after its last block, is
        reported uncovered so the caller never mistakes "no data" for "silent
        speakers"; a shorter stretch after the last block is the pause between
        two synthesized sentences, when the speakers really are quiet.
        """

        starts, ends, levels = arrays
        lo = query - self._ECHO_TRACK_SMEAR_BACK_S
        hi = query + self._ECHO_TRACK_SMEAR_FWD_S
        overlap = (starts[None, :] <= hi[:, None]) & (ends[None, :] >= lo[:, None])
        smeared = np.where(overlap, levels[None, :], 0.0).max(axis=1)
        covered = (query >= starts[0]) & (query <= ends[-1] + self._ECHO_TRACK_PAUSE_COVER_S)
        return smeared, covered

    @staticmethod
    def _point_reference(
        arrays: tuple[np.ndarray, np.ndarray, np.ndarray], query: np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        """Level of the output block playing at each query time, plus coverage.

        Calibration estimates the lag on the UNsmeared envelope (the smear
        would blur the very timing it is measuring), the same lookup the
        envelope-correlation gate uses.
        """

        starts, ends, levels = arrays
        idx = np.searchsorted(starts, query, side="right") - 1
        idx_clamped = np.clip(idx, 0, len(starts) - 1)
        inside = (idx >= 0) & (query < ends[idx_clamped])
        values = np.where(inside, levels[idx_clamped], 0.0)
        covered = (query >= starts[0]) & (query < ends[-1])
        return values, covered

    def _calibrate_echo_track(self) -> tuple[float, float] | None:
        """Device lag and speaker->mic gain from the pure-echo grace window."""

        samples = self._grace_samples
        self._grace_samples = []
        if len(samples) < self._ECHO_TRACK_MIN_FRAMES:
            return None
        times = np.array([ts for ts, _rms in samples], dtype=np.float64)
        mic = np.array([rms for _ts, rms in samples], dtype=np.float64)
        if float(mic.std()) < self._ECHO_CORR_MIN_STD:
            return None
        try:
            window = (
                time.monotonic() - float(times[0])
                + self._ECHO_CORR_MAX_LAG_S
                + self._ECHO_TRACK_SMEAR_BACK_S
                + 1.0
            )
            arrays = self._reference_arrays(list(self._echo_reference_snapshot(window)))
        except Exception:  # noqa: BLE001 — calibration failure = adaptive floor only
            log.debug("Echo-tracking calibration snapshot failed", exc_info=True)
            return None
        if arrays is None:
            return None
        best_r = 0.0
        best_lag: float | None = None
        lag = 0.0
        while lag <= self._ECHO_CORR_MAX_LAG_S:
            point, covered = self._point_reference(arrays, times - lag)
            if int(covered.sum()) >= self._ECHO_TRACK_MIN_FRAMES:
                ref_values = point[covered]
                mic_values = mic[covered]
                if (
                    float(ref_values.std()) >= self._ECHO_CORR_MIN_STD
                    and float(mic_values.std()) >= self._ECHO_CORR_MIN_STD
                ):
                    r = float(np.corrcoef(mic_values, ref_values)[0, 1])
                    if math.isfinite(r) and r > best_r:
                        best_r = r
                        best_lag = lag
            lag += self._ECHO_CORR_LAG_STEP_S
        if best_lag is None or best_r < self._ECHO_TRACK_MIN_CORR:
            log.info(
                "Barge echo tracking not calibrated (best correlation %.2f); "
                "using the adaptive echo floor.",
                best_r,
            )
            return None
        point, covered = self._point_reference(arrays, times - best_lag)
        ref_values = point[covered]
        mic_values = mic[covered]
        significant = ref_values >= self._ECHO_TRACK_SIGNIFICANT_FRACTION * float(
            ref_values.max()
        )
        significant &= ref_values > 0.0
        if int(significant.sum()) < 4:
            return None
        gain = float(np.percentile(mic_values[significant] / ref_values[significant], 90))
        if not math.isfinite(gain) or gain <= 0.0:
            return None
        log.info(
            "Barge echo tracking calibrated: output lag %.0f ms, speaker->mic "
            "gain %.3f (correlation %.2f).",
            best_lag * 1000.0,
            gain,
            best_r,
        )
        return best_lag, gain

    def _echo_track_reference(
        self, batch_s: float
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
        track = self._track
        if track is None:
            return None
        try:
            window = batch_s + track[0] + self._ECHO_TRACK_SMEAR_BACK_S + 1.0
            return self._reference_arrays(list(self._echo_reference_snapshot(window)))
        except Exception:  # noqa: BLE001 — no reference = adaptive floor only
            log.debug("Echo-tracking snapshot failed", exc_info=True)
            return None

    def _tracked_floor(
        self, arrays: tuple[np.ndarray, np.ndarray, np.ndarray], frame_ts: float
    ) -> float | None:
        """Gate for one frame from the echo predicted at that instant, or None."""

        track = self._track
        if track is None:
            return None
        lag, gain = track
        smeared, covered = self._smeared_reference(
            arrays, np.array([frame_ts - lag], dtype=np.float64)
        )
        if not bool(covered[0]):
            return None
        predicted = gain * float(smeared[0])
        return max(self._min_frame_rms, self._ECHO_TRACK_MARGIN * predicted)

    def _effective_floor(self) -> float:
        """Current energy gate: static minimum or the learned echo floor.

        ``min_frame_rms == 0.0`` disables gating entirely (adaptive included) —
        the explicit logic-test / opt-out hook. Otherwise the floor is the 90th
        percentile of the lagged RMS history times the safety margin, clamped
        to [static minimum, cap]. With too little history (fresh detector, no
        playback echo measured yet) it falls back to the static minimum.
        """

        if self._min_frame_rms <= 0.0:
            return 0.0
        baseline = list(self._rms_history)[: -self._adaptive_floor_lag]
        if len(baseline) < self._ADAPTIVE_FLOOR_MIN_BASELINE_FRAMES:
            return self._min_frame_rms
        learned = self._adaptive_floor_margin * float(np.percentile(baseline, 90))
        return min(self._adaptive_floor_cap, max(self._min_frame_rms, learned))

    def _clear_candidate(self) -> None:
        self._candidate_frames = []
        self._speech_run = 0
        self._speech_frames = 0
        self._gap_run = 0
        self._bridged_frames = 0
        self._solid_run_seen = False
        self._masked_run = 0
        self._masked_frames = 0
        # Masked stamps outlive a candidate only while their frame is still
        # in the pre-speech window a next candidate would start from.
        if self._masked_ts:
            self._masked_ts = {
                ts for _frame, ts in self._pre_buffer if ts in self._masked_ts
            }

    def _reset_buffers(self) -> None:
        self._residual = np.empty(0, dtype=np.dtype("<i2"))
        self._pre_buffer.clear()
        self._clear_candidate()


class DesktopRealtimePlayback:
    """Feed provider PCM deltas through one persistent ``AudioPlayer`` stream.

    The bounded queue applies backpressure when an output device is slower than
    the provider. A turn-complete marker drains naturally; barge-in or teardown
    stops the device immediately and discards queued audio.

    Each turn opens with a jitter buffer (``prebuffer_ms``) so a provider that
    pauses mid-reply eats a reserve instead of the speaker (see
    ``DEFAULT_PREBUFFER_MS``).
    """

    #: Reopening within this window after a natural turn boundary skips the
    #: jitter prebuffer. ChatGPT-Live announces no terminal response item
    #: (probe-confirmed 2026-08-06: the notification vocabulary is
    #: sdp/started/transcript only), so every generation pause longer than the
    #: adapter's 1.2 s quiescence backstop ends the turn locally - and the
    #: next delta used to reopen with a fresh 180 ms reserve, which made every
    #: such pause an audible seam MID-REPLY. Matches the adapter's
    #: continuation grace (same "still the same answer" semantic).
    RESUME_WITHOUT_PREBUFFER_S = 4.0

    def __init__(
        self,
        player: Any,
        *,
        sample_rate: int = 24_000,
        max_queue_chunks: int = 200,
        finish_timeout_s: float = 120.0,
        prebuffer_ms: int = DEFAULT_PREBUFFER_MS,
        prebuffer_timeout_ms: int = DEFAULT_PREBUFFER_TIMEOUT_MS,
        resume_without_prebuffer_s: float = RESUME_WITHOUT_PREBUFFER_S,
    ) -> None:
        self._player = player
        self._sample_rate = int(sample_rate)
        self._max_queue_chunks = max(1, int(max_queue_chunks))
        self._finish_timeout_s = max(1.0, float(finish_timeout_s))
        self._prebuffer_s = max(0.0, float(prebuffer_ms) / 1000.0)
        self._prebuffer_timeout_s = max(0.0, float(prebuffer_timeout_ms) / 1000.0)
        self._resume_without_prebuffer_s = max(
            0.0, float(resume_without_prebuffer_s)
        )
        self._queue: asyncio.Queue[tuple[int, AudioChunk] | None] | None = None
        self._task: asyncio.Task[bool | None] | None = None
        self._closed = False
        self._last_finish_at = 0.0

    async def send_binary(self, pcm: bytes) -> None:
        if not pcm or self._closed or getattr(self._player, "output_muted", False):
            return
        if self._task is None or self._task.done():
            prebuffer_s = self._prebuffer_s
            since_finish = time.monotonic() - self._last_finish_at
            # Strict comparison: a grace of 0.0 means "never" even on
            # Windows' coarse monotonic clock, where since_finish can be
            # exactly 0.0 right after the boundary.
            if (
                self._last_finish_at > 0.0
                and since_finish < self._resume_without_prebuffer_s
            ):
                # Same-answer resume: the boundary we just crossed was the
                # local silence backstop, not the provider ending its answer.
                # Nothing is dropped or reordered - the reserve simply is not
                # rebuilt, so the seam costs ~0 ms instead of 180 ms.
                prebuffer_s = 0.0
                log.debug(
                    "reopened realtime playback %d ms after the last boundary"
                    " - skipping the jitter prebuffer (same-answer resume)",
                    int(since_finish * 1000.0),
                )
            self._queue = asyncio.Queue(maxsize=self._max_queue_chunks)
            self._task = asyncio.create_task(
                self._player.play_chunks(
                    self._chunks(self._queue, prebuffer_s)
                ),
                name="realtime-desktop-playback",
            )
            # A terminal surface cancellation can race a provider callback
            # that is already unwinding. Always retrieve the native playback
            # result even if that callback loses its final await; explicit
            # ``finish_turn`` callers still receive the same exception.
            self._task.add_done_callback(self._observe_playback_result)
        assert self._queue is not None
        generation = getattr(self._player, "output_generation", 0)
        await self._queue.put((generation,
            AudioChunk(pcm=bytes(pcm), sample_rate=self._sample_rate, timestamp_ns=0)
        ))

    def set_sample_rate(self, sample_rate: int) -> None:
        """Set the rate announced by the accepted provider handshake.

        Providers must announce this before their first audio delta. Refuse a
        mid-stream rate change because one ``AudioPlayer`` stream cannot safely
        reinterpret already queued PCM at a different rate.
        """
        rate = int(sample_rate)
        if rate <= 0:
            raise ValueError("Realtime output sample rate must be positive")
        if self._task is not None and not self._task.done():
            raise RuntimeError("Cannot change realtime sample rate during playback")
        self._sample_rate = rate

    async def finish_turn(self) -> bool:
        """Confirm only a turn that drained without cancellation or timeout."""
        queue, task = self._queue, self._task
        if queue is None or task is None:
            return False
        await queue.put(None)
        try:
            result = await asyncio.wait_for(task, timeout=self._finish_timeout_s)
            return result is not False and self._task is task
        except TimeoutError:
            self._player.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            # The playback worker was canceled by a concurrent barge-in.
        except Exception:
            if self._task is task:
                raise
            # A concurrent cancel detached this task and aborted its PortAudio
            # stream. The blocked write can then unwind with "Stream is
            # stopped"; that is the expected result of barge-in, not a failed
            # realtime session.
        finally:
            # Keep the active task discoverable while it drains. A user can
            # barge in after the provider has sent turn_complete but before the
            # local speaker queue is empty; cancel() must still be able to stop
            # and drain this exact task during that window.
            if self._task is task:
                self._queue = None
                self._task = None
                # Natural boundary only: a barge-in cancel() deliberately does
                # NOT stamp this, so the next (genuinely new) reply rebuilds
                # its jitter reserve.
                self._last_finish_at = time.monotonic()

        return False

    async def cancel(self) -> None:
        queue, task = self._detach()
        task_was_done = task is not None and task.done()
        self._player.stop()
        if queue is None or task is None:
            return
        while not queue.empty():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover - race-safe guard
                break
        try:
            queue.put_nowait(None)
        except asyncio.QueueFull:  # pragma: no cover - queue was drained above
            pass
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except TimeoutError:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        except Exception:
            if task_was_done:
                raise
            # stop() deliberately aborts the live PortAudio stream. A write
            # already running in its worker thread can report that abort as a
            # playback exception while the task unwinds.

    async def close(self) -> None:
        # Terminal close differs from an ordinary barge-in ``cancel``: later
        # provider callbacks belong to a dead voice surface and must never
        # create a fresh OutputStream after teardown has started.
        self._closed = True
        await self.cancel()

    @staticmethod
    def _observe_playback_result(task: asyncio.Task[bool | None]) -> None:
        if task.cancelled():
            return
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc is not None:
            log.debug("Realtime desktop playback task ended with %r", exc)

    def _detach(
        self,
    ) -> tuple[
        asyncio.Queue[tuple[int, AudioChunk] | None] | None, asyncio.Task[bool | None] | None,
    ]:
        queue, task = self._queue, self._task
        self._queue = None
        self._task = None
        return queue, task

    async def _chunks(
        self,
        queue: asyncio.Queue[tuple[int, AudioChunk] | None],
        prebuffer_s: float | None = None,
    ) -> AsyncIterator[AudioChunk]:
        """Yield the turn's audio, opening with the jitter buffer.

        Nothing is dropped or reordered: the banked chunks are handed on in
        arrival order, the reserve simply starts playback that much later so a
        provider pause has something to eat. The wait is bounded twice over —
        by ``prebuffer_timeout_ms`` and by the end-of-turn sentinel — so a reply
        shorter than the reserve still plays immediately. ``prebuffer_s``
        overrides the configured reserve for THIS task (0.0 on a same-answer
        resume).
        """
        banked: list[tuple[int, AudioChunk]] = []
        ended = False
        if prebuffer_s is None:
            prebuffer_s = self._prebuffer_s
        target_bytes = int(self._sample_rate * 2 * prebuffer_s)
        if target_bytes > 0:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self._prebuffer_timeout_s
            banked_bytes = 0
            while banked_bytes < target_bytes:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    chunk = await asyncio.wait_for(queue.get(), remaining)
                except TimeoutError:
                    break
                if chunk is None:
                    ended = True
                    break
                banked.append(chunk)
                banked_bytes += len(chunk[1].pcm)
        for generation, chunk in banked:
            if (not getattr(self._player, "output_muted", False)
                    and generation == getattr(self._player, "output_generation", 0)):
                yield chunk
        if ended:
            return
        while True:
            chunk = await queue.get()
            if chunk is None:
                return
            generation, audio = chunk
            if (not getattr(self._player, "output_muted", False)
                    and generation == getattr(self._player, "output_generation", 0)):
                yield audio


__all__ = ["DesktopRealtimeBargeInDetector", "DesktopRealtimePlayback"]
