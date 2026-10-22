"""Nous Portal is wired at the same provider sites as NVIDIA NIM.

Nous Portal (inference-api.nousresearch.com) is a cloud model host with an
OpenAI-compatible API. It must be a first-class brain provider and mission
worker — (1) plugin + credentials, (2) the "Brain Provider" card + live model
catalog, (3) the in-process subagent worker — and it is labelled as "Nous
Portal", never as the separate "Hermes Agent" CLI product.

The Portal answers 400 "missing user tag" to any chat request without
``{"tags": ["user=<name>"]}`` in the body, so the tests pin that every request
the brain (and therefore the worker) sends carries one.
"""
from __future__ import annotations

import importlib
import tomllib
from pathlib import Path
from typing import Any

import pytest

import jarvis.core.config as cfg
from jarvis.agent_chat.catalog import provider_row
from jarvis.agent_chat.runner_api import BRAIN_BY_PROVIDER
from jarvis.brain.manager import (
    _SECRET_KEY_TO_BRAIN,
    PROVIDER_ALIASES,
    _provider_display_name,
    get_tier_default_model,
)
from jarvis.brain.model_catalog import (
    _ENDPOINTS,
    CATALOG_PROVIDERS,
    catalog_spec,
    classify_model,
    parse_models_response,
)
from jarvis.core.config import JarvisConfig
from jarvis.core.protocols import BrainMessage, BrainRequest, ImageBlock
from jarvis.missions.init import _API_AGENT_SLUGS, _select_subagent_worker_kind
from jarvis.missions.worker_runtime.provider_map import env_vars_for, to_worker_slug
from jarvis.missions.workers import api_agent_worker
from jarvis.plugins.brain import nous
from jarvis.setup.wizard import SECRETS
from jarvis.ui.web.provider_spec import get_spec, provider_billing

_FREE_STEP = "stepfun/step-3.7-flash:free"
_REPO = Path(__file__).resolve().parents[3]


class _FakeOpenAI:
    last_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        _FakeOpenAI.last_kwargs = kwargs


class _EmptyStream:
    def __aiter__(self) -> _EmptyStream:
        return self

    async def __anext__(self) -> Any:
        raise StopAsyncIteration


class _RecordingClient:
    """Stands in for AsyncOpenAI: records every chat.completions.create call."""

    base_url = nous.BASE_URL

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        chat = type("Chat", (), {})()
        chat.completions = type("Completions", (), {})()
        chat.completions.create = self._create
        self.chat = chat

    async def _create(self, **kwargs: Any) -> _EmptyStream:
        self.calls.append(kwargs)
        return _EmptyStream()


def _keyed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, key: str | None = "sk-nous-test"
) -> None:
    # No config file on disk: no base_url override and no team proxy, so the
    # endpoint resolves to the vendor default regardless of the local install.
    monkeypatch.setenv("JARVIS_CONFIG", str(tmp_path / "absent.toml"))
    monkeypatch.setattr(cfg, "load_config", lambda: JarvisConfig())
    monkeypatch.setattr(cfg, "get_provider_secret", lambda _pid: key)


async def _drain(brain: Any, req: BrainRequest) -> None:
    async for _ in brain.complete(req):
        pass


# ── Site 1: plugin + credential layer ────────────────────────────────────────
def test_nous_entry_point_is_declared_and_loads() -> None:
    project = tomllib.loads((_REPO / "pyproject.toml").read_text(encoding="utf-8"))
    target = project["project"]["entry-points"]["jarvis.brain"]["nous"]
    assert target == "jarvis.plugins.brain.nous:NousBrain"
    module_name, cls_name = target.split(":")
    cls = getattr(importlib.import_module(module_name), cls_name)
    brain = cls()
    assert brain.name == "nous"
    assert brain.can_call_tools() is True
    # No vision advertised: images never go to this cloud host.
    assert brain.supports_vision is False


def test_nous_credential_slots_resolve() -> None:
    assert cfg.PROVIDER_SECRET_CANDIDATES["nous"] == (("nous_api_key", "NOUS_API_KEY"),)
    agent = cfg.JARVIS_AGENT_SECRET_CANDIDATES["nous"]
    assert agent[0] == ("jarvis_agent_nous_api_key", "JARVIS_AGENT_NOUS_API_KEY")
    assert ("nous_api_key", "NOUS_API_KEY") in agent
    assert cfg.secret_family_primary_slot("nous") == "nous_api_key"
    wizard_slots = {spec.key for spec in SECRETS}
    assert {"nous_api_key", "jarvis_agent_nous_api_key"} <= wizard_slots


def test_plugin_builds_the_client_on_the_portal_base_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeOpenAI)
    _keyed(monkeypatch, tmp_path)
    nous.NousBrain()._ensure_client()
    assert _FakeOpenAI.last_kwargs["base_url"] == "https://inference-api.nousresearch.com/v1"
    assert _FakeOpenAI.last_kwargs["api_key"] == "sk-nous-test"


def test_missing_key_is_a_clean_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _keyed(monkeypatch, tmp_path, key=None)
    with pytest.raises(RuntimeError, match="Nous Portal API key"):
        nous.NousBrain()._ensure_client()


async def test_every_chat_request_carries_the_user_tag() -> None:
    brain = nous.NousBrain(model=_FREE_STEP)
    client = _RecordingClient()
    brain._client = client
    await _drain(brain, BrainRequest(messages=(BrainMessage(role="user", content="hi"),)))
    # A turn that also opts out of reasoning keeps the tag (only the
    # reasoning knob is ever trimmed on retry).
    await _drain(
        brain,
        BrainRequest(
            messages=(BrainMessage(role="user", content="hi"),), reasoning_effort="none"
        ),
    )
    assert len(client.calls) == 2
    for call in client.calls:
        assert call["model"] == _FREE_STEP
        assert call["extra_body"]["tags"] == ["user=jarvis"]


async def test_images_are_never_sent_to_the_portal() -> None:
    brain = nous.NousBrain()
    client = _RecordingClient()
    brain._client = client
    image = ImageBlock(mime="image/png", data_b64="iVBORw0KGgo=")
    await _drain(
        brain,
        BrainRequest(messages=(BrainMessage(role="user", content="look", images=(image,)),)),
    )
    assert "image_url" not in repr(client.calls[0]["messages"])


def test_default_model_is_a_free_route_and_costs_nothing() -> None:
    brain = nous.NousBrain()
    assert nous.DEFAULT_MODEL == _FREE_STEP
    req = BrainRequest(messages=(BrainMessage(role="user", content="hi"),))
    assert brain.estimate_cost(req) == 0.0


# ── Site 2: "Brain Provider" card + live model catalog ───────────────────────
def test_provider_card_is_an_accurately_labelled_cloud_api_key_provider() -> None:
    spec = get_spec("nous")
    assert spec is not None
    assert spec.label == "Nous Portal"
    assert "Hermes Agent" not in spec.label
    assert spec.tier == "brain"
    assert spec.auth_mode == "api_key"
    assert provider_billing(spec) == "api"
    assert spec.secret_keys == ("nous_api_key",)
    assert spec.dashboard_url == "https://portal.nousresearch.com"
    assert spec.brain_switchable is True
    assert spec.recommended is False
    assert spec.recommended_model == _FREE_STEP
    assert "sk-nous-" in (spec.credential_help or "")


def test_nous_has_a_live_model_catalog_with_free_routes() -> None:
    spec = catalog_spec("nous")
    assert spec is not None
    assert spec.tier == "brain"
    assert spec.live is True
    assert "nous" in CATALOG_PROVIDERS
    ep = _ENDPOINTS["nous"]
    assert ep.vendor_base + ep.path == "https://inference-api.nousresearch.com/v1/models"
    assert ep.auth == "bearer"
    curated = {m.id for m in spec.curated}
    assert _FREE_STEP in curated
    models = parse_models_response(
        "nous", {"data": [{"id": _FREE_STEP}, {"id": "Hermes-4-70B"}]}
    )
    assert [m.id for m in models] == [_FREE_STEP, "Hermes-4-70B"]
    assert classify_model(_FREE_STEP).free is True
    assert classify_model("Hermes-4-70B").free is False


def test_nous_tier_defaults_names_and_aliases() -> None:
    assert get_tier_default_model("router", "nous") == _FREE_STEP
    assert get_tier_default_model("deep", "nous") == _FREE_STEP
    assert PROVIDER_ALIASES["nous"] == "nous"
    # "hermes" names the Hermes Agent CLI, never this cloud host.
    assert PROVIDER_ALIASES.get("hermes") != "nous"
    assert _provider_display_name("nous") == "Nous Portal"
    assert _SECRET_KEY_TO_BRAIN["nous_api_key"] == "nous"


# ── Site 3: subagent worker + agent chat ─────────────────────────────────────
def test_nous_runs_as_in_process_api_agent_subagent() -> None:
    assert "nous" in _API_AGENT_SLUGS
    assert _select_subagent_worker_kind("nous", "") == "api_agent"
    assert api_agent_worker.supports_api_agent_worker("nous") is True
    assert api_agent_worker._BRAIN_BY_PROVIDER["nous"] == (
        "jarvis.plugins.brain.nous",
        "NousBrain",
    )
    assert api_agent_worker._DEFAULT_MODEL["nous"] == _FREE_STEP
    assert to_worker_slug("nous") == "nous"
    assert env_vars_for("nous") == ("NOUS_API_KEY",)


async def test_worker_brain_sends_the_user_tag() -> None:
    brain = api_agent_worker._build_brain("nous", _FREE_STEP)
    assert isinstance(brain, nous.NousBrain)
    client = _RecordingClient()
    brain._client = client
    await _drain(brain, BrainRequest(messages=(BrainMessage(role="user", content="go"),)))
    assert client.calls[0]["extra_body"]["tags"] == ["user=jarvis"]


def test_agent_chat_offers_nous_as_an_api_row() -> None:
    assert BRAIN_BY_PROVIDER["nous"] == ("jarvis.plugins.brain.nous", "NousBrain")
    row = provider_row("nous")
    assert row is not None
    assert row.label == "Nous Portal"
    assert row.runner == "api"
    assert row.models_source == "live"
