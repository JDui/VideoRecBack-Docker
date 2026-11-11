import asyncio

from PIL import Image

from app.config import Settings
from app.db import Database
from app.scanner import MEDIA_VERSION, THUMBNAIL_VERSION, Scanner
from app.thumbnails import VideoToolError


def make_scanner(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    source = root / "clip.mp4"
    source.write_bytes(b"source fixture")
    db = Database(tmp_path / "data")
    db.init()
    scanner = Scanner(db, tmp_path / "data")
    scanner.cache_dir.mkdir()
    main = scanner.cache_dir / "1.webp"
    Image.new("RGB", (160, 90), "red").save(main)
    stat = source.stat()
    with db.connect() as conn:
        conn.execute(
            """INSERT INTO videos(id, path, name, relative_path, folder, type, size_bytes,
                   mtime, mtime_ns, duration_seconds, width, height, thumb_status, thumb_version,
                   media_version, hdr_checked, thumb_path, thumb_revision)
               VALUES (1, ?, 'clip.mp4', 'clip.mp4', '/', 'flat', ?, ?, ?, 10, 160, 90,
                   'ready', ?, ?, 1, ?, 123)""",
            (str(source), stat.st_size, stat.st_mtime, stat.st_mtime_ns,
             THUMBNAIL_VERSION, MEDIA_VERSION, str(main)),
        )
    return scanner, source, main


def test_failed_sequence_keeps_successful_main_cover_and_removes_stale_sequence(monkeypatch, tmp_path):
    scanner, source, main = make_scanner(tmp_path)
    sequence = scanner.cache_dir / "1-scrolling.webp"
    sequence.write_bytes(b"stale")

    def generate_main(path, output, *args):
        Image.new("RGB", (160, 90), "blue").save(output)

    def fail_sequence(path, output, duration):
        output.write_bytes(b"partial")
        raise VideoToolError("cannot sample sequence")

    monkeypatch.setattr("app.scanner.generate_thumbnail", generate_main)
    monkeypatch.setattr("app.scanner.generate_scrolling_thumbnail", fail_sequence)
    assert scanner._generate_and_record_thumbnail(source, 1, "flat", 10)
    assert not sequence.exists()
    with scanner.db.connect() as conn:
        row = conn.execute("SELECT thumb_status, thumb_error, thumb_path FROM videos WHERE id=1").fetchone()
    assert row["thumb_status"] == "ready" and row["thumb_error"] is None
    assert row["thumb_path"] == str(main)
    with Image.open(main) as cover:
        assert cover.getpixel((80, 45))[2] > 200


def test_unchanged_scan_and_media_queue_fill_missing_sequence_without_rebuilding_main(monkeypatch, tmp_path):
    scanner, source, main = make_scanner(tmp_path)
    original = main.read_bytes()
    calls = []

    def generate_sequence(path, output, duration):
        calls.append(path)
        output.write_bytes(b"sequence fixture")

    def unexpected_main(*args):
        raise AssertionError("main cover must stay cached")

    monkeypatch.setattr("app.scanner.generate_scrolling_thumbnail", generate_sequence)
    monkeypatch.setattr("app.scanner.generate_thumbnail", unexpected_main)
    monkeypatch.setattr(scanner, "_refresh_metadata", lambda *args: True)
    settings = Settings(video_root=str(source.parent))
    assert scanner._scan_sync(settings).indexed == 0
    assert scanner.pending_media_jobs() == 1
    assert scanner._process_media_jobs_sync(settings, 1).indexed == 1
    assert scanner.pending_media_jobs() == 0
    sequence = scanner.cache_dir / "1-scrolling.webp"
    assert sequence.is_file() and calls == [source]
    sequence.unlink()
    assert scanner._scan_file_sync(settings, source).indexed == 0
    assert sequence.is_file() and calls == [source, source]
    assert main.read_bytes() == original
    with scanner.db.connect() as conn:
        assert conn.execute("SELECT thumb_revision FROM videos WHERE id=1").fetchone()[0] == 123


def test_sequence_maintenance_and_cleanup_do_not_replace_main_covers(monkeypatch, tmp_path):
    scanner, source, main = make_scanner(tmp_path)
    original = main.read_bytes()
    sequence = scanner.cache_dir / "1-scrolling.webp"
    monkeypatch.setattr("app.scanner.generate_scrolling_thumbnail",
                        lambda path, output, duration: output.write_bytes(b"sequence fixture"))
    progress = []
    summary = asyncio.run(scanner.rebuild_scrolling_thumbnails(
        Settings(), lambda summary, total: progress.append((summary.seen, total)),
    ))
    assert (summary.indexed, summary.errors) == (1, 0)
    assert progress == [(1, 1)] and not scanner.is_running
    assert main.read_bytes() == original and sequence.is_file()
    monkeypatch.setattr("app.scanner.generate_thumbnail",
                        lambda path, output, *args: Image.new("RGB", (160, 90), "blue").save(output))
    assert scanner._generate_and_record_thumbnail(source, 1, "panorama", 10)
    assert not sequence.exists()
    sequence.write_bytes(b"old flat sequence")
    assert scanner._delete_target_records(source) == 1
    assert not sequence.exists() and not main.exists()


def test_failed_preserved_main_rebuild_keeps_existing_sequence(monkeypatch, tmp_path):
    scanner, source, main = make_scanner(tmp_path)
    original = main.read_bytes()
    sequence = scanner.cache_dir / "1-scrolling.webp"
    sequence.write_bytes(b"existing sequence")

    def fail_main(*args):
        raise VideoToolError("source is offline")

    monkeypatch.setattr("app.scanner.generate_thumbnail", fail_main)
    assert not scanner._generate_and_record_thumbnail(source, 1, "flat", 10, preserve_existing=True)
    assert main.read_bytes() == original
    assert sequence.read_bytes() == b"existing sequence"
