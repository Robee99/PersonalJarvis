from __future__ import annotations

import json

import pytest

from jarvis.agent_chat.runner_api import _tool_arguments


def test_tool_arguments_accept_objects_and_json_encoded_objects() -> None:
    expected = {"file_path": "notes.txt"}

    assert _tool_arguments(expected) is expected
    assert _tool_arguments(json.dumps(expected)) == expected
    assert _tool_arguments(None) == {}


@pytest.mark.parametrize("raw", ['{"file_path":', "[]", '"text"', 7, ["x"]])
def test_tool_arguments_reject_malformed_or_non_object_values(raw: object) -> None:
    with pytest.raises(ValueError, match="JSON object"):
        _tool_arguments(raw)
