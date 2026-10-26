"""Failed Windows HWND pixels must fall back to the visible window rectangle."""
from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace

import pytest

pytest.importorskip("PIL")
from PIL import Image, ImageGrab

from jarvis.cu.capture import grabber_for
from jarvis.cu.geometry import MonitorInfo
from jarvis.platform import window_capture


@pytest.mark.parametrize("shade", [0, 3])
def test_black_hwnd_capture_falls_back_once_with_identical_bounds(monkeypatch, shade):
    from jarvis.core import win32_dpi

    monkeypatch.setattr(window_capture, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(window_capture, "detect_platform", lambda: "win32")
    monkeypatch.setattr(win32_dpi, "per_monitor_dpi_context", nullcontext)
    native_calls = []

    def native(**kwargs):
        native_calls.append(kwargs)
        return Image.new("RGB", (80, 60), (shade,) * 3)

    monkeypatch.setattr(ImageGrab, "grab", native)
    rects = []
    actual = ((80, 60), bytes((30, 40, 50)) * (80 * 60))

    def rectangle(bbox):
        rects.append(dict(bbox))
        return actual

    target = MonitorInfo(left=316, top=21, width=80, height=60, window_handle=7)
    bbox = {"left": 316, "top": 21, "width": 80, "height": 60}
    grab = grabber_for(target, _rect_grab=rectangle)

    assert grab(bbox) == actual
    assert grab(bbox) == actual
    assert native_calls == [{"window": 7}]
    assert rects == [bbox, bbox]


def test_textured_dark_hwnd_is_preserved(monkeypatch):
    from jarvis.core import win32_dpi

    monkeypatch.setattr(window_capture, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(win32_dpi, "per_monitor_dpi_context", nullcontext)
    image = Image.new("RGB", (80, 60), (3, 3, 3))
    for y in range(15, 45):
        for x in range(15, 65):
            image.putpixel((x, y), (60, 60, 60))
    expected = image.tobytes()
    monkeypatch.setattr(ImageGrab, "grab", lambda **_kw: image)

    result = window_capture._grab_window_windows(
        7, {"left": 0, "top": 0, "width": 80, "height": 60},
    )
    assert result == ((80, 60), expected)
