"""In-app routing controls: ``/api/brain/route-policy`` and the config writer.

Pins what the Brain tab relies on: a save validates before writing, applies to
the running Brain from the next turn, keeps escalation explicit (no phrase, no
escalation), survives a reload from disk, and can be rolled back.
"""

from __future__ import annotations

import functools
from pathlib import Path

import pytest
import tomlkit
from fastapi.testclient import TestClient

import jarvis.core.config_writer as writer
from jarvis.core.bus import EventBus
from jarvis.core.config import BrainRoutePolicyConfig, JarvisConfig
from jarvis.ui.web.server import WebServer


class _Brain:
    """Just the surface the routes touch: the provider list and the setter."""

    def __init__(self) -> None:
        self.applied: list[BrainRoutePolicyConfig] = []

    def available_providers(self) -> list[str]:
        return ["nous", "local-openai", "gemini"]

    def set_route_policy(self, policy: BrainRoutePolicyConfig) -> None:
        self.applied.append(policy)


@pytest.fixture
def toml_path(tmp_path: Path) -> Path:
    path = tmp_path / "jarvis.toml"
    path.write_text('[brain]\nprimary = "gemini"\n', encoding="utf-8")
    return path


@pytest.fixture
def server(toml_path: Path, monkeypatch: pytest.MonkeyPatch) -> WebServer:
    monkeypatch.setattr(
        writer, "set_route_policy", functools.partial(writer.set_route_policy, path=toml_path)
    )
    monkeypatch.setattr(
        writer,
        "restore_previous_route_policy",
        functools.partial(writer.restore_previous_route_policy, path=toml_path),
    )
    original_backup = writer.route_policy_backup_path
    monkeypatch.setattr(
        writer, "route_policy_backup_path", lambda path=toml_path: original_backup(path)
    )
    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    srv = WebServer(cfg, bus=EventBus())
    srv.app.state.config = cfg
    srv.app.state.brain = _Brain()
    return srv


FAST_STEP = {"provider": "nous", "model": "stepfun/step-3.7-flash:free", "local": False}
DEEP_QWEN = {"provider": "local-openai", "model": "qwen", "local": True}


def _disk_policy(path: Path) -> dict:
    return tomlkit.parse(path.read_text(encoding="utf-8"))["brain"]["route_policy"].unwrap()


def test_defaults_are_off_and_cloud_vision_off(server: WebServer) -> None:
    with TestClient(server.app) as client:
        body = client.get("/api/brain/route-policy").json()
    assert body["policy"]["enabled"] is False
    assert body["policy"]["allow_cloud_vision"] is False
    assert body["policy"]["escalation"]["enabled"] is False
    assert "test_override_tier" not in body["policy"]
    assert body["can_restore"] is False


def test_save_persists_and_applies_to_the_running_brain(
    server: WebServer, toml_path: Path
) -> None:
    with TestClient(server.app) as client:
        resp = client.put(
            "/api/brain/route-policy",
            json={"enabled": True, "fast": FAST_STEP, "deep": DEEP_QWEN},
        )
    assert resp.status_code == 200, resp.text
    disk = _disk_policy(toml_path)
    assert disk["enabled"] is True
    assert disk["fast"] == FAST_STEP
    assert disk["deep"] == DEEP_QWEN
    assert "test_override_tier" not in disk
    # Applied live: the Brain and the app config hold the new table.
    applied = server.app.state.brain.applied[-1]
    assert applied.deep.provider == "local-openai" and applied.deep.local is True
    assert server.app.state.config.brain.route_policy.fast.model == FAST_STEP["model"]
    # And a fresh load from disk (a restart) reads the same thing.
    reloaded = JarvisConfig.model_validate(tomlkit.parse(toml_path.read_text()).unwrap())
    assert reloaded.brain.route_policy.deep.model == "qwen"
    assert resp.json()["can_restore"] is True


def test_escalation_without_a_phrase_is_refused_and_nothing_is_written(
    server: WebServer, toml_path: Path
) -> None:
    before = toml_path.read_text()
    with TestClient(server.app) as client:
        resp = client.put(
            "/api/brain/route-policy",
            json={"escalation": {"enabled": True, "agent": "claude", "trigger_phrases": [" "]}},
        )
    assert resp.status_code == 422
    assert toml_path.read_text() == before
    assert server.app.state.brain.applied == []


def test_unknown_provider_and_bad_values_are_refused(server: WebServer, toml_path: Path) -> None:
    before = toml_path.read_text()
    with TestClient(server.app) as client:
        unknown = client.put(
            "/api/brain/route-policy", json={"deep": {"provider": "made-up", "model": "x"}}
        )
        invalid = client.put(
            "/api/brain/route-policy",
            json={"escalation": {"enabled": False, "deadline_s": 1}},
        )
    assert unknown.status_code == 404
    assert invalid.status_code == 422
    assert toml_path.read_text() == before


def test_restore_rolls_back_and_a_second_restore_redoes(
    server: WebServer, toml_path: Path
) -> None:
    with TestClient(server.app) as client:
        client.put("/api/brain/route-policy", json={"enabled": True, "deep": DEEP_QWEN})
        undone = client.post("/api/brain/route-policy/restore")
        assert undone.status_code == 200
        assert "route_policy" not in tomlkit.parse(toml_path.read_text())["brain"]
        assert server.app.state.brain.applied[-1].enabled is False
        redone = client.post("/api/brain/route-policy/restore")
    assert redone.status_code == 200
    assert _disk_policy(toml_path)["deep"] == DEEP_QWEN
    assert server.app.state.brain.applied[-1].enabled is True


def test_restore_without_an_earlier_save_says_so(server: WebServer) -> None:
    with TestClient(server.app) as client:
        resp = client.post("/api/brain/route-policy/restore")
    assert resp.status_code == 404


def test_writer_keeps_unrelated_keys_and_a_test_pin(toml_path: Path) -> None:
    toml_path.write_text(
        '# keep me\n[brain]\nprimary = "gemini"\n\n[brain.route_policy]\n'
        'test_override_tier = "deep"\nenabled = false\n',
        encoding="utf-8",
    )
    writer.set_route_policy({"enabled": True, "unknown_key": 1}, path=toml_path)
    text = toml_path.read_text()
    assert "# keep me" in text and 'primary = "gemini"' in text
    disk = _disk_policy(toml_path)
    assert disk["enabled"] is True
    assert disk["test_override_tier"] == "deep"
    assert "unknown_key" not in disk
