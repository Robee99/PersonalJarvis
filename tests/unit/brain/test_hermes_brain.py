"""Hermes Agent as Jarvis's brain: Hermes orchestrates, Jarvis is the front-end.

Pinned here, with Hermes's API server played by ``tests/fakes/fake_hermes_api``:

* each turn is one Hermes run without Jarvis's tools, and Hermes's own pick of
  the model is used unless the card names a Hermes route or ``provider::model``
  (both local models, Qwen and Gemma, are reached that way);
* the provider and model Hermes actually ran on come back and are recorded;
* tools Hermes ran count as evidence, so a real action is spoken and an empty
  promise is still caught;
* with Hermes as the brain, Jarvis's own shortcuts (local actions, skills,
  Agentic-IDE, force-spawn) never take the turn;
* the key stays in Hermes's .env and a remote Hermes is refused.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from jarvis.brain.manager import SUBAGENT_ONLY_BRAIN_PROVIDERS, BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainRoutePolicyConfig, load_config
from jarvis.core.protocols import BrainDelta, BrainMessage, BrainRequest
from jarvis.plugins.brain import hermes
from jarvis.plugins.brain.hermes import HermesBrain, build_instructions, model_fields
from tests.fakes.fake_hermes_api import FakeHermesApi, say


def _brain(server: FakeHermesApi, model: str | None = None) -> HermesBrain:
    brain = HermesBrain(model=model)
    brain.transport = server.transport
    return brain


def _req(text: str, **kw: Any) -> BrainRequest:
    return BrainRequest(
        messages=(BrainMessage(role="user", content=text),),
        tools=({"type": "function", "function": {"name": "open_app"}},),
        system="ROUTER RULES: call open_app for apps.",
        **kw,
    )


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "hermes-home"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "no-home")
    monkeypatch.setattr(hermes, "configured_base_url", lambda: "http://127.0.0.1:8642")
    monkeypatch.setattr(hermes, "thinking_off_by_config", lambda: False)
    return home


async def _collect(brain: HermesBrain, req: BrainRequest) -> list[BrainDelta]:
    return [d async for d in brain.complete(req)]


@pytest.mark.parametrize(
    ("card", "expected"),
    [
        ("", {"model": "hermes-agent"}),
        ("hermes-agent", {"model": "hermes-agent"}),
        ("qwen", {"model": "qwen"}),
        ("local-qwen::qwen", {"provider": "local-qwen", "model": "qwen"}),
        (
            "local-gemma::gemma-4-12b-qat",
            {"provider": "local-gemma", "model": "gemma-4-12b-qat"},
        ),
        (
            "nous::stepfun/step-3.7-flash:free",
            {"provider": "nous", "model": "stepfun/step-3.7-flash:free"},
        ),
    ],
)
def test_the_card_value_becomes_hermes_model_selection(card: str, expected: dict) -> None:
    assert model_fields(card) == expected


def test_hermes_gets_the_front_end_contract_not_jarvis_tool_rules() -> None:
    req = BrainRequest(
        messages=(BrainMessage(role="user", content="hi"),),
        system="ROUTER RULES: call open_app.\n\nReply in English.",
    )
    instructions = build_instructions(req)
    assert "open_app" not in instructions
    assert instructions.startswith(hermes.FRONT_END_INSTRUCTIONS)


def test_instructions_stay_identical_across_spoken_typed_and_language_turns() -> None:
    # Hermes puts the instructions in the system prompt; any per-turn change
    # makes llama.cpp re-read the whole ~47k-token prompt (2026-10-12).
    spoken = BrainRequest(
        messages=(BrainMessage(role="user", content="hi"),),
        system="X\n\nREPLY LANGUAGE: English.",
    )
    typed_german = BrainRequest(
        messages=(BrainMessage(role="user", content="schau nur, klick nichts"),),
        system="X\n\nREPLY LANGUAGE: German.",
    )
    assert build_instructions(spoken) == build_instructions(typed_german)
    assert hermes.run_input(spoken) == (
        f"[spoken] {hermes.FOCUS_NOTE} REPLY LANGUAGE: English.\nhi"
    )
    note = hermes.turn_note(typed_german, written=True)
    assert note.startswith("[typed]")
    assert "REPLY LANGUAGE: German." in note


def test_only_the_first_message_of_a_typed_chat_says_it_is_a_new_chat() -> None:
    first = BrainRequest(messages=(BrainMessage(role="user", content="hi"),))
    later = BrainRequest(
        messages=(
            BrainMessage(role="user", content="add chicken to my cart"),
            BrainMessage(role="assistant", content="Working on it."),
            BrainMessage(role="user", content="what time is it"),
        )
    )
    assert hermes.NEW_CHAT_NOTE in hermes.turn_note(first, written=True)
    assert hermes.NEW_CHAT_NOTE not in hermes.turn_note(later, written=True)
    # Voice has no chats: the note never goes on a spoken turn.
    assert hermes.NEW_CHAT_NOTE not in hermes.turn_note(first)


def test_every_other_turn_says_earlier_tasks_are_over() -> None:
    # One Hermes session carries every turn; a small model replayed an old
    # task in answer to a new question (2026-10-20).
    first = BrainRequest(messages=(BrainMessage(role="user", content="hi"),))
    later = BrainRequest(
        messages=(
            BrainMessage(role="user", content="sort my videos"),
            BrainMessage(role="assistant", content="I can't."),
            BrainMessage(role="user", content="show the earthquakes"),
        )
    )
    assert hermes.FOCUS_NOTE in hermes.turn_note(first)
    assert hermes.FOCUS_NOTE in hermes.turn_note(later, written=True)
    assert hermes.FOCUS_NOTE not in hermes.turn_note(first, written=True)


def hermes_manager(monkeypatch: pytest.MonkeyPatch) -> BrainManager:
    monkeypatch.setattr("jarvis.core.config.get_secret_any", lambda _candidates: "test-key")
    cfg = load_config()
    cfg.brain.primary = "hermes"
    # What ``jarvis system free-voice`` sets: Hermes is the only chain entry,
    # so no test turn can reach a network provider.
    cfg.brain.route_policy = BrainRoutePolicyConfig.model_validate(
        {"enabled": True, "fast": {"provider": "hermes"}}
    )
    return BrainManager.from_tier_config("router", cfg, EventBus(), provider_override="hermes")


def test_hermes_is_a_main_brain_and_owns_tools(hermes_manager: BrainManager) -> None:
    assert "hermes" not in SUBAGENT_ONLY_BRAIN_PROVIDERS
    assert hermes_manager.active_provider == "hermes"
    assert hermes_manager._brain_orchestrates_tools() is True


def hermes_of(mgr: BrainManager) -> HermesBrain:
    chain = mgr._build_fallback_chain("fast")
    assert [p for p, _ in chain] == ["hermes"]
    brain = mgr._get_brain(*chain[0])
    assert isinstance(brain, HermesBrain)
    return brain


class _ShortcutTaken(AssertionError):
    pass


def _forbid_shortcuts(mgr: BrainManager, monkeypatch: pytest.MonkeyPatch) -> None:
    async def _taken(*_a: Any, **_k: Any) -> Any:
        raise _ShortcutTaken("a Jarvis shortcut took a turn Hermes owns")

    for name in (
        "_run_local_action_fast_path",
        "_run_wiki_ingest_fast_path",
        "_run_agentic_ide_fast_path",
        "_run_agentic_ide_close_fast_path",
        "_run_agentic_ide_spawn_fast_path",
        "_force_spawn_worker",
        "_maybe_dispatch_skill_mission",
        "_resolve_screen_context_turn",
    ):
        monkeypatch.setattr(mgr, name, _taken)
    monkeypatch.setattr(mgr, "_check_unsupported_intent", lambda *_a: "I can't do that.")


@pytest.mark.asyncio
async def test_open_an_app_goes_to_hermes_and_its_action_is_spoken(
    hermes_manager: BrainManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_shortcuts(hermes_manager, monkeypatch)
    server = FakeHermesApi(say("I'll open Notepad now.", tools=("computer_use",)))
    hermes_of(hermes_manager).transport = server.transport

    answer = await hermes_manager.generate("open notepad", use_history=False)

    # Hermes's own tool run is the evidence, so the honesty guard keeps it.
    assert answer == "I'll open Notepad now."
    assert len(server.runs) == 1 and "tools" not in server.runs[0].body


@pytest.mark.asyncio
async def test_a_promise_without_any_hermes_tool_run_is_still_caught(
    hermes_manager: BrainManager, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_shortcuts(hermes_manager, monkeypatch)
    server = FakeHermesApi(say("I'll open Notepad now."))
    hermes_of(hermes_manager).transport = server.transport

    answer = await hermes_manager.generate("open notepad", use_history=False)

    assert answer != "I'll open Notepad now."
    assert "did not start" in answer
