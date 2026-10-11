"""Spoken consent for ask-tier Jarvis tools that an outside agent calls.

With Hermes as the brain (ADR-0042) the model reaches Jarvis's own tools over
MCP (``jarvis.mcp.jarvis_tools_server``). An ask-tier tool there must not run
on the model's word alone: the model could claim "the user said yes" in a
tool argument, and nothing could tell. This module is the piece of the gate
the model cannot reach.

* :func:`mint` issues an opaque, single-use token for ONE deferred call. It is
  bound to the tool name and a digest of the exact arguments, and it expires
  after :data:`CONSENT_TTL_S`.
* :func:`note_user_turn` is called by the conversation path for every real
  user turn, voice or typed (``BrainManager.generate``). The model has no
  handle on it.
* :func:`consent_for` decides a retry: only the first user turn that arrived
  AFTER the token was minted counts. "yes" grants, "no" denies, a hedge
  ("wait", "hmm") keeps waiting, and anything else means the user moved on,
  which denies, the same rule ADR-0042 applies to Hermes's own approvals.

The yes/no classifier is the deterministic one the voice flows already use
(``jarvis.voice.echo_confirmation.classify_response``, no LLM call). A turn is
classified once, when it is noted, and only while a token is outstanding: an
idle Jarvis keeps no user text here at all.

State is in-process on purpose. The MCP server runs inside the app's web
server, the same process as the brain manager, and a token must never survive
a restart.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import threading
import time
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Literal
from uuid import UUID

log = logging.getLogger(__name__)

#: The one clock mints and user turns share (a seam for tests).
_clock = time.monotonic

#: How long the user has to answer before the token is dead.
CONSENT_TTL_S: Final[float] = 120.0

#: The answers kept while a token waits; older ones can no longer matter
#: because only the first turn after a mint decides.
_MAX_TURNS: Final[int] = 16

#: Languages whose confirmation phrases are checked. The turn's language is
#: not known yet when a turn is noted, so all are tried with the classifier's
#: own safety order: a veto in any language wins.
_LANGUAGES: Final[tuple[str, ...]] = ("de", "en", "es")

Answer = Literal["confirm", "veto", "ambiguous", "unknown"]
ConsentStatus = Literal[
    "granted",  # the user said yes after the mint; run it once
    "denied",  # the user said no, or moved on to something else
    "awaiting",  # no user turn since the mint; ask the user first
    "unclear",  # only hedges so far; ask again
    "expired",  # the token outlived CONSENT_TTL_S
    "mismatch",  # a different tool or different arguments than were asked about
    "unknown",  # never minted, or already used
]


@dataclass(frozen=True, slots=True)
class ConsentRequest:
    """One deferred call waiting for the user's answer."""

    token: str
    tool_name: str
    args_digest: str
    trace_id: UUID
    minted_at: float
    expires_at: float


@dataclass(frozen=True, slots=True)
class ConsentVerdict:
    status: ConsentStatus
    request: ConsentRequest | None = None


_lock = threading.Lock()
_pending: dict[str, ConsentRequest] = {}
_turns: deque[tuple[float, Answer]] = deque(maxlen=_MAX_TURNS)


#: Free-text fields where the model explains itself. They change nothing the
#: tool does, and a model rewords them on the retry ("taking a photo to see"
#: becomes "to see what they hold"), which refused a call the user had just
#: agreed to. Every other argument still has to match exactly, and the call
#: that runs is always the deferred one, with the arguments the user heard.
NARRATIVE_ARGS: Final[frozenset[str]] = frozenset({"reason", "rationale", "why", "explanation"})


def args_digest(args: Mapping[str, Any]) -> str:
    """A stable digest of what the call does (key order and narration ignored)."""
    canonical = json.dumps(
        {k: v for k, v in args.items() if k not in NARRATIVE_ARGS},
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def classify_answer(text: str) -> Answer:
    """The user's turn as yes / no / hedge / something else, in any language."""
    # Lazy: ``jarvis.voice`` pulls in the self-mod package at import time.
    from jarvis.voice.echo_confirmation import classify_response

    verdicts = {classify_response(text, language=lang) for lang in _LANGUAGES}
    for answer in ("veto", "confirm", "ambiguous"):
        if answer in verdicts:
            return answer  # type: ignore[return-value]
    return "unknown"


def mint(
    tool_name: str,
    args: Mapping[str, Any],
    *,
    trace_id: UUID,
    now: float | None = None,
    ttl_s: float = CONSENT_TTL_S,
) -> ConsentRequest:
    """Issue a token for one deferred call of ``tool_name`` with ``args``."""
    minted_at = _clock() if now is None else now
    request = ConsentRequest(
        token=secrets.token_urlsafe(24),
        tool_name=tool_name,
        args_digest=args_digest(args),
        trace_id=trace_id,
        minted_at=minted_at,
        expires_at=minted_at + ttl_s,
    )
    with _lock:
        _pending[request.token] = request
    return request


def note_user_turn(text: str, at: float | None = None) -> None:
    """Record that a real user turn arrived. Cheap no-op while nothing waits."""
    with _lock:
        if not _pending:
            return
    answer = classify_answer(text or "")
    stamp = _clock() if at is None else at
    with _lock:
        _turns.append((stamp, answer))


def note_conversation_turn(text: str, *, in_conversation: bool) -> None:
    """The conversation path's hook (``BrainManager.generate``): note a REAL user turn.

    A typed chat turn counts only when a person sent it
    (``ChatTurn.direct_user``); an agent's message or a scheduled task never
    does. Outside a chat (voice, the CLI) a turn counts when it belongs to the
    conversation, which leaves out one-shot internal prompts such as the
    skill ranker. Never raises: a failure here only means no consent can be
    granted, which fails closed.
    """
    try:
        from jarvis.core.chat_turn import current_chat_turn

        turn = current_chat_turn.get()
        if turn.direct_user if turn is not None else in_conversation:
            note_user_turn(text)
    except Exception:  # noqa: BLE001 — a turn must never die here; the gate stays closed
        log.warning("spoken consent: could not note the user turn", exc_info=True)


def consent_for(
    token: str,
    *,
    tool_name: str,
    args: Mapping[str, Any],
    now: float | None = None,
) -> ConsentVerdict:
    """Decide a retry that carries ``token``.

    Every final verdict (granted, denied, expired, mismatch) consumes the
    token; ``awaiting`` and ``unclear`` keep it, so the real answer can still
    arrive. The caller runs the deferred call only on ``granted``.
    """
    current = _clock() if now is None else now
    with _lock:
        request = _pending.get(token)
        if request is None:
            return ConsentVerdict("unknown")
        if current >= request.expires_at:
            del _pending[token]
            return ConsentVerdict("expired", request)
        if request.tool_name != tool_name or request.args_digest != args_digest(args):
            del _pending[token]
            return ConsentVerdict("mismatch", request)
        answers = [answer for at, answer in _turns if at > request.minted_at]
        if not answers:
            return ConsentVerdict("awaiting", request)
        for answer in answers:
            if answer == "ambiguous":
                continue
            del _pending[token]
            return ConsentVerdict("granted" if answer == "confirm" else "denied", request)
        return ConsentVerdict("unclear", request)


def expire_stale(now: float | None = None) -> list[ConsentRequest]:
    """Drop and return every token past its deadline (their calls never run)."""
    current = _clock() if now is None else now
    with _lock:
        stale = [request for request in _pending.values() if current >= request.expires_at]
        for request in stale:
            del _pending[request.token]
        if not _pending:
            _turns.clear()
    return stale


def _reset_for_tests() -> None:
    with _lock:
        _pending.clear()
        _turns.clear()


__all__ = [
    "CONSENT_TTL_S",
    "ConsentRequest",
    "ConsentStatus",
    "ConsentVerdict",
    "args_digest",
    "classify_answer",
    "consent_for",
    "expire_stale",
    "mint",
    "note_conversation_turn",
    "note_user_turn",
]
