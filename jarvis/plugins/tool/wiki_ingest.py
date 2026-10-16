"""``wiki-ingest`` tool — explicit brain-driven wiki write path.

B5 follow-up (post-merge).  Router-tier tool with write semantics.

Why this tool exists alongside the voice-bridge
-----------------------------------------------
:class:`jarvis.memory.wiki.voice_bridge.VoiceFactBridge` already pushes
voice-spoken facts to the wiki via one heuristic: the *ack path* (brain
reply acknowledges a saved fact). The former *aggressive path* that
reviewed every user turn was removed on 2026-09-30.

Heuristics drift.  When the brain consciously decides "this is worth
storing" (e.g. user typed something into chat, or the brain is
summarising a long subagent run), it should be able to call a tool
explicitly instead of relying on the bridge picking it up.  That is what
this tool provides.

Architecture
------------
The tool delegates to the live :class:`~jarvis.memory.wiki.curator.WikiCurator`
instance owned by ``bootstrap_wiki_integration``.  The curator handles
salience filtering (an empty source returns zero updates, no harm done),
atomic writing with backup/rollback, and log-entry rendering.  This tool
adds nothing beyond the dispatch.

Risk tier and confirmation
--------------------------
Marked ``monitor``. The write surface is protected by ``AtomicWriter``
(AP-3 pre-validate, AP-4 backup) and the curator's salience filter; the
tool itself has no destructive scope.  Bypass-confirmation matches
``wiki-recall`` so the brain can call both freely.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from jarvis.core.protocols import ToolResult
from jarvis.memory.wiki.ingest_service import ingest_wiki_text


class WikiIngestTool:
    """Router-tier deterministic-ingest path into the long-term wiki vault."""

    name: str = "wiki-ingest"
    description: str = (
        "Store a fact, observation, or summary in the user's long-term Obsidian "
        "wiki. Use this when the user explicitly asks you to remember something "
        "('remember that', 'save this') or when you have finished "
        "a substantial piece of work whose outcome should outlive this session. "
        "The curator decides which pages to touch — give it the content as one "
        "self-contained block."
    )
    # H10 (2026-05-17 audit): `safe` is wrong for an LLM-and-disk tool.
    # wiki-ingest spawns the WikiCuratorLLM (Gemini/Grok call) and writes
    # markdown pages under the vault; that is the precise definition of
    # `monitor` per the project's risk-tier taxonomy -- run without a
    # user prompt (anti-confirmation-fatigue) but log every invocation
    # for audit. `ask` would nag the user on every memory request; `safe`
    # falsely implied no side effects.
    risk_tier: str = "monitor"
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": (
                    "The fact or summary to ingest.  Plain prose; do not "
                    "pre-format as a wiki page — the curator does that."
                ),
            },
            "source": {
                "type": "string",
                "description": (
                    "Optional short label describing where the content came "
                    "from, e.g. 'chat:2026-05-17' or 'voice:user-direct'. "
                    "Used in the log entry."
                ),
            },
        },
        "required": ["text"],
    }
    input_examples: list[dict[str, Any]] = [
        {
            "text": (
                "Joy's birthday is August 14th. She is Harald's younger "
                "sister."
            ),
            "source": "voice:user-direct",
        },
        {
            "text": "We finished Phase B5 today — the brain now reads from the wiki.",
            "source": "chat:milestone",
        },
    ]

    def __init__(
        self,
        *,
        curator_resolver: Callable[[], Any],
        cognee_adapter: Any | None = None,
        graphrag_adapter: Any | None = None,
    ) -> None:
        # Lazy resolver so the curator can be set up after the brain is
        # built (mirrors the spawn-worker lazy-resolver pattern in
        # ``factory.py:_load_tools_for_tier``).
        self._resolve_curator = curator_resolver
        self._cognee = cognee_adapter
        self._graphrag = graphrag_adapter

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        text = str(args.get("text", "")).strip()
        source = str(args.get("source") or "tool:wiki-ingest").strip() or "tool:wiki-ingest"
        curator = self._resolve_curator()
        outcome = await ingest_wiki_text(
            curator=curator,
            text=text,
            source=source,
        )
        if not outcome.success:
            return ToolResult(success=False, output="", error=outcome.error)
        summary = outcome.render_summary()
        if self._cognee is not None and self._cognee.enabled:
            indexed = 0
            for path in outcome.applied:
                try:
                    await self._cognee.index_path(path)
                    indexed += 1
                except Exception as exc:  # native wiki write remains authoritative
                    import logging

                    logging.getLogger(__name__).warning(
                        "wiki-ingest: Cognee indexing failed for %s: %s", path.name, exc
                    )
            summary += f"\n- Cognee semantic sources indexed: {indexed}/{len(outcome.applied)}"
        if self._graphrag is not None and self._graphrag.enabled:
            indexed = 0
            try:
                # The isolated worker is a one-shot process. Batch the pages
                # from this curator turn so a multi-page update pays for one
                # GraphRAG startup/index pass, not one per page.
                result = await self._graphrag.index_paths(outcome.applied)
                indexed = min(len(outcome.applied), max(0, int(result.get("indexed", 0))))
            except Exception as exc:  # native wiki write remains authoritative
                import logging

                logging.getLogger(__name__).warning(
                    "wiki-ingest: GraphRAG indexing failed for %d page(s): %s",
                    len(outcome.applied),
                    exc,
                )
            summary += f"\n- GraphRAG sources indexed: {indexed}/{len(outcome.applied)}"
        return ToolResult(success=True, output=summary)
