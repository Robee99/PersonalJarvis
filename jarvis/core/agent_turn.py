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

    reason = "read_only_unavailable"


def policy_response(language: str) -> str:
    """An intentional refusal, expressed in the already-resolved turn language."""
    phrases = {
        "en": (
            "I can't safely run the agent in read-only mode with this gateway, so I "
            "did not start a new run or perform any action."
        ),
        "de": (
            "Mit diesem Gateway kann ich den Agenten nicht sicher im Nur-Lesen- "  # i18n-allow
            "Modus ausführen. Ich habe keinen neuen Lauf gestartet und keine Aktion "  # i18n-allow
            "ausgeführt."  # i18n-allow
        ),
        "es": (
            "No puedo ejecutar el agente de forma segura en modo de solo lectura "
            "con este gateway, así que no inicié una nueva ejecución ni realicé "
            "ninguna acción."
        ),
    }
    return phrases.get(language, phrases["en"])


@dataclass(frozen=True, slots=True)
class AgentTurnContext:
    trace_id: UUID
    publish: Callable[[AgentToolActivity], Awaitable[None]] | None = None


current_agent_turn: ContextVar[AgentTurnContext | None] = ContextVar(
    "current_agent_turn",
    default=None,
)
