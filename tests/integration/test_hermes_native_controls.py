"""Optional real-Hermes contract. Run with Hermes's scripts/run_tests.sh.

Requires the native Hermes source/dependencies, not a model or credentials.
The real plugin loader, API adapter, session DB and detached executor run in
isolated homes; the injected child runner performs no external action.
"""

from __future__ import annotations

import asyncio
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
