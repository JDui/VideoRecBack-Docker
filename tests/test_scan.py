import asyncio
from importlib import import_module

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, save_settings


@pytest.fixture
def scan_app(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    save_settings(tmp_path / "config", Settings(scan_interval_hours=0))
    main = import_module("app.main")
    app = main.create_app()
    calls = []

    async def fake_scan(target, settings):
        calls.append(settings.video_root)
        target.state.manual_scan_pending = False

    async def idle_worker(target):
        await asyncio.Event().wait()

    monkeypatch.setattr(main, "background_maintenance", idle_worker)
    monkeypatch.setattr(main, "background_media_worker", idle_worker)
    monkeypatch.setattr(main, "run_manual_scan", fake_scan)
    return app, calls


def test_scan_json_starts_without_redirect_and_reuses_running_scan(scan_app):
    app, calls = scan_app
    with TestClient(app) as client:
        response = client.post("/scan", headers={"Accept": "application/json"}, follow_redirects=False)
        assert response.status_code == 200
        assert response.json() == {"ok": True, "started": True, "scanning": True}
        assert "location" not in response.headers
        app.state.scanner._running_count = 1
        response = client.post("/scan", headers={"Accept": "application/json"})
        assert response.json() == {"ok": True, "started": False, "scanning": True}
    assert len(calls) == 1


@pytest.mark.parametrize("referer, target", [
    ("http://testserver/library?view=calendar&type=flat", "/library?view=calendar&type=flat&scan=running"),
    ("http://testserver/?view=folders&folder=travel&scan=old", "/?view=folders&folder=travel&scan=running"),
    ("http://testserver/", "/?scan=running"),
    ("https://example.com/library", "/library?scan=running"),
    ("http://testserver/settings", "/library?scan=running"),
    ("", "/library?scan=running"),
])
def test_scan_form_fallback_keeps_local_view_and_rejects_external_redirects(scan_app, referer, target):
    app, _ = scan_app
    app.state.manual_scan_pending = True
    with TestClient(app) as client:
        response = client.post("/scan", headers={"Referer": referer}, follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == target
