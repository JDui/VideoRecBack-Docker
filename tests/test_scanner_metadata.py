from pathlib import Path

from app.config import Settings
from app.db import Database
import pytest

from app.scanner import THUMBNAIL_VERSION, Scanner, calculate_aspect_ratio, safe_relative_path
from app.thumbnails import VideoToolError
from app.thumbnails import ProbeResult


def test_calculate_aspect_ratio():
    assert calculate_aspect_ratio(1920, 1080) == 1.7778
    assert calculate_aspect_ratio(None, 1080) is None


def test_safe_relative_path():
    assert safe_relative_path(Path("/media/a/b.mp4"), Path("/media")) == "a/b.mp4"


def test_scan_path_indexes_only_target_file(monkeypatch, tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    target = root / "one.mp4"
    other = root / "two.mp4"
    target.write_bytes(b"one")
    other.write_bytes(b"two")
    db = Database(tmp_path / "data")
    db.init()
    scanner = Scanner(db, tmp_path / "data")

    monkeypatch.setattr("app.scanner.probe_video", lambda path: ProbeResult(10, 1920, 1080))
    monkeypatch.setattr("app.scanner.generate_thumbnail", lambda *args: None)

    summary = scanner._scan_path_sync(Settings(video_root=str(root)), target)

    with db.connect() as conn:
        rows = conn.execute("SELECT name FROM videos ORDER BY name").fetchall()

    assert summary.seen == 1
    assert [row["name"] for row in rows] == ["one.mp4"]


def test_scan_defaults_two_to_one_video_to_panorama(monkeypatch, tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    target = root / "wide.mp4"
    target.write_bytes(b"wide")
    db = Database(tmp_path / "data")
    db.init()
    scanner = Scanner(db, tmp_path / "data")

    monkeypatch.setattr("app.scanner.probe_video", lambda path: ProbeResult(10, 3840, 1920))
    monkeypatch.setattr("app.scanner.generate_thumbnail", lambda *args: None)

    scanner._scan_file_sync(Settings(video_root=str(root)), target)

    with db.connect() as conn:
        row = conn.execute("SELECT type FROM videos WHERE name = 'wide.mp4'").fetchone()

    assert row["type"] == "panorama"


def test_scan_records_bit_depth_and_codec(monkeypatch, tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    target = root / "tenbit.mp4"
    target.write_bytes(b"tenbit")
    db = Database(tmp_path / "data")
    db.init()
    scanner = Scanner(db, tmp_path / "data")

    monkeypatch.setattr("app.scanner.probe_video", lambda path: ProbeResult(10, 1920, 1080, 10, "hevc", "420", 8_500_000))
    monkeypatch.setattr("app.scanner.generate_thumbnail", lambda *args: None)

    scanner._scan_file_sync(Settings(video_root=str(root)), target)

    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT bit_depth, is_10bit, video_codec, chroma_subsampling, average_bitrate
            FROM videos
            WHERE name = 'tenbit.mp4'
            """
        ).fetchone()

    assert row["bit_depth"] == 10
    assert row["is_10bit"] == 1
    assert row["video_codec"] == "hevc"
    assert row["chroma_subsampling"] == "420"
    assert row["average_bitrate"] == 8_500_000


def test_scan_does_not_backfill_only_missing_tenbit_status(monkeypatch, tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    target = root / "old.mp4"
    target.write_bytes(b"old")
    thumb = tmp_path / "data" / "cache" / "thumb.webp"
    thumb.parent.mkdir(parents=True)
    thumb.write_bytes(b"thumb")
    stat = target.stat()
    db = Database(tmp_path / "data")
    db.init()
    scanner = Scanner(db, tmp_path / "data")

    def fail_probe(path):
        raise AssertionError("probe should not run")

    monkeypatch.setattr("app.scanner.probe_video", fail_probe)
    monkeypatch.setattr("app.scanner.generate_thumbnail", lambda *args: None)
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO videos(
                path, name, mtime, missing, type, size_bytes, duration_seconds,
                width, height, bit_depth, is_10bit, chroma_subsampling, average_bitrate,
                thumb_status, thumb_path, thumb_version
            )
            VALUES (?, 'old.mp4', ?, 0, 'flat', ?, 12, 1920, 1080, 8, NULL, '420', 1000, 'ready', ?, ?)
            """,
            (str(target), stat.st_mtime, stat.st_size, str(thumb), THUMBNAIL_VERSION),
        )

    scanner._scan_file_sync(Settings(video_root=str(root)), target)

    with db.connect() as conn:
        row = conn.execute("SELECT is_10bit FROM videos WHERE name = 'old.mp4'").fetchone()

    assert row["is_10bit"] is None


def test_recheck_all_video_metadata_updates_probe_fields(monkeypatch, tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    target = root / "old.mp4"
    target.write_bytes(b"old")
    db = Database(tmp_path / "data")
    db.init()
    scanner = Scanner(db, tmp_path / "data")

    monkeypatch.setattr("app.scanner.probe_video", lambda path: ProbeResult(12, 1280, 720, 10, "hevc", "422", 4_200_000))
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO videos(
                id, path, name, mtime, missing, type, size_bytes, duration_seconds,
                width, height, bit_depth, is_10bit, chroma_subsampling, average_bitrate, video_codec
            )
            VALUES (1, ?, 'old.mp4', 1, 0, 'flat', 1, 3, 640, 360, 8, 0, '420', 1000, 'h264')
            """,
            (str(target),),
        )

    summary = scanner._recheck_all_video_metadata_sync()

    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT duration_seconds, width, height, bit_depth, is_10bit,
                   chroma_subsampling, average_bitrate, video_codec
            FROM videos
            WHERE id = 1
            """
        ).fetchone()

    assert summary.seen == 1
    assert summary.indexed == 1
    assert summary.errors == 0
    assert row["duration_seconds"] == 12
    assert row["width"] == 1280
    assert row["height"] == 720
    assert row["bit_depth"] == 10
    assert row["is_10bit"] == 1
    assert row["chroma_subsampling"] == "422"
    assert row["average_bitrate"] == 4_200_000
    assert row["video_codec"] == "hevc"


def test_recheck_panorama_types_promotes_wide_videos_and_rebuilds_thumbnail(monkeypatch, tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    target = root / "wide.mp4"
    target.write_bytes(b"wide")
    db = Database(tmp_path / "data")
    db.init()
    scanner = Scanner(db, tmp_path / "data")
    generated = []
    monkeypatch.setattr("app.scanner.generate_thumbnail", lambda *args: generated.append(args))
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO videos(
                id, path, name, mtime, missing, type, duration_seconds, width, height, thumb_status, thumb_version
            )
            VALUES (1, ?, 'wide.mp4', 1, 0, 'flat', 10, 3840, 1920, 'ready', 7)
            """,
            (str(target),),
        )

    changed = scanner.recheck_panorama_types()

    with db.connect() as conn:
        row = conn.execute("SELECT type FROM videos WHERE id = 1").fetchone()

    assert changed == 1
    assert row["type"] == "panorama"
    assert generated


def test_scan_ignores_dotfiles_and_dot_directories(monkeypatch, tmp_path):
    root = tmp_path / "media"
    hidden_dir = root / ".hidden"
    hidden_dir.mkdir(parents=True)
    visible = root / "visible.mp4"
    hidden_file = root / ".hidden.mp4"
    nested_hidden = hidden_dir / "nested.mp4"
    visible.write_bytes(b"visible")
    hidden_file.write_bytes(b"hidden")
    nested_hidden.write_bytes(b"nested")
    db = Database(tmp_path / "data")
    db.init()
    scanner = Scanner(db, tmp_path / "data")

    monkeypatch.setattr("app.scanner.probe_video", lambda path: ProbeResult(10, 1920, 1080))
    monkeypatch.setattr("app.scanner.generate_thumbnail", lambda *args: None)

    summary = scanner._scan_sync(Settings(video_root=str(root)))

    with db.connect() as conn:
        rows = conn.execute("SELECT name FROM videos ORDER BY name").fetchall()

    assert summary.seen == 1
    assert summary.skipped == 2
    assert [row["name"] for row in rows] == ["visible.mp4"]


def test_scan_ignores_configured_name_patterns(monkeypatch, tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    keep = root / "keep.mp4"
    ignored = root / "draft.tmp.mp4"
    keep.write_bytes(b"keep")
    ignored.write_bytes(b"ignored")
    db = Database(tmp_path / "data")
    db.init()
    scanner = Scanner(db, tmp_path / "data")

    monkeypatch.setattr("app.scanner.probe_video", lambda path: ProbeResult(10, 1920, 1080))
    monkeypatch.setattr("app.scanner.generate_thumbnail", lambda *args: None)

    summary = scanner._scan_sync(
        Settings(video_root=str(root), ignore_dotfiles=False, ignore_name_patterns=["*.tmp.mp4"])
    )

    with db.connect() as conn:
        rows = conn.execute("SELECT name FROM videos ORDER BY name").fetchall()

    assert summary.seen == 1
    assert summary.skipped == 1
    assert [row["name"] for row in rows] == ["keep.mp4"]


def test_scan_file_skips_ignored_target(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    hidden = root / ".hidden.mp4"
    hidden.write_bytes(b"hidden")
    db = Database(tmp_path / "data")
    db.init()
    scanner = Scanner(db, tmp_path / "data")

    summary = scanner._scan_file_sync(Settings(video_root=str(root)), hidden)

    with db.connect() as conn:
        count = conn.execute("SELECT count(*) AS total FROM videos").fetchone()["total"]

    assert summary.seen == 0
    assert summary.skipped == 1
    assert count == 0


def test_scan_path_delete_removes_matching_rows_and_thumbnails(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    db = Database(tmp_path / "data")
    db.init()
    scanner = Scanner(db, tmp_path / "data")
    deleted_path = root / "gone.mp4"
    kept_path = root / "kept.mp4"
    thumb_path = tmp_path / "data" / "cache" / "gone.webp"
    stream_path = tmp_path / "data" / "cache" / "streams" / "stream-1-low-old.mp4"
    thumb_path.parent.mkdir(parents=True)
    stream_path.parent.mkdir(parents=True)
    thumb_path.write_bytes(b"thumb")
    stream_path.write_bytes(b"stream")
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO videos(path, name, mtime, missing, thumb_path) VALUES (?, 'gone.mp4', 1, 0, ?)",
            (str(deleted_path), str(thumb_path)),
        )
        conn.execute(
            "INSERT INTO videos(path, name, mtime, missing) VALUES (?, 'kept.mp4', 1, 0)",
            (str(kept_path),),
        )

    summary = scanner._scan_path_sync(Settings(video_root=str(root)), deleted_path, "delete")

    with db.connect() as conn:
        rows = [row["name"] for row in conn.execute("SELECT name FROM videos").fetchall()]

    assert summary.deleted == 1
    assert rows == ["kept.mp4"]
    assert not thumb_path.exists()
    assert not stream_path.exists()


def test_scan_file_rejects_path_outside_root(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"outside")
    db = Database(tmp_path / "data")
    db.init()
    scanner = Scanner(db, tmp_path / "data")

    with pytest.raises(VideoToolError):
        scanner._scan_file_sync(Settings(video_root=str(root)), outside)


def test_full_scan_defers_changed_media_work(monkeypatch, tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    target = root / "new.mp4"
    target.write_bytes(b"video")
    db = Database(tmp_path / "data")
    db.init()
    scanner = Scanner(db, tmp_path / "data")

    monkeypatch.setattr("app.scanner.probe_video", lambda path: (_ for _ in ()).throw(AssertionError("probe must be deferred")))
    monkeypatch.setattr("app.scanner.generate_thumbnail", lambda *args: (_ for _ in ()).throw(AssertionError("thumbnail must be deferred")))

    summary = scanner._scan_sync(Settings(video_root=str(root)))

    with db.connect() as conn:
        video = conn.execute("SELECT id, thumb_status FROM videos WHERE path = ?", (str(target),)).fetchone()
        job = conn.execute("SELECT video_id, status FROM media_jobs").fetchone()

    assert summary.indexed == 1
    assert video["thumb_status"] == "pending"
    assert job["video_id"] == video["id"]
    assert job["status"] == "pending"


def test_full_scan_skips_unchanged_database_writes(monkeypatch, tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    target = root / "existing.mp4"
    target.write_bytes(b"video")
    thumb = tmp_path / "data" / "cache" / "1.webp"
    thumb.parent.mkdir(parents=True)
    thumb.write_bytes(b"thumb")
    stat = target.stat()
    db = Database(tmp_path / "data")
    db.init()
    scanner = Scanner(db, tmp_path / "data")
    with db.connect() as conn:
        conn.execute(
            """
            INSERT INTO videos(
                id, path, name, relative_path, folder, size_bytes, duration_seconds,
                width, height, bit_depth, chroma_subsampling, average_bitrate,
                mtime, mtime_ns, thumb_status, thumb_path, thumb_version, updated_at
            ) VALUES(1, ?, 'existing.mp4', 'existing.mp4', '/', ?, 10, 1920, 1080, 8, '420', 1000, ?, ?, 'ready', ?, ?, '2000-01-01')
            """,
            (str(target), stat.st_size, stat.st_mtime, stat.st_mtime_ns, str(thumb), THUMBNAIL_VERSION),
        )

    monkeypatch.setattr("app.scanner.probe_video", lambda path: (_ for _ in ()).throw(AssertionError("probe must not run")))
    summary = scanner._scan_sync(Settings(video_root=str(root)))

    with db.connect() as conn:
        row = conn.execute("SELECT updated_at FROM videos WHERE id = 1").fetchone()
        jobs = conn.execute("SELECT COUNT(*) AS count FROM media_jobs").fetchone()["count"]

    assert summary.indexed == 0
    assert row["updated_at"] == "2000-01-01"
    assert jobs == 0


def test_scan_queue_coalesces_same_path(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    db = Database(tmp_path / "data")
    db.init()
    scanner = Scanner(db, tmp_path / "data")

    import asyncio

    asyncio.run(scanner.enqueue(root / "one.mp4", "upsert"))
    asyncio.run(scanner.enqueue(root / "one.mp4", "delete"))

    with db.connect() as conn:
        rows = conn.execute("SELECT path, action, status FROM scan_queue").fetchall()

    assert len(rows) == 1
    assert rows[0]["action"] == "delete"
    assert rows[0]["status"] == "pending"


def test_media_worker_completes_deferred_metadata_and_thumbnail(monkeypatch, tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    target = root / "new.mp4"
    target.write_bytes(b"video")
    db = Database(tmp_path / "data")
    db.init()
    scanner = Scanner(db, tmp_path / "data")
    scanner.cache_dir.mkdir(parents=True, exist_ok=True)
    scanner._scan_sync(Settings(video_root=str(root)))

    monkeypatch.setattr(
        "app.scanner.probe_video",
        lambda path: ProbeResult(12, 1920, 1080, 10, "hevc", "420", 8_000_000),
    )
    monkeypatch.setattr("app.scanner.generate_thumbnail", lambda path, output, *args: output.write_bytes(b"thumb"))
    monkeypatch.setattr("app.scanner.generate_preview_thumbnail", lambda source, output: output)

    summary = scanner._process_media_jobs_sync(Settings(video_root=str(root)), limit=1)

    with db.connect() as conn:
        video = conn.execute(
            "SELECT duration_seconds, bit_depth, is_10bit, thumb_status FROM videos WHERE path = ?",
            (str(target),),
        ).fetchone()
        jobs = conn.execute("SELECT COUNT(*) AS count FROM media_jobs").fetchone()["count"]

    assert summary.indexed == 1
    assert summary.thumbnails == 1
    assert video["duration_seconds"] == 12
    assert video["bit_depth"] == 10
    assert video["is_10bit"] == 1
    assert video["thumb_status"] == "ready"
    assert jobs == 0


def test_scan_folder_deletes_only_missing_folder_records(monkeypatch, tmp_path):
    root = tmp_path / "media"
    target = root / "target"
    sibling = root / "sibling"
    target.mkdir(parents=True)
    sibling.mkdir()
    kept = target / "kept.mp4"
    removed = target / "removed.mp4"
    outside = sibling / "outside.mp4"
    kept.write_bytes(b"kept")
    outside.write_bytes(b"outside")
    removed_thumb = tmp_path / "data" / "cache" / "removed.webp"
    outside_thumb = tmp_path / "data" / "cache" / "outside.webp"
    removed_thumb.parent.mkdir(parents=True)
    removed_thumb.write_bytes(b"removed")
    outside_thumb.write_bytes(b"outside")
    db = Database(tmp_path / "data")
    db.init()
    scanner = Scanner(db, tmp_path / "data")

    monkeypatch.setattr("app.scanner.probe_video", lambda path: ProbeResult(10, 1920, 1080))
    monkeypatch.setattr("app.scanner.generate_thumbnail", lambda *args: None)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO videos(path, name, mtime, missing, thumb_path) VALUES (?, 'removed.mp4', 1, 0, ?)",
            (str(removed), str(removed_thumb)),
        )
        conn.execute(
            "INSERT INTO videos(path, name, mtime, missing, thumb_path) VALUES (?, 'outside.mp4', 1, 0, ?)",
            (str(outside), str(outside_thumb)),
        )

    summary = scanner._scan_folder_sync(Settings(video_root=str(root)), target)

    with db.connect() as conn:
        rows = [row["name"] for row in conn.execute("SELECT name FROM videos ORDER BY name").fetchall()]

    assert summary.seen == 1
    assert summary.deleted == 1
    assert rows == ["kept.mp4", "outside.mp4"]
    assert not removed_thumb.exists()
    assert outside_thumb.exists()
