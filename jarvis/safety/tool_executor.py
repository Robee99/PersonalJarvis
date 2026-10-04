"""ToolExecutor: orchestrates risk-eval → plausibility → approval → execute → event log.

The only authorized entry point for tool calls. Calling `Tool.execute()`
directly bypasses safety — that is a bug.

Phase 4 (persona mandate): a plausibility check runs before every approval
decision. If the voice pipeline has registered a ``plausibility_context_fn``,
the executor fetches transcript confidence + wake age and decides whether
``ask``/``monitor`` tools need an extra confirmation (see
``jarvis.brain.plausibility``).
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import time
from collections.abc import Callable, Iterator
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from jarvis.core.bus import EventBus
from jarvis.core.events import (
    ActionApprovalRequired,
    ActionDenied,
    ActionExecuted,
    ActionProposed,
)
from jarvis.core.protocols import (
    CancelToken,
    ExecutionContext,
    Tool,
    ToolResult,
    Transcript,
)
from jarvis.core.redact import redact_secrets, safe_preview

from .approval import TIMEOUT_REASON, ApprovalWorkflow
from .approval_surface import (
    CONVERSATIONAL,
    UNATTENDED,
    resolve_approval_surface,
)
from .risk_tier import ActionBlocked, RiskTierEvaluator

if TYPE_CHECKING:
    from jarvis.brain.plausibility import PlausibilityDecision
    from jarvis.core.config import BrainPlausibilityConfig


log = logging.getLogger(__name__)


# Consequential tools started inside the current ``recording_side_effects()``
# block. The Brain opens one block per provider attempt: when an attempt fails
# after an action tool already started, the action's outcome is unknown and the
# turn must not be replayed on another provider (that would repeat the action).
_side_effects: ContextVar[list[str] | None] = ContextVar("jarvis_side_effects", default=None)


@contextlib.contextmanager
def recording_side_effects() -> Iterator[list[str]]:
    """Collect the names of non-read tools started while the block is open."""
    started: list[str] = []
    token = _side_effects.set(started)
    try:
        yield started
    finally:
        _side_effects.reset(token)


def _note_side_effect(tool: Tool, args: dict[str, Any]) -> None:
    started = _side_effects.get()
    if started is None:
        return
    from jarvis.core.tool_read_only import allows_read

    if not allows_read(tool, args):
        started.append(tool.name)


# Sentinel returned (as ``ToolResult.error``) when a confirmation-requiring tool
# is invoked on a CONVERSATIONAL turn (``config_snapshot["voice_confirm"]``).
# Instead of blocking in ``ApprovalWorkflow.wait()`` for a UI approval no voice/
# chat user can give (which is then beheaded by the 20 s no-first-frame ceiling →
# the misleading "took too long" phrase, forensic 2026-06-18), the executor stashes
# the action and returns this sentinel so the brain SPEAKS a confirmation question
# and ends the turn. The next "ja" re-runs the action via ``execute_confirmed``.
VOICE_CONFIRM_SENTINEL = "__voice_confirm_required__"


# The three ways an approval-gated call can end WITHOUT running. They carry
# distinct ``ToolResult.error`` prefixes on purpose: until the GT-12 fix a
# 60-second timeout came back as ``approval-denied (timeout)``, so every
# consumer that classifies on ``startswith("approval-denied")`` — the mission
# ``WorkerToolBroker`` does exactly that — recorded "the user refused" for a
# decision nobody was ever asked to make. A refusal, a silence, and an absent
# human are three different facts and must read as three different facts.
APPROVAL_DENIED_PREFIX = "approval-denied"
APPROVAL_TIMEOUT_PREFIX = "approval-timeout"
APPROVAL_UNAVAILABLE_PREFIX = "approval-unavailable"

#: ``ToolResult.output["outcome"]`` for the unattended dead end, and the
#: ``ActionDenied.reason`` published alongside it. The word says what happened:
#: approval was impossible, not withheld.
APPROVAL_UNAVAILABLE_OUTCOME = "approval_unavailable"


# ---------------------------------------------------------------------------
# Execution deadline and in-flight cancellation
# ---------------------------------------------------------------------------
#
# The approval timeout above bounds how long a HUMAN may take to decide. The
# deadline below bounds how long an approved TOOL may run. Without it a tool
# that hangs (a dead socket, a stuck native call) parked the whole turn
# forever, and a kill-switch press only took effect before the call started.

#: The run budget for a tool that declares none. Generous on purpose: nearly
#: every tool finishes in seconds, and the ones that legitimately run for
#: minutes (shell commands with a ``timeout_s``, harness and computer-use
#: missions, the agent browser, model downloads) declare their own budget.
DEFAULT_EXECUTION_TIMEOUT_S = 120.0

#: No tool may hold the executor longer than this, whatever it declares.
MAX_EXECUTION_TIMEOUT_S = 3600.0

#: How long a cancelled tool gets to run its own cleanup (kill a child
#: process, close a stream) before the executor stops waiting for it.
CANCEL_GRACE_S = 2.0

#: ``ToolResult.error`` prefixes for a call that STARTED and was interrupted.
#: Both mean the same thing for safety: the action may or may not have taken
#: effect. Distinct from the pre-execution ``cancelled (...)`` result, which
#: is only returned when nothing ran.
EXECUTION_TIMEOUT_PREFIX = "execution-timeout"
EXECUTION_CANCELLED_PREFIX = "execution-cancelled"

#: ``ToolResult.output["outcome"]`` for an interrupted call.
OUTCOME_UNKNOWN = "outcome_unknown"

#: ``ToolResult.error`` prefix for arguments that do not match the tool's
#: schema. Nothing ran, so the caller may correct the call and try again.
INVALID_ARGUMENTS_PREFIX = "invalid-arguments"

# Interrupted tool tasks that ignored cancellation past the grace period.
# Held strongly so the loop does not garbage-collect them mid-flight; each
# removes itself when it finally ends.
_ABANDONED_TOOL_TASKS: set[asyncio.Future[Any]] = set()


def _budget_value(raw: Any) -> float | None:
    """A declared budget as seconds, or ``None`` when it is not usable."""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):  # not a number: the caller falls back to the next budget source
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return value


def execution_budget_s(tool: Tool, args: dict[str, Any]) -> float:
    """How long ``tool`` may run for this call, in seconds.

    Discovered by capability, never by tool name:

    * ``execution_timeout_for_args(args) -> float | None`` refines the budget
      per call (a shell command's own ``timeout_s`` plus room to kill it);
    * ``execution_timeout_s`` is the tool's static budget;
    * otherwise :data:`DEFAULT_EXECUTION_TIMEOUT_S`.

    The result is clamped to :data:`MAX_EXECUTION_TIMEOUT_S`. A hook that
    raises or answers something unusable falls through to the next source.
    """
    refine = getattr(tool, "execution_timeout_for_args", None)
    if callable(refine):
        try:
            value = _budget_value(refine(args))
        except Exception as exc:  # noqa: BLE001 — a broken hook keeps the static budget
            log.debug("execution_timeout_for_args on %r failed: %s", tool.name, exc)
            value = None
        if value is not None:
            return min(value, MAX_EXECUTION_TIMEOUT_S)
    value = _budget_value(getattr(tool, "execution_timeout_s", None))
    if value is not None:
        return min(value, MAX_EXECUTION_TIMEOUT_S)
    return DEFAULT_EXECUTION_TIMEOUT_S


class _Interrupted:
    """Why an in-flight tool call was stopped (``timeout`` or ``cancelled``)."""

    __slots__ = ("kind", "reason")

    def __init__(self, kind: str, reason: str) -> None:
        self.kind = kind
        self.reason = reason


async def _cancel_signal(cancel_token: CancelToken) -> None:
    """Return once ``cancel_token`` fires: its event when it has one, else polling."""
    wait = getattr(cancel_token, "wait_until_cancelled", None)
    if callable(wait):
        await wait()
        return
    # A token without the event-based wait (only possible for a structural
    # stand-in) is polled; there is no event to wait on.
    while not cancel_token.is_cancelled():  # noqa: ASYNC110
        await asyncio.sleep(0.1)


def _forget_abandoned(task: asyncio.Future[Any]) -> None:
    _ABANDONED_TOOL_TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        log.info("interrupted tool task ended late with %s", type(exc).__name__)


async def _stop_tool_task(task: asyncio.Future[Any], tool_name: str) -> None:
    """Cancel ``task`` and give it :data:`CANCEL_GRACE_S` to clean up.

    Cooperative cancellation reaches the tool's coroutine as ``CancelledError``
    at its current ``await``, so ``finally``/``except CancelledError`` blocks
    run there: an asyncio child process killed in such a block is gone before
    this returns. What cancellation CANNOT stop: work handed to a thread
    (``asyncio.to_thread``, an executor) or a native call. The awaiting task
    ends promptly, but the thread keeps running to completion in the
    background. A coroutine that swallows the cancellation and keeps going is
    abandoned after the grace period: it is kept referenced so it can finish,
    and the executor returns without waiting for it.
    """
    if task.done():
        return
    task.cancel()
    done, _pending = await asyncio.wait({task}, timeout=CANCEL_GRACE_S)
    if task in done:
        if not task.cancelled() and task.exception() is not None:
            log.debug(
                "tool %s raised while being cancelled: %s",
                tool_name, type(task.exception()).__name__,
            )
        return
    log.warning(
        "tool %s did not stop within %.1fs of cancellation; leaving it to "
        "finish in the background (outcome unknown)",
        tool_name, CANCEL_GRACE_S,
    )
    _ABANDONED_TOOL_TASKS.add(task)
    task.add_done_callback(_forget_abandoned)


async def _run_bounded(
    tool: Tool,
    args: dict[str, Any],
    ctx: ExecutionContext,
    *,
    budget_s: float,
    cancel_token: CancelToken | None,
) -> ToolResult | _Interrupted:
    """Run ``tool.execute`` racing its deadline and the cancel token.

    Returns the tool's result (a tool exception propagates unchanged), or an
    :class:`_Interrupted` once the deadline passed or the token fired. A
    cancellation of the CALLER is passed on to the tool task before it is
    re-raised, exactly as a direct ``await`` would have done.
    """
    task: asyncio.Future[Any] = asyncio.ensure_future(tool.execute(args, ctx))
    watchers: set[asyncio.Future[Any]] = {task}
    cancel_wait: asyncio.Future[Any] | None = None
    if cancel_token is not None:
        cancel_wait = asyncio.ensure_future(_cancel_signal(cancel_token))
        watchers.add(cancel_wait)
    try:
        done, _pending = await asyncio.wait(
            watchers, timeout=budget_s, return_when=asyncio.FIRST_COMPLETED,
        )
    except BaseException:
        await _stop_tool_task(task, tool.name)
        raise
    finally:
        if cancel_wait is not None and not cancel_wait.done():
            cancel_wait.cancel()
    if task in done:
        return task.result()
    if cancel_wait is not None and cancel_wait in done:
        interrupted = _Interrupted(
            "cancelled", str(getattr(cancel_token, "reason", None) or "requested"),
        )
    else:
        interrupted = _Interrupted("timeout", f"no result within {budget_s:.0f}s")
    await _stop_tool_task(task, tool.name)
    return interrupted


def _interrupted_result(
    tool: Tool, trace_id: UUID, interrupted: _Interrupted, budget_s: float,
) -> ToolResult:
    """The typed failure for a call that started and was stopped.

    It never claims the action was undone: the tool may have acted before it
    was stopped. ``retry_safe`` tells every consumer, the model included, not
    to repeat the call on its own.
    """
    prefix = (
        EXECUTION_TIMEOUT_PREFIX
        if interrupted.kind == "timeout"
        else EXECUTION_CANCELLED_PREFIX
    )
    detail = safe_preview(interrupted.reason, max_chars=120)
    return ToolResult(
        success=False,
        output={
            "outcome": OUTCOME_UNKNOWN,
            "interrupted_by": interrupted.kind,
            "tool_name": tool.name,
            "trace_id": str(trace_id),
            "budget_s": round(budget_s, 3),
            "retry_safe": False,
            "message": (
                f"{tool.name} was started but stopped before it reported a "
                "result, so whether it took effect is unknown. Do not repeat "
                "it automatically; check its effect or ask the user first."
            ),
        },
        error=f"{prefix} ({detail}; {OUTCOME_UNKNOWN}, not retried)",
    )


# ---------------------------------------------------------------------------
# Central argument validation
# ---------------------------------------------------------------------------

#: The schema keywords a call is refused for. Range and shape limits
#: (minimum, maxLength, pattern, ...) are NOT enforced here: tools clamp or
#: normalize those themselves today, and refusing them would break calls that
#: work.
_ENFORCED_KEYWORDS = frozenset({"type", "required", "additionalProperties", "enum"})
_UNION_KEYWORDS = frozenset({"anyOf", "oneOf"})
_MAX_NAMED_FIELDS = 5

_validator_classes: dict[int, Any] = {}


def _numeric_string(value: Any) -> float | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        number = float(value.strip())
    except ValueError:  # not numeric: the type check reports the field, nothing is lost
        return None
    return number if math.isfinite(number) else None


def _lenient_number(_checker: Any, instance: Any) -> bool:
    # Models routinely send "30" for a number; every tool reads numbers with
    # float()/int(), so a numeric string is accepted. A bool never is.
    if isinstance(instance, bool):
        return False
    if isinstance(instance, int | float):
        return True
    return _numeric_string(instance) is not None


def _lenient_integer(_checker: Any, instance: Any) -> bool:
    if isinstance(instance, bool):
        return False
    if isinstance(instance, int):
        return True
    if isinstance(instance, float):
        return instance.is_integer()
    number = _numeric_string(instance)
    return number is not None and number.is_integer()


def _validator_for(schema: dict[str, Any]) -> Any:
    """A jsonschema validator class for ``schema`` with lenient numbers."""
    from jsonschema import validators  # lazy: keeps jsonschema off the boot path

    base = validators.validator_for(schema, default=validators.Draft202012Validator)
    cls = _validator_classes.get(id(base))
    if cls is None:
        checker = base.TYPE_CHECKER.redefine_many(
            {"number": _lenient_number, "integer": _lenient_integer},
        )
        cls = validators.extend(base, type_checker=checker)
        _validator_classes[id(base)] = cls
    return cls


def _enforced(error: Any) -> bool:
    if error.validator in _ENFORCED_KEYWORDS:
        return True
    if error.validator in _UNION_KEYWORDS:
        context = list(error.context or ())
        return bool(context) and all(_enforced(sub) for sub in context)
    return False


def _field_name(parts: Any) -> str:
    name = ".".join(str(part) for part in parts)
    return safe_preview(name, max_chars=64) if name else "arguments"


def _names(values: Any) -> str:
    listed = [safe_preview(str(value), max_chars=64) for value in values]
    shown = ", ".join(f"'{name}'" for name in listed[:_MAX_NAMED_FIELDS])
    if len(listed) > _MAX_NAMED_FIELDS:
        shown += f" (+{len(listed) - _MAX_NAMED_FIELDS} more)"
    return shown


def _describe_error(error: Any) -> str:
    """A short reason naming the field. Never quotes the offending VALUE:
    an argument can carry a secret, and this text reaches logs and the UI."""
    where = _field_name(error.absolute_path)
    if error.validator == "required":
        instance = error.instance if isinstance(error.instance, dict) else {}
        missing = [name for name in error.validator_value if name not in instance]
        prefix = "" if where == "arguments" else f"{where}."
        return "missing required field " + _names(f"{prefix}{name}" for name in missing)
    if error.validator == "additionalProperties":
        instance = error.instance if isinstance(error.instance, dict) else {}
        declared = (error.schema or {}).get("properties") or {}
        extras = sorted(str(name) for name in instance if name not in declared)
        return "unexpected field " + _names(extras) + (
            "" if where == "arguments" else f" in '{where}'"
        )
    if error.validator == "type":
        expected = error.validator_value
        if isinstance(expected, list):
            expected = " or ".join(str(kind) for kind in expected)
        return f"field '{where}' must be of type {expected}"
    if error.validator == "enum":
        allowed = list(error.validator_value or ())
        return f"field '{where}' must be one of {_names(allowed)}"
    return f"field '{where}' does not match any allowed form"


def validate_tool_args(tool: Tool, args: Any) -> str | None:
    """Check ``args`` against ``tool.schema``; the reason it fails, or ``None``.

    Enforces types (numbers may arrive as numeric strings), required fields,
    ``additionalProperties: false`` and ``enum``. Extra fields pass when the
    schema does not forbid them, and ``null`` on an OPTIONAL top-level field
    reads as "not provided" (models emit it routinely and every tool reads
    arguments with ``.get``). A missing, non-dict or unusable schema skips
    validation instead of refusing every call to that tool.
    """
    schema = getattr(tool, "schema", None)
    if not isinstance(schema, dict) or not schema:
        log.debug("tool %r has no usable schema; argument validation skipped",
                  getattr(tool, "name", "?"))
        return None
    candidate = args
    if isinstance(args, dict):
        declared = schema.get("properties")
        required = schema.get("required")
        if isinstance(declared, dict):
            required_names = set(required) if isinstance(required, list) else set()
            candidate = {
                key: value
                for key, value in args.items()
                if not (value is None and key in declared and key not in required_names)
            }
    try:
        validator = _validator_for(schema)(schema)
        errors = [error for error in validator.iter_errors(candidate) if _enforced(error)]
    except Exception as exc:  # noqa: BLE001 — a broken schema must not block the tool
        log.debug(
            "argument validation skipped for %r (unusable schema: %s)",
            getattr(tool, "name", "?"), type(exc).__name__,
        )
        return None
    if not errors:
        return None
    errors.sort(key=lambda error: (len(error.absolute_path), str(error.validator)))
    return _describe_error(errors[0])


# ``plausibility_context_fn`` returns (Transcript | None, wake_age_s | None).
# The voice pipeline registers a provider that supplies the last user-turn
# transcript and the seconds since the last wake trigger. On
# ``None`` returns the executor behaves as before (no plausibility check).
PlausibilityContextFn = Callable[[], "tuple[Transcript | None, float | None]"]


def _optional_string(value: Any) -> str | None:
    """Normalize optional correlation metadata without inventing identifiers."""
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


class ToolExecutor:
    """Pipeline: evaluate → (plausibility) → (approve) → execute → log."""

    def __init__(
        self,
        bus: EventBus,
        evaluator: RiskTierEvaluator,
        approval: ApprovalWorkflow,
        *,
        default_timeout_s: float = 60.0,
        plausibility_config: BrainPlausibilityConfig | None = None,
        plausibility_context_fn: PlausibilityContextFn | None = None,
    ) -> None:
        self._bus = bus
        self._evaluator = evaluator
        self._approval = approval
        self._default_timeout_s = default_timeout_s
        self._plausibility_config = plausibility_config
        self._plausibility_context_fn = plausibility_context_fn
        # Two-turn voice/chat confirmation: actions deferred by ``execute`` on a
        # conversational turn, keyed by trace_id, awaiting an ``execute_confirmed``
        # (user said "ja") or ``cancel_pending`` (user said "nein"). The tool +
        # args live here OUT-OF-BAND — never in the serialized ToolResult.output.
        self._pending_voice: dict[UUID, tuple[Tool, dict[str, Any]]] = {}

    #: The longest a surface may keep an approval open, whatever it asks for.
    MAX_APPROVAL_TIMEOUT_S = 900.0

    def _approval_timeout(self, config_snapshot: dict[str, Any] | None) -> float:
        """The wait for this call: the surface's ``approval_timeout_s`` when it
        declares one (never below the default, never above the cap), else the
        default the executor was built with."""
        raw = (config_snapshot or {}).get("approval_timeout_s")
        if raw is None:
            return self._default_timeout_s
        try:
            asked = float(raw)
        except (TypeError, ValueError):
            return self._default_timeout_s
        return max(self._default_timeout_s, min(asked, self.MAX_APPROVAL_TIMEOUT_S))

    def set_plausibility_context_fn(
        self, fn: PlausibilityContextFn | None,
    ) -> None:
        """Late registration of the plausibility-context provider.

        The voice pipeline calls this after its own ``run()`` setup, because
        the ToolExecutor is built earlier in the bootstrap order than
        the pipeline. Idempotent — ``None`` resets the hook.
        """
        self._plausibility_context_fn = fn

    def _evaluate_plausibility(
        self,
        tool: Tool,
        decision: Any,
    ) -> PlausibilityDecision | None:
        """Fetches the current plausibility context and checks it.

        Returns ``None`` if no context provider is registered, or the
        tool was downgraded to ``safe`` via whitelist — whitelist logic
        is sacred (mandate: "whitelist-downgraded tools keep running
        without a plausibility check").
        """
        if self._plausibility_context_fn is None:
            return None
        # Whitelist downgrade: skip plausibility.
        if decision.approved_by == "whitelist":
            return None
        try:
            transcript, wake_age = self._plausibility_context_fn()
        except Exception as exc:  # noqa: BLE001
            log.debug("plausibility_context_fn failed: %s", exc)
            return None
        from jarvis.brain.plausibility import check_plausibility

        return check_plausibility(
            tool_name=tool.name,
            risk_tier=decision.tier,
            transcript=transcript,
            wake_age_s=wake_age,
            config=self._plausibility_config,
        )

    async def publish_guard_denied(
        self,
        tool_name: str,
        reason: str,
        *,
        trace_id: UUID | None = None,
    ) -> None:
        """Surface a tool call refused BEFORE reaching :meth:`execute`.

        The tool-use loop's deterministic guards (how-to question, research
        intent, STT hallucination, unknown tool name, …) block a call without
        ever entering the executor — so no ActionProposed/ActionExecuted event
        fires and the session timeline shows NO trace of why a turn refused
        (2026-07-06 audit: the unknown-tool 'run-shell' incident left zero
        events). Publishing the same ``ActionDenied`` the blacklist path uses
        makes the refusal visible; the recorder already subscribes to it.
        Best-effort: observability must never break tool execution.
        """
        try:
            await self._bus.publish(ActionDenied(
                trace_id=trace_id or uuid4(),
                tool_name=tool_name,
                reason=reason,
            ))
        except Exception:  # noqa: BLE001
            log.debug("publish_guard_denied failed", exc_info=True)

    async def _approval_unavailable(
        self,
        tool: Tool,
        tid: UUID,
        risk_tier: str,
        config_snapshot: dict[str, Any] | None,
    ) -> ToolResult:
        """The honest dead end: this action needed a human and had none.

        Publishes ``ActionDenied`` so the refusal is visible in the timeline
        (same reasoning as :meth:`publish_guard_denied` — a call that silently
        vanishes is worse than one that failed), but says in the reason WHY it
        did not run. The returned error carries its own prefix so no consumer
        can mistake it for a user's "no", and the output carries a
        plain-language sentence a person can actually read in a run log.
        """
        reason = f"{APPROVAL_UNAVAILABLE_OUTCOME}: no approval channel on this surface"
        await self._bus.publish(ActionDenied(
            trace_id=tid,
            tool_name=tool.name,
            reason=reason,
        ))
        log.info(
            "approval-unavailable: %s (tier=%s) needed a confirmation and this "
            "surface has nobody to give it — failing fast",
            tool.name, risk_tier,
        )
        output: dict[str, Any] = {
            "outcome": APPROVAL_UNAVAILABLE_OUTCOME,
            "tool_name": tool.name,
            "trace_id": str(tid),
            "risk_tier": risk_tier,
        }
        # Plain-language sentence in the turn's ALREADY-RESOLVED output
        # language (Runtime Output Language doctrine: this layer reads the
        # resolved value, it never re-derives one). Phrasing lives in the one
        # channel-agnostic table, next to the confirmation question it belongs
        # to. Lazy import — ``jarvis.voice`` couples to ``jarvis.core.self_mod``
        # via its package __init__, so importing at module load would create an
        # order-dependent cycle (same pattern as the tool-use loop).
        try:
            from jarvis.voice.tool_confirmation import format_approval_unavailable

            output["message"] = format_approval_unavailable(
                tool.name,
                language=str((config_snapshot or {}).get("output_language") or ""),
            )
        except Exception as exc:  # noqa: BLE001 — phrasing must not decide safety
            log.debug("approval-unavailable phrasing failed: %s", exc)
        return ToolResult(
            success=False,
            output=output,
            error=f"{APPROVAL_UNAVAILABLE_PREFIX} ({reason})",
        )

    async def execute(
        self,
        tool: Tool,
        args: dict[str, Any],
        *,
        user_utterance: str = "",
        config_snapshot: dict[str, Any] | None = None,
        memory_read: Any | None = None,
        trace_id: UUID | None = None,
        rationale: str = "",
        cancel_token: CancelToken | None = None,
    ) -> ToolResult:
        tid = trace_id or uuid4()
        t_start = time.perf_counter()

        if (config_snapshot or {}).get("chat_read_only"):
            from jarvis.core.tool_read_only import allows_read

            if not allows_read(tool, args):
                await self._bus.publish(ActionDenied(
                    trace_id=tid, tool_name=tool.name, reason="Plan mode permits reads only",
                ))
                return ToolResult(False, None, "Plan mode permits reads only")

        if cancel_token is not None and cancel_token.is_cancelled():
            return ToolResult(
                success=False,
                output=None,
                error=f"cancelled ({cancel_token.reason or 'requested'})",
            )

        # 0. Arguments must match the tool's schema before anything is
        # evaluated, approved or run. Nothing has happened yet, so the caller
        # may correct the call and try again.
        invalid = await self._refuse_invalid_args(tool, args, tid)
        if invalid is not None:
            return invalid

        # 1. Evaluate
        try:
            decision = self._evaluator.evaluate(tool, args)
        except ActionBlocked as exc:
            await self._bus.publish(ActionDenied(
                trace_id=tid,
                tool_name=tool.name,
                reason=f"blacklist: {exc.pattern}",
            ))
            return ToolResult(success=False, output=None, error=str(exc))

        # 2. Plausibility check (Phase 4): the result can force confirmation
        # even when the tier workflow does not (for example, ``monitor``).
        plaus = self._evaluate_plausibility(tool, decision)
        if plaus is not None and plaus.reason != "ok":
            log.info(
                "Plausibility[%s]: tier=%s reason=%s require_confirm=%s",
                tool.name, decision.tier, plaus.reason, plaus.require_confirmation,
            )

        # 3. Arm approval BEFORE publishing ActionProposed. Subscribers such as
        # TaskAutoApprover may answer synchronously from that event; registering
        # afterward loses the answer and turns a valid grant into a timeout.
        approved_by = decision.approved_by or "auto"
        tier_confirm = self._evaluator.needs_user_confirmation(decision)
        plaus_confirm = plaus is not None and plaus.require_confirmation
        # Explicit intent (Claude-Code permission model, mandate 2026-08-08):
        # a consequential action the user's own utterance already asked for by
        # name ("delete the folder X") skips the redundant confirmation. Only
        # the TIER requirement may be waived — a plausibility-forced
        # confirmation (low STT confidence, stale wake) stays binding, because
        # the whole doubt there is whether the utterance was heard right.
        if tier_confirm and not plaus_confirm and user_utterance:
            confirms = getattr(tool, "intent_confirms_args", None)
            if callable(confirms):
                try:
                    if confirms(args, user_utterance):
                        tier_confirm = False
                        approved_by = "explicit-intent"
                        log.info(
                            "explicit-intent: %s authorized by the utterance, "
                            "skipping confirmation", tool.name,
                        )
                except Exception as exc:  # noqa: BLE001 — keep the confirmation on a broken hook
                    log.warning(
                        "intent_confirms_args on %r raised %r — keeping confirmation",
                        tool.name, exc,
                    )
        needs_confirm = tier_confirm or plaus_confirm
        # WHO can answer this gate — declared by the calling layer, never by
        # the model (see ``approval_surface``). It changes only how the
        # approval is obtained, never whether one is needed: ``needs_confirm``
        # above is the untouched tier decision.
        surface = resolve_approval_surface(config_snapshot)
        voice_confirm = surface == CONVERSATIONAL
        # How long a card may stay open — declared by the calling layer like
        # the surface itself (a person reading a chat card must not lose the
        # tool to a clock built for a spoken "ja"), clamped so no surface can
        # park the executor forever.
        approval_timeout_s = self._approval_timeout(config_snapshot)
        approval_ticket = None
        if needs_confirm and not voice_confirm:
            # An unattended call arms too: the pre-authorization bridges answer
            # synchronously on the ActionApprovalRequired publish below, and
            # ADR-0031 relies on the ticket existing before that publish.
            approval_ticket = self._approval.arm(tid)

        # 3.5 Proposed event (the UI can use this as a live indicator). The
        # rationale is redacted and capped so no raw secret reaches the bus.
        try:
            await self._bus.publish(ActionProposed(
                trace_id=tid,
                tool_name=tool.name,
                args=args,
                risk_tier=decision.tier,
                rationale=safe_preview(rationale),
            ))
        except BaseException:
            if approval_ticket is not None:
                approval_ticket.close()
            raise

        if approval_ticket is not None:
            reason = (
                "plausibility"
                if plaus is not None and plaus.require_confirmation
                else "risk_tier"
            )
            # An unattended call expires immediately: it will not be waited on,
            # so advertising a 60-second window would invite a UI to offer a
            # decision that can no longer reach anybody.
            window_ns = (
                0
                if surface == UNATTENDED
                else int(approval_timeout_s * 1_000_000_000)
            )
            await self._bus.publish(
                ActionApprovalRequired(
                    trace_id=tid,
                    tool_name=tool.name,
                    risk_tier=decision.tier,
                    reason=reason,
                    args_preview=safe_preview(args),
                    expires_at_ns=time.time_ns() + window_ns,
                    mission_id=_optional_string(
                        (config_snapshot or {}).get("mission_id")
                    ),
                    worker_id=_optional_string(
                        (config_snapshot or {}).get("worker_id")
                    ),
                    approval_ref=_optional_string(
                        (config_snapshot or {}).get("approval_ref")
                    ),
                )
            )

        if needs_confirm:
            # Two-turn confirmation on a conversational turn: do NOT block on the
            # UI-approval future (no voice/chat user can resolve it within the
            # turn's latency window). Stash the action and return the sentinel so
            # the brain speaks a confirmation question; the user's next "ja" calls
            # ``execute_confirmed`` (AD-OE: the talker never awaits heavy/blocking
            # work on the turn). ``needs_confirm`` already excludes whitelist
            # downgrades, so this fires only for genuinely consequential tools.
            if voice_confirm:
                self._pending_voice[tid] = (tool, dict(args))
                log.info(
                    "voice-confirm: deferring %s (tier=%s) for two-turn confirmation",
                    tool.name, decision.tier,
                )
                sentinel_output: dict[str, Any] = {
                    "tool_name": tool.name,
                    "trace_id": str(tid),
                    "risk_tier": decision.tier,
                }
                # Optional hook (like ``risk_tier_for_args``): a tool may
                # summarize what the deferred action would DO so the spoken
                # confirmation question can say it in plain language.
                # Best-effort — phrasing must never block the confirm flow.
                describe = getattr(tool, "describe_args", None)
                if callable(describe):
                    try:
                        summary = describe(args)
                    except Exception as exc:  # noqa: BLE001
                        log.debug("describe_args on %r failed: %s", tool.name, exc)
                        summary = None
                    if isinstance(summary, dict) and summary:
                        sentinel_output["impact"] = {
                            str(k): str(v) for k, v in summary.items()
                        }
                return ToolResult(
                    success=False,
                    output=sentinel_output,
                    error=VOICE_CONFIRM_SENTINEL,
                )
            if surface == UNATTENDED:
                # Nobody is there to ask. The pre-authorization bridges have
                # already had their say — they answer inline while the
                # ActionApprovalRequired publish above is awaited — so a ticket
                # that is still undecided will stay undecided forever. Waiting
                # the full timeout would buy a minute of silence and then
                # report a refusal nobody made (audit GT-12).
                decided = (
                    approval_ticket.peek() if approval_ticket is not None else None
                )
                if decided is None:
                    if approval_ticket is not None:
                        approval_ticket.close()
                    return await self._approval_unavailable(
                        tool, tid, decision.tier, config_snapshot,
                    )
                approved, who_or_reason = decided
                if approval_ticket is not None:
                    approval_ticket.close()
            else:
                try:
                    approved, who_or_reason = await self._approval.wait(
                        tid, approval_timeout_s
                    )
                finally:
                    if approval_ticket is not None:
                        approval_ticket.close()
            if not approved:
                await self._bus.publish(ActionDenied(
                    trace_id=tid,
                    tool_name=tool.name,
                    reason=who_or_reason,
                ))
                # A silence is not a "no". Keep the two apart so the caller,
                # the mission broker, and the run log can each say which
                # actually happened.
                if who_or_reason == TIMEOUT_REASON:
                    error = (
                        f"{APPROVAL_TIMEOUT_PREFIX} (nobody decided within "
                        f"{approval_timeout_s:.0f}s)"
                    )
                else:
                    error = f"{APPROVAL_DENIED_PREFIX} ({who_or_reason})"
                return ToolResult(success=False, output=None, error=error)
            approved_by = who_or_reason  # "user" or "auto"

        if cancel_token is not None and cancel_token.is_cancelled():
            await self._bus.publish(ActionDenied(
                trace_id=tid,
                tool_name=tool.name,
                reason=f"cancelled: {cancel_token.reason or 'requested'}",
            ))
            return ToolResult(
                success=False,
                output=None,
                error=f"cancelled ({cancel_token.reason or 'requested'})",
            )

        # 4. Execute
        ctx = ExecutionContext(
            trace_id=tid,
            user_utterance=user_utterance,
            config=config_snapshot or {},
            memory_read=memory_read,
            approved_by=approved_by,
        )
        return await self._run_and_record(
            tool, args, ctx, cancel_token=cancel_token, t_start=t_start,
        )

    async def _refuse_invalid_args(
        self, tool: Tool, args: Any, tid: UUID,
    ) -> ToolResult | None:
        """The typed refusal for arguments that fail the tool's schema, else ``None``."""
        reason = validate_tool_args(tool, args)
        if reason is None:
            return None
        error = f"{INVALID_ARGUMENTS_PREFIX}: {reason}"
        log.info("invalid arguments for %s: %s", tool.name, reason)
        await self._bus.publish(ActionDenied(
            trace_id=tid, tool_name=tool.name, reason=error,
        ))
        return ToolResult(
            success=False,
            output={
                "outcome": "not_executed",
                "tool_name": tool.name,
                "retryable": True,
                "message": f"{reason}. Correct the arguments and call again.",
            },
            error=error,
        )

    async def _run_and_record(
        self,
        tool: Tool,
        args: dict[str, Any],
        ctx: ExecutionContext,
        *,
        cancel_token: CancelToken | None,
        t_start: float,
    ) -> ToolResult:
        """Run an approved call under its deadline and publish ``ActionExecuted``.

        Shared by :meth:`execute` and :meth:`execute_confirmed` so both paths
        bound and record a call the same way.
        """
        # Recorded before the call: a tool that raises, times out or is
        # cancelled may still have acted, so the Brain never replays the turn.
        _note_side_effect(tool, args)
        budget_s = execution_budget_s(tool, args)
        try:
            outcome = await _run_bounded(
                tool, args, ctx, budget_s=budget_s, cancel_token=cancel_token,
            )
        except Exception as exc:  # noqa: BLE001
            duration_ms = int((time.perf_counter() - t_start) * 1000)
            await self._bus.publish(ActionExecuted(
                trace_id=ctx.trace_id,
                tool_name=tool.name,
                success=False,
                duration_ms=duration_ms,
                error=redact_secrets(str(exc)),
            ))
            return ToolResult(success=False, output=None, error=str(exc))

        duration_ms = int((time.perf_counter() - t_start) * 1000)
        if isinstance(outcome, _Interrupted):
            log.warning(
                "tool %s interrupted (%s: %s) after %dms; outcome unknown",
                tool.name, outcome.kind, outcome.reason, duration_ms,
            )
            result = _interrupted_result(tool, ctx.trace_id, outcome, budget_s)
        else:
            result = outcome
        await self._bus.publish(ActionExecuted(
            trace_id=ctx.trace_id,
            tool_name=tool.name,
            success=result.success,
            duration_ms=duration_ms,
            error=redact_secrets(result.error) if result.error else result.error,
            output_preview=safe_preview(result.output),
        ))
        return result

    # ------------------------------------------------------------------
    # Two-turn voice/chat confirmation resume (turn N+1)
    # ------------------------------------------------------------------

    def has_pending_voice_confirm(self, trace_id: UUID) -> bool:
        """True while an action deferred for ``trace_id`` still awaits a yes/no."""
        return trace_id in self._pending_voice

    async def execute_confirmed(
        self,
        trace_id: UUID,
        *,
        user_utterance: str = "",
        config_snapshot: dict[str, Any] | None = None,
        memory_read: Any | None = None,
        cancel_token: CancelToken | None = None,
    ) -> ToolResult:
        """Run the action stashed by a prior voice-confirm deferral ("ja").

        Single-use: the pending entry is popped first, so a repeated "ja" cannot
        double-fire the side effect. ``approved_by="user"`` records that the human
        authorized it. The arguments are validated again (never trusting the
        first pass), and the call runs under the same execution deadline and
        cancel-token race as the normal path, publishing ``ActionExecuted``.
        """
        pending = self._pending_voice.pop(trace_id, None)
        if pending is None:
            return ToolResult(
                success=False,
                output=None,
                error="voice-confirm expired (no pending action for this turn)",
            )
        tool, args = pending
        invalid = await self._refuse_invalid_args(tool, args, trace_id)
        if invalid is not None:
            return invalid
        if cancel_token is not None and cancel_token.is_cancelled():
            await self._bus.publish(ActionDenied(
                trace_id=trace_id,
                tool_name=tool.name,
                reason=f"cancelled: {cancel_token.reason or 'requested'}",
            ))
            return ToolResult(
                success=False,
                output=None,
                error=f"cancelled ({cancel_token.reason or 'requested'})",
            )
        ctx = ExecutionContext(
            trace_id=trace_id,
            user_utterance=user_utterance,
            config=config_snapshot or {},
            memory_read=memory_read,
            approved_by="user",
        )
        return await self._run_and_record(
            tool, args, ctx, cancel_token=cancel_token, t_start=time.perf_counter(),
        )

    async def cancel_pending(self, trace_id: UUID, *, reason: str = "voice_vetoed") -> bool:
        """Drop the action stashed for ``trace_id`` ("nein"). Returns whether one
        existed. Publishes ``ActionDenied`` for the audit trail; ``reason`` says
        why, so a call that merely ended is not recorded as the user's veto."""
        pending = self._pending_voice.pop(trace_id, None)
        if pending is None:
            return False
        tool, _args = pending
        await self._bus.publish(ActionDenied(
            trace_id=trace_id,
            tool_name=tool.name,
            reason=reason,
        ))
        return True
