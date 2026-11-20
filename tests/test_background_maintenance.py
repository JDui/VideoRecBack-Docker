import asyncio
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings, save_settings


def test_scan_remains_running_while_media_jobs_are_pending(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    from app import main

    scanner = SimpleNamespace(
        is_running=False,
        is_processing_media=False,
        pending_media_jobs=lambda: 4,
    )
    app = SimpleNamespace(state=SimpleNamespace(manual_scan_pending=False, scanner=scanner))

    assert main.is_scan_running(app) is True


def test_background_maintenance_disables_full_scan(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    from app import main

    sleeps = []

    class Scanner:
        def __init__(self):
            self.processed = 0
            self.scanned = 0

        async def process_queue(self, settings):
            self.processed += 1

        async def scan(self, settings):
            self.scanned += 1

    async def sleep(delay):
        sleeps.append(delay)
        raise asyncio.CancelledError

    scanner = Scanner()
    app = SimpleNamespace(state=SimpleNamespace(config_dir=tmp_path / "config", scanner=scanner))
    monkeypatch.setattr(main, "load_settings", lambda config_dir: Settings(scan_interval_hours=0))
    monkeypatch.setattr(main.asyncio, "sleep", sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main.background_maintenance(app))

    assert scanner.processed == 1
    assert scanner.scanned == 0
    assert sleeps == [300]


def test_background_maintenance_runs_after_positive_interval(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    from app import main

    sleeps = []

    class Scanner:
        def __init__(self):
            self.processed = 0
            self.scanned = 0

        async def process_queue(self, settings):
            self.processed += 1

        async def scan(self, settings):
            self.scanned += 1

    async def sleep(delay):
        sleeps.append(delay)
        if len(sleeps) > 1:
            raise asyncio.CancelledError

    scanner = Scanner()
    app = SimpleNamespace(state=SimpleNamespace(config_dir=tmp_path / "config", scanner=scanner))
    monkeypatch.setattr(main, "load_settings", lambda config_dir: Settings(scan_interval_hours=2))
    monkeypatch.setattr(main.asyncio, "sleep", sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main.background_maintenance(app))

    assert scanner.processed == 2
    assert scanner.scanned == 1
    assert sleeps == [7200, 7200]


def test_startup_replaces_old_thumbnail_caches_without_a_full_scan(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    from app import main
    from app.scanner import THUMBNAIL_VERSION
    from app.thumbnails import VideoToolError

    app = main.create_app()
    scanner = app.state.scanner
    save_settings(app.state.config_dir, Settings(scan_interval_hours=0))
    cache = scanner.cache_dir
    streams = cache / "streams"
    streams.mkdir(parents=True)
    playback = streams / "stream-1-original.mp4"
    playback.write_bytes(b"playback cache")
    legacy = {1: cache / "older-name.webp", 2: cache / "2.webp", 3: cache / "3.webp", 4: cache / "4.webp"}
    with app.state.db.connect() as conn:
        for video_id, cover in legacy.items():
            source = tmp_path / f"demo-{video_id}.mp4"
            source.write_bytes(b"source")
            for cached in (cover, cache / f"{video_id}-preview.webp", cache / f"{video_id}-scrolling.webp"):
                Image.new("RGB", (32, 18), "red").save(cached)
            version = THUMBNAIL_VERSION if video_id == 3 else THUMBNAIL_VERSION - 1
            conn.execute(
                """INSERT INTO videos(id,path,name,missing,thumb_status,thumb_path,thumb_version,thumb_revision,
                                      duration_seconds,media_version,hdr_checked)
                   VALUES (?,?,?,?,'ready',?,?,1,8,1,1)""",
                (video_id, str(source), source.name, int(video_id == 4), str(cover), version),
            )
        conn.execute("INSERT INTO media_jobs(video_id,status,attempts) VALUES (1,'pending',3)")

    def generate(source, still, video_type, duration, resolution, scrolling):
        if source.name == "demo-2.mp4":
            raise VideoToolError("fixture decode failed")
        assert source.name == "demo-1.mp4"
        for target in (still, scrolling):
            Image.new("RGB", (32, 18), "blue").save(target)

    monkeypatch.setattr("app.scanner.generate_thumbnail", generate)
    with TestClient(app) as client:
        deadline = time.monotonic() + 8
        while scanner.pending_media_jobs() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert scanner.pending_media_jobs() == 0
        assert client.get("/thumb/2.webp").status_code == 200

    assert not legacy[1].exists()
    for name in ("1.webp", "1-preview.webp", "1-scrolling.webp"):
        with Image.open(cache / name) as replacement:
            assert replacement.getpixel((10, 10))[2] > 200
    for name in ("2.webp", "2-preview.webp", "2-scrolling.webp"):
        with Image.open(cache / name) as previous:
            assert previous.getpixel((10, 10))[0] > 200
    assert playback.read_bytes() == b"playback cache"
    assert scanner.queue_thumbnail_upgrades() == 0
    with app.state.db.connect() as conn:
        states = conn.execute("SELECT thumb_status, thumb_version FROM videos ORDER BY id").fetchall()
        jobs = conn.execute("SELECT video_id,status,attempts FROM media_jobs").fetchall()
    assert [tuple(row) for row in states] == [
        ("ready", THUMBNAIL_VERSION), ("ready", THUMBNAIL_VERSION - 1),
        ("ready", THUMBNAIL_VERSION), ("ready", THUMBNAIL_VERSION - 1),
    ]
    assert [tuple(row) for row in jobs] == [(2, "error", 3)]
