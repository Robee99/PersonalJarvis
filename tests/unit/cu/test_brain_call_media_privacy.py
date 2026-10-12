"""Computer-Use screenshots honour ``[brain.route_policy]`` media consent.

Every CU engine selects its planner through ``ComputerUsePlannerSelector`` and
falls back through ``iter_last_resort_vision``. With a routing policy, a
screenshot may only reach a tier marked ``local`` (or any allowed tier once the
owner sets ``allow_cloud_vision``); the last resort never reaches outside the
policy's tiers. Without a policy nothing changes.
"""
from __future__ import annotations

from typing import Any

import pytest

from jarvis.core.config import BrainRoutePolicyConfig
from jarvis.core.protocols import BrainDelta, ImageBlock
from jarvis.cu.brain_call import CUNoVisionProviderError, call_vision_brain
from jarvis.harness.computer_use_planner import iter_last_resort_vision

POLICY = {
    "enabled": True,
    "fast": {"provider": "cloud-fast", "model": "step"},
    "deep": {"provider": "local-deep", "model": "qwen", "local": True},
}

_IMG = ImageBlock(mime="image/png", data_b64="ZmFrZQ==")


class _FakeBrain:
    supports_tools = False
    supports_vision = True

    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, req: Any):  # type: ignore[no-untyped-def]
        self.calls += 1
        yield BrainDelta(content='{"action": "done"}')
        yield BrainDelta(finish_reason="stop")

    def estimate_cost(self, req: Any) -> float:
        return 0.0


class _Registry:
    def available(self) -> list[str]:
        return ["cloud-fast", "local-deep", "other-cloud"]


class _FakeManager:
    active_provider = "cloud-fast"

    def __init__(self, chain: list[tuple[str, str | None]], policy: dict | None) -> None:
        self._chain = chain
        self._policy = BrainRoutePolicyConfig.model_validate(policy) if policy else None
        self.brains = {name: _FakeBrain() for name in _Registry().available()}
        self._registry = _Registry()

    def _route_policy(self):
        return self._policy

    def _build_fallback_chain(self, level: str) -> list[tuple[str, str | None]]:
        return list(self._chain)

    def _get_brain(self, name: str, model: str | None = None) -> _FakeBrain:
        return self.brains[name]

    def _cu_model(self, name: str) -> str | None:
        return None

    def _fast_model(self, name: str) -> str | None:
        return {"cloud-fast": "step", "local-deep": "qwen"}.get(name)


def _prompt(_provider: str, _brain: Any) -> tuple[str, str]:
    return "system", "user"


@pytest.mark.asyncio
async def test_screenshot_skips_the_cloud_tier_and_uses_the_local_one() -> None:
    mgr = _FakeManager([("cloud-fast", "step"), ("local-deep", "qwen")], POLICY)

    reply = await call_vision_brain(mgr, build_prompt=_prompt, images=[_IMG], max_tokens=64)

    assert reply.provider == "local-deep"
    assert mgr.brains["cloud-fast"].calls == 0


@pytest.mark.asyncio
async def test_screenshot_with_no_local_tier_reaches_no_model() -> None:
    cloud_only = {**POLICY, "deep": {"provider": "other-cloud", "model": "big"}}
    mgr = _FakeManager([("cloud-fast", "step"), ("other-cloud", "big")], cloud_only)

    with pytest.raises(CUNoVisionProviderError, match="stay on this device"):
        await call_vision_brain(mgr, build_prompt=_prompt, images=[_IMG], max_tokens=64)

    assert all(brain.calls == 0 for brain in mgr.brains.values())


@pytest.mark.asyncio
async def test_cloud_vision_consent_lets_the_cloud_tier_see_the_screen() -> None:
    mgr = _FakeManager([("cloud-fast", "step")], {**POLICY, "allow_cloud_vision": True})

    reply = await call_vision_brain(mgr, build_prompt=_prompt, images=[_IMG], max_tokens=64)

    assert reply.provider == "cloud-fast"


@pytest.mark.asyncio
async def test_without_a_policy_nothing_changes() -> None:
    mgr = _FakeManager([("cloud-fast", "step")], None)

    reply = await call_vision_brain(mgr, build_prompt=_prompt, images=[_IMG], max_tokens=64)

    assert reply.provider == "cloud-fast"


def test_last_resort_stays_inside_the_policy_and_its_consent() -> None:
    mgr = _FakeManager([], POLICY)
    names = [p for p, _m, _b in iter_last_resort_vision(mgr, already_tried=set())]
    assert names == ["local-deep"]

    consented = _FakeManager([], {**POLICY, "allow_cloud_vision": True})
    names = [p for p, _m, _b in iter_last_resort_vision(consented, already_tried=set())]
    assert names == ["cloud-fast", "local-deep"]
