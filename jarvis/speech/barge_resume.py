"""Resume an answer after a false interruption.

A barge-in stops the answer the moment the microphone confirms sustained
speech over it. Most of the time that is the user taking the floor, but a
cough, a laugh, someone else in the room or a burst of speaker echo can pass
the acoustic gates too. Without a way back, every such false interruption
ends the answer mid-sentence and leaves the user in silence.

This keeps the sentence that was cut off and decides, from what the user
actually produced afterwards, whether to say it again:

* real words arrived (a transcript that passed every guard) -> the user did
  interrupt; the cut sentence is dropped (:meth:`clear`);
* the follow-up held no words (empty transcript, speaker echo, a recognizer
  hallucination) -> resume (:meth:`take`);
* nothing was heard at all for :attr:`QUIET_WINDOW_S` -> resume as well; the
  caller polls :meth:`seconds_until_quiet_check` while it waits for speech.

Resuming is bounded: at most :attr:`MAX_CONSECUTIVE_RESUMES` in a row without
real user words in between, so an echo path that keeps triggering barge-ins
cannot replay the same sentence forever.

Standard library only, like the other turn-taking helpers in this package.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

__all__ = ["FalseInterruptionResume"]


@dataclass(frozen=True)
class _CutSentence:
    text: str
    language: str | None
    armed_at: float


class FalseInterruptionResume:
    """Remembers the sentence a barge-in cut off and when to say it again."""

    #: Silence after a barge-in that marks it as false: an interrupting user
    #: is speaking by then.
    QUIET_WINDOW_S = 2.0
    #: How long a cut sentence stays eligible. Long enough for the follow-up
    #: to pass the endpoint silence and the final transcription, short enough
    #: that an old sentence is never replayed into a new conversation turn.
    VALID_S = 8.0
    MAX_CONSECUTIVE_RESUMES = 1

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._cut: _CutSentence | None = None
        self._consecutive_resumes = 0

    def arm(self, text: str, language: str | None) -> None:
        """Remember ``text`` as the sentence a barge-in just cut off."""
        cleaned = (text or "").strip()
        if not cleaned:
            self._cut = None
            return
        self._cut = _CutSentence(cleaned, language, self._clock())

    def clear(self) -> None:
        """The user said something real: the interruption was genuine."""
        self._cut = None
        self._consecutive_resumes = 0

    def _live(self) -> _CutSentence | None:
        cut = self._cut
        if cut is None:
            return None
        if self._clock() - cut.armed_at > self.VALID_S:
            self._cut = None
            return None
        return cut

    @property
    def pending(self) -> bool:
        return self._live() is not None

    def seconds_until_quiet_check(self) -> float | None:
        """Seconds until a silent follow-up counts as a false interruption.

        ``0.0`` once the quiet window has elapsed, ``None`` when nothing is
        pending (no cut sentence, or it expired).
        """
        cut = self._live()
        if cut is None:
            return None
        return max(0.0, cut.armed_at + self.QUIET_WINDOW_S - self._clock())

    def take(self) -> tuple[str, str | None] | None:
        """Return ``(text, language)`` to speak again, or ``None``.

        Consumes the cut sentence either way. ``None`` when nothing is pending
        or the consecutive-resume budget is spent.
        """
        cut = self._live()
        self._cut = None
        if cut is None:
            return None
        if self._consecutive_resumes >= self.MAX_CONSECUTIVE_RESUMES:
            return None
        self._consecutive_resumes += 1
        return cut.text, cut.language
