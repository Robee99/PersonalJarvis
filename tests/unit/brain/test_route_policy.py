"""Deterministic routing fixtures for ``[brain.route_policy]``.

The provider ids below are configuration values of a test install, never names
the policy itself knows about: the same cases hold for any ids.
"""
from __future__ import annotations

import pytest

from jarvis.brain.route_policy import (
    RECOVERY_MESSAGES,
    TEST_MODE_ENV,
    RouteFailure,
    TurnSignals,
    decide_route,
    filter_denied,
    wants_escalation,
)
from jarvis.core.config import BrainRoutePolicyConfig

FAST = ("fast-host", "fast-model")
DEEP = ("deep-host", "deep-model")


def _policy(**overrides) -> BrainRoutePolicyConfig:
    raw = {
        "enabled": True,
        "fast": {"provider": FAST[0], "model": FAST[1]},
        "deep": {"provider": DEEP[0], "model": DEEP[1]},
        "escalation": {
            "enabled": True,
            "agent": "delegate",
            "trigger_phrases": ["ask the expert"],
        },
        "deny_providers": ["direct-family-api", "direct-family-cli"],
        "deny_model_prefixes": ["direct-family/"],
    }
    raw.update(overrides)
    return BrainRoutePolicyConfig.model_validate(raw)


def _decide(signals: TurnSignals, policy=None, *, available=(FAST[0], DEEP[0]),
            tools=lambda p, m: True, vision=lambda p, m: True, environ=None):
    return decide_route(
        signals,
        policy or _policy(),
        available=available,
        can_call_tools=tools,
        supports_vision=vision,
        environ=environ or {},
    )


# Curated cases: (signals, expected tier, expected first target, reason prefix)
CASES = [
    (TurnSignals(level="fast"), "fast", FAST, "intent:fast"),
    (TurnSignals(level="deep"), "deep", DEEP, "intent:deep"),
    (TurnSignals(level="code"), "deep", DEEP, "intent:code"),
    (TurnSignals(level=""), "fast", FAST, "intent:fast"),
    (TurnSignals(level="fast", needs_tools=True), "fast", FAST, "intent:fast"),
    (TurnSignals(level="fast", explicit_escalation=True), "escalation", None,
     "escalation:explicit-request"),
]


@pytest.mark.parametrize(("signals", "tier", "first", "reason"), CASES)
def test_curated_routing_fixtures(signals, tier, first, reason) -> None:
    decision = _decide(signals)
    assert decision.tier == tier
    assert decision.reason.startswith(reason)
    if first is None:
        assert decision.chain == []
    else:
        assert decision.chain[0] == first


def test_simple_turn_falls_back_once_to_deep_and_never_loops() -> None:
    decision = _decide(TurnSignals(level="fast"))
    assert decision.chain == [FAST, DEEP]
    assert len(set(decision.chain)) == len(decision.chain)


def test_unavailable_fast_target_moves_to_deep_with_a_reason() -> None:
    decision = _decide(TurnSignals(level="fast"), available=(DEEP[0],))
    assert decision.tier == "deep"
    assert decision.chain == [DEEP]
    assert decision.reason == "intent:fast;fallback-from:fast"
    assert ("fast:fast-host", "unavailable") in decision.excluded


def test_tool_turn_skips_a_target_that_cannot_call_tools() -> None:
    decision = _decide(
        TurnSignals(level="fast", needs_tools=True),
        tools=lambda p, m: p != FAST[0],
    )
    assert decision.tier == "deep"
    assert ("fast:fast-host", "no-tools") in decision.excluded


def test_vision_turn_without_a_seeing_target_is_capability_unsupported() -> None:
    decision = _decide(TurnSignals(level="fast", needs_vision=True), vision=lambda p, m: False)
    assert decision.tier == "none"
    assert decision.chain == []
    assert decision.reason == "capability:vision-unsupported"


def test_denied_target_is_excluded_even_when_configured() -> None:
    policy = _policy(deep={"provider": "direct-family-api", "model": "big"})
    decision = _decide(
        TurnSignals(level="deep"), policy, available=(FAST[0], "direct-family-api")
    )
    assert all(p != "direct-family-api" for p, _ in decision.chain)
    assert ("deep:direct-family-api", "denied") in decision.excluded


def test_denied_model_prefix_through_a_gateway_is_excluded() -> None:
    policy = _policy(deep={"provider": "gateway", "model": "direct-family/opus"})
    decision = _decide(TurnSignals(level="deep"), policy, available=(FAST[0], "gateway"))
    assert decision.chain == [FAST]


def test_filter_denied_strips_override_chains_too() -> None:
    chain = [("direct-family-cli", None), ("gateway", "direct-family/x"), FAST]
    assert filter_denied(_policy(), chain) == [FAST]


def test_nothing_configured_reports_no_eligible_target() -> None:
    policy = BrainRoutePolicyConfig.model_validate({"enabled": True})
    decision = _decide(TurnSignals(level="fast"), policy)
    assert decision.tier == "none"
    assert decision.reason == "no-eligible-target"


def test_escalation_disabled_ignores_the_request() -> None:
    policy = _policy(escalation={"enabled": False, "trigger_phrases": ["ask the expert"]})
    decision = _decide(TurnSignals(level="fast", explicit_escalation=True), policy)
    assert decision.tier == "fast"
    assert not wants_escalation(policy, "please ask the expert")


def test_trigger_phrases_come_from_config() -> None:
    policy = _policy()
    assert wants_escalation(policy, "Could you Ask The Expert about this?")
    assert not wants_escalation(policy, "what's the weather")


def test_test_override_is_ignored_outside_test_mode() -> None:
    policy = _policy(test_override_tier="deep")
    assert _decide(TurnSignals(level="fast"), policy, environ={}).tier == "fast"
    pinned = _decide(TurnSignals(level="fast"), policy, environ={TEST_MODE_ENV: "1"})
    assert pinned.tier == "deep"
    assert pinned.reason == "test-override:deep"


def test_every_failure_category_has_a_user_safe_message() -> None:
    assert set(RECOVERY_MESSAGES) == set(RouteFailure)
    for message in RECOVERY_MESSAGES.values():
        assert "Traceback" not in message and "Error" not in message


def test_decision_serialises_for_the_route_event() -> None:
    payload = _decide(TurnSignals(level="deep")).to_dict()
    assert payload["tier"] == "deep"
    assert payload["chain"] == ["deep-host:deep-model", "fast-host:fast-model"]
    assert payload["reason"] == "intent:deep"
