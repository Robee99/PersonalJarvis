"""Two-step navigation: "open settings and go to the model page".

The matcher splits the command; the fast path opens the section and clicks the
label through ``navigate``'s ``then`` argument, then answers with what really
happened. No model call anywhere.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.brain.navigation_followup import NavigationFollowup, match_navigation_followup
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.core.protocols import ToolResult


@pytest.mark.parametrize(
    ("spoken", "section", "label"),
    [
        ("open settings and go to the model page", "settings", "model"),
        ("Open God's Eye and show the news tab", "world-intelligence", "news"),
        ("go to memory then open the graph", "memory", "graph"),
        ("open settings, then click Model", "settings", "Model"),
        ("open settings and then select API keys", "settings", "API keys"),
        (
            "zeig mir die Einstellungen und dann öffne den Reiter Modell",  # i18n-allow
            "settings",
            "Modell",
        ),
    ],
)
def test_two_step_commands_split(spoken: str, section: str, label: str) -> None:
    assert match_navigation_followup(spoken) == NavigationFollowup(section, label)


@pytest.mark.parametrize(
    "spoken",
    [
        "open settings",
        "open settings and tell me which model I use",
        "open settings and show me how to change the voice",
        "open settings and go to the page where I can change my default model",
        "what can I change in settings and go",
        "",
    ],
)
def test_anything_else_is_not_a_two_step_command(spoken: str) -> None:
    assert match_navigation_followup(spoken) is None


class _Executor:
    """Records the navigate call and returns a scripted ``ToolResult``."""

    def __init__(self, result: Any) -> None:
        self.result = result
        self.calls: list[dict[str, Any]] = []

    async def execute(
        self,
        tool: object,
        args: dict[str, Any],
        *,
        user_utterance: str = "",
        trace_id: UUID | None = None,
    ) -> Any:
        self.calls.append(dict(args))
        return self.result


def _manager(result: Any) -> tuple[BrainManager, _Executor]:
    cfg = JarvisConfig()
    cfg.brain.primary = "fake"
    manager = BrainManager(config=cfg, bus=EventBus(), tools={"navigate": object()})
    executor = _Executor(result)
    manager._tool_executor = executor  # type: ignore[assignment]
    manager._reply_language = "en"
    return manager, executor


async def test_fast_path_opens_the_section_and_clicks_the_label() -> None:
    manager, executor = _manager(
        ToolResult(success=True, output={"section": "settings", "clicked": "Model"})
    )
    reply = await manager._run_navigation_fast_path("open settings and go to the model page")

    assert executor.calls == [{"section": "settings", "then": "model"}]
    assert reply == "Opened Settings and clicked Model."


async def test_fast_path_says_when_the_label_was_not_there() -> None:
    manager, executor = _manager(
        ToolResult(
            success=True,
            output={"section": "settings", "clicked": None, "then_error": "No control named"},
        )
    )
    reply = await manager._run_navigation_fast_path("open settings and go to the model page")

    assert executor.calls == [{"section": "settings", "then": "model"}]
    assert reply == "Opened Settings, but I couldn't find model there."


async def test_fast_path_says_when_no_window_answered() -> None:
    manager, _ = _manager(
        ToolResult(success=True, output={"section": "memory", "clicked": None, "no_window": True})
    )
    reply = await manager._run_navigation_fast_path("go to memory then open the graph")

    assert reply is not None
    assert reply.startswith("I asked for Memory, but the Jarvis window did not answer")


async def test_a_plain_command_keeps_its_old_answer() -> None:
    manager, executor = _manager({"success": True})
    reply = await manager._run_navigation_fast_path("open settings")

    assert executor.calls == [{"section": "settings"}]
    assert reply == "Opening Settings."


async def test_a_tab_inside_a_section_is_not_a_terminal_tab() -> None:
    # Live 2026-10-20: the Agentic IDE read "open … tab" as a terminal tab and
    # answered "No workspace is open".
    manager, executor = _manager(
        ToolResult(success=True, output={"section": "world-intelligence", "clicked": "News"})
    )
    reply = await manager._run_navigation_fast_path("Open God's Eye and show the news tab")

    assert executor.calls == [{"section": "world-intelligence", "then": "news"}]
    assert reply is not None and reply.endswith("clicked News.")


def test_only_a_control_in_an_app_section_outranks_other_gates() -> None:
    from jarvis.brain.manager import _names_app_control, _navigation_followup

    assert _names_app_control(_navigation_followup("open settings and go to the model page"))
    assert not _names_app_control(_navigation_followup("open settings"))
    assert not _names_app_control(_navigation_followup("open the IDE and show the terminal tab"))
