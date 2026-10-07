"""One brain, one conversation (ADR-0042): with Hermes as the brain nothing else answers.

* voice and the typed chat run in the same Hermes session and share its
  pending approval; housekeeping runs in a background session;
* no Jarvis fallback chain sits behind Hermes;
* the front page's chat offers Hermes only and moves older chats onto it;
* no flash model writes acknowledgements beside Hermes.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.plugins.brain import hermes
from jarvis.plugins.brain.hermes import BACKGROUND_SESSION_ID, SESSION_ID, HermesBrain
from tests.fakes.fake_hermes_api import FakeHermesApi, asks_approval


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(hermes, "configured_base_url", lambda: "http://127.0.0.1:8642")
    monkeypatch.setattr(hermes, "thinking_off_by_config", lambda: False)
    monkeypatch.setattr(hermes, "phrase_language", lambda: "en")


def _manager(monkeypatch: pytest.MonkeyPatch, *, policy: bool) -> Any:
    from jarvis.brain.manager import BrainManager
    from jarvis.core.bus import EventBus
    from jarvis.core.config import BrainRoutePolicyConfig, load_config

    monkeypatch.setattr("jarvis.core.config.get_secret_any", lambda _candidates: "test-key")
    cfg = load_config()
    cfg.brain.primary = "hermes"
    cfg.brain.deep_brain = "gemini"
    cfg.brain.route_policy = BrainRoutePolicyConfig.model_validate(
        {"enabled": policy, "fast": {"provider": "hermes"}}
    )
    return BrainManager.from_tier_config("router", cfg, EventBus(), provider_override="hermes")


def test_a_plain_hermes_brain_works_in_the_background_session() -> None:
    brain = HermesBrain()
    assert brain.session_id == BACKGROUND_SESSION_ID
    brain.join_conversation()
    assert brain.session_id == SESSION_ID


def test_voice_and_chat_brains_join_the_conversation_and_others_do_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(monkeypatch, policy=True)
    voice = manager._get_brain("hermes", None)
    chat = manager._get_brain("hermes", "stepfun/step-3.7-flash:free", scope="agent")
    verifier = manager._get_brain("hermes", None, scope="goal-verifier:abc")
    assert voice is not chat
    assert voice.session_id == chat.session_id == SESSION_ID
    assert verifier.session_id == BACKGROUND_SESSION_ID


@pytest.mark.asyncio
async def test_an_approval_asked_by_voice_is_answered_from_the_chat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _manager(monkeypatch, policy=True)
    server = FakeHermesApi(asks_approval("Deleted notes.txt.", "Left it."))
    voice = manager._get_brain("hermes", None)
    chat = manager._get_brain("hermes", None, scope="agent")
    voice.transport = chat.transport = server.transport

    from jarvis.core.protocols import BrainMessage, BrainRequest

    def req(text: str) -> BrainRequest:
        return BrainRequest(messages=(BrainMessage(role="user", content=text),))

    asked = "".join([d.content or "" async for d in voice.complete(req("delete my notes"))])
    assert "yes or no" in asked.lower()
    assert chat.pending_approval is not None
    answer = "".join([d.content or "" async for d in chat.complete(req("yes"))])
    assert answer == "Deleted notes.txt."
    assert server.approvals == [{"choice": "once", "request_id": "req-7"}]
    assert voice.pending_approval is None


@pytest.mark.parametrize("level", ["fast", "deep", "code"])
def test_no_jarvis_fallback_chain_sits_behind_hermes(
    monkeypatch: pytest.MonkeyPatch, level: str
) -> None:
    manager = _manager(monkeypatch, policy=False)
    chain = manager._build_fallback_chain(level)
    assert [name for name, _model in chain] == ["hermes"]


def _live(monkeypatch: pytest.MonkeyPatch, *, hermes_brain: bool) -> None:
    from jarvis.core import runtime_refs

    cfg = SimpleNamespace(
        brain=SimpleNamespace(
            primary="hermes" if hermes_brain else "openai",
            route_policy=SimpleNamespace(enabled=False, fast=None),
        )
    )
    monkeypatch.setattr(runtime_refs, "get_brain_manager", lambda: SimpleNamespace(_config=cfg))


def test_the_front_page_chat_offers_only_hermes_while_it_is_the_brain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.agent_chat.catalog import offers, rows_for

    _live(monkeypatch, hermes_brain=True)
    assert [row.id for row in rows_for("jarvis")] == ["hermes"]
    assert not offers("jarvis", "openai")
    _live(monkeypatch, hermes_brain=False)
    assert len(rows_for("jarvis")) > 1


def test_a_chat_on_another_seat_opens_on_hermes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from jarvis.agent_chat.service import AgentChatService
    from jarvis.agent_chat.store import AgentChatStore

    _live(monkeypatch, hermes_brain=True)
    svc = AgentChatService(
        AgentChatStore(":memory:"), assistant_name=lambda: "Jarvis", bus=lambda: None
    )
    session = svc.create_session(
        provider="openai", model="gpt-5", effort="", cwd=str(tmp_path),
        permission_mode="ask", surface="jarvis",
    )
    assert session.provider == "hermes"
    assert session.model == ""


def test_no_flash_model_speaks_beside_hermes() -> None:
    from jarvis.brain.factory import _build_flash_provider

    cfg = SimpleNamespace(
        brain=SimpleNamespace(primary="hermes", route_policy=None),
    )
    ack = SimpleNamespace(provider="gemini", enabled=True)
    assert _build_flash_provider(cfg, ack) is None
