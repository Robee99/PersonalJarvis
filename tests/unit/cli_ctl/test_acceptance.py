"""``jarvis system acceptance``: each check is judged from what the app did, not from a mock."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from jarvis.cli_ctl import acceptance as acc
from jarvis.cli_ctl.client import ApiError


class FakeApp:
    """The routes the run drives, answering like a Jarvis whose brain is Hermes."""

    def __init__(
        self,
        *,
        provider: str = "hermes",
        remembers: bool = True,
        opens_spotify: bool = False,
        computer_use: bool = True,
        first_text_ms: int = 900,
    ) -> None:
        self.provider, self.remembers = provider, remembers
        self.opens_spotify, self.computer_use = opens_spotify, computer_use
        self.first_text_ms = first_text_ms
        self.events: list[dict[str, Any]] = []
        self.code = ""
        self.imported: dict[str, str] = {}
        self.sent: list[str] = []
        self.brightness = 70
        self.notepads = 1
        self.typed = ""
        self.running = False
        self.stopped_after_s: float | None = None

    def _emit(self, kind: str, ts: int, **payload: Any) -> None:
        self.events.append(
            {"seq": len(self.events) + 1, "ts_ms": ts, "kind": kind, "payload": payload}
        )

    def _answer(self, text: str) -> tuple[str, list[str]]:
        found = re.search(r"code word for later: (\w+)", text)
        if found:
            self.code = found.group(1)
            return "OK", []
        if "code word" in text:
            return (self.code if self.remembers else "I don't know."), []
        if "example.com" in text and "JSON" in text:
            return '```json\n{"heading": "Example Domain", "link_text": "Learn more"}\n```', []
        if "example.com" in text and "click" in text:
            return "I landed on https://www.iana.org/help/example-domains", ["browser_click"]
        if "example.com" in text and "organisation" in text:
            return "IANA maintains it.", ["web_search"]
        if "example.com" in text:
            return "Example Domain", ["browser_navigate"]
        if text.startswith("Don't click"):
            return "Okay.", []
        if text.startswith("Open Notepad"):
            self.notepads += 1
            self.typed = text.rsplit(" ", 1)[-1]
            return "Done.", []
        if "on my screen" in text:
            return f"Notepad shows the word {self.typed}.", []
        if "Spotify" in text:
            return ("Opened Spotify.", ["open-app"]) if self.opens_spotify else ("Okay.", [])
        marker = re.search(r"JARVIS_ACCEPTANCE_\w+", text)
        if marker:
            note = next((v for v in self.imported.values() if marker.group(0) in v), "")
            colour = re.search(r"is (\w+)\.", note)
            return (colour.group(1).capitalize() if colour else "Nothing found."), []
        return "Okay.", []

    def request(self, method: str, path: str, **kw: Any) -> Any:
        body = kw.get("json") or {}
        if path == "/api/settings/voice-mode":
            return {"mode": "pipeline"}
        if path == "/api/hermes/inventory":
            return {"toolsets": {"items": [{"name": "computer_use", "enabled": self.computer_use}]}}
        if method == "POST" and path == "/api/agent-chat/sessions":
            return {"session_id": "s1", "provider": self.provider, "running": False}
        if method == "GET" and path == "/api/agent-chat/sessions/s1":
            return {"session": {"running": self.running}, "events": list(self.events)}
        if path == "/api/agent-chat/sessions/s1/cancel":
            self.running = False
            t = len(self.events) * 10_000
            self._emit("turn_finished", t, turn_id="t", status="cancelled")
            return {"cancelled": True}
        if path == "/api/agent-chat/sessions/s1/messages":
            self.sent.append(body["text"])
            if "lighthouses" in body["text"]:
                self.running = True
                self._emit("turn_started", len(self.events) * 10_000, turn_id="t")
                return {"turn_id": "t"}
            reply, tools = self._answer(body["text"])
            t = len(self.events) * 10_000
            self._emit("turn_started", t, turn_id="t")
            for name in tools:
                self._emit("tool_call", t + 100, name=name)
            self._emit("text_delta", t + self.first_text_ms, text=reply)
            self._emit("assistant_text", t + self.first_text_ms + 50, text=reply)
            self._emit("turn_finished", t + self.first_text_ms + 100, turn_id="t", status="done")
            return {"turn_id": "t"}
        if path == "/api/acceptance/spoken-turn":
            text = body["text"]
            level = re.search(r"brightness to (\d+)", text)
            if level:
                self.brightness = int(level.group(1))
                return {"reply": f"Brightness is now {self.brightness}%.", "first_text_ms": 300,
                        "total_ms": 400}
            reply, _tools = self._answer(text)
            return {"reply": reply, "first_text_ms": 800, "total_ms": 1500}
        if method == "POST" and path == "/api/wiki/import":
            folder = Path(body["path"])
            for f in folder.iterdir():
                self.imported[f.name] = f.read_text(encoding="utf-8")
            return {"job_id": "j1", "running": False, "imported": 1, "updated": 0}
        raise AssertionError(f"unexpected {method} {path}")


def _run(app: FakeApp, tmp_path: Path, *, on_windows: bool = True) -> acc.Scorecard:
    restored: list[int] = []

    def write(level: int) -> bool:
        restored.append(level)
        app.brightness = level
        return True

    card = acc.run_acceptance(
        app,
        probe=lambda: (True, "answered in 1.0s on local/qwen"),
        probe_folder=tmp_path / "probe",
        read_brightness=lambda: app.brightness,
        write_brightness=write,
        count_windows=lambda _name: app.notepads,
        on_windows=on_windows,
        chat=acc.ChatDriver(app, sleep=lambda _s: None),
        code="falcon123",
    )
    card.restored = restored  # type: ignore[attr-defined]
    return card


def _status(card: acc.Scorecard) -> dict[str, str]:
    return {c.id: c.status for c in card.checks}


def test_a_healthy_app_is_ship_ready(tmp_path: Path) -> None:
    app = FakeApp()
    card = _run(app, tmp_path)
    assert _status(card) == {
        "hermes": "PASS",
        "chat-turn": "PASS",
        "chat-recall": "PASS",
        "voice-continuity": "PASS",
        "no-action": "PASS",
        "memory": "PASS",
        "web-page": "PASS",
        "web-extract": "PASS",
        "web-click": "PASS",
        "web-search": "PASS",
        "no-click": "PASS",
        "interrupt": "PASS",
        "resume": "PASS",
        "notepad": "PASS",
        "screen": "PASS",
        "brightness": "PASS",
        "latency": "PASS",
    }
    assert card.verdict == "SHIP-READY"
    chat = next(c for c in card.checks if c.id == "chat-turn")
    assert chat.timings == {"first_text_ms": 900, "total_ms": 1000}


def test_voice_that_forgets_the_typed_turn_fails(tmp_path: Path) -> None:
    app = FakeApp(remembers=False)
    card = _run(app, tmp_path)
    assert _status(card)["chat-recall"] == "FAIL"
    assert _status(card)["voice-continuity"] == "FAIL"
    assert card.verdict == "NOT-SHIP-READY"


def test_a_tool_run_on_a_negated_command_fails(tmp_path: Path) -> None:
    card = _run(FakeApp(opens_spotify=True), tmp_path)
    check = next(c for c in card.checks if c.id == "no-action")
    assert check.status == "FAIL" and "open-app" in check.detail


def test_a_chat_that_opens_on_another_seat_fails(tmp_path: Path) -> None:
    card = _run(FakeApp(provider="openai"), tmp_path)
    assert _status(card)["chat-turn"] == "FAIL"
    assert "openai" in card.checks[1].detail


def test_computer_use_off_fails_and_says_how_to_fix_it(tmp_path: Path) -> None:
    card = _run(FakeApp(computer_use=False), tmp_path)
    assert card.checks[0].status == "FAIL"
    assert "free-voice" in card.checks[0].detail


def test_slow_first_text_fails_the_latency_check(tmp_path: Path) -> None:
    card = _run(FakeApp(first_text_ms=8000), tmp_path)
    assert _status(card)["latency"] == "FAIL"
    assert _status(card)["chat-recall"] == "PASS"


def test_brightness_is_read_back_and_restored(tmp_path: Path) -> None:
    app = FakeApp()
    card = _run(app, tmp_path)
    check = next(c for c in card.checks if c.id == "brightness")
    assert "Windows reads 30%" in check.detail
    assert card.restored == [70]  # type: ignore[attr-defined]
    assert app.brightness == 70


def test_brightness_is_skipped_off_windows(tmp_path: Path) -> None:
    card = _run(FakeApp(), tmp_path, on_windows=False)
    assert _status(card)["brightness"] == "SKIP"
    assert card.verdict == "SHIP-READY"


def test_an_unreachable_app_stops_the_run(tmp_path: Path) -> None:
    class Down:
        def request(self, *_a: Any, **_k: Any) -> Any:
            raise ApiError("Jarvis at http://127.0.0.1:47821 is unreachable.", None)

    card = acc.run_acceptance(
        Down(),
        probe=lambda: (True, ""),
        probe_folder=tmp_path,
        read_brightness=lambda: None,
        write_brightness=lambda _l: True,
    )
    assert [c.id for c in card.checks] == ["jarvis"]
    assert card.verdict == "NOT-SHIP-READY"


def test_an_approval_ask_is_declined_not_granted(tmp_path: Path) -> None:
    app = FakeApp()
    original = app._answer

    def asks(text: str) -> tuple[str, list[str]]:
        if "Spotify" in text:
            return "Open Spotify? Say yes or no.", []
        return original(text)

    app._answer = asks  # type: ignore[method-assign]
    card = _run(app, tmp_path)
    check = next(c for c in card.checks if c.id == "no-action")
    assert check.status == "FAIL" and "said no" in check.detail
    assert "No." in app.sent and "Yes." not in app.sent


def test_the_scorecard_is_written_as_markdown_and_json(tmp_path: Path) -> None:
    card = _run(FakeApp(), tmp_path)
    path = acc.write_scorecard(card, tmp_path / "out")
    assert "**SHIP-READY**" in path.read_text(encoding="utf-8")
    assert '"verdict": "SHIP-READY"' in path.with_suffix(".json").read_text(encoding="utf-8")


def test_a_task_that_ignores_the_stop_fails(tmp_path: Path) -> None:
    app = FakeApp()
    original = app.request

    def stubborn(method: str, path: str, **kw: Any) -> Any:
        if path.endswith("/cancel"):
            return {"cancelled": False}
        return original(method, path, **kw)

    app.request = stubborn  # type: ignore[method-assign]
    clock = iter(range(0, 10_000, 1))
    chat = acc.ChatDriver(app, sleep=lambda _s: None, clock=lambda: float(next(clock)))
    card = acc.run_acceptance(
        app,
        probe=lambda: (True, ""),
        probe_folder=tmp_path / "probe",
        read_brightness=lambda: None,
        write_brightness=lambda _l: True,
        on_windows=False,
        chat=chat,
        code="falcon123",
    )
    check = next(c for c in card.checks if c.id == "interrupt")
    assert check.status == "FAIL"


def test_a_screen_that_cannot_read_the_typed_word_fails(tmp_path: Path) -> None:
    app = FakeApp()
    original = app._answer

    def blind(text: str) -> tuple[str, list[str]]:
        if "on my screen" in text:
            return "I can't see your screen.", []
        return original(text)

    app._answer = blind  # type: ignore[method-assign]
    card = _run(app, tmp_path)
    assert _status(card)["screen"] == "FAIL"
    assert _status(card)["notepad"] == "PASS"
