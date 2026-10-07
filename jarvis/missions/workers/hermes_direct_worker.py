"""HermesDirectWorker — run one mission step through the Hermes Agent CLI.

Hermes Agent (Nous Research, MIT) is an external agent with its own model
providers, skills and memory. Jarvis stays the orchestrator: the mission
supervisor plans, isolates the worktree and judges the result; Hermes is the
hands for one step, like Codex or Grok Build.

Headless invocation (``hermes --help``, one-shot mode):

    hermes -z <prompt> --usage-file <log_dir>/hermes-usage.json

``-z`` prints only the final answer on stdout and auto-approves Hermes's own
tool prompts (a one-shot run has nobody to ask), so the containment is the
mission's: a fresh worktree as the working directory and the job object that
kills the process tree. The model is whatever the user configured in Hermes
(``hermes model``); Jarvis does not pass one, because a Jarvis model id means
nothing to Hermes's provider list. ``--usage-file`` gives tokens and the cost
Hermes computed, written even when the run fails.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path
from typing import Any, ClassVar, Literal

from .capabilities import WorkerCapabilityInventory
from .process_utils import create_worker_subprocess
from .stream_consumer import ClaudeAssistantMessage, ClaudeResult, ClaudeSystemInit

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_S: float = 1200.0
_HARDCAP_GRACE_S: float = 30.0
_USAGE_FILE = "hermes-usage.json"


def resolve_hermes_binary() -> str | None:
    """The ``hermes`` launcher, with the installer's bin directory on PATH."""
    try:
        from jarvis.core.path_augment import ensure_cli_paths

        ensure_cli_paths()
    except Exception as exc:  # noqa: BLE001 — PATH augmentation is best-effort
        logger.debug("CLI PATH augmentation failed during hermes discovery: %s", exc)
    for name in ("hermes", "hermes.exe", "hermes.cmd"):
        path = shutil.which(name)
        if path:
            return path
    return None


def build_hermes_cmd(
    *, binary: str, prompt: str, usage_file: Path, model: str = ""
) -> list[str]:
    """Headless Hermes argv: one ``-z`` prompt and a usage report.

    ``model`` is the Assistant-Agents pick for Hermes (``-m``); "" keeps the
    model configured in Hermes itself. The Windows installer falls back to a
    command file when it cannot write an executable launcher, and cmd.exe ends
    an argument at a line break, so the prompt is flattened onto one line only
    for that launcher.
    """
    if binary.lower().endswith((".cmd", ".bat")):
        prompt = " ".join(prompt.split())
    model_args = ["-m", model] if model else []
    return [binary, *model_args, "-z", prompt, "--usage-file", str(usage_file)]


def _redacted_argv(cmd: list[str]) -> list[str]:
    """``cmd`` for the log, with the prompt after ``-z`` replaced."""
    out = list(cmd)
    if "-z" in out:
        index = out.index("-z") + 1
        if index < len(out):
            out[index] = "<prompt>"
    return out


def hermes_pinned_model() -> str:
    """The model picked for Hermes on the Assistant-Agents tab, else "".

    Only a ``[brain.worker].model`` that belongs to Hermes counts: the
    ``model`` a mission passes is a Jarvis id for another provider, which
    Hermes would reject, so it is never forwarded.
    """
    try:
        from jarvis.core.config import load_config
        from jarvis.missions.worker_runtime.provider_map import (
            HERMES_SUBAGENT_CANONICAL,
            pinned_worker_model,
        )

        return pinned_worker_model(load_config(), HERMES_SUBAGENT_CANONICAL)
    except Exception as exc:  # noqa: BLE001 - a broken config keeps Hermes's own model
        logger.warning("HermesDirectWorker: model pin unreadable (%s); using Hermes's own", exc)
        return ""


def read_usage(path: Path) -> dict[str, Any]:
    """Hermes's usage report, or ``{}`` when it wrote none."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # no report means no figures, never a failed run
        return {}
    return data if isinstance(data, dict) else {}


class HermesDirectWorker:
    """Heavy worker that calls ``hermes -z`` in the mission worktree.

    ``cli`` is declared ``"codex"`` so the telemetry schema needs no migration
    (the same reason as GrokBuildDirectWorker); the init event's ``model``
    carries ``hermes/<model>`` once Hermes reports it.
    """

    cli: ClassVar[Literal["claude", "codex", "python", "browser"]] = "codex"

    def __init__(
        self,
        *,
        capability_inventory: WorkerCapabilityInventory | None = None,
    ) -> None:
        self.last_pid: int | None = None
        self.last_session_id: str | None = None
        self.capability_inventory = capability_inventory or WorkerCapabilityInventory.build()

    async def spawn(
        self,
        prompt: str,
        *,
        worktree: Path,
        env: dict[str, str],
        job: Any,
        worker_id: str,
        log_dir: Path,
        model: str = "",
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        mission_id: str = "",
        _broker_binding: Any | None = None,
        **_unused: Any,
    ) -> AsyncIterator[Any]:
        del model  # a Jarvis id for another provider; see hermes_pinned_model
        broker_binding = _broker_binding
        issued_here = broker_binding is None
        if issued_here:
            broker_binding = self.capability_inventory.bind_broker(
                ttl_s=timeout_s + _HARDCAP_GRACE_S + 60.0,
                mission_id=mission_id or None,
                worker_id=worker_id,
            )
        try:
            async for event in self._spawn_bound(
                prompt,
                worktree=worktree,
                env=env,
                job=job,
                worker_id=worker_id,
                log_dir=log_dir,
                timeout_s=timeout_s,
                broker_binding=broker_binding,
            ):
                yield event
        finally:
            if issued_here and broker_binding is not None:
                try:
                    broker_binding.close()
                except Exception:  # noqa: BLE001 - cleanup must not mask cancellation
                    logger.exception("HermesDirectWorker: broker binding cleanup failed")

    async def _spawn_bound(
        self,
        prompt: str,
        *,
        worktree: Path,
        env: dict[str, str],
        job: Any,
        worker_id: str,
        log_dir: Path,
        timeout_s: float,
        broker_binding: Any | None,
    ) -> AsyncIterator[Any]:
        log_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
        usage_file = log_dir / _USAGE_FILE
        with suppress(OSError):
            usage_file.unlink()
        session_id = str(uuid.uuid4())
        self.last_session_id = session_id

        binary = resolve_hermes_binary()
        yield ClaudeSystemInit(
            session_id=session_id,
            model="hermes",
            tools=[],
            cwd=str(worktree),
            external_capabilities=self.capability_inventory.report_for(
                "hermes", binding=broker_binding
            ),
        )
        if binary is None:
            yield ClaudeResult(
                subtype="error_during_execution",
                is_error=True,
                session_id=session_id,
                duration_ms=0,
                result=(
                    "HermesDirectWorker: the hermes command was not found. Install Hermes "
                    "Agent from the Agentic IDE (or hermes-agent.nousresearch.com)."
                ),
            )
            return

        cmd = build_hermes_cmd(
            binary=binary, prompt=prompt, usage_file=usage_file, model=hermes_pinned_model()
        )
        run_env = dict(env)
        if broker_binding is not None:
            run_env = broker_binding.apply_environment(run_env)
        logger.info(
            "HermesDirectWorker[%s] spawn: cwd=%s argv=%s",
            worker_id,
            worktree,
            _redacted_argv(cmd),
        )

        t0 = time.perf_counter()
        try:
            proc = await create_worker_subprocess(
                cmd,
                cwd=str(worktree),
                env=run_env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            # Not silent: the missing binary is handed back as the run's own
            # error result, which is what the caller reports to the user.
            yield ClaudeResult(
                subtype="error_during_execution",
                is_error=True,
                session_id=session_id,
                duration_ms=int((time.perf_counter() - t0) * 1000),
                result=f"HermesDirectWorker: hermes could not start: {exc}",
            )
            return

        self.last_pid = proc.pid
        try:
            job.assign(proc.pid)
        except Exception:  # noqa: BLE001
            logger.warning(
                "HermesDirectWorker[%s]: job.assign(pid=%d) failed",
                worker_id,
                proc.pid,
                exc_info=True,
            )

        wall = timeout_s + _HARDCAP_GRACE_S
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=wall)
        except TimeoutError:
            # A one-shot run prints only at the end, so silence is normal until
            # the wall clock; past it the run is killed and reported as such.
            with suppress(ProcessLookupError, OSError):
                proc.kill()
            with suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=5.0)
            yield ClaudeResult(
                subtype="error_during_execution",
                is_error=True,
                session_id=session_id,
                duration_ms=int((time.perf_counter() - t0) * 1000),
                result=f"HermesDirectWorker: subprocess wall-clock timeout ({wall:.0f}s) exceeded",
                timed_out=True,
            )
            return

        duration_ms = int((time.perf_counter() - t0) * 1000)
        with suppress(OSError):
            (log_dir / "stdout.txt").write_bytes(stdout or b"")
            (log_dir / "stderr.log").write_bytes(stderr or b"")
        text = (stdout or b"").decode("utf-8", errors="replace").strip()
        usage = read_usage(usage_file)
        if usage.get("session_id"):
            self.last_session_id = str(usage["session_id"])
        if text:
            yield ClaudeAssistantMessage(
                message={"role": "assistant", "content": [{"type": "text", "text": text}]},
                session_id=session_id,
            )
        failed = bool(usage.get("failed")) or proc.returncode not in (None, 0)
        if failed and not text:
            detail = str(usage.get("failure") or "")
            stderr_text = (stderr or b"").decode("utf-8", errors="replace").strip()
            text = detail or stderr_text[-800:] or (
                f"HermesDirectWorker: hermes exited {proc.returncode}"
            )
        cost = usage.get("estimated_cost_usd")
        calls = usage.get("api_calls")
        yield ClaudeResult(
            subtype="error_during_execution" if failed else "success",
            is_error=failed,
            session_id=session_id,
            duration_ms=duration_ms,
            cost_usd=float(cost) if isinstance(cost, int | float) else None,
            num_turns=int(calls) if isinstance(calls, int) else None,
            result=text,
        )
