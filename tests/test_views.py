from datetime import datetime
from importlib import import_module

from fastapi.testclient import TestClient


def load_main(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    return import_module("app.main")


def test_calendar_defaults_to_year(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)

    class Request:
        query_params = {"view": "calendar"}

    filters = main.read_filters(Request())

    assert filters["view"] == "calendar"
    assert filters["calendar_zoom"] == "year"


def test_settings_page_includes_panorama_thumbnail_refresh(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    app = main.create_app()

    with TestClient(app) as client:
        response = client.get("/settings?panorama_refresh=2")

    assert response.status_code == 200
    assert 'action="/settings/refresh-panorama-thumbnails"' in response.text
    assert "确认要刷新全部全景视频封面吗" in response.text
    assert "已提交刷新 2 个全景视频封面" in response.text


def test_panorama_play_page_includes_hls_overlay_and_progress(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    app = main.create_app()
    video_path = tmp_path / "media" / "pano.mp4"
    video_path.parent.mkdir()
    video_path.write_bytes(b"video")
    with app.state.db.connect() as conn:
        conn.execute(
            """
            INSERT INTO videos(
                id, path, name, relative_path, folder, type, size_bytes,
                duration_seconds, width, height, aspect_ratio, mtime,
                missing, thumb_status, thumb_version
            )
            VALUES (1, ?, 'pano.mp4', 'pano.mp4', '/', 'panorama', 5, 12, 640, 320, 2.0, 1, 0, 'error', 0)
            """,
            (str(video_path),),
        )

    with TestClient(app) as client:
        response = client.get("/video/1/play")

    assert response.status_code == 200
    assert "/static/vendor/hls.min.js" in response.text
    assert 'data-transcode-overlay hidden' in response.text
    assert 'data-pano-progress' in response.text
    assert response.text.count('data-pano-progress-hit') == 4


def test_timeline_rail_groups_by_quarter(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    rows = [
        {"mtime": datetime(2026, 1, 10).timestamp()},
        {"mtime": datetime(2026, 3, 20).timestamp()},
        {"mtime": datetime(2025, 11, 5).timestamp()},
    ]

    rail = main.build_timeline_rail(rows)

    assert rail == [
        {
            "year": 2026,
            "quarters": [
                {"year": 2026, "quarter": 4, "label": "Q4", "count": 0, "labels": []},
                {"year": 2026, "quarter": 3, "label": "Q3", "count": 0, "labels": []},
                {"year": 2026, "quarter": 2, "label": "Q2", "count": 0, "labels": []},
                {"year": 2026, "quarter": 1, "label": "Q1", "count": 2, "labels": []},
            ],
        },
        {
            "year": 2025,
            "quarters": [
                {"year": 2025, "quarter": 4, "label": "Q4", "count": 1, "labels": []},
                {"year": 2025, "quarter": 3, "label": "Q3", "count": 0, "labels": []},
                {"year": 2025, "quarter": 2, "label": "Q2", "count": 0, "labels": []},
                {"year": 2025, "quarter": 1, "label": "Q1", "count": 0, "labels": []},
            ],
        },
    ]


def test_timeline_rail_attaches_labels(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    rows = [{"mtime": datetime(2026, 1, 10).timestamp()}]

    rail = main.build_timeline_rail(rows, {(2026, 1): [{"label": "春节", "color": "#ff0000"}]})

    assert rail[0]["quarters"][-1]["labels"] == [{"label": "春节", "color": "#ff0000"}]


def test_timeline_rail_fills_empty_quarters(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    rows = [
        {"mtime": datetime(2026, 1, 10).timestamp()},
        {"mtime": datetime(2025, 7, 8).timestamp()},
    ]

    rail = main.build_timeline_rail(rows)

    assert [
        (year["year"], quarter["quarter"], quarter["count"])
        for year in rail
        for quarter in year["quarters"]
    ] == [
        (2026, 4, 0),
        (2026, 3, 0),
        (2026, 2, 0),
        (2026, 1, 1),
        (2025, 4, 0),
        (2025, 3, 1),
        (2025, 2, 0),
        (2025, 1, 0),
    ]


def test_timeline_groups_include_quarter_anchor(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    rows = [{"mtime": datetime(2026, 7, 8).timestamp()}]

    groups = main.group_by_date(rows)

    assert groups[0]["year"] == 2026
    assert groups[0]["quarter"] == 3
