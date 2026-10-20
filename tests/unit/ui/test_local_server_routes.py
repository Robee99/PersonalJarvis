"""The Model page's local-server list: with Hermes as the brain a pick becomes Hermes's model."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.brain import local_servers
from jarvis.brain.local_servers import LocalServer
from jarvis.ui.web import local_server_routes, provider_routes

QWEN = LocalServer(
    url="http://127.0.0.1:11437",
    port=11437,
    kind="llama.cpp",
    state="running",
    models=["qwen"],
    hermes_provider="local-qwen",
    hermes_model="qwen",
)


def _app(primary: str) -> tuple[TestClient, SimpleNamespace]:
    hermes_card = SimpleNamespace(model="hermes-agent", base_url="")
    local_card = SimpleNamespace(model="", base_url="")
    cfg = SimpleNamespace(
        brain=SimpleNamespace(
            primary=primary,
            providers={"hermes": hermes_card, "local-openai": local_card},
        )
    )
    app = FastAPI()
    app.state.config = cfg
    app.include_router(local_server_routes.router)
    return TestClient(app), cfg


@pytest.fixture
def saved(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []

    async def scan(**_kw: Any) -> list[LocalServer]:
        return [QWEN]

    async def apply(provider: str, model: str, *_a: Any, **_kw: Any) -> Any:
        calls.append((provider, model))
        return SimpleNamespace(applied_live=True)

    monkeypatch.setattr(local_servers, "scan", scan)
    monkeypatch.setattr(
        local_servers,
        "read_hermes_config",
        lambda: {
            "providers": {"local-qwen": {"base_url": "http://127.0.0.1:11437/v1", "model": "qwen"}},
        },
    )
    monkeypatch.setattr(provider_routes, "_apply_brain_model", apply)
    return calls


def test_the_list_says_hermes_is_the_brain_and_what_it_has_selected(saved: list) -> None:
    client, _cfg = _app("hermes")
    body = client.get("/api/local-servers").json()
    assert body["hermes_brain"] is True
    assert body["selection"] == "hermes-agent"
    assert body["servers"][0]["hermes_provider"] == "local-qwen"


def test_picking_qwen_under_hermes_selects_the_hermes_provider(saved: list) -> None:
    client, _cfg = _app("hermes")
    resp = client.post("/api/local-servers/use", json={"url": "127.0.0.1:11437"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["selection"] == "local-qwen::qwen"
    assert saved == [("hermes", "local-qwen::qwen")]


def test_without_hermes_the_local_card_gets_the_server(
    saved: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jarvis.core import config_writer

    urls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(config_writer, "set_provider_base_url", lambda p, u: urls.append((p, u)))
    client, cfg = _app("ollama")
    resp = client.post("/api/local-servers/use", json={"url": "http://localhost:11437/v1"})
    assert resp.status_code == 200, resp.text
    assert urls == [("local-openai", "http://127.0.0.1:11437")]
    assert saved == [("local-openai", "qwen")]
    assert cfg.brain.providers["local-openai"].base_url == "http://127.0.0.1:11437"


def test_a_remote_address_is_refused(saved: list) -> None:
    client, _cfg = _app("hermes")
    resp = client.post("/api/local-servers/use", json={"url": "http://10.0.0.5:8080"})
    assert resp.status_code == 422
    assert saved == []


def test_the_list_says_how_much_context_hermes_needs(saved: list) -> None:
    client, _cfg = _app("hermes")
    body = client.get("/api/local-servers").json()
    assert body["hermes_min_context"] == local_servers.HERMES_MIN_CONTEXT
    assert body["servers"][0]["context"] is None


def test_hermes_refuses_a_server_with_too_small_a_context(
    saved: list, monkeypatch: pytest.MonkeyPatch
) -> None:
    small = LocalServer(
        url="http://127.0.0.1:11441",
        port=11441,
        kind="llama.cpp",
        state="running",
        models=["ui-venus-2-9b"],
        context=16384,
    )

    async def scan(**_kw: Any) -> list[LocalServer]:
        return [small]

    monkeypatch.setattr(local_servers, "scan", scan)
    client, _cfg = _app("hermes")
    resp = client.post("/api/local-servers/use", json={"url": "127.0.0.1:11441"})
    assert resp.status_code == 409
    assert "16,384" in resp.json()["detail"]
    assert saved == []
