"""Nous Portal through a free local gateway on this machine.

The owner runs an OpenAI-compatible gateway on ``127.0.0.1:11436`` that signs
upstream with his own Nous login, so the client sends no key. The card's
server-URL field points Nous Portal at it. Pinned here:

* the override persists through the existing ``PUT /providers/{id}/base-url``
  route and the brain then talks to ``<root>/v1``;
* a loopback base URL without a key builds a client and counts as configured
  (card, brain switch, worker viability) — a REMOTE base URL without a key
  still fails cleanly and stays unconfigured;
* the live model picker lists the gateway's own models (no network: an
  ``httpx.MockTransport`` stands in), and its free routes carry the free tag;
* it stays a cloud provider: no vision, billed "api".
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

import jarvis.core.config as cfg
from jarvis.brain import app_control
from jarvis.brain.model_catalog import ModelCatalog, classify_model
from jarvis.core import config_writer
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.missions import init as mi
from jarvis.plugins.brain import nous
from jarvis.ui.web.provider_spec import get_spec, provider_billing
from jarvis.ui.web.server import WebServer

_GATEWAY_ROOT = "http://127.0.0.1:11436"
_GATEWAY_MODELS = (
    "stealth/space-bunny-alpha",
    "inclusionai/ling-3.0-flash-sante:free",
    "poolside/laguna-s-2.1:free",
    "poolside/laguna-xs-2.1:free",
    "stepfun/step-3.7-flash:free",
    "meituan/longcat-2.5-preview:free",
)


class _FakeOpenAI:
    last_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        _FakeOpenAI.last_kwargs = kwargs


def _world(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, base_url: str | None) -> Path:
    """A config file whose Nous card points at ``base_url``, and no stored key."""
    toml = tmp_path / "jarvis.toml"
    toml.write_text("", encoding="utf-8")
    monkeypatch.setenv("JARVIS_CONFIG", str(toml))
    # Persist through the same writer the in-app route uses.
    config_writer.set_provider_base_url("nous", base_url, path=toml)
    cfg.clear_config_cache()
    monkeypatch.setattr(cfg, "get_provider_secret", lambda _pid: None)
    monkeypatch.setattr(cfg, "get_secret_any", lambda _candidates: None)
    monkeypatch.setattr(cfg, "get_secret", lambda *_a, **_k: None)
    return toml


@pytest.fixture(autouse=True)
def _fresh_config_cache():
    cfg.clear_config_cache()
    yield
    cfg.clear_config_cache()


def test_card_offers_the_server_url_field_and_stays_cloud() -> None:
    spec = get_spec("nous")
    assert spec.supports_base_url is True
    assert spec.default_base_url == "https://inference-api.nousresearch.com/v1"
    assert spec.auth_mode == "api_key"
    assert provider_billing(spec) == "api"
    assert nous.NousBrain.supports_vision is False


def test_route_persists_the_gateway_url_as_a_server_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        "jarvis.core.config_writer.set_provider_base_url",
        lambda provider, url, **_k: writes.append((provider, url)),
    )
    conf = JarvisConfig()
    conf.ui.dev_mode = True
    server = WebServer(conf, bus=EventBus())
    server.app.state.config = conf
    with TestClient(server.app) as client:
        resp = client.put(
            "/api/providers/nous/base-url", json={"base_url": f"{_GATEWAY_ROOT}/v1"}
        )
    assert resp.status_code == 200
    assert resp.json()["base_url"] == _GATEWAY_ROOT
    assert resp.json()["default_base_url"] == "https://inference-api.nousresearch.com/v1"
    assert writes == [("nous", _GATEWAY_ROOT)]


def test_loopback_gateway_without_a_key_builds_a_client_and_is_ready(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeOpenAI)
    _world(monkeypatch, tmp_path, _GATEWAY_ROOT)

    nous.NousBrain()._ensure_client()
    assert _FakeOpenAI.last_kwargs["base_url"] == f"{_GATEWAY_ROOT}/v1"
    assert _FakeOpenAI.last_kwargs["api_key"] == nous.LOOPBACK_PLACEHOLDER_KEY

    # Card "configured", brain switch credential check, and the pre-boot
    # dead-list rescue all read this one shared probe.
    assert app_control.is_credential_present(get_spec("nous")) is True
    from jarvis.brain.manager import _keyless_provider_is_rescued_by_oauth

    assert _keyless_provider_is_rescued_by_oauth("nous") is True
    # The mission worker counts it as viable too.
    monkeypatch.setattr(
        "jarvis.api_family_quota_state.api_family_in_cooldown", lambda *_a, **_k: False
    )
    assert mi._api_key_family_viable("nous") is True


@pytest.mark.parametrize("remote", ["https://inference-api.nousresearch.com/v1", None])
def test_remote_base_url_without_a_key_fails_cleanly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, remote: str | None
) -> None:
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeOpenAI)
    _world(monkeypatch, tmp_path, remote)

    with pytest.raises(RuntimeError, match="Nous Portal API key"):
        nous.NousBrain()._ensure_client()
    assert app_control.is_credential_present(get_spec("nous")) is False
    assert mi._api_key_family_viable("nous") is False


def test_lan_address_is_not_loopback() -> None:
    assert cfg.is_loopback_url("http://localhost:11436") is True
    assert cfg.is_loopback_url("http://[::1]:11436/v1") is True
    assert cfg.is_loopback_url("http://127.0.0.2:11436") is True
    assert cfg.is_loopback_url("http://192.168.1.20:11436") is False
    assert cfg.is_loopback_url("https://inference-api.nousresearch.com/v1") is False


async def test_catalog_lists_the_gateway_models_keyless(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _world(monkeypatch, tmp_path, _GATEWAY_ROOT)
    seen: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200, json={"object": "list", "data": [{"id": mid} for mid in _GATEWAY_MODELS]}
        )

    catalog = ModelCatalog(
        cache_path=tmp_path / "catalog.json",
        http_client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(_handler)),
    )
    result = await catalog.list_models("nous", force_refresh=True)

    assert str(seen[0].url) == f"{_GATEWAY_ROOT}/v1/models"
    assert "authorization" not in seen[0].headers
    assert result.source == "live"
    assert {m.id for m in result.models} == set(_GATEWAY_MODELS)
    assert all(classify_model(mid).free for mid in _GATEWAY_MODELS)
