"""Play a named video on YouTube without a model (jarvis/platform/youtube_play.py).

Live 2026-10-26 "GO TO CHROME OPEN YOUTUBE AND PLAY I MEAN IT BY G EAZY"
failed on every model route; the reflex opens the top result's watch page in
the browser the owner named.
"""

from __future__ import annotations

import pytest

from jarvis.platform.youtube_play import YouTubeAsk, parse_youtube_ask, youtube_reflex


@pytest.mark.parametrize(
    ("text", "ask"),
    [
        (
            "GO TO CHROME OPEN YOUTUBE AND PLAY I MEAN IT BY G EAZY",
            YouTubeAsk("I MEAN IT BY G EAZY", True, "chrome"),
        ),
        (
            "open my youtube in my chrome and play i mean it by g eazy",
            YouTubeAsk("i mean it by g eazy", True, "chrome"),
        ),
        ("play i mean it by g eazy on youtube", YouTubeAsk("i mean it by g eazy", True, "")),
        ("play despacito on youtube in edge", YouTubeAsk("despacito", True, "edge")),
        ("open youtube and search for lofi beats", YouTubeAsk("lofi beats", False, "")),
        ("youtube pe arijit singh ke gaane chalao", YouTubeAsk("arijit singh ke gaane", True, "")),
        # Live 2026-10-27, typed by the owner: each went through five tools and failed.
        (
            "now go to youtube and search this codeacus your local harness is nerfed and "
            "play the video",
            YouTubeAsk("codeacus your local harness is nerfed", True, ""),
        ),
        (
            "very good now go to youtube and search this codeacus your local harness is nerfed",
            YouTubeAsk("codeacus your local harness is nerfed", False, ""),
        ),
        (
            "open youtube n open this video your local ai is nerfed by codeacus",
            YouTubeAsk("your local ai is nerfed by codeacus", True, ""),
        ),
        ("go to youtube search lofi and play the first video", YouTubeAsk("lofi", True, "")),
    ],
)
def test_plain_asks_parse(text: str, ask: YouTubeAsk) -> None:
    assert parse_youtube_ask(text) == ask


@pytest.mark.parametrize(
    "text",
    [
        "play x on youtube then turn the volume up",
        "what is playing on youtube?",
        "don't play anything on youtube",
        "open youtube music and play x",
        "play the next song",
        "open youtube.com",
        "open youtube and open settings",
        "play youtube",
    ],
)
def test_everything_else_stays_with_the_brain(text: str) -> None:
    assert parse_youtube_ask(text) is None


def test_the_top_result_opens_in_the_named_browser() -> None:
    opened: list[tuple[str, str]] = []

    def opener(url: str, browser: str) -> str:
        opened.append((url, browser))
        return "Chrome"

    reply = youtube_reflex(
        "go to chrome open youtube and play i mean it by g eazy",
        lookup=lambda q: ("CxnaPa8ohmM", "G-Eazy - I Mean It (Official Music Video) ft. Remo"),
        opener=opener,
    )

    assert opened == [("https://www.youtube.com/watch?v=CxnaPa8ohmM", "chrome")]
    title = "G-Eazy - I Mean It (Official Music Video) ft. Remo"
    assert reply == f"Opened {title} on YouTube in Chrome."


def test_no_result_opens_the_search_and_says_so() -> None:
    opened: list[str] = []

    def opener(url: str, browser: str) -> str:
        opened.append(url)
        return "your browser"

    reply = youtube_reflex("play zzqx on youtube", lookup=lambda q: None, opener=opener)

    assert opened == ["https://www.youtube.com/results?search_query=zzqx"]
    assert reply is not None and "couldn't read" in reply


def test_off_windows_without_an_opener_nothing_happens() -> None:
    assert youtube_reflex("play x on youtube", platform="linux") is None
