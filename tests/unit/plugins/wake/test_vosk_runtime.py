"""A broken vosk install must not grow PATH, spin, or flood the log.

Installed Windows app, 2026-10: the frozen build carried vosk's Python modules
but not its native folder, so every ``from vosk import Model`` failed. vosk's
``__init__`` prepends its folder to PATH each time it executes and a failed
import is not cached, while the wake detect loop retried on every ~30 ms audio
chunk — PATH grew past Windows' 32767-character limit and the log carried
4,353 "model unusable" warnings a day. Pinned here with fakes (any OS):

- a failed import runs once per process and leaves PATH exactly as it was;
- even unbounded re-imports (fresh runtimes) never grow PATH;
- ``os.add_dll_directory`` is called at most once per folder;
- the cheap probe says "unavailable" when the native library is missing;
- a failed model load backs off (exponential, jittered, capped), the counter
  resets on success, and each state change logs one line;
- the detect loop over hundreds of chunks attempts a broken model a bounded
  number of times and logs one warning.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
import types
from collections.abc import AsyncIterator
from pathlib import Path

import numpy as np
import pytest

from jarvis.core.protocols import AudioChunk
from jarvis.plugins.wake.vosk_kws_provider import VoskKwsProvider
from jarvis.plugins.wake.vosk_runtime import (
    LoadBackoff,
    VoskRuntime,
    VoskUnavailable,
    native_library_name,
)

MODULE = "jarvis_test_fake_vosk_pkg"  # never a real module name
ORIGINAL_PATH = os.pathsep.join(["C:\\Windows\\system32", "C:\\Windows"])


class _FakeVoskPackage:
    """A vosk-shaped package folder plus an importer that behaves like vosk.

    ``import_module`` reproduces ``vosk/__init__.py``'s Windows branch: it
    prepends its folder to PATH, then fails to load the missing native library
    (or succeeds when ``loadable``).
    """

    def __init__(self, root: Path, environ: dict[str, str], *, loadable: bool = False):
        self.dir = root / "vosk"
        self.dir.mkdir(parents=True, exist_ok=True)
        self.environ = environ
        self.loadable = loadable
        self.executions = 0
        self.dll_dirs: list[str] = []

    def find_spec(self, name: str) -> types.SimpleNamespace:
        return types.SimpleNamespace(
            submodule_search_locations=[str(self.dir)],
            origin=str(self.dir / "__init__.py"),
        )

    def import_module(self, name: str) -> types.ModuleType:
        self.executions += 1
        self.environ["PATH"] = str(self.dir) + os.pathsep + self.environ["PATH"]
        if not self.loadable:
            raise OSError(f"cannot load library '{self.dir / 'libvosk.dll'}'")
        return types.ModuleType(name)

    def add_dll_directory(self, path: str) -> object:
        self.dll_dirs.append(path)
        return object()

    def runtime(self) -> VoskRuntime:
        return VoskRuntime(
            MODULE,
            platform="win32",
            environ=self.environ,
            add_dll_directory=self.add_dll_directory,
            find_spec=self.find_spec,
            import_module=self.import_module,
        )


@pytest.fixture
def env() -> dict[str, str]:
    return {"PATH": ORIGINAL_PATH}


def test_a_failed_import_runs_once_and_never_grows_path(tmp_path, env, caplog) -> None:
    pkg = _FakeVoskPackage(tmp_path, env)
    runtime = pkg.runtime()
    caplog.set_level(logging.DEBUG, logger="jarvis.wake.vosk")

    for _ in range(1_000):  # ~30 s of 30 ms chunks in the old loop
        with pytest.raises(VoskUnavailable):
            runtime.load()

    assert env["PATH"] == ORIGINAL_PATH
    assert pkg.executions == 1
    assert runtime.import_attempts == 1
    assert pkg.dll_dirs == [str(pkg.dir)]
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "import failed" in warnings[0].getMessage()


def test_even_unbounded_reimports_leave_path_unchanged(tmp_path, env) -> None:
    """The PATH guard holds on its own, without the once-per-process cache."""
    pkg = _FakeVoskPackage(tmp_path, env)
    for _ in range(500):
        with pytest.raises(VoskUnavailable):
            pkg.runtime().load()
    assert pkg.executions == 500
    assert env["PATH"] == ORIGINAL_PATH
    assert len(env["PATH"]) < 32_767


def test_a_successful_import_keeps_the_vosk_folder_on_path_exactly_once(
    tmp_path, env
) -> None:
    pkg = _FakeVoskPackage(tmp_path, env, loadable=True)
    for _ in range(50):
        assert pkg.runtime().load().__name__ == MODULE
    entries = env["PATH"].split(os.pathsep)
    assert entries.count(str(pkg.dir)) == 1
    assert entries[1:] == ORIGINAL_PATH.split(os.pathsep)


def test_dll_directory_is_registered_only_on_windows(tmp_path, env) -> None:
    pkg = _FakeVoskPackage(tmp_path, env)
    runtime = VoskRuntime(
        MODULE,
        platform="linux",
        environ=env,
        add_dll_directory=pkg.add_dll_directory,
        find_spec=pkg.find_spec,
        import_module=pkg.import_module,
    )
    with pytest.raises(VoskUnavailable):
        runtime.load()
    assert pkg.dll_dirs == []


def test_probe_reports_a_missing_native_library_once(tmp_path, env, caplog) -> None:
    pkg = _FakeVoskPackage(tmp_path, env)
    runtime = pkg.runtime()
    caplog.set_level(logging.DEBUG, logger="jarvis.wake.vosk")

    assert [runtime.available() for _ in range(20)] == [False] * 20
    assert pkg.executions == 0  # the probe never imports
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "libvosk.dll" in warnings[0].getMessage()

    (pkg.dir / "libvosk.dll").write_bytes(b"MZ")
    assert runtime.available() is True


def test_probe_is_false_for_an_absent_package(env) -> None:
    runtime = VoskRuntime(MODULE, environ=env, find_spec=lambda name: None)
    assert runtime.available() is False


def test_native_library_names_match_vosk_open_dll() -> None:
    assert native_library_name("win32") == "libvosk.dll"
    assert native_library_name("linux") == "libvosk.so"
    assert native_library_name("darwin") == "libvosk.dyld"
    assert native_library_name("sunos5") is None


# --- LoadBackoff ---------------------------------------------------------------


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def test_backoff_doubles_up_to_the_cap(caplog) -> None:
    clock = _Clock()
    backoff = LoadBackoff(base_s=2.0, cap_s=60.0, jitter=0.2, clock=clock, rand=lambda: 0.5)
    caplog.set_level(logging.DEBUG, logger="jarvis.wake.vosk")
    delays = [backoff.failed("m", OSError("broken")) for _ in range(8)]
    assert delays == [2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0, 60.0]
    assert backoff.remaining("m") == pytest.approx(60.0)
    with pytest.raises(VoskUnavailable):
        backoff.check("m")
    clock.now += 60.0
    backoff.check("m")  # due again: no raise
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1  # one line for the state change, not one per try


@pytest.mark.parametrize(("rand", "expected"), ((0.0, 8.0), (1.0, 12.0)))
def test_backoff_jitter_is_bounded(rand: float, expected: float) -> None:
    backoff = LoadBackoff(base_s=10.0, cap_s=11.0, jitter=0.2, clock=_Clock(), rand=lambda: rand)
    # 10 s +/- 20 %, and never past the cap.
    assert backoff.failed("m", OSError("x")) == pytest.approx(min(expected, 11.0))


def test_success_resets_the_counter_and_logs_the_recovery(caplog) -> None:
    backoff = LoadBackoff(base_s=2.0, cap_s=300.0, clock=_Clock(), rand=lambda: 0.5)
    caplog.set_level(logging.DEBUG, logger="jarvis.wake.vosk")
    for _ in range(4):
        backoff.failed("m", OSError("x"))
    backoff.succeeded("m")
    backoff.succeeded("m")  # already healthy: no second line
    assert backoff.failures("m") == 0
    assert backoff.failed("m", OSError("again")) == 2.0  # fresh unit of work
    levels = [r.levelno for r in caplog.records if r.levelno >= logging.INFO]
    assert levels == [logging.WARNING, logging.INFO, logging.WARNING]


# --- the detect loop -----------------------------------------------------------


@pytest.fixture
def broken_vosk(monkeypatch) -> dict[str, int]:
    """A vosk module whose models never load (missing native library)."""
    calls = {"model": 0}
    mod = types.ModuleType("vosk")

    def _model(path: str) -> object:
        calls["model"] += 1
        raise OSError("cannot load library 'libvosk.dll'")

    def _rec(*args: object) -> object:  # pragma: no cover — never reached
        raise AssertionError("no recognizer without a model")

    mod.Model = _model
    mod.KaldiRecognizer = _rec
    mod.SetLogLevel = lambda *_a: None
    monkeypatch.setitem(sys.modules, "vosk", mod)
    return calls


def _chunk() -> AudioChunk:
    pcm = np.full(512, 3000, dtype=np.int16).tobytes()  # 32 ms at 16 kHz
    return AudioChunk(pcm=pcm, sample_rate=16000, timestamp_ns=0)


async def _detect(provider: VoskKwsProvider, n: int, clock: _Clock, step_s: float) -> list[str]:
    async def _chunks() -> AsyncIterator[AudioChunk]:
        for _ in range(n):
            clock.now += step_s
            yield _chunk()

    fired: list[str] = []

    async def _drive() -> None:
        async for keyword in provider.detect(_chunks()):
            fired.append(keyword)

    await asyncio.wait_for(_drive(), timeout=20.0)
    return fired


async def test_detect_loop_backs_off_a_broken_model_and_logs_once(
    broken_vosk, caplog
) -> None:
    clock = _Clock()
    provider = VoskKwsProvider("Hey Nova", model_path="broken-model", keyword="nova")
    provider._load_backoff = LoadBackoff(clock=clock, rand=lambda: 0.5)  # noqa: SLF001
    caplog.set_level(logging.DEBUG, logger="jarvis.wake.vosk")

    # 2,000 chunks of 32 ms = 64 s of audio. The old loop tried ~2,000 loads.
    fired = await _detect(provider, 2_000, clock, 0.032)

    assert fired == []
    # Exponential from 2 s: tries at 0, 2, 6, 14, 30, 62 s -> at most 6.
    assert 1 <= broken_vosk["model"] <= 6
    warnings = [
        r for r in caplog.records
        if r.levelno >= logging.WARNING and r.name == "jarvis.wake.vosk"
    ]
    assert len(warnings) == 1, [r.getMessage() for r in warnings]


async def test_a_frozen_clock_means_exactly_one_load_attempt(broken_vosk) -> None:
    clock = _Clock()
    provider = VoskKwsProvider("Hey Nova", model_path="broken-model", keyword="nova")
    provider._load_backoff = LoadBackoff(clock=clock, rand=lambda: 0.5)  # noqa: SLF001
    await _detect(provider, 500, clock, 0.0)
    assert broken_vosk["model"] == 1


async def test_stop_starts_a_fresh_unit_of_work(broken_vosk) -> None:
    clock = _Clock()
    provider = VoskKwsProvider("Hey Nova", model_path="broken-model", keyword="nova")
    provider._load_backoff = LoadBackoff(clock=clock, rand=lambda: 0.5)  # noqa: SLF001
    await _detect(provider, 10, clock, 0.0)
    await provider.stop()
    await _detect(provider, 10, clock, 0.0)
    assert broken_vosk["model"] == 2
