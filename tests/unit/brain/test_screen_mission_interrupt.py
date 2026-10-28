"""Speaking over a running screen mission stops or replaces it.

2026-10-26 Roberto: "the voice doesn't have any interruption layer if I wanna
change or modify the task". Only Escape or a hang-up could stop a running
computer_use mission; "stop" cancelled background agents only, and a
correction started a second mission next to the first.
"""
from __future__ import annotations

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.harness import computer_use_context as cu_ctx


class _Token:
    def __init__(self) -> None:
        self.reasons: list[str] = []

    def cancel(self, reason: str = "") -> None:
        self.reasons.append(reason)

    def is_cancelled(self) -> bool:
        return bool(self.reasons)


@pytest.fixture
def mission():
    token = _Token()
    cu_ctx.register_active_cu_token(token)
    yield token
    cu_ctx.unregister_active_cu_token(token)


@pytest.mark.parametrize(
    "text",
    [
        "wait, open edge instead",
        "no, I meant notepad",
        "actually open edge",
        "no wait play it on spotify",
        "do it in edge instead",
    ],
)
def test_a_spoken_correction_replaces_the_running_mission(mission, text):
    assert BrainManager._redirects_screen_mission(text)


@pytest.mark.parametrize("text", ["open edge", "stop the music", "what time is it"])
def test_other_words_leave_the_mission_running(mission, text):
    assert not BrainManager._redirects_screen_mission(text)


def test_nothing_is_redirected_when_no_mission_runs():
    assert not BrainManager._redirects_screen_mission("wait, open edge instead")


def test_stop_cancels_the_running_mission(mission):
    assert BrainManager._stop_screen_mission() == 1
    assert mission.reasons == ["voice_stop"]


def test_stop_with_no_mission_counts_nothing():
    assert BrainManager._stop_screen_mission() == 0
