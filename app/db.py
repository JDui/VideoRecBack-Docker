from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    relative_path TEXT,
    folder TEXT,
    type TEXT NOT NULL DEFAULT 'flat',
    size_bytes INTEGER NOT NULL DEFAULT 0,
    duration_seconds REAL,
    width INTEGER,
    height INTEGER,
    aspect_ratio REAL,
    bit_depth INTEGER,
    is_10bit INTEGER,
    chroma_subsampling TEXT,
    average_bitrate INTEGER,
    video_codec TEXT,
    mtime REAL NOT NULL DEFAULT 0,
    mtime_ns INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    missing INTEGER NOT NULL DEFAULT 0,
    favorite INTEGER NOT NULL DEFAULT 0,
    thumb_status TEXT NOT NULL DEFAULT 'pending',
    thumb_error TEXT,
    thumb_path TEXT,
    thumb_version INTEGER NOT NULL DEFAULT 2,
    media_version INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scan_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL,
    action TEXT NOT NULL DEFAULT 'upsert',
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS media_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id INTEGER NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(video_id) REFERENCES videos(id) ON DELETE CASCADE
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_videos_visible_timeline ON videos(missing, mtime DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_videos_visible_type_timeline ON videos(missing, type, mtime DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_videos_visible_favorite_timeline ON videos(missing, favorite, mtime DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_videos_missing ON videos(missing);
CREATE INDEX IF NOT EXISTS idx_videos_favorite ON videos(favorite, mtime DESC);
CREATE INDEX IF NOT EXISTS idx_videos_type ON videos(type);
CREATE INDEX IF NOT EXISTS idx_videos_folder ON videos(folder);
CREATE INDEX IF NOT EXISTS idx_scan_queue_status ON scan_queue(status, created_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_scan_queue_path ON scan_queue(path);
CREATE INDEX IF NOT EXISTS idx_media_jobs_pending ON media_jobs(status, created_at, id);
"""

MIGRATIONS = {
    "relative_path": "ALTER TABLE videos ADD COLUMN relative_path TEXT",
    "folder": "ALTER TABLE videos ADD COLUMN folder TEXT",
    "width": "ALTER TABLE videos ADD COLUMN width INTEGER",
    "height": "ALTER TABLE videos ADD COLUMN height INTEGER",
    "aspect_ratio": "ALTER TABLE videos ADD COLUMN aspect_ratio REAL",
    "bit_depth": "ALTER TABLE videos ADD COLUMN bit_depth INTEGER",
    "is_10bit": "ALTER TABLE videos ADD COLUMN is_10bit INTEGER",
    "chroma_subsampling": "ALTER TABLE videos ADD COLUMN chroma_subsampling TEXT",
    "average_bitrate": "ALTER TABLE videos ADD COLUMN average_bitrate INTEGER",
    "video_codec": "ALTER TABLE videos ADD COLUMN video_codec TEXT",
    "mtime_ns": "ALTER TABLE videos ADD COLUMN mtime_ns INTEGER NOT NULL DEFAULT 0",
    "thumb_version": "ALTER TABLE videos ADD COLUMN thumb_version INTEGER NOT NULL DEFAULT 0",
    "favorite": "ALTER TABLE videos ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0",
    "media_version": "ALTER TABLE videos ADD COLUMN media_version INTEGER NOT NULL DEFAULT 0",
}


class Database:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.path = data_dir / "app.sqlite3"

    def init(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            existing = {row["name"] for row in conn.execute("PRAGMA table_info(videos)").fetchall()}
            for column, statement in MIGRATIONS.items():
                if column not in existing:
                    conn.execute(statement)
            if "media_version" not in existing:
                conn.execute(
                    """
                    UPDATE videos
                    SET media_version = 1
                    WHERE thumb_status = 'ready' AND thumb_path IS NOT NULL
                    """
                )
            conn.execute("DELETE FROM scan_queue WHERE status = 'done'")
            conn.execute(
                """
                DELETE FROM scan_queue
                WHERE id NOT IN (SELECT MAX(id) FROM scan_queue GROUP BY path)
                """
            )
            conn.execute(
                "UPDATE media_jobs SET status = 'pending' WHERE status = 'running'"
            )
            conn.executescript(INDEXES)

    def sync_settings(self, values: dict[str, object]) -> None:
        with self.connect() as conn:
            conn.executemany(
                """
                INSERT INTO app_settings(key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
                """,
                [(key, str(value)) for key, value in values.items()],
            )

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA wal_autocheckpoint=256")
        conn.execute("PRAGMA journal_size_limit=33554432")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
