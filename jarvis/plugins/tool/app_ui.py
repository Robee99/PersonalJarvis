"""``app_ui_*`` — read and use the controls on the Jarvis app screen.

``navigate`` opens a section; these four act INSIDE it, on whatever the view
shows, without a per-view registry:

* ``app_ui_snapshot`` lists the visible controls (tabs, buttons, switches,
  fields, links) with short refs (``e1``, ``e2`` ...), the screen's headings
  and where the scroll stands.
* ``app_ui_click`` clicks one, by ref or by the words on it.
* ``app_ui_type`` types into a field, optionally pressing Enter.
* ``app_ui_scroll`` scrolls the main content.

Each call is one :class:`~jarvis.core.events.AppUiRequest` round trip through
:class:`~jarvis.core.app_ui_bridge.AppUiBridge`: the app's main window performs
it (``src/lib/appUi.ts``) exactly as a user's click would, and reports back.
Only the app's own window is touched, never another program. Buttons the app
marks destructive (delete, remove, reset) are refused by the window: the user
clicks those. Password fields are never read or typed into.

Risk ``safe``, router tier only; never a spawn and never in a worker or society
tool set (AP-5/AP-14).
"""

from __future__ import annotations

from typing import Any

from jarvis.brain.tool_switch import names_other_program
from jarvis.core.app_ui_bridge import AppUiBridge, AppUiReply
from jarvis.core.protocols import ToolResult

#: How long a click or type by label may wait for its control to render.
LABEL_WAIT_MS = 1500

_REGION_TITLES: tuple[tuple[str, str], ...] = (
    ("dialog", "Open dialog (deal with it first):"),
    ("main", "Main content:"),
    ("nav", "Navigation (sidebar and tab bars):"),
    ("other", "Other:"),
)

_TOGGLE_ROLES = frozenset({"checkbox", "switch", "radio", "menuitemcheckbox", "menuitemradio"})


def _flags(element: dict[str, Any]) -> str:
    parts: list[str] = []
    role = str(element.get("role") or "")
    value = element.get("value")
    if isinstance(value, str):
        parts.append(f'= "{value}"')
    checked = element.get("checked")
    if role in _TOGGLE_ROLES or checked is not None:
        if checked == "mixed":
            parts.append("mixed")
        elif checked is not None:
            parts.append("on" if checked else "off")
    if element.get("selected"):
        parts.append("selected")
    expanded = element.get("expanded")
    if expanded is not None:
        parts.append("expanded" if expanded else "collapsed")
    if element.get("disabled"):
        parts.append("disabled")
    if element.get("destructive"):
        parts.append("destructive: user only")
    return (" " + " ".join(parts)) if parts else ""


def _screen_line(result: dict[str, Any]) -> str:
    label = str(result.get("section_label") or result.get("section") or "unknown")
    headings = [str(h) for h in result.get("headings") or [] if str(h).strip()]
    line = f"Screen: {label}"
    if headings:
        line += " — " + " › ".join(headings)
    return line


def render_snapshot(result: dict[str, Any]) -> str:
    """The window's snapshot as compact text a small model can act on."""
    lines = [_screen_line(result)]
    scroll = str(result.get("scroll") or "").strip()
    if scroll:
        lines.append(f"Scroll: {scroll}")
    raw = result.get("elements")
    elements: list[dict[str, Any]] = (
        [e for e in raw if isinstance(e, dict)] if isinstance(raw, list) else []
    )
    if not elements:
        lines.append("No controls are visible on this screen.")
        return "\n".join(lines)
    for region, title in _REGION_TITLES:
        group = [e for e in elements if (e.get("region") or "other") == region]
        if not group:
            continue
        lines.append(title)
        for element in group:
            name = str(element.get("name") or "").replace('"', "'")
            lines.append(f'{element.get("ref")} {element.get("role")} "{name}"{_flags(element)}')
    total = result.get("total")
    if isinstance(total, int) and total > len(elements):
        lines.append(f"({total - len(elements)} more controls not listed; scroll to reach them.)")
    lines.append('Next: app_ui_click(ref="eN") or app_ui_type(ref="eN", text=...).')
    return "\n".join(lines)


def _after(result: dict[str, Any]) -> str:
    count = result.get("count")
    tail = f" ({count} controls)" if isinstance(count, int) else ""
    return f"{_screen_line(result)}{tail}."


def _failure(reply: AppUiReply) -> ToolResult:
    return ToolResult(success=False, output=None, error=reply.error or "The app did not say why.")


_OTHER_PROGRAM_ERROR = (
    "app_ui tools only reach the Jarvis window, and this request is about another "
    "program. Use the desktop tools for it: windows-desktop/Snapshot, then "
    "windows-desktop/Click with loc=[x, y] from the element's line (or computer_use)."
)


def _other_program(ctx: Any) -> ToolResult | None:
    """A refusal when the person's request is about another program's window."""
    if names_other_program(str(getattr(ctx, "user_utterance", "") or "")):
        return ToolResult(success=False, output=None, error=_OTHER_PROGRAM_ERROR)
    return None


def _target(args: dict[str, Any]) -> tuple[str, str]:
    ref = str(args.get("ref") or "").strip()
    label = " ".join(str(args.get("label") or "").split())
    return ref, label


class _AppUiTool:
    risk_tier: str = "safe"

    def __init__(self, bus: Any, bridge: AppUiBridge | None = None) -> None:
        self._bus = bus
        self._bridge = bridge or AppUiBridge(bus)


class AppUiSnapshotTool(_AppUiTool):
    """List the visible controls of the open Jarvis screen."""

    name: str = "app_ui_snapshot"
    description: str = (
        "Read the controls on the Jarvis app screen that is open now: tabs, "
        "sub-pages, buttons, switches and fields, each with a ref like e5. Use it "
        "after navigate when the user wants something INSIDE a section, then "
        "call app_ui_click with the ref. Only the Jarvis app, never other programs."
    )
    schema: dict[str, Any] = {"type": "object", "properties": {}}

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        refused = _other_program(ctx)
        if refused is not None:
            return refused
        reply = await self._bridge.request("snapshot")
        if not reply.ok:
            return _failure(reply)
        return ToolResult(success=True, output=render_snapshot(reply.result))


class AppUiClickTool(_AppUiTool):
    """Click one control on the open Jarvis screen."""

    name: str = "app_ui_click"
    description: str = (
        "Click a control on the open Jarvis app screen: a tab, sub-page, button, "
        "switch or menu item. Pass ref from app_ui_snapshot (best) or label = the "
        "words on the control. Delete/remove buttons are refused; the user clicks those."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "ref": {"type": "string", "description": "A ref from app_ui_snapshot, e.g. 'e5'."},
            "label": {
                "type": "string",
                "description": "The words on the control, when there is no ref.",
            },
        },
    }

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        refused = _other_program(ctx)
        if refused is not None:
            return refused
        ref, label = _target(args)
        if not ref and not label:
            return ToolResult(
                success=False, output=None, error="Pass ref (from app_ui_snapshot) or label."
            )
        reply = await self._bridge.request(
            "click", ref=ref, label=label, wait_ms=0 if ref else LABEL_WAIT_MS
        )
        if not reply.ok:
            return _failure(reply)
        clicked = reply.result.get("clicked") or {}
        what = f'{clicked.get("role") or "control"} "{clicked.get("name") or ref or label}"'
        return ToolResult(success=True, output=f"Clicked {what}. {_after(reply.result)}")


class AppUiTypeTool(_AppUiTool):
    """Type text into a field on the open Jarvis screen."""

    name: str = "app_ui_type"
    description: str = (
        "Type text into a field on the open Jarvis app screen (a search box, a "
        "name, a setting). Pass ref from app_ui_snapshot or label = the field's "
        "name; submit=true presses Enter. Never for passwords or API keys."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "ref": {"type": "string", "description": "A ref from app_ui_snapshot, e.g. 'e7'."},
            "label": {"type": "string", "description": "The field's name, when there is no ref."},
            "text": {"type": "string", "description": "The text to put in the field."},
            "submit": {"type": "boolean", "description": "Press Enter afterwards."},
        },
        "required": ["text"],
    }

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        refused = _other_program(ctx)
        if refused is not None:
            return refused
        ref, label = _target(args)
        if not ref and not label:
            return ToolResult(
                success=False, output=None, error="Pass ref (from app_ui_snapshot) or label."
            )
        text = str(args.get("text") or "")
        submit = args.get("submit") is True
        reply = await self._bridge.request(
            "type",
            ref=ref,
            label=label,
            text=text,
            submit=submit,
            wait_ms=0 if ref else LABEL_WAIT_MS,
        )
        if not reply.ok:
            return _failure(reply)
        field = reply.result.get("typed_into") or {}
        name = field.get("name") or ref or label
        done = " and pressed Enter" if reply.result.get("submitted") else ""
        return ToolResult(success=True, output=f'Typed into "{name}"{done}. {_after(reply.result)}')


class AppUiScrollTool(_AppUiTool):
    """Scroll the main content of the open Jarvis screen."""

    name: str = "app_ui_scroll"
    description: str = (
        "Scroll the open Jarvis app screen to reach controls app_ui_snapshot did "
        "not list: direction up, down, top or bottom. Take a new app_ui_snapshot after."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "direction": {"type": "string", "enum": ["up", "down", "top", "bottom"]},
        },
        "required": ["direction"],
    }

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        refused = _other_program(ctx)
        if refused is not None:
            return refused
        direction = str(args.get("direction") or "").strip().lower()
        if direction not in ("up", "down", "top", "bottom"):
            return ToolResult(
                success=False, output=None, error="direction must be up, down, top or bottom."
            )
        reply = await self._bridge.request("scroll", direction=direction)
        if not reply.ok:
            return _failure(reply)
        position = reply.result.get("position") or "unknown"
        if not reply.result.get("scrolled"):
            return ToolResult(
                success=True, output=f"Nothing to scroll that way (position: {position})."
            )
        return ToolResult(success=True, output=f"Scrolled {direction}; now at {position}.")


__all__ = [
    "LABEL_WAIT_MS",
    "AppUiClickTool",
    "AppUiScrollTool",
    "AppUiSnapshotTool",
    "AppUiTypeTool",
    "render_snapshot",
]
