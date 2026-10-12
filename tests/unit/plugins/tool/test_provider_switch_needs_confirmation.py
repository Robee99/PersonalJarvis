"""A model may never switch the provider, brain, voice mode or realtime engine
on its own initiative.

Live 2026-10-04: in the first Gemini Live turn the model called the registry
``brain-switch`` command (``POST /api/brain/switch``) although the user had
asked for nothing of the kind. Every model-reachable switch is therefore
``ask`` tier: the one ToolExecutor holds it for the user's explicit spoken or
clicked yes (AP-3), and a call without that yes changes nothing. Read-only
status tools stay unconfirmed.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from jarvis.app_actions.catalog import build_catalog, default_tier, is_provider_switch
from jarvis.commands.registry import get_registry
from jarvis.core.bus import EventBus
from jarvis.core.config import SafetyConfig
from jarvis.plugins.tool.app_command import AppCommandTool
from jarvis.plugins.tool.describe_app_settings import DescribeAppSettingsTool
from jarvis.plugins.tool.switch_provider import SwitchProviderTool
from jarvis.realtime.tools import RealtimeToolBridge
from jarvis.safety.approval import ApprovalWorkflow
from jarvis.safety.risk_tier import RiskTierEvaluator
from jarvis.safety.tool_executor import VOICE_CONFIRM_SENTINEL, ToolExecutor

#: Every curated switch command; the regex in the catalog must keep covering them.
SWITCH_COMMANDS = {
    "brain-switch",
    "tts-switch",
    "stt-switch",
    "realtime-switch",
    "computer-use-switch",
    "jarvis-agent-switch",
    "voice-mode-set",
}


@pytest.fixture(autouse=True)
def _no_user_action_policy(tmp_path, monkeypatch):
    """No Jarvis-actions policy file: every command keeps its registry tier."""
    monkeypatch.setattr("jarvis.core.config.DATA_DIR", tmp_path)


def _switch_app(calls: list[tuple[str, dict]]) -> FastAPI:
    app = FastAPI()

    @app.post("/api/brain/switch")
    async def brain_switch(body: dict) -> dict:
        calls.append(("brain", body))
        return {"ok": True, "active": body["provider"], "old_provider": "gemini"}

    @app.get("/api/providers")
    async def providers() -> dict:
        calls.append(("list", {}))
        return {"brain": {"active": "gemini"}}

    return app


def _registry_tools(calls: list[tuple[str, dict]]) -> dict[str, Any]:
    loader = AppCommandTool(
        transport=httpx.ASGITransport(app=_switch_app(calls)),
        control_key_resolver=lambda: None,
        config_resolver=SimpleNamespace,
    )
    return {tool.name: tool for tool in loader.expand()}


def _executor() -> ToolExecutor:
    bus = EventBus()
    return ToolExecutor(
        bus=bus, evaluator=RiskTierEvaluator(SafetyConfig()), approval=ApprovalWorkflow(bus)
    )


def test_every_switch_command_resolves_to_the_ask_tier() -> None:
    evaluator = RiskTierEvaluator(SafetyConfig())
    tools = _registry_tools([])
    switches = {
        cmd.id for cmd in get_registry() if is_provider_switch(cmd.method, cmd.path)
    }
    assert switches == SWITCH_COMMANDS
    for name in SWITCH_COMMANDS:
        decision = evaluator.evaluate(tools[name], {"provider": "openai"})
        assert decision.tier == "ask", name
        assert evaluator.needs_user_confirmation(decision), name


def test_registry_switch_commands_are_dangerous_like_their_catalog_routes() -> None:
    """Parity: a curated command on a switch route can never be less strict."""
    for cmd in get_registry():
        if is_provider_switch(cmd.method, cmd.path):
            assert cmd.dangerous, f"{cmd.id}: provider switch must require confirmation"


def test_switch_provider_tool_resolves_to_the_ask_tier() -> None:
    evaluator = RiskTierEvaluator(SafetyConfig())
    decision = evaluator.evaluate(
        SwitchProviderTool(), {"tier": "tts", "provider": "elevenlabs", "reason": "x"}
    )
    assert decision.tier == "ask"
    assert evaluator.needs_user_confirmation(decision)


def test_app_action_catalog_asks_before_a_switch_and_reads_freely() -> None:
    body = {"content": {"application/json": {"schema": {"type": "object"}}}}
    spec = {
        "paths": {
            "/api/realtime/switch": {"post": {"operationId": "rt", "requestBody": body}},
            "/api/tts/switch": {"post": {"operationId": "tts", "requestBody": body}},
            "/api/settings/voice-mode": {
                "put": {"operationId": "mode", "requestBody": body},
                "get": {"operationId": "mode_get"},
            },
            "/api/providers": {"get": {"operationId": "providers"}},
            "/api/brain/switch": {"post": {"operationId": "brain", "requestBody": body}},
            "/api/society/kill-switch": {"post": {"operationId": "kill"}},
        }
    }
    catalog = build_catalog(spec)
    assert "brain" not in catalog  # the main-brain switch is never offered at all
    assert default_tier(catalog["rt"]) == "ask"
    assert default_tier(catalog["tts"]) == "ask"
    assert default_tier(catalog["mode"]) == "ask"
    assert default_tier(catalog["mode_get"]) == "safe"
    assert default_tier(catalog["providers"]) == "safe"
    assert not is_provider_switch("POST", "/api/society/kill-switch")


def test_read_only_status_tools_never_ask() -> None:
    evaluator = RiskTierEvaluator(SafetyConfig())
    describe = evaluator.evaluate(DescribeAppSettingsTool(), {})
    assert describe.tier == "safe"
    assert not evaluator.needs_user_confirmation(describe)
    listing = evaluator.evaluate(_registry_tools([])["providers-list"], {})
    assert not evaluator.needs_user_confirmation(listing)
    refused_brain = evaluator.evaluate(
        SwitchProviderTool(), {"tier": "brain", "provider": "openai", "reason": "x"}
    )
    # Refused before anything is touched, so there is nothing to confirm.
    assert not evaluator.needs_user_confirmation(refused_brain)


@pytest.mark.asyncio
async def test_model_initiated_switch_without_confirmation_changes_nothing() -> None:
    calls: list[tuple[str, dict]] = []
    tools = _registry_tools(calls)
    bridge = RealtimeToolBridge(
        tools={"brain-switch": tools["brain-switch"]},
        executor=_executor(),
        language="en",
    )
    # The user ordered something unrelated; the model reached for a switch.
    await bridge.handle_user_transcript("Open the settings for me")
    _name, first = await bridge.execute(
        wire_name="brain-switch", arguments={"provider": "openai"}
    )
    assert first["success"] is False
    assert first["confirmation_required"] is True
    assert calls == []

    # Calling again without a yes still changes nothing.
    _name, again = await bridge.execute(
        wire_name="brain-switch", arguments={"provider": "openai"}
    )
    assert again["confirmation_required"] is True
    assert calls == []

    # A "no" drops the pending switch for good.
    await bridge.handle_user_transcript("no")
    _name, vetoed = await bridge.execute(
        wire_name="brain-switch", arguments={"provider": "openai"}
    )
    assert vetoed["success"] is False
    assert calls == []


@pytest.mark.asyncio
async def test_switch_runs_once_after_the_users_explicit_yes() -> None:
    calls: list[tuple[str, dict]] = []
    tools = _registry_tools(calls)
    bridge = RealtimeToolBridge(
        tools={"brain-switch": tools["brain-switch"]},
        executor=_executor(),
        language="en",
    )
    await bridge.handle_user_transcript("Switch the brain provider to OpenAI")
    _name, asked = await bridge.execute(
        wire_name="brain-switch", arguments={"provider": "openai"}
    )
    assert asked["confirmation_required"] is True
    assert calls == []

    await bridge.handle_user_transcript("yes")
    _name, done = await bridge.execute(
        wire_name="brain-switch", arguments={"provider": "openai"}
    )
    assert done["success"] is True
    assert calls == [("brain", {"provider": "openai", "persist": True})]


@pytest.mark.asyncio
async def test_switch_provider_defers_without_touching_the_switch(monkeypatch) -> None:
    switched: list[tuple[str, str]] = []

    async def _record(tier: str, provider: str, **_kwargs: Any) -> dict[str, Any]:
        switched.append((tier, provider))
        return {"ok": True}

    monkeypatch.setattr("jarvis.brain.app_control.apply_provider_switch", _record)
    result = await _executor().execute(
        SwitchProviderTool(),
        {"tier": "tts", "provider": "elevenlabs", "reason": "model chose it"},
        config_snapshot={"voice_confirm": True},
    )
    assert result.error == VOICE_CONFIRM_SENTINEL
    assert switched == []
