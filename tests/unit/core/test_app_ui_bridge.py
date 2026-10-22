"""The round trip between an ``app_ui_*`` tool and the app's main window."""

from __future__ import annotations

import asyncio

from jarvis.core import app_ui_bridge
from jarvis.core.app_ui_bridge import NO_WINDOW_ERROR, AppUiBridge, deliver_result
from jarvis.core.events import AppUiRequest
from tests.fakes.fake_app_window import FakeAppWindowBus


async def test_request_is_published_and_the_window_answer_comes_back() -> None:
    bus = FakeAppWindowBus({"ok": True, "result": {"clicked": {"name": "Model"}}})
    reply = await AppUiBridge(bus).request("click", label="Model", wait_ms=1500)

    assert reply.ok is True
    assert reply.result == {"clicked": {"name": "Model"}}
    (request,) = bus.requests
    assert isinstance(request, AppUiRequest)
    assert (request.action, request.label, request.wait_ms) == ("click", "Model", 1500)
    assert request.request_id
    assert app_ui_bridge.pending_count() == 0


async def test_a_refusal_from_the_window_is_passed_on() -> None:
    bus = FakeAppWindowBus({"ok": False, "error": "No control named 'Foo'."})
    reply = await AppUiBridge(bus).request("click", label="Foo")

    assert reply.ok is False
    assert reply.error == "No control named 'Foo'."
    assert reply.no_window is False


async def test_no_window_times_out_with_a_plain_error() -> None:
    bus = FakeAppWindowBus(answer=False)
    reply = await AppUiBridge(bus, timeout_s=0.05).request("snapshot")

    assert reply.ok is False
    assert reply.no_window is True
    assert reply.error == NO_WINDOW_ERROR
    assert app_ui_bridge.pending_count() == 0


async def test_a_late_or_second_answer_is_not_accepted() -> None:
    bus = FakeAppWindowBus(answer=False)
    reply = await AppUiBridge(bus, timeout_s=0.01).request("snapshot")
    assert reply.no_window

    assert deliver_result(bus.requests[0].request_id, {"ok": True}) is False
    assert deliver_result("never-issued", {"ok": True}) is False


async def test_first_answer_wins() -> None:
    bus = FakeAppWindowBus(answer=False)
    task = asyncio.create_task(AppUiBridge(bus).request("snapshot"))
    await bus.requested.wait()
    request_id = bus.requests[0].request_id

    assert deliver_result(request_id, {"ok": True, "result": {"from": "main"}}) is True
    assert deliver_result(request_id, {"ok": True, "result": {"from": "other"}}) is False
    assert (await task).result == {"from": "main"}


async def test_an_unknown_action_is_refused_without_publishing() -> None:
    bus = FakeAppWindowBus()
    reply = await AppUiBridge(bus).request("drag")

    assert reply.ok is False and "Unknown app UI action" in reply.error
    assert bus.events == []
