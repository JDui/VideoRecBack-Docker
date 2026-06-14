from __future__ import annotations

import asyncio
import fnmatch
import logging
from dataclasses import dataclass
from pathlib import Path

from app.config import Settings
from app.db import Database
from app.thumbnails import VideoToolError, generate_thumbnail, probe_video
from app.video_types import detect_video_type

THUMBNAIL_VERSION = 7
LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ScanSummary:
    seen: int = 0
    indexed: int = 0
    thumbnails: int = 0
    errors: int = 0
    deleted: int = 0
    skipped: int = 0


class Scanner:
    def __init__(self, db: Database, data_dir: Path):
        self.db = db
        self.cache_dir = data_dir / "cache"
        self._lock = asyncio.Lock()
        self._running_count = 0

    @property
    def is_running(self) -> bool:
        return self._running_count > 0

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
        with self.db.connect() as conn:
            conn.execute(
                """
                INSERT INTO scan_queue(path, action, status, updated_at)
                VALUES (?, ?, 'pending', CURRENT_TIMESTAMP)
                """,
                (str(target), clean_action),
            )

    async def process_queue(self, settings: Settings, limit: int = 50) -> ScanSummary:
        self._running_count += 1
        try:
            async with self._lock:
                return await asyncio.to_thread(self._process_queue_sync, settings, limit)
        finally:
            self._running_count -= 1

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
            if should_ignore_path(path, root, settings):
                summary.skipped += 1
                continue
            summary.seen += 1
            seen_paths.add(str(path))
            summary.indexed += self._upsert_video(path, root)

        summary.deleted = self._delete_missing(seen_paths)
        return summary

    def _scan_path_sync(self, settings: Settings, target: Path, action: str = "upsert") -> ScanSummary:
        root = Path(settings.video_root)
        target = Path(target)
        target = normalize_target(root, target)
        if target.exists() and target.is_dir():
            return self._scan_folder_sync(settings, target, action)
        return self._scan_file_sync(settings, target, action)

    def _scan_file_sync(self, settings: Settings, target: Path, action: str = "upsert") -> ScanSummary:
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
        summary.indexed = self._upsert_video(target, root)
        LOGGER.info(
            "Single-file scan complete: seen=%s indexed=%s skipped=%s deleted=%s.",
            summary.seen,
            summary.indexed,
            summary.skipped,
            summary.deleted,
        )
        return summary

    def _scan_folder_sync(self, settings: Settings, target: Path, action: str = "upsert") -> ScanSummary:
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
        for path in target.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in extensions:
                summary.skipped += 1
                continue
            if should_ignore_path(path, root, settings):
                summary.skipped += 1
                continue
            summary.seen += 1
            seen_paths.add(str(path))
            summary.indexed += self._upsert_video(path, root)
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
                summary = self._scan_path_sync(settings, row["path"], row["action"])
                total.seen += summary.seen
                total.indexed += summary.indexed
                total.thumbnails += summary.thumbnails
                total.errors += summary.errors
                total.deleted += summary.deleted
                total.skipped += summary.skipped
                with self.db.connect() as conn:
                    conn.execute(
                        "UPDATE scan_queue SET status = 'done', updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                        (row["id"],),
                    )
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
        video_type = detect_video_type(path, width, height)

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

    def rebuild_video_thumbnail(self, video_id: int, video_type: str) -> None:
        with self.db.connect() as conn:
            row = conn.execute("SELECT path, duration_seconds FROM videos WHERE id = ?", (video_id,)).fetchone()
            if row is None:
                return
        self._generate_and_record_thumbnail(Path(row["path"]), video_id, video_type, row["duration_seconds"])

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

    def _refresh_metadata(self, path: Path, video_id: int) -> None:
        try:
            probe = probe_video(path)
        except (VideoToolError, OSError):
            return
        video_type = detect_video_type(path, probe.width, probe.height)
        with self.db.connect() as conn:
            conn.execute(
                """
                UPDATE videos
                SET duration_seconds = ?,
                    width = ?,
                    height = ?,
                    aspect_ratio = ?,
                    type = CASE WHEN ? = 'panorama' THEN 'panorama' ELSE type END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    probe.duration_seconds,
                    probe.width,
                    probe.height,
                    calculate_aspect_ratio(probe.width, probe.height),
                    video_type,
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


def calculate_aspect_ratio(width: int | None, height: int | None) -> float | None:
    if not width or not height:
        return None
    return round(width / height, 4)


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
