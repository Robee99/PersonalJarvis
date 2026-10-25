"""Captures: "remember that ..." kept at once, without a model (prompt pack 05).

1. The trigger phrases capture; questions and ordinary speech do not.
2. The fact goes to MEMORY.md and to a dated note under captures/.
3. A capture never fails silently, and the guard's refusal is said.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jarvis.memory.capture import capture_reflex, capture_text


class _Notebook:
    def __init__(self, error: Exception | None = None) -> None:
        self.kept: list[str] = []
        self.error = error

    def __call__(self, fact: str, *, evidence: str) -> None:
        del evidence
        if self.error is not None:
            raise self.error
        self.kept.append(fact)


def _now() -> datetime:
    return datetime(2026, 10, 25, 9, 30)


def test_trigger_phrases_capture_and_questions_do_not() -> None:
    assert capture_text("Remember that the finish window should be 900 milliseconds") == (
        "The finish window should be 900 milliseconds"
    )
    assert capture_text("Jarvis, note to self: call Dana on Monday.") == "Call Dana on Monday"
    assert capture_text("don't let me forget the backup") == "The backup"
    assert capture_text("do you remember that song?") is None
    assert capture_text("remember that?") is None
    assert capture_text("what is the weather") is None


def test_a_capture_lands_in_memory_and_as_a_dated_note(tmp_path: Path) -> None:
    notebook = _Notebook()

    reply = capture_reflex(
        "remember that the finish window should be 900 milliseconds",
        vault_root=tmp_path, remember=notebook, clock=_now,
    )

    assert reply == "Noted, Sir."
    assert notebook.kept == ["The finish window should be 900 milliseconds"]
    [note] = (tmp_path / "captures").iterdir()
    assert note.name == "2026-10-25-the-finish-window-should-be-900.md"
    body = note.read_text(encoding="utf-8")
    assert "created: 2026-10-25 09:30" in body
    assert "The finish window should be 900 milliseconds" in body


def test_a_second_capture_never_overwrites_the_first(tmp_path: Path) -> None:
    for _ in range(2):
        capture_reflex(
            "note to self buy milk", vault_root=tmp_path, remember=_Notebook(), clock=_now
        )
    assert len(list((tmp_path / "captures").iterdir())) == 2


def test_a_lost_capture_is_said_out_loud(tmp_path: Path) -> None:
    blocked = tmp_path / "file-not-folder"
    blocked.write_text("x", encoding="utf-8")

    reply = capture_reflex(
        "remember that the vault is read-only",
        vault_root=blocked, remember=_Notebook(LookupError("not running")), clock=_now,
    )

    assert reply is not None
    assert reply.startswith("I could not save that, Sir:")
    assert "notebook is not running" in reply


def test_the_guard_refusal_is_said_and_nothing_is_written(tmp_path: Path) -> None:
    reply = capture_reflex(
        "remember that my key is sk-test",
        vault_root=tmp_path, remember=_Notebook(ValueError("that looks like a secret")),
        clock=_now,
    )
    assert reply == "I will not write that down, Sir: that looks like a secret."
    assert not (tmp_path / "captures").exists()
