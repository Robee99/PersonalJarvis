"""Weather answered from a real forecast, in English or Hinglish.

Battery 2026-10-26: "Bengaluru ka aaj ka mausam batao" got an invented
forecast (28/22 °C) with no tool call, and "What is the weather in Bengaluru
today?" got "On it, I will check the weather" and nothing more. A weather ask
is now answered here from data, or with a plain "I couldn't reach the weather
service", never from the model's imagination.

* **Sources, both free with no key.** Places are found with OpenStreetMap's
  Nominatim (one request per new place, cached in ``weather.json``); the
  forecast is MET Norway's Locationforecast (CC BY 4.0, commercial use
  allowed with attribution: "Weather data from MET Norway"). Both ask for an
  identifying User-Agent, sent below.
* **Asks.** "what's the weather in Pune today", "will it rain tomorrow in
  Mumbai", "Delhi weather", "how hot is it in Chennai", "Bengaluru ka aaj ka
  mausam batao", "kal Mumbai mein barish hogi kya", "aaj ka mausam kaisa hai".
  With no place named, the last place asked about is used; with none yet,
  Jarvis asks which city. How-to and meta questions go to the brain.
* **Reply.** The day's sky, low and high, rain, and the temperature now, in
  English, or in Hinglish when the ask was Hinglish.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

log = logging.getLogger(__name__)

FetchJson = Callable[[str], Awaitable[Any]]

USER_AGENT = "PersonalJarvis/1.0 (+https://github.com/Robee99/PersonalJarvis)"
GEOCODE_URL = (
    "https://nominatim.openstreetmap.org/search?q={q}&format=jsonv2&limit=1"
    "&addressdetails=1&accept-language=en"
)
FORECAST_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact?lat={lat}&lon={lon}"
TIMEOUT_S = 8.0

_EN_WORDS = re.compile(
    r"\b(?:weather|forecast|temperature|rain(?:ing|y)?|umbrella|humid(?:ity)?|"
    r"how\s+(?:hot|cold|warm)|is\s+it\s+(?:hot|cold|warm|sunny|cloudy))\b"
)
_HI_WORDS = re.compile(
    r"(?:\b(?:mausam|mosam|mausum|barish|baarish|baris|garmi|thand|thandi|tapman)\b"
    r"|मौसम|बारिश|तापमान|गर्मी|ठंड)"
)
_EN_OPENER = re.compile(
    r"^(?:what(?:'s|s| is| will)|how(?:'s|s| is| hot| cold| warm)|will\s+it|is\s+it|"
    r"should\s+i|do\s+i\s+need|tell\s+me|check|get|give\s+me|weather|forecast|"
    r"temperature|today'?s|tomorrow'?s|the\s+weather|any\s+rain|current|[a-z][a-z .'-]*\s+"
    r"(?:weather|forecast|temperature)\b)"
)
_META = re.compile(
    r"\b(?:how\s+does|how\s+do|why|explain|causes?|app|apps|api|code|website|channel|"
    r"song|movie|film|history|climate|search|google|youtube|news|define|meaning|"
    r"cpu|gpu|laptop|pc|computer|processor|battery|room|water|body|fever|oven)\b"
)
_POLITE = re.compile(
    r"^(?:(?:hey\s+)?jarvis[,\s]+|please\s+|can\s+you\s+|could\s+you\s+|ok(?:ay)?[,\s]+|"
    r"sir[,\s]+)+"
)
_TAIL = re.compile(r"(?:[,\s]+(?:please|thanks?|thank\s+you|jarvis|sir|ji))*[.!?\s]*$")
_DAY_EN = re.compile(
    r"\b(?:right\s+now|now|today|tonight|tomorrow|this\s+(?:morning|afternoon|evening|week)|"
    r"in\s+(?:celsius|fahrenheit)|outside|like|currently)\b"
)
_NOT_A_PLACE = {
    "here", "my area", "my city", "my location", "the city", "this city", "town", "home",
}
_NOT_PLACE_WORDS = {
    "what", "what's", "whats", "how", "how's", "hows", "is", "will", "it", "today's",
    "tomorrow's", "current", "s", "tell", "me", "check", "give", "get", "weather",
}
_HI_PARTICLES = {"ka", "ki", "ke", "mein", "me", "main", "mai", "में", "का", "की", "के"}
_HI_STOP = {
    "aaj", "aj", "kal", "abhi", "yahan", "yaha", "idhar", "mausam", "mosam", "mausum",
    "barish", "baarish", "baris", "garmi", "thand", "thandi", "tapman", "kaisa", "kaisi",
    "kya", "hai", "hoga", "hogi", "batao", "bata", "bataiye", "batana", "kitni", "kitna",
    "mera", "mere", "meri", "hamara", "humare", "shehar", "sheher", "city", "jarvis",
    "aaj ka", "ka", "ki", "ke", "mein", "me", "main", "mai", "आज", "कल", "मौसम", "बारिश",
    "कैसा", "है", "बताओ", "का", "की", "के", "में",
}

_SKY = {
    "clearsky": ("clear skies", "saaf aasmaan"),
    "fair": ("mostly clear", "zyada tar saaf"),
    "partlycloudy": ("partly cloudy", "halke baadal"),
    "cloudy": ("cloudy", "baadal"),
    "fog": ("foggy", "kohra"),
    "lightrainshowers": ("light showers", "halki bauchhaar"),
    "rainshowers": ("showers", "bauchhaar"),
    "heavyrainshowers": ("heavy showers", "tez bauchhaar"),
    "lightrain": ("light rain", "halki baarish"),
    "rain": ("rain", "baarish"),
    "heavyrain": ("heavy rain", "tez baarish"),
    "lightrainshowersandthunder": ("showers with thunder", "garaj ke saath bauchhaar"),
    "rainshowersandthunder": ("thundershowers", "garaj ke saath baarish"),
    "heavyrainshowersandthunder": ("heavy thundershowers", "garaj ke saath tez baarish"),
    "lightrainandthunder": ("light rain with thunder", "garaj ke saath halki baarish"),
    "rainandthunder": ("rain with thunder", "garaj ke saath baarish"),
    "heavyrainandthunder": ("heavy rain with thunder", "garaj ke saath tez baarish"),
    "lightsnow": ("light snow", "halki barf"),
    "snow": ("snow", "barfbaari"),
    "heavysnow": ("heavy snow", "bhaari barfbaari"),
    "sleet": ("sleet", "ole aur baarish"),
}


@dataclass(frozen=True)
class WeatherAsk:
    place: str  # "" when none was named
    day: str  # "today" or "tomorrow"
    hinglish: bool


@dataclass(frozen=True)
class Place:
    name: str
    lat: float
    lon: float


@dataclass(frozen=True)
class DayForecast:
    sky: str  # MET symbol without _day/_night, "" when unknown
    low: float
    high: float
    rain_mm: float
    now: float | None  # the temperature now, today only


def _normalise(text: str) -> str:
    phrase = " ".join((text or "").strip().lower().replace("’", "'").split())
    phrase = _POLITE.sub("", phrase)
    return _TAIL.sub("", phrase).strip(" ,")


def _clean_place(raw: str) -> str:
    place = re.sub(r"^(?:the\s+)", "", raw.strip(" ,.?!'"))
    place = re.sub(r"\s+(?:city|today|tomorrow|now)$", "", place).strip()
    if not place or place in _NOT_A_PLACE or len(place) > 40:
        return ""
    if any(word in _NOT_PLACE_WORDS for word in place.split()):
        return ""
    return place


def _english_place(phrase: str) -> str:
    rest = " ".join(_DAY_EN.sub(" ", phrase).split())
    m = re.search(r"\b(?:in|for|at|around|near)\s+(?P<place>[^\d\W][\w .'-]*)$", rest)
    if m:
        return _clean_place(m.group("place"))
    m = re.match(
        r"^(?:what(?:'s|s| is)\s+)?(?:the\s+)?(?P<place>[a-z][a-z .'-]*?)'?s?\s+"
        r"(?:weather|forecast|temperature)\b",
        rest,
    )
    if m and m.group("place") not in {"the", "today", "tomorrow", "current", "what", "how"}:
        return _clean_place(m.group("place"))
    return ""


def _hinglish_place(phrase: str) -> str:
    tokens = [t for t in re.split(r"[\s,?!.]+", phrase) if t]
    for i, token in enumerate(tokens):
        if token not in _HI_PARTICLES:
            continue
        words: list[str] = []
        j = i - 1
        while j >= 0 and tokens[j] not in _HI_STOP and len(words) < 3:
            words.insert(0, tokens[j])
            j -= 1
        if words:
            return _clean_place(" ".join(words))
    return ""


def parse_weather_ask(text: str) -> WeatherAsk | None:
    """A plain weather ask, or ``None`` for anything the brain should take."""
    phrase = _normalise(text)
    if not phrase or len(phrase.split()) > 14:
        return None
    if _META.search(phrase) or re.search(r"\b(?:don'?t|never|tha|thi|was|yesterday)\b", phrase):
        return None
    if _HI_WORDS.search(phrase):
        day = "tomorrow" if re.search(r"\bkal\b|कल", phrase) else "today"
        return WeatherAsk(_hinglish_place(phrase), day, True)
    if not _EN_WORDS.search(phrase) or not _EN_OPENER.match(phrase):
        return None
    day = "tomorrow" if "tomorrow" in phrase else "today"
    return WeatherAsk(_english_place(phrase), day, False)


# -- data -------------------------------------------------------------------


async def _http_json(url: str) -> Any:
    import httpx  # noqa: PLC0415 - lazy: only a weather ask pays for it

    async with httpx.AsyncClient(
        timeout=TIMEOUT_S, headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        follow_redirects=True,
    ) as client:
        res = await client.get(url)
        res.raise_for_status()
        return res.json()


class PlaceBook:
    """Places already looked up, and the last one asked about, in ``weather.json``."""

    def __init__(self, path: Path | None) -> None:
        self._path = path
        self._data: dict[str, Any] | None = None

    def _rows(self) -> dict[str, Any]:
        if self._data is None:
            self._data = {"last": None, "places": {}}
            if self._path is not None and self._path.exists():
                try:
                    loaded = json.loads(self._path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        self._data.update(loaded)
                except (OSError, ValueError) as exc:
                    log.warning("weather: %s unreadable, starting empty: %s", self._path, exc)
        return self._data

    def _save(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._rows(), indent=1), encoding="utf-8")
        except OSError as exc:
            log.warning("weather: could not save %s: %s", self._path, exc)

    def get(self, key: str) -> Place | None:
        row = self._rows()["places"].get(key)
        return Place(**row) if isinstance(row, dict) else None

    def put(self, key: str, place: Place) -> None:
        self._rows()["places"][key] = place.__dict__.copy()
        self._save()

    @property
    def last(self) -> Place | None:
        row = self._rows().get("last")
        return Place(**row) if isinstance(row, dict) else None

    def remember(self, place: Place) -> None:
        self._rows()["last"] = place.__dict__.copy()
        self._save()


async def find_place(name: str, fetch: FetchJson, book: PlaceBook) -> Place | None:
    key = name.lower()
    cached = book.get(key)
    if cached is not None:
        return cached
    rows = await fetch(GEOCODE_URL.format(q=quote(name)))
    if not isinstance(rows, list) or not rows:
        return None
    row = rows[0]
    address = row.get("address") or {}
    label = (
        address.get("city") or address.get("town") or address.get("village")
        or row.get("name") or name.title()
    )
    if name.title() in str(label):  # "Greater London" for "london" reads as "London"
        label = name.title()
    place = Place(str(label), round(float(row["lat"]), 4), round(float(row["lon"]), 4))
    book.put(key, place)
    return place


def summarise(payload: Any, day: date, *, now: datetime) -> DayForecast | None:
    """The day's sky, low, high and rain from a Locationforecast payload.

    Times arrive in UTC and are read in this PC's time zone, which is the
    owner's for every place he is likely to ask about.
    """
    series = (payload or {}).get("properties", {}).get("timeseries") or []
    temps: list[float] = []
    skies: Counter[str] = Counter()
    rain = 0.0
    current: float | None = None
    for row in series:
        try:
            at = datetime.fromisoformat(str(row["time"]).replace("Z", "+00:00")).astimezone()
            data = row["data"]
            temp = float(data["instant"]["details"]["air_temperature"])
        except (KeyError, TypeError, ValueError):
            continue
        if current is None and abs((at - now).total_seconds()) <= 5400:
            current = temp
        if at.date() != day:
            continue
        temps.append(temp)
        hour = data.get("next_1_hours") or {}
        if hour:
            rain += float((hour.get("details") or {}).get("precipitation_amount") or 0.0)
        else:  # past the hourly part the rows are six hours apart
            six = data.get("next_6_hours") or {}
            rain += float((six.get("details") or {}).get("precipitation_amount") or 0.0)
            hour = six
        symbol = str((hour.get("summary") or {}).get("symbol_code") or "")
        if symbol and 6 <= at.hour <= 19:
            skies[re.sub(r"_(?:day|night|polartwilight)$", "", symbol)] += 1
    if not temps:
        return None
    sky = skies.most_common(1)[0][0] if skies else ""
    today = day == now.date()
    return DayForecast(sky, min(temps), max(temps), round(rain, 1), current if today else None)


def describe(place: Place, day: str, forecast: DayForecast, *, hinglish: bool) -> str:
    low, high = round(forecast.low), round(forecast.high)
    sky_en, sky_hi = _SKY.get(forecast.sky, (forecast.sky.replace("rain", " rain").strip(), ""))
    rainy = "rain" in forecast.sky or "sleet" in forecast.sky
    if hinglish:
        when = "kal" if day == "tomorrow" else "aaj"
        parts = [f"{place.name} mein {when}: {sky_hi or sky_en}", f"{low} se {high} degree"]
        if forecast.rain_mm >= 0.5:
            parts.append(f"lagbhag {forecast.rain_mm:g} mm baarish")
        elif not rainy:
            parts.append("baarish ki ummeed nahi")
        text = ", ".join(parts) + "."
        if forecast.now is not None:
            text += f" Abhi {round(forecast.now)} degree hai."
        return text
    parts = [f"{place.name} {day}: {sky_en or 'no sky reading'}", f"{low} to {high} degrees"]
    if forecast.rain_mm >= 0.5:
        parts.append(f"about {forecast.rain_mm:g} millimetres of rain")
    elif not rainy:
        parts.append("no rain expected")
    text = ", ".join(parts) + "."
    if forecast.now is not None:
        text += f" It's {round(forecast.now)} degrees now."
    return text


_BOOK: PlaceBook | None = None


def get_book() -> PlaceBook:
    global _BOOK
    if _BOOK is None:
        from jarvis.core.paths import user_data_dir  # noqa: PLC0415

        _BOOK = PlaceBook(user_data_dir() / "data" / "weather.json")
    return _BOOK


async def weather_reflex(
    text: str,
    *,
    fetch: FetchJson | None = None,
    book: PlaceBook | None = None,
    honorific: str = "Sir",
    now: datetime | None = None,
) -> str | None:
    """Answer a weather ask from the forecast, or ``None`` when it is not one."""
    ask = parse_weather_ask(text)
    if ask is None:
        return None
    fetch = fetch or _http_json
    book = book or get_book()
    address = f", {honorific}" if honorific else ""
    try:
        place = await find_place(ask.place, fetch, book) if ask.place else book.last
        if place is None and ask.place:
            return f"I couldn't find a place called {ask.place.title()}{address}."
        if place is None:
            if ask.hinglish:
                return f"Kaunse shehar ka mausam bataun{address}?"
            return f"Which city should I check{address}?"
        payload = await fetch(FORECAST_URL.format(lat=place.lat, lon=place.lon))
    except Exception as exc:  # noqa: BLE001 — any network fault is said plainly, never guessed
        log.warning("weather: lookup failed: %s", exc)
        return f"I couldn't reach the weather service{address}, so I have no forecast."
    current = (now or datetime.now(UTC)).astimezone()
    target = current.date() + (timedelta(days=1) if ask.day == "tomorrow" else timedelta())
    forecast = summarise(payload, target, now=current)
    if forecast is None:
        return f"The weather service sent no forecast for {place.name}{address}."
    book.remember(place)
    return describe(place, ask.day, forecast, hinglish=ask.hinglish)


__all__ = [
    "DayForecast",
    "Place",
    "PlaceBook",
    "WeatherAsk",
    "describe",
    "find_place",
    "parse_weather_ask",
    "summarise",
    "weather_reflex",
]
