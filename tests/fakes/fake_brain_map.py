"""Fakes for the brain map tests: a settable clock, store builders, IDE rows.

Every store is a REAL SQLite file in the test's tmp dir built from the
production schema, so the readers are exercised against the shapes they read
in the app. Nothing here touches the network or the user's data dir.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from jarvis.agent_chat.store import AgentChatStore

DAY_S = 24 * 3600.0
#: 2026-10-01 12:00 UTC — a fixed "now" so dates in reasons are stable.
T0 = 1_790_856_000.0


class FakeClock:
    def __init__(self, start: float = T0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, *, days: float = 0.0, seconds: float = 0.0) -> None:
        self.now += days * DAY_S + seconds

    @property
    def ms(self) -> int:
        return int(self.now * 1000)


class ChatFixture:
    """An agent-chat store whose events carry the fake clock's timestamps."""

    def __init__(self, data_dir: Path, clock: FakeClock) -> None:
        self.store = AgentChatStore(data_dir / "agent_chat.db")
        self.clock = clock

    def session(self, *, cwd: str = "", title: str = "", surface: str = "jarvis") -> str:
        session = self.store.create_session(
            provider="fake", model="fake", effort="", cwd=cwd, title=title, surface=surface
        )
        return session.session_id

    def say(self, session_id: str, text: str, *, role: str = "user") -> int:
        kind = "user_message" if role == "user" else "assistant_text"
        payload = {"text": text}
        if role != "user":
            payload.update(turn_id="t1", message_id=f"m-{self.clock.ms}")
        event = self.store.append_event(
            session_id, {"kind": kind, "ts_ms": self.clock.ms, "payload": payload}
        )
        return int(event["seq"])

    def close(self) -> None:
        self.store.close()


_MISSIONS_SCHEMA = (
    Path(__file__).resolve().parents[2] / "jarvis" / "missions" / "missions_schema.sql"
)


def missions_db(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.executescript(_MISSIONS_SCHEMA.read_text(encoding="utf-8"))
    return conn


def put_mission(
    conn: sqlite3.Connection, mission_id: str, prompt: str, state: str, updated_ms: int
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO missions (id, prompt, state, created_ms, updated_ms)"
        " VALUES (?, ?, ?, ?, ?)",
        (mission_id, prompt, state, updated_ms, updated_ms),
    )
    conn.commit()


@dataclass
class FakeIdeProject:
    id: str
    path: str
    name: str
    archived: bool = False
    last_opened_at: float = 0.0
    scratch: bool = False


@dataclass
class FakeIdeThread:
    id: str
    title: str
    agent: str = "claude"
    updated_at: float = 0.0
    archived: bool = False


@dataclass
class FakeIdeLibrary:
    projects: list[FakeIdeProject] = field(default_factory=list)
    threads: dict[str, list[FakeIdeThread]] = field(default_factory=dict)
    branches: dict[str, str] = field(default_factory=dict)

    def list_projects(self) -> list[FakeIdeProject]:
        return list(self.projects)

    def list_threads(self, project_id: str) -> list[FakeIdeThread]:
        return list(self.threads.get(project_id, []))

    def branch_of(self, path: str) -> str:
        return self.branches.get(path, "")
