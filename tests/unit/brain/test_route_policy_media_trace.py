"""Route policy at the turn level: media privacy, tool hoisting, escalation and trace.

With ``[brain.route_policy]`` on:
* an image turn only reaches targets declared ``local`` unless the user set
  ``allow_cloud_vision``; with no local target the turn answers that the image
  stays on the device and no model is called;
* a tool turn never pulls a provider from outside the configured tiers;
* a deep turn that fails on every model is not escalated on its own;
* the trace id given to ``generate`` is the one on the route event and on the
  dispatch, so tool events of the turn correlate with it.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID, uuid4

import pytest

from jarvis.brain import manager as manager_mod
from jarvis.brain.manager import BrainManager
from jarvis.brain.route_policy import RouteDecision, media_stays_local_message
from jarvis.brain.streaming import StreamingAggregate
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainRoutePolicyConfig, JarvisConfig
from jarvis.core.events import BrainRouteSelected
from jarvis.core.protocols import BrainDelta, BrainRequest, ImageBlock
from jarvis.screen_context.turn import TurnScreenContext

CLOUD = ("openrouter", "cloud-model")
LOCAL = ("local-openai", "qwen-local")

POLICY = {
    "enabled": True,
    "fast": {"provider": "openrouter", "model": "cloud-model"},
    "deep": {"provider": "local-openai", "model": "qwen-local", "local": True},
    "escalation": {"enabled": True, "agent": "claude", "trigger_phrases": ["ask claude"]},
}

_IMG = ImageBlock(mime="image/png", data_b64="ZmFrZQ==")


class _FakeBrain:
    context_window = 8192
    supports_tools = True
    supports_vision = True

    def __init__(self, name: str) -> None:
        self.name = name

    async def complete(self, req: BrainRequest) -> AsyncIterator[BrainDelta]:
        yield BrainDelta(content="ok")
        yield BrainDelta(finish_reason="stop")


class _RecordingDispatcher:
    def __init__(self, provider: str, calls: list[dict], fail: bool) -> None:
        self._provider = provider
        self._calls = calls
        self._fail = fail

    async def dispatch(self, user_text, *, images=(), history=None, trace_id=None, **_kw):
        self._calls.append({
            "provider": self._provider, "images": images, "trace_id": trace_id,
        })
        if self._fail:
            raise ConnectionError("provider down")
        agg = StreamingAggregate()
        agg.text = f"reply from {self._provider}"
        agg.finish_reason = "stop"
        return agg


def _dispatched(calls: list[dict]) -> list[dict]:
    return [c for c in calls if "provider" in c]


def _built(calls: list[dict]) -> list[dict]:
    return [c for c in calls if "built" in c]


def _manager(
    chain: list[tuple[str, str | None]], policy: dict | None = None, *, fail: bool = False,
) -> tuple[BrainManager, list[dict], list[BrainRouteSelected]]:
    cfg = JarvisConfig()
    cfg.brain.primary = chain[0][0]
    cfg.brain.route_policy = BrainRoutePolicyConfig.model_validate(policy or POLICY)
    mgr = BrainManager(config=cfg, bus=EventBus(), tools={})
    calls: list[dict] = []
    routes: list[BrainRouteSelected] = []

    async def _record(event: BrainRouteSelected) -> None:
        routes.append(event)

    mgr._bus.subscribe(BrainRouteSelected, _record)

    def _chain(_level):
        mgr._last_route_decision = RouteDecision("fast", list(chain), "intent:fast")
        return list(chain)

    mgr._build_fallback_chain = _chain  # type: ignore[method-assign]
    mgr._get_brain = lambda name, _model: _FakeBrain(name)  # type: ignore[method-assign]
    def _dispatcher(brain, *, tools_override=None, tool_images=True, **_kw):
        calls.append({"built": brain.name, "tool_images": tool_images})
        return _RecordingDispatcher(brain.name, calls, fail)

    mgr._build_dispatcher = _dispatcher  # type: ignore[method-assign]

    async def _screen_not_requested(*_args, **_kwargs) -> TurnScreenContext:
        return TurnScreenContext(status="none")

    mgr._resolve_screen_context_turn = _screen_not_requested  # type: ignore[method-assign]

    async def _no_permanent_vision(**_kwargs) -> tuple[ImageBlock, ...]:
        return ()

    mgr._collect_vision_images = _no_permanent_vision  # type: ignore[method-assign]
    return mgr, calls, routes


@pytest.mark.asyncio
async def test_image_turn_skips_cloud_targets_and_uses_the_local_one() -> None:
    mgr, calls, _ = _manager([CLOUD, LOCAL])
    mgr.add_dropped_context("[dropped invoice.png]", (_IMG,))

    await mgr.generate("what does this say", trace_id=uuid4(), use_history=False)

    assert [c["provider"] for c in _dispatched(calls)] == ["local-openai"]
    assert _IMG in _dispatched(calls)[0]["images"]


@pytest.mark.asyncio
async def test_image_turn_without_a_local_target_stays_on_the_device() -> None:
    mgr, calls, routes = _manager([CLOUD])
    mgr.add_dropped_context("[dropped invoice.png]", (_IMG,))

    answer = await mgr.generate("what does this say", trace_id=uuid4(), use_history=False)

    assert calls == [], "the image must not reach a cloud model without consent"
    assert answer == media_stays_local_message(mgr._resolve_turn_lang())
    assert routes[-1].outcome == "blocked"
    assert routes[-1].reason == "privacy:media-stays-local"
    assert "openrouter:cloud-vision-off" in routes[-1].excluded


@pytest.mark.asyncio
async def test_cloud_vision_consent_lets_the_cloud_target_take_the_image() -> None:
    mgr, calls, _ = _manager([CLOUD], {**POLICY, "allow_cloud_vision": True})
    mgr.add_dropped_context("[dropped invoice.png]", (_IMG,))

    await mgr.generate("what does this say", trace_id=uuid4(), use_history=False)

    assert [c["provider"] for c in _dispatched(calls)] == ["openrouter"]


@pytest.mark.asyncio
async def test_text_turn_is_unaffected_by_the_media_gate() -> None:
    mgr, calls, _ = _manager([CLOUD, LOCAL])

    await mgr.generate("hello there", trace_id=uuid4(), use_history=False)

    assert [c["provider"] for c in _dispatched(calls)] == ["openrouter"]


def test_tool_hoist_keeps_the_policy_chain_and_adds_no_provider() -> None:
    mgr, _, _ = _manager([CLOUD, LOCAL])
    mgr._brain_can_call_tools = lambda p, _m: p == "local-openai"  # type: ignore[method-assign]

    assert mgr._hoist_tool_model([CLOUD, LOCAL]) == [LOCAL]


@pytest.mark.asyncio
async def test_failed_deep_turn_is_not_escalated_automatically(monkeypatch) -> None:
    mgr, calls, _ = _manager([LOCAL], fail=True)

    def _no_delegate(_cfg, on_progress=None):
        raise AssertionError("a failed turn must not escalate without an explicit request")

    monkeypatch.setattr(manager_mod, "delegate_from_config", _no_delegate)

    await mgr.generate(
        "design the whole migration plan in depth", trace_id=uuid4(), use_history=False,
    )

    assert _dispatched(calls) and all(
        c["provider"] == "local-openai" for c in _dispatched(calls)
    )
    assert mgr._last_turn_all_failed is True


@pytest.mark.asyncio
async def test_one_trace_id_covers_route_event_and_dispatch() -> None:
    mgr, calls, routes = _manager([CLOUD])
    trace = uuid4()

    await mgr.generate("hello there", trace_id=trace, use_history=False)

    assert routes and routes[0].trace_id == trace
    assert _dispatched(calls)[0]["trace_id"] == trace


@pytest.mark.asyncio
async def test_turn_without_a_trace_id_still_uses_one_id() -> None:
    mgr, calls, routes = _manager([CLOUD])

    await mgr.generate("hello there", use_history=False)

    assert isinstance(_dispatched(calls)[0]["trace_id"], UUID)
    assert routes[0].trace_id == _dispatched(calls)[0]["trace_id"]


@pytest.mark.asyncio
async def test_cloud_target_without_consent_gets_no_tool_screenshots() -> None:
    mgr, calls, _ = _manager([CLOUD])

    await mgr.generate("hello there", trace_id=uuid4(), use_history=False)

    assert _built(calls)[0] == {"built": "openrouter", "tool_images": False}


@pytest.mark.asyncio
async def test_local_target_and_consented_cloud_get_tool_screenshots() -> None:
    local_mgr, local_calls, _ = _manager([LOCAL])
    await local_mgr.generate("hello there", trace_id=uuid4(), use_history=False)
    consent_mgr, consent_calls, _ = _manager([CLOUD], {**POLICY, "allow_cloud_vision": True})
    await consent_mgr.generate("hello there", trace_id=uuid4(), use_history=False)

    assert _built(local_calls)[0]["tool_images"] is True
    assert _built(consent_calls)[0]["tool_images"] is True
