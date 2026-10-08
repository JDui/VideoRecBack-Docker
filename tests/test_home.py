from datetime import datetime
from importlib import import_module
from html import unescape
from urllib.parse import parse_qs, urlsplit
import re

from fastapi.testclient import TestClient

from app.config import Settings, load_settings, save_settings
from app.db import Database
from app.home import home_content, random_videos


def make_library(tmp_path):
    db = Database(tmp_path / "data")
    db.init()
    records = [
        ("recent.mp4", datetime(2026, 10, 2, 12), 0, 0, "ready"),
        ("favorite.mp4", datetime(2026, 10, 1, 12), 0, 1, "pending"),
        ("last-year.mp4", datetime(2025, 10, 2, 23, 59), 0, 1, "ready"),
        ("two-years.mp4", datetime(2024, 10, 2, 0, 0), 0, 0, "error"),
        ("other-day.mp4", datetime(2025, 10, 3), 0, 0, "ready"),
        ("removed.mp4", datetime(2026, 10, 2, 15), 1, 1, "ready"),
        ("future.mp4", datetime(2027, 10, 2), 0, 0, "ready"),
    ]
    with db.connect() as conn:
        conn.executemany(
            """
            INSERT INTO videos(path, name, mtime, missing, favorite, thumb_status, size_bytes)
            VALUES (?, ?, ?, ?, ?, ?, 100)
            """,
            [(str(tmp_path / name), name, date.timestamp(), missing, favorite, status)
             for name, date, missing, favorite, status in records],
        )
    return db


def test_home_limits_and_memories_exclude_today_future_and_missing(tmp_path):
    content = home_content(make_library(tmp_path), datetime(2026, 10, 2, 16))
    assert [row["name"] for row in content["memories"]] == ["last-year.mp4", "two-years.mp4"]
    assert content["recent"][0]["name"] == "future.mp4"
    assert len(content["recent"]) == 6
    assert {row["name"] for row in content["favorites"]} == {"favorite.mp4", "last-year.mp4"}
    assert content["summary"]["total"] == 6
    assert content["summary"]["favorites"] == 2
    assert content["summary"]["pending"] == 1
    assert content["summary"]["errors"] == 1
    assert content["summary"]["size_bytes"] == 600


def test_random_refresh_avoids_previous_group_and_handles_small_libraries(tmp_path):
    db = make_library(tmp_path)
    previous = [row["id"] for row in random_videos(db)]
    refreshed = [row["id"] for row in random_videos(db, previous)]
    assert len(refreshed) == 3
    assert not set(previous).intersection(refreshed)
    with db.connect() as conn:
        conn.execute("DELETE FROM videos WHERE id > 2")
    assert len(random_videos(db, [1, 2])) == 2
    assert len({row["id"] for row in random_videos(db, [1, 2])}) == 2
    with db.connect() as conn:
        conn.execute("DELETE FROM videos")
    assert random_videos(db) == []
    assert home_content(db)["summary"]["total"] == 0


def test_leap_day_memories_match_only_previous_february_29(tmp_path):
    db = make_library(tmp_path)
    with db.connect() as conn:
        conn.execute("DELETE FROM videos")
        for year, month, day in [(2024, 2, 29), (2025, 2, 28), (2028, 2, 29)]:
            conn.execute(
                "INSERT INTO videos(path, name, mtime) VALUES (?, ?, ?)",
                (str(year), str(year), datetime(year, month, day).timestamp()),
            )
    assert [row["name"] for row in home_content(db, datetime(2028, 2, 29))["memories"]] == ["2024"]


def test_home_routes_and_legacy_library_links(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    make_library(tmp_path)
    main = import_module("app.main")
    app = main.create_app()
    with TestClient(app) as client:
        home = client.get("/")
        assert home.status_code == 200
        for copy in ["随机视频", "最近记录", "收藏", "那年今日", "影像库概览"]:
            assert copy in home.text
        assert "removed.mp4" not in home.text
        assert 'data-timeline-root' in client.get("/library").text
        assert 'data-timeline-root' in client.get("/?view=timeline").text
        assert 'favorites-view' in client.get("/?view=favorites").text
        random_response = client.get("/home/random")
        assert random_response.status_code == 200
        ids = set(re.findall(r'data-home-video="(\d+)"', random_response.json()["html"]))
        assert len(ids) == 3
        next_response = client.get("/home/random", params={"exclude": ",".join(ids)})
        next_ids = set(re.findall(r'data-home-video="(\d+)"', next_response.json()["html"]))
        assert not ids.intersection(next_ids)
        assert client.get("/home/random?exclude=bad").status_code == 400
        assert client.get("/home/random?exclude=1,2,3,4").status_code == 400


def test_regrouped_settings_saves_all_fields_and_unchecked_options(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    app = import_module("app.main").create_app()
    with TestClient(app) as client:
        response = client.post("/settings", data={
            "site_title": "时光影像", "video_root": "/media/family", "scan_interval_hours": "0",
            "default_volume_percent": "35", "default_flat_quality": "ultra",
            "default_panorama_quality": "low", "thumbnail_resolution": "720",
            "flat_hls_encoder": "h264_qsv", "panorama_hls_encoder": "libx264_veryfast",
            "hls_cache_max_mb": "2048", "stream_cache_retention_days": "12",
            "video_extensions": ".mp4, .mov", "ignore_name_patterns": "temp*\n._*",
            "show_date": "on", "intranet_keepalive_enabled": "on",
            "intranet_redirect_host": "192.168.1.10", "intranet_redirect_port": "8080",
            "intranet_redirect_protocol": "https",
        })
    assert response.status_code == 200
    assert "设置已保存。" in response.text
    saved = load_settings(tmp_path / "config")
    assert saved.site_title == "时光影像"
    assert saved.video_root == "/media/family"
    assert saved.default_volume_percent == 35
    assert saved.default_flat_quality == "ultra"
    assert saved.default_panorama_quality == "low"
    assert saved.thumbnail_resolution == 720
    assert saved.hls_cache_max_mb == 2048
    assert saved.stream_cache_retention_days == 12
    assert saved.show_date and not saved.show_size and not saved.show_duration
    assert not saved.ignore_dotfiles
    assert saved.intranet_redirect_protocol == "https"
    assert saved.intranet_redirect_host == "192.168.1.10"
    assert saved.ignore_name_patterns == ["temp*", "._*"]


def test_home_exposes_intranet_setup_or_existing_detection(monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    monkeypatch.setenv("APP_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    app = import_module("app.main").create_app()
    with TestClient(app) as client:
        for enabled, host in [(False, ""), (True, ""), (False, "192.168.1.10")]:
            save_settings(config_dir, Settings(scan_interval_hours=0,
                intranet_keepalive_enabled=enabled, intranet_redirect_host=host))
            home = client.get("/")
            assert 'href="/settings"' in home.text
            assert 'class="site-intranet-settings"' not in home.text
            button = re.search(r'<button[^>]*data-intranet-jump[^>]*>', home.text).group()
            assert "hidden" in button
            assert 'class="site-intranet-settings"' not in client.get("/settings").text

        save_settings(config_dir, Settings(scan_interval_hours=0,
            intranet_keepalive_enabled=True, intranet_redirect_host="192.168.1.10",
            intranet_redirect_port="8080"))
        home = client.get("/")
        button = re.search(r'<button[^>]*data-intranet-jump[^>]*>', home.text).group()
        assert "hidden" not in button
        assert "is-visible" in button
        assert 'data-intranet-enabled="1"' in home.text
        assert 'data-intranet-redirect-host="192.168.1.10"' in home.text
        assert 'data-intranet-redirect-port="8080"' in home.text
        assert 'class="site-intranet-settings"' not in home.text
        assert '/static/intranet.js?' in home.text


def test_home_and_library_share_navigation_without_duplicate_library_actions(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    make_library(tmp_path)
    app = import_module("app.main").create_app()
    with TestClient(app) as client:
        home = client.get("/")
        assert '/static/site-shell.css?' in home.text
        assert 'href="/" aria-current="page"' in home.text
        for path, view in [("/library", "timeline"), ("/?view=folders", "folders"), ("/?view=calendar", "calendar"), ("/?view=favorites", "favorites"), ("/library?view=calendar&type=flat&duration=long", "calendar")]:
            page = client.get(path)
            assert page.status_code == 200
            assert '/static/site-shell.css?' in page.text
            assert page.text.count('class="site-header"') == 1
            assert page.text.count('data-intranet-jump') == 1
            header = re.search(r'<header class="site-header">(.*?)</header>', page.text, re.S).group(1)
            assert 'href="/"' in header and '>首页</a>' in header
            navigation = re.search(r'<nav class="site-navigation"[^>]*>(.*?)</nav>', header, re.S).group(1)
            links = re.findall(r'<a href="([^"]*)"([^>]*)>([^<]*)</a>', navigation)
            assert [label for _, _, label in links] == ["首页", "时间线", "文件夹", "日历", "收藏"]
            active_links = [(url, label) for url, attrs, label in links if 'aria-current="page"' in attrs]
            assert len(active_links) == 1
            assert parse_qs(urlsplit(unescape(active_links[0][0])).query)["view"] == [view]
            if "type=flat" in path:
                for url, _, _ in links[1:]:
                    params = parse_qs(urlsplit(unescape(url)).query)
                    assert params["type"] == ["flat"]
                    assert params["duration"] == ["long"]
            assert 'href="/settings"' in header
            assert 'class="site-intranet-settings"' not in header
            toolbar = re.search(r'<header class="library-topbar">(.*?)</header>', page.text, re.S).group(1)
            assert '<nav' not in toolbar
            assert 'view-switch' not in page.text
            assert '>首页</a>' not in toolbar
            assert 'href="/settings"' not in toolbar
            assert 'data-intranet-jump' not in toolbar
            assert 'data-preview-size' in toolbar
            assert 'class="filter-menu"' in toolbar
            assert 'data-scan-form' in toolbar
        timeline = client.get("/library").text
        assert 'data-gallery-previous' in timeline
        assert 'data-player-frame' in timeline
        assert 'data-resizer' in timeline
