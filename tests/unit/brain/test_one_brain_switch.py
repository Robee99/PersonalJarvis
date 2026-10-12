"""One brain switch: ``brain.primary`` alone decides who the main brain is.

A brain switch (UI ``POST /api/brain/switch`` or the voice gate, both through
``app_control.apply_provider_switch``) used to change only ``brain.primary``.
With ``[brain.route_policy]`` on and its fast tier still naming Hermes (the
free-voice setup), Hermes kept answering every turn while the UI showed the
other provider, and ``hermes_is_main_brain`` kept saying "Hermes" because it
also read the fast tier. These tests pin the fix through a real BrainManager:
switching away makes the other provider answer and nothing routes to Hermes;
switching back restores Hermes; a switch never loosens a deny list.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.brain import app_control
from jarvis.brain.manager import BrainManager
from jarvis.brain.route_policy import hermes_is_main_brain, tiers_following_switch
from jarvis.core import runtime_refs
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainRoutePolicyConfig, load_config

#: The table ``jarvis system free-voice`` writes (paid providers deny-listed).
FREE_VOICE_POLICY = {
    "enabled": True,
    "fast": {"provider": "hermes"},
    "deep": {"provider": ""},
    "escalation": {"enabled": False},
    "deny_providers": ["claude-api", "claude-cli", "openai", "openrouter"],
}


@pytest.fixture(autouse=True)
def _keys_present_and_no_live_refs(monkeypatch: pytest.MonkeyPatch):
    for name in ("get_secret_any", "get_provider_secret", "get_secret"):
        monkeypatch.setattr(f"jarvis.core.config.{name}", lambda *_a, **_k: "test-key")
    runtime_refs._reset_for_tests()
    yield
    runtime_refs._reset_for_tests()


@pytest.fixture
def free_voice_manager() -> BrainManager:
    cfg = load_config()
    cfg.brain.primary = "hermes"
    cfg.brain.route_policy = BrainRoutePolicyConfig.model_validate(FREE_VOICE_POLICY)
    return BrainManager.from_tier_config("router", cfg, EventBus(), provider_override="hermes")


class FakeConfigWriter:
    """Records the route-policy writes a switch persists (no file is touched)."""

    def __init__(self) -> None:
        self.route_policy_writes: list[tuple[dict[str, Any], bool]] = []

    def set_route_policy(self, values: dict[str, Any], *, backup: bool = True) -> dict:
        self.route_policy_writes.append((values, backup))
        return dict(values)


def _providers(chain: list[tuple[str, str | None]]) -> list[str]:
    return [provider for provider, _model in chain]


async def _switch(mgr: BrainManager, provider: str, *, persist: bool = False) -> dict:
    return await app_control.apply_provider_switch(
        "brain", provider, cfg=mgr._config, persist=persist, manager=mgr
    )


# --- the pure rules ---------------------------------------------------------


def test_hermes_is_main_brain_reads_only_primary() -> None:
    on_fast = SimpleNamespace(enabled=True, fast=SimpleNamespace(provider="hermes"))
    split = SimpleNamespace(brain=SimpleNamespace(primary="gemini", route_policy=on_fast))
    assert hermes_is_main_brain(split) is False
    split.brain.primary = "hermes"
    assert hermes_is_main_brain(split) is True


def test_the_fast_tier_and_a_deep_tier_on_the_old_brain_follow_the_switch() -> None:
    policy = BrainRoutePolicyConfig.model_validate(
        {"enabled": True, "fast": {"provider": "hermes", "model": "m"},
         "deep": {"provider": "hermes"}}
    )
    assert tiers_following_switch(policy, "hermes", "gemini") == {
        "fast": {"provider": "gemini"},
        "deep": {"provider": "gemini"},
    }


def test_an_unrelated_deep_tier_and_a_disabled_policy_are_left_alone() -> None:
    policy = BrainRoutePolicyConfig.model_validate(
        {"enabled": True, "fast": {"provider": "gemini"}, "deep": {"provider": "openai"}}
    )
    assert tiers_following_switch(policy, "gemini", "hermes") == {
        "fast": {"provider": "hermes"}
    }
    assert tiers_following_switch(policy, "gemini", "gemini") == {}
    off = BrainRoutePolicyConfig.model_validate({"enabled": False, "fast": {"provider": "hermes"}})
    assert tiers_following_switch(off, "hermes", "gemini") == {}


# --- through the real Brain -------------------------------------------------


@pytest.mark.asyncio
async def test_switching_away_from_hermes_makes_the_other_provider_answer(
    free_voice_manager: BrainManager,
) -> None:
    mgr = free_voice_manager
    assert hermes_is_main_brain(mgr._config)
    assert _providers(mgr._build_fallback_chain("fast")) == ["hermes"]

    result = await _switch(mgr, "gemini")

    assert result["ok"], result
    assert mgr.active_provider == "gemini"
    assert hermes_is_main_brain(mgr._config) is False
    for level in ("fast", "deep", "code"):
        chain = _providers(mgr._build_fallback_chain(level))
        assert chain and chain[0] == "gemini"
        assert "hermes" not in chain
    # The deny list is untouched: no paid provider was let in by the switch.
    assert mgr._config.brain.route_policy.deny_providers == FREE_VOICE_POLICY["deny_providers"]


@pytest.mark.asyncio
async def test_switching_back_restores_hermes(free_voice_manager: BrainManager) -> None:
    mgr = free_voice_manager
    assert (await _switch(mgr, "gemini"))["ok"]

    result = await _switch(mgr, "hermes")

    assert result["ok"], result
    assert hermes_is_main_brain(mgr._config)
    assert _providers(mgr._build_fallback_chain("fast")) == ["hermes"]


@pytest.mark.asyncio
async def test_reselecting_the_primary_heals_an_already_split_install(
    free_voice_manager: BrainManager,
) -> None:
    """An install that switched before this fix has primary=X but fast=hermes."""
    mgr = free_voice_manager
    assert (await _switch(mgr, "gemini"))["ok"]
    mgr.set_route_policy(BrainRoutePolicyConfig.model_validate(FREE_VOICE_POLICY))
    assert "hermes" in _providers(mgr._build_fallback_chain("fast"))

    assert (await _switch(mgr, "gemini"))["ok"]

    assert "hermes" not in _providers(mgr._build_fallback_chain("fast"))


@pytest.mark.asyncio
async def test_a_switch_to_a_deny_listed_provider_is_refused_and_hermes_stays(
    free_voice_manager: BrainManager,
) -> None:
    mgr = free_voice_manager

    result = await _switch(mgr, "openrouter")

    assert result["ok"] is False
    assert result["error_kind"] == "route_policy_denied"
    assert mgr.active_provider == "hermes"
    assert mgr._config.brain.route_policy.fast.provider == "hermes"


@pytest.mark.asyncio
async def test_a_persisted_switch_writes_the_tiers_through_config_writer(
    free_voice_manager: BrainManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer = FakeConfigWriter()
    monkeypatch.setattr(app_control, "_import_writer", lambda: writer)
    monkeypatch.setattr(BrainManager, "_persist_primary", staticmethod(lambda _name: True))

    result = await _switch(free_voice_manager, "gemini", persist=True)

    assert result["ok"] and result["persisted"]
    # backup=False: the routing controls' restore point is not replaced.
    assert writer.route_policy_writes == [({"fast": {"provider": "gemini"}}, False)]


def test_set_route_policy_can_keep_the_restore_point(tmp_path: Path) -> None:
    from jarvis.core import config_writer

    toml = tmp_path / "jarvis.toml"
    toml.write_text(
        '[brain.route_policy]\nenabled = true\n\n[brain.route_policy.fast]\nprovider = "hermes"\n',
        encoding="utf-8",
    )
    backup = config_writer.route_policy_backup_path(toml)
    backup.write_text(json.dumps({"enabled": False}), encoding="utf-8")

    table = config_writer.set_route_policy(
        {"fast": {"provider": "gemini"}}, path=toml, backup=False
    )

    assert table["fast"] == {"provider": "gemini", "local": False}
    assert json.loads(backup.read_text(encoding="utf-8")) == {"enabled": False}
