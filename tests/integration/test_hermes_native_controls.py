"""Optional real-Hermes contract. Run with Hermes's scripts/run_tests.sh.

Requires the native Hermes source/dependencies, not a model or credentials.
The real plugin loader, API adapter, session DB and detached executor run in
isolated homes; the injected child runner performs no external action.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import os
import site
import subprocess
import threading
import time
from pathlib import Path

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


def _wait_for_fixture(predicate, timeout=8):
    """File/process readiness crosses processes; poll outside the event loop."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise TimeoutError("Fixture did not become ready")
        time.sleep(0.02)


@pytest.mark.asyncio
async def test_native_mcp_consent_and_cancel_reach_jarvis_executor(tmp_path, monkeypatch, request):
    """Actual installed Hermes SDK/approval system against Jarvis's separate SDK.

    No agent/model call or real account: the child offers a fixture Gmail tool.
    SDK 2.x abandonment and native approval withdrawal must both fail closed.
    """
    jarvis_python = request.config.getoption("--jarvis-test-python", default=None) or os.getenv(
        "JARVIS_TEST_PYTHON"
    )
    if not jarvis_python:
        pytest.skip("JARVIS_TEST_PYTHON must point to the isolated Jarvis test environment")
    runtime_site = request.config.getoption("--hermes-runtime-site", default=None) or os.getenv(
        "HERMES_TEST_RUNTIME_SITE"
    )
    if runtime_site:
        # Process installed Windows .pth files too (pywin32 DLL bootstrap). A
        # plain path in the private verifier does not run those bootstraps.
        site.addsitedir(runtime_site)
    import httpx2
    from gateway.session_context import clear_session_vars, set_session_vars
    from tools.mcp_tool_sampling import ElicitationHandler

    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS
    from tools import approval, approval_context, mcp_tool

    mcp_tool._ensure_mcp_sdk()
    assert mcp_tool._MCP_AVAILABLE and mcp_tool._MCP_ELICITATION_TYPES
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "hermes-home"))
    monkeypatch.setenv("HERMES_SESSION_PLATFORM", "api_server")
    monkeypatch.setenv("HERMES_CRON_SESSION", "")
    monkeypatch.setenv("HERMES_SINGLE_QUERY_SESSION", "")
    endpoint_file, receipts_file = tmp_path / "endpoint.txt", tmp_path / "receipts.jsonl"
    source = (await asyncio.to_thread(Path(__file__).resolve)).parents[2]
    log_file = tmp_path / "fixture-server.log"
    with log_file.open("w", encoding="utf-8") as output:
        process = await asyncio.to_thread(
            subprocess.Popen,
            [
                jarvis_python,
                "-m",
                "tests.fakes.hermes_connected_apps_server",
                str(endpoint_file),
                str(receipts_file),
            ],
            cwd=source,
            env={**os.environ, "PYTHONPATH": str(source)},
            stdout=output,
            stderr=output,
            text=True,
            encoding="utf-8",
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
        session_key = "fixture/native-connected-apps"
        session_tokens = set_session_vars(
            platform="api_server", session_key=session_key, session_id="fixture-session"
        )
        context_token = approval_context._approval_session_key.set(session_key)
        captured = contextvars.copy_context()
        queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def notify(data):
            loop.call_soon_threadsafe(queue.put_nowait, dict(data))

        def receipts():
            return (
                [
                    json.loads(line)
                    for line in receipts_file.read_text(encoding="utf-8").splitlines()
                ]
                if receipts_file.exists()
                else []
            )

        try:

            def ready():
                assert process.poll() is None, log_file.read_text(encoding="utf-8")
                return endpoint_file.exists()

            await asyncio.to_thread(_wait_for_fixture, ready)
            endpoint = endpoint_file.read_text(encoding="utf-8")
            handler = ElicitationHandler(
                "jarvis-fixture", {"timeout": 5}, call_context=lambda: captured
            )
            async with httpx2.AsyncClient(
                headers={"Authorization": "Bearer fixture-control"}
            ) as http:
                async with mcp_tool.streamable_http_client(endpoint, http_client=http) as streams:
                    async with mcp_tool.ClientSession(
                        streams[0], streams[1], elicitation_callback=handler
                    ) as client:
                        await client.initialize()
                        approval.register_gateway_notify(session_key, notify)
                        task = asyncio.create_task(client.call_tool("gmail", {"action": "send"}))
                        event = await asyncio.wait_for(queue.get(), 3)
                        assert event["request_id"] and receipts() == []
                        assert (
                            approval.resolve_gateway_approval(
                                session_key, "once", request_id=event["request_id"]
                            )
                            == 1
                        )
                        result = await asyncio.wait_for(task, 3)
                        assert not getattr(result, "is_error", getattr(result, "isError", False))
                        assert (
                            len([row for row in receipts() if row["event"] == "ActionExecuted"])
                            == 1
                        )
                        assert (
                            approval.resolve_gateway_approval(
                                session_key, "once", request_id=event["request_id"]
                            )
                            == 0
                        )

                        task = asyncio.create_task(client.call_tool("gmail", {"action": "send"}))
                        event = await asyncio.wait_for(queue.get(), 3)
                        assert (
                            approval.resolve_gateway_approval(
                                session_key, "deny", request_id=event["request_id"]
                            )
                            == 1
                        )
                        result = await asyncio.wait_for(task, 3)
                        assert getattr(result, "is_error", getattr(result, "isError", False))

                        # Same withdrawal primitive used by native run teardown
                        # after Stop. No late exact-ID reply can revive the action.
                        task = asyncio.create_task(client.call_tool("gmail", {"action": "send"}))
                        event = await asyncio.wait_for(queue.get(), 3)
                        approval.unregister_gateway_notify(session_key)
                        result = await asyncio.wait_for(task, 3)
                        assert getattr(result, "is_error", getattr(result, "isError", False))
                        assert (
                            approval.resolve_gateway_approval(
                                session_key, "once", request_id=event["request_id"]
                            )
                            == 0
                        )

                        # Actual Hermes SDK 2.x sends notifications/cancelled on
                        # coroutine abandonment; SDK 1.x's explicit notification
                        # is covered separately in the Jarvis transport tests.
                        approval.register_gateway_notify(session_key, notify)
                        task = asyncio.create_task(client.call_tool("gmail", {"action": "send"}))
                        event = await asyncio.wait_for(queue.get(), 3)
                        task.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await task
                        await asyncio.to_thread(
                            _wait_for_fixture,
                            lambda: (
                                len([row for row in receipts() if row["event"] == "ActionDenied"])
                                >= 3
                            ),
                            3,
                        )
                        approval.unregister_gateway_notify(session_key)
                        assert (
                            approval.resolve_gateway_approval(
                                session_key, "once", request_id=event["request_id"]
                            )
                            == 0
                        )
                        assert (
                            len([row for row in receipts() if row["event"] == "ActionExecuted"])
                            == 1
                        )
        finally:
            approval.unregister_gateway_notify(session_key)
            approval_context._approval_session_key.reset(context_token)
            clear_session_vars(session_tokens)
            process.terminate()
            await asyncio.to_thread(process.wait, 5)


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
