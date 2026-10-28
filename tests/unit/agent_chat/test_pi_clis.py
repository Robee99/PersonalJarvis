"""Pi and Oh My Pi, end to end: the pane, the chat seat, the session and the bill.

Both CLIs share one session format and one JSON stream (Oh My Pi is a fork of
Pi), so one file pins both. Every record below was captured from the installed
binaries (pi 1.4.0, omp 18.12.6, 2026-10-26) running against a local
OpenAI-compatible server, then trimmed.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from jarvis.agent_chat import permissions
from jarvis.agent_chat import runner_cli as rc
from jarvis.agentic_ide import agent_sessions, intent
from jarvis.costs import cli_usage_index as index
from jarvis.workspace import agents as workspace_agents

_SID = "01a177d2-09b3-768e-8089-9f1c2f37f57d"
_CWD = "/work/pi-app"

_HEADER = {
    "type": "session",
    "version": 3,
    "id": _SID,
    "timestamp": "2026-10-26T10:46:56.179Z",
    "cwd": _CWD,
}


def _assistant(content: list, *, usage_in: int, out: int, stop: str, response: str = "") -> dict:
    message: dict = {
        "role": "assistant",
        "content": content,
        "api": "openai-completions",
        "provider": "jarvis",
        "model": "gemma-4b-qat",
        "usage": {
            "input": usage_in,
            "output": out,
            "cacheRead": 3,
            "cacheWrite": 0,
            "reasoning": 0,
            "totalTokens": usage_in + out + 3,
            "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "total": 0},
        },
        "stopReason": stop,
        "timestamp": 1793011616287,
    }
    if response:
        message["responseId"] = response
    return message


_TOOL_CALL = {
    "type": "toolCall",
    "id": "call_1",
    "name": "bash",
    "arguments": {"command": "echo hi"},
}
_USER = {"role": "user", "content": [{"type": "text", "text": "say hi"}]}
_ANSWER = [
    {"type": "thinking", "thinking": "It printed hi."},
    {"type": "text", "text": "The command printed hi."},
]

_CALLED = _assistant([_TOOL_CALL], usage_in=120, out=9, stop="toolUse")

# ``pi -p --mode json`` for "say hi with bash", in order (deltas trimmed).
_STREAM = [
    _HEADER,
    {"type": "agent_start"},
    {"type": "turn_start"},
    {"type": "message_end", "message": _USER},
    {"type": "message_update", "assistantMessageEvent": {"type": "toolcall_start"}},
    {"type": "message_end", "message": _CALLED},
    {
        "type": "tool_execution_start",
        "toolCallId": "call_1",
        "toolName": "bash",
        "args": {"command": "echo hi"},
    },
    {
        "type": "tool_execution_end",
        "toolCallId": "call_1",
        "toolName": "bash",
        "result": {"content": [{"type": "text", "text": "hi\n"}]},
        "isError": False,
        "durationMs": 12,
    },
    {"type": "turn_end", "message": _CALLED},
    {
        "type": "message_end",
        "message": _assistant(_ANSWER, usage_in=150, out=5, stop="stop"),
    },
    {"type": "agent_end", "messages": []},
    {"type": "agent_settled", "aborted": False},
]


# ---------------------------------------------------------------- the pane


def test_both_panes_start_on_the_local_model_never_a_paid_default() -> None:
    """Without ``--model`` Pi takes the first provider it has credentials for."""
    for name in ("pi", "omp"):
        entry = workspace_agents.get_agent(name)
        assert entry is not None and entry.is_coding_agent
        assert workspace_agents.install_command(name)
        args = entry.launch_args
        assert args[args.index("-e") + 1] == workspace_agents.PI_LOCAL_EXTENSION
        assert args[args.index("--model") + 1] == workspace_agents.PI_LOCAL_MODEL
    assert Path(workspace_agents.PI_LOCAL_EXTENSION).is_file()


def test_the_extension_offers_the_local_server_as_a_free_provider() -> None:
    source = Path(workspace_agents.PI_LOCAL_EXTENSION).read_text(encoding="utf-8")
    provider, _, model = workspace_agents.PI_LOCAL_MODEL.partition("/")
    assert f'registerProvider("{provider}"' in source
    assert f'"{model}"' in source
    assert "127.0.0.1:11443" in source
    assert "cost: FREE" in source


def test_pi_and_oh_my_pi_can_be_asked_for_by_name() -> None:
    assert intent._canonical_agent("pi") == "pi"
    assert intent._canonical_agent("oh my pi") == "omp"
    assert intent._canonical_agent("omp") == "omp"
    # Not a pane: "pie" on its own is food or a chart.
    assert intent._canonical_agent("pie") != "pi"


# ------------------------------------------------------------- the session


def _session_file(
    root: Path, sid: str, *, user_turn: bool = True, title: bool = False
) -> Path:
    name = f"2026-10-26T10-46-56-179Z_{sid}.jsonl"
    path = root / "sessions" / "--work-pi-app--" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if title:
        lines.append({"type": "title", "v": 1, "title": ""})
    now = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
    header = {**_HEADER, "id": sid, "timestamp": now}
    lines.append(header)
    lines.append({"type": "model_change", "id": "a1", "modelId": "gemma-4b-qat"})
    if user_turn:
        lines.append({"type": "message", "id": "a2", "message": {"role": "user", "content": "x"}})
    text = "\n".join(json.dumps(x, separators=(",", ":")) for x in lines) + "\n"
    path.write_text(text, encoding="utf-8")
    return path


def test_pi_mints_its_session_id_and_resumes_by_it(tmp_path: Path) -> None:
    argv, handle = agent_sessions.launch_extra("pi")
    assert handle is not None and argv == ("--session-id", handle.id)
    assert agent_sessions.resume_argv("pi", handle) == ("--session", handle.id)
    # Opened but never prompted: nothing to resume.
    _session_file(tmp_path, handle.id, user_turn=False)
    assert not agent_sessions.has_conversation("pi", handle, home=tmp_path)
    _session_file(tmp_path, handle.id)
    assert agent_sessions.has_conversation("pi", handle, home=tmp_path)


def test_a_pi_fork_carries_the_copys_own_id() -> None:
    _, source = agent_sessions.launch_extra("pi")
    assert source is not None
    forked = agent_sessions.fork_argv("pi", source)
    assert forked is not None
    argv, copy = forked
    assert copy is not None and copy.id != source.id
    assert argv == ("--fork", source.id, "--session-id", copy.id)


def test_oh_my_pi_finds_the_session_its_pane_created(tmp_path: Path) -> None:
    argv, handle = agent_sessions.launch_extra("omp")
    assert argv == () and handle is None
    started = time.time() - 1
    _session_file(tmp_path, "omp-old", title=True)
    found = agent_sessions.discover("omp", _CWD, started, taken={"omp-old"}, home=tmp_path)
    assert found is None
    _session_file(tmp_path, "omp-new", title=True)
    found = agent_sessions.discover("omp", _CWD, started, taken={"omp-old"}, home=tmp_path)
    assert found is not None and found.id == "omp-new"
    assert agent_sessions.resume_argv("omp", found) == ("--resume", "omp-new")
    assert agent_sessions.has_conversation("omp", found, home=tmp_path)
    # Another folder's session is never this pane's.
    assert agent_sessions.discover("omp", "/elsewhere", started, home=tmp_path) is None


# --------------------------------------------------------------- the seat


@pytest.fixture
def stubbed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rc, "pi_argv_prefix", lambda: ["pi"])
    monkeypatch.setattr(rc, "omp_argv_prefix", lambda: ["omp"])
    monkeypatch.setattr(rc, "_account_env", lambda platform: {"HOME": "h"})
    monkeypatch.setattr(rc, "_registry_env", lambda agent, base: {**base, "AGENT": agent})


def test_plan_pi_prints_json_on_the_local_model(stubbed: None) -> None:
    plan = rc.plan_pi(
        prompt="-- fix it", cwd=Path("."), model="", effort="", permission_mode="auto", resume=None
    )
    assert plan.argv[:4] == ["pi", "-p", "--mode", "json"]
    assert plan.argv[plan.argv.index("-e") + 1] == workspace_agents.PI_LOCAL_EXTENSION
    assert plan.argv[plan.argv.index("--model") + 1] == workspace_agents.PI_LOCAL_MODEL
    assert plan.argv[-2:] == ["--", "-- fix it"]
    assert "--tools" not in plan.argv
    # Closed stdin: Pi's print mode otherwise waits for piped input.
    assert plan.stdin_text is None and plan.shape == "pi" and plan.env["AGENT"] == "pi"

    plan = rc.plan_pi(
        prompt="p", cwd=Path("."), model="jarvis/qwen", effort="", permission_mode="plan",
        resume="s-1",
    )
    assert plan.argv[plan.argv.index("--tools") + 1] == "read,grep,find,ls"
    assert plan.argv[plan.argv.index("--session") + 1] == "s-1"
    assert plan.argv[plan.argv.index("--model") + 1] == "jarvis/qwen"


def test_plan_omp_approves_tools_itself_and_resumes_with_its_flag(stubbed: None) -> None:
    plan = rc.plan_omp(
        prompt="p", cwd=Path("."), model="", effort="", permission_mode="auto", resume="s-2"
    )
    assert plan.argv[:4] == ["omp", "-p", "--mode", "json"]
    assert "--auto-approve" in plan.argv
    assert plan.argv[plan.argv.index("--resume") + 1] == "s-2"
    plan = rc.plan_omp(
        prompt="p", cwd=Path("."), model="", effort="", permission_mode="plan", resume=None
    )
    assert "--auto-approve" not in plan.argv
    assert plan.argv[plan.argv.index("--tools") + 1] == "read,grep,glob,lsp"


def test_a_mode_from_another_ladder_folds_onto_pi() -> None:
    assert permissions.normalize_permission("pi-cli", "bypassPermissions") == "auto"
    assert permissions.normalize_permission("omp-cli", "plan") == "plan"


def test_the_stream_becomes_a_session_text_reasoning_tool_rows_and_usage() -> None:
    st = rc._PiState(turn_id="t1")
    events = [e for obj in _STREAM for e in rc.translate_pi_line(obj, st)]
    kinds = [e["kind"] for e in events]
    assert kinds == ["tool_call", "tool_result", "reasoning", "assistant_text"]
    call, result = events[0]["payload"], events[1]["payload"]
    assert call["name"] == "bash" and call["input"] == {"command": "echo hi"}
    assert result["call_id"] == "call_1" and result["output"] == "hi\n"
    assert result["duration_ms"] == 12
    assert events[3]["payload"]["text"] == "The command printed hi."
    assert st.vendor_session == _SID
    assert st.status == "done" and st.error is None
    # turn_end repeats the first message; it is not counted twice.
    assert st.usage == {"input_tokens": 270, "output_tokens": 14, "cache_read_input_tokens": 6}
    assert st.result_text == "The command printed hi." and st.emitted_text


def test_a_failed_model_call_ends_the_turn_with_its_message() -> None:
    st = rc._PiState(turn_id="t1")
    failed = _assistant([], usage_in=0, out=0, stop="error")
    failed["errorMessage"] = "Connection refused"
    rc.translate_pi_line({"type": "message_end", "message": failed}, st)
    assert st.status == "error" and st.error == "Connection refused"


# --------------------------------------------------------------- the bill


def test_pi_and_oh_my_pi_spend_is_counted_once_even_across_a_fork(tmp_path: Path) -> None:
    data = tmp_path / "data"
    answer = [{"type": "text", "text": "ok"}]
    turn = {
        "type": "message",
        "id": "m1",
        "timestamp": "2026-10-26T10:46:57.000Z",
        "message": _assistant(answer, usage_in=120, out=9, stop="stop", response="r-1"),
    }
    pi_home, omp_home = tmp_path / ".pi" / "agent", tmp_path / ".omp" / "agent"
    for root, sid in ((pi_home, "pi-a"), (pi_home, "pi-fork"), (omp_home, "omp-a")):
        path = _session_file(root, sid)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(turn, separators=(",", ":")) + "\n")

    index.refresh(data_dir=data, home=tmp_path)

    turns = list(index.entries(data_dir=data, since_ms=0, until_ms=2**62))
    by_agent = sorted((t.agent, t.tokens_in, t.tokens_out, t.tokens_cached) for t in turns)
    # The fork copied pi-a's call: still one row for Pi, and one for Oh My Pi.
    assert by_agent == [(index.AGENT_OMP, 120, 9, 3), (index.AGENT_PI, 120, 9, 3)]
    assert {t.model for t in turns} == {"jarvis/gemma-4b-qat"}
    assert {t.cwd for t in turns} == {_CWD}
