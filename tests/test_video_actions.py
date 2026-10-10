import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from importlib import import_module
from pathlib import Path
from threading import Event
from urllib.parse import unquote

import pytest
from fastapi.testclient import TestClient

from app.config import Settings, save_settings
from app.db import Database, SCHEMA
from app.home import home_content, memory_date_values, random_videos
from app.media import STREAM_QUALITIES, resolve_stream_path


@pytest.fixture
def video_app(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    save_settings(tmp_path / "config", Settings(scan_interval_hours=0))
    main = import_module("app.main")
    app = main.create_app()
    with app.state.db.connect() as conn:
        for index in range(1, 7):
            source = tmp_path / f"影像-{index}.mov"
            source.write_bytes(f"original-video-{index}".encode())
            conn.execute(
                """INSERT INTO videos(path, name, mtime, is_10bit, width, height, favorite)
                   VALUES (?, ?, ?, 0, 1920, 1080, 1)""",
                (str(source), source.name, datetime(2025, 10, 10, index).timestamp()),
            )
    monkeypatch.setattr(main, "memory_date_values", lambda: memory_date_values(datetime(2026, 10, 10)))
    return app


def test_participation_migrates_existing_database_and_preserves_values(tmp_path):
    db = Database(tmp_path)
    with sqlite3.connect(db.path) as conn:
        conn.executescript(SCHEMA.replace("    exclude_random INTEGER NOT NULL DEFAULT 0,\n", "")
                           .replace("    exclude_memories INTEGER NOT NULL DEFAULT 0,\n", ""))
        conn.execute("INSERT INTO videos(path, name, favorite) VALUES ('/sample.mp4', 'sample.mp4', 1)")
    db.init()
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM videos").fetchone()
        assert row["exclude_random"] == row["exclude_memories"] == 0
        assert row["favorite"] == 1
        conn.execute("UPDATE videos SET exclude_random = 1, exclude_memories = 1")
    db.init()
    with db.connect() as conn:
        assert tuple(conn.execute("SELECT exclude_random, exclude_memories FROM videos").fetchone()) == (1, 1)


def test_random_only_exclusion_preserves_memories_and_existing_memory_exclusion(video_app):
    with TestClient(video_app) as client:
        assert client.post('/video/1/participation', data={"exclude_random": 1}).json() == {
            "ok": True, "exclude_random": True, "exclude_memories": False,
        }
        assert client.post('/video/2/participation', data={"exclude_random": 1, "exclude_memories": 1}).status_code == 200
        assert client.post('/video/2/participation', data={"exclude_random": 0}).json()["exclude_memories"] is True
        html = client.get('/home/random?selected=1,2,3').json()["html"]
        assert 'data-home-card="1"' not in html
        assert 'data-home-card="2"' in html
        memories = client.get('/library?view=memories').text
        assert "影像-1.mov" in memories
        assert "影像-2.mov" not in memories
    content = home_content(video_app.state.db, datetime(2026, 10, 10))
    assert all(row["id"] != 2 for row in content["memories"])
    assert content["summary"]["total"] == 6
    assert len(content["recent"]) == 6
    assert content["summary"]["favorites"] == 6


def test_exclusion_applies_to_fallback_cached_selection_and_empty_library(video_app):
    with TestClient(video_app) as client:
        for index in range(1, 7):
            client.post(f'/video/{index}/participation', data={"exclude_random": 1, "exclude_memories": 1})
        assert random_videos(video_app.state.db, [1, 2, 3]) == []
        assert random_videos(video_app.state.db, selected=[1, 2, 3]) == []
        assert "暂无参与随机的视频" in client.get('/home/random').json()["html"]
        home = client.get('/').text
        assert "home-empty--welcome" not in home
        assert 'data-home-shuffle disabled' in home
        assert 'data-video-id="1"' in client.get('/library').text
        restored = client.post('/video/1/participation', data={"exclude_random": 0, "exclude_memories": 0})
        assert restored.json() == {"ok": True, "exclude_random": False, "exclude_memories": False}
        assert [row["id"] for row in random_videos(video_app.state.db, [1])] == [1]
        assert 'data-home-card="1"' in client.get('/home/random?selected=1,2,3').json()["html"]


def test_cached_selection_preserves_order_and_replaces_excluded_or_missing(video_app):
    with video_app.state.db.connect() as conn:
        conn.execute("UPDATE videos SET exclude_random = 1 WHERE id = 2")
        conn.execute("UPDATE videos SET missing = 1 WHERE id = 3")
    rows = random_videos(video_app.state.db, selected=[5, 2, 3])
    assert rows[0]["id"] == 5
    assert len(rows) == 3
    assert all(row["id"] not in {2, 3} for row in rows)


@pytest.mark.parametrize("data,status", [
    ({"exclude_random": 2}, 400), ({"exclude_random": 1, "exclude_memories": -1}, 400),
    ({"exclude_random": "bad"}, 422), ({}, 422),
])
def test_participation_rejects_invalid_values(video_app, data, status):
    with TestClient(video_app) as client:
        assert client.post('/video/1/participation', data=data).status_code == status
        assert client.post('/video/999/participation', data={"exclude_random": 1}).status_code == 404


@pytest.mark.parametrize("query", ["selected=1,2,3,4", "selected=-1", "selected=x", "exclude=0"])
def test_random_selection_rejects_invalid_ids(video_app, query):
    with TestClient(video_app) as client:
        assert client.get(f'/home/random?{query}').status_code == 400


def test_original_download_filename_range_and_missing_file(video_app):
    with TestClient(video_app) as client:
        response = client.get('/video/1/download')
        assert response.content == b"original-video-1"
        assert response.headers['content-disposition'].startswith('attachment;')
        assert "影像-1.mov" in unquote(response.headers['content-disposition'])
        partial = client.get('/video/1/download', headers={"Range": "bytes=2-7"})
        assert partial.status_code == 206
        assert partial.content == response.content[2:8]
        assert partial.headers['content-range'] == 'bytes 2-7/16'
        assert client.get('/video/1/download?quality=invalid').status_code == 400
        assert client.get('/video/999/download').status_code == 404
        with video_app.state.db.connect() as conn:
            Path(conn.execute('SELECT path FROM videos WHERE id = 1').fetchone()[0]).unlink()
        assert client.get('/video/1/download').status_code == 404
        assert client.get('/video/1/download?quality=low').status_code == 404


@pytest.mark.parametrize("quality,label", [("ultra", "超清"), ("low", "高清"), ("high", "流畅")])
def test_transcoded_download_is_complete_mp4_and_reuses_cache(video_app, monkeypatch, quality, label):
    generated = []

    def generate(source, output, profile):
        generated.append(profile)
        output.write_bytes(b"complete-mp4-from-start-to-end")

    monkeypatch.setattr('app.media.generate_stream_cache', generate)
    with TestClient(video_app) as client:
        for _ in range(2):
            response = client.get(f'/video/1/download?quality={quality}')
            assert response.status_code == 200
            assert response.content == b"complete-mp4-from-start-to-end"
            assert response.headers['content-type'] == 'video/mp4'
            assert f"影像-1-{label}.mp4" in unquote(response.headers['content-disposition'])
    assert generated == [STREAM_QUALITIES[quality]]


def test_concurrent_downloads_generate_one_complete_cache(video_app, monkeypatch):
    started, release = Event(), Event()
    calls = []

    def generate(source, output, quality):
        calls.append(quality)
        output.write_bytes(b"partial")
        started.set()
        assert release.wait(2)
        output.write_bytes(b"complete")

    monkeypatch.setattr('app.media.generate_stream_cache', generate)
    with video_app.state.db.connect() as conn:
        video = dict(conn.execute('SELECT * FROM videos WHERE id = 1').fetchone())
    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(resolve_stream_path, video, video_app.state.data_dir, 'low')
        assert started.wait(2)
        second = executor.submit(resolve_stream_path, video, video_app.state.data_dir, 'low')
        release.set()
        assert first.result().read_bytes() == second.result().read_bytes() == b"complete"
    assert len(calls) == 1


@pytest.mark.parametrize("error", [OSError("transcode failed"), subprocess.TimeoutExpired("ffmpeg", 3600)])
def test_failed_transcode_does_not_leave_partial_download_and_can_retry(video_app, monkeypatch, error):
    def fail(source, output, quality):
        output.write_bytes(b"partial")
        raise error

    monkeypatch.setattr('app.media.generate_stream_cache', fail)
    with TestClient(video_app) as client:
        assert client.get('/video/1/download?quality=low').status_code == 500
        assert list((video_app.state.data_dir / 'cache' / 'streams').glob('*.mp4')) == []
        monkeypatch.setattr('app.media.generate_stream_cache', lambda source, output, quality: output.write_bytes(b"complete"))
        assert client.get('/video/1/download?quality=low').content == b"complete"


def test_player_download_menu_is_available_in_full_and_embedded_players(video_app):
    with TestClient(video_app) as client:
        for suffix in ('', '?embed=1'):
            response = client.get(f'/video/1/play{suffix}')
            assert response.status_code == 200
            assert 'data-download-menu' in response.text
            assert 'class="player-viewport"' in response.text
            for quality in ('original', 'ultra', 'low', 'high'):
                assert f'data-download-quality="{quality}"' in response.text
