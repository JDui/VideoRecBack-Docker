from datetime import datetime
from importlib import import_module

from fastapi.testclient import TestClient

from app.config import Settings, save_settings


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


def test_calendar_filters_narrow_to_selected_year_and_month(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    app = main.create_app()
    videos = [
        ("old.mp4", datetime(2025, 3, 1).timestamp()),
        ("march.mp4", datetime(2026, 3, 1).timestamp()),
        ("april.mp4", datetime(2026, 4, 1).timestamp()),
    ]
    with app.state.db.connect() as conn:
        conn.executemany(
            """
            INSERT INTO videos(path, name, mtime, missing, type, size_bytes)
            VALUES (?, ?, ?, 0, 'flat', 1)
            """,
            [(str(tmp_path / name), name, mtime) for name, mtime in videos],
        )

    class YearRequest:
        query_params = {"view": "calendar", "calendar_zoom": "month", "calendar_year": "2026"}

    class MonthRequest:
        query_params = {
            "view": "calendar",
            "calendar_zoom": "day",
            "calendar_year": "2026",
            "calendar_month": "3",
        }

    year_rows = main.query_videos(app.state.db, main.read_filters(YearRequest()))
    month_rows = main.query_videos(app.state.db, main.read_filters(MonthRequest()))

    assert [row["name"] for row in year_rows] == ["april.mp4", "march.mp4"]
    assert [row["name"] for row in month_rows] == ["march.mp4"]


def test_calendar_zoom_urls_disable_unreachable_levels(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    base_filters = main.read_filters(type("Request", (), {"query_params": {"view": "calendar"}})())
    year_filters = {
        **base_filters,
        "calendar_zoom": "month",
        "calendar_year": "2026",
    }

    assert main.calendar_zoom_urls(base_filters)["month"] == "#"
    assert main.calendar_zoom_urls(base_filters)["day"] == "#"
    assert main.calendar_zoom_urls(year_filters)["month"] != "#"
    assert main.calendar_zoom_urls(year_filters)["day"] == "#"


def test_settings_page_includes_thumbnail_refresh(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    app = main.create_app()

    with TestClient(app) as client:
        response = client.get("/settings?thumbnail_refresh=2")

    assert response.status_code == 200
    assert 'action="/settings/refresh-thumbnails"' in response.text
    assert 'action="/settings/recheck-panorama-types"' in response.text
    assert 'name="default_flat_quality"' in response.text
    assert 'name="default_panorama_quality"' in response.text
    assert 'name="thumbnail_resolution"' in response.text
    assert '<option value="ultra"' in response.text
    assert "确认要刷新所有封面吗" in response.text
    assert "确认要对数据库中所有视频重新校验全景类型吗" in response.text
    assert "已提交刷新 2 个视频封面" in response.text


def test_panorama_recheck_route_promotes_wide_video(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    monkeypatch.setattr("app.scanner.generate_thumbnail", lambda *args: None)
    app = main.create_app()
    video_path = tmp_path / "media" / "wide.mp4"
    video_path.parent.mkdir()
    video_path.write_bytes(b"video")
    with app.state.db.connect() as conn:
        conn.execute(
            """
            INSERT INTO videos(
                id, path, name, mtime, missing, type, duration_seconds, width, height, thumb_status, thumb_version
            )
            VALUES (1, ?, 'wide.mp4', 1, 0, 'flat', 12, 3840, 1920, 'ready', 7)
            """,
            (str(video_path),),
        )

    with TestClient(app) as client:
        response = client.post("/settings/recheck-panorama-types", follow_redirects=False)

    with app.state.db.connect() as conn:
        row = conn.execute("SELECT type FROM videos WHERE id = 1").fetchone()

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?panorama_recheck=1"
    assert row["type"] == "panorama"


def test_panorama_play_page_includes_hls_overlay_and_progress(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    save_settings(tmp_path / "config", Settings(default_flat_quality="ultra", default_panorama_quality="low"))
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
    assert 'data-seek-control' in response.text
    assert "progress-strip" in response.text
    assert 'data-quality-menu' in response.text
    assert 'data-quality-option="ultra">超清' in response.text
    assert 'data-quality-option="low">高清' in response.text
    assert 'data-quality-option="high">流畅' in response.text
    assert 'data-default-quality="low"' in response.text
    assert 'src="/media/1"' not in response.text
    assert 'preload="none"' in response.text
    assert 'data-pano-progress' not in response.text


def test_flat_play_page_uses_overlay_controls_without_bottom_progress(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    save_settings(tmp_path / "config", Settings(default_flat_quality="ultra", default_panorama_quality="low"))
    app = main.create_app()
    video_path = tmp_path / "media" / "flat.mp4"
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
            VALUES (1, ?, 'flat.mp4', 'flat.mp4', '/', 'flat', 5, 12, 640, 360, 1.7778, 1, 0, 'error', 0)
            """,
            (str(video_path),),
        )

    with TestClient(app) as client:
        response = client.get("/video/1/play")

    assert response.status_code == 200
    assert 'data-quality-menu' in response.text
    assert 'data-quality-option="ultra">超清' in response.text
    assert 'data-default-quality="ultra"' in response.text
    assert 'src="/media/1"' not in response.text
    assert 'preload="none"' in response.text
    assert 'data-flat-controls' in response.text
    assert 'class="flat-player-progress"' in response.text
    assert 'class="progress-strip"' not in response.text
    assert 'class="flat-player-bar"' not in response.text
    assert 'data-flat-play' in response.text


def test_timeline_rail_exposes_year_month_day_buckets(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    rows = [
        {"mtime": datetime(2026, 1, 10).timestamp()},
        {"mtime": datetime(2026, 3, 20).timestamp()},
        {"mtime": datetime(2025, 11, 5).timestamp()},
    ]

    rail = main.build_timeline_rail(rows)

    assert [mark["kind"] for mark in rail[0]["marks"]] == ["day", "month", "day", "month", "year"]
    day_marks = [mark for mark in rail[0]["marks"] if mark["kind"] == "day"]
    assert [(mark["label"], mark["count"]) for mark in day_marks] == [("3/20", 1), ("1/10", 1)]
    assert [(mark["label"], mark["count"]) for mark in rail[1]["marks"] if mark["kind"] == "day"] == [("11/5", 1)]


def test_timeline_rail_month_bucket_points_to_real_section(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    rows = [{"mtime": datetime(2026, 1, 10).timestamp()}]

    rail = main.build_timeline_rail(rows)

    month = next(mark for mark in rail[0]["marks"] if mark["kind"] == "month")
    assert month["href"] == "#timeline-2026-01"
    assert month["target"] == "#timeline-2026-01-10"


def test_timeline_rail_skips_empty_periods(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    rows = [
        {"mtime": datetime(2026, 1, 10).timestamp()},
        {"mtime": datetime(2025, 7, 8).timestamp()},
    ]

    rail = main.build_timeline_rail(rows)

    assert [
        (year["year"], mark["label"], mark["count"])
        for year in rail
        for mark in year["marks"]
        if mark["kind"] == "day"
    ] == [
        (2026, "1/10", 1),
        (2025, "7/8", 1),
    ]


def test_timeline_rail_clamps_old_videos_to_2010_with_real_target(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    rows = [
        {"mtime": datetime(2006, 5, 6).timestamp()},
        {"mtime": datetime(2026, 1, 10).timestamp()},
    ]

    rail = main.build_timeline_rail(rows)

    assert [year["year"] for year in rail] == [2026, 2010]
    old_mark = next(mark for mark in rail[1]["marks"] if mark["kind"] == "day")
    assert old_mark["label"] == "2010前"
    assert old_mark["href"] == "#timeline-2010-01-01"
    assert old_mark["target"] == "#timeline-2006-05-06"


def test_timeline_groups_include_calendar_data(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    rows = [{"mtime": datetime(2026, 7, 8).timestamp()}]

    groups = main.group_by_date(rows)

    assert groups[0]["year"] == 2026
    assert groups[0]["month"] == 7
    assert groups[0]["day"] == 8


def test_refresh_all_thumbnails_route_marks_background_pending(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    calls = []
    app = main.create_app()

    def rebuild_all(settings):
        calls.append(settings.thumbnail_resolution)

    app.state.scanner.rebuild_all_thumbnails = rebuild_all
    with app.state.db.connect() as conn:
        conn.execute(
            """
            INSERT INTO videos(path, name, mtime, missing, type, size_bytes)
            VALUES (?, 'flat.mp4', 1, 0, 'flat', 1)
            """,
            (str(tmp_path / "flat.mp4"),),
        )

    response = TestClient(app).post("/settings/refresh-thumbnails", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?thumbnail_refresh=1"
    assert calls == [576]
