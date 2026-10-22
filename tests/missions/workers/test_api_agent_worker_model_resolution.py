"""Regression: the in-process API-agent worker must run the user's PICKED model,
never a hardcoded paid foreign-family default.

Live forensic 2026-06-29: ``api_agent_worker._DEFAULT_MODEL["openrouter"]`` was
``anthropic/claude-opus-4.8``. When a mission step carried no model and the
sub-agent provider was OpenRouter, the heavy worker ran Opus 4.8 ON THE OPENROUTER
KEY — a paid model the user never picked (the user's OpenRouter pick was a FREE
model). The worker must resolve the model from the user's selection first:
the step's explicit model → the matching ``[brain.sub_jarvis].model`` →
``[brain.providers[provider]].model`` → only then a non-paid default
(AP-21/AP-22, open-source single-key §3).
"""
from __future__ import annotations

import pytest

from jarvis.core.config import BrainProviderConfig, BrainTierConfig, JarvisConfig
from jarvis.missions.workers import api_agent_worker as m

_FREE = "nvidia/nemotron-3-ultra-550b-a55b:free"


def _patch_config(monkeypatch: pytest.MonkeyPatch, config: JarvisConfig) -> None:
    monkeypatch.setattr("jarvis.core.config.load_config", lambda: config)


def test_explicit_step_model_always_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_config(monkeypatch, JarvisConfig())
    assert m._resolve_worker_model("openrouter", "openai/gpt-5.5") == "openai/gpt-5.5"


def test_openrouter_uses_provider_pick_not_paid_opus(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = JarvisConfig()
    config.brain.providers["openrouter"] = BrainProviderConfig(model=_FREE)
    _patch_config(monkeypatch, config)

    got = m._resolve_worker_model("openrouter", "")
    assert got == _FREE
    assert "anthropic/claude" not in got


def test_subagent_pin_used_only_for_its_own_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """[brain.worker].model belongs to [brain.worker].provider — it must
    NOT be applied to a different worker provider (that is how a claude-opus pin
    set for antigravity would otherwise leak onto the OpenRouter key)."""
    config = JarvisConfig()
    config.brain.worker = BrainTierConfig(
        provider="antigravity", model="claude-opus-4-8",
    )
    config.brain.providers["openrouter"] = BrainProviderConfig(model=_FREE)
    _patch_config(monkeypatch, config)

    got = m._resolve_worker_model("openrouter", "")
    assert got == _FREE, "antigravity's opus pin leaked onto the openrouter worker"


def test_openrouter_default_without_any_pick_is_not_paid_anthropic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_config(monkeypatch, JarvisConfig())  # nothing configured
    got = m._resolve_worker_model("openrouter", "") or ""
    assert "anthropic/claude" not in got


def test_pin_applies_when_the_worker_inherits_the_primary_brain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no [brain.worker].provider the worker runs on brain.primary, and the
    model picked in the Assistant-Agents tab must run there too."""
    config = JarvisConfig()
    config.brain.primary = "openrouter"
    config.brain.worker = BrainTierConfig(provider="", model=_FREE)
    _patch_config(monkeypatch, config)

    assert m._resolve_worker_model("openrouter", "") == _FREE


def test_inherited_pin_does_not_leak_to_another_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = JarvisConfig()
    config.brain.primary = "gemini"
    config.brain.worker = BrainTierConfig(provider="", model="gemini-3.5-flash")
    config.brain.providers["openrouter"] = BrainProviderConfig(model=_FREE)
    _patch_config(monkeypatch, config)

    assert m._resolve_worker_model("openrouter", "") == _FREE


def test_gemini_worker_runs_the_picked_model(monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis.missions.workers import gemini_worker

    config = JarvisConfig()
    config.brain.primary = "gemini"
    config.brain.worker = BrainTierConfig(provider="", model="gemini-3.5-flash")
    _patch_config(monkeypatch, config)

    assert gemini_worker._resolve_gemini_model(None) == "gemini-3.5-flash"
