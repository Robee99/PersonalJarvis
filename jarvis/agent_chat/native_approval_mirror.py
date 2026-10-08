"""Project native approvals into the existing chat log; the agent owns grants.

No Future or second confirmation state lives here. Cards carry exact native
request identities and route clicks to the manager's capability resolver.
Spoken answers and Stop resolve the same request and publish its decision.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

from jarvis.agent_chat.events import make_event
from jarvis.core.events import (
    AgentApprovalReply,
    AgentApprovalRequested,
    AgentApprovalResolved,
    AgentToolActivity,
)


@dataclass(frozen=True)
class _Target:
    session_id: str
    turn_id: str
    run_id: str
    trace_id: UUID


class NativeApprovalMirror:
    def __init__(self, get_service: Callable[[], Any | None]) -> None:
        self._get_service = get_service
        self._targets: dict[str, _Target] = {}

    def attach(self, bus: Any) -> None:
        bus.subscribe(AgentApprovalRequested, self._requested)
        bus.subscribe(AgentApprovalResolved, self._resolved)
        bus.subscribe(AgentApprovalReply, self._reply)
        bus.subscribe(AgentToolActivity, self._tool)

    async def _requested(self, event: AgentApprovalRequested) -> None:
        from jarvis.agent_chat.voice_mirror import VoiceChatMirror

        svc = self._get_service()
        if svc is None:
            return
        svc._native_approval_mirror = self
        previous = next(
            (
                target
                for target in reversed(list(self._targets.values()))
                if target.run_id == event.run_id and target.trace_id == event.trace_id
            ),
            None,
        )
        origin = event.chat_session_id or (previous.session_id if previous is not None else "")
        session = svc.store.get_session(origin) if origin else None
        if origin and session is None:
            return  # Never reroute a deleted origin into another person's chat.
        if session is None:
            session = VoiceChatMirror._target_session(svc)
        if session is None:
            session = VoiceChatMirror._ensure_session(
                svc, SimpleNamespace(provider="hermes", model="hermes-agent")
            )
            if session is None:
                return
            svc.bind_voice_chat(session.session_id)
        turn_id = event.chat_turn_id or "native-" + event.run_id
        if not event.chat_turn_id and not any(
            e["kind"] == "turn_started" and e["payload"].get("turn_id") == turn_id
            for e in svc.store.list_events(session.session_id)
        ):
            await svc._emit(
                session.session_id,
                make_event(
                    "turn_started",
                    {
                        "turn_id": turn_id,
                        "provider": "hermes",
                        "runner": "voice",
                        "model": session.model,
                    },
                ),
            )
        self._targets[event.approval_id] = _Target(
            session.session_id,
            turn_id,
            event.run_id,
            event.trace_id,
        )
        # Bounded evidence routes, not approval authority. A stale card still
        # cannot grant because the native adapter checks its pending exact id.
        while len(self._targets) > 128:
            self._targets.pop(next(iter(self._targets)))
        await svc._emit(
            session.session_id,
            make_event(
                "approval_required",
                {
                    "turn_id": turn_id,
                    "approval_id": event.approval_id,
                    "call_id": event.call_id,
                    "name": event.tool_name,
                    "input": {"command": event.command} if event.command else {},
                    "summary": event.description,
                    "decisions": ["allow", "deny"],
                    "external": True,
                    "scope": "once",
                },
            ),
        )
        if not event.chat_turn_id:
            await svc._emit(
                session.session_id,
                make_event(
                    "turn_finished",
                    {
                        "turn_id": turn_id,
                        "status": "done",
                        "duration_ms": 0,
                        "usage": {},
                    },
                ),
            )

    async def _resolved(self, event: AgentApprovalResolved) -> None:
        target, svc = self._targets.get(event.approval_id), self._get_service()
        if target is None or svc is None or target.trace_id != event.trace_id:
            return
        await svc._emit(
            target.session_id,
            make_event(
                "approval_resolved",
                {
                    "turn_id": target.turn_id,
                    "approval_id": event.approval_id,
                    "decision": event.decision,
                },
            ),
        )

    async def _tool(self, event: AgentToolActivity) -> None:
        target = next(
            (
                t
                for t in reversed(list(self._targets.values()))
                if t.run_id == event.run_id and t.trace_id == event.trace_id
            ),
            None,
        )
        svc = self._get_service()
        if target is None or svc is None:
            return
        history = svc.store.list_events(target.session_id)
        started = any(
            e["kind"] == "tool_call" and e["payload"].get("call_id") == event.call_id
            for e in history
        )
        if not started:
            await svc._emit(
                target.session_id,
                make_event(
                    "tool_call",
                    {
                        "turn_id": target.turn_id,
                        "call_id": event.call_id,
                        "name": event.tool_name,
                        "input": {"preview": event.preview} if event.state == "started" else {},
                    },
                ),
            )
        if event.state != "started" and not any(
            e["kind"] == "tool_result" and e["payload"].get("call_id") == event.call_id
            for e in history
        ):
            await svc._emit(
                target.session_id,
                make_event(
                    "tool_result",
                    {
                        "turn_id": target.turn_id,
                        "call_id": event.call_id,
                        "output": event.preview,
                        "is_error": event.state != "completed",
                        "duration_ms": event.duration_ms,
                    },
                ),
            )

    async def _reply(self, event: AgentApprovalReply) -> None:
        target, svc = self._targets.get(event.approval_id), self._get_service()
        if target is None or svc is None or target.trace_id != event.trace_id:
            return
        if event.text:
            await svc._emit(
                target.session_id,
                make_event(
                    "assistant_text",
                    {
                        "turn_id": target.turn_id,
                        "message_id": uuid4().hex,
                        "text": event.text,
                    },
                ),
            )
        await svc._emit(
            target.session_id,
            make_event(
                "turn_finished",
                {
                    "turn_id": target.turn_id,
                    "status": "error" if event.is_error else "done",
                    "error": "Native continuation did not complete." if event.is_error else None,
                    "duration_ms": 0,
                    "usage": {},
                },
            ),
        )
        self._targets.pop(event.approval_id, None)

    def pending(self, session_id: str) -> list[str]:
        from jarvis.agent_chat.runner_brain import brain_manager

        manager = brain_manager()
        ids = getattr(manager, "pending_agent_approval_ids", lambda: set())()
        return [
            aid
            for aid, target in self._targets.items()
            if target.session_id == session_id and aid in ids
        ]

    async def resolve(
        self,
        svc: Any,
        session_id: str,
        approval_id: str,
        decision: str,
    ) -> bool:
        if svc is not self._get_service():
            return False
        target = self._targets.get(approval_id)
        if target is None or target.session_id != session_id or decision not in {"allow", "deny"}:
            return False
        from jarvis.agent_chat.runner_brain import brain_manager

        manager = brain_manager()
        resolve = getattr(manager, "resolve_agent_approval", None)
        return bool(callable(resolve) and await resolve(approval_id, decision))
