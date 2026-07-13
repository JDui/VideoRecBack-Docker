from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.db import Database
from app.thumbnails import VideoToolError, generate_preview_thumbnail, generate_thumbnail, probe_video
from app.video_types import detect_video_type

THUMBNAIL_VERSION = 8
LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ScanSummary:
    seen: int = 0
    indexed: int = 0
    thumbnails: int = 0
    errors: int = 0
    deleted: int = 0
    skipped: int = 0


@dataclass(slots=True)
class FileSnapshot:
    path: Path
    size_bytes: int
    mtime: float
    mtime_ns: int


class Scanner:
    def __init__(self, db: Database, data_dir: Path):
        self.db = db
        self.cache_dir = data_dir / "cache"
        self._lock = asyncio.Lock()
        self._running_count = 0
        self._media_running_count = 0

    @property
    def is_running(self) -> bool:
        return self._running_count > 0

    @property
    def is_processing_media(self) -> bool:
        return self._media_running_count > 0

    async def scan(self, settings: Settings) -> ScanSummary:
        self._running_count += 1
        try:
            async with self._lock:
                return await asyncio.to_thread(self._scan_sync, settings)
        finally:
            self._running_count -= 1

    async def scan_path(self, settings: Settings, target: str | Path, action: str = "upsert") -> ScanSummary:
        self._running_count += 1
        try:
            async with self._lock:
                return await asyncio.to_thread(self._scan_path_sync, settings, Path(target), action)
        finally:
            self._running_count -= 1

    async def scan_file(self, settings: Settings, target: str | Path, action: str = "upsert") -> ScanSummary:
        self._running_count += 1
        try:
            async with self._lock:
                return await asyncio.to_thread(self._scan_file_sync, settings, Path(target), action)
        finally:
            self._running_count -= 1

    async def scan_folder(self, settings: Settings, target: str | Path, action: str = "upsert") -> ScanSummary:
        self._running_count += 1
        try:
            async with self._lock:
                return await asyncio.to_thread(self._scan_folder_sync, settings, Path(target), action)
        finally:
            self._running_count -= 1

    async def enqueue(self, target: str | Path, action: str = "upsert") -> None:
        clean_action = action if action in {"upsert", "delete"} else "upsert"
        clean_path = str(Path(target))
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO scan_queue(path, action, status, updated_at)
                VALUES (?, ?, 'pending', CURRENT_TIMESTAMP)
                ON CONFLICT(path) DO UPDATE SET
                    action = excluded.action,
                    status = 'pending',
                    error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (clean_path, clean_action),
            )

    async def process_queue(self, settings: Settings, limit: int = 50) -> ScanSummary:
        self._running_count += 1
        try:
            async with self._lock:
                return await asyncio.to_thread(self._process_queue_sync, settings, limit)
        finally:
            self._running_count -= 1

    async def recheck_all_video_metadata(self) -> ScanSummary:
        self._running_count += 1
        try:
            async with self._lock:
                return await asyncio.to_thread(self._recheck_all_video_metadata_sync)
        finally:
            self._running_count -= 1

    async def process_media_jobs(self, settings: Settings, limit: int = 1) -> ScanSummary:
        self._media_running_count += 1
        try:
            async with self._lock:
                return await asyncio.to_thread(self._process_media_jobs_sync, settings, limit)
        finally:
            self._media_running_count -= 1

    def pending_media_jobs(self) -> int:
        with self.db.connect() as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) AS count FROM media_jobs WHERE status IN ('pending', 'running')"
                ).fetchone()["count"]
            )

    def _scan_sync(self, settings: Settings) -> ScanSummary:
        summary = ScanSummary()
        root = Path(settings.video_root)
        if not root.exists():
            return summary

        extensions = {ext.lower() for ext in settings.video_extensions}
        existing = self._load_video_index()
        seen_paths: set[str] = set()
        changed: list[FileSnapshot] = []
        incomplete_ids: list[int] = []
        metadata_updates: list[tuple[int, str, str, int]] = []
        for snapshot in iter_video_files(root, extensions, settings, summary):
            path = snapshot.path
            path_text = str(path)
            summary.seen += 1
            seen_paths.add(path_text)
            row = existing.get(path_text)
            if row is None or not snapshot_matches(row, snapshot):
                changed.append(snapshot)
                continue

            relative_path = safe_relative_path(path, root)
            folder = str(Path(relative_path).parent)
            if folder == ".":
                folder = "/"
            if row["relative_path"] != relative_path or row["folder"] != folder or not row["mtime_ns"]:
                metadata_updates.append((snapshot.mtime_ns, relative_path, folder, int(row["id"])))
            if media_data_incomplete(row):
                incomplete_ids.append(int(row["id"]))

        summary.indexed = self._record_changed_files(changed, root, incomplete_ids, metadata_updates)
        missing = [row for path, row in existing.items() if path not in seen_paths]
        summary.deleted = self._delete_video_records(missing)
        return summary

    def _load_video_index(self) -> dict[str, object]:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT * FROM videos").fetchall()
        return {str(row["path"]): row for row in rows}

    def _record_changed_files(
        self,
        snapshots: list[FileSnapshot],
        root: Path,
        incomplete_ids: list[int],
        metadata_updates: list[tuple[int, str, str, int]],
    ) -> int:
        if not snapshots and not incomplete_ids and not metadata_updates:
            return 0
        with self.db.connect() as conn:
            if metadata_updates:
                conn.executemany(
                    """
                    UPDATE videos
                    SET mtime_ns = ?, relative_path = ?, folder = ?
                    WHERE id = ?
                    """,
                    metadata_updates,
                )
            for snapshot in snapshots:
                path = snapshot.path
                relative_path = safe_relative_path(path, root)
                folder = str(Path(relative_path).parent)
                if folder == ".":
                    folder = "/"
                row = conn.execute(
                    """
                    INSERT INTO videos (
                        path, name, relative_path, folder, type, size_bytes,
                        mtime, mtime_ns, missing, thumb_status, thumb_error,
                        thumb_version, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 'pending', NULL, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(path) DO UPDATE SET
                        name = excluded.name,
                        relative_path = excluded.relative_path,
                        folder = excluded.folder,
                        type = excluded.type,
                        size_bytes = excluded.size_bytes,
                        duration_seconds = NULL,
                        width = NULL,
                        height = NULL,
                        aspect_ratio = NULL,
                        bit_depth = NULL,
                        is_10bit = NULL,
                        chroma_subsampling = NULL,
                        average_bitrate = NULL,
                        video_codec = NULL,
                        mtime = excluded.mtime,
                        mtime_ns = excluded.mtime_ns,
                        missing = 0,
                        thumb_status = 'pending',
                        thumb_error = NULL,
                        thumb_version = excluded.thumb_version,
                        updated_at = CURRENT_TIMESTAMP
                    RETURNING id
                    """,
                    (
                        str(path),
                        path.name,
                        relative_path,
                        folder,
                        detect_video_type(path),
                        snapshot.size_bytes,
                        snapshot.mtime,
                        snapshot.mtime_ns,
                        THUMBNAIL_VERSION,
                    ),
                ).fetchone()
                self._enqueue_media_job(int(row["id"]), conn=conn)
            for video_id in incomplete_ids:
                self._enqueue_media_job(video_id, conn=conn)
        return len(snapshots)

    def _enqueue_media_job(self, video_id: int, conn=None) -> None:
        def execute(connection) -> None:
            connection.execute(
                """
                INSERT INTO media_jobs(video_id, status, attempts, error, updated_at)
                VALUES (?, 'pending', 0, NULL, CURRENT_TIMESTAMP)
                ON CONFLICT(video_id) DO UPDATE SET
                    status = 'pending',
                    attempts = 0,
                    error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (video_id,),
            )

        if conn is not None:
            execute(conn)
            return
        with self.db.connect() as connection:
            execute(connection)

    def _scan_path_sync(
        self,
        settings: Settings,
        target: Path,
        action: str = "upsert",
        defer_media: bool = False,
    ) -> ScanSummary:
        root = Path(settings.video_root)
        target = Path(target)
        target = normalize_target(root, target)
        if target.exists() and target.is_dir():
            return self._scan_folder_sync(settings, target, action, defer_media)
        return self._scan_file_sync(settings, target, action, defer_media)

    def _scan_file_sync(
        self,
        settings: Settings,
        target: Path,
        action: str = "upsert",
        defer_media: bool = False,
    ) -> ScanSummary:
        summary = ScanSummary()
        root = Path(settings.video_root)
        target = Path(target)
        target = validate_target_in_root(root, target)
        LOGGER.info("Scanning single file only: %s; no full scan will run.", target)
        if action == "delete" or not target.exists():
            summary.deleted = self._delete_target_records(target)
            return summary

        extensions = {ext.lower() for ext in settings.video_extensions}
        if should_ignore_path(target, root, settings):
            summary.skipped = 1
            LOGGER.info("Skipped single-file scan for %s; ignored by settings.", target)
            return summary
        if not target.is_file() or target.suffix.lower() not in extensions:
            summary.skipped = 1
            LOGGER.info("Skipped single-file scan for %s; unsupported extension or not a file.", target)
            return summary

        summary.seen = 1
        summary.indexed = self._upsert_video(target, root, settings, defer_media=defer_media)
        LOGGER.info(
            "Single-file scan complete: seen=%s indexed=%s skipped=%s deleted=%s.",
            summary.seen,
            summary.indexed,
            summary.skipped,
            summary.deleted,
        )
        return summary

    def _scan_folder_sync(
        self,
        settings: Settings,
        target: Path,
        action: str = "upsert",
        defer_media: bool = False,
    ) -> ScanSummary:
        summary = ScanSummary()
        root = Path(settings.video_root)
        target = Path(target)
        target = validate_target_in_root(root, target)
        LOGGER.info("Scanning folder only: %s; siblings and root will not be scanned.", target)
        if action == "delete" or not target.exists():
            summary.deleted = self._delete_target_records(target)
            LOGGER.info("Folder scan deleted cached records: target=%s deleted=%s.", target, summary.deleted)
            return summary

        if not target.is_dir():
            summary.skipped = 1
            return summary

        extensions = {ext.lower() for ext in settings.video_extensions}
        seen_paths: set[str] = set()
        for snapshot in iter_video_files(target, extensions, settings, summary, ignore_root=root, count_unsupported=True):
            path = snapshot.path
            summary.seen += 1
            seen_paths.add(str(path))
            summary.indexed += self._upsert_video(path, root, settings, defer_media=defer_media)
        summary.deleted = self._delete_missing_under_target(target, seen_paths)
        LOGGER.info(
            "Folder scan complete: target=%s seen=%s indexed=%s skipped=%s missing=%s errors=%s.",
            target,
            summary.seen,
            summary.indexed,
            summary.skipped,
            summary.deleted,
            summary.errors,
        )
        return summary

    def _process_queue_sync(self, settings: Settings, limit: int) -> ScanSummary:
        total = ScanSummary()
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, path, action
                FROM scan_queue
                WHERE status = 'pending'
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        for row in rows:
            with self.db.connect() as conn:
                conn.execute(
                    "UPDATE scan_queue SET status = 'running', error = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                    (row["id"],),
                )
            try:
                summary = self._scan_path_sync(settings, row["path"], row["action"], defer_media=True)
                total.seen += summary.seen
                total.indexed += summary.indexed
                total.thumbnails += summary.thumbnails
                total.errors += summary.errors
                total.deleted += summary.deleted
                total.skipped += summary.skipped
                with self.db.connect() as conn:
                    conn.execute("DELETE FROM scan_queue WHERE id = ?", (row["id"],))
            except Exception as exc:
                total.errors += 1
                with self.db.connect() as conn:
                    conn.execute(
                        """
                        UPDATE scan_queue
                        SET status = 'error', error = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (str(exc), row["id"]),
                    )
        return total

    def _process_media_jobs_sync(self, settings: Settings, limit: int) -> ScanSummary:
        summary = ScanSummary()
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT media_jobs.id AS job_id, media_jobs.video_id, videos.path
                FROM media_jobs
                JOIN videos ON videos.id = media_jobs.video_id
                WHERE media_jobs.status = 'pending' AND media_jobs.attempts < 3
                ORDER BY media_jobs.created_at ASC, media_jobs.id ASC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

        for row in rows:
            job_id = int(row["job_id"])
            video_id = int(row["video_id"])
            path = Path(row["path"])
            summary.seen += 1
            with self.db.connect() as conn:
                conn.execute(
                    """
                    UPDATE media_jobs
                    SET status = 'running', attempts = attempts + 1, error = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (job_id,),
                )
            try:
                if not path.is_file():
                    raise VideoToolError(f"Video file is unavailable: {path}")
                if not self._refresh_metadata(path, video_id):
                    raise VideoToolError(f"Unable to read video metadata: {path}")
                with self.db.connect() as conn:
                    video = conn.execute(
                        "SELECT type, duration_seconds FROM videos WHERE id = ?",
                        (video_id,),
                    ).fetchone()
                if video is None:
                    continue
                if not self._generate_and_record_thumbnail(
                    path,
                    video_id,
                    video["type"],
                    video["duration_seconds"],
                    settings.thumbnail_resolution,
                ):
                    raise VideoToolError(f"Unable to generate thumbnail: {path}")
                with self.db.connect() as conn:
                    conn.execute("DELETE FROM media_jobs WHERE id = ?", (job_id,))
                summary.indexed += 1
                summary.thumbnails += 1
            except (VideoToolError, OSError) as exc:
                summary.errors += 1
                with self.db.connect() as conn:
                    conn.execute(
                        """
                        UPDATE media_jobs
                        SET status = CASE WHEN attempts >= 3 THEN 'error' ELSE 'pending' END,
                            error = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE id = ?
                        """,
                        (str(exc), job_id),
                    )
        return summary

    def _upsert_video(self, path: Path, root: Path, settings: Settings, defer_media: bool = False) -> int:
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
                metadata_missing = (
                    row["duration_seconds"] is None
                    or row["width"] is None
                    or row["height"] is None
                    or row["bit_depth"] is None
                    or row["chroma_subsampling"] is None
                    or row["average_bitrate"] is None
                )
                if metadata_missing:
                    unchanged_metadata_id = row_id
                version_stale = row["thumb_version"] != THUMBNAIL_VERSION
                if row["thumb_status"] in {"pending", "error"} or thumb_missing or version_stale:
                    unchanged_thumbnail_args = (row_id, row["type"], row["duration_seconds"])
        if row and unchanged:
            if defer_media:
                if unchanged_metadata_id is not None or unchanged_thumbnail_args is not None:
                    self._enqueue_media_job(row_id)
                return 0
            if unchanged_metadata_id is not None:
                self._refresh_metadata(path, unchanged_metadata_id)
            if unchanged_thumbnail_args is not None:
                row_id, cached_type, cached_duration = unchanged_thumbnail_args
                self._generate_and_record_thumbnail(
                    path,
                    row_id,
                    cached_type,
                    cached_duration,
                    settings.thumbnail_resolution,
                )
            return 0

        if defer_media:
            snapshot = FileSnapshot(path, stat.st_size, stat.st_mtime, stat.st_mtime_ns)
            return self._record_changed_files([snapshot], root, [], [])

        duration = None
        width = None
        height = None
        bit_depth = None
        video_codec = None
        chroma_subsampling = None
        average_bitrate = None
        is_10bit = None
        thumb_status = "pending"
        thumb_error = None
        try:
            probe = probe_video(path)
            duration = probe.duration_seconds
            width = probe.width
            height = probe.height
            bit_depth = probe.bit_depth
            is_10bit = is_tenbit_depth(bit_depth)
            video_codec = probe.codec_name
            chroma_subsampling = probe.chroma_subsampling
            average_bitrate = probe.average_bitrate
        except (VideoToolError, OSError) as exc:
            thumb_status = "error"
            thumb_error = str(exc)
        aspect_ratio = calculate_aspect_ratio(width, height)
        video_type = detect_video_type(path, width, height)

        thumb_path = str(self.cache_dir / f"{stable_id(path_text)}.webp")
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO videos (
                    path, name, relative_path, folder, type, size_bytes, duration_seconds,
                    width, height, aspect_ratio, bit_depth, is_10bit, chroma_subsampling,
                    average_bitrate, video_codec, mtime,
                    missing, thumb_status, thumb_error, thumb_path, thumb_version, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, CURRENT_TIMESTAMP)
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
                    bit_depth = excluded.bit_depth,
                    is_10bit = excluded.is_10bit,
                    chroma_subsampling = excluded.chroma_subsampling,
                    average_bitrate = excluded.average_bitrate,
                    video_codec = excluded.video_codec,
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
                    bit_depth,
                    is_10bit,
                    chroma_subsampling,
                    average_bitrate,
                    video_codec,
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
            self._generate_and_record_thumbnail(path, video_id, video_type, duration, settings.thumbnail_resolution)
        return 1

    def rebuild_video_thumbnail(self, video_id: int, video_type: str, thumbnail_resolution: int = 576) -> None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT path, duration_seconds FROM videos WHERE id = ?", (video_id,)).fetchone()
            if row is None:
                return
        self._generate_and_record_thumbnail(
            Path(row["path"]),
            video_id,
            video_type,
            row["duration_seconds"],
            thumbnail_resolution,
        )

    def rebuild_all_thumbnails(self, settings: Settings) -> int:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, path, type, duration_seconds, thumb_path
                FROM videos
                WHERE missing = 0
                ORDER BY id ASC
                """
            ).fetchall()
            conn.execute(
                """
                UPDATE videos
                SET thumb_status = 'pending',
                    thumb_error = NULL,
                    thumb_version = 0,
                    updated_at = CURRENT_TIMESTAMP
                WHERE missing = 0
                """
            )

        for row in rows:
            self._delete_thumbnail_files(row)
        for row in rows:
            self._generate_and_record_thumbnail(
                Path(row["path"]),
                int(row["id"]),
                row["type"],
                row["duration_seconds"],
                settings.thumbnail_resolution,
            )
        return len(rows)

    def recheck_panorama_types(self) -> int:
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, path, type, duration_seconds, width, height
                FROM videos
                WHERE missing = 0
                ORDER BY id ASC
                """
            ).fetchall()

        changed = 0
        for row in rows:
            if row["type"] == "panorama":
                continue
            if detect_video_type(row["path"], row["width"], row["height"]) != "panorama":
                continue
            video_id = int(row["id"])
            path = Path(row["path"])
            with self.db.connect() as conn:
                conn.execute(
                    """
                    UPDATE videos
                    SET type = 'panorama', thumb_status = 'pending', thumb_version = 0, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (video_id,),
                )
            self._generate_and_record_thumbnail(path, video_id, "panorama", row["duration_seconds"])
            changed += 1
        return changed

    def _recheck_all_video_metadata_sync(self) -> ScanSummary:
        summary = ScanSummary()
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, path
                FROM videos
                WHERE missing = 0
                ORDER BY id ASC
                """
            ).fetchall()

        for row in rows:
            summary.seen += 1
            path = Path(row["path"])
            if not path.exists() or not path.is_file():
                summary.errors += 1
                continue
            if self._refresh_metadata(path, int(row["id"])):
                summary.indexed += 1
            else:
                summary.errors += 1
        return summary

    def _refresh_metadata(self, path: Path, video_id: int) -> bool:
        try:
            probe = probe_video(path)
        except (VideoToolError, OSError):
            return False
        video_type = detect_video_type(path, probe.width, probe.height)
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE videos
                SET duration_seconds = ?,
                    width = ?,
                    height = ?,
                    aspect_ratio = ?,
                    bit_depth = ?,
                    is_10bit = ?,
                    chroma_subsampling = ?,
                    average_bitrate = ?,
                    video_codec = ?,
                    type = CASE WHEN ? = 'panorama' THEN 'panorama' ELSE type END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    probe.duration_seconds,
                    probe.width,
                    probe.height,
                    calculate_aspect_ratio(probe.width, probe.height),
                    probe.bit_depth,
                    is_tenbit_depth(probe.bit_depth),
                    probe.chroma_subsampling,
                    probe.average_bitrate,
                    probe.codec_name,
                    video_type,
                    video_id,
                ),
            )
        return True

    def _generate_and_record_thumbnail(
        self,
        path: Path,
        video_id: int,
        video_type: str,
        duration: float | None,
        thumbnail_resolution: int = 576,
    ) -> bool:
        try:
            final_thumb_path = self.cache_dir / f"{video_id}.webp"
            generate_thumbnail(path, final_thumb_path, video_type, duration, thumbnail_resolution)
            if final_thumb_path.exists():
                generate_preview_thumbnail(final_thumb_path, self.cache_dir / f"{video_id}-preview.webp")
            with self.db.connect() as conn:
                conn.execute(
                    """
                    UPDATE videos
                    SET thumb_status = 'ready', thumb_error = NULL, thumb_path = ?, thumb_version = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (str(final_thumb_path), THUMBNAIL_VERSION, video_id),
                )
            return True
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
            return False

    def _delete_target_records(self, target: Path) -> int:
        target_text = str(target)
        prefix = f"{target_text.rstrip('/')}/"
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, path, thumb_path
                FROM videos
                WHERE path = ? OR path LIKE ?
                """,
                (target_text, f"{prefix}%"),
            ).fetchall()
        return self._delete_video_records(rows)

    def _delete_missing_under_target(self, target: Path, seen_paths: set[str]) -> int:
        target_text = str(target)
        prefix = f"{target_text.rstrip('/')}/"
        with self.db.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, path, thumb_path
                FROM videos
                WHERE path = ? OR path LIKE ?
                """,
                (target_text, f"{prefix}%"),
            ).fetchall()
        rows_to_delete = [row for row in rows if row["path"] not in seen_paths]
        return self._delete_video_records(rows_to_delete)

    def _delete_missing(self, seen_paths: set[str]) -> int:
        with self.db.connect() as conn:
            rows = conn.execute("SELECT id, path, thumb_path FROM videos").fetchall()
        rows_to_delete = [row for row in rows if row["path"] not in seen_paths]
        return self._delete_video_records(rows_to_delete)

    def _delete_video_records(self, rows) -> int:
        if not rows:
            return 0
        for row in rows:
            self._delete_thumbnail_files(row)
        with self.db.connect() as conn:
            conn.executemany("DELETE FROM videos WHERE id = ?", [(row["id"],) for row in rows])
        return len(rows)

    def _delete_thumbnail_files(self, row) -> None:
        cache_root = self.cache_dir.resolve(strict=False)
        candidates = [
            Path(row["thumb_path"]) if row["thumb_path"] else None,
            self.cache_dir / f"{row['id']}.webp",
            self.cache_dir / f"{row['id']}-preview.webp",
            self.cache_dir / f"{stable_id(row['path'])}.webp",
        ]
        stream_dir = self.cache_dir / "streams"
        if stream_dir.exists():
            candidates.extend(stream_dir.glob(f"stream-{int(row['id'])}-*.mp4"))
            candidates.extend(stream_dir.glob(f"hls-{int(row['id'])}-*"))
        for candidate in candidates:
            if candidate is None:
                continue
            resolved = candidate.resolve(strict=False)
            try:
                resolved.relative_to(cache_root)
            except ValueError:
                LOGGER.warning("Skipped cache cleanup outside cache dir: %s", resolved)
                continue
            try:
                if resolved.is_dir():
                    for child in resolved.glob("*"):
                        if child.is_file():
                            child.unlink(missing_ok=True)
                    resolved.rmdir()
                else:
                    resolved.unlink(missing_ok=True)
            except OSError as exc:
                LOGGER.warning("Failed to delete cached thumbnail %s: %s", resolved, exc)


def stable_id(value: str) -> str:
    import hashlib

    return hashlib.sha1(value.encode("utf-8")).hexdigest()


def iter_video_files(
    scan_root: Path,
    extensions: set[str],
    settings: Settings,
    summary: ScanSummary,
    *,
    ignore_root: Path | None = None,
    count_unsupported: bool = False,
):
    ignore_root = ignore_root or scan_root
    pending = [scan_root]
    while pending:
        directory = pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    path = Path(entry.path)
                    if should_ignore_path(path, ignore_root, settings):
                        summary.skipped += 1
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            pending.append(path)
                            continue
                        if not entry.is_file(follow_symlinks=False) or path.suffix.lower() not in extensions:
                            if count_unsupported:
                                summary.skipped += 1
                            continue
                        stat = entry.stat(follow_symlinks=False)
                    except OSError:
                        summary.errors += 1
                        continue
                    yield FileSnapshot(path, stat.st_size, stat.st_mtime, stat.st_mtime_ns)
        except OSError:
            summary.errors += 1


def snapshot_matches(row, snapshot: FileSnapshot) -> bool:
    if int(row["size_bytes"] or 0) != snapshot.size_bytes:
        return False
    recorded_ns = int(row["mtime_ns"] or 0)
    if recorded_ns:
        return recorded_ns == snapshot.mtime_ns
    return float(row["mtime"] or 0) == snapshot.mtime


def media_data_incomplete(row) -> bool:
    metadata_missing = (
        row["duration_seconds"] is None
        or row["width"] is None
        or row["height"] is None
        or row["bit_depth"] is None
        or row["chroma_subsampling"] is None
        or row["average_bitrate"] is None
    )
    thumb_path = row["thumb_path"]
    thumbnail_missing = (
        row["thumb_status"] != "ready"
        or row["thumb_version"] != THUMBNAIL_VERSION
        or not thumb_path
        or not Path(thumb_path).exists()
    )
    return metadata_missing or thumbnail_missing


def calculate_aspect_ratio(width: int | None, height: int | None) -> float | None:
    if not width or not height:
        return None
    return round(width / height, 4)


def is_tenbit_depth(bit_depth: int | None) -> int | None:
    if bit_depth is None:
        return None
    return 1 if bit_depth >= 10 else 0


def safe_relative_path(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return path.name


def should_ignore_path(path: Path, root: Path, settings: Settings) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        try:
            relative = path.relative_to(root.resolve())
        except ValueError:
            relative = Path(path.name)

    parts = relative.parts or (path.name,)
    if settings.ignore_dotfiles and any(part.startswith(".") for part in parts):
        return True

    relative_text = relative.as_posix()
    for pattern in settings.ignore_name_patterns:
        if any(fnmatch.fnmatchcase(part, pattern) for part in parts):
            return True
        if fnmatch.fnmatchcase(relative_text, pattern):
            return True
    return False


def normalize_target(root: Path, target: Path) -> Path:
    if target.is_absolute():
        return target
    return root / target


def validate_target_in_root(root: Path, target: Path) -> Path:
    root_resolved = root.resolve()
    target = normalize_target(root_resolved, target)
    target_resolved = target.resolve() if target.exists() else target.resolve(strict=False)
    try:
        target_resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise VideoToolError(f"Scan target is outside video root: {target_resolved}") from exc
    return target_resolved
