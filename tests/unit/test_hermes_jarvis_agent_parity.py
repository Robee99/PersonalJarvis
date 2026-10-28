"""Anti-drift parity: Hermes Agent slugs are a SINGLE source of truth."""
from __future__ import annotations

from jarvis.core.config_writer import worker_provider_changed
from jarvis.missions.init import _select_subagent_worker_kind
from jarvis.missions.worker_runtime.provider_map import (
    HERMES_SUBAGENT_CANONICAL,
    HERMES_SUBAGENT_SLUGS,
)
from jarvis.ui.web import provider_routes


def test_provider_routes_reuses_the_single_source() -> None:
    assert provider_routes._HERMES_SUBAGENT_SLUGS is HERMES_SUBAGENT_SLUGS


def test_every_slug_routes_to_the_hermes_worker_whatever_the_step_model() -> None:
    assert HERMES_SUBAGENT_CANONICAL in HERMES_SUBAGENT_SLUGS
    for slug in HERMES_SUBAGENT_SLUGS:
        assert _select_subagent_worker_kind(slug, "") == "hermes", slug
        assert _select_subagent_worker_kind(slug, "gemini-3-pro") == "hermes", slug


def test_the_alias_is_not_a_worker_switch() -> None:
    assert worker_provider_changed("hermes-agent", "hermes") is False
    assert worker_provider_changed("hermes", "grok-build") is True
