"""Shared set-up for the screen-context tests."""

from __future__ import annotations

import pytest

from jarvis.screen_context import uitext


@pytest.fixture(autouse=True)
def _no_real_windows_ocr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep Windows' built-in OCR out of tests that fake the other engines.

    On a Windows runner with the WinRT OCR wheels installed, the real engine
    read first and every RapidOCR and Tesseract fake went unused. Tests of the
    Windows engine reset this themselves (test_windows_ocr.py).
    """
    monkeypatch.setattr(uitext, "_WINDOWS_OCR_ENGINE", None)
    monkeypatch.setattr(uitext, "_WINDOWS_OCR_FAILURE", "switched off in tests")
