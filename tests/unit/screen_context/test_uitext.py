"""OCR geometry and accessibility-density guards for Screen Context."""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from jarvis.screen_context import uitext
from jarvis.screen_context.models import DegradationCode
from jarvis.screen_context.uitext import (
    UNREADABLE_MARK,
    ocr_engine_status,
    ocr_supplement_with_regions,
    text_is_sparse,
)


@pytest.fixture(autouse=True)
def _no_rapidocr(monkeypatch) -> None:
    """The Tesseract tests below run as if RapidOCR were not installed."""
    monkeypatch.setitem(sys.modules, "rapidocr", None)
    monkeypatch.setattr(uitext, "_RAPIDOCR_ENGINE", None)


def test_ocr_words_are_grouped_into_redactable_lines(monkeypatch) -> None:
    data = {
        "text": ["Card", "4111", "1111"],
        "left": [10, 55, 100],
        "top": [20, 20, 20],
        "width": [35, 35, 35],
        "height": [12, 12, 12],
        "block_num": [1, 1, 1],
        "par_num": [1, 1, 1],
        "line_num": [1, 1, 1],
    }
    fake = SimpleNamespace(
        Output=SimpleNamespace(DICT="dict"),
        image_to_data=lambda _image, *, output_type: data,
    )
    monkeypatch.setitem(sys.modules, "pytesseract", fake)

    result = ocr_supplement_with_regions(object())

    assert result.text == "Card 4111 1111"
    assert result.regions[0].bounds == (10, 20, 125, 12)
    assert result.degradation is None


def test_sparse_text_threshold_scales_with_full_monitor_area() -> None:
    text = "A few toolbar labels that exceed the legacy fixed threshold"

    assert not text_is_sparse(text, image_size=(400, 200))
    assert text_is_sparse(text, image_size=(2048, 1152))


def _fake_engine(monkeypatch, data: dict) -> None:
    fake = SimpleNamespace(
        Output=SimpleNamespace(DICT="dict"),
        image_to_data=lambda _image, *, output_type: data,
    )
    monkeypatch.setitem(sys.modules, "pytesseract", fake)


def _line(words: list[str], confs: list[float]) -> dict:
    n = len(words)
    return {
        "text": words,
        "conf": confs,
        "left": [10 + 40 * i for i in range(n)],
        "top": [20] * n,
        "width": [35] * n,
        "height": [12] * n,
        "block_num": [1] * n,
        "par_num": [1] * n,
        "line_num": [1] * n,
    }


def test_confident_read_has_no_degradation_and_reports_confidence(monkeypatch) -> None:
    _fake_engine(monkeypatch, _line(["Invoice", "total", "42.00"], [96, 91, 88]))

    result = ocr_supplement_with_regions(object())

    assert result.text == "Invoice total 42.00"
    assert result.degradation is None
    assert result.confidence == pytest.approx((96 + 91 + 88) / 3)


def test_low_confidence_words_are_masked_never_passed_as_fact(monkeypatch) -> None:
    _fake_engine(monkeypatch, _line(["Pay", "S7B.0O", "now"], [93, 12, 90]))

    result = ocr_supplement_with_regions(object())

    assert "S7B.0O" not in result.text
    assert result.text == f"Pay {UNREADABLE_MARK} now"
    assert result.degradation is not None
    assert result.degradation.code is DegradationCode.OCR_LOW_CONFIDENCE
    # The redaction geometry still covers the whole line, unread word included.
    assert result.regions[0].text == "Pay S7B.0O now"


def test_layout_boxes_without_a_word_score_are_ignored(monkeypatch) -> None:
    data = _line(["", "Ready"], [-1, 95])
    _fake_engine(monkeypatch, data)

    result = ocr_supplement_with_regions(object())

    assert result.text == "Ready"
    assert result.degradation is None


def test_engine_without_confidence_keeps_the_text(monkeypatch) -> None:
    data = _line(["Settings"], [0])
    del data["conf"]
    _fake_engine(monkeypatch, data)

    result = ocr_supplement_with_regions(object())

    assert result.text == "Settings"
    assert result.confidence is None


def test_missing_engine_is_a_typed_unavailable(monkeypatch) -> None:
    monkeypatch.setitem(sys.modules, "pytesseract", None)

    result = ocr_supplement_with_regions(object())

    assert result.text == ""
    assert result.degradation is not None
    assert result.degradation.code is DegradationCode.OCR_UNAVAILABLE


def _fake_rapidocr(monkeypatch, word_results: tuple) -> list[dict]:
    calls: list[dict] = []

    class _RapidOCR:
        def __init__(self, params=None) -> None:
            self.params = params

        def __call__(self, _image, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(word_results=word_results)

    monkeypatch.setitem(sys.modules, "rapidocr", SimpleNamespace(RapidOCR=_RapidOCR))
    return calls


def _quad(left: int, top: int, width: int, height: int) -> list[list[int]]:
    right, bottom = left + width, top + height
    return [[left, top], [right, top], [right, bottom], [left, bottom]]


def test_rapidocr_is_preferred_and_scores_use_the_same_scale(monkeypatch) -> None:
    calls = _fake_rapidocr(monkeypatch, (
        (("Invoice", 0.96, _quad(10, 20, 60, 12)), ("total", 0.91, _quad(75, 20, 40, 12))),
        (("42.00", 0.88, _quad(10, 40, 40, 12)),),
    ))
    monkeypatch.setitem(sys.modules, "pytesseract", None)

    result = ocr_supplement_with_regions(object())

    assert result.text == "Invoice total\n42.00"
    assert [r.bounds for r in result.regions] == [(10, 20, 105, 12), (10, 40, 40, 12)]
    assert result.confidence == pytest.approx((96 + 91 + 88) / 3)
    assert result.degradation is None
    # The engine's own line filter is off, so an unsure line still gets redacted.
    assert calls[0]["text_score"] == 0.0 and calls[0]["return_word_box"] is True


def test_rapidocr_low_score_word_is_masked_but_still_redacted(monkeypatch) -> None:
    _fake_rapidocr(monkeypatch, (
        (("Pay", 0.93, _quad(10, 20, 30, 12)), ("S7B.0O", 0.12, _quad(45, 20, 50, 12))),
    ))

    result = ocr_supplement_with_regions(object())

    assert result.text == f"Pay {UNREADABLE_MARK}"
    assert result.regions[0].text == "Pay S7B.0O"
    assert result.degradation is not None
    assert result.degradation.code is DegradationCode.OCR_LOW_CONFIDENCE


def test_engine_status_names_what_is_missing(monkeypatch) -> None:
    monkeypatch.setattr("importlib.util.find_spec", lambda _name: None)

    available, reason = ocr_engine_status()

    assert available is False
    assert "RapidOCR" in reason
