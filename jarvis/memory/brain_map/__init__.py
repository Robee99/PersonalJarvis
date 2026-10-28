"""The brain map: one project intelligence layer over the stores Jarvis has.

Projects, conversations, tasks, decisions, blockers, milestones, documents,
agents and outputs as one status-bearing graph, derived incrementally from the
agent-chat log (voice turns included), missions and their outputs, ``/goal``,
society quests, automations, the Agentic IDE library, wiki project pages and
imported conversations, and the coding-CLI/Hermes session index.

Derived, not duplicated: every entity points back at its source row, and the
only text kept is a bounded verbatim quote per piece of evidence. Status is
always derived by Python rules (``status.py``); a model may only propose, never
decide (``extractor.py``).

Layout: ``constants`` (vocabulary) · ``store`` (SQLite) · ``sources`` (readers)
· ``rules`` (deterministic extraction) · ``extractor`` (optional model step) ·
``status`` / ``engine`` (status, rollup, priority) · ``indexer`` (pipeline) ·
``query`` (read model) · ``service`` (wiring, tick) · ``mcp_tools``.
Design notes: the 2026-10-07 memory/project-intelligence audit, §8.
"""

from __future__ import annotations
