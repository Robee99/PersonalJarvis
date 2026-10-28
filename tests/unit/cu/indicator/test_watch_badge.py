from jarvis.core.bus import EventBus
from jarvis.cu.indicator import protocol
from jarvis.cu.indicator.controller import CUIndicatorController


def test_watch_badge_command_round_trip():
    wire = protocol.encode_command("watch", visible=True, dark=False)
    assert protocol.decode_command(wire) == {"cmd": "watch", "visible": True, "dark": False}


async def test_watch_badge_uses_existing_sidecar_without_arming_desktop_control(monkeypatch):
    ctl = CUIndicatorController(EventBus())
    calls = []
    monkeypatch.setattr(ctl, "_border_capability", lambda: (True, ""))
    monkeypatch.setattr(ctl, "_spawn_sidecar", lambda: setattr(ctl, "_proc", object()))
    monkeypatch.setattr(ctl, "_send_and_wait", lambda *args, **kw: calls.append((args, kw)) or True)
    monkeypatch.setattr(ctl, "_schedule_idle_quit", lambda: None)
    assert await ctl.watch_badge(visible=True, dark=True)
    assert calls[-1][0][0] == "watch"
    assert calls[-1][1] == {"visible": True, "dark": True}
    assert not ctl._active
    assert await ctl.watch_badge(visible=False)
    assert not calls[-1][1]["visible"]
