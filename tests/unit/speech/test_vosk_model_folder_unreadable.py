"""An unreadable Vosk model folder skips Vosk instead of crashing voice.

Live 2026-10-05: ``data/wake_models/vosk/en`` was created while the app ran
elevated; once it ran as the user, listing it raised PermissionError out of
the wake setup and the whole speech pipeline went offline.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.speech import wake_constants


@pytest.fixture
def models_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("JARVIS__MEMORY__DATA_DIR", str(tmp_path))
    root = tmp_path / "wake_models" / "vosk"
    (root / "en" / "vosk-model-small-en" / "am").mkdir(parents=True)
    return root


def _deny(monkeypatch: pytest.MonkeyPatch, denied: Path) -> None:
    real_iterdir = Path.iterdir

    def iterdir(self: Path):
        if self == denied:
            raise PermissionError(5, "Access is denied", str(self))
        return real_iterdir(self)

    monkeypatch.setattr(Path, "iterdir", iterdir)


def test_readable_model_resolves(models_root: Path) -> None:
    assert wake_constants.resolve_vosk_model_path("en") == str(
        models_root / "en" / "vosk-model-small-en"
    )


def test_unreadable_language_folder_means_no_model(
    models_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _deny(monkeypatch, models_root / "en")
    assert wake_constants.resolve_vosk_model_path("en") is None
    assert wake_constants.resolve_vosk_model_paths("en") == []


def test_unreadable_root_means_no_model(
    models_root: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _deny(monkeypatch, models_root)
    assert wake_constants.resolve_vosk_model_path(None) is None
    assert wake_constants.resolve_vosk_model_paths(None) == []
