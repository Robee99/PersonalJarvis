""" "Which model are you running on?" is answered from the turn's own chain.

Live 2026-10-25 both local brains (Gemma 4 E4B, Nemotron 3.5 Bolt E4B) said
"I'm Jarvis" and pointed at the settings instead of naming themselves.
"""

from __future__ import annotations

from typing import Any

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.screen_context.turn import TurnScreenContext


class _NoBrain:
    def for_provider(self, name: str) -> _NoBrain:
        raise AssertionError(f"a model was called for {name}")


def _manager(chain: list[tuple[str, str | None]]) -> BrainManager:
    cfg = JarvisConfig()
    cfg.brain.primary = chain[0][0]
    mgr = BrainManager(config=cfg, bus=EventBus(), tools={})
    mgr._build_fallback_chain = lambda _l: list(chain)  # type: ignore[method-assign]
    mgr._get_brain = lambda name, _m: object()  # type: ignore[method-assign]
    mgr._build_dispatcher = lambda brain, **_kw: _NoBrain().for_provider("any")  # type: ignore[method-assign]

    async def _no_screen(*_args: Any, **_kwargs: Any) -> TurnScreenContext:
        return TurnScreenContext(status="none")

    async def _no_images(**_kwargs: Any) -> tuple:
        return ()

    mgr._resolve_screen_context_turn = _no_screen  # type: ignore[method-assign]
    mgr._collect_vision_images = _no_images  # type: ignore[method-assign]
    mgr._resolve_turn_lang = lambda: "en"  # type: ignore[method-assign]
    return mgr


@pytest.mark.asyncio
async def test_names_the_model_that_would_answer() -> None:
    mgr = _manager([("local-openai", "nemotron-4b"), ("local-openai", "gemma-4b-qat")])

    answer = await mgr.generate("Which AI model are you running on right now?", use_history=False)

    assert answer == "I'm running on nemotron-4b, through the local model server on this computer."


@pytest.mark.asyncio
async def test_skips_a_provider_already_dead() -> None:
    mgr = _manager([("first", "m1"), ("local-openai", "gemma-4b-qat")])
    mgr._dead_providers.add("first")

    answer = await mgr.generate("what model are you using?", use_history=False)

    assert answer == "I'm running on gemma-4b-qat, through the local model server on this computer."


def test_only_the_plain_question_matches() -> None:
    from jarvis.brain.manager import _MODEL_ASK

    assert _MODEL_ASK.match("Jarvis, which model is powering you?")
    assert not _MODEL_ASK.match("what model should I buy?")
    assert not _MODEL_ASK.match("which model are you running on for the essay and why")
