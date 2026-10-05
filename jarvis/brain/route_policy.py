"""Configured fast/deep/escalation routing, decided inside the Brain.

``[brain.route_policy]`` lets an install name the provider/model it wants for
simple work (``fast``), for multi-step reasoning and building (``deep``), and
whether a bounded escalation may hand the task to a delegate (``escalation``,
for example a Claude agent in Paperclip). The names live in configuration only:
this module never compares against a provider or model id (AP-21). It decides
from the turn's signals, the configured targets, what is available right now,
and the capabilities each target declares.

``decide_route`` is a pure function so every rule is pinned by deterministic
tests. ``BrainManager._build_fallback_chain`` calls it when the policy is
enabled and publishes the result's machine-readable ``reason`` per turn.
"""
from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# The only environment switch that lets ``test_override_tier`` take effect.
# Production installs never set it, so a stray config value cannot pin routing.
TEST_MODE_ENV = "JARVIS_ROUTE_POLICY_TEST_MODE"

TIER_FAST = "fast"
TIER_DEEP = "deep"
TIER_ESCALATION = "escalation"
_DEEP_LEVELS = frozenset({"deep", "code"})


class RouteFailure(StrEnum):
    """Typed failure categories for a routed turn (spec Phase 1, step 5)."""

    UNAVAILABLE = "unavailable"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    INVALID_MODEL_OUTPUT = "invalid-model-output"
    POLICY_DENIED = "policy-denied"
    TOOL_DENIED = "tool-denied"
    DELEGATION_FAILED = "delegation-failed"
    CAPABILITY_UNSUPPORTED = "capability-unsupported"


# User-safe wording per failure, per output language (de/en/es; the turn's
# language is resolved once by the Brain): no provider names, no stack traces.
RECOVERY_MESSAGES: dict[RouteFailure, dict[str, str]] = {
    RouteFailure.UNAVAILABLE: {
        "en": "I can't reach a model for that right now. Please try again in a moment.",
        "de": (
            "Ich erreiche gerade kein Modell dafür. Versuch es bitte "  # i18n-allow
            "gleich noch einmal."  # i18n-allow
        ),
        "es": (
            "Ahora mismo no puedo acceder a un modelo para eso. Inténtalo "
            "de nuevo en un momento."
        ),
    },
    RouteFailure.TIMEOUT: {
        "en": "That took too long, so I stopped it. Want me to try again?",
        "de": (
            "Das hat zu lange gedauert, also habe ich es abgebrochen. Soll "  # i18n-allow
            "ich es nochmal versuchen?"  # i18n-allow
        ),
        "es": "Tardaba demasiado, así que lo detuve. ¿Quieres que lo intente de nuevo?",
    },
    RouteFailure.CANCELLED: {
        "en": "Okay, I stopped that.",
        "de": "Okay, ich habe das gestoppt.",  # i18n-allow
        "es": "Vale, lo he detenido.",
    },
    RouteFailure.INVALID_MODEL_OUTPUT: {
        "en": "I got a garbled answer for that. Want me to try again?",
        "de": (
            "Ich habe dafür eine unbrauchbare Antwort bekommen. Soll ich "  # i18n-allow
            "es nochmal versuchen?"  # i18n-allow
        ),
        "es": "Recibí una respuesta ilegible. ¿Quieres que lo intente de nuevo?",
    },
    RouteFailure.POLICY_DENIED: {
        "en": "My routing settings don't allow that step, so I didn't run it.",
        "de": (
            "Meine Routing-Einstellungen erlauben diesen Schritt nicht, "  # i18n-allow
            "also habe ich ihn nicht ausgeführt."  # i18n-allow
        ),
        "es": "Mi configuración de enrutamiento no permite ese paso, así que no lo ejecuté.",
    },
    RouteFailure.TOOL_DENIED: {
        "en": "That action wasn't approved, so I didn't run it.",
        "de": (
            "Diese Aktion wurde nicht freigegeben, also habe ich sie nicht "  # i18n-allow
            "ausgeführt."  # i18n-allow
        ),
        "es": "Esa acción no se aprobó, así que no la ejecuté.",
    },
    RouteFailure.DELEGATION_FAILED: {
        "en": "The complex step couldn't finish. I can retry it or try a simpler version.",
        "de": (
            "Der komplexe Schritt konnte nicht abgeschlossen werden. Ich "  # i18n-allow
            "kann es erneut oder einfacher versuchen."  # i18n-allow
        ),
        "es": (
            "El paso complejo no pudo terminar. Puedo reintentarlo o "
            "probar una versión más simple."
        ),
    },
    RouteFailure.CAPABILITY_UNSUPPORTED: {
        "en": "None of my configured models can do that kind of request yet.",
        "de": (
            "Keines meiner eingerichteten Modelle kann diese Art von "  # i18n-allow
            "Anfrage bisher."  # i18n-allow
        ),
        "es": "Ninguno de mis modelos configurados puede hacer ese tipo de petición todavía.",
    },
}

# Spoken when a turn carries a screenshot, camera frame or dropped image, cloud
# vision is not allowed, and no local target can take it.
MEDIA_STAYS_LOCAL_MESSAGES: dict[str, str] = {
    "en": (
        "I didn't send that image to a cloud model, because cloud vision is switched "
        "off and no local model can look at it right now."
    ),
    "de": (
        "Ich habe das Bild an kein Cloud-Modell geschickt, weil Cloud-Vision "  # i18n-allow
        "ausgeschaltet ist und gerade kein lokales Modell es ansehen kann."  # i18n-allow
    ),
    "es": (
        "No envié esa imagen a un modelo en la nube, porque la visión en la nube "
        "está desactivada y ahora mismo ningún modelo local puede verla."
    ),
}

_FALLBACK_LANG = "en"


def recovery_message(failure: RouteFailure, lang: str) -> str:
    """The user-safe recovery line for ``failure`` in the turn's language."""
    table = RECOVERY_MESSAGES[failure]
    return table.get(lang) or table[_FALLBACK_LANG]


def media_stays_local_message(lang: str) -> str:
    return MEDIA_STAYS_LOCAL_MESSAGES.get(lang) or MEDIA_STAYS_LOCAL_MESSAGES[_FALLBACK_LANG]


@dataclass(frozen=True)
class TurnSignals:
    """What the Brain already knows about the turn when it builds the chain."""

    level: str = "fast"
    needs_tools: bool = False
    needs_vision: bool = False
    explicit_escalation: bool = False


@dataclass(frozen=True)
class RouteDecision:
    """The selected tier, the ordered attempts, and why."""

    tier: str
    chain: list[tuple[str, str | None]]
    reason: str
    excluded: list[tuple[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "chain": [f"{p}:{m or ''}" for p, m in self.chain],
            "reason": self.reason,
            "excluded": [f"{p}:{why}" for p, why in self.excluded],
        }


def _target(policy: Any, tier: str) -> tuple[str, str | None] | None:
    raw = getattr(policy, tier, None)
    provider = str(getattr(raw, "provider", "") or "").strip()
    if not provider:
        return None
    model = getattr(raw, "model", None)
    return provider, (str(model).strip() or None) if model else None


def is_denied(policy: Any, provider: str, model: str | None) -> bool:
    """True when the configured deny lists exclude this provider or model."""
    denied = {p.strip() for p in (getattr(policy, "deny_providers", None) or []) if p}
    if provider in denied:
        return True
    prefixes = [p for p in (getattr(policy, "deny_model_prefixes", None) or []) if p]
    return bool(model) and any(str(model).startswith(prefix) for prefix in prefixes)


def filter_denied(
    policy: Any, chain: Iterable[tuple[str, str | None]]
) -> list[tuple[str, str | None]]:
    """Drop every attempt the deny lists exclude (used on override chains too)."""
    return [(p, m) for p, m in chain if not is_denied(policy, p, m)]


def pinned_test_tier(policy: Any, environ: dict[str, str] | None = None) -> str | None:
    """The pinned tier for deterministic tests, honoured only in test mode."""
    tier = str(getattr(policy, "test_override_tier", "") or "").strip()
    env = os.environ if environ is None else environ
    if not tier or env.get(TEST_MODE_ENV) != "1":
        return None
    return tier if tier in (TIER_FAST, TIER_DEEP) else None


def decide_route(
    signals: TurnSignals,
    policy: Any,
    *,
    available: Iterable[str],
    can_call_tools: Callable[[str, str | None], bool],
    supports_vision: Callable[[str, str | None], bool],
    selected_model: Callable[[str], str | None] | None = None,
    environ: dict[str, str] | None = None,
) -> RouteDecision:
    """Pick the tier and the bounded, ordered chain for one turn.

    Rules, in order:
    * An explicit escalation request selects ``escalation`` when it is enabled;
      the caller runs the delegate instead of a model chain.
    * A pinned test tier wins in test mode only.
    * ``deep``/``code`` intents start on the deep target, everything else on the
      fast target. The other target follows as the one bounded fallback; the
      chain never repeats a target, so it cannot loop.
    * A target is excluded, with its reason recorded, when it is denied,
      unavailable, cannot call tools on a tool turn, or cannot see on a vision
      turn. Excluded targets never reappear later in the chain.
    """
    available_set = set(available)
    escalation = getattr(policy, "escalation", None)
    if signals.explicit_escalation and bool(getattr(escalation, "enabled", False)):
        return RouteDecision(TIER_ESCALATION, [], "escalation:explicit-request")

    pinned = pinned_test_tier(policy, environ)
    if pinned is not None:
        order = [pinned, TIER_DEEP if pinned == TIER_FAST else TIER_FAST]
        reason = f"test-override:{pinned}"
    elif signals.level in _DEEP_LEVELS:
        order = [TIER_DEEP, TIER_FAST]
        reason = f"intent:{signals.level}"
    else:
        order = [TIER_FAST, TIER_DEEP]
        reason = f"intent:{signals.level or 'fast'}"

    chain: list[tuple[str, str | None]] = []
    excluded: list[tuple[str, str]] = []
    selected: str | None = None
    for tier in order:
        target = _target(policy, tier)
        if target is None:
            excluded.append((tier, "not-configured"))
            continue
        provider, model = target
        # A route with no explicit pin follows the provider card's selection.
        # Resolve before checking denies/capabilities, never after admission.
        if model is None and selected_model is not None:
            model = selected_model(provider)
            target = provider, model
        why = None
        if is_denied(policy, provider, model):
            why = "denied"
        elif provider not in available_set:
            why = "unavailable"
        elif signals.needs_tools and not can_call_tools(provider, model):
            why = "no-tools"
        elif signals.needs_vision and not supports_vision(provider, model):
            why = "no-vision"
        if why is not None:
            excluded.append((f"{tier}:{provider}", why))
            continue
        if target not in chain:
            chain.append(target)
        if selected is None:
            selected = tier

    if selected is None:
        if signals.needs_vision and any(why == "no-vision" for _, why in excluded):
            return RouteDecision("none", [], "capability:vision-unsupported", excluded)
        return RouteDecision("none", [], "no-eligible-target", excluded)
    if selected != order[0]:
        reason = f"{reason};fallback-from:{order[0]}"
    return RouteDecision(selected, chain, reason, excluded)


def _matches_tier(policy: Any, provider: str, model: str | None, *, local_only: bool) -> bool:
    for tier in (TIER_FAST, TIER_DEEP):
        raw = getattr(policy, tier, None)
        if local_only and not bool(getattr(raw, "local", False)):
            continue
        target = _target(policy, tier)
        if target is not None and target[0] == provider and (
            target[1] is None or model is None or target[1] == model
        ):
            return True
    return False


def is_local_target(policy: Any, provider: str, model: str | None) -> bool:
    """True when a configured tier declares this provider/model as on-device."""
    return _matches_tier(policy, provider, model, local_only=True)


def is_policy_target(policy: Any, provider: str, model: str | None) -> bool:
    """True when this provider/model is one of the configured tiers."""
    return _matches_tier(policy, provider, model, local_only=False)


def media_allowed(policy: Any, provider: str, model: str | None) -> bool:
    """Whether this target may receive images (local, or cloud vision allowed)."""
    if bool(getattr(policy, "allow_cloud_vision", False)):
        return True
    return is_local_target(policy, provider, model)


def media_chain(
    policy: Any,
    chain: Iterable[tuple[str, str | None]],
    *,
    supports_vision: Callable[[str, str | None], bool],
) -> tuple[list[tuple[str, str | None]], list[tuple[str, str]]]:
    """Order and confine a policy chain for a turn that carries images.

    Only targets already on the chain are considered, so no provider outside
    the configured tiers can be pulled in for an image. Without
    ``allow_cloud_vision`` every target not declared ``local`` is excluded
    (reason ``cloud-vision-off``). Targets that can see go first; the order
    within each group is kept.
    """
    kept: list[tuple[str, str | None]] = []
    excluded: list[tuple[str, str]] = []
    for provider, model in chain:
        if not media_allowed(policy, provider, model):
            excluded.append((provider, "cloud-vision-off"))
            continue
        kept.append((provider, model))
    seeing = [t for t in kept if supports_vision(*t)]
    blind = [t for t in kept if not supports_vision(*t)]
    return seeing + blind, excluded


def wants_escalation(policy: Any, user_text: str) -> bool:
    """True when the user explicitly asks for the escalation delegate.

    The phrases come from ``escalation.trigger_phrases`` in the config, so the
    words that summon a delegate are the user's choice, not code.
    """
    escalation = getattr(policy, "escalation", None)
    if not bool(getattr(escalation, "enabled", False)):
        return False
    text = (user_text or "").casefold()
    phrases = getattr(escalation, "trigger_phrases", None) or []
    return any(p and p.casefold() in text for p in phrases)
