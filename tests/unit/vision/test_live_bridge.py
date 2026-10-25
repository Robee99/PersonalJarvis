import asyncio
from uuid import uuid4

import pytest

from jarvis.core.bus import EventBus
from jarvis.vision.live_bridge import LiveVisionBridge, LiveVisionFrameRequested, LiveVisionUnavailable
from tests.unit.vision.test_live_inputs import frame


async def test_voice_requests_only_camera_for_camera_question_and_releases_response():
    bridge = LiveVisionBridge()
    bridge.register("chat-a", "window-a", ("screen", "webcam"), "local-openai", "model-a")
    bus = EventBus()
    requests = []
    async def answer(event):
        requests.append(event)
        bridge.deliver(event.request_id, "chat-a", "window-a", [frame("webcam")])
    bus.subscribe(LiveVisionFrameRequested, answer)
    result = await bridge.request("chat-a", "look at me", uuid4(), bus)
    assert result is not None and len(result.images) == 1
    assert result.provider == "local-openai" and result.model == "model-a"
    assert requests[0].sources == ("webcam",)
    assert bridge.pending_count == 0


async def test_another_window_or_conversation_cannot_supply_the_frame():
    bridge = LiveVisionBridge()
    bridge.register("chat-a", "window-a", ("screen",), "local-openai", "model")
    bus = EventBus()
    async def answer(event):
        with pytest.raises(ValueError):
            bridge.deliver(event.request_id, "chat-b", "window-a", [frame()])
        with pytest.raises(ValueError):
            bridge.deliver(event.request_id, "chat-a", "window-b", [frame()])
        bridge.deliver(event.request_id, "chat-a", "window-a", [frame()])
    bus.subscribe(LiveVisionFrameRequested, answer)
    assert await bridge.request("chat-a", "read the screen", uuid4(), bus)


async def test_stopped_source_cannot_finish_a_pending_request():
    bridge = LiveVisionBridge()
    bridge.register("chat-a", "window-a", ("screen",), "local-openai", "model")
    bus = EventBus()
    async def stop(event):
        bridge.register("chat-a", "window-a", (), "local-openai", "model")
    bus.subscribe(LiveVisionFrameRequested, stop)
    with pytest.raises(LiveVisionUnavailable):
        await bridge.request("chat-a", "screen?", uuid4(), bus)
    assert bridge.pending_count == 0


async def test_timeout_does_not_return_an_old_frame():
    bridge = LiveVisionBridge(timeout_s=.01)
    bridge.register("chat-a", "window-a", ("screen",), "local-openai", "model")
    with pytest.raises(LiveVisionUnavailable):
        await bridge.request("chat-a", "screen?", uuid4(), EventBus())
    assert bridge.pending_count == 0


async def test_cancelled_voice_turn_releases_its_waiter():
    bridge = LiveVisionBridge()
    bridge.register("chat-a", "window-a", ("screen",), "local-openai", "model")
    task = asyncio.create_task(bridge.request("chat-a", "screen?", uuid4(), EventBus()))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert bridge.pending_count == 0


async def test_heartbeat_does_not_cancel_a_camera_request_when_screen_is_also_on():
    bridge = LiveVisionBridge()
    bridge.register("chat-a", "window-a", ("screen", "webcam"), "local-openai", "model")
    bus = EventBus()
    async def answer(event):
        bridge.register("chat-a", "window-a", ("screen", "webcam"), "local-openai", "model")
        bridge.deliver(event.request_id, "chat-a", "window-a", [frame("webcam")])
    bus.subscribe(LiveVisionFrameRequested, answer)
    assert await bridge.request("chat-a", "look at me", uuid4(), bus)


async def test_late_registration_cannot_restore_a_stopped_source():
    bridge = LiveVisionBridge()
    bridge.register("chat-a", "window-a", (), "local-openai", "model", sequence=2)
    bridge.register("chat-a", "window-a", ("screen",), "local-openai", "model", sequence=1)
    assert await bridge.request("chat-a", "read the screen", uuid4(), EventBus()) is None
