"""``[voice_engine]`` writes: the model API and server root beside the voice."""

from __future__ import annotations

import tomllib

import pytest

from jarvis.core.config_writer import set_voice_engine_settings


def _section(path) -> dict:
    return tomllib.loads(path.read_text(encoding="utf-8"))["voice_engine"]


def test_api_and_server_land_in_one_write(tmp_path) -> None:
    path = tmp_path / "jarvis.toml"
    set_voice_engine_settings(
        tts="piper", llm_api="OpenAI", llm_base_url=" http://127.0.0.1:11435 ", path=path
    )
    assert _section(path) == {
        "tts": "piper",
        "llm_api": "openai",
        "llm_base_url": "http://127.0.0.1:11435",
    }
    set_voice_engine_settings(llm_base_url="", path=path)
    assert _section(path)["llm_base_url"] == ""


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [({"llm_api": "grpc"}, "model API"), ({"llm_base_url": "ftp://x"}, "http://")],
)
def test_unknown_values_are_refused(tmp_path, kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        set_voice_engine_settings(path=tmp_path / "jarvis.toml", **kwargs)
