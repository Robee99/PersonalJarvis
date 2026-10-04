"""HermesDirectWorker: argv shape, usage report, and a real run against a stand-in CLI."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from jarvis.missions.workers import hermes_direct_worker as hermes
from jarvis.missions.workers.stream_consumer import ClaudeAssistantMessage, ClaudeResult


def test_cmd_is_one_shot_with_a_usage_report(tmp_path: Path) -> None:
    usage = tmp_path / "u.json"
    cmd = hermes.build_hermes_cmd(binary="hermes", prompt="fix\nit", usage_file=usage)
    assert cmd == ["hermes", "-z", "fix\nit", "--usage-file", str(usage)]
    flat = hermes.build_hermes_cmd(binary="C:/h/hermes.cmd", prompt="fix\n it", usage_file=usage)
    assert flat[2] == "fix it"


def test_a_missing_or_broken_usage_report_reads_as_empty(tmp_path: Path) -> None:
    assert hermes.read_usage(tmp_path / "none.json") == {}
    broken = tmp_path / "b.json"
    broken.write_text("{", encoding="utf-8")
    assert hermes.read_usage(broken) == {}


class _Job:
    def __init__(self) -> None:
        self.pids: list[int] = []

    def assign(self, pid: int) -> None:
        self.pids.append(pid)


def _stand_in(tmp_path: Path, *, answer: str, exit_code: int, failed: bool) -> Path:
    """A ``hermes`` that writes its usage report and prints its answer."""
    script = tmp_path / "bin" / "hermes"
    script.parent.mkdir()
    report = {"estimated_cost_usd": 0.0042, "api_calls": 3, "session_id": "h-1",
              "failed": failed}
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "out = sys.argv[sys.argv.index('--usage-file') + 1]\n"
        f"open(out, 'w').write(json.dumps({report!r}))\n"
        f"print({answer!r})\n"
        f"sys.exit({exit_code})\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


async def _run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **stand_in) -> list:
    script = _stand_in(tmp_path, **stand_in)
    monkeypatch.setattr(hermes, "resolve_hermes_binary", lambda: str(script))
    worker = hermes.HermesDirectWorker()
    worktree = tmp_path / "wt"
    worktree.mkdir()
    events = [
        event
        async for event in worker.spawn(
            "add a test", worktree=worktree, env={"PATH": "/usr/bin:/bin"}, job=_Job(),
            worker_id="w1", log_dir=tmp_path / "logs", timeout_s=60.0,
        )
    ]
    assert worker.last_session_id == "h-1"
    return events


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in CLI is a POSIX script")
async def test_a_run_reports_the_answer_and_the_cost(tmp_path, monkeypatch) -> None:
    events = await _run(tmp_path, monkeypatch, answer="Added test_x.", exit_code=0, failed=False)

    message = next(e for e in events if isinstance(e, ClaudeAssistantMessage))
    result = events[-1]
    assert message.message["content"][0]["text"] == "Added test_x."
    assert isinstance(result, ClaudeResult) and result.is_error is False
    assert (result.cost_usd, result.num_turns) == (0.0042, 3)
    assert json.loads((tmp_path / "logs" / "hermes-usage.json").read_text())["api_calls"] == 3


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in CLI is a POSIX script")
async def test_a_failed_run_is_an_error_result(tmp_path, monkeypatch) -> None:
    events = await _run(tmp_path, monkeypatch, answer="", exit_code=1, failed=True)

    result = events[-1]
    assert isinstance(result, ClaudeResult) and result.is_error is True
    assert result.result


async def test_no_binary_is_an_honest_error(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(hermes, "resolve_hermes_binary", lambda: None)
    events = [
        e async for e in hermes.HermesDirectWorker().spawn(
            "x", worktree=tmp_path, env={}, job=_Job(), worker_id="w", log_dir=tmp_path / "l"
        )
    ]
    assert events[-1].is_error is True and "not found" in events[-1].result


def test_a_picked_model_goes_to_dash_m_and_the_log_never_shows_the_prompt(tmp_path: Path) -> None:
    usage = tmp_path / "u.json"
    cmd = hermes.build_hermes_cmd(
        binary="hermes", prompt="secret task", usage_file=usage,
        model="poolside/laguna-s-2.1:free",
    )
    assert cmd == [
        "hermes", "-m", "poolside/laguna-s-2.1:free", "-z", "secret task",
        "--usage-file", str(usage),
    ]
    assert "secret task" not in hermes._redacted_argv(cmd)
    assert "poolside/laguna-s-2.1:free" in hermes._redacted_argv(cmd)


def _cfg(provider: str, model: str, primary: str = "gemini"):
    from types import SimpleNamespace

    return SimpleNamespace(
        brain=SimpleNamespace(primary=primary, worker=SimpleNamespace(provider=provider, model=model))
    )


def test_only_a_pin_made_for_hermes_reaches_hermes(monkeypatch: pytest.MonkeyPatch) -> None:
    import jarvis.core.config as config_mod

    monkeypatch.setattr(config_mod, "load_config", lambda: _cfg("hermes", "stepfun/step-3.7-flash:free"))
    assert hermes.hermes_pinned_model() == "stepfun/step-3.7-flash:free"
    # A pin made while another worker was active is another provider's id.
    monkeypatch.setattr(config_mod, "load_config", lambda: _cfg("openrouter", "openai/gpt-5.5"))
    assert hermes.hermes_pinned_model() == ""


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in CLI is a POSIX script")
async def test_a_run_passes_the_pick_and_never_the_missions_model(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(hermes, "hermes_pinned_model", lambda: "poolside/laguna-xs-2.1:free")
    script = tmp_path / "bin" / "hermes"
    script.parent.mkdir()
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        "out = sys.argv[sys.argv.index('--usage-file') + 1]\n"
        "open(out, 'w').write(json.dumps({'session_id': 'h-2'}))\n"
        "print(sys.argv[sys.argv.index('-m') + 1] if '-m' in sys.argv else 'no-model')\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setattr(hermes, "resolve_hermes_binary", lambda: str(script))
    worktree = tmp_path / "wt"
    worktree.mkdir()
    events = [
        event
        async for event in hermes.HermesDirectWorker().spawn(
            "add a test", worktree=worktree, env={"PATH": "/usr/bin:/bin"}, job=_Job(),
            worker_id="w1", log_dir=tmp_path / "logs", timeout_s=60.0,
            model="gemini-3.5-flash",
        )
    ]
    message = next(e for e in events if isinstance(e, ClaudeAssistantMessage))
    assert message.message["content"][0]["text"] == "poolside/laguna-xs-2.1:free"
