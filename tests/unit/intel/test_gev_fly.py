"""A plain "fly me to X in God's Eye View" calls GEV's view tool (intel/gev_fly.py)."""

from __future__ import annotations

import pytest

from jarvis.intel.gev_fly import fly_reply, parse_fly_request, view_tool


@pytest.mark.parametrize(
    ("text", "place"),
    [
        ("fly me to Mumbai in God's Eye View", "Mumbai"),
        ("go to gods eye view and fly to Picnic Garden, Tiljala, Kolkata",
         "Picnic Garden, Tiljala, Kolkata"),
        ("open GEV and take me to Paharganj, Delhi", "Paharganj, Delhi"),
        ("zoom to Tokyo on the world map", "Tokyo"),
    ],
)
def test_a_plain_fly_ask_names_its_place(text: str, place: str) -> None:
    assert parse_fly_request(text) == place


@pytest.mark.parametrize(
    "text",
    [
        "take me to mumbai",
        "can you fly me to Delhi in GEV?",
        "fly me to Mumbai in GEV and then open cctv",
        "show me earthquakes in GEV",
        "open gods eye view",
    ],
)
def test_other_asks_stay_with_the_brain(text: str) -> None:
    assert parse_fly_request(text) is None


def test_the_view_tool_is_found_by_its_tail_and_the_reply_uses_gevs_label() -> None:
    tool = object()
    assert view_tool({"gods-eye-view/get_recent_launches": 1,
                      "gods-eye-view/show_in_gods_eye_view": tool}) is tool
    out = ["Open Mumbai, Maharashtra in God's Eye View: http://x", "{}", "Shown now: ..."]
    assert fly_reply("Mumbai", out) == "Flying the globe to Mumbai, Maharashtra in God's Eye View."
    assert "not open" in fly_reply("Mumbai", ["Open Mumbai in God's Eye View: http://x"])
