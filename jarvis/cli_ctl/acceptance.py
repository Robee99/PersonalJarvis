"""``jarvis system acceptance``: judge a release by what the running app does.

Each check drives the RUNNING app the way a person does (the front page's chat
routes for typed turns, ``/api/acceptance/spoken-turn`` for spoken ones),
reads the outcome back from somewhere other than the reply when it can
(Windows' brightness, the chat's tool events), and records how long the turn
took. Nothing is mocked and nothing is answered on the person's behalf: when
Hermes asks for approval the run says "no" and records the ask.

The checks, in order (later ones reuse the code word the first one sets):

* ``hermes``: Hermes answers one real turn, and its ``computer_use`` toolset is
  on for the API server Jarvis talks to.
* ``chat-turn``: a typed turn on the front page's chat runs on Hermes.
* ``chat-recall``: the next typed turn remembers the code word.
* ``voice-continuity``: a spoken turn remembers the code word typed in the
  chat (voice and text are one conversation).
* ``no-action``: "don't open Spotify" runs no tool.
* ``memory``: a note imported into the memory orb is found by Hermes.
* ``brightness``: a spoken brightness command lands, read back from Windows,
  within the reflex budget; the old level is restored afterwards.
* ``latency``: typed turns put their first text on screen within the
  conversational budget.

The scorecard (Markdown and JSON) is written under the user data folder; the
verdict is SHIP-READY only when nothing failed.
"""

from __future__ import annotations

import json
import secrets
import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jarvis.cli_ctl.client import ApiError

#: A reflex (brightness, volume, an app launch) answers within this.
REFLEX_BUDGET_MS = 2000
#: A conversational turn shows its first text within this.
CONVERSATION_BUDGET_MS = 5000
#: One turn may run tools or load a local model; give it room before failing.
TURN_TIMEOUT_S = 180.0
IMPORT_TIMEOUT_S = 120.0
POLL_S = 0.25
BRIGHTNESS_TARGET = 30
BRIGHTNESS_TOLERANCE = 5
#: Words Jarvis uses when Hermes asks for approval (see ``hermes.py``).
_APPROVAL_ASK = ("yes or no",)
_COLOURS = ("teal", "amber", "violet", "crimson", "olive", "indigo")

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


@dataclass
class Check:
    id: str
    title: str
    status: str
    detail: str = ""
    timings: dict[str, int | None] = field(default_factory=dict)


@dataclass
class Scorecard:
    started: str
    checks: list[Check] = field(default_factory=list)

    def add(
        self, check_id: str, title: str, status: str, detail: str = "", **timings: int | None
    ) -> Check:
        check = Check(check_id, title, status, detail, dict(timings))
        self.checks.append(check)
        return check

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.status == FAIL]

    @property
    def verdict(self) -> str:
        return "NOT-SHIP-READY" if self.failed or not self.checks else "SHIP-READY"

    def as_dict(self) -> dict[str, Any]:
        return {
            "started": self.started,
            "verdict": self.verdict,
            "checks": [c.__dict__ for c in self.checks],
        }


@dataclass
class Turn:
    reply: str
    tools: list[str]
    first_text_ms: int | None
    total_ms: int | None
    status: str
    error: str = ""

    @property
    def asked_approval(self) -> bool:
        low = self.reply.casefold()
        return any(phrase in low for phrase in _APPROVAL_ASK)


class ChatDriver:
    """Typed turns on one front-page chat, read back from its event log."""

    def __init__(
        self,
        client: Any,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client, self.sleep, self.clock = client, sleep, clock
        self.session_id = ""

    def open(self) -> dict[str, Any]:
        session = self.client.request(
            "POST",
            "/api/agent-chat/sessions",
            json={"provider": "hermes", "surface": "jarvis", "title": "Acceptance run"},
        )
        self.session_id = str(session["session_id"])
        return session

    def _events(self) -> tuple[bool, list[dict[str, Any]]]:
        data = self.client.request(
            "GET", f"/api/agent-chat/sessions/{self.session_id}", params={"tail": 300}
        )
        return bool(data["session"].get("running")), list(data.get("events") or [])

    def start(self, text: str) -> int:
        """Send ``text`` and return the event number the turn's events come after."""
        _running, before = self._events()
        floor = max((int(e.get("seq") or 0) for e in before), default=0)
        self.client.request(
            "POST", f"/api/agent-chat/sessions/{self.session_id}/messages", json={"text": text}
        )
        return floor

    def wait(self, floor: int, *, timeout_s: float = TURN_TIMEOUT_S) -> Turn:
        deadline = self.clock() + timeout_s
        while True:
            running, events = self._events()
            fresh = [e for e in events if int(e.get("seq") or 0) > floor]
            if any(e.get("kind") == "turn_finished" for e in fresh) and not running:
                return _turn_from(fresh)
            if self.clock() > deadline:
                turn = _turn_from(fresh)
                turn.status, turn.error = "timeout", f"no answer in {timeout_s:.0f}s"
                return turn
            self.sleep(POLL_S)

    def send(self, text: str) -> Turn:
        return self.wait(self.start(text))

    def stop(self) -> None:
        self.client.request("POST", f"/api/agent-chat/sessions/{self.session_id}/cancel")


def _turn_from(events: list[dict[str, Any]]) -> Turn:
    def at(kind: str | tuple[str, ...]) -> int | None:
        kinds = (kind,) if isinstance(kind, str) else kind
        return next((int(e["ts_ms"]) for e in events if e.get("kind") in kinds), None)

    started = at("turn_started")
    first = at(("text_delta", "assistant_text"))
    finished = at("turn_finished")
    texts = [
        str((e.get("payload") or {}).get("text") or "")
        for e in events
        if e.get("kind") == "assistant_text"
    ]
    end = next((e for e in events if e.get("kind") == "turn_finished"), None)
    payload = (end or {}).get("payload") or {}
    return Turn(
        reply=texts[-1].strip() if texts else "",
        tools=[
            str((e.get("payload") or {}).get("name") or "?")
            for e in events
            if e.get("kind") == "tool_call"
        ],
        first_text_ms=first - started if started is not None and first is not None else None,
        total_ms=finished - started if started is not None and finished is not None else None,
        status=str(payload.get("status") or ("finished" if end else "unfinished")),
        error=str(payload.get("error") or ""),
    )


def spoken(client: Any, text: str) -> dict[str, Any]:
    return client.request(
        "POST", "/api/acceptance/spoken-turn", json={"text": text}, timeout_s=TURN_TIMEOUT_S
    )


def _short(text: str, limit: int = 160) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _decline(chat: ChatDriver, turn: Turn) -> str:
    """Answer an approval Hermes asked for with "no", so it never hangs over the next turn."""
    if not turn.asked_approval:
        return ""
    chat.send("No.")
    return " Hermes asked for approval; the run said no."


def check_hermes(card: Scorecard, client: Any, probe: Callable[[], tuple[bool, str]]) -> bool:
    answered, detail = probe()
    if not answered:
        card.add("hermes", "Hermes answers and can use the computer", FAIL, detail)
        return False
    try:
        inventory = client.request("GET", "/api/hermes/inventory", params={"refresh": True})
    except ApiError as exc:  # recorded on the scorecard as this check's failure
        card.add("hermes", "Hermes answers and can use the computer", FAIL, f"{detail}; {exc}")
        return True
    toolsets = {
        row.get("name"): bool(row.get("enabled"))
        for row in ((inventory.get("toolsets") or {}).get("items") or [])
    }
    if toolsets.get("computer_use"):
        card.add("hermes", "Hermes answers and can use the computer", PASS, detail)
    else:
        card.add(
            "hermes",
            "Hermes answers and can use the computer",
            FAIL,
            f"{detail}; computer_use is off for Jarvis (run `jarvis system free-voice`)",
        )
    return True


def check_conversation(card: Scorecard, client: Any, chat: ChatDriver, code: str) -> list[Turn]:
    turns: list[Turn] = []
    try:
        session = chat.open()
    except ApiError as exc:  # recorded on the scorecard as this check's failure
        card.add("chat-turn", "A typed turn runs on Hermes", FAIL, f"could not open a chat: {exc}")
        return turns
    if session.get("provider") != "hermes":
        card.add(
            "chat-turn",
            "A typed turn runs on Hermes",
            FAIL,
            f"the chat opened on {session.get('provider')!r}, not Hermes",
        )
        return turns

    first = chat.send(f"Remember this code word for later: {code}. Reply with just OK.")
    turns.append(first)
    ok = first.status == "done" and bool(first.reply)
    card.add(
        "chat-turn",
        "A typed turn runs on Hermes",
        PASS if ok else FAIL,
        _short(first.reply) if ok else f"{first.status}: {first.error or 'no reply'}",
        first_text_ms=first.first_text_ms,
        total_ms=first.total_ms,
    )
    if not ok:
        return turns

    recall = chat.send("What code word did I just give you? Reply with only the word.")
    turns.append(recall)
    card.add(
        "chat-recall",
        "The next typed turn remembers it",
        PASS if code.casefold() in recall.reply.casefold() else FAIL,
        _short(recall.reply) or recall.error,
        first_text_ms=recall.first_text_ms,
        total_ms=recall.total_ms,
    )

    try:
        voice = spoken(client, "What was the code word I typed in the chat? Just the word.")
    except ApiError as exc:  # recorded on the scorecard as this check's failure
        card.add("voice-continuity", "A spoken turn remembers the typed one", FAIL, str(exc))
    else:
        reply = str(voice.get("reply") or "")
        card.add(
            "voice-continuity",
            "A spoken turn remembers the typed one",
            PASS if code.casefold() in reply.casefold() else FAIL,
            _short(reply) or "no reply",
            first_text_ms=voice.get("first_text_ms"),
            total_ms=voice.get("total_ms"),
        )

    refusal = chat.send("Don't open Spotify. Just say okay.")
    turns.append(refusal)
    acted = refusal.tools or refusal.asked_approval
    note = _decline(chat, refusal)
    card.add(
        "no-action",
        '"Don\'t open Spotify" runs nothing',
        FAIL if acted else PASS,
        (f"ran {', '.join(refusal.tools)}" if refusal.tools else _short(refusal.reply)) + note,
        total_ms=refusal.total_ms,
    )
    return turns


def _json_object(text: str) -> dict[str, Any] | None:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(text[start : end + 1])
    except ValueError:  # not JSON: the check fails on the missing object
        return None
    return value if isinstance(value, dict) else None


def check_web(card: Scorecard, chat: ChatDriver) -> None:
    """Hermes's own browser and web tools on a page whose content is known."""
    if not chat.session_id:
        return

    def ask(check_id: str, title: str, text: str, judge: Callable[[str], bool]) -> None:
        turn = chat.send(text)
        note = _decline(chat, turn)
        card.add(
            check_id,
            title,
            PASS if turn.status == "done" and judge(turn.reply) else FAIL,
            (_short(turn.reply) or turn.error or "no reply") + note,
            first_text_ms=turn.first_text_ms,
            total_ms=turn.total_ms,
        )

    def extracted(reply: str) -> bool:
        data = _json_object(reply) or {}
        return "example domain" in str(data.get("heading", "")).casefold() and bool(
            str(data.get("link_text", "")).strip()
        )

    ask(
        "web-page",
        "Reads a real web page",
        "Open https://example.com and tell me its main heading, exactly as written.",
        lambda r: "example domain" in r.casefold(),
    )
    ask(
        "web-extract",
        "Extracts structured data from a page",
        "Read https://example.com and reply with only a JSON object with the keys "
        "heading and link_text (the text of its one link).",
        extracted,
    )
    ask(
        "web-click",
        "Clicks a link in the browser",
        "In the browser, open https://example.com, click its only link, and tell me "
        "the address of the page you land on.",
        lambda r: "iana.org" in r.casefold(),
    )
    ask(
        "web-search",
        "Searches the web",
        "Search the web: which organisation maintains the example.com domain? "
        "Answer in a few words.",
        lambda r: "iana" in r.casefold() or "assigned numbers" in r.casefold(),
    )
    turn = chat.send("Don't click anything. Just say okay.")
    note = _decline(chat, turn)
    card.add(
        "no-click",
        '"Don\'t click anything" clicks nothing',
        FAIL if turn.tools or turn.asked_approval else PASS,
        (f"ran {', '.join(turn.tools)}" if turn.tools else _short(turn.reply)) + note,
        total_ms=turn.total_ms,
    )


def check_interrupt(card: Scorecard, chat: ChatDriver, code: str) -> None:
    """A long delegated task stops when told to, and the conversation carries on."""
    if not chat.session_id:
        return
    title = "A long delegated task stops when interrupted"
    floor = chat.start(
        "Use a subagent to write a 2000-word essay on the history of lighthouses, "
        "then summarise it for me."
    )
    deadline = chat.clock() + 30
    while chat.clock() < deadline:
        running, _events = chat._events()
        if running:
            break
        chat.sleep(POLL_S)
    chat.sleep(3.0)
    asked = chat.clock()
    try:
        chat.stop()
    except ApiError as exc:  # recorded on the scorecard as this check's failure
        card.add("interrupt", title, FAIL, f"stop was refused: {exc}")
        chat.wait(floor)
        return
    turn = chat.wait(floor, timeout_s=60)
    stop_ms = round((chat.clock() - asked) * 1000)
    if turn.status == "done":
        card.add("interrupt", title, SKIP, "the task finished before it could be stopped")
    else:
        card.add(
            "interrupt",
            title,
            PASS if turn.status != "timeout" and stop_ms <= CONVERSATION_BUDGET_MS else FAIL,
            f"turn ended {turn.status} {stop_ms} ms after stop",
            total_ms=stop_ms,
        )
    resume = chat.send("What was the code word from earlier? Reply with only the word.")
    card.add(
        "resume",
        "The conversation carries on after the interruption",
        PASS if code.casefold() in resume.reply.casefold() else FAIL,
        _short(resume.reply) or resume.error or "no reply",
        first_text_ms=resume.first_text_ms,
        total_ms=resume.total_ms,
    )


def check_desktop(
    card: Scorecard,
    client: Any,
    code: str,
    *,
    on_windows: bool,
    count_windows: Callable[[str], int | None],
) -> None:
    """Hermes's computer use opens and types into Notepad; vision reads it back."""
    title = "Opens Notepad and types, by voice"
    if not on_windows:
        card.add("notepad", title, SKIP, "not on Windows")
        return
    before = count_windows("notepad") or 0
    try:
        answer = spoken(client, f"Open Notepad and type the word {code}")
    except ApiError as exc:  # recorded on the scorecard as this check's failure
        card.add("notepad", title, FAIL, str(exc))
        return
    reply = str(answer.get("reply") or "")
    if any(phrase in reply.casefold() for phrase in _APPROVAL_ASK):
        spoken(client, "No.")
        card.add(
            "notepad",
            title,
            SKIP,
            "Hermes asked for approval; the run said no. Say it yourself and answer yes.",
        )
        return
    after = count_windows("notepad") or 0
    try:
        look = spoken(client, "What is on my screen right now? Tell me the word typed in Notepad.")
    except ApiError as exc:  # recorded on the scorecard as this check's failure
        look = {"reply": f"(failed: {exc})"}
    seen = str(look.get("reply") or "")
    read_back = code.casefold() in seen.casefold()
    # Windows 11 may open Notepad as a tab of a running one, so a new process is
    # not required when the screen shows the typed word.
    card.add(
        "notepad",
        title,
        PASS if after > before or read_back else FAIL,
        f"Notepad processes {before} -> {after}; reply: {_short(reply)}",
        first_text_ms=answer.get("first_text_ms"),
        total_ms=answer.get("total_ms"),
    )
    card.add(
        "screen",
        "Sees the screen (reads the typed word back)",
        PASS if read_back else FAIL,
        _short(seen) or "no reply",
        first_text_ms=look.get("first_text_ms"),
        total_ms=look.get("total_ms"),
    )


def check_memory(card: Scorecard, client: Any, chat: ChatDriver, folder: Path) -> None:
    if not chat.session_id:
        card.add("memory", "Hermes finds an imported note", SKIP, "no chat to ask in")
        return
    marker = f"JARVIS_ACCEPTANCE_{secrets.token_hex(4).upper()}"
    colour = secrets.choice(_COLOURS)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "acceptance-probe.md").write_text(
        f"# Jarvis acceptance probe\n\nMarker: {marker}\n\n"
        f"The colour recorded for this marker is {colour}.\n",
        encoding="utf-8",
    )
    try:
        job = client.request("POST", "/api/wiki/import", json={"path": str(folder)})
        deadline = chat.clock() + IMPORT_TIMEOUT_S
        while job.get("running", True) and chat.clock() < deadline:
            chat.sleep(POLL_S)
            job = client.request("GET", f"/api/wiki/import/{job['job_id']}")
    except ApiError as exc:  # recorded on the scorecard as this check's failure
        card.add("memory", "Hermes finds an imported note", FAIL, f"import failed: {exc}")
        return
    if job.get("running", True) or not (job.get("imported") or job.get("updated")):
        card.add(
            "memory",
            "Hermes finds an imported note",
            FAIL,
            f"import did not write the note (phase {job.get('phase')}, failed {job.get('failed')})",
        )
        return
    turn = chat.send(
        f"Search my memory for {marker}. What colour is recorded there? Reply with one word."
    )
    note = _decline(chat, turn)
    card.add(
        "memory",
        "Hermes finds an imported note",
        PASS if colour in turn.reply.casefold() else FAIL,
        (_short(turn.reply) or turn.error or "no reply") + note,
        first_text_ms=turn.first_text_ms,
        total_ms=turn.total_ms,
    )


def check_brightness(
    card: Scorecard,
    client: Any,
    *,
    read: Callable[[], int | None],
    write: Callable[[int], bool],
    on_windows: bool,
) -> None:
    title = "A spoken brightness command lands, read back from Windows"
    if not on_windows:
        card.add("brightness", title, SKIP, "not on Windows")
        return
    before = read()
    if before is None:
        card.add("brightness", title, SKIP, "Windows exposes no brightness for this screen")
        return
    target = BRIGHTNESS_TARGET if abs(before - BRIGHTNESS_TARGET) > BRIGHTNESS_TOLERANCE else 60
    try:
        answer = spoken(client, f"Set the brightness to {target} percent")
    except ApiError as exc:  # recorded on the scorecard as this check's failure
        card.add("brightness", title, FAIL, str(exc))
        return
    after = read()
    write(before)
    landed = after is not None and abs(after - target) <= BRIGHTNESS_TOLERANCE
    total = answer.get("total_ms")
    fast = total is not None and total <= REFLEX_BUDGET_MS
    detail = f"asked {target}%, Windows reads {after}%, reply: {_short(str(answer.get('reply')))}"
    if landed and not fast:
        detail += f"; slower than the {REFLEX_BUDGET_MS} ms reflex budget"
    card.add(
        "brightness",
        title,
        PASS if landed and fast else FAIL,
        detail,
        first_text_ms=answer.get("first_text_ms"),
        total_ms=total,
    )


def check_latency(card: Scorecard, turns: list[Turn]) -> None:
    firsts = [t.first_text_ms for t in turns if t.first_text_ms is not None]
    if not firsts:
        card.add("latency", "Typed turns show text within budget", SKIP, "no timed turns")
        return
    median = int(statistics.median(firsts))
    card.add(
        "latency",
        "Typed turns show text within budget",
        PASS if median <= CONVERSATION_BUDGET_MS else FAIL,
        f"median first text {median} ms over {len(firsts)} turns "
        f"(budget {CONVERSATION_BUDGET_MS} ms)",
        first_text_ms=median,
    )


def run_acceptance(
    client: Any,
    *,
    probe: Callable[[], tuple[bool, str]],
    probe_folder: Path,
    read_brightness: Callable[[], int | None],
    write_brightness: Callable[[int], bool],
    count_windows: Callable[[str], int | None] = lambda _name: None,
    on_windows: bool = sys.platform == "win32",
    chat: ChatDriver | None = None,
    code: str | None = None,
) -> Scorecard:
    card = Scorecard(started=datetime.now(UTC).isoformat(timespec="seconds"))
    try:
        client.request("GET", "/api/settings/voice-mode")
    except ApiError as exc:  # recorded on the scorecard as this check's failure
        card.add("jarvis", "Jarvis is running", FAIL, f"{exc}. Start it first.")
        return card
    if not check_hermes(card, client, probe):
        return card
    chat = chat or ChatDriver(client)
    code = code or f"falcon{secrets.randbelow(900) + 100}"
    turns = check_conversation(card, client, chat, code)
    check_memory(card, client, chat, probe_folder)
    check_web(card, chat)
    check_interrupt(card, chat, code)
    check_desktop(card, client, code, on_windows=on_windows, count_windows=count_windows)
    check_brightness(
        card, client, read=read_brightness, write=write_brightness, on_windows=on_windows
    )
    check_latency(card, turns)
    return card


def count_processes(name: str) -> int | None:
    """How many processes called ``name`` run on this Windows machine, else ``None``."""
    import subprocess

    from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS

    if sys.platform != "win32" or not name.isalnum():
        return None
    try:
        done = subprocess.run(
            [
                "powershell", "-NoProfile", "-NonInteractive", "-Command",
                f"(Get-Process -Name {name} -ErrorAction SilentlyContinue | Measure-Object).Count",
            ],
            capture_output=True, text=True, timeout=15, check=False,
            creationflags=NO_WINDOW_CREATIONFLAGS,
        )
    except (OSError, subprocess.SubprocessError):  # unknown count: the check relies on the screen
        return None
    out = (done.stdout or "").strip()
    return int(out) if done.returncode == 0 and out.isdigit() else None


def render_scorecard(card: Scorecard) -> str:
    lines = [
        f"# Jarvis acceptance run ({card.started})",
        "",
        f"**{card.verdict}**",
        "",
        "| Check | Result | First text | Total | Detail |",
        "|---|---|---|---|---|",
    ]
    for c in card.checks:
        first = c.timings.get("first_text_ms")
        total = c.timings.get("total_ms")
        lines.append(
            f"| {c.title} | {c.status} | "
            f"{'' if first is None else f'{first} ms'} | "
            f"{'' if total is None else f'{total} ms'} | "
            f"{c.detail.replace('|', '/')} |"
        )
    return "\n".join(lines) + "\n"


def write_scorecard(card: Scorecard, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    stamp = card.started.replace(":", "").replace("-", "")
    path = folder / f"acceptance-{stamp}.md"
    path.write_text(render_scorecard(card), encoding="utf-8")
    path.with_suffix(".json").write_text(json.dumps(card.as_dict(), indent=2), encoding="utf-8")
    return path
