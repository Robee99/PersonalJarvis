"""The butler persona and its "Sir" (owner's choice 2026-10-25).

Contract under test:

1. ``[persona] style`` defaults to the butler block and its compact twin;
   ``companion`` restores the honorific-free blocks.
2. The butler is told how to address the owner (``[persona] honorific``).
3. The voice scrubber keeps "Sir" only while the butler's honorific is "Sir".
"""

from __future__ import annotations

from types import SimpleNamespace

from jarvis.brain import persona_loader
from jarvis.brain.output_filter import allow_honorific, scrub_for_voice
from jarvis.core.config import PersonaConfig


def _config(**persona: object) -> SimpleNamespace:
    return SimpleNamespace(persona=PersonaConfig(**persona))


def test_butler_is_the_default_style_with_sir() -> None:
    assert persona_loader.persona_style(_config()) == ("butler", "Sir")
    assert persona_loader.persona_style(None) == ("butler", "Sir")


def test_companion_style_has_no_honorific() -> None:
    assert persona_loader.persona_style(_config(style="companion")) == ("companion", "")


def test_butler_blocks_are_butler_and_compact_is_shorter() -> None:
    full = persona_loader.load_effective_persona_prompt(style="butler")
    compact = persona_loader.load_effective_persona_prompt(compact=True, style="butler")
    assert "butler" in full and "butler" in compact
    assert "Never use an honorific" not in full
    assert len(compact) < len(full)
    companion = persona_loader.load_effective_persona_prompt(style="companion")
    assert 'Never use an honorific such as "Sir"' in companion


def test_address_directive_names_the_honorific() -> None:
    assert '"Sir"' in persona_loader.address_directive("Sir")
    assert "without an honorific" in persona_loader.address_directive("")


def test_scrubber_keeps_sir_for_the_butler_and_strips_it_otherwise() -> None:
    allow_honorific("Sir")
    assert scrub_for_voice("Done, Sir.", language="en").cleaned == "Done, Sir."
    allow_honorific("")
    assert scrub_for_voice("Done, Sir.", language="en").cleaned == "Done."
