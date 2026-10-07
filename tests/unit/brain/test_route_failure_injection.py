"""Failure injection through the configured ``[brain.route_policy]`` tiers.

Both tiers run the real ``LocalOpenAIBrain`` adapter, the real OpenAI SDK
client it builds (default retry policy and timeouts untouched) and the shared
``stream_complete`` parser. Only the wire is fake: an ``httpx.MockTransport``
plays the fast tier's loopback gateway (``127.0.0.1:11436``, a hosted free
model behind it) and the deep tier's llama.cpp server (``127.0.0.1:8080``,
declared ``local``). The SDK's retry sleep is replaced by a recorder, so no test
waits for a real ``Retry-After``.

Every case checks the same four promises:
* the number of HTTP attempts is bounded (no retry storm across turns either);
* no provider outside the two tiers is ever asked for tokens, the escalation
  delegate (Claude through Paperclip) is never reached without an explicit
  request, and nothing goes to a billed provider id;
* an action tool that started is never replayed on the other tier;
* the returned (spoken) text is honest: it never claims a success that did
  not happen.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

import httpx
import pytest

openai = pytest.importorskip("openai")
anyio = pytest.importorskip("anyio")

from jarvis.brain import manager as manager_mod  # noqa: E402
from jarvis.brain.manager import BrainManager  # noqa: E402
from jarvis.brain.provider_registry import BrainProviderRegistry  # noqa: E402
from jarvis.brain.route_policy import RouteFailure, recovery_message  # noqa: E402
from jarvis.core.bus import EventBus  # noqa: E402
from jarvis.core.config import BrainRoutePolicyConfig, JarvisConfig, SafetyConfig  # noqa: E402
from jarvis.core.events import BrainRouteSelected  # noqa: E402
from jarvis.core.protocols import ExecutionContext, ToolResult  # noqa: E402
from jarvis.plugins.brain.local_openai import LocalOpenAIBrain  # noqa: E402
from jarvis.safety.approval import ApprovalWorkflow  # noqa: E402
from jarvis.safety.risk_tier import RiskTierEvaluator  # noqa: E402
from jarvis.safety.tool_executor import ToolExecutor  # noqa: E402
from jarvis.screen_context.turn import TurnScreenContext  # noqa: E402
from jarvis.speech.pipeline import SpeechPipeline  # noqa: E402
from jarvis.ui.web.provider_spec import get_spec, provider_billing  # noqa: E402

FAST = ("step-gateway", "stepfun/step-3.7-flash:free")
DEEP = ("local-openai", "qwen3.6-35b-a3b")
ROOTS = {FAST[0]: "http://127.0.0.1:11436", DEEP[0]: "http://127.0.0.1:8080"}
PORTS = {11436: FAST[0], 8080: DEEP[0]}
TIER_PROVIDERS = frozenset(ROOTS)

POLICY = {
    "enabled": True,
    "fast": {"provider": FAST[0], "model": FAST[1]},
    "deep": {"provider": DEEP[0], "model": DEEP[1], "local": True},
    "escalation": {"enabled": True, "agent": "claude", "trigger_phrases": ["ask claude"]},
    "deny_providers": ["claude-api", "claude-cli"],
    "deny_model_prefixes": ["anthropic/"],
}

FAST_TURN = "good morning"  # classified "fast"
DEEP_TURN = "design the whole migration plan in depth"  # classified "deep"
ACTION_TURN = "send the note to Sam"  # classified "deep"

FAST_ANSWER = "Good morning from the fast tier."
DEEP_ANSWER = "Here is the plan from the deep tier."


# ── The wire ─────────────────────────────────────────────────────────────


def _chunk(
    content: str | None = None,
    *,
    finish: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
) -> bytes:
    delta: dict[str, Any] = {}
    if content is not None:
        delta["content"] = content
    if tool_calls is not None:
        delta["tool_calls"] = tool_calls
    body = {
        "id": "chatcmpl-test",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "served",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return b"data: " + json.dumps(body).encode() + b"\n\n"


_DONE = b"data: [DONE]\n\n"
_SSE = {"content-type": "text/event-stream"}


class _Stream(httpx.AsyncByteStream):
    """A response body produced chunk by chunk, so a test can stall or cut it."""

    def __init__(self, body: Callable[[], AsyncIterator[bytes]], server: _Server) -> None:
        self._body = body
        self._server = server

    async def __aiter__(self) -> AsyncIterator[bytes]:
        self._server.open_streams += 1
        try:
            async for part in self._body():
                yield part
        finally:
            self._server.open_streams -= 1

    async def aclose(self) -> None:
        return None


Behaviour = Callable[[httpx.Request, "_Server"], httpx.Response]


def answer(text: str) -> Behaviour:
    def respond(_req: httpx.Request, _srv: _Server) -> httpx.Response:
        words = text.split(" ")
        parts = [_chunk(w + (" " if i < len(words) - 1 else "")) for i, w in enumerate(words)]
        return httpx.Response(
            200, headers=_SSE, content=b"".join(parts) + _chunk(finish="stop") + _DONE
        )

    return respond


def rate_limited(retry_after: str) -> Behaviour:
    def respond(_req: httpx.Request, _srv: _Server) -> httpx.Response:
        return httpx.Response(
            429,
            headers={"retry-after": retry_after},
            json={"error": {"message": "Rate limit exceeded, slow down", "code": 429}},
        )

    return respond


def refused(req: httpx.Request, _srv: _Server) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=req)


def stalled(_req: httpx.Request, srv: _Server) -> httpx.Response:
    async def body() -> AsyncIterator[bytes]:
        await asyncio.Event().wait()  # headers sent, no chunk ever follows
        yield b""

    return httpx.Response(200, headers=_SSE, stream=_Stream(body, srv))


def drip(text: str, gap_s: float) -> Behaviour:
    def respond(_req: httpx.Request, srv: _Server) -> httpx.Response:
        async def body() -> AsyncIterator[bytes]:
            for word in text.split(" "):
                await asyncio.sleep(gap_s)
                yield _chunk(word + " ")
            yield _chunk(finish="stop") + _DONE

        return httpx.Response(200, headers=_SSE, stream=_Stream(body, srv))

    return respond


def empty_stream(_req: httpx.Request, _srv: _Server) -> httpx.Response:
    return httpx.Response(200, headers=_SSE, content=_DONE)


def garbage_stream(_req: httpx.Request, _srv: _Server) -> httpx.Response:
    return httpx.Response(200, headers=_SSE, content=b"data: {this is not json\n\n" + _DONE)


def unclosed_tool_call(_req: httpx.Request, _srv: _Server) -> httpx.Response:
    fragment = [
        {
            "index": 0,
            "id": "call_1",
            "type": "function",
            "function": {"name": "send_note", "arguments": '{"to": "Sa'},
        }
    ]
    return httpx.Response(200, headers=_SSE, content=_chunk(tool_calls=fragment) + _DONE)


def disconnect_after(text: str) -> Behaviour:
    def respond(_req: httpx.Request, srv: _Server) -> httpx.Response:
        async def body() -> AsyncIterator[bytes]:
            yield _chunk(text)
            raise httpx.RemoteProtocolError(
                "peer closed connection without sending complete message body"
            )

        return httpx.Response(200, headers=_SSE, stream=_Stream(body, srv))

    return respond


def tool_call_then_disconnect(_req: httpx.Request, srv: _Server) -> httpx.Response:
    """First model round asks for ``send_note``; every later round is cut."""
    if srv.requests == 1:
        call = [
            {
                "index": 0,
                "id": "call_1",
                "type": "function",
                "function": {"name": "send_note", "arguments": '{"to": "Sam"}'},
            }
        ]
        return httpx.Response(
            200,
            headers=_SSE,
            content=_chunk(tool_calls=call) + _chunk(finish="tool_calls") + _DONE,
        )
    return disconnect_after("Done, I sent")(_req, srv)


@dataclass
class _Server:
    provider: str
    script: list[Behaviour]
    requests: int = 0
    open_streams: int = 0

    def respond(self, req: httpx.Request) -> httpx.Response:
        self.requests += 1
        behaviour = self.script[min(self.requests, len(self.script)) - 1]
        return behaviour(req, self)


# ── The rest of the world: tripwires ─────────────────────────────────────


class _TripwireBrain:
    """Any provider outside the two tiers: being asked for tokens is the bug."""

    context_window = 8192
    supports_tools = True
    supports_vision = True

    def __init__(self, name: str, calls: list[str]) -> None:
        self.name = name
        self._calls = calls

    async def complete(self, _req: Any) -> AsyncIterator[Any]:
        self._calls.append(self.name)
        raise AssertionError(f"provider {self.name!r} outside the route policy was called")
        yield  # pragma: no cover


class _TierRegistry:
    """The manager's provider registry: both tier servers plus every real id.

    The real ids stay visible (billed APIs and Claude among them) so a
    regression that pulls one into a chain shows up as a tripwire call instead
    of disappearing because the provider was not registered.
    """

    def __init__(self, *, unregistered: frozenset[str] = frozenset()) -> None:
        self._real = BrainProviderRegistry().available()
        self._unregistered = unregistered
        self.instantiated: list[tuple[str, str | None]] = []
        self.tripwire_calls: list[str] = []

    def available(self) -> list[str]:
        return sorted((set(self._real) | TIER_PROVIDERS) - self._unregistered)

    def instantiate(self, name: str, **kwargs: Any) -> Any:
        self.instantiated.append((name, kwargs.get("model")))
        if name in TIER_PROVIDERS and name not in self._unregistered:
            brain = LocalOpenAIBrain(model=kwargs.get("model"))
            # The server root the provider card would store; the adapter
            # appends /v1 and builds its own SDK client from it.
            brain._server_root = ROOTS[name]
            brain._credential = None
            return brain
        return _TripwireBrain(name, self.tripwire_calls)


class _SendNote:
    """A consequential tool: every run is a real side effect."""

    name = "send_note"
    description = "Send a short note to a contact."
    risk_tier = "safe"
    is_action_tool = True
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {"to": {"type": "string"}},
        "required": ["to"],
    }

    def __init__(self) -> None:
        self.runs = 0

    async def execute(self, args: dict[str, Any], ctx: ExecutionContext) -> ToolResult:
        self.runs += 1
        return ToolResult(success=True, output=f"note sent to {args.get('to')}")


@dataclass
class _Rig:
    mgr: BrainManager
    servers: dict[str, _Server]
    registry: _TierRegistry
    sleeps: list[float]
    routes: list[BrainRouteSelected]
    delegate_calls: list[str]
    unknown_hosts: list[str] = field(default_factory=list)

    @property
    def total_requests(self) -> int:
        return sum(s.requests for s in self.servers.values())

    def max_attempts_per_call(self) -> int:
        """1 + the retry budget of the SDK client the adapter really built."""
        budgets = [
            getattr(b, "_client", None)
            for b in self.mgr._brain_cache.values()  # noqa: SLF001
        ]
        retries = [int(c.max_retries) for c in budgets if c is not None]
        assert retries, "no tier client was built"
        return 1 + max(retries)


def _rig(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fast: list[Behaviour],
    deep: list[Behaviour],
    unregistered: frozenset[str] = frozenset(),
    tool: _SendNote | None = None,
) -> _Rig:
    servers = {FAST[0]: _Server(FAST[0], fast), DEEP[0]: _Server(DEEP[0], deep)}
    unknown_hosts: list[str] = []

    def route(req: httpx.Request) -> httpx.Response:
        provider = PORTS.get(req.url.port or 0)
        if provider is None or req.url.host != "127.0.0.1":
            unknown_hosts.append(str(req.url))
            raise AssertionError(f"request left the two tier servers: {req.url}")
        return servers[provider].respond(req)

    transport = httpx.MockTransport(route)
    real_client = openai.AsyncOpenAI

    class _WiredAsyncOpenAI(real_client):  # type: ignore[misc, valid-type]
        """The SDK client exactly as the adapter builds it, on the fake wire."""

        def __init__(self, **kwargs: Any) -> None:
            kwargs["http_client"] = httpx.AsyncClient(transport=transport)
            super().__init__(**kwargs)

    monkeypatch.setattr(openai, "AsyncOpenAI", _WiredAsyncOpenAI)

    sleeps: list[float] = []

    async def _no_wait(seconds: float, *_a: Any, **_kw: Any) -> None:
        sleeps.append(float(seconds))
        await asyncio.sleep(0)

    monkeypatch.setattr(anyio, "sleep", _no_wait)

    cfg = JarvisConfig()
    cfg.brain.primary = DEEP[0]
    cfg.brain.route_policy = BrainRoutePolicyConfig.model_validate(POLICY)
    bus = EventBus()
    executor = None
    tools: dict[str, Any] = {}
    if tool is not None:
        tools = {tool.name: tool}
        executor = ToolExecutor(
            bus=bus,
            evaluator=RiskTierEvaluator(SafetyConfig()),
            approval=ApprovalWorkflow(bus),
        )
    mgr = BrainManager(config=cfg, bus=bus, tools=tools, tool_executor=executor)
    registry = _TierRegistry(unregistered=unregistered)
    mgr._registry = registry  # type: ignore[assignment]  # noqa: SLF001

    async def _no_screen(*_a: Any, **_kw: Any) -> TurnScreenContext:
        return TurnScreenContext(status="none")

    async def _no_images(**_kw: Any) -> tuple:
        return ()

    mgr._resolve_screen_context_turn = _no_screen  # type: ignore[method-assign]
    mgr._collect_vision_images = _no_images  # type: ignore[method-assign]
    mgr._resolve_turn_lang = lambda: "en"  # type: ignore[method-assign]

    routes: list[BrainRouteSelected] = []

    async def _record(event: BrainRouteSelected) -> None:
        routes.append(event)

    bus.subscribe(BrainRouteSelected, _record)

    delegate_calls: list[str] = []

    def _no_delegate(_cfg: Any, on_progress: Any = None) -> Any:
        delegate_calls.append("delegate_from_config")
        raise AssertionError("the escalation delegate was reached without an explicit request")

    monkeypatch.setattr(manager_mod, "delegate_from_config", _no_delegate)
    return _Rig(mgr, servers, registry, sleeps, routes, delegate_calls, unknown_hosts)


def _assert_contained(rig: _Rig) -> None:
    """No paid/Claude/off-policy provider, no delegate, no stray host."""
    assert rig.registry.tripwire_calls == []
    assert rig.delegate_calls == []
    assert rig.unknown_hosts == []
    asked = {name for name, _ in rig.registry.instantiated}
    off_policy = asked - TIER_PROVIDERS
    assert off_policy == set(), f"providers outside the tiers were built: {off_policy}"
    for name in asked:
        spec = get_spec(name)
        assert spec is None or provider_billing(spec) == "local", (
            f"{name} bills per token and is not a configured tier"
        )
    for event in rig.routes:
        for entry in event.chain:
            assert entry.split(":", 1)[0] in TIER_PROVIDERS


_SUCCESS_WORDS = ("done", "sent", "here is the plan", "good morning from")


def _assert_no_false_success(text: str) -> None:
    lowered = text.casefold()
    assert text.strip(), "a failed turn must still say something"
    assert not any(w in lowered for w in _SUCCESS_WORDS), text


# ── 1. Fast tier rate-limited ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fast_tier_429_honours_retry_after_then_falls_back_to_deep(monkeypatch) -> None:
    rig = _rig(monkeypatch, fast=[rate_limited("2")], deep=[answer(DEEP_ANSWER)])

    reply = await rig.mgr.generate(FAST_TURN, trace_id=uuid4(), use_history=False)

    assert reply.strip() == DEEP_ANSWER
    budget = rig.max_attempts_per_call()
    assert rig.servers[FAST[0]].requests == budget, "the SDK retry budget, no more"
    assert rig.servers[DEEP[0]].requests == 1
    # Retry-After is honoured, and only through the recorder: nothing slept.
    assert rig.sleeps == [2.0] * (budget - 1)
    assert [e.reason for e in rig.routes] == ["intent:fast"]
    _assert_contained(rig)

    # The next turn inside the cooldown does not hammer the limited tier again.
    second = await rig.mgr.generate(FAST_TURN, trace_id=uuid4(), use_history=False)
    assert second.strip() == DEEP_ANSWER
    assert rig.servers[FAST[0]].requests == budget
    _assert_contained(rig)


@pytest.mark.asyncio
async def test_unbounded_retry_after_is_capped_not_obeyed(monkeypatch) -> None:
    """An hour-long Retry-After never turns into an hour-long voice turn."""
    rig = _rig(monkeypatch, fast=[rate_limited("3600")], deep=[answer(DEEP_ANSWER)])

    reply = await rig.mgr.generate(FAST_TURN, trace_id=uuid4(), use_history=False)

    assert reply.strip() == DEEP_ANSWER
    assert rig.servers[FAST[0]].requests <= rig.max_attempts_per_call()
    # The SDK either stops retrying or falls back to its own bounded backoff.
    assert all(s <= 8.0 for s in rig.sleeps), rig.sleeps
    _assert_contained(rig)


@pytest.mark.asyncio
async def test_retry_after_wait_fits_inside_the_voice_stall_window(monkeypatch) -> None:
    rig = _rig(monkeypatch, fast=[rate_limited("45")], deep=[answer(DEEP_ANSWER)])

    reply = await rig.mgr.generate(FAST_TURN, trace_id=uuid4(), use_history=False)

    assert reply.strip() == DEEP_ANSWER
    # The voice pipeline's default no-progress window (brain_timeout_s).
    pipeline_defaults = SpeechPipeline.__init__.__kwdefaults__ or {}
    window = float(pipeline_defaults.get("brain_timeout_s", 30.0))
    assert sum(rig.sleeps) < window, f"slept {sum(rig.sleeps)} s before the fallback"


# ── 2. Stalled, empty and malformed streams ──────────────────────────────


def _guarded_pipeline(stall_s: float) -> SpeechPipeline:
    """The voice pipeline's real stall watchdog on a bare instance."""
    p = SpeechPipeline.__new__(SpeechPipeline)
    p._brain_timeout_s = stall_s
    p._brain_hard_timeout_s = 30.0
    p._brain_stall_poll_s = 0.02
    p._brain_last_progress = time.monotonic()
    return p


async def _voice_turn(rig: _Rig, p: SpeechPipeline, text: str) -> tuple[str, bool]:
    spoken: list[str] = []
    reply = await rig.mgr.generate(
        text,
        trace_id=uuid4(),
        use_history=False,
        text_consumer=spoken.append,
        on_progress=p._mark_brain_progress,
    )
    return reply, False


@pytest.mark.asyncio
async def test_stalled_stream_trips_the_stall_watchdog_and_closes_the_stream(monkeypatch) -> None:
    rig = _rig(monkeypatch, fast=[stalled], deep=[answer(DEEP_ANSWER)])
    p = _guarded_pipeline(stall_s=0.3)

    started = time.monotonic()
    with pytest.raises(TimeoutError):
        await p._run_brain_with_stall_guard(_voice_turn(rig, p, FAST_TURN))
    await asyncio.sleep(0)

    assert time.monotonic() - started < 5.0, "the watchdog bounded the turn"
    assert rig.servers[FAST[0]].requests == 1, "a stalled stream is not re-requested"
    assert rig.servers[FAST[0]].open_streams == 0, "the stalled stream was closed"
    assert rig.servers[DEEP[0]].requests == 0
    _assert_contained(rig)


@pytest.mark.asyncio
async def test_stall_watchdog_resets_per_streamed_chunk(monkeypatch) -> None:
    """AP-19: a slow stream whose every gap is under the window completes."""
    rig = _rig(
        monkeypatch,
        fast=[drip("one two three four five six seven", 0.1)],
        deep=[answer(DEEP_ANSWER)],
    )
    p = _guarded_pipeline(stall_s=0.3)

    reply, _ = await p._run_brain_with_stall_guard(_voice_turn(rig, p, FAST_TURN))

    assert reply.split() == ["one", "two", "three", "four", "five", "six", "seven"]
    assert rig.total_requests == 1
    _assert_contained(rig)


@pytest.mark.parametrize(
    "broken",
    [empty_stream, garbage_stream, unclosed_tool_call],
    ids=["empty-stream", "garbage-sse", "unclosed-tool-call"],
)
@pytest.mark.asyncio
async def test_broken_fast_stream_falls_back_once_to_deep(monkeypatch, broken) -> None:
    tool = _SendNote()
    rig = _rig(monkeypatch, fast=[broken], deep=[answer(DEEP_ANSWER)], tool=tool)

    reply = await rig.mgr.generate(FAST_TURN, trace_id=uuid4(), use_history=False)

    assert reply.strip() == DEEP_ANSWER
    assert tool.runs == 0, "a tool call fragment that never closed must not run"
    assert rig.servers[FAST[0]].requests == 1
    assert rig.servers[DEEP[0]].requests == 1
    _assert_contained(rig)


@pytest.mark.asyncio
async def test_broken_streams_on_both_tiers_end_in_an_honest_failure(monkeypatch) -> None:
    rig = _rig(monkeypatch, fast=[garbage_stream], deep=[empty_stream])

    reply = await rig.mgr.generate(FAST_TURN, trace_id=uuid4(), use_history=False)

    _assert_no_false_success(reply)
    assert rig.mgr._last_turn_all_failed is True
    assert rig.total_requests == 2
    _assert_contained(rig)


# ── 3. Mid-stream disconnect ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mid_stream_disconnect_falls_back_and_returns_the_complete_answer(
    monkeypatch,
) -> None:
    rig = _rig(
        monkeypatch, fast=[disconnect_after("Good morning, the")], deep=[answer(DEEP_ANSWER)]
    )

    reply = await rig.mgr.generate(FAST_TURN, trace_id=uuid4(), use_history=False)

    assert reply.strip() == DEEP_ANSWER, "the cut fragment is not part of the answer"
    assert rig.servers[FAST[0]].requests == 1, "a cut stream is not re-requested"
    assert rig.servers[DEEP[0]].requests == 1
    _assert_contained(rig)


@pytest.mark.asyncio
async def test_spoken_stream_does_not_glue_a_cut_fragment_to_the_fallback(monkeypatch) -> None:
    fragment = "Good morning, the"
    rig = _rig(monkeypatch, fast=[disconnect_after(fragment)], deep=[answer(DEEP_ANSWER)])

    heard = "".join(
        [
            chunk
            async for chunk in rig.mgr.generate_stream(
                FAST_TURN, trace_id=uuid4(), use_history=False
            )
        ]
    )

    assert heard.endswith(DEEP_ANSWER)
    before = heard[: -len(DEEP_ANSWER)].strip()
    # Either the fragment was withheld, or something was said between the
    # broken-off fragment and the new answer.
    assert before == "" or len(before) > len(fragment), heard
    _assert_contained(rig)


@pytest.mark.asyncio
async def test_disconnect_after_an_action_started_is_not_replayed(monkeypatch) -> None:
    tool = _SendNote()
    rig = _rig(monkeypatch, fast=[answer(FAST_ANSWER)], deep=[tool_call_then_disconnect], tool=tool)

    reply = await rig.mgr.generate(ACTION_TURN, trace_id=uuid4(), use_history=False)

    assert [e.tier for e in rig.routes] == ["deep"]
    assert tool.runs == 1, "the action ran once and was not repeated"
    assert rig.servers[FAST[0]].requests == 0, "the turn was not replayed on the other tier"
    assert reply == manager_mod._UNKNOWN_OUTCOME_PHRASES["en"]
    # Round one asked for the tool; round two was cut and not re-requested.
    assert rig.servers[DEEP[0]].requests == 2
    _assert_contained(rig)


# ── 4. Deep tier (local Qwen) unavailable ────────────────────────────────


@pytest.mark.asyncio
async def test_unregistered_deep_tier_is_excluded_and_the_turn_uses_fast(monkeypatch) -> None:
    rig = _rig(
        monkeypatch,
        fast=[answer(FAST_ANSWER)],
        deep=[answer(DEEP_ANSWER)],
        unregistered=frozenset({DEEP[0]}),
    )

    reply = await rig.mgr.generate(DEEP_TURN, trace_id=uuid4(), use_history=False)

    assert reply.strip() == FAST_ANSWER
    [event] = rig.routes
    assert event.intent_level == "deep"
    assert event.tier == "fast"
    assert event.reason == "intent:deep;fallback-from:deep"
    assert f"deep:{DEEP[0]}:unavailable" in event.excluded
    assert event.chain == (f"{FAST[0]}:{FAST[1]}",)
    assert rig.servers[DEEP[0]].requests == 0
    assert rig.servers[FAST[0]].requests == 1
    _assert_contained(rig)


@pytest.mark.asyncio
async def test_deep_server_down_at_runtime_falls_back_to_fast(monkeypatch) -> None:
    rig = _rig(monkeypatch, fast=[answer(FAST_ANSWER)], deep=[refused])

    reply = await rig.mgr.generate(DEEP_TURN, trace_id=uuid4(), use_history=False)

    assert reply.strip() == FAST_ANSWER
    [event] = rig.routes
    assert event.tier == "deep"
    assert event.chain == (f"{DEEP[0]}:{DEEP[1]}", f"{FAST[0]}:{FAST[1]}")
    assert rig.servers[DEEP[0]].requests <= rig.max_attempts_per_call()
    assert rig.servers[FAST[0]].requests == 1
    assert all(s <= 8.0 for s in rig.sleeps)
    _assert_contained(rig)


# ── 5. Fast tier (Step gateway) unavailable ──────────────────────────────


@pytest.mark.asyncio
async def test_fast_gateway_down_falls_back_to_deep(monkeypatch) -> None:
    rig = _rig(monkeypatch, fast=[refused], deep=[answer(DEEP_ANSWER)])

    reply = await rig.mgr.generate(FAST_TURN, trace_id=uuid4(), use_history=False)

    assert reply.strip() == DEEP_ANSWER
    assert rig.servers[FAST[0]].requests <= rig.max_attempts_per_call()
    assert rig.servers[DEEP[0]].requests == 1
    _assert_contained(rig)


@pytest.mark.asyncio
async def test_unregistered_fast_tier_is_excluded_with_a_reason(monkeypatch) -> None:
    rig = _rig(
        monkeypatch,
        fast=[answer(FAST_ANSWER)],
        deep=[answer(DEEP_ANSWER)],
        unregistered=frozenset({FAST[0]}),
    )

    reply = await rig.mgr.generate(FAST_TURN, trace_id=uuid4(), use_history=False)

    assert reply.strip() == DEEP_ANSWER
    [event] = rig.routes
    assert event.reason == "intent:fast;fallback-from:fast"
    assert f"fast:{FAST[0]}:unavailable" in event.excluded
    assert rig.servers[FAST[0]].requests == 0
    _assert_contained(rig)


# ── 6. Both tiers unavailable ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_both_tiers_down_is_an_honest_failure_without_escalation(monkeypatch) -> None:
    rig = _rig(monkeypatch, fast=[refused], deep=[refused])
    per_call = None

    replies = []
    for _ in range(3):
        replies.append(await rig.mgr.generate(DEEP_TURN, trace_id=uuid4(), use_history=False))
        per_call = per_call or rig.max_attempts_per_call()

    for reply in replies:
        _assert_no_false_success(reply)
        assert "reach" in reply.casefold()  # names the real cause: unreachable
    assert rig.mgr._last_turn_all_failed is True
    # Each turn tries each tier once (with the SDK's bounded retries), never more.
    assert rig.total_requests <= 3 * 2 * per_call
    assert all(s <= 8.0 for s in rig.sleeps)
    _assert_contained(rig)


@pytest.mark.asyncio
async def test_both_tiers_rate_limited_backs_off_instead_of_storming(monkeypatch) -> None:
    rig = _rig(monkeypatch, fast=[rate_limited("1")], deep=[rate_limited("1")])

    first = await rig.mgr.generate(FAST_TURN, trace_id=uuid4(), use_history=False)
    after_first = rig.total_requests
    second = await rig.mgr.generate(FAST_TURN, trace_id=uuid4(), use_history=False)

    for reply in (first, second):
        _assert_no_false_success(reply)
        assert "too many requests" in reply.casefold()
    assert after_first <= 2 * rig.max_attempts_per_call()
    assert rig.total_requests == after_first, "both tiers cool down; turn two sends nothing"
    _assert_contained(rig)


@pytest.mark.asyncio
async def test_both_tiers_unregistered_says_so_and_calls_nothing(monkeypatch) -> None:
    rig = _rig(
        monkeypatch,
        fast=[answer(FAST_ANSWER)],
        deep=[answer(DEEP_ANSWER)],
        unregistered=TIER_PROVIDERS,
    )

    reply = await rig.mgr.generate(FAST_TURN, trace_id=uuid4(), use_history=False)

    _assert_no_false_success(reply)
    [event] = rig.routes
    assert event.tier == "none"
    assert event.reason == "no-eligible-target"
    assert event.chain == ()
    assert rig.total_requests == 0
    _assert_contained(rig)


# ── 7. Escalation delegate timeout (manager level) ───────────────────────


@pytest.mark.asyncio
async def test_explicit_escalation_timeout_is_typed_and_touches_no_tier(monkeypatch) -> None:
    from jarvis.brain.paperclip_delegation import PaperclipDelegate

    rig = _rig(monkeypatch, fast=[answer(FAST_ANSWER)], deep=[answer(DEEP_ANSWER)])
    paperclip_calls: list[tuple[str, str]] = []
    now = [0.0]

    class _SlowPaperclip:
        async def request(self, method: str, path: str, json: dict | None = None):
            paperclip_calls.append((method, path))
            if path == "/api/companies":
                return 200, [{"id": "c-1"}]
            if path.endswith("/agents"):
                return 200, [{"id": "a-claude", "name": "claude"}]
            if method == "POST":
                return 201, {"id": "i-1", "identifier": "ROB-1", "status": "todo"}
            return 200, {"id": "i-1", "status": "in_progress"}

    async def _tick(seconds: float) -> None:
        now[0] += seconds

    def _delegate(_cfg: Any, on_progress: Any = None) -> PaperclipDelegate:
        return PaperclipDelegate(
            _SlowPaperclip(),
            agent_name="claude",
            deadline_s=10.0,
            poll_interval_s=2.0,
            max_context_chars=200,
            clock=lambda: now[0],
            sleep=_tick,
        )

    monkeypatch.setattr(manager_mod, "delegate_from_config", _delegate)

    reply = await rig.mgr.generate(
        "please ask claude to review this", trace_id=uuid4(), use_history=False
    )

    assert reply == recovery_message(RouteFailure.TIMEOUT, "en")
    assert [e.outcome for e in rig.routes] == ["timeout"]
    assert sum(1 for m, _ in paperclip_calls if m == "POST") == 1
    polls = sum(1 for m, p in paperclip_calls if m == "GET" and p == "/api/issues/i-1")
    assert polls <= 10.0 / 2.0 + 1
    assert ("PATCH", "/api/issues/i-1") in paperclip_calls
    assert rig.total_requests == 0, "a delegated turn never runs a model tier"
    assert rig.registry.tripwire_calls == []
