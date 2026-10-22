"""``POST /api/app-ui/result`` hands the window's answer to the waiting tool."""

from __future__ import annotations

import asyncio

import httpx
from fastapi import FastAPI

from jarvis.core.app_ui_bridge import AppUiBridge
from jarvis.ui.web.app_ui_routes import router
from tests.fakes.fake_app_window import FakeAppWindowBus


def _client() -> httpx.AsyncClient:
    app = FastAPI()
    app.include_router(router)
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_posted_result_resolves_the_waiting_request() -> None:
    bus = FakeAppWindowBus(answer=False)
    task = asyncio.create_task(AppUiBridge(bus).request("click", label="Model"))
    await bus.requested.wait()

    async with _client() as client:
        response = await client.post(
            "/api/app-ui/result",
            json={
                "request_id": bus.requests[0].request_id,
                "ok": True,
                "result": {"clicked": {"name": "Model", "role": "tab"}},
            },
        )

    assert response.status_code == 200
    assert response.json() == {"accepted": True}
    reply = await task
    assert reply.ok is True
    assert reply.result["clicked"]["name"] == "Model"


async def test_a_refusal_carries_its_error() -> None:
    bus = FakeAppWindowBus(answer=False)
    task = asyncio.create_task(AppUiBridge(bus).request("click", label="Delete"))
    await bus.requested.wait()

    async with _client() as client:
        await client.post(
            "/api/app-ui/result",
            json={"request_id": bus.requests[0].request_id, "ok": False, "error": "destructive"},
        )

    reply = await task
    assert (reply.ok, reply.error) == (False, "destructive")


async def test_an_unknown_request_is_not_accepted() -> None:
    async with _client() as client:
        response = await client.post(
            "/api/app-ui/result", json={"request_id": "nobody-waits", "ok": True}
        )
    assert response.status_code == 200
    assert response.json() == {"accepted": False}


async def test_a_body_without_request_id_is_rejected() -> None:
    async with _client() as client:
        response = await client.post("/api/app-ui/result", json={"ok": True})
    assert response.status_code == 422


def test_the_route_is_mounted_by_the_server() -> None:
    from pathlib import Path

    server = Path(__file__).resolve().parents[4] / "jarvis" / "ui" / "web" / "server.py"
    source = server.read_text(encoding="utf-8")
    assert "from .app_ui_routes import router as app_ui_router" in source
    assert "app.include_router(app_ui_router)" in source
