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
    mtime REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    missing INTEGER NOT NULL DEFAULT 0,
    favorite INTEGER NOT NULL DEFAULT 0,
    thumb_status TEXT NOT NULL DEFAULT 'pending',
    thumb_error TEXT,
    thumb_path TEXT,
    thumb_version INTEGER NOT NULL DEFAULT 2
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
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_videos_mtime ON videos(mtime DESC);
CREATE INDEX IF NOT EXISTS idx_videos_missing ON videos(missing);
CREATE INDEX IF NOT EXISTS idx_videos_favorite ON videos(favorite, mtime DESC);
CREATE INDEX IF NOT EXISTS idx_videos_type ON videos(type);
CREATE INDEX IF NOT EXISTS idx_videos_folder ON videos(folder);
CREATE INDEX IF NOT EXISTS idx_scan_queue_status ON scan_queue(status, created_at);
"""

MIGRATIONS = {
    "relative_path": "ALTER TABLE videos ADD COLUMN relative_path TEXT",
    "folder": "ALTER TABLE videos ADD COLUMN folder TEXT",
    "width": "ALTER TABLE videos ADD COLUMN width INTEGER",
    "height": "ALTER TABLE videos ADD COLUMN height INTEGER",
    "aspect_ratio": "ALTER TABLE videos ADD COLUMN aspect_ratio REAL",
    "thumb_version": "ALTER TABLE videos ADD COLUMN thumb_version INTEGER NOT NULL DEFAULT 0",
    "favorite": "ALTER TABLE videos ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0",
}


class Database:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.path = data_dir / "app.sqlite3"

    def init(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            existing = {row["name"] for row in conn.execute("PRAGMA table_info(videos)").fetchall()}
            for column, statement in MIGRATIONS.items():
                if column not in existing:
                    conn.execute(statement)
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
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
