"""Voice and chat use one native, durable Hermes model preference."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from jarvis.brain import hermes_selection
from jarvis.core.protocols import BrainMessage, BrainRequest
from jarvis.plugins.brain import hermes
from tests.fakes.fake_hermes_api import FakeHermesApi, say


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(hermes, "_SESSIONS", {})
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(hermes, "configured_base_url", lambda: "http://127.0.0.1:8642")
    server = FakeHermesApi()
    original = hermes.HermesBrain.__init__

    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.transport = server.transport

    monkeypatch.setattr(hermes.HermesBrain, "__init__", init)
    return server


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["nous::cloud-one:free", "local-gemma::gemma", "hermes-agent"])
async def test_native_selection_survives_adapter_rebuild_and_ignores_old_per_surface_pick(
    setup, model
):
    server = setup
    await hermes_selection.set_selection(model)
    voice, chat = hermes.HermesBrain(model="old-voice"), hermes.HermesBrain(model="old-chat")
    voice.join_conversation()
    chat.join_conversation()
    for brain in (voice, chat):
        server.scripts.append(say("fixture reply"))
        async for _ in brain.complete(
            BrainRequest(messages=(BrainMessage(role="user", content="hello"),))
        ):
            pass
        assert server.runs[-1].body["model"] == "hermes-agent"
        assert "provider" not in server.runs[-1].body
    assert (await hermes_selection.get_selection())["selection"] == model


@pytest.mark.asyncio
async def test_native_model_failure_does_not_change_saved_selection(setup):
    server = setup
    await hermes_selection.set_selection("local-qwen::qwen")
    server.reject_model_selection = True
    with pytest.raises(hermes_selection.SelectionError, match="selection"):
        await hermes_selection.set_selection("nous::cloud-two:free")
    assert (await hermes_selection.get_selection())["selection"] == "local-qwen::qwen"


@pytest.fixture
def app(setup, tmp_path, monkeypatch):
    from jarvis.agent_chat.service import AgentChatService
    from jarvis.agent_chat.store import AgentChatStore
    from jarvis.brain.model_catalog import CatalogResult, ModelInfo
    from jarvis.core.config import JarvisConfig
    from jarvis.ui.web import agent_chat_routes, provider_routes

    class Catalog:
        async def list_models(self, provider, *, force_refresh=False):
            return CatalogResult(
                provider=provider,
                models=(
                    ModelInfo(id="gemma", label="Cached Gemma"),
                    ModelInfo(id="nous::fixture-free", label="Free fixture"),
                ),
                source="live",
                fetched_at=123.0,
            )

    def no_extra_pin(*_a, **_kw):
        raise AssertionError(
            "Hermes must not write a second Jarvis model pin or call a model probe"
        )

    monkeypatch.setattr("jarvis.core.config_writer.set_brain_provider_model", no_extra_pin)
    monkeypatch.setattr(provider_routes, "_probe_brain_model", no_extra_pin)
    application = FastAPI()
    application.include_router(agent_chat_routes.router)
    application.include_router(provider_routes.router)
    application.state.config = JarvisConfig()
    application.state.model_catalog = Catalog()
    application.state.agent_chat = AgentChatService(AgentChatStore(tmp_path / "chat.db"))
    yield application
    application.state.agent_chat.store.close()


@pytest.mark.asyncio
async def test_settings_chat_and_voice_read_the_same_preference(app, setup, tmp_path):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        response = await client.put(
            "/api/agent-chat/selection",
            json={
                "provider": "hermes",
                "model": "gemma",
            },
        )
        assert response.status_code == 200, response.text
        assert (await client.get("/api/providers/hermes/models")).json()["current_model"] == "gemma"
        created = await client.post(
            "/api/agent-chat/sessions",
            json={
                "surface": "jarvis",
                "provider": "hermes",
                "model": "stale-chat-model",
                "cwd": str(tmp_path),
            },
        )
        assert created.status_code == 201, created.text
        sid = created.json()["session_id"]
        assert created.json()["model"] == "gemma"
        response = await client.put(
            "/api/providers/hermes/model", json={"model": "nous::fixture-free"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["persisted"] and response.json()["applied_live"]
        assert response.json()["probe"] is None
        assert app.state.agent_chat.store.get_session(sid).model == "nous::fixture-free"
        updates = app.state.agent_chat.store.list_events(sid)
        assert any(
            e["kind"] == "session_updated" and e["payload"].get("model") == "nous::fixture-free"
            for e in updates
        )
        assert (await client.get(f"/api/agent-chat/sessions/{sid}")).json()["session"][
            "model"
        ] == "nous::fixture-free"
        catalog = await client.get("/api/agent-chat/catalog?surface=jarvis")
        assert catalog.json()["selection"]["model"] == "nous::fixture-free"
        response = await client.patch(f"/api/agent-chat/sessions/{sid}", json={"model": "gemma"})
        assert response.status_code == 200, response.text
        assert (await hermes_selection.get_selection())["selection"] == "gemma"
        voice = hermes.HermesBrain(model="old-voice-pick")
        voice.join_conversation()
        setup.scripts.append(say("voice fixture reply"))
        _ = [
            delta
            async for delta in voice.complete(
                BrainRequest(
                    messages=(BrainMessage(role="user", content="hello"),),
                )
            )
        ]
        assert setup.runs[-1].body["model"] == "hermes-agent"
        assert (await client.get("/api/providers/hermes/models")).json()["current_model"] == "gemma"


@pytest.mark.asyncio
async def test_failed_selector_write_preserves_existing_preference_and_chat(app, setup):
    await hermes_selection.set_selection("gemma")
    svc = app.state.agent_chat
    session = svc.create_session(provider="hermes", model="gemma", surface="jarvis")
    setup.reject_model_selection = True
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://fixture"
    ) as client:
        for method, path in [
            ("PUT", "/api/providers/hermes/model"),
            ("PUT", "/api/agent-chat/selection"),
            ("PATCH", f"/api/agent-chat/sessions/{session.session_id}"),
        ]:
            response = await client.request(
                method, path, json={"provider": "hermes", "model": "nous::fixture-free"}
            )
            assert response.status_code == 503, response.text
            assert "private provider" not in response.text
        assert svc.store.get_session(session.session_id).model == "gemma"
        assert (await hermes_selection.get_selection())["selection"] == "gemma"


@pytest.mark.asyncio
async def test_gateway_reported_cloud_fallback_keeps_user_preference(setup, caplog):
    await hermes_selection.set_selection("hermes-agent")
    brain = hermes.HermesBrain()
    brain.join_conversation()
    runtime = {
        "provider": "nous",
        "model": "fixture-free-cloud",
        "fallback_reason": "local unavailable",
    }
    setup.scripts.append(say("cloud fixture reply", runtime=runtime))
    with caplog.at_level("INFO", logger="jarvis.plugins.brain.hermes"):
        _ = [
            delta
            async for delta in brain.complete(
                BrainRequest(
                    messages=(BrainMessage(role="user", content="hello"),),
                )
            )
        ]
    assert brain.last_runtime == runtime
    assert "fallback to nous/fixture-free-cloud: local unavailable" in caplog.text
    assert (await hermes_selection.get_selection())["selection"] == "hermes-agent"
