from importlib import import_module

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, load_settings, save_settings


@pytest.fixture
def intranet_app(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    app = import_module("app.main").create_app()
    save_settings(app.state.config_dir, Settings(
        scan_interval_hours=0,
        intranet_keepalive_enabled=True,
        intranet_auto_redirect_enabled=True,
        intranet_redirect_host="192.168.1.10",
        intranet_redirect_port="8080",
    ))
    return app


@pytest.mark.parametrize("path", ["/", "/library?view=timeline", "/settings", "/video/1", "/video/1/play?embed=1"])
def test_redirect_preflight_runs_before_loading_page_data(intranet_app, monkeypatch, path):
    def unexpected_page_load(*args, **kwargs):
        pytest.fail("Page data must not load before the intranet preflight")

    main = import_module("app.main")
    for symbol in ("home_content", "query_videos", "get_video", "ensure_tenbit_status"):
        monkeypatch.setattr(main, symbol, unexpected_page_load)

    response = TestClient(intranet_app).get(path)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert 'data-intranet-preflight="1"' in response.text
    assert 'data-intranet-auto-redirect-enabled="1"' in response.text
    assert "_vbr_skip_intranet=1" in response.text
    assert "/thumb/" not in response.text
    assert "/static/player.js" not in response.text
    assert "/static/styles.css" not in response.text
    assert "VideoRecBackLoading" in response.text
    assert "正在检测内网连接" not in response.text


@pytest.mark.parametrize("changes", [
    {"intranet_auto_redirect_enabled": False},
    {"intranet_keepalive_enabled": False},
    {"intranet_redirect_host": ""},
])
def test_preflight_requires_both_switches_and_a_host(intranet_app, changes):
    settings = load_settings(intranet_app.state.config_dir)
    for name, value in changes.items():
        setattr(settings, name, value)
    save_settings(intranet_app.state.config_dir, settings)

    response = TestClient(intranet_app).get("/")

    assert response.status_code == 200
    assert 'data-intranet-preflight="1"' not in response.text
    assert 'class="home-page"' in response.text


@pytest.mark.parametrize("protocol, port, origin", [
    ("http", "8080", "http://192.168.1.10:8080"),
    ("http", "80", "http://192.168.1.10"),
    ("https", "", "https://192.168.1.10:443"),
])
def test_local_origin_skips_preflight(intranet_app, protocol, port, origin):
    settings = load_settings(intranet_app.state.config_dir)
    settings.intranet_redirect_protocol = protocol
    settings.intranet_redirect_port = port
    save_settings(intranet_app.state.config_dir, settings)

    response = TestClient(intranet_app, base_url=origin).get("/")

    assert response.status_code == 200
    assert 'data-intranet-preflight="1"' not in response.text
    assert 'class="home-page"' in response.text


def test_fallback_loads_original_view_and_cleans_bypass_parameter(intranet_app):
    response = TestClient(intranet_app).get("/library?view=folders&q=family&_vbr_skip_intranet=1")

    assert response.status_code == 200
    assert 'data-intranet-preflight="1"' not in response.text
    assert 'name="view" value="folders"' in response.text
    assert "q=family" in response.text
    assert 'pageUrl.searchParams.delete("_vbr_skip_intranet")' in response.text
    assert "history.replaceState" in response.text


def test_preflight_does_not_intercept_probes_assets_or_api(intranet_app):
    client = TestClient(intranet_app)

    assert client.get("/intranet/health").json() == {"ok": True, "service": "videorecback"}
    assert client.get("/intranet/health.gif").headers["content-type"] == "image/gif"
    assert client.get("/static/intranet.js").headers["content-type"].startswith("text/javascript")
    assert "html" in client.get("/home/random").json()


def test_settings_can_enable_and_disable_auto_redirect(intranet_app):
    client = TestClient(intranet_app)
    form = {
        "site_title": "视频归档", "video_root": "/media", "scan_interval_hours": "0",
        "default_volume_percent": "20", "video_extensions": ".mp4",
        "intranet_keepalive_enabled": "on", "intranet_auto_redirect_enabled": "on",
        "intranet_redirect_host": "192.168.1.10", "intranet_redirect_port": "8080",
    }
    for enabled in (True, False):
        if not enabled:
            del form["intranet_auto_redirect_enabled"]
        response = client.post("/settings", data=form, follow_redirects=False)
        assert response.status_code == 303
        assert load_settings(intranet_app.state.config_dir).intranet_auto_redirect_enabled is enabled
        with intranet_app.state.db.connect() as conn:
            assert conn.execute(
                "SELECT value FROM app_settings WHERE key = 'intranet_auto_redirect_enabled'"
            ).fetchone()["value"] == str(int(enabled))
        page = client.get("/settings?_vbr_skip_intranet=1")
        checkbox = page.text.split('name="intranet_auto_redirect_enabled"', 1)[1].split(">", 1)[0]
        assert ("checked" in checkbox) is enabled
