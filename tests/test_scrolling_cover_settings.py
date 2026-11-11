import json
from importlib import import_module

from fastapi.testclient import TestClient

from app.config import Settings, load_settings, normalize_scrolling_cover_cycle, save_settings


def test_scrolling_cover_defaults_and_persistence(tmp_path):
    settings = load_settings(tmp_path)
    assert settings.scrolling_cover_enabled is False
    assert settings.scrolling_cover_cycle_ms == 6000

    save_settings(tmp_path, Settings(scrolling_cover_enabled=True, scrolling_cover_cycle_ms=3600))
    settings = load_settings(tmp_path)
    assert settings.scrolling_cover_enabled is True
    assert settings.scrolling_cover_cycle_ms == 3600

    (tmp_path / "settings.json").write_text("{}", encoding="utf-8")
    assert load_settings(tmp_path).scrolling_cover_enabled is False
    assert load_settings(tmp_path).scrolling_cover_cycle_ms == 6000


def test_scrolling_cover_cycle_handles_invalid_and_out_of_range_values(tmp_path):
    assert normalize_scrolling_cover_cycle("bad") == 6000
    assert normalize_scrolling_cover_cycle(None) == 6000
    assert normalize_scrolling_cover_cycle(float("inf")) == 6000
    assert normalize_scrolling_cover_cycle(-1) == 1200
    assert normalize_scrolling_cover_cycle(99999) == 20000
    save_settings(tmp_path, Settings(scrolling_cover_cycle_ms=99999))
    assert json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))["scrolling_cover_cycle_ms"] == 20000
    assert load_settings(tmp_path).scrolling_cover_cycle_ms == 20000


def test_legacy_per_frame_setting_migrates_to_a_full_cycle(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"scrolling_cover_interval_ms": 800}), encoding="utf-8")
    settings = load_settings(tmp_path)
    assert settings.scrolling_cover_cycle_ms == 3200
    save_settings(tmp_path, settings)
    raw = json.loads((tmp_path / "settings.json").read_text(encoding="utf-8"))
    assert raw["scrolling_cover_cycle_ms"] == 3200
    assert "scrolling_cover_interval_ms" not in raw
    assert load_settings(tmp_path).scrolling_cover_cycle_ms == 3200


def test_settings_post_saves_and_disables_scrolling_cover(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    app = import_module("app.main").create_app()
    form = {
        "site_title": "视频归档",
        "video_root": "/media",
        "scan_interval_hours": "0",
        "default_volume_percent": "20",
        "video_extensions": ".mp4",
        "scrolling_cover_enabled": "on",
        "scrolling_cover_cycle_ms": "3200",
    }
    client = TestClient(app)
    response = client.post("/settings", data=form, follow_redirects=False)
    assert response.status_code == 303
    settings = load_settings(app.state.config_dir)
    assert settings.scrolling_cover_enabled is True
    assert settings.scrolling_cover_cycle_ms == 3200

    form.pop("scrolling_cover_enabled")
    form["scrolling_cover_cycle_ms"] = "100000"
    assert client.post("/settings", data=form, follow_redirects=False).status_code == 303
    settings = load_settings(app.state.config_dir)
    assert settings.scrolling_cover_enabled is False
    assert settings.scrolling_cover_cycle_ms == 20000
