"""llama.cpp single-model servers publish image input on ``/props``, not ``/v1/models``.

Without this probe a vision model served with its mmproj (Qwen3.6, Gemma 4)
reached the capability consumers as blind, because the single-model
``/v1/models`` entry carries no ``architecture`` block at all.
"""

from __future__ import annotations

from typing import Any

from jarvis.brain.model_catalog import ModelCatalog, ModelInfo, parse_models_response

_URL = "http://127.0.0.1:11435/v1/models"


class _Resp:
    def __init__(self, payload: dict[str, Any], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> dict[str, Any]:
        return self._payload


class _PropsClient:
    def __init__(self, props: dict[str, Any] | None, status_code: int = 200) -> None:
        self.props = props
        self.status_code = status_code
        self.calls: list[dict[str, Any]] = []

    async def get(self, url: str, headers=None, params=None) -> _Resp:
        self.calls.append({"url": url, "headers": headers or {}})
        if self.props is None:
            raise RuntimeError("connection refused")
        return _Resp(self.props, self.status_code)


def _single(owned_by: str = "llamacpp", n_ctx: int | None = 32768) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": "qwen3.6-35b-a3b", "object": "model", "owned_by": owned_by}
    if n_ctx is not None:
        entry["meta"] = {"n_ctx": n_ctx}
    return {"data": [entry]}


async def _enrich(client: _PropsClient, payload: dict[str, Any], headers=None) -> list[ModelInfo]:
    models = parse_models_response("local-openai", payload)
    return await ModelCatalog._enrich_llamacpp_capabilities(
        client, _URL, payload, models, headers or {}
    )


async def test_vision_from_props_and_context_from_meta() -> None:
    client = _PropsClient({"modalities": {"vision": True, "audio": False}})

    [model] = await _enrich(client, _single())

    assert model.input_modalities == ("text", "image")
    assert model.context_length == 32768
    assert client.calls == [{"url": "http://127.0.0.1:11435/props", "headers": {}}]


async def test_text_only_server_is_declared_blind_not_unknown() -> None:
    [model] = await _enrich(_PropsClient({"modalities": {"vision": False}}), _single())

    assert model.input_modalities == ("text",)


async def test_context_falls_back_to_props_generation_settings() -> None:
    props = {"modalities": {"vision": True}, "default_generation_settings": {"n_ctx": 65536}}

    [model] = await _enrich(_PropsClient(props), _single(n_ctx=None))

    assert model.context_length == 65536


async def test_optional_key_rides_along_to_props() -> None:
    client = _PropsClient({"modalities": {"vision": True}})

    await _enrich(client, _single(), headers={"Authorization": "Bearer sk-local"})

    assert client.calls[0]["headers"] == {"Authorization": "Bearer sk-local"}


async def test_other_servers_are_not_probed() -> None:
    client = _PropsClient({"modalities": {"vision": True}})

    [model] = await _enrich(client, _single(owned_by="vllm"))

    assert model.input_modalities is None
    assert client.calls == []


async def test_multi_model_lists_are_not_attributed() -> None:
    client = _PropsClient({"modalities": {"vision": True}})
    payload = {
        "data": [
            {"id": "a", "owned_by": "llamacpp"},
            {"id": "b", "owned_by": "llamacpp"},
        ]
    }

    models = await _enrich(client, payload)

    assert all(m.input_modalities is None for m in models)
    assert client.calls == []


async def test_router_mode_declaration_is_kept_without_a_probe() -> None:
    client = _PropsClient({"modalities": {"vision": False}})
    payload = {
        "data": [
            {
                "id": "gemma",
                "owned_by": "llamacpp",
                "architecture": {"input_modalities": ["text", "image"]},
            }
        ]
    }

    [model] = await _enrich(client, payload)

    assert model.input_modalities == ("text", "image")
    assert client.calls == []


async def test_unreachable_props_stays_unknown() -> None:
    [model] = await _enrich(_PropsClient(None), _single())

    assert model.input_modalities is None


async def test_http_error_on_props_stays_unknown() -> None:
    [model] = await _enrich(_PropsClient({}, status_code=404), _single())

    assert model.input_modalities is None
