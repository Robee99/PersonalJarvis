"""``[brain.route_policy]`` wired into BrainManager.

Pins three things at the Brain boundary:
* with the policy on, the chain is exactly the configured tiers (no hard-coded
  cross-provider order), and a deny-listed family never appears on any chain;
* an explicit escalation request goes to the delegate and never reaches a model
  provider; a delegate that is not connected yields a typed, spoken recovery;
* with the policy off nothing changes (the upstream chain is untouched).
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from jarvis.brain import manager as manager_mod
from jarvis.brain.manager import BrainManager
from jarvis.brain.paperclip_delegation import DelegationResult, DelegationStatus
from jarvis.brain.route_policy import RouteFailure, recovery_message
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainRoutePolicyConfig, load_config
from jarvis.core.events import BrainRouteSelected


@pytest.fixture(autouse=True)
def _all_test_providers_have_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jarvis.core.config.get_secret_any", lambda _candidates: "test-key")


def _manager(policy: dict | None) -> BrainManager:
    cfg = load_config()
    cfg.brain.primary = "openrouter"
    if policy is not None:
        cfg.brain.route_policy = BrainRoutePolicyConfig.model_validate(policy)
    return BrainManager.from_tier_config("router", cfg, EventBus(), provider_override="openrouter")


POLICY = {
    "enabled": True,
    "fast": {"provider": "openrouter", "model": "fast-free-model"},
    "deep": {"provider": "openai", "model": "deep-model"},
    "escalation": {"enabled": True, "agent": "claude", "trigger_phrases": ["ask claude"]},
    "deny_providers": ["claude-api", "claude-cli"],
    "deny_model_prefixes": ["anthropic/"],
}


def test_policy_chain_is_exactly_the_configured_tiers() -> None:
    mgr = _manager(POLICY)
    assert mgr._build_fallback_chain("fast") == [
        ("openrouter", "fast-free-model"), ("openai", "deep-model"),
    ]
    assert mgr._build_fallback_chain("deep") == [
        ("openai", "deep-model"), ("openrouter", "fast-free-model"),
    ]
    assert mgr._last_route_decision.reason == "intent:deep"


def test_no_policy_chain_contains_a_denied_direct_provider() -> None:
    mgr = _manager({**POLICY, "deep": {"provider": "claude-api", "model": "x"}})
    for level in ("fast", "deep", "code"):
        chain = mgr._build_fallback_chain(level)
        assert all(p not in ("claude-api", "claude-cli") for p, _ in chain)
    assert mgr._apply_route_deny([("claude-api", None), ("openrouter", "anthropic/x")]) == []


def test_policy_off_keeps_the_upstream_chain() -> None:
    off = _manager(None)
    assert off._route_policy() is None
    chain = off._build_fallback_chain("fast")
    assert chain and chain[0][0] == "openrouter"
    assert off._apply_route_deny(chain) == chain


class _ProviderCallForbidden(AssertionError):
    pass


@pytest.mark.asyncio
async def test_explicit_escalation_goes_to_the_delegate_not_a_model(monkeypatch) -> None:
    mgr = _manager(POLICY)
    seen: list[BrainRouteSelected] = []

    async def _record(event: BrainRouteSelected) -> None:
        seen.append(event)

    mgr._bus.subscribe(BrainRouteSelected, _record)

    def _no_chain(_level):
        raise _ProviderCallForbidden("a model chain was built for an escalated turn")

    monkeypatch.setattr(mgr, "_build_fallback_chain", _no_chain)

    class _FakeDelegate:
        async def delegate(self, request, cancel_token=None):
            assert request.task == "please ask claude to review my plan"
            return DelegationResult(DelegationStatus.COMPLETED, text="Reviewed.",
                                    issue_identifier="ROB-1", elapsed_s=1.5)

    monkeypatch.setattr(manager_mod, "delegate_from_config",
                        lambda _cfg, on_progress=None: _FakeDelegate())

    answer = await mgr.generate("please ask claude to review my plan", use_history=False)

    assert answer == "Reviewed."
    assert [e.tier for e in seen] == ["escalation"]
    assert seen[0].reason == "escalation:explicit-request"
    assert seen[0].outcome == "completed"


@pytest.mark.asyncio
async def test_escalation_without_a_connection_is_a_typed_recovery(monkeypatch) -> None:
    mgr = _manager(POLICY)
    monkeypatch.setattr(manager_mod, "delegate_from_config", lambda _cfg, on_progress=None: None)

    answer = await mgr._escalate_to_delegate(
        "ask claude", uuid4(), level="fast", reason="escalation:explicit-request",
        use_history=False, on_progress=None,
    )
    assert answer == recovery_message(RouteFailure.UNAVAILABLE, mgr._resolve_turn_lang())


@pytest.mark.asyncio
async def test_escalation_budget_is_bounded_per_session(monkeypatch) -> None:
    policy = {**POLICY, "escalation": {**POLICY["escalation"], "max_per_session": 1}}
    mgr = _manager(policy)

    class _FailingDelegate:
        async def delegate(self, request, cancel_token=None):
            return DelegationResult(DelegationStatus.TIMEOUT)

    monkeypatch.setattr(manager_mod, "delegate_from_config",
                        lambda _cfg, on_progress=None: _FailingDelegate())
    first = await mgr._escalate_to_delegate(
        "ask claude", uuid4(), level="fast", reason="r", use_history=False, on_progress=None)
    second = await mgr._escalate_to_delegate(
        "ask claude", uuid4(), level="fast", reason="r", use_history=False, on_progress=None)

    assert first == recovery_message(RouteFailure.TIMEOUT, mgr._resolve_turn_lang())
    assert second == recovery_message(RouteFailure.POLICY_DENIED, mgr._resolve_turn_lang())
