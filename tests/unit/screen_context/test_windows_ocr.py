"""Windows' built-in OCR is the first engine, and its lines are laid out.

Live 2026-10-25 on the PC, Windows.Media.Ocr read a 1536x864 screen in 54 ms
(52 lines), against RapidOCR's seconds on a CPU. Pinned here with fake WinRT
modules (the real ones exist only on Windows):

- on Windows the built-in engine reads first, and RapidOCR is not started;
- ``windows_only`` (the OCR switch off) never falls through to RapidOCR;
- a user with no OCR language falls through to the optional engines;
- OCR lines come out in the screen's layout with numbered entities, numbered
  on from the accessibility text.
"""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest
from PIL import Image

from jarvis.screen_context import uitext


class _Awaitable:
    def __init__(self, value) -> None:
        self._value = value

    def __await__(self):
        if False:  # pragma: no cover - makes this a generator
            yield
        return self._value


def _word(text: str, x: int, y: int, w: int = 40, h: int = 12) -> SimpleNamespace:
    return SimpleNamespace(text=text, bounding_rect=SimpleNamespace(x=x, y=y, width=w, height=h))


class _Engine:
    def __init__(self, lines) -> None:
        self.bitmaps: list = []
        self._lines = lines

    def recognize_async(self, bitmap):
        self.bitmaps.append(bitmap)
        return _Awaitable(SimpleNamespace(lines=self._lines))


def _install_winrt(monkeypatch, engine) -> None:
    ocr = ModuleType("winrt.windows.media.ocr")
    ocr.OcrEngine = SimpleNamespace(try_create_from_user_profile_languages=lambda: engine)

    class _Writer:
        def write_bytes(self, data) -> None:
            self.data = bytes(data)

        def detach_buffer(self):
            return self.data

    streams = ModuleType("winrt.windows.storage.streams")
    streams.DataWriter = _Writer
    imaging = ModuleType("winrt.windows.graphics.imaging")
    imaging.BitmapPixelFormat = SimpleNamespace(BGRA8="bgra8")
    imaging.SoftwareBitmap = SimpleNamespace(
        create_copy_from_buffer=lambda buf, fmt, w, h: (len(buf), fmt, w, h)
    )
    for name, module in (
        ("winrt.windows.media.ocr", ocr),
        ("winrt.windows.storage.streams", streams),
        ("winrt.windows.graphics.imaging", imaging),
    ):
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(uitext.sys, "platform", "win32")


class _RapidOcrStarted:
    def __init__(self, params=None) -> None:
        raise AssertionError("RapidOCR must not start while Windows OCR can read")


@pytest.fixture(autouse=True)
def _fresh_engines(monkeypatch) -> None:
    for name, value in (
        ("_WINDOWS_OCR_ENGINE", None),
        ("_WINDOWS_OCR_FAILURE", None),
        ("_RAPIDOCR_ENGINE", None),
        ("_RAPIDOCR_FAILURE", None),
        ("_RAPIDOCR_ORT_DLL_DIRECTORY", None),
        ("_RAPIDOCR_ORT_DLL", None),
        ("_RAPIDOCR_ORT_DLL_ATTEMPTED", False),
        ("_NO_ENGINE_LOGGED", False),
        ("_prepare_rapidocr_onnx_dlls", lambda: None),
    ):
        monkeypatch.setattr(uitext, name, value)
    monkeypatch.setitem(sys.modules, "rapidocr", SimpleNamespace(RapidOCR=_RapidOcrStarted))


_LINES = [
    SimpleNamespace(words=[_word("Dr", 10, 10), _word("Mehta", 55, 10)]),
    SimpleNamespace(
        words=[
            _word("Call", 10, 40),
            _word("+91", 55, 40),
            _word("98765", 100, 40),
            _word("43210", 145, 40),
        ]
    ),
]


def test_windows_ocr_reads_first_and_keeps_its_lines(monkeypatch) -> None:
    engine = _Engine(_LINES)
    _install_winrt(monkeypatch, engine)

    result = uitext.ocr_supplement_with_regions(Image.new("RGB", (200, 60), "white"))

    assert result.text == "Dr Mehta\nCall +91 98765 43210"
    assert result.lines[1].bounds == (10, 40, 175, 12)
    assert result.degradation is None
    assert engine.bitmaps[0] == (200 * 60 * 4, "bgra8", 200, 60)


def test_windows_only_never_starts_the_optional_engines(monkeypatch) -> None:
    _install_winrt(monkeypatch, None)  # no OCR language for this user

    result = uitext.ocr_supplement_with_regions(object(), windows_only=True)

    assert result.text == ""
    assert result.degradation is not None


def test_no_ocr_language_falls_through_to_the_optional_engines(monkeypatch) -> None:
    _install_winrt(monkeypatch, None)
    data = {
        "text": ["Hello"],
        "left": [5],
        "top": [5],
        "width": [40],
        "height": [10],
        "conf": [95],
        "block_num": [1],
        "par_num": [1],
        "line_num": [1],
    }
    monkeypatch.setitem(sys.modules, "rapidocr", None)
    monkeypatch.setitem(
        sys.modules,
        "pytesseract",
        SimpleNamespace(Output=SimpleNamespace(DICT="dict"), image_to_data=lambda *_a, **_k: data),
    )

    assert uitext.windows_ocr_ready() is False
    assert uitext.ocr_supplement_with_regions(object()).text == "Hello"


def test_rapidocr_runtime_is_preloaded_before_windows_ocr_activation(monkeypatch) -> None:
    order: list[str] = []

    ocr = ModuleType("winrt.windows.media.ocr")
    ocr.OcrEngine = SimpleNamespace(
        try_create_from_user_profile_languages=lambda: (
            order.append("windows-ocr") or _Engine(_LINES)
        )
    )
    monkeypatch.setitem(sys.modules, "winrt.windows.media.ocr", ocr)
    monkeypatch.setattr(uitext.sys, "platform", "win32")
    monkeypatch.setattr(
        uitext,
        "_prepare_rapidocr_onnx_dlls",
        lambda: order.append("rapidocr-onnx"),
        raising=False,
    )

    assert uitext.windows_ocr_ready() is True
    assert order == ["rapidocr-onnx", "windows-ocr"]


def test_off_windows_the_built_in_engine_is_never_tried(monkeypatch) -> None:
    monkeypatch.setattr(uitext.sys, "platform", "linux")

    assert uitext.windows_ocr_ready() is False


def test_ocr_lines_are_laid_out_with_numbered_entities(monkeypatch) -> None:
    _install_winrt(monkeypatch, _Engine(_LINES))
    result = uitext.ocr_supplement_with_regions(Image.new("RGB", (200, 60), "white"))

    text = uitext.ocr_layout(result, max_chars=500, first_index=3)

    assert text == "Dr Mehta\nCall {{3. +91 98765 43210}}"
