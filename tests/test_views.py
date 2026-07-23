from datetime import datetime, timedelta
from importlib import import_module

from fastapi.testclient import TestClient

from app.config import Settings, save_settings
from app.thumbnails import ProbeResult


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


def test_calendar_renders_unavailable_zoom_levels_as_disabled_buttons(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    app = main.create_app()
    with app.state.db.connect() as conn:
        conn.execute(
            """
            INSERT INTO videos(path, name, mtime, missing, type, size_bytes)
            VALUES (?, 'flat.mp4', ?, 0, 'flat', 1)
            """,
            (str(tmp_path / "flat.mp4"), datetime(2026, 7, 8).timestamp()),
        )

    with TestClient(app) as client:
        response = client.get("/?view=calendar")

    assert response.status_code == 200
    assert 'href="#"' not in response.text
    assert 'disabled aria-label="月视图需要先选择对应日期"' in response.text
    assert 'disabled aria-label="日视图需要先选择对应日期"' in response.text


def test_settings_page_includes_thumbnail_refresh(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    app = main.create_app()

    with TestClient(app) as client:
        response = client.get("/settings?thumbnail_refresh=2")

    assert response.status_code == 200
    assert 'action="/settings/refresh-thumbnails"' in response.text
    assert 'action="/settings/recheck-panorama-types"' in response.text
    assert 'action="/settings/recheck-all-video-data"' in response.text
    assert 'name="default_flat_quality"' in response.text
    assert 'name="default_panorama_quality"' in response.text
    assert 'name="thumbnail_resolution"' in response.text
    assert 'name="flat_hls_encoder"' in response.text
    assert 'name="panorama_hls_encoder"' in response.text
    assert 'name="intranet_keepalive_enabled"' in response.text
    assert 'name="intranet_redirect_host"' in response.text
    assert 'name="intranet_redirect_port"' in response.text
    assert 'name="intranet_redirect_protocol"' in response.text
    assert "内网直连" in response.text
    assert "服务器连通测试" in response.text
    assert "/static/intranet.js?v=2.7" in response.text
    assert "/static/settings.js?v=2.7-r1" in response.text
    assert '<option value="ultra"' in response.text
    assert "需要确认的操作" in response.text
    assert "确认要刷新所有封面吗" in response.text
    assert "确认要对数据库中所有视频重新校验全景类型吗" in response.text
    assert "确认要重校验全部视频数据吗" in response.text
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
    assert 'data-video-bit-depth="8"' in response.text
    assert 'src="/media/1"' not in response.text
    assert 'preload="none"' in response.text
    assert 'data-pano-progress' not in response.text


def test_flat_play_page_keeps_controls_outside_video_stage(monkeypatch, tmp_path):
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
    assert 'data-favorite-toggle' in response.text
    assert 'data-default-quality="ultra"' in response.text
    assert 'src="/media/1"' not in response.text
    assert 'preload="none"' in response.text
    assert 'data-flat-controls' in response.text
    assert 'class="flat-player-progress"' in response.text
    assert 'class="progress-strip"' not in response.text
    assert 'class="flat-player-bar"' in response.text
    assert 'class="flat-player-overlay"' not in response.text
    assert response.text.index('class="player-stage') < response.text.index('class="flat-player-bar"')
    assert 'data-flat-play' in response.text
    assert 'class="flat-play-glyph"' in response.text
    assert 'aria-label="后退10秒"' in response.text
    assert 'aria-label="前进10秒"' in response.text


def test_play_page_lazily_records_tenbit_status(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    monkeypatch.setattr("app.main.probe_video", lambda path: ProbeResult(12, 1920, 1080, 10, "hevc"))
    app = main.create_app()
    video_path = tmp_path / "media" / "tenbit.mp4"
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
            VALUES (1, ?, 'tenbit.mp4', 'tenbit.mp4', '/', 'flat', 5, 12, 640, 360, 1.7778, 1, 0, 'error', 0)
            """,
            (str(video_path),),
        )

    with TestClient(app) as client:
        response = client.get("/video/1/play")

    with app.state.db.connect() as conn:
        row = conn.execute("SELECT bit_depth, is_10bit, video_codec FROM videos WHERE id = 1").fetchone()

    assert response.status_code == 200
    assert 'data-video-bit-depth="10"' in response.text
    assert row["bit_depth"] == 10
    assert row["is_10bit"] == 1
    assert row["video_codec"] == "hevc"


def test_play_page_skips_tenbit_detection_when_status_exists(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)

    def fail_probe(path):
        raise AssertionError("probe should not run")

    monkeypatch.setattr("app.main.probe_video", fail_probe)
    app = main.create_app()
    video_path = tmp_path / "media" / "flat.mp4"
    video_path.parent.mkdir()
    video_path.write_bytes(b"video")
    with app.state.db.connect() as conn:
        conn.execute(
            """
            INSERT INTO videos(
                id, path, name, relative_path, folder, type, size_bytes,
                bit_depth, is_10bit, mtime, missing, thumb_status, thumb_version
            )
            VALUES (1, ?, 'flat.mp4', 'flat.mp4', '/', 'flat', 5, 8, 0, 1, 0, 'error', 0)
            """,
            (str(video_path),),
        )

    with TestClient(app) as client:
        response = client.get("/video/1/play")

    assert response.status_code == 200
    assert 'data-video-bit-depth="8"' in response.text


def test_embed_play_page_hides_inner_favorite_for_parent_chrome(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
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
        response = client.get("/video/1/play?embed=1")

    assert response.status_code == 200
    assert 'data-favorite-toggle' not in response.text


def test_favorite_route_updates_video_and_returns_json(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    app = main.create_app()
    video_path = tmp_path / "media" / "flat.mp4"
    video_path.parent.mkdir()
    video_path.write_bytes(b"video")
    with app.state.db.connect() as conn:
        conn.execute(
            """
            INSERT INTO videos(path, name, mtime, missing, type, size_bytes)
            VALUES (?, 'flat.mp4', 1, 0, 'flat', 1)
            """,
            (str(video_path),),
        )

    with TestClient(app) as client:
        response = client.post("/video/1/favorite", data={"favorite": "1"})

    with app.state.db.connect() as conn:
        row = conn.execute("SELECT favorite FROM videos WHERE id = 1").fetchone()

    assert response.status_code == 200
    assert response.json() == {"ok": True, "favorite": True}
    assert row["favorite"] == 1


def test_favorites_view_filters_and_exposes_context_actions(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    app = main.create_app()
    with app.state.db.connect() as conn:
        conn.executemany(
            """
            INSERT INTO videos(path, name, mtime, missing, type, size_bytes, favorite)
            VALUES (?, ?, ?, 0, 'flat', 1, ?)
            """,
            [
                (str(tmp_path / "fav.mp4"), "fav.mp4", datetime(2026, 7, 8).timestamp(), 1),
                (str(tmp_path / "plain.mp4"), "plain.mp4", datetime(2026, 7, 9).timestamp(), 0),
            ],
        )

    with TestClient(app) as client:
        response = client.get("/?view=favorites")

    assert response.status_code == 200
    assert ">收藏</a>" in response.text
    assert 'data-intranet-jump hidden>跳转内网</button>' in response.text
    assert "fav.mp4" in response.text
    assert "plain.mp4" not in response.text
    assert 'data-favorite-menu="1"' in response.text
    assert "跳转到时间线位置" in response.text
    assert 'data-timeline-url="/?view=timeline#timeline-2026-07-08"' in response.text


def test_folder_browser_counts_nested_videos(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    rows = [
        {"folder": "旅行", "name": "root.mp4"},
        {"folder": "旅行/全景/2025", "name": "pano.mp4"},
        {"folder": "城市/夜景", "name": "city.mp4"},
    ]

    root = main.build_folder_browser(rows, {"view": "folders", "folder": ""})
    travel = main.build_folder_browser(rows, {"view": "folders", "folder": "旅行"})

    assert root["total_count"] == 3
    assert [(item["name"], item["count"]) for item in root["folders"]] == [("城市", 1), ("旅行", 2)]
    assert travel["total_count"] == 2
    assert [row["name"] for row in travel["files"]] == ["root.mp4"]


def test_detail_page_exposes_working_navigation_and_actions(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    app = main.create_app()
    with app.state.db.connect() as conn:
        conn.executemany(
            """
            INSERT INTO videos(id, path, name, folder, mtime, missing, type, size_bytes)
            VALUES (?, ?, ?, ?, ?, 0, 'flat', 1)
            """,
            [
                (1, str(tmp_path / "new.mp4"), "new.mp4", "旅行/全景/2025", 2),
                (2, str(tmp_path / "old.mp4"), "old.mp4", "旅行/全景/2024", 1),
            ],
        )

    with TestClient(app) as client:
        response = client.get("/video/1")

    assert response.status_code == 200
    assert 'class="detail-neighbor disabled" aria-disabled="true"' in response.text
    assert 'class="detail-neighbor" href="/video/2">下一条' in response.text
    assert 'action="/video/1/favorite"' in response.text
    assert 'action="/video/1/refresh-thumbnail"' in response.text
    assert 'href="/?view=folders&amp;folder=%E6%97%85%E8%A1%8C%2F%E5%85%A8%E6%99%AF%2F2025"' in response.text
    assert "点击添加备注" not in response.text
    assert 'aria-label="添加标签"' not in response.text


def test_refresh_single_thumbnail_route_rebuilds_selected_video(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    app = main.create_app()
    calls = []
    app.state.scanner.rebuild_video_thumbnail = lambda video_id, video_type: calls.append((video_id, video_type))
    with app.state.db.connect() as conn:
        conn.execute(
            """
            INSERT INTO videos(
                id, path, name, folder, mtime, missing, type, size_bytes,
                thumb_status, thumb_version, thumb_error
            )
            VALUES (1, ?, 'flat.mp4', '旅行', 1, 0, 'flat', 1, 'error', 7, 'failed')
            """,
            (str(tmp_path / "flat.mp4"),),
        )

    with TestClient(app) as client:
        response = client.post("/video/1/refresh-thumbnail", follow_redirects=False)

    with app.state.db.connect() as conn:
        video = conn.execute(
            "SELECT thumb_status, thumb_version, thumb_error FROM videos WHERE id = 1"
        ).fetchone()

    assert response.status_code == 303
    assert response.headers["location"] == "/video/1"
    assert calls == [(1, "flat")]
    assert video["thumb_status"] == "pending"
    assert video["thumb_version"] == 0
    assert video["thumb_error"] is None


def test_connectivity_test_endpoints(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    app = main.create_app()

    with TestClient(app) as client:
        ping = client.get("/settings/connectivity-test/ping")
        download = client.get("/settings/connectivity-test/download?size=1048576")

    assert ping.status_code == 200
    assert ping.json()["ok"] is True
    assert download.status_code == 200
    assert download.headers["content-length"] == str(2 * 1024 * 1024)
    assert len(download.content) == 2 * 1024 * 1024


def test_intranet_health_allows_browser_direct_probe(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    app = main.create_app()

    with TestClient(app) as client:
        response = client.get("/intranet/health")
        preflight = client.options("/intranet/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "service": "videorecback"}
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.headers["cache-control"] == "no-store"
    assert preflight.status_code == 204
    assert preflight.headers["access-control-allow-private-network"] == "true"


def test_hls_encoder_is_selected_by_video_type(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    settings = Settings(flat_hls_encoder="h264_qsv", panorama_hls_encoder="libx264_veryfast")

    assert main.hls_encoder_for_video(settings, {"type": "flat"}) == "h264_qsv"
    assert main.hls_encoder_for_video(settings, {"type": "panorama"}) == "libx264_veryfast"


def test_settings_sync_splits_hls_encoder_keys(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    app = main.create_app()
    with app.state.db.connect() as conn:
        conn.execute("INSERT INTO app_settings(key, value) VALUES ('hls_encoder', 'h264_qsv')")
        conn.execute("INSERT INTO app_settings(key, value) VALUES ('intranet_probe_host', '192.168.31.1')")
        conn.execute("INSERT INTO app_settings(key, value) VALUES ('intranet_redirect_host', '192.168.31.20')")

    main.sync_settings_to_db(
        app.state.db,
        Settings(
            flat_hls_encoder="h264_qsv",
            panorama_hls_encoder="libx264_veryfast",
            intranet_redirect_host="192.168.31.20",
            intranet_redirect_protocol="https",
        ),
    )

    with app.state.db.connect() as conn:
        values = {
            row["key"]: row["value"]
            for row in conn.execute("SELECT key, value FROM app_settings").fetchall()
        }

    assert values["flat_hls_encoder"] == "h264_qsv"
    assert values["panorama_hls_encoder"] == "libx264_veryfast"
    assert values["intranet_keepalive_enabled"] == "0"
    assert values["intranet_redirect_host"] == "192.168.31.20"
    assert values["intranet_redirect_port"] == ""
    assert values["intranet_redirect_protocol"] == "https"
    assert "hls_encoder" not in values
    assert "intranet_probe_host" not in values


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
    assert month["target"] == "#timeline-2026-01"


def test_dense_timeline_rail_targets_existing_month_page(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    start = datetime(2026, 1, 1)
    rows = [{"mtime": (start + timedelta(days=index)).timestamp()} for index in range(91)]

    groups = main.build_timeline_groups(rows)
    rail = main.build_timeline_rail(rows)

    assert groups[0]["granularity"] == "month"
    assert {group["anchor"] for group in groups} >= {"timeline-2026-01", "timeline-2026-02", "timeline-2026-03"}
    january_day = next(
        mark
        for year in rail
        for mark in year["marks"]
        if mark["kind"] == "day" and mark["period"] == "2026-01-15"
    )
    assert january_day["target"] == "#timeline-2026-01-15"


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


def test_timeline_cache_excludes_video_rows(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    rows = [{"mtime": datetime(2026, 7, 8).timestamp()}]
    groups = main.build_timeline_groups(rows)
    rail = main.build_timeline_rail(rows)

    cache = main.build_timeline_cache(groups, rail, {"view": "timeline", "type": "flat"})

    assert cache["filters"]["type"] == "flat"
    assert cache["groups"][0]["anchor"] == "timeline-2026-07"
    assert cache["groups"][0]["count"] == 1
    assert cache["groups"][0]["days"][0]["anchor"] == "timeline-2026-07-08"
    assert "videos" not in cache["groups"][0]


def test_index_embeds_timeline_cache_and_lazy_thumbnails(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    app = main.create_app()
    with app.state.db.connect() as conn:
        conn.execute(
            """
            INSERT INTO videos(path, name, mtime, missing, type, size_bytes, thumb_status, bit_depth)
            VALUES (?, 'flat.mp4', ?, 0, 'flat', 1, 'ready', 10)
            """,
            (str(tmp_path / "flat.mp4"), datetime(2026, 7, 8).timestamp()),
        )

    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "data-timeline-cache=" in response.text
    assert 'data-inline-favorite' in response.text
    assert 'data-favorite-state="0"' in response.text
    assert 'class="asset-bit-depth">10bit</span>' in response.text
    assert "/static/app.js?v=2.7-r1" in response.text
    assert '"anchor": "timeline-2026-07"' in response.text
    assert '"anchor": "timeline-2026-07-08"' in response.text
    assert 'loading="lazy"' in response.text


def test_timeline_uses_batched_rendering(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    app = main.create_app()
    now = datetime(2026, 7, 8).timestamp()
    with app.state.db.connect() as conn:
        conn.executemany(
            """
            INSERT INTO videos(path, name, mtime, missing, type, size_bytes)
            VALUES (?, ?, ?, 0, 'flat', 1)
            """,
            [
                (str(tmp_path / f"video-{index}.mp4"), f"video-{index}.mp4", now - index)
                for index in range(400)
            ],
        )

    with TestClient(app) as client:
        first = client.get("/")
        cursor_mtime = first.text.split('data-next-mtime="', 1)[1].split('"', 1)[0]
        cursor_id = first.text.split('data-next-id="', 1)[1].split('"', 1)[0]
        second = client.get(
            "/timeline-batch",
            params={"view": "timeline", "cursor_mtime": cursor_mtime, "cursor_id": cursor_id},
        )

    assert first.status_code == 200
    assert first.text.count("data-video-id=") == main.TIMELINE_PAGE_SIZE
    assert 'data-has-more="1"' in first.text
    assert second.status_code == 200
    assert second.json()["html"].count("data-video-id=") == main.TIMELINE_PAGE_SIZE
    assert second.json()["has_more"] is True


def test_intranet_health_gif_is_fast_probe_target(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    app = main.create_app()

    with TestClient(app) as client:
        response = client.get("/intranet/health.gif")
        preflight = client.options("/intranet/health.gif")

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/gif"
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.content.startswith(b"GIF89a")
    assert preflight.status_code == 204
    assert preflight.headers["access-control-allow-private-network"] == "true"


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


def test_recheck_all_video_data_route_marks_background_pending(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    calls = []
    app = main.create_app()

    async def recheck_all():
        calls.append("recheck")

    app.state.scanner.recheck_all_video_metadata = recheck_all
    with app.state.db.connect() as conn:
        conn.execute(
            """
            INSERT INTO videos(path, name, mtime, missing, type, size_bytes)
            VALUES (?, 'flat.mp4', 1, 0, 'flat', 1)
            """,
            (str(tmp_path / "flat.mp4"),),
        )

    response = TestClient(app).post("/settings/recheck-all-video-data", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/settings?metadata_recheck=1"
    assert calls == ["recheck"]
