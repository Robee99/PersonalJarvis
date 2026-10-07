"""Hermes as the brain: images reach it, and a look-only turn cannot act.

Hermes's API server is played by ``tests/fakes/fake_hermes_api``.

* an image in the turn goes to Hermes as an ``image_url`` part of the run's
  input, so Hermes can show it to a model that sees or describe it with its own
  vision tool; a turn without images still sends plain text;
* "don't click anything, just look" tells Hermes to only look, denies every
  approval Hermes asks for without asking the user, and stops the run if
  Hermes starts a tool that acts anyway.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from jarvis.core.protocols import BrainMessage, BrainRequest, ImageBlock
from jarvis.plugins.brain import hermes
from jarvis.plugins.brain.hermes import OBSERVE_ONLY_INSTRUCTIONS, HermesBrain
from tests.fakes.fake_hermes_api import FakeHermesApi, FakeRun, asks_approval, say


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(hermes, "configured_base_url", lambda: "http://127.0.0.1:8642")
    monkeypatch.setattr(hermes, "thinking_off_by_config", lambda: False)
    monkeypatch.setattr(hermes, "phrase_language", lambda: "en")


def _brain(server: FakeHermesApi) -> HermesBrain:
    brain = HermesBrain()
    brain.transport = server.transport
    return brain


async def _ask(brain: HermesBrain, message: BrainMessage) -> str:
    deltas = [d async for d in brain.complete(BrainRequest(messages=(message,)))]
    return "".join(d.content or "" for d in deltas)


@pytest.mark.asyncio
async def test_an_image_in_the_turn_reaches_hermes_as_an_image_part() -> None:
    server = FakeHermesApi(say("You are holding a red mug."))
    photo = ImageBlock(mime="image/jpeg", data_b64="AAAA")

    answer = await _ask(
        _brain(server), BrainMessage(role="user", content="What am I holding?", images=(photo,))
    )

    assert answer == "You are holding a red mug."
    assert server.runs[0].body["input"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What am I holding?"},
                {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAAA"}},
            ],
        }
    ]
    assert HermesBrain.supports_vision is True


@pytest.mark.asyncio
async def test_a_turn_without_images_still_sends_plain_text() -> None:
    server = FakeHermesApi(say("Hi."))

    await _ask(_brain(server), BrainMessage(role="user", content="say hi"))

    assert server.runs[0].body["input"] == "say hi"
    assert OBSERVE_ONLY_INSTRUCTIONS not in server.runs[0].body["instructions"]


@pytest.mark.asyncio
async def test_a_look_only_turn_tells_hermes_and_denies_its_approvals_unasked() -> None:
    server = FakeHermesApi(
        asks_approval("Clicked Start.", "I only looked: the desktop shows Notepad.")
    )
    brain = _brain(server)

    answer = await _ask(
        brain, BrainMessage(role="user", content="Don't click anything, just look at my screen.")
    )

    assert OBSERVE_ONLY_INSTRUCTIONS in server.runs[0].body["instructions"]
    assert [a["choice"] for a in server.approvals] == ["deny"]
    assert brain.pending_approval is None, "the user is not asked; the answer is no"
    assert answer == "I only looked: the desktop shows Notepad."


def _clicks_anyway() -> Any:
    async def script(_server: FakeHermesApi, _run: FakeRun) -> AsyncIterator[dict[str, Any]]:
        yield {"event": "tool.started", "tool": "browser_snapshot", "preview": ""}
        yield {"event": "tool.completed", "tool": "browser_snapshot", "duration": 0.1}
        yield {"event": "tool.started", "tool": "browser_click", "preview": "@e3"}
        yield {"event": "message.delta", "delta": "Clicked it."}
        yield {"event": "run.completed", "output": "Clicked it."}

    return script


@pytest.mark.asyncio
async def test_a_look_only_turn_stops_hermes_when_it_starts_to_act() -> None:
    server = FakeHermesApi(_clicks_anyway())

    answer = await _ask(
        _brain(server),
        BrainMessage(role="user", content="Tell me what is on this page, don't interact."),
    )

    assert "stopped Hermes" in answer and "browser_click" in answer
    assert "Clicked it." not in answer
    assert server.runs[0].stopped is True


@pytest.mark.asyncio
async def test_an_ordinary_click_request_still_asks_the_user() -> None:
    server = FakeHermesApi(asks_approval("Clicked Start.", "Left it."))
    brain = _brain(server)

    question = await _ask(brain, BrainMessage(role="user", content="click the Start button"))

    assert "Say yes or no" in question
    assert brain.pending_approval is not None
    assert server.approvals == []


# --- through the manager: the brightness reflex in front of Hermes -----------


@pytest.fixture
def hermes_manager(monkeypatch: pytest.MonkeyPatch) -> Any:
    from jarvis.brain.manager import BrainManager
    from jarvis.core.bus import EventBus
    from jarvis.core.config import BrainRoutePolicyConfig, load_config

    monkeypatch.setattr("jarvis.core.config.get_secret_any", lambda _candidates: "test-key")
    cfg = load_config()
    cfg.brain.primary = "hermes"
    cfg.brain.route_policy = BrainRoutePolicyConfig.model_validate(
        {"enabled": True, "fast": {"provider": "hermes"}}
    )
    return BrainManager.from_tier_config("router", cfg, EventBus(), provider_override="hermes")


@pytest.mark.asyncio
async def test_a_plain_brightness_command_is_answered_before_hermes_and_verified(
    hermes_manager: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.platform import brightness

    level = {"now": 70}

    def windows(script: str) -> tuple[int, str]:
        if "WmiSetBrightness" in script:
            level["now"] = int(script.split("[byte]")[1].split("}")[0])
            return 0, ""
        return 0, str(level["now"])

    monkeypatch.setattr(brightness.sys, "platform", "win32")
    monkeypatch.setattr(brightness, "_powershell", windows)
    monkeypatch.setattr(brightness, "SETTLE_S", 0.0)
    server = FakeHermesApi(say("I can't promise anything about brightness."))
    hermes_manager._get_brain(*hermes_manager._build_fallback_chain("fast")[0]).transport = (
        server.transport
    )

    reply = await hermes_manager.generate("set the brightness to 40", use_history=False)
    assert reply == "Brightness is now 40%."
    assert server.runs == [], "the reflex answered; Hermes was not asked"

    reply = await hermes_manager.generate("Don't change brightness.", use_history=False)
    assert reply == "I can't promise anything about brightness."
    assert level["now"] == 40, "a negation never changes anything"
    assert len(server.runs) == 1
