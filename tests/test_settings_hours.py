import json

from app.config import load_settings


def test_loads_legacy_scan_minutes_as_hours(tmp_path):
    (tmp_path / "settings.json").write_text(
        json.dumps({"scan_interval_minutes": 180}),
        encoding="utf-8",
    )

    settings = load_settings(tmp_path)

    assert settings.scan_interval_hours == 3
