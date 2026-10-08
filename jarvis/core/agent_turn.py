"""Turn-local observation channel for agents that execute their own tools.

This carries evidence to the app bus, never permission to execute a tool.
The context follows a turn across awaits without mixing concurrent chats.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass
from uuid import UUID

from jarvis.core.events import AgentToolActivity


class AgentPolicyError(RuntimeError):
    """A turn cannot safely run; do not retry or label it a provider outage."""


@dataclass(frozen=True, slots=True)
class AgentTurnContext:
    trace_id: UUID
    publish: Callable[[AgentToolActivity], Awaitable[None]] | None = None


current_agent_turn: ContextVar[AgentTurnContext | None] = ContextVar(
    "current_agent_turn", default=None,
)
