"""Deterministic extraction rules: en/de/es, negation, questions, spans."""

from __future__ import annotations

import pytest

from jarvis.memory.brain_map.rules import branch_names, extract


def _kinds(text: str, role: str = "user") -> list[tuple[str, str]]:
    return [(s.kind, s.signal) for s in extract(text, role=role)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("We're blocked on the API key.", [("BLOCKER", "blocked")]),
        ("We are waiting for the vendor's answer.", [("BLOCKER", "waiting")]),
        ("I can't deploy until the cert is renewed.", [("BLOCKER", "blocked")]),
        ("We decided to use Qwen.", [("DECISION", "decided")]),
        ("Let's go with the smaller model.", [("DECISION", "decided")]),
        ("TODO: rotate the logs", [("TASK", "todo")]),
        ("Remind me to call the bank.", [("TASK", "todo")]),
        ("Next step: wire the REST route.", [("TASK", "todo")]),
        ("We shipped the orb redesign.", [("MILESTONE", "shipped")]),
        ("Done with the migration.", [("MILESTONE", "shipped")]),
        ("Wir haben uns für Postgres entschieden.", [("DECISION", "decided")]),  # i18n-allow
        ("Wir warten auf die Freigabe.", [("BLOCKER", "waiting")]),  # i18n-allow
        ("Ich muss noch die Tests schreiben.", [("TASK", "todo")]),  # i18n-allow
        ("Hemos decidido usar Postgres.", [("DECISION", "decided")]),  # i18n-allow
        ("Estamos bloqueados por la API.", [("BLOCKER", "blocked")]),  # i18n-allow
        ("Tengo que revisar el contrato.", [("TASK", "todo")]),  # i18n-allow
    ],
)
def test_signals_in_three_languages(text: str, expected: list[tuple[str, str]]) -> None:
    assert _kinds(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Should we go with Qwen?",
        "We haven't decided yet.",
        "We are not blocked on anything.",
        "ok",
        "My api_key = sk-proj-abcdefghijklmnopqrstuvwxyz123456, we decided to use it.",
    ],
)
def test_questions_negations_noise_and_secrets_yield_nothing(text: str) -> None:
    assert extract(text, role="user") == []


def test_quote_is_the_exact_span_and_role_sets_confidence() -> None:
    text = "Morning.\n- We decided to use Qwen for voice. Next step is the router!"
    user = extract(text, role="user")
    assert [s.kind for s in user] == ["DECISION", "TASK"]
    for sig in user:
        assert text[sig.char_start : sig.char_end] == sig.quote
    assert user[0].quote == "We decided to use Qwen for voice."
    assert user[0].confidence == 0.85
    assert extract(text, role="assistant")[0].confidence == 0.6


def test_resolution_and_urgency() -> None:
    (sig,) = extract("We are no longer blocked on the key.")
    assert (sig.kind, sig.signal) == ("BLOCKER", "unblocked")
    (urgent,) = extract("TODO: fix the login asap")
    assert urgent.urgency == 3


def test_branch_names() -> None:
    assert branch_names("merged feat/brain-map and fix/orb-colours.") == {
        "feat/brain-map",
        "fix/orb-colours",
    }
