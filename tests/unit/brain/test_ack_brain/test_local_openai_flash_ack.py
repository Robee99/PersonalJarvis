"""The local voice acknowledgement uses the selected OpenAI-compatible brain."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from jarvis.brain.ack_brain.config import LocalOpenAIAckProviderConfig
from jarvis.brain.ack_brain.providers.local_openai import LocalOpenAIFlashAck


@pytest.mark.asyncio
async def test_uses_configured_local_model_and_normalizes_v1_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "I am checking that now."}}]},
        )

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *, timeout: original_client(
            timeout=timeout, transport=httpx.MockTransport(handler)
        ),
    )
    from jarvis.core import config as cfg

    monkeypatch.setattr(
        cfg,
        "load_config",
        lambda: SimpleNamespace(
            brain=SimpleNamespace(
                providers={"local-openai": SimpleNamespace(model="qwen3-vl-8b")}
            )
        ),
    )
    monkeypatch.setattr(
        cfg,
        "resolve_provider_endpoint",
        lambda *_args, **_kwargs: SimpleNamespace(
            base_url="http://127.0.0.1:11440/v1", credential=""
        ),
    )

    adapter = LocalOpenAIFlashAck(LocalOpenAIAckProviderConfig())
    response = await adapter.run("Open the settings", "en", persona_prompt="Jarvis")

    assert response == "I am checking that now."
    assert seen["url"] == "http://127.0.0.1:11440/v1/chat/completions"
    assert seen["body"]["model"] == "qwen3-vl-8b"
    assert seen["body"]["max_tokens"] == 40


@pytest.mark.asyncio
async def test_returns_none_when_local_endpoint_is_not_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from jarvis.core import config as cfg

    monkeypatch.setattr(
        cfg,
        "load_config",
        lambda: SimpleNamespace(
            brain=SimpleNamespace(
                providers={"local-openai": SimpleNamespace(model="qwen3-vl-8b")}
            )
        ),
    )
    monkeypatch.setattr(
        cfg,
        "resolve_provider_endpoint",
        lambda *_args, **_kwargs: SimpleNamespace(base_url="", credential=""),
    )

    adapter = LocalOpenAIFlashAck(LocalOpenAIAckProviderConfig())

    assert await adapter.run("Hello", "en", persona_prompt="Jarvis") is None
