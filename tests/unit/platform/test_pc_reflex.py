"""Volume and window reflexes: plain commands only, replies from the read-back."""

from __future__ import annotations

import pytest

from jarvis.platform import pc_reflex
from jarvis.platform.pc_reflex import (
    VolumeCommand,
    WindowCommand,
    parse_volume_command,
    parse_window_command,
)
from jarvis.plugins.tool.window_control import WindowResult


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("set the volume to 30 percent", VolumeCommand("set", 30)),
        ("volume 40", VolumeCommand("set", 40)),
        ("Jarvis, volume to 75%", VolumeCommand("set", 75)),
        ("set volume to 250", VolumeCommand("set", 100)),
        ("turn the volume up", VolumeCommand("up", 10)),
        ("turn down the volume", VolumeCommand("down", 10)),
        ("volume down", VolumeCommand("down", 10)),
        ("lower the volume by 20", VolumeCommand("down", 20)),
        ("increase volume", VolumeCommand("up", 10)),
        ("louder", VolumeCommand("up", 10)),
        ("make it quieter please", VolumeCommand("down", 10)),
        ("mute", VolumeCommand("mute")),
        ("mute the sound", VolumeCommand("mute")),
        ("unmute.", VolumeCommand("unmute")),
        ("Set my computer's volume to 35 percent.", VolumeCommand("set", 35)),
        ("turn my pc volume down", VolumeCommand("down", 10)),
        ("mute my laptop's sound", VolumeCommand("mute")),
    ],
)
def test_plain_volume_commands_match(text: str, expected: VolumeCommand) -> None:
    assert parse_volume_command(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "unmute and set volume to 50",
        "don't change the volume",
        "what is the volume?",
        "how do I turn the volume up",
        "set the spotify volume to 30",
        "mute discord",
        "set the volume to 20 when the movie starts",
        "set the brightness to 40",
        "turn the volume up and open spotify",
    ],
)
def test_volume_leaves_everything_else_to_the_brain(text: str) -> None:
    assert parse_volume_command(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("maximize notepad", WindowCommand("maximize", "notepad")),
        ("Minimise the Chrome window", WindowCommand("minimize", "chrome")),
        ("restore spotify", WindowCommand("restore", "spotify")),
        ("close notepad please", WindowCommand("close", "notepad")),
        ("bring notepad back", WindowCommand("focus", "notepad")),
        ("bring back chrome", WindowCommand("focus", "chrome")),
        ("switch to visual studio code", WindowCommand("focus", "visual studio code")),
        ("Jarvis, focus discord.", WindowCommand("focus", "discord")),
    ],
)
def test_plain_window_commands_match(text: str, expected: WindowCommand) -> None:
    assert parse_window_command(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "close it",
        "close the tab",
        "close all windows",
        "don't close notepad",
        "should I close notepad?",
        "close notepad and open chrome",
        "maximize the window",
        "close the door to the garden behind the shed",
        "open notepad",
    ],
)
def test_window_leaves_everything_else_to_the_brain(text: str) -> None:
    assert parse_window_command(text) is None


def test_window_reply_states_the_read_back(monkeypatch: pytest.MonkeyPatch) -> None:
    import jarvis.plugins.tool.window_control as wc

    monkeypatch.setattr(pc_reflex.sys, "platform", "win32")
    normal = WindowResult("Untitled - Notepad", "normal")
    monkeypatch.setattr(wc, "change", lambda action, target, **kw: normal)
    # Windows reported the window still normal: the reply says so, not "maximized".
    assert pc_reflex.window_reflex("maximize notepad") == "Notepad is open."
    maximized = WindowResult("Untitled - Notepad", "maximized")
    monkeypatch.setattr(wc, "change", lambda action, target, **kw: maximized)
    assert pc_reflex.window_reflex("maximize notepad") == "Notepad is maximized."


def test_window_not_found_goes_to_the_brain(monkeypatch: pytest.MonkeyPatch) -> None:
    import jarvis.plugins.tool.window_control as wc

    def missing(action: str, target: str, **kw: object) -> WindowResult:
        raise LookupError(target)

    monkeypatch.setattr(pc_reflex.sys, "platform", "win32")
    monkeypatch.setattr(wc, "change", missing)
    assert pc_reflex.window_reflex("close downloads") is None


def test_resizing_a_window_that_is_not_open_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    import jarvis.plugins.tool.window_control as wc

    def missing(action: str, target: str, **kw: object) -> WindowResult:
        raise LookupError(target)

    monkeypatch.setattr(pc_reflex.sys, "platform", "win32")
    monkeypatch.setattr(wc, "change", missing)
    assert pc_reflex.window_reflex("Maximize the Notepad window.") == (
        "I can't find an open Notepad window, so nothing changed."
    )
    assert pc_reflex.window_reflex("focus chat") is None


@pytest.mark.parametrize(
    "text",
    [
        "Which apps and windows are open on my desktop right now?",
        "what windows are open",
        "list the open windows",
        "show me my open apps",
        "Jarvis, which programs are running?",
    ],
)
def test_asking_which_windows_are_open_reads_them_from_windows(
    monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    import jarvis.plugins.tool.window_control as wc

    seen: list[tuple[str, str]] = []

    def listed(action: str, target: str) -> str:
        seen.append((action, target))
        return "Open windows: Calculator; Untitled - Notepad"

    monkeypatch.setattr(pc_reflex.sys, "platform", "win32")
    monkeypatch.setattr(wc, "apply", listed)
    assert pc_reflex.window_list_reflex(text) == "Open windows: Calculator; Untitled - Notepad"
    assert seen == [("list", "")]


@pytest.mark.parametrize(
    "text",
    ["which window should I close?", "what apps are open in my browser tabs", "open notepad"],
)
def test_other_window_questions_go_to_the_brain(text: str) -> None:
    assert pc_reflex.window_list_reflex(text) is None


def test_volume_reply_is_the_read_back(monkeypatch: pytest.MonkeyPatch) -> None:
    import jarvis.plugins.tool.system_volume as sv

    monkeypatch.setattr(pc_reflex.sys, "platform", "win32")
    monkeypatch.setattr(sv, "apply", lambda action, percent: f"Volume is {percent}%.")
    assert pc_reflex.volume_reflex("volume 40") == "Volume is 40%."


def test_reflexes_stand_down_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pc_reflex.sys, "platform", "linux")
    assert pc_reflex.volume_reflex("mute") is None
    assert pc_reflex.window_reflex("maximize notepad") is None


def test_close_matches_the_program_only(monkeypatch: pytest.MonkeyPatch) -> None:
    import jarvis.plugins.tool.window_control as wc

    seen: dict[str, object] = {}

    def record(action: str, target: str, **kw: object) -> WindowResult:
        seen.update(kw, action=action)
        return WindowResult("Untitled - Notepad", "closed")

    monkeypatch.setattr(pc_reflex.sys, "platform", "win32")
    monkeypatch.setattr(wc, "change", record)
    assert pc_reflex.window_reflex("close notepad") == "Notepad is closed."
    assert seen == {"action": "close", "program_only": True}
    pc_reflex.window_reflex("maximize notepad")
    assert seen["program_only"] is False


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Open YouTube dot com", "https://youtube.com"),
        ("open youtube.com", "https://youtube.com"),
        ("Yeah, open youtube dot com.", "https://youtube.com"),
        ("go to news.ycombinator.com please", "https://news.ycombinator.com"),
        ("Jarvis, open https://www.github.com/Robee99", "https://github.com/Robee99"),
        ("visit wikipedia dot org in my browser", "https://wikipedia.org"),
    ],
)
def test_named_web_addresses_match(text: str, expected: str) -> None:
    assert pc_reflex.parse_url_command(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "open youtube",  # an app or a site: the brain decides
        "don't open youtube.com",
        "open youtube.com and play lofi",
        "can you tell me what youtube.com is?",
        "open youtube.com later",
        "search youtube.com for cats",
    ],
)
def test_other_web_requests_go_to_the_brain(text: str) -> None:
    assert pc_reflex.parse_url_command(text) is None


def test_url_reflex_opens_the_browser(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str] = []
    monkeypatch.setattr(pc_reflex.sys, "platform", "win32")
    monkeypatch.setattr("os.startfile", opened.append, raising=False)
    assert pc_reflex.url_reflex("Open YouTube dot com") == "Opened youtube.com in your browser."
    assert opened == ["https://youtube.com"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("unmute and set volume to 50", [VolumeCommand("unmute"), VolumeCommand("set", 50)]),
        ("mute, then volume up", [VolumeCommand("mute"), VolumeCommand("up", 10)]),
        ("set the volume to 30", [VolumeCommand("set", 30)]),
    ],
)
def test_volume_orders_in_one_breath_all_match(text: str, expected: list) -> None:
    assert pc_reflex.parse_volume_commands(text) == expected


def test_a_mixed_utterance_goes_to_the_brain() -> None:
    assert pc_reflex.parse_volume_commands("unmute and open spotify") == []


def test_several_volume_orders_run_in_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    import jarvis.plugins.tool.system_volume as sv

    ran: list[tuple[str, int | None]] = []

    def apply(action: str, percent: int | None) -> str:
        ran.append((action, percent))
        return f"after {action}"

    monkeypatch.setattr(pc_reflex.sys, "platform", "win32")
    monkeypatch.setattr(sv, "apply", apply)
    assert pc_reflex.volume_reflex("unmute and set volume to 50") == "after set"
    assert ran == [("unmute", None), ("set", 50)]


@pytest.mark.parametrize(
    "text",
    [
        "How much free space is on my C drive?",
        "what's the free space on c:",
        "how much storage is left?",
    ],
)
def test_free_disk_space_is_read_from_the_disk(monkeypatch: pytest.MonkeyPatch, text: str) -> None:
    import shutil
    from collections import namedtuple

    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda root: usage(512 * 1024**3, 0, 81 * 1024**3))

    assert pc_reflex.system_facts_reflex(text).startswith("81 GB free of 512 GB on ")


def test_the_battery_level_is_read_from_the_machine(monkeypatch: pytest.MonkeyPatch) -> None:
    from collections import namedtuple

    import psutil

    battery = namedtuple("battery", "percent secsleft power_plugged")
    monkeypatch.setattr(psutil, "sensors_battery", lambda: battery(73.0, 0, True))

    assert pc_reflex.system_facts_reflex("What is my laptop's battery level?") == (
        "The battery is at 73 percent, charging."
    )


@pytest.mark.parametrize(
    "text",
    [
        "how do I free up space on my phone so that apps stop crashing all the time",
        "what battery should I buy for my car",
        "open the battery settings",
    ],
)
def test_other_disk_and_battery_talk_goes_to_the_brain(text: str) -> None:
    assert pc_reflex.system_facts_reflex(text) is None


# -- media reflex (live 2026-10-25: "Pause the music" went to an unconnected skill) --

from jarvis.platform.media_session import MediaSessionCapability, NowPlaying  # noqa: E402
from jarvis.platform.pc_reflex import media_reflex, parse_media_command  # noqa: E402


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Pause the music", "pause"),
        ("pause", "pause"),
        ("Jarvis, pause it please", "pause"),
        ("stop the music", "pause"),
        ("resume", "play"),
        ("resume the song", "play"),
        ("play the music again", "play"),
        ("next song", "next"),
        ("skip this track", "next"),
        ("play the next track", "next"),
        ("previous song", "previous"),
        ("go back a track", "previous"),
    ],
)
def test_plain_media_commands_match(text: str, expected: str) -> None:
    assert parse_media_command(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "stop",
        "stop it",
        "next",
        "skip",
        "play the music",
        "play lo-fi music on YouTube",
        "don't pause the music",
        "why did the music pause?",
        "pause the music and open notepad",
        "pause the music when this song ends",
    ],
)
def test_other_utterances_stay_with_the_brain(text: str) -> None:
    assert parse_media_command(text) is None


class _FakePlayer:
    def __init__(self, status: str = "playing", *, can_read: bool = True, accepts: bool = True):
        self.status = status
        self.can_read = can_read
        self.accepts = accepts
        self.calls: list[str] = []

    async def capability(self) -> MediaSessionCapability:
        return MediaSessionCapability(self.can_read, True, "fake")

    async def now_playing(self) -> NowPlaying | None:
        if self.status == "none":
            return None
        return NowPlaying("Lofi Beats", "", "", "Google Chrome", self.status)  # type: ignore[arg-type]

    async def _do(self, action: str, status: str | None) -> bool:
        self.calls.append(action)
        if self.accepts and status is not None:
            self.status = status
        return self.accepts

    async def pause(self) -> bool:
        return await self._do("pause", "paused")

    async def play(self) -> bool:
        return await self._do("play", "playing")

    async def next(self) -> bool:
        return await self._do("next", None)

    async def previous(self) -> bool:
        return await self._do("previous", None)


def test_pause_reports_what_the_session_reads_back() -> None:
    player = _FakePlayer()
    assert media_reflex("Pause the music", controller=player) == (
        "Paused Lofi Beats in Google Chrome."
    )
    assert player.calls == ["pause"]


def test_a_pause_the_player_ignored_is_not_claimed() -> None:
    player = _FakePlayer(accepts=False)
    assert media_reflex("pause", controller=player) == (
        "The player did not accept pause, so nothing changed."
    )


def test_nothing_playing_is_said_and_resume_goes_to_the_brain() -> None:
    assert media_reflex("pause the music", controller=_FakePlayer("none")) == (
        "Nothing is playing, so there is nothing to pause."
    )
    assert media_reflex("resume", controller=_FakePlayer("none")) is None


def test_media_keys_only_say_the_key_was_pressed() -> None:
    player = _FakePlayer(can_read=False)
    reply = media_reflex("next song", controller=player)
    assert reply == "I pressed the next media key; I can't read back what is playing."
    assert player.calls == ["next"]


def test_non_media_text_never_touches_the_player() -> None:
    player = _FakePlayer()
    assert media_reflex("open notepad", controller=player) is None
    assert player.calls == []


# -- machine facts, Settings pages and folders (live 2026-10-26 battery) --------

from types import SimpleNamespace  # noqa: E402


@pytest.mark.parametrize(
    ("text", "pattern"),
    [
        ("How much RAM is free on this computer right now?", "_RAM_ASK"),
        ("how much memory is in use?", "_RAM_ASK"),
        ("Which three programs are using the most memory right now?", "_TOP_MEM_ASK"),
        ("What apps use the most RAM?", "_TOP_MEM_ASK"),
        ("How long has this computer been on since the last restart?", "_UPTIME_ASK"),
        ("What's the system uptime?", "_UPTIME_ASK"),
        ("What is this computer's local IP address?", "_IP_ASK"),
        ("what's my ip", "_IP_ASK"),
        ("Which Wi-Fi network am I connected to?", "_WIFI_ASK"),
        ("What is the screen brightness right now?", "_BRIGHT_ASK"),
    ],
)
def test_machine_fact_questions_match(text: str, pattern: str) -> None:
    assert getattr(pc_reflex, pattern).search(text)


@pytest.mark.parametrize(
    "text",
    [
        "What do you remember about me?",
        "How do I free up RAM?",
        "Set the brightness to 40",
        "What is an IP address?",
    ],
)
def test_other_questions_are_not_machine_facts(text: str, monkeypatch) -> None:
    monkeypatch.setattr(pc_reflex.sys, "platform", "win32")
    monkeypatch.setattr(pc_reflex, "_local_ip", lambda: "192.168.1.5")
    monkeypatch.setattr(pc_reflex, "_wifi_name", lambda: "Home")
    assert pc_reflex.machine_facts_reflex(text) is None


def test_free_ram_is_read_from_the_machine(monkeypatch) -> None:
    import psutil

    monkeypatch.setattr(pc_reflex.sys, "platform", "win32")
    monkeypatch.setattr(
        psutil, "virtual_memory",
        lambda: SimpleNamespace(available=6 * 1024**3, total=16 * 1024**3, percent=62.5),
    )
    assert pc_reflex.machine_facts_reflex("How much RAM is free?") == (
        "6.0 GB of 16.0 GB of RAM is free (62 percent in use)."
    )


def test_wifi_and_ip_answers(monkeypatch) -> None:
    monkeypatch.setattr(pc_reflex.sys, "platform", "win32")
    monkeypatch.setattr(pc_reflex, "_local_ip", lambda: "192.168.1.5")
    monkeypatch.setattr(pc_reflex, "_wifi_name", lambda: None)
    assert pc_reflex.machine_facts_reflex("What is my local IP address?") == (
        "This computer's local IP address is 192.168.1.5."
    )
    assert pc_reflex.machine_facts_reflex("Which Wi-Fi network am I connected to?") == (
        "This computer is not connected to a Wi-Fi network right now."
    )


def test_uptime_is_spoken_in_days_hours_and_minutes() -> None:
    assert pc_reflex._duration(90061) == "1 day, 1 hour, 1 minute"
    assert pc_reflex._duration(30) == "less than a minute"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Open the Bluetooth page in Windows Settings.", ("bluetooth", "Bluetooth")),
        ("open bluetooth settings", ("bluetooth", "Bluetooth")),
        ("Open Windows Update settings", ("windowsupdate", "Windows Update")),
        ("show me the display settings", ("display", "Display")),
        ("Open settings", ("", "Settings")),
        # Live 2026-10-26: answered "Opening ... and then selecting" with no call.
        (
            "open windows pc settings go to display then advanced display",
            ("display-advanced", "Advanced display"),
        ),
        ("open settings, then display", ("display", "Display")),
        ("Open Settings and go to Bluetooth", ("bluetooth", "Bluetooth")),
        ("open advanced display settings", ("display-advanced", "Advanced display")),
    ],
)
def test_settings_pages_match(text: str, expected: tuple[str, str]) -> None:
    assert pc_reflex.parse_settings_page(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "open bluetooth",
        "open the settings for my mixer",
        "don't open settings",
        "open youtube.com",
        "open settings then turn off bluetooth",
        "open chrome settings then display",
    ],
)
def test_other_opens_are_not_settings_pages(text: str) -> None:
    assert pc_reflex.parse_settings_page(text) is None


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Open File Explorer at my Downloads folder.", "Downloads"),
        ("open my documents folder", "Personal"),
        ("Open the desktop folder in explorer", "Desktop"),
    ],
)
def test_known_folders_match(text: str, expected: str) -> None:
    assert pc_reflex.parse_folder_open(text) == expected


def test_open_music_is_not_a_folder() -> None:
    assert pc_reflex.parse_folder_open("open music") is None


def test_closing_a_named_window_that_is_not_open_says_so(monkeypatch) -> None:
    from jarvis.plugins.tool import window_control

    def _missing(*_a, **_k):
        raise LookupError("no window")

    monkeypatch.setattr(pc_reflex.sys, "platform", "win32")
    monkeypatch.setattr(window_control, "change", _missing)
    assert pc_reflex.window_reflex("Close the Settings window.") == (
        "I can't find an open Settings window, so nothing changed."
    )
    assert pc_reflex.window_reflex("close the downloads") is None


def test_what_is_playing_is_read_from_the_media_session() -> None:
    assert parse_media_command("What song is playing right now?") == "now"
    assert media_reflex("What song is playing right now?", controller=_FakePlayer()) == (
        "Lofi Beats is playing in Google Chrome."
    )
    assert media_reflex("what's playing?", controller=_FakePlayer("none")) == (
        "Nothing is playing right now."
    )
