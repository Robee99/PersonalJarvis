"""Opt-in, real-Chrome BrowserSkill smoke against a local-only page fixture.

Run manually with ``JARVIS_MY_CHROME_LIVE=1 pytest -m skip_ci
tests/contract/test_my_chrome_live.py``. It requires the owner's installed
BrowserSkill extension and an already running daemon. It never launches a
private/headless browser or contacts an external site.
"""

from __future__ import annotations

import asyncio
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.brain.my_chrome import MyChromeTool, bsk_path, run_bsk

pytestmark = [
    pytest.mark.skip_ci,
    pytest.mark.skipif(
        os.environ.get("JARVIS_MY_CHROME_LIVE") != "1",
        reason="set JARVIS_MY_CHROME_LIVE=1 to run the real-Chrome fixture check",
    ),
    pytest.mark.skipif(os.name != "nt", reason="requires the Windows BrowserSkill extension"),
]


class _FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/fixture":
            self.send_error(404)
            return
        body = (Path(__file__).parents[1] / "fixtures" / "browser_acceptance.html").read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        return


def _run(coro):
    return asyncio.run(coro)


def test_real_chrome_opens_local_fixture_clicks_once_and_reads_dom_state() -> None:
    exe = bsk_path()
    assert exe, "BrowserSkill CLI bsk is not installed"

    # The test must use a daemon already owned by the current desktop session.
    # This check is client-only (BSK_AUTO_START=0), so it cannot spawn a hidden
    # browser or an independent daemon from pytest.
    try:
        _run(run_bsk(exe, "status", "--json"))
    except Exception as exc:  # noqa: BLE001 - include the CLI's setup hint
        pytest.fail(
            "BrowserSkill daemon is unavailable. Start it in a persistent "
            f"desktop task, then rerun this opt-in check. Details: {exc}"
        )

    server = ThreadingHTTPServer(("127.0.0.1", 0), _FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    tool = MyChromeTool(exe=exe)
    ctx = SimpleNamespace(user_utterance="Use the local Jarvis browser fixture")
    try:
        opened = _run(tool.execute(
            {"action": "open", "url": f"http://127.0.0.1:{server.server_port}/fixture"},
            ctx,
        ))
        assert opened.success, opened.error
        assert "Jarvis browser acceptance fixture" in str(opened.output)
        button_line = next(
            (
                line for line in str(opened.output).splitlines()
                if "button" in line.casefold() and "Mark browser action complete" in line
            ),
            "",
        )
        found = re.search(r"\[(e\d+)\]", button_line)
        assert found, f"fixture button missing from BrowserSkill snapshot:\n{opened.output}"

        clicked = _run(tool.execute({"action": "click", "ref": found.group(1)}, ctx))
        assert clicked.success, clicked.error
        assert "VERIFIED" in str(clicked.output), (
            "BrowserSkill returned without showing the changed page state:\n"
            f"{clicked.output}"
        )
    finally:
        try:
            _run(tool.execute({"action": "done"}, ctx))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
