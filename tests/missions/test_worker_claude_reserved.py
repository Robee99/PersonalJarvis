"""``[brain.route_policy]`` reserves Claude for explicit requests: missions too.

With the policy on and the Claude family deny-listed, a mission must never land
on a Claude worker by itself, whether as the default, a fallback or the last
resort. It runs on another reachable family, or fails with a clear reason.
Without the policy the upstream worker routing is unchanged.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.core.config import BrainRoutePolicyConfig
from jarvis.missions import init as mi
from jarvis.missions.workers.api_agent_worker import ApiAgentWorker
from jarvis.missions.workers.claude_direct_worker import ClaudeDirectWorker
from tests.missions.test_worker_cross_family_fallback import _patch_env

POLICY = {
    "enabled": True,
    "fast": {"provider": "local-openai", "model": "step"},
    "deny_providers": ["claude-api", "claude-cli"],
}


def _use_policy(monkeypatch: pytest.MonkeyPatch, policy: dict | None) -> None:
    route_policy = BrainRoutePolicyConfig.model_validate(policy) if policy else None
    cfg = SimpleNamespace(brain=SimpleNamespace(route_policy=route_policy))
    monkeypatch.setattr("jarvis.core.config.load_config", lambda: cfg)


def test_policy_swaps_a_claude_worker_for_another_family(monkeypatch) -> None:
    _patch_env(monkeypatch, claude_binary="/usr/bin/claude", keys=("gemini",))
    _use_policy(monkeypatch, POLICY)

    worker = mi._without_automatic_claude(ClaudeDirectWorker(), "build it", None)

    assert isinstance(worker, ApiAgentWorker) and worker.provider == "gemini"


def test_policy_never_picks_the_anthropic_key_either(monkeypatch) -> None:
    _patch_env(monkeypatch, keys=("claude-api", "openrouter"))
    _use_policy(monkeypatch, POLICY)

    worker = mi._without_automatic_claude(ApiAgentWorker("claude-api"), "t", None)

    assert isinstance(worker, ApiAgentWorker) and worker.provider == "openrouter"


def test_policy_with_only_claude_reachable_fails_with_a_reason(monkeypatch) -> None:
    _patch_env(monkeypatch, claude_binary="/usr/bin/claude", keys=("claude-api",))
    _use_policy(monkeypatch, POLICY)

    with pytest.raises(mi.ClaudeReservedError, match="explicit"):
        mi._without_automatic_claude(ClaudeDirectWorker(), "t", None)


def test_no_policy_keeps_the_upstream_worker(monkeypatch) -> None:
    _patch_env(monkeypatch, claude_binary="/usr/bin/claude", keys=("gemini",))
    _use_policy(monkeypatch, None)
    claude = ClaudeDirectWorker()

    assert mi._without_automatic_claude(claude, "t", None) is claude


def test_policy_leaves_non_claude_workers_alone(monkeypatch) -> None:
    _patch_env(monkeypatch, keys=("gemini",))
    _use_policy(monkeypatch, POLICY)
    gemini = ApiAgentWorker("gemini")

    assert mi._without_automatic_claude(gemini, "t", None) is gemini


def test_policy_keeps_the_mission_critic_off_claude(monkeypatch) -> None:
    from jarvis.missions.critic import runner

    _patch_env(monkeypatch, claude_binary="/usr/bin/claude", keys=("claude-api", "gemini"))
    monkeypatch.setattr(mi, "_api_key_family_viable", lambda p: p in ("claude-api", "gemini"))
    monkeypatch.setattr(runner, "_provider_picked_model", lambda _p: None)
    _use_policy(monkeypatch, POLICY)

    assert runner._claude_cli_critic_viable() is False
    assert runner._resolve_api_critic_provider("claude-api", "x")[0] == "gemini"

    _use_policy(monkeypatch, None)
    assert runner._resolve_api_critic_provider("claude-api", "x")[0] == "claude-api"
