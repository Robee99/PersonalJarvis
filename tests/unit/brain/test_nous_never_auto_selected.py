"""Saving a Nous Portal key never makes Nous an automatic fallback.

Nous Portal is a cloud host with no published privacy policy. It answers only
when the user selects it — as the active brain, an explicit fallback, a route
policy tier, or the mission worker. A key in the keyring alone must not pull it
into the Brain's cross-provider chain, the route-policy chain, the tool lead,
the mission worker's cross-family last resort, or the cross-family critic.

Every test below registers the Nous plugin and gives EVERY provider a key, so
an auto-add would show up; the chains are then checked for ``nous``.
"""
from __future__ import annotations

from collections.abc import Callable

import pytest

from jarvis.brain.manager import _MAIN_BRAIN_FALLBACK_PROVIDER_ORDER, BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainRoutePolicyConfig, load_config
from jarvis.missions import init as mi
from jarvis.missions.critic import runner as critic_runner
from jarvis.plugins.brain.nous import NousBrain

_LEVELS = ("fast", "deep", "code")


@pytest.fixture(autouse=True)
def _every_provider_has_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("jarvis.core.config.get_secret_any", lambda _candidates: "test-key")


def _manager(
    monkeypatch: pytest.MonkeyPatch,
    *,
    policy: dict | None = None,
    only: tuple[str, ...] | None = None,
) -> BrainManager:
    cfg = load_config()
    cfg.brain.primary = "openrouter"
    cfg.brain.deep_brain = None
    if policy is not None:
        cfg.brain.route_policy = BrainRoutePolicyConfig.model_validate(policy)
    mgr = BrainManager.from_tier_config(
        "router", cfg, EventBus(), provider_override="openrouter"
    )
    # The Nous plugin is registered exactly as an installed entry point would
    # register it, independent of the venv's installed metadata.
    registry = mgr._registry
    registry._load()
    registry._classes["nous"] = NousBrain
    if only is not None:
        names = sorted(only)
        monkeypatch.setattr(registry, "available", lambda: list(names))
    assert "nous" in registry.available()
    assert "nous" not in mgr._dead_providers  # its key counts as present
    return mgr


def _providers(chain: list[tuple[str, str | None]]) -> set[str]:
    return {provider for provider, _model in chain}


def test_saved_key_never_enters_the_brain_fallback_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mgr = _manager(monkeypatch)
    for level in _LEVELS:
        chain = mgr._build_fallback_chain(level)
        # Keyed peers DO join this chain, so leaving Nous out is meaningful.
        assert len(_providers(chain)) > 1, (level, chain)
        assert "nous" not in _providers(chain), (level, chain)


def test_even_as_the_only_other_keyed_provider_it_is_not_pulled_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mgr = _manager(monkeypatch, only=("openrouter", "nous"))
    for level in _LEVELS:
        assert _providers(mgr._build_fallback_chain(level)) == {"openrouter"}, level
    # Nor is it picked to lead a tool turn for a talker that cannot call tools.
    assert mgr._first_tool_capable_provider("fast", exclude="openrouter") is None


def test_saved_key_never_enters_the_route_policy_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mgr = _manager(
        monkeypatch,
        policy={
            "enabled": True,
            "fast": {"provider": "openrouter", "model": "fast-model"},
            "deep": {"provider": "openai", "model": "deep-model"},
        },
    )
    for level in _LEVELS:
        chain = mgr._build_fallback_chain(level)
        assert _providers(chain) == {"openrouter", "openai"}, (level, chain)


def test_selecting_nous_is_what_puts_it_on_the_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Counter-check: the user's own selection does reach Nous."""
    mgr = _manager(
        monkeypatch,
        policy={
            "enabled": True,
            "fast": {"provider": "nous", "model": "stepfun/step-3.7-flash:free"},
            "deep": {"provider": "openai", "model": "deep-model"},
        },
    )
    assert mgr._build_fallback_chain("fast")[0] == ("nous", "stepfun/step-3.7-flash:free")


def test_nous_is_not_a_main_brain_fallback() -> None:
    assert "nous" not in _MAIN_BRAIN_FALLBACK_PROVIDER_ORDER


def _missions_world(monkeypatch: pytest.MonkeyPatch, keyed: Callable[[str], bool]) -> None:
    """No claude CLI, no codex login; only the providers ``keyed`` names hold a key."""
    monkeypatch.setattr(
        "jarvis.missions.workers.claude_direct_worker._resolve_claude_binary", lambda: None
    )
    monkeypatch.setattr(mi, "_claude_cli_auth_viable", lambda: False)
    monkeypatch.setattr(
        "jarvis.missions.workers.codex_direct_worker._codex_oauth_available", lambda: False
    )
    monkeypatch.setattr("jarvis.codex_auth_state.codex_needs_reauth", lambda: False)
    monkeypatch.setattr(
        "jarvis.codex_quota_state.codex_in_quota_cooldown", lambda **_k: False
    )
    monkeypatch.setattr("jarvis.core.config.get_secret_any", lambda _candidates: None)
    monkeypatch.setattr(
        "jarvis.core.config.get_provider_secret",
        lambda p: "sk-nous-test" if keyed((p or "").strip().lower()) else None,
    )
    monkeypatch.setattr(mi, "_assemble_worker_mcp_servers", lambda **_k: ())


def test_a_nous_only_key_is_not_a_mission_last_resort(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _missions_world(monkeypatch, lambda p: p == "nous")
    assert mi.reachable_worker_families() == []
    assert mi._cross_family_last_resort_worker("do the task") is None


def test_critic_uses_nous_only_for_a_mission_nous_ran(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _missions_world(monkeypatch, lambda p: p == "nous")
    # Another provider's mission is never graded on the Nous key.
    assert critic_runner._resolve_api_critic_provider("antigravity", None)[0] != "nous"
    # A mission the user ran on Nous is graded there, on its own model.
    assert critic_runner._resolve_api_critic_provider(
        "nous", "stepfun/step-3.7-flash:free"
    ) == ("nous", "stepfun/step-3.7-flash:free")
