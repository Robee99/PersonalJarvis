"""The PC pilot: one tool that opens, clicks, types and reads back any window.

Live 2026-10-26 Roberto asked Jarvis to open Settings and click Windows
Update, to work Calculator, Notepad and Chrome. Contract under test, through
a fake desk (no Windows needed):

1. Steps run in order in the target window, never in Jarvis's own.
2. A click names what a person sees; spoken buttons map to Calculator's names.
3. A control that is not there yet is looked for again once.
4. The policy refuses password fields and asks before delete/send/pay.
5. Every call reads the window back, with a picture when it has too few names.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from jarvis.platform.win_pilot import Control, Window, key_codes
from jarvis.plugins.tool.pc_pilot import (
    PcPilot,
    PilotPolicy,
    best_control,
    _match_window,
    open_target,
    settings_uri,
)

CALC = Window(handle=1, title="Calculator", exe="ApplicationFrameHost.exe")
NOTEPAD = Window(handle=2, title="Untitled - Notepad", exe="Notepad.exe")
LOGIN = Window(handle=3, title="Windows Security", exe="CredentialUIBroker.exe")


def _button(name: str, x: int = 10, role: str = "Button") -> Control:
    return Control(name=name, role=role, bounds=(x, 10, 20, 20))


@dataclass
class FakeDesk:
    wins: list[Window]
    panes: dict[int, list[list[Control]]] = field(default_factory=dict)
    front: int = 0
    log: list[tuple[str, Any]] = field(default_factory=list)
    text: str = ""

    def windows(self) -> list[Window]:
        return list(self.wins)

    def foreground(self) -> int:
        return self.front

    def is_window(self, handle: int) -> bool:
        return any(w.handle == handle for w in self.wins)

    def activate(self, win: Window) -> bool:
        self.front = win.handle
        self.log.append(("activate", win.title))
        return True

    def window_op(self, win: Window, op: str) -> None:
        self.log.append((op, win.title))

    def controls(self, win: Window) -> list[Control]:
        frames = self.panes.get(win.handle, [[]])
        return frames.pop(0) if len(frames) > 1 else frames[0]

    def invoke(self, control: Control) -> bool:
        self.log.append(("invoke", control.name))
        return True

    def focused_text(self) -> str | None:
        return self.text

    def click(self, x: int, y: int, button: str = "left", count: int = 1) -> None:
        self.log.append(("click", (x, y, button, count)))

    def scroll(self, x: int, y: int, notches: int) -> None:
        self.log.append(("scroll", notches))

    def hover(self, x: int, y: int) -> None:
        self.log.append(("hover", (x, y)))

    def drag(self, start: tuple[int, int], end: tuple[int, int]) -> None:
        self.log.append(("drag", (start, end)))

    def set_value(self, control: Control, value: str) -> str | None:
        if control.role != "Slider":
            return None
        self.log.append(("set", (control.name, value)))
        return value

    def press(self, combo: str) -> None:
        self.log.append(("press", combo))

    def type_text(self, text: str) -> None:
        self.text += text
        self.log.append(("type", text))


@dataclass
class FakeOpenApp:
    desk: FakeDesk
    opens: Window | None = None
    asked: list[str] = field(default_factory=list)

    async def execute(self, args: dict[str, Any], ctx: Any) -> Any:
        self.asked.append(args["app_name"])
        if self.opens is not None and self.opens not in self.desk.wins:
            self.desk.wins.insert(0, self.opens)
        return type("R", (), {"success": True, "error": None})()


class Ctx:
    trace_id = None


def _pilot(desk: FakeDesk, tmp_path: Path, opens: Window | None = None) -> PcPilot:
    pictures: list[int] = []

    async def see(ctx: Any) -> dict[str, str]:
        pictures.append(1)
        return {"mime": "image/png", "data": "AAAA"}

    pilot = PcPilot(
        desk,
        open_app=FakeOpenApp(desk, opens),
        see_screen=see,
        policy=PilotPolicy(),
        audit_path=tmp_path / "audit.jsonl",
        sleep=lambda s: None,
    )
    pilot.pictures = pictures  # type: ignore[attr-defined]
    return pilot


def _calc_controls() -> list[Control]:
    names = ["Seven", "Eight", "Plus", "Equals", "Clear", "One", "Two"]
    return [_button(n, x=i * 30) for i, n in enumerate(names)] + [
        Control("Display is 15", "Text", (0, 0, 100, 20))
    ]


async def test_calculator_buttons_are_pressed_by_their_spoken_names(tmp_path: Path) -> None:
    desk = FakeDesk(wins=[], panes={CALC.handle: [_calc_controls()]})
    pilot = _pilot(desk, tmp_path, opens=CALC)

    results = [
        await pilot.execute(action, Ctx())
        for action in (
            {"open": "calculator"}, {"click": "7"}, {"click": "+"},
            {"click": "8"}, {"click": "="},
        )
    ]

    assert all(result.success for result in results), [result.error for result in results]
    assert [v for k, v in desk.log if k == "invoke"] == ["Seven", "Plus", "Eight", "Equals"]
    assert "Display is 15" in str(results[-1].output)
    assert pilot.pictures == []  # type: ignore[attr-defined]


async def test_desktop_audit_steps_carry_the_turn_trace_id(tmp_path: Path) -> None:
    desk = FakeDesk(wins=[CALC], panes={CALC.handle: [_calc_controls()]})
    pilot = _pilot(desk, tmp_path)
    ctx = Ctx()
    ctx.trace_id = "trace-browser-123"

    result = await pilot.execute({"app": "calculator", "click": "7"}, ctx)

    assert result.success, result.error
    audit = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(audit[0])["trace_id"] == "trace-browser-123"


async def test_input_goes_to_the_target_window_not_jarvis(tmp_path: Path) -> None:
    desk = FakeDesk(wins=[NOTEPAD], panes={NOTEPAD.handle: [[_button("File", role="MenuItem")]]},
                    front=999)
    pilot = _pilot(desk, tmp_path)

    result = await pilot.execute({"app": "notepad", "type": "hello"}, Ctx())

    assert result.success, result.error
    assert desk.log.index(("activate", NOTEPAD.title)) < desk.log.index(("type", "hello"))
    assert "the field now holds 'hello'" in str(result.output)


async def test_a_drag_goes_from_one_control_to_another(tmp_path: Path) -> None:
    desk = FakeDesk(wins=[CALC], panes={CALC.handle: [[_button("Card", x=0), _button("Done", x=300),
                                                       _button("A"), _button("B")]]})
    pilot = _pilot(desk, tmp_path)

    result = await pilot.execute({"app": "calculator", "drag": "Card", "to": "Done"}, Ctx())

    assert result.success, result.error
    assert ("drag", ((10, 20), (310, 20))) in desk.log


async def test_a_slider_is_set_to_a_value_not_dragged(tmp_path: Path) -> None:
    settings = Window(handle=8, title="Settings", exe="SystemSettings.exe")
    slider = Control("Brightness", "Slider", (0, 0, 200, 40))
    desk = FakeDesk(wins=[settings], panes={8: [[slider, _button("Night light"), _button("A"),
                                                 _button("B")]]})
    pilot = _pilot(desk, tmp_path)

    done = await pilot.execute({"app": "settings", "set": "brightness", "value": "60"}, Ctx())
    refused = await pilot.execute({"app": "settings", "set": "Night light", "value": "on"}, Ctx())

    assert done.success, done.error
    assert ("set", ("Brightness", "60")) in desk.log
    assert "set Brightness to 60" in str(done.output)
    assert not refused.success and "cannot be set directly" in refused.error


async def test_a_control_still_loading_is_looked_for_again(tmp_path: Path) -> None:
    later = [_button("Check for updates"), _button("Update history"), _button("Advanced options"),
             _button("Pause")]
    desk = FakeDesk(wins=[CALC], panes={CALC.handle: [[_button("Home")], later]})
    pilot = _pilot(desk, tmp_path)

    result = await pilot.execute({"app": "calculator", "click": "check for updates"}, Ctx())

    assert result.success, result.error
    assert ("invoke", "Check for updates") in desk.log


async def test_a_missing_control_fails_with_the_names_and_a_picture(tmp_path: Path) -> None:
    desk = FakeDesk(wins=[CALC], panes={CALC.handle: [_calc_controls()]})
    pilot = _pilot(desk, tmp_path)

    result = await pilot.execute({"app": "calculator", "click": "Launch rockets"}, Ctx())

    assert not result.success
    assert "Launch rockets" in str(result.error)
    assert "Seven" in result.output["description"]
    assert result.output["_image"]["mime"] == "image/png"


async def test_a_window_with_few_names_comes_back_as_a_picture(tmp_path: Path) -> None:
    game = Window(handle=7, title="Some Game", exe="game.exe")
    desk = FakeDesk(wins=[game], panes={7: [[]]})
    pilot = _pilot(desk, tmp_path)

    result = await pilot.execute({"app": "some game", "look": True}, Ctx())

    assert result.success
    assert "_image" in result.output


async def test_password_and_sign_in_windows_are_never_typed_into(tmp_path: Path) -> None:
    secret = Control("Password", "Edit", (0, 0, 50, 20), focused=True, password=True)
    desk = FakeDesk(wins=[NOTEPAD, LOGIN], panes={NOTEPAD.handle: [[secret]], LOGIN.handle: [[]]})
    pilot = _pilot(desk, tmp_path)

    in_field = await pilot.execute({"app": "notepad", "type": "hunter2"}, Ctx())
    in_login = await pilot.execute({"app": "windows security", "type": "hunter2"}, Ctx())

    assert not in_field.success and "password" in in_field.error
    assert not in_login.success and "credentials" in in_login.error
    assert not any(k == "type" for k, _ in desk.log)
    audit = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert "hunter2" not in audit and '"type_chars": 7' in audit


async def test_a_password_goes_in_only_as_a_secret_and_is_never_echoed(tmp_path: Path) -> None:
    secret = Control("Password", "Edit", (0, 0, 50, 20), focused=True, password=True)
    desk = FakeDesk(wins=[NOTEPAD], panes={NOTEPAD.handle: [[secret]]})
    pilot = _pilot(desk, tmp_path)
    desk.text = ""

    result = await pilot.execute({"app": "notepad", "type": "hunter2", "secret": True}, Ctx())

    assert result.success, result.error
    assert ("type", "hunter2") in desk.log
    assert "hunter2" not in str(result.output)
    assert PcPilot(policy=PilotPolicy()).risk_tier_for_args({"type": "x", "secret": True}) == "ask"


@pytest.mark.parametrize(
    ("args", "tier"),
    [
        ({"click": "Delete"}, "ask"),
        ({"steps": [{"click": "Send"}]}, "ask"),
        ({"app": "Chrome", "click": "Send"}, "ask"),
        ({"keys": "alt+f4"}, "ask"),
        ({"window": "close", "app": "notepad"}, "ask"),
        ({"click": "Check for updates"}, "monitor"),
        ({"type": "delete this line"}, "monitor"),
    ],
)
def test_lasting_actions_ask_first(args: dict[str, Any], tier: str) -> None:
    pilot = PcPilot(policy=PilotPolicy())
    assert pilot.risk_tier_for_args(args) == tier
    assert pilot.requires_explicit_confirmation_for_args(args) is (tier == "ask")


def test_the_owners_policy_file_only_adds_rules(tmp_path: Path) -> None:
    path = tmp_path / "pilot_policy.json"
    path.write_text(json.dumps({"ask_words": ["archive"], "deny_windows": ["bank"]}))

    policy = PilotPolicy.load(path)

    assert "archive" in policy.ask_words and "delete" in policy.ask_words
    reason = policy.refusal({"click": "x"}, Window(5, "My Bank - Chrome"), False)
    assert "keeps the pilot out" in reason


@pytest.mark.parametrize(
    ("said", "uri"),
    [
        ("settings windows update", "ms-settings:windowsupdate"),
        ("windows update", "ms-settings:windowsupdate"),
        ("open windows settings", "ms-settings:"),
        ("display settings", "ms-settings:display"),
        ("bluetooth settings", "ms-settings:bluetooth"),
        ("calculator", None),
    ],
)
def test_settings_pages(said: str, uri: str | None) -> None:
    assert settings_uri(said) == uri


def test_open_targets() -> None:
    assert open_target("youtube.com") == ("url", "https://youtube.com")
    assert open_target("https://example.com/a") == ("url", "https://example.com/a")
    assert open_target("Downloads")[0] == "path"
    assert open_target("recycle bin") == ("shell", "shell:RecycleBinFolder")
    assert open_target("notepad") == ("app", "notepad")
    assert open_target("Device Manager") == ("system", "devmgmt.msc")


def test_best_control_prefers_the_exact_name_and_skips_strangers() -> None:
    controls = [_button("Update history"), _button("Windows Update Settings"),
                _button("Windows Update")]
    assert best_control(controls, "windows update") is controls[2]
    assert best_control(controls, "bluetooth") is None


def test_key_names() -> None:
    assert key_codes("ctrl+shift+s") == [0x11, 0x10, ord("S")]
    assert key_codes("Volume Up".replace(" ", "")) == [0xAF]
    with pytest.raises(ValueError):
        key_codes("hyper+q")


TERMINAL = Window(handle=7, title=r"C:\hermes\venv\Scripts\pythonw.exe", exe="WindowsTerminal.exe")
EDGE = Window(handle=8, title="New tab - Microsoft Edge", exe="msedge.exe")
CHROME = Window(handle=9, title="YouTube - Google Chrome", exe="chrome.exe")


def test_a_page_is_worked_in_the_browser_it_went_to(tmp_path: Path) -> None:
    """Live 2026-10-27 the page went to Chrome; Edge sat open behind it."""
    desk = FakeDesk([TERMINAL, EDGE, CHROME])
    pilot = _pilot(desk, tmp_path)
    found = pilot._wait_for_window(desk, set(), "", web=True, strict=True, browser="chrome.exe")
    assert found == CHROME


async def test_open_url_reuses_last_verified_browser(tmp_path: Path, monkeypatch) -> None:
    import jarvis.plugins.tool.pc_pilot as module

    monkeypatch.setattr(module, "_PAGE_WAIT_S", 0)
    monkeypatch.setattr(module, "_OPEN_SETTLE_S", 0)
    desk = FakeDesk([TERMINAL, EDGE, CHROME], front=EDGE.handle)
    pilot = _pilot(desk, tmp_path)
    pilot._last = CHROME
    pilot._last_at = 1.0

    def unexpected_launch(*_args):
        pytest.fail("a verified current browser must be reused before the default")

    monkeypatch.setattr(module, "_launch", unexpected_launch)
    result = await pilot.execute({"open": "https://example.com"}, Ctx())

    assert result.success
    assert ("activate", CHROME.title) in desk.log
    assert ("press", "ctrl+l") in desk.log
    assert ("type", "https://example.com") in desk.log
    assert ("press", "enter") in desk.log


def test_swiggy_instamart_alias_matches_its_browser_title() -> None:
    instamart = Window(
        10, "Online Grocery Store | Instamart - Google Chrome", exe="chrome.exe"
    )
    other = Window(11, "Instamart - Google Chrome", exe="chrome.exe")

    assert _match_window([instamart], "Swiggy Instamart") == instamart
    assert _match_window([instamart, other], "Swiggy Instamart") is None
    assert _match_window(
        [instamart, other], "Swiggy Instamart", preferred_handle=other.handle
    ) == other


async def test_multiple_ui_actions_in_one_step_are_rejected(tmp_path: Path) -> None:
    desk = FakeDesk([CHROME], front=CHROME.handle)
    pilot = _pilot(desk, tmp_path)

    result = await pilot.execute(
        {"steps": [{"click": "Search", "wait": 1}]}, Ctx()
    )

    assert not result.success
    assert "exactly one UI action" in result.error
    assert desk.log == []


def test_no_browser_window_never_falls_back_to_another_program(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Live 2026-10-27 the search text was typed into a Hermes terminal."""
    import jarvis.plugins.tool.pc_pilot as pilot_module

    monkeypatch.setattr(pilot_module, "_OPEN_WAIT_S", 0.0)
    desk = FakeDesk([TERMINAL], front=TERMINAL.handle)
    pilot = _pilot(desk, tmp_path)
    assert pilot._wait_for_window(desk, set(), "", web=True, strict=True) is None


@pytest.mark.parametrize("step", [
    {"navigate": {"open": "swiggy instamart", "then": "swiggy instamart"}},
    {"type": "type", "text": "diet coke", "into": "Search"},
])
async def test_malformed_steps_are_rejected_before_any_input(tmp_path, step):
    desk = FakeDesk([NOTEPAD], front=NOTEPAD.handle)
    pilot = _pilot(desk, tmp_path)
    result = await pilot.execute({"steps": [{"click": "Search"}, step]}, Ctx())
    assert not result.success
    assert "one ui action per call" in result.error.lower()
    assert desk.log == []


async def test_two_ui_actions_inside_one_step_are_rejected_before_input(tmp_path):
    desk = FakeDesk([NOTEPAD], front=NOTEPAD.handle)
    pilot = _pilot(desk, tmp_path)

    result = await pilot.execute(
        {"steps": [{"open": "notepad", "click": "File"}]}, Ctx()
    )

    assert not result.success
    assert "multiple ui actions" in result.error.lower()
    assert desk.log == []


@pytest.mark.parametrize("new_window", [False, True])
def test_named_app_open_never_selects_unrelated_window(tmp_path, monkeypatch, new_window):
    import jarvis.plugins.tool.pc_pilot as module

    monkeypatch.setattr(module, "_OPEN_WAIT_S", 0)
    chat = Window(50, "ChatGPT", exe="ChatGPT.exe")
    desk = FakeDesk([chat], front=chat.handle)
    pilot = _pilot(desk, tmp_path)
    before = set() if new_window else {chat.handle}
    assert pilot._wait_for_window(desk, before, "chrome") is None


async def test_failed_open_does_not_describe_an_unrelated_window_as_target(tmp_path, monkeypatch):
    import jarvis.plugins.tool.pc_pilot as module

    monkeypatch.setattr(module, "_OPEN_WAIT_S", 0)
    chat = Window(50, "ChatGPT", exe="ChatGPT.exe")
    desk = FakeDesk([chat], front=chat.handle)
    pilot = _pilot(desk, tmp_path)
    result = await pilot.execute({"open": "chrome"}, Ctx())
    assert not result.success
    assert "Window: 'ChatGPT'" not in str(result.output)
    assert desk.log == []


@pytest.mark.parametrize(("name", "expected"), [
    ("chrome.exe", False),
    ("msedgewebview2.exe", True),
])
def test_only_embedded_webview_child_is_jarvis_window(monkeypatch, name, expected):
    import psutil

    from jarvis.platform.win_pilot import WinDesk

    class Child:
        def ppid(self):
            return 100

        def name(self):
            return name

    monkeypatch.setattr(psutil, "Process", lambda _pid: Child())
    desk = WinDesk.__new__(WinDesk)
    desk._own_pid = 100
    assert desk._is_own(100)
    assert desk._is_own(200) is expected
