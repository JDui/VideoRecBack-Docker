from app.db import Database


def test_database_has_video_metadata_columns(tmp_path):
    db = Database(tmp_path)
    db.init()

    with db.connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(videos)").fetchall()}

    assert {
        "relative_path",
        "folder",
        "width",
        "height",
        "aspect_ratio",
        "bit_depth",
        "is_10bit",
        "chroma_subsampling",
        "average_bitrate",
        "video_codec",
        "thumb_version",
        "media_version",
        "favorite",
    }.issubset(columns)


def test_database_syncs_app_settings(tmp_path):
    db = Database(tmp_path)
    db.init()

    db.sync_settings({"default_volume_percent": 20, "site_title": "视频归档"})
    db.sync_settings({"default_volume_percent": 35})

    with db.connect() as conn:
        rows = {
            row["key"]: row["value"]
            for row in conn.execute("SELECT key, value FROM app_settings").fetchall()
        }

    assert rows["default_volume_percent"] == "35"
    assert rows["site_title"] == "视频归档"


def test_database_does_not_create_legacy_timeline_labels_table(tmp_path):
    db = Database(tmp_path)
    db.init()

    with db.connect() as conn:
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}

    assert "timeline_labels" not in tables


def test_database_has_scan_queue_table(tmp_path):
    db = Database(tmp_path)
    db.init()

    with db.connect() as conn:
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}

    assert "scan_queue" in tables


def test_database_has_scan_indexes_and_media_jobs(tmp_path):
    db = Database(tmp_path)
    db.init()

    with db.connect() as conn:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(videos)").fetchall()}
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}
        indexes = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'").fetchall()}

    assert "mtime_ns" in columns
    assert "media_jobs" in tables
    assert "idx_videos_visible_timeline" in indexes
    assert "idx_scan_queue_path" in indexes


def test_database_uses_bounded_wal_settings(tmp_path):
    db = Database(tmp_path)
    db.init()

    with db.connect() as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        synchronous = conn.execute("PRAGMA synchronous").fetchone()[0]
        checkpoint_pages = conn.execute("PRAGMA wal_autocheckpoint").fetchone()[0]
        journal_limit = conn.execute("PRAGMA journal_size_limit").fetchone()[0]

    assert journal_mode == "wal"
    assert synchronous == 1
    assert checkpoint_pages == 256
    assert journal_limit == 32 * 1024 * 1024
