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


def test_scan_path_delete_marks_matching_rows_missing(tmp_path):
    root = tmp_path / "media"
    root.mkdir()
    db = Database(tmp_path / "data")
    db.init()
    scanner = Scanner(db, tmp_path / "data")
    deleted_path = root / "gone.mp4"
    kept_path = root / "kept.mp4"
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO videos(path, name, mtime, missing) VALUES (?, 'gone.mp4', 1, 0)",
            (str(deleted_path),),
        )
        conn.execute(
            "INSERT INTO videos(path, name, mtime, missing) VALUES (?, 'kept.mp4', 1, 0)",
            (str(kept_path),),
        )

    summary = scanner._scan_path_sync(Settings(video_root=str(root)), deleted_path, "delete")

    with db.connect() as conn:
        rows = {
            row["name"]: row["missing"]
            for row in conn.execute("SELECT name, missing FROM videos").fetchall()
        }

    assert summary.deleted == 1
    assert rows == {"gone.mp4": 1, "kept.mp4": 0}


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


def test_scan_folder_marks_only_folder_records_missing(monkeypatch, tmp_path):
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
    db = Database(tmp_path / "data")
    db.init()
    scanner = Scanner(db, tmp_path / "data")

    monkeypatch.setattr("app.scanner.probe_video", lambda path: ProbeResult(10, 1920, 1080))
    monkeypatch.setattr("app.scanner.generate_thumbnail", lambda *args: None)
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO videos(path, name, mtime, missing) VALUES (?, 'removed.mp4', 1, 0)",
            (str(removed),),
        )
        conn.execute(
            "INSERT INTO videos(path, name, mtime, missing) VALUES (?, 'outside.mp4', 1, 0)",
            (str(outside),),
        )

    summary = scanner._scan_folder_sync(Settings(video_root=str(root)), target)

    with db.connect() as conn:
        rows = {
            row["name"]: row["missing"]
            for row in conn.execute("SELECT name, missing FROM videos").fetchall()
        }

    assert summary.seen == 1
    assert summary.deleted == 1
    assert rows == {"kept.mp4": 0, "outside.mp4": 0, "removed.mp4": 1}
