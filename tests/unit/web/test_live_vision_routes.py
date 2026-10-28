from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web import live_vision_routes


def test_missing_pose_assets_are_reported_without_installation(tmp_path, monkeypatch):
    monkeypatch.setattr(live_vision_routes, "asset_root", lambda: tmp_path)
    app = FastAPI(); app.include_router(live_vision_routes.router)
    with TestClient(app) as client:
        result = client.get("/api/live-vision/capabilities").json()
        assert result["tracking_ready"] is False
        assert "face_landmarker.task" in result["missing"]
        assert client.get("/api/live-vision/tracking/unknown.task").status_code == 404
    assert list(tmp_path.iterdir()) == []


def test_frame_body_limit_and_source_schema():
    from jarvis.ui.web.agent_chat_routes import MessageBody
    from pydantic import ValidationError
    import pytest

    with pytest.raises(ValidationError):
        MessageBody(vision_frames=[{"source": "microphone", "data_b64": "x", "captured_at_ms": 1}])


def test_stopping_one_window_preserves_another_windows_watch_card(monkeypatch):
    from jarvis.cu.indicator import controller
    calls = []
    class FakeIndicator:
        async def watch_badge(self, **kwargs):
            calls.append(kwargs)
            return True
    monkeypatch.setattr(controller, "wire_cu_indicator", lambda bus: FakeIndicator())
    app = FastAPI(); app.state.bus = object(); app.include_router(live_vision_routes.router)
    with TestClient(app) as client:
        for owner, visible in [("window-a", True), ("window-b", True), ("window-a", False)]:
            assert client.post("/api/live-vision/watch-indicator", json={"client_id": owner, "visible": visible}).status_code == 200
        assert calls[-1]["visible"] is True
        client.post("/api/live-vision/watch-indicator", json={"client_id": "window-b", "visible": False})
        assert calls[-1]["visible"] is False
