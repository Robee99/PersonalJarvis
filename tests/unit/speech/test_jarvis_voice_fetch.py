"""The British "Jarvis" voice is fetched on top of a working local voice, never instead of it."""

from __future__ import annotations

import pytest

from jarvis.speech import local_install
from jarvis.speech.local_models import KOKORO_JARVIS_VOICE, LocalModelStatus, get_local_provider


def _status(ready: bool) -> LocalModelStatus:
    return LocalModelStatus(
        runtime="sherpa-onnx",
        engine_installed=ready,
        model_present=ready,
        model_label="Piper voices",
        detail="",
    )


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.setattr(
        local_install, "start_install", lambda pid: calls.append(pid) or {"state": "running"}
    )
    return calls


def test_the_british_voice_is_optional_for_readiness() -> None:
    entry = get_local_provider("piper-local")
    assert entry is not None
    assert KOKORO_JARVIS_VOICE in entry.extra_bundles
    assert KOKORO_JARVIS_VOICE not in entry.bundles


def test_a_working_local_voice_without_george_starts_the_download(
    monkeypatch: pytest.MonkeyPatch, started: list[str]
) -> None:
    monkeypatch.setattr(local_install, "local_status", lambda _pid: _status(True))
    monkeypatch.setattr(local_install, "_extras_missing", lambda _entry: [KOKORO_JARVIS_VOICE])
    assert local_install.fetch_missing_extras("piper-local") is True
    assert started == ["piper-local"]


@pytest.mark.parametrize(("ready", "missing"), [(False, True), (True, False)])
def test_nothing_starts_without_a_working_voice_or_when_george_is_there(
    monkeypatch: pytest.MonkeyPatch, started: list[str], ready: bool, missing: bool
) -> None:
    monkeypatch.setattr(local_install, "local_status", lambda _pid: _status(ready))
    monkeypatch.setattr(
        local_install, "_extras_missing", lambda _e: [KOKORO_JARVIS_VOICE] if missing else []
    )
    assert local_install.fetch_missing_extras("piper-local") is False
    assert started == []


def test_an_optional_download_failure_does_not_fail_the_install(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetched: list[str] = []

    def download(model_id: str, **_kw: object) -> None:
        if model_id == KOKORO_JARVIS_VOICE:
            raise OSError("offline")
        fetched.append(model_id)

    monkeypatch.setattr("jarvis.speech.sherpa_models.download_sherpa_model", download)
    entry = get_local_provider("piper-local")
    assert entry is not None
    local_install._download_model(entry)
    assert fetched == list(entry.bundles)
