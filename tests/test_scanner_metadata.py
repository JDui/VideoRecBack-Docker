from pathlib import Path

from app.config import Settings
from app.db import Database
import pytest

from app.scanner import Scanner, calculate_aspect_ratio, safe_relative_path
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
