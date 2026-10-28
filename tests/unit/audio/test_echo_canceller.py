"""Optional microphone echo cancellation (``jarvis.audio.echo_canceller``).

The engine (the ``livekit`` package's WebRTC audio processing module) is
optional: without it, or with ``[audio].echo_cancellation`` off, capture must
be byte-for-byte unchanged. With it, every played block is the far-end
reference and every captured block is processed against it in exact 10 ms
frames. The fake engine below records what it is given; one test runs the real
engine when it is installed.
"""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from jarvis.audio import echo_canceller
from jarvis.audio.capture import MicrophoneCapture
from jarvis.dictation.audio_preprocessing import assess_desktop_audio_preprocessing


class _FakeAudioFrame:
    def __init__(self, data, sample_rate: int, num_channels: int, samples_per_channel: int) -> None:
        assert len(data) == samples_per_channel * num_channels * 2
        self._data = bytearray(data)
        self.sample_rate = sample_rate
        self.num_channels = num_channels

    @property
    def data(self) -> memoryview:
        return memoryview(self._data).cast("h")


class _FakeAPM:
    """Halves every captured sample; records reverse frames and delay hints."""

    instances: list[_FakeAPM] = []

    def __init__(self, *, echo_cancellation: bool = False, **_kwargs: bool) -> None:
        assert echo_cancellation is True
        self.reverse: list[tuple[int, int]] = []
        self.delays: list[int] = []
        self.processed = 0
        _FakeAPM.instances.append(self)

    def set_stream_delay_ms(self, delay_ms: int) -> None:
        self.delays.append(delay_ms)

    def process_stream(self, frame: _FakeAudioFrame) -> None:
        samples = np.frombuffer(bytes(frame._data), dtype="<i2") // 2
        frame._data[:] = samples.astype("<i2").tobytes()
        self.processed += 1

    def process_reverse_stream(self, frame: _FakeAudioFrame) -> None:
        self.reverse.append((frame.sample_rate, len(frame._data) // 2))


class _FakeRtc:
    AudioFrame = _FakeAudioFrame
    AudioProcessingModule = _FakeAPM


@pytest.fixture
def fake_engine(monkeypatch: pytest.MonkeyPatch):
    _FakeAPM.instances = []
    monkeypatch.setattr(echo_canceller, "_rtc", _FakeRtc)
    monkeypatch.setattr(echo_canceller, "_import_failed", False)
    monkeypatch.setattr(echo_canceller, "_enabled", True)
    monkeypatch.setattr(echo_canceller, "_cancellers", echo_canceller.weakref.WeakSet())
    return _FakeRtc


@pytest.fixture
def no_engine(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(echo_canceller, "_rtc", None)
    monkeypatch.setattr(echo_canceller, "_import_failed", True)
    monkeypatch.setattr(echo_canceller, "_cancellers", echo_canceller.weakref.WeakSet())


def _pcm(samples: int, value: int = 1000) -> bytes:
    return np.full(samples, value, dtype="<i2").tobytes()


def test_disabled_setting_never_cancels(fake_engine, monkeypatch) -> None:
    monkeypatch.setattr(echo_canceller, "_enabled", False)

    assert echo_canceller.open_capture(16_000, 1) is None
    assert echo_canceller.running() is False


def test_missing_engine_leaves_capture_alone(no_engine, monkeypatch) -> None:
    monkeypatch.setattr(echo_canceller, "_enabled", True)

    assert echo_canceller.open_capture(16_000, 1) is None
    assert echo_canceller.is_active() is False


def test_unsupported_streams_are_not_cancelled(fake_engine) -> None:
    assert echo_canceller.open_capture(16_000, 2) is None
    assert echo_canceller.open_capture(44_100, 1) is None


def test_capture_is_processed_in_whole_10ms_frames(fake_engine) -> None:
    canceller = echo_canceller.open_capture(16_000, 1)
    assert canceller is not None

    # Two 32 ms capture blocks = 1024 samples -> six 160-sample frames now,
    # the 64-sample remainder waits for the next block.
    first = canceller.process(_pcm(512))
    second = canceller.process(_pcm(512))

    assert len(first) == 3 * 160 * 2
    assert len(second) == 3 * 160 * 2
    assert set(np.frombuffer(first + second, dtype="<i2")) == {500}
    assert _FakeAPM.instances[0].processed == 6


def test_playback_is_broadcast_as_the_far_end_reference(fake_engine) -> None:
    first = echo_canceller.open_capture(16_000, 1)
    second = echo_canceller.open_capture(16_000, 1)
    assert first is not None and second is not None
    assert echo_canceller.is_active() is True

    # 60 ms of stereo at 48 kHz, exactly as the player writes it.
    block = np.full((2880, 2), 0.25, dtype=np.float32)
    echo_canceller.feed_playback(block, 48_000, output_latency_s=0.12)
    first.process(_pcm(160))

    for apm in _FakeAPM.instances:
        assert apm.reverse == [(48_000, 480)] * 6
    # Delay hint: output latency plus the capture allowance.
    assert _FakeAPM.instances[0].delays == [130]


def test_odd_output_rates_are_resampled_for_the_reference(fake_engine) -> None:
    canceller = echo_canceller.open_capture(16_000, 1)
    assert canceller is not None

    echo_canceller.feed_playback(np.zeros(2205, dtype=np.float32), 22_050, 0.0)

    # 100 ms at 22.05 kHz -> 1600 samples at 16 kHz -> ten 10 ms frames.
    assert _FakeAPM.instances[0].reverse == [(16_000, 160)] * 10


def test_closed_canceller_passes_audio_through(fake_engine) -> None:
    canceller = echo_canceller.open_capture(16_000, 1)
    assert canceller is not None
    canceller.close()

    pcm = _pcm(512)
    assert canceller.process(pcm) == pcm
    assert echo_canceller.is_active() is False


@pytest.mark.asyncio
async def test_microphone_capture_delivers_the_cancelled_signal(fake_engine) -> None:
    mic = MicrophoneCapture()
    mic._loop = asyncio.get_running_loop()
    mic._echo_canceller = echo_canceller.open_capture(16_000, 1)

    mic._callback(_pcm(480), 480, None, None)
    await asyncio.sleep(0)

    chunk = mic._queue.get_nowait()
    assert set(np.frombuffer(chunk.pcm, dtype="<i2")) == {500}


@pytest.mark.asyncio
async def test_a_failing_canceller_falls_back_to_the_raw_microphone(fake_engine) -> None:
    class _Broken:
        closed = False

        def process(self, _pcm: bytes) -> bytes:
            raise RuntimeError("engine fault")

        def close(self) -> None:
            self.closed = True

    broken = _Broken()
    mic = MicrophoneCapture()
    mic._loop = asyncio.get_running_loop()
    mic._echo_canceller = broken  # type: ignore[assignment]

    raw = _pcm(480)
    mic._callback(raw, 480, None, None)
    await asyncio.sleep(0)

    assert mic._queue.get_nowait().pcm == raw
    assert mic._echo_canceller is None
    assert broken.closed is True


def test_dictation_audit_reports_running_cancellation_honestly() -> None:
    assert (
        assess_desktop_audio_preprocessing(
            echo_cancellation_requested=True, echo_cancellation_running=True
        ).acoustic_echo_cancellation
        == "on"
    )
    assert (
        assess_desktop_audio_preprocessing(
            echo_cancellation_requested=True
        ).acoustic_echo_cancellation
        == "unavailable"
    )
    assert (
        assess_desktop_audio_preprocessing(
            echo_cancellation_requested=False, echo_cancellation_running=True
        ).acoustic_echo_cancellation
        == "off"
    )


def test_real_engine_removes_played_audio_from_the_microphone(monkeypatch) -> None:
    rtc = pytest.importorskip("livekit.rtc")
    monkeypatch.setattr(echo_canceller, "_rtc", rtc)
    monkeypatch.setattr(echo_canceller, "_import_failed", False)
    monkeypatch.setattr(echo_canceller, "_enabled", True)
    monkeypatch.setattr(echo_canceller, "_cancellers", echo_canceller.weakref.WeakSet())
    canceller = echo_canceller.open_capture(16_000, 1)
    assert canceller is not None

    rate = 16_000
    rng = np.random.default_rng(3)
    seconds = 6
    t = np.arange(seconds * rate) / rate
    played = (rng.standard_normal(t.size) * 0.2 * (0.5 + 0.5 * np.sin(2 * np.pi * 3 * t))).astype(
        np.float32
    )
    delay = int(0.04 * rate)
    echo = np.zeros_like(played)
    echo[delay:] = 0.6 * played[:-delay]
    out = bytearray()
    for start in range(0, played.size, 480):  # 30 ms blocks
        echo_canceller.feed_playback(played[start : start + 480], rate, 0.03)
        mic = (echo[start : start + 480] * 32767.0).astype("<i2").tobytes()
        out += canceller.process(mic)
    canceller.close()

    cleaned = np.frombuffer(bytes(out), dtype="<i2").astype(np.float32) / 32768.0
    tail = slice(3 * rate, 5 * rate)  # after convergence
    before = float(np.sqrt(np.mean(np.square(echo[tail]))))
    after = float(np.sqrt(np.mean(np.square(cleaned[tail]))))
    assert after < before * 0.1


def test_fully_suppressed_echo_never_reads_as_a_withheld_microphone(
    fake_engine, monkeypatch
) -> None:
    # Exact digital zeros mean "the OS withholds the microphone" to the
    # capture's silence guard; suppressed echo must not look like that.
    monkeypatch.setattr(
        _FakeAPM,
        "process_stream",
        lambda self, frame: frame._data.__setitem__(slice(None), bytes(len(frame._data))),
    )
    canceller = echo_canceller.open_capture(16_000, 1)
    assert canceller is not None

    cleaned = canceller.process(_pcm(160))
    silent = canceller.process(bytes(320))

    assert any(cleaned)
    assert not any(silent), "real digital silence still passes through as silence"
