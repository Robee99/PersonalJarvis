"""Optional real-Hermes contract. Run with Hermes's scripts/run_tests.sh.

Requires the native Hermes source/dependencies, not a model or credentials.
The real plugin loader, API adapter, session DB and detached executor run in
isolated homes; the injected child runner performs no external action.
"""

from __future__ import annotations

import asyncio
import json
import threading

import pytest

pytest.importorskip("hermes_constants", reason="native Hermes environment required")
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from hermes_cli.plugins import (
    _reset_plugin_managers_for_tests,
    discover_plugins,
    get_plugin_manager,
)
from hermes_constants import reset_hermes_home_override, set_hermes_home_override

from jarvis.cli_ctl.hermes_controls import install_controls
from tools import async_delegation as registry


@pytest.mark.asyncio
async def test_real_native_child_stop_is_owned_retryable_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "process-home"))
    _reset_plugin_managers_for_tests()
    registry._reset_for_tests()
    clients, adapters, interrupts, done, tokens = [], [], [], [], []
    try:
        for label in ("A", "B"):
            home = tmp_path / label
            home.mkdir()
            home.joinpath("config.yaml").write_text(
                "plugins:\n  enabled: [jarvis-control]\n", encoding="utf-8"
            )
            install_controls(home=home, argv=["hermes"], run=lambda _a: (0, "enabled"))
            token = set_hermes_home_override(home)
            try:
                discover_plugins()
                assert get_plugin_manager().get_platform_handler_factories("api_server")
                adapter = APIServerAdapter(
                    PlatformConfig(enabled=True, extra={"key": "fixture-control-key-long-enough"})
                )
                adapters.append(adapter)
                db = await adapter._ensure_session_db_async()
                db.create_session("jarvis-main", source="api_server")
                db.record_gateway_session_peer(
                    "jarvis-main", source="api_server", session_key="jarvis:main"
                )
                db.create_session("foreign", source="api_server")
                interrupt, ended = threading.Event(), threading.Event()
                interrupts.append(interrupt)
                done.append(ended)

                # Freeze closure variables per child; each profile shares the
                # same durable session id to prove ids alone are insufficient.
                def child(interrupt=interrupt, ended=ended):
                    if not interrupt.wait(10):
                        raise RuntimeError("Fixture child was not interrupted")
                    ended.set()
                    return {"status": "interrupted", "summary": "Fixture stopped"}

                handle = registry.dispatch_async_delegation(
                    goal="fixture",
                    context=None,
                    toolsets=None,
                    role="test",
                    model=None,
                    session_key="jarvis:main",
                    parent_session_id="jarvis-main",
                    runner=child,
                    interrupt_fn=interrupt.set,
                )
                assert handle["status"] == "dispatched"
                tokens.append(handle["delegation_id"])
                app = web.Application()
                adapter._wire_plugin_handlers(app)
                client = TestClient(TestServer(app))
                await client.start_server()
                clients.append(client)
            finally:
                reset_hermes_home_override(token)
        headers = {
            "Authorization": "Bearer fixture-control-key-long-enough",
            "X-Hermes-Session-Key": "jarvis:main",
        }
        path = "/api/jarvis/conversations/jarvis-main/delegations"
        for index in (0, 1, 0):  # real A -> B -> A profile resolution
            home_token = set_hermes_home_override(tmp_path / ("A" if index == 0 else "B"))
            try:
                response = await clients[index].get(path, headers=headers)
                assert response.status == 200
                data = await response.json()
                assert [r["delegation_id"] for r in data["data"]] == [tokens[index]]
                assert (await clients[index].get(path)).status == 401
                before_foreign = [e.is_set() for e in interrupts]
                foreign = await clients[index].post(
                    path + "/stop", headers=headers, json={"delegation_ids": [tokens[1 - index]]}
                )
                assert foreign.status == 409
                assert [e.is_set() for e in interrupts] == before_foreign
                if index == 0 and not interrupts[index].is_set():
                    record = registry._records[tokens[index]]
                    real_interrupt = record["interrupt_fn"]

                    def rejects():
                        raise RuntimeError("secret fixture content must not leave the gateway")

                    record["interrupt_fn"] = rejects
                    response = await clients[index].post(
                        path + "/stop", headers=headers, json={"delegation_ids": [tokens[index]]}
                    )
                    if not interrupts[index].is_set():
                        assert response.status == 503
                        assert "secret" not in await response.text()
                    record["interrupt_fn"] = real_interrupt
                response = await clients[index].post(
                    path + "/stop", headers=headers, json={"delegation_ids": [tokens[index]]}
                )
                assert response.status == 200
                assert await asyncio.to_thread(done[index].wait, 2)
                # A real detached worker returns and finalizes, not just a
                # local delivery/polling watcher disappearing.
                for _ in range(100):
                    if registry._records[tokens[index]]["status"] == "interrupted":
                        break
                    await asyncio.sleep(0.01)
                assert registry._records[tokens[index]]["status"] == "interrupted"
                again = await clients[index].post(
                    path + "/stop", headers=headers, json={"delegation_ids": [tokens[index]]}
                )
                assert again.status == 200
                assert (await again.json())["requested"] == []
            finally:
                reset_hermes_home_override(home_token)
    finally:
        for event in interrupts:
            event.set()
        for client in clients:
            await client.close()
        for adapter in adapters:
            adapter._close_cached_session_dbs()
        registry._reset_for_tests()
        _reset_plugin_managers_for_tests()


@pytest.mark.asyncio
async def test_native_model_preference_is_durable_owned_and_used_by_runtime(tmp_path, monkeypatch):
    """Exercise the real DB/route/precedence contract without calling a model."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    _reset_plugin_managers_for_tests()
    tmp_path.joinpath("config.yaml").write_text(
        "plugins:\n  enabled: [jarvis-control]\n", encoding="utf-8"
    )
    install_controls(home=tmp_path, argv=["hermes"], run=lambda _a: (0, "enabled"))
    discover_plugins()
    config = PlatformConfig(
        enabled=True,
        extra={
            "key": "fixture-control-key-long-enough",
            "model_name": "hermes-agent",
            "model_routes": {"gemma": {"model": "fixture-gemma", "provider": "local-gemma"}},
        },
    )
    adapter = APIServerAdapter(config)
    reloaded_adapters = []
    app = web.Application()
    adapter._wire_plugin_handlers(app)
    path = "/api/jarvis/conversations/jarvis-main/model"
    headers = {
        "Authorization": "Bearer fixture-control-key-long-enough",
        "X-Hermes-Session-Key": "jarvis:main",
    }
    try:
        async with TestClient(TestServer(app)) as client:
            assert (await client.get(path)).status == 401
            response = await client.get(path, headers=headers)
            assert (await response.json())["selection"] == "hermes-agent"
            for choice, model, provider in [
                ("gemma", "fixture-gemma", "local-gemma"),
                ("nous::fixture-free-model", "fixture-free-model", "nous"),
            ]:
                response = await client.post(path, headers=headers, json={"selection": choice})
                assert response.status == 200, await response.text()
                assert (await response.json())["selection"] == choice
                # Close/reopen the real native DB: the preference is not an
                # adapter-local cache and survives the gateway rebuilding it.
                reloaded = APIServerAdapter(config)
                reloaded_adapters.append(reloaded)
                db = await reloaded._ensure_session_db_async()
                row = db.get_session("jarvis-main")
                assert row["session_key"] == "jarvis:main"
                runtime = adapter._effective_session_runtime_request(
                    session=row, body={"model": "hermes-agent"}
                )
                assert runtime["persisted_lock"] and runtime["require_model_lock"]
                # A stale native /model override also cannot shadow this lock.
                monkeypatch.setattr(
                    adapter,
                    "_session_model_override_for",
                    lambda _k: {
                        "model": "stale-other-model",
                        "provider": "other-provider",
                    },
                )

                def provider_runtime(
                    kwargs, chosen, *, target_model, required=False, provider=provider, model=model
                ):
                    assert required and chosen == provider and target_model == model
                    kwargs["provider"] = chosen
                    return True

                monkeypatch.setattr(adapter, "_apply_provider_runtime", provider_runtime)
                kwargs = {"provider": "old-default"}
                resolved, override, _, _ = adapter._select_agent_runtime(
                    kwargs,
                    "old-model",
                    requested_model=runtime["requested"]["model"],
                    requested_provider=runtime["requested"]["provider"],
                    route=runtime["route"],
                    session_model=row["model"],
                    confirmed_runtime_lock=True,
                    gateway_session_key="jarvis:main",
                    session_id="jarvis-main",
                )
                assert (resolved, kwargs["provider"], override) == (model, provider, None)
                before = db.get_session("jarvis-main")["model_config"]

                def unavailable(*_a, **_kw):
                    raise RuntimeError("fixture local model unavailable")

                monkeypatch.setattr(adapter, "_apply_provider_runtime", unavailable)
                with pytest.raises(RuntimeError, match="unavailable"):
                    adapter._select_agent_runtime(
                        {},
                        "old-model",
                        requested_model=runtime["requested"]["model"],
                        requested_provider=runtime["requested"]["provider"],
                        route=runtime["route"],
                        session_model=row["model"],
                        confirmed_runtime_lock=True,
                        gateway_session_key="jarvis:main",
                        session_id="jarvis-main",
                    )
                assert db.get_session("jarvis-main")["model_config"] == before
                foreign = {**headers, "X-Hermes-Session-Key": "another-chat"}
                assert (
                    await client.post(path, headers=foreign, json={"selection": "gemma"})
                ).status == 403
                assert db.get_session("jarvis-main")["model_config"] == before
            response = await client.post(path, headers=headers, json={"selection": "hermes-agent"})
            assert response.status == 200
            assert (await response.json())["source"] == "hermes_routing"
            row = db.get_session("jarvis-main")
            assert not json.loads(row["model_config"])["browser_model_lock"]["confirmed"]
            assert adapter._runtime_request_from_persisted_session_lock(row, {}) is None
            assert row["model"] == ""
    finally:
        adapter._close_cached_session_dbs()
        for reloaded in reloaded_adapters:
            reloaded._close_cached_session_dbs()
        _reset_plugin_managers_for_tests()
