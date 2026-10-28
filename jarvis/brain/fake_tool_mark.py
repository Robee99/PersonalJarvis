"""Tool markers a model writes into its own reply.

The chat history folds each past tool round into a line "[used tool X: ...]"
(jarvis/agent_chat/runner_brain.py). Live 2026-10-26 Gemma answered "click on
roberto" three times with the text "[used tool app_ui_click]\nClicking on
Roberto's profile now." and no call: it copied the marker. Written by the
model, the marker claims an action that never ran.
"""

from __future__ import annotations

import re

FAKE_TOOL_MARK = re.compile(r"^[ \t]*\[used tool [^\]\n]*\][ \t]*(?:\n|$)", re.MULTILINE)


def strip_fake_tool_marks(text: str) -> tuple[str, bool]:
    """``text`` without written tool markers, and whether it had any."""
    cleaned = FAKE_TOOL_MARK.sub("", text or "")
    if cleaned == (text or ""):
        return text, False
    return cleaned.strip(), True


__all__ = ["FAKE_TOOL_MARK", "strip_fake_tool_marks"]
