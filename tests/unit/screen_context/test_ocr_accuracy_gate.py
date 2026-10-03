"""OCR accuracy gate over labelled, rendered UI-text fixtures.

The fixtures are drawn at test time (no binary files in the repo), each with
its exact label, at UI-like sizes. The gate measures normalised character
accuracy of ``ocr_supplement_with_regions`` against the label and fails below
``TARGET_CHAR_ACCURACY``.

It needs a real OCR engine (``pytesseract`` plus the Tesseract binary), so it
skips on machines without one, including CI. Run it on the target device:

    pytest tests/unit/screen_context/test_ocr_accuracy_gate.py -rs

``TARGET_CHAR_ACCURACY`` is a provisional number, not a product decision; the
measured value is printed so the threshold can be set from real results.
"""
from __future__ import annotations

import difflib
import shutil

import pytest

from jarvis.screen_context.uitext import UNREADABLE_MARK, ocr_supplement_with_regions

TARGET_CHAR_ACCURACY = 0.95

FIXTURES = [
    ("Save changes before closing?", 28, (255, 255, 255), (20, 20, 20)),
    ("Invoice total: 1,284.50 EUR", 26, (250, 250, 250), (30, 30, 30)),
    ("Meeting moved to Friday 14:30", 24, (32, 33, 36), (232, 234, 237)),
    ("Download complete - 3 files", 22, (240, 244, 248), (10, 60, 120)),
    ("Settings > Privacy > Microphone", 24, (255, 255, 255), (0, 0, 0)),
]


def _engine_available() -> bool:
    try:
        import pytesseract  # noqa: F401
    except ImportError:
        return False
    return shutil.which("tesseract") is not None


def _render(text: str, size: int, bg: tuple, fg: tuple):
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.load_default(size=size)
    left, top, right, bottom = font.getbbox(text)
    image = Image.new("RGB", (right - left + 40, bottom - top + 30), bg)
    ImageDraw.Draw(image).text((20 - left, 15 - top), text, font=font, fill=fg)
    return image


def _normalise(text: str) -> str:
    return " ".join(text.split()).casefold()


@pytest.mark.skipif(
    not _engine_available(), reason="no OCR engine installed (pytesseract + tesseract)"
)
def test_rendered_ui_text_meets_the_accuracy_target() -> None:
    scores: list[float] = []
    for label, size, bg, fg in FIXTURES:
        result = ocr_supplement_with_regions(_render(label, size, bg, fg))
        got = _normalise(result.text.replace(UNREADABLE_MARK, ""))
        scores.append(difflib.SequenceMatcher(None, _normalise(label), got).ratio())
    mean = sum(scores) / len(scores)
    print(
        f"OCR char accuracy over {len(scores)} fixtures: {mean:.3f} "
        f"(target {TARGET_CHAR_ACCURACY})"
    )
    assert mean >= TARGET_CHAR_ACCURACY, [round(s, 3) for s in scores]
