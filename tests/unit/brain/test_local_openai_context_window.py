"""The local OpenAI-compatible brain budgets against llama.cpp's real n_ctx."""
from __future__ import annotations

from types import SimpleNamespace

from jarvis.core import config as cfg
from jarvis.plugins.brain.local_openai import LocalOpenAIBrain


def test_local_openai_uses_conservative_llama_cpp_context_default(monkeypatch) -> None:
    monkeypatch.setattr(
        cfg,
        "load_config",
        lambda: SimpleNamespace(brain=SimpleNamespace(providers={})),
    )

    brain = LocalOpenAIBrain("qwen3-vl-8b")

    assert brain.context_window == 8_192


def test_local_openai_respects_configured_context_window(monkeypatch) -> None:
    provider = SimpleNamespace(context_window=16_384)
    monkeypatch.setattr(
        cfg,
        "load_config",
        lambda: SimpleNamespace(
            brain=SimpleNamespace(providers={"local-openai": provider})
        ),
    )

    brain = LocalOpenAIBrain("qwen3-vl-8b")

    assert brain.context_window == 16_384
