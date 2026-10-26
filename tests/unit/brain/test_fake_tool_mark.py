"""Tool markers a model writes are removed (jarvis/brain/fake_tool_mark.py)."""

from __future__ import annotations

from jarvis.agent_chat.runner_brain import _assistant_prose
from jarvis.brain.fake_tool_mark import strip_fake_tool_marks


def test_a_marker_line_is_removed() -> None:
    text = "[used tool app_ui_click]\nClicking on Roberto's profile now."
    assert strip_fake_tool_marks(text) == ("Clicking on Roberto's profile now.", True)


def test_a_marker_with_a_summary_is_removed() -> None:
    text = "[used tool window_control: maximize calculator]\nDone, Sir."
    assert strip_fake_tool_marks(text) == ("Done, Sir.", True)


def test_brackets_inside_a_sentence_stay() -> None:
    text = "The log says [used tool x] once."
    assert strip_fake_tool_marks(text) == (text, False)


def test_history_keeps_real_rounds_and_drops_written_markers() -> None:
    content = [
        {"type": "text", "text": "[used tool app_ui_click]\nClicking now."},
        {"type": "tool_use", "name": "click_element", "input": {"name": "Roberto"}},
    ]
    prose = _assistant_prose(content)
    assert "app_ui_click" not in prose
    assert prose.startswith("Clicking now.\n[used tool click_element")
