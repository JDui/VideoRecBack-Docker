from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.db import Database
from app.thumbnails import VideoToolError, generate_thumbnail, probe_video
from app.video_types import detect_video_type

THUMBNAIL_VERSION = 4


@dataclass(slots=True)
class ScanSummary:
    seen: int = 0
    indexed: int = 0
    thumbnails: int = 0
    errors: int = 0


class Scanner:
    def __init__(self, db: Database, data_dir: Path):
        self.db = db
        self.cache_dir = data_dir / "cache"
        self._lock = asyncio.Lock()

    async def scan(self, settings: Settings) -> ScanSummary:
        async with self._lock:
            return await asyncio.to_thread(self._scan_sync, settings)

    def _scan_sync(self, settings: Settings) -> ScanSummary:
        summary = ScanSummary()
        root = Path(settings.video_root)
        if not root.exists():
            return summary

        extensions = {ext.lower() for ext in settings.video_extensions}
        seen_paths: set[str] = set()
        for path in root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in extensions:
                continue
            summary.seen += 1
            seen_paths.add(str(path))
            summary.indexed += self._upsert_video(path, root)

        self._mark_missing(seen_paths)
        return summary

    def _upsert_video(self, path: Path, root: Path) -> int:
        stat = path.stat()
        path_text = str(path)
        name = path.name
        video_type = detect_video_type(path)
        relative_path = safe_relative_path(path, root)
        folder = str(Path(relative_path).parent)
        if folder == ".":
            folder = "/"

        unchanged_thumbnail_args: tuple[int, str, float | None] | None = None
        unchanged_metadata_id: int | None = None
        with self.db.connect() as conn:
            row = conn.execute("SELECT * FROM videos WHERE path = ?", (path_text,)).fetchone()
            unchanged = row and row["mtime"] == stat.st_mtime and row["size_bytes"] == stat.st_size
            if unchanged:
                row_id = int(row["id"])
                conn.execute(
                    """
                    UPDATE videos
                    SET missing = 0, relative_path = ?, folder = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (relative_path, folder, row_id),
                )
                thumb_path = row["thumb_path"]
                thumb_missing = not thumb_path or not Path(thumb_path).exists()
                metadata_missing = row["duration_seconds"] is None or row["width"] is None or row["height"] is None
                if metadata_missing:
                    unchanged_metadata_id = row_id
                version_stale = row["thumb_version"] != THUMBNAIL_VERSION
                if row["thumb_status"] in {"pending", "error"} or thumb_missing or version_stale:
                    unchanged_thumbnail_args = (row_id, row["type"], row["duration_seconds"])
        if row and unchanged:
            if unchanged_metadata_id is not None:
                self._refresh_metadata(path, unchanged_metadata_id)
            if unchanged_thumbnail_args is not None:
                row_id, cached_type, cached_duration = unchanged_thumbnail_args
                self._generate_and_record_thumbnail(path, row_id, cached_type, cached_duration)
            return 0

        duration = None
        width = None
        height = None
        thumb_status = "pending"
        thumb_error = None
        try:
            probe = probe_video(path)
            duration = probe.duration_seconds
            width = probe.width
            height = probe.height
        except (VideoToolError, OSError) as exc:
            thumb_status = "error"
            thumb_error = str(exc)
        aspect_ratio = calculate_aspect_ratio(width, height)

        thumb_path = str(self.cache_dir / f"{stable_id(path_text)}.webp")
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO videos (
                    path, name, relative_path, folder, type, size_bytes, duration_seconds,
                    width, height, aspect_ratio, mtime,
                    missing, thumb_status, thumb_error, thumb_path, thumb_version, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(path) DO UPDATE SET
                    name = excluded.name,
                    relative_path = excluded.relative_path,
                    folder = excluded.folder,
                    type = excluded.type,
                    size_bytes = excluded.size_bytes,
                    duration_seconds = excluded.duration_seconds,
                    width = excluded.width,
                    height = excluded.height,
                    aspect_ratio = excluded.aspect_ratio,
                    mtime = excluded.mtime,
                    missing = 0,
                    thumb_status = excluded.thumb_status,
                    thumb_error = excluded.thumb_error,
                    thumb_path = excluded.thumb_path,
                    thumb_version = excluded.thumb_version,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    path_text,
                    name,
                    relative_path,
                    folder,
                    video_type,
                    stat.st_size,
                    duration,
                    width,
                    height,
                    aspect_ratio,
                    stat.st_mtime,
                    thumb_status,
                    thumb_error,
                    thumb_path,
                    THUMBNAIL_VERSION,
                ),
            )
            row = conn.execute("SELECT id FROM videos WHERE path = ?", (path_text,)).fetchone()
            video_id = int(row["id"])

        if thumb_status == "pending":
            self._generate_and_record_thumbnail(path, video_id, video_type, duration)
        return 1

    def _refresh_metadata(self, path: Path, video_id: int) -> None:
        try:
            probe = probe_video(path)
        except (VideoToolError, OSError):
            return
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE videos
                SET duration_seconds = ?, width = ?, height = ?, aspect_ratio = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    probe.duration_seconds,
                    probe.width,
                    probe.height,
                    calculate_aspect_ratio(probe.width, probe.height),
                    video_id,
                ),
            )

    def _generate_and_record_thumbnail(
        self,
        path: Path,
        video_id: int,
        video_type: str,
        duration: float | None,
    ) -> None:
        try:
            final_thumb_path = self.cache_dir / f"{video_id}.webp"
            generate_thumbnail(path, final_thumb_path, video_type, duration)
            with self.db.connect() as conn:
                conn.execute(
                    """
                    UPDATE videos
                    SET thumb_status = 'ready', thumb_error = NULL, thumb_path = ?, thumb_version = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (str(final_thumb_path), THUMBNAIL_VERSION, video_id),
                )
        except (VideoToolError, OSError) as exc:
            with self.db.connect() as conn:
                conn.execute(
                    """
                    UPDATE videos
                    SET thumb_status = 'error', thumb_error = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (str(exc), video_id),
                )

    def _mark_missing(self, seen_paths: set[str]) -> None:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT id, path FROM videos WHERE missing = 0").fetchall()
            for row in rows:
                if row["path"] not in seen_paths:
                    conn.execute(
                        "UPDATE videos SET missing = 1, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (row["id"],),
                    )


def stable_id(value: str) -> str:
    import hashlib

    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def calculate_aspect_ratio(width: int | None, height: int | None) -> float | None:
    if not width or not height:
        return None
    return round(width / height, 4)


def safe_relative_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name
