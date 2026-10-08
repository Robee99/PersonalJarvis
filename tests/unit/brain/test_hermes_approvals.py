"""Native permission cards and speech resolve one exact, agent-owned request."""

from __future__ import annotations

import asyncio
import json

import pytest

from jarvis.agent_chat import runner_brain
from jarvis.agent_chat.service import AgentChatService
from jarvis.agent_chat.store import AgentChatStore
from jarvis.agent_chat.voice_mirror import VoiceChatMirror
from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainRoutePolicyConfig, load_config
from jarvis.plugins.brain import hermes
from tests.fakes.fake_hermes_api import FakeHermesApi, asks_approval


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(hermes, "_SESSIONS", {})
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(hermes, "configured_base_url", lambda: "http://127.0.0.1:8642")
    monkeypatch.setattr(hermes, "thinking_off_by_config", lambda: False)
    monkeypatch.setattr(hermes, "phrase_language", lambda: "en")
    monkeypatch.setattr("jarvis.core.config.get_secret_any", lambda _: "fixture-key")
    cfg = load_config()
    cfg.brain.primary, cfg.brain.reply_language = "hermes", "en"
    cfg.brain.route_policy = BrainRoutePolicyConfig.model_validate(
        {"enabled": True, "fast": {"provider": "hermes"}}
    )
    manager = BrainManager.from_tier_config("router", cfg, EventBus(), provider_override="hermes")
    server = FakeHermesApi()
    original = hermes.HermesBrain.__init__

    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.transport = server.transport

    monkeypatch.setattr(hermes.HermesBrain, "__init__", init)
    monkeypatch.setattr(runner_brain, "brain_manager", lambda: manager)
    monkeypatch.setattr(runner_brain, "_agent_secret", lambda *_: None)
    svc = AgentChatService(AgentChatStore(":memory:"), bus=lambda: manager._bus)
    session = svc.create_session(
        provider="hermes", permission_mode="ask", surface="jarvis", cwd=str(tmp_path)
    )
    svc.bind_voice_chat(session.session_id)
    VoiceChatMirror(lambda: svc).attach(manager._bus)
    return manager, server, svc, session.session_id


def card(svc, sid):
    return [e["payload"] for e in svc.store.list_events(sid) if e["kind"] == "approval_required"][
        -1
    ]


async def wait_continuations():
    tasks = set().union(*(s.continuations for s in hermes._SESSIONS.values()))
    if tasks:
        await asyncio.wait_for(asyncio.gather(*tasks), 5)


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["voice", "chat"])
@pytest.mark.parametrize("decision", ["allow", "deny"])
async def test_native_card_and_spoken_answer_share_exact_request(setup, origin, decision):
    manager, server, svc, sid = setup
    server.scripts.append(asks_approval("Fixture action finished.", "Fixture left unchanged."))
    if origin == "voice":
        await manager.generate("delete the fixture", use_history=False)
    else:
        await svc.send(sid, "delete the fixture")
        await svc.wait_turn(sid)
    pending = card(svc, sid)
    aid = pending["approval_id"]
    assert pending["external"] and pending["decisions"] == ["allow", "deny"]
    assert svc.pending_approvals(sid) == [aid]
    assert manager.has_pending_voice_confirm()
    if origin == "voice":
        assert await svc.resolve_native_approval(sid, aid, decision)
        assert not await svc.resolve_native_approval(sid, aid, decision)
        await wait_continuations()
    else:
        await manager.generate("yes" if decision == "allow" else "no", use_history=False)
        assert not await svc.resolve_native_approval(sid, aid, "allow")
    assert server.approvals == [
        {"choice": "once" if decision == "allow" else "deny", "request_id": "req-7"}
    ]
    assert not manager.has_pending_voice_confirm()
    assert svc.pending_approvals(sid) == []
    events = svc.store.list_events(sid)
    assert any(
        e["kind"] == "approval_resolved" and e["payload"]["decision"] == decision for e in events
    )
    assert bool(
        [e for e in events if e["kind"] == "tool_result" and not e["payload"]["is_error"]]
    ) == (decision == "allow")


@pytest.mark.asyncio
async def test_stop_retires_card_and_stale_click_cannot_approve_new_request(setup):
    manager, server, svc, sid = setup
    server.scripts.extend(
        [asks_approval("done", "denied"), asks_approval("new done", "new denied")]
    )
    await manager.generate("delete the fixture", use_history=False)
    old = card(svc, sid)["approval_id"]
    await svc.controls.pause(sid, "Stopped by the user")
    assert server.runs[0].stopped
    assert any(
        e["kind"] == "approval_resolved" and e["payload"]["decision"] == "cancel"
        for e in svc.store.list_events(sid)
    )
    await manager.generate("delete another fixture", use_history=False)
    new = card(svc, sid)["approval_id"]
    assert new != old
    assert not await svc.resolve_native_approval(sid, old, "allow")
    assert not await svc.resolve_native_approval(sid, new, "allow_always")
    assert server.runs[1].approvals == []
    await manager.generate("no", use_history=False)


@pytest.mark.asyncio
async def test_native_card_redacts_commands_and_refuses_other_chat(setup):
    manager, server, svc, sid = setup
    server.scripts.append(
        asks_approval(
            "done", "denied", description="remove fixture api_key=super-private-fixture-key"
        )
    )
    reply = await manager.generate("delete the fixture", use_history=False)
    pending = card(svc, sid)
    assert "super-private-fixture-key" not in json.dumps(pending) + reply
    other = svc.create_session(provider="hermes", surface="jarvis")
    assert not await svc.resolve_native_approval(other.session_id, pending["approval_id"], "allow")
    assert server.approvals == []
    await manager.generate("no", use_history=False)


@pytest.mark.asyncio
async def test_reopened_card_without_native_authority_expires(setup):
    manager, server, svc, sid = setup
    svc.store.append_event(
        sid,
        {
            "kind": "approval_required",
            "payload": {
                "turn_id": "old-turn",
                "approval_id": "native-old",
                "external": True,
                "call_id": "old-call",
                "name": "hermes:terminal",
                "summary": "Old permission",
            },
        },
    )
    await svc._expire_native_cards(sid)
    assert svc.store.list_events(sid)[-1]["payload"]["decision"] == "expired"
    assert not await svc.resolve_native_approval(sid, "native-old", "allow")
    assert server.approvals == []


@pytest.mark.asyncio
async def test_unavailable_native_resolver_preserves_request_for_retry(setup):
    manager, server, svc, sid = setup
    server.scripts.append(asks_approval("done", "denied"))
    await manager.generate("delete the fixture", use_history=False)
    aid = card(svc, sid)["approval_id"]
    server.approval_failure_status = 503
    with pytest.raises(RuntimeError, match="Try again or choose Stop") as failure:
        await svc.resolve_native_approval(sid, aid, "allow")
    assert "private-provider-fixture" not in str(failure.value)
    assert svc.pending_approvals(sid) == [aid]
    assert server.approvals == []
    server.approval_failure_status = None
    assert await svc.resolve_native_approval(sid, aid, "deny")
    await wait_continuations()
    assert server.approvals == [{"choice": "deny", "request_id": "req-7"}]


@pytest.mark.asyncio
async def test_native_expiry_retires_card_without_granting(setup):
    manager, server, svc, sid = setup
    server.scripts.append(asks_approval("done", "denied"))
    await manager.generate("delete the fixture", use_history=False)
    aid = card(svc, sid)["approval_id"]
    server.approval_failure_status = 409
    assert not await svc.resolve_native_approval(sid, aid, "allow")
    assert svc.pending_approvals(sid) == []
    assert not manager.has_pending_voice_confirm()
    assert server.runs[0].stopped
    assert server.approvals == []
    assert any(
        e["kind"] == "approval_resolved" and e["payload"]["decision"] == "expired"
        for e in svc.store.list_events(sid)
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("origin", ["chat", "voice"])
async def test_second_approval_keeps_original_turn_when_visible_chat_changes(setup, origin):
    manager, server, svc, sid = setup

    async def twice(_server, run):
        for request_id in ("first", "second"):
            run.decided.clear()
            yield {
                "event": "approval.request",
                "request_id": request_id,
                "description": "Use harmless fixture",
                "choices": ["once", "deny"],
            }
            await run.decided.wait()
        yield {"event": "run.completed", "output": "Fixture unchanged."}

    server.scripts.append(twice)
    if origin == "chat":
        await svc.send(sid, "use the fixture")
        await svc.wait_turn(sid)
    else:
        await manager.generate("use the fixture", use_history=False)
    first = card(svc, sid)
    other = svc.create_session(provider="hermes", surface="jarvis")
    svc.bind_voice_chat(other.session_id)
    assert await svc.resolve_native_approval(sid, first["approval_id"], "allow")
    await wait_continuations()
    second = card(svc, sid)
    assert second["approval_id"] != first["approval_id"]
    assert second["turn_id"] == first["turn_id"]
    assert not [
        e for e in svc.store.list_events(other.session_id) if e["kind"] == "approval_required"
    ]
    assert (
        len(
            [
                e
                for e in svc.store.list_events(sid)
                if e["kind"] == "turn_started" and e["payload"]["turn_id"] == first["turn_id"]
            ]
        )
        == 1
    )
    assert not await svc.resolve_native_approval(sid, first["approval_id"], "allow")
    assert await svc.resolve_native_approval(sid, second["approval_id"], "deny")
    await wait_continuations()
    assert server.approvals == [
        {"choice": "once", "request_id": "first"},
        {"choice": "deny", "request_id": "second"},
    ]
