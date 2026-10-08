"""Hermes Agent's API server for tests: runs, run events, approvals, sessions.

Plays the endpoints ``jarvis.plugins.brain.hermes`` uses, through
``httpx.MockTransport``:

* ``POST /v1/runs`` starts the next scripted run (202 + ``run_id``);
* ``GET /v1/runs/{id}/events`` streams it as Hermes does (``data:`` JSON that
  names its ``event``, ``: keepalive`` comments, closed when the run ends);
* ``POST /v1/runs/{id}/approval`` and ``/stop`` resolve or stop it;
* ``GET /api/sessions/{id}/messages`` returns the session transcript rows.

A script is an async generator ``script(server, run)`` yielding event dicts;
it can ``await run.decided.wait()`` to wait for an approval answer.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

Script = Callable[["FakeHermesApi", "FakeRun"], AsyncIterator[dict[str, Any]]]


@dataclass
class FakeRun:
    run_id: str
    body: dict[str, Any]
    headers: httpx.Headers
    script: Script
    approvals: list[dict[str, Any]] = field(default_factory=list)
    decided: asyncio.Event = field(default_factory=asyncio.Event)
    stopped: bool = False


class FakeHermesApi:
    """Answers like Hermes's API server and records every request."""

    def __init__(self, *scripts: Script) -> None:
        self.scripts = list(scripts)
        self.runs: list[FakeRun] = []
        self.session_rows: list[dict[str, Any]] = []
        self.delegations: dict[str, dict[str, Any]] = {}
        self.delegation_stops: list[str] = []
        self.reject_delegation_stop = False
        self.approval_failure_status: int | None = None

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    @property
    def approvals(self) -> list[dict[str, Any]]:
        return [a for run in self.runs for a in run.approvals]

    def _run(self, run_id: str) -> FakeRun:
        return next(r for r in self.runs if r.run_id == run_id)

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/v1/runs":
            run = FakeRun(
                run_id=f"run_{len(self.runs) + 1}",
                body=json.loads(request.content),
                headers=request.headers,
                script=self.scripts.pop(0),
            )
            self.runs.append(run)
            return httpx.Response(202, json={"run_id": run.run_id, "status": "started"})
        parts = path.strip("/").split("/")
        if parts[:3] == ["api", "jarvis", "conversations"]:
            sid = parts[3]
            records = {
                rid: row for rid, row in self.delegations.items() if row["session_id"] == sid
            }
            requested = []
            if request.method == "POST":
                if self.reject_delegation_stop:
                    return httpx.Response(503, text="private provider response")
                ids = json.loads(request.content)["delegation_ids"]
                for rid in ids:
                    row = records[rid]
                    if row["status"] == "running":
                        row["interrupt"].set()
                        row["status"] = "interrupt_requested"
                        self.delegation_stops.append(rid)
                        requested.append(rid)
            return httpx.Response(
                200,
                json={
                    "session_id": sid,
                    "requested": requested,
                    "failed": [],
                    "data": [
                        {"delegation_id": rid, "status": row["status"]}
                        for rid, row in records.items()
                    ],
                },
            )
        if parts[:2] == ["v1", "runs"] and len(parts) == 4:
            run = self._run(parts[2])
            if parts[3] == "events":
                return httpx.Response(
                    200,
                    stream=_EventStream(self, run),
                    headers={"content-type": "text/event-stream"},
                )
            if parts[3] == "approval":
                if self.approval_failure_status is not None:
                    code = (
                        "approval_not_pending"
                        if self.approval_failure_status == 409
                        else "unavailable"
                    )
                    return httpx.Response(
                        self.approval_failure_status,
                        json={
                            "error": {"code": code, "message": "api_key=private-provider-fixture"},
                        },
                    )
                run.approvals.append(json.loads(request.content))
                run.decided.set()
                return httpx.Response(200, json={"resolved": 1})
            if parts[3] == "stop":
                run.stopped = True
                return httpx.Response(200, json={"status": "stopping"})
        if parts[:2] == ["api", "sessions"] and parts[-1] == "messages":
            return httpx.Response(200, json={"data": list(self.session_rows)})
        raise AssertionError(f"unexpected {request.method} {path}")


class _EventStream(httpx.AsyncByteStream):
    def __init__(self, server: FakeHermesApi, run: FakeRun) -> None:
        self._server = server
        self._run = run

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b": keepalive\n\n"
        async for event in self._run.script(self._server, self._run):
            event = {"run_id": self._run.run_id, **event}
            yield f"data: {json.dumps(event)}\n\n".encode()
        yield b": stream closed\n\n"


def say(text: str, *, runtime: dict[str, str] | None = None, tools: tuple[str, ...] = ()) -> Script:
    """A run that starts ``tools``, streams ``text`` and completes."""

    async def script(_server: FakeHermesApi, _run: FakeRun) -> AsyncIterator[dict[str, Any]]:
        for tool in tools:
            yield {"event": "tool.started", "tool": tool, "preview": ""}
            yield {"event": "tool.completed", "tool": tool, "duration": 0.1, "error": False}
        words = text.split(" ")
        for i, word in enumerate(words):
            yield {"event": "message.delta", "delta": word + (" " if i < len(words) - 1 else "")}
        yield {
            "event": "run.completed",
            "output": text,
            "usage": {"input_tokens": 40, "output_tokens": 3},
            "runtime": runtime or {"provider": "nous", "model": "stepfun/step-3.7-flash:free"},
        }

    return script


def asks_approval(after_yes: str, after_no: str, *, description: str = "delete a file") -> Script:
    """A run that parks on an approval, then says what came of the answer."""

    async def script(_server: FakeHermesApi, run: FakeRun) -> AsyncIterator[dict[str, Any]]:
        yield {
            "event": "approval.request",
            "request_id": "req-7",
            "description": description,
            "command": "del C:\\notes.txt",
            "choices": ["once", "session", "deny"],
        }
        await run.decided.wait()
        if run.approvals[-1]["choice"] == "once":
            yield {"event": "tool.started", "tool": "terminal", "preview": "del"}
            yield {"event": "tool.completed", "tool": "terminal", "duration": 0.1, "error": False}
            text = after_yes
        else:
            text = after_no
        yield {"event": "message.delta", "delta": text}
        yield {"event": "run.completed", "output": text}

    return script
