"""Explicitly index or forget one user-authorized wiki source in Cognee."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jarvis.core.protocols import ToolResult
from jarvis.memory.cognee_adapter import CogneeUnavailable, build_cognee_adapter
from jarvis.memory.wiki.vault_root import resolve_vault_root


class CogneeSourceTool:
    name = "cognee-source"
    description = (
        "Index or forget one explicitly selected Markdown page from the authorized Jarvis wiki "
        "in the optional local Cognee semantic memory. "
        "Use index after a user asks to add an existing note to semantic recall; "
        "use forget when the user asks to remove its derived semantic memory. "
        "This never reads outside the wiki vault and never sends content to hosted model APIs."
    )
    risk_tier = "monitor"
    schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["index", "forget"]},
            "path": {
                "type": "string",
                "description": "Vault-relative Markdown page path, e.g. people/joy.md",
            },
        },
        "required": ["action", "path"],
    }
    input_examples = [{"action": "index", "path": "people/joy.md"}]

    def __init__(self, adapter: Any, vault_root: Path) -> None:
        self._adapter = adapter
        self._vault_root = vault_root.resolve()

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        action, raw_path = str(args.get("action", "")), str(args.get("path", "")).strip()
        candidate = Path(raw_path)
        if not raw_path or candidate.is_absolute() or ".." in candidate.parts:
            return ToolResult(
                success=False, output="", error="path must be a wiki-relative Markdown path"
            )
        path = (self._vault_root / candidate).resolve()
        if not path.is_relative_to(self._vault_root):
            return ToolResult(success=False, output="", error="path outside wiki vault")
        try:
            if action == "index":
                if not path.is_file():
                    return ToolResult(success=False, output="", error="wiki page not found")
                result = await self._adapter.index_path(path)
                state = "already current" if result.get("unchanged") else "indexed"
                return ToolResult(
                    success=True,
                    output=(
                        f"Cognee source {state}: {candidate.as_posix()} "
                        f"(source {result['source_id']})"
                    ),
                )
            if action == "forget":
                result = await self._adapter.forget_path(path)
                return ToolResult(
                    success=True,
                    output=(
                        f"Cognee derived memory forgotten: {candidate.as_posix()} "
                        f"(source {result['source_id']})"
                    ),
                )
            return ToolResult(success=False, output="", error="unsupported action")
        except (CogneeUnavailable, OSError, ValueError) as exc:  # Surface source errors through the tool result.
            return ToolResult(success=False, output="", error=str(exc))


def build_cognee_source_tool() -> CogneeSourceTool:
    root = resolve_vault_root(None).path.resolve()
    return CogneeSourceTool(build_cognee_adapter(root), root)
