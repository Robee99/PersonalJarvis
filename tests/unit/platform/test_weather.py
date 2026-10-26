"""Weather from a real forecast (jarvis/platform/weather.py).

Battery 2026-10-26: "Bengaluru ka aaj ka mausam batao" got an invented
forecast with no tool call. Contract:

1. English and Hinglish weather asks are recognised; how-to, search and
   machine-temperature asks are not.
2. The reply is built only from the forecast payload.
3. A lookup that fails is said plainly, never answered from nothing.
4. The place is looked up once and remembered for "aaj ka mausam".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from jarvis.platform.weather import (
    PlaceBook,
    parse_weather_ask,
    summarise,
    weather_reflex,
)

NOW = datetime(2026, 10, 26, 6, 0, tzinfo=UTC)


def _payload(start: datetime, hours: int, temp0: float = 20.0) -> dict[str, Any]:
    rows = []
    for h in range(hours):
        at = start + timedelta(hours=h)
        rain = 1.0 if h in (8, 9) else 0.0
        rows.append(
            {
                "time": at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "data": {
                    "instant": {"details": {"air_temperature": temp0 + (h % 24) / 3}},
                    "next_1_hours": {
                        "summary": {"symbol_code": "partlycloudy_day"},
                        "details": {"precipitation_amount": rain},
                    },
                },
            }
        )
    return {"properties": {"timeseries": rows}}


class _Net:
    """A fake fetcher: Nominatim rows, then a forecast."""

    def __init__(self, *, places: list[Any] | None = None, fail: bool = False) -> None:
        self.urls: list[str] = []
        self.places = [{"lat": "12.9767936", "lon": "77.5900820", "name": "Bengaluru",
                        "address": {"city": "Bengaluru"}}] if places is None else places
        self.fail = fail

    async def __call__(self, url: str) -> Any:
        self.urls.append(url)
        if self.fail:
            raise OSError("network is unreachable")
        if "nominatim" in url:
            return self.places
        midnight = NOW.astimezone().replace(hour=0, minute=0, second=0, microsecond=0)
        return _payload(midnight.astimezone(UTC), 60)


@pytest.mark.parametrize(
    ("ask", "place", "day", "hinglish"),
    [
        ("What is the weather in Bengaluru today?", "bengaluru", "today", False),
        ("Bengaluru ka aaj ka mausam batao.", "bengaluru", "today", True),
        ("aaj ka mausam kaisa hai", "", "today", True),
        ("kal Mumbai mein barish hogi kya", "mumbai", "tomorrow", True),
        ("will it rain tomorrow in Mumbai", "mumbai", "tomorrow", False),
        ("Delhi weather", "delhi", "today", False),
        ("what's new delhi's weather", "new delhi", "today", False),
        ("how hot is it in Chennai", "chennai", "today", False),
        ("what's the weather like", "", "today", False),
        ("tomorrow's forecast for pune", "pune", "tomorrow", False),
    ],
)
def test_weather_asks_are_read(ask: str, place: str, day: str, hinglish: bool) -> None:
    parsed = parse_weather_ask(ask)
    assert parsed is not None
    assert (parsed.place, parsed.day, parsed.hinglish) == (place, day, hinglish)


@pytest.mark.parametrize(
    "ask",
    [
        "Search the web for today's weather in Bengaluru.",
        "what is the temperature of the cpu",
        "how does the weather work",
        "play rain sounds",
        "what was the weather yesterday",
        "set the volume to 30",
    ],
)
def test_other_asks_reach_the_brain(ask: str) -> None:
    assert parse_weather_ask(ask) is None


def test_the_day_is_summarised_from_the_payload() -> None:
    now = NOW.astimezone()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    payload = _payload(midnight.astimezone(UTC), 48)
    today = summarise(payload, now.date(), now=now)
    assert today is not None
    assert today.sky == "partlycloudy"
    assert today.rain_mm == 2.0
    assert today.low == pytest.approx(20.0)
    assert today.high == pytest.approx(20.0 + 23 / 3)
    assert today.now is not None
    tomorrow = summarise(payload, now.date() + timedelta(days=1), now=now)
    assert tomorrow is not None and tomorrow.now is None


async def test_an_english_ask_is_answered_from_data(tmp_path: Path) -> None:
    net = _Net()
    book = PlaceBook(tmp_path / "weather.json")
    reply = await weather_reflex("What is the weather in Bengaluru today?", fetch=net,
                                 book=book, now=NOW)
    assert reply is not None
    assert reply.startswith("Bengaluru today: partly cloudy, 20 to 28 degrees, ")
    assert "about 2 millimetres of rain" in reply and "degrees now." in reply
    assert "lat=12.9768&lon=77.5901" in net.urls[-1]


async def test_a_hinglish_ask_gets_a_hinglish_reply_and_the_place_is_kept(
    tmp_path: Path,
) -> None:
    net = _Net()
    book = PlaceBook(tmp_path / "weather.json")
    reply = await weather_reflex("Bengaluru ka aaj ka mausam batao", fetch=net, book=book,
                                 now=NOW)
    assert reply is not None and reply.startswith("Bengaluru mein aaj: halke baadal, 20 se 28")
    assert "Abhi " in reply

    again = PlaceBook(tmp_path / "weather.json")
    net2 = _Net()
    reply2 = await weather_reflex("kal ka mausam kaisa hai", fetch=net2, book=again, now=NOW)
    assert reply2 is not None and reply2.startswith("Bengaluru mein kal: ")
    assert not any("nominatim" in url for url in net2.urls)


async def test_a_failed_lookup_is_said_plainly(tmp_path: Path) -> None:
    reply = await weather_reflex("weather in Pune", fetch=_Net(fail=True),
                                 book=PlaceBook(tmp_path / "w.json"), now=NOW)
    assert reply == "I couldn't reach the weather service, Sir, so I have no forecast."


async def test_an_unknown_place_and_no_place(tmp_path: Path) -> None:
    book = PlaceBook(tmp_path / "w.json")
    reply = await weather_reflex("weather in Zzyzxville", fetch=_Net(places=[]), book=book,
                                 now=NOW)
    assert reply == "I couldn't find a place called Zzyzxville, Sir."
    assert await weather_reflex("what's the weather like", fetch=_Net(), book=book,
                                now=NOW) == "Which city should I check, Sir?"
    assert await weather_reflex("aaj ka mausam kaisa hai", fetch=_Net(), book=book,
                                now=NOW) == "Kaunse shehar ka mausam bataun, Sir?"


async def test_a_wider_area_name_reads_as_the_asked_place(tmp_path: Path) -> None:
    net = _Net(places=[{"lat": "51.5", "lon": "-0.12", "name": "Greater London", "address": {}}])
    reply = await weather_reflex("weather in london", fetch=net,
                                 book=PlaceBook(tmp_path / "w.json"), now=NOW)
    assert reply is not None and reply.startswith("London today: ")
