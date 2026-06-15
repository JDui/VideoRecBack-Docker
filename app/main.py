from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import (
    Settings,
    clamp_days,
    clamp_percent,
    load_settings,
    normalize_extensions,
    normalize_ignore_patterns,
    normalize_quality,
    save_settings,
)
from app.db import Database
from app.formatting import format_date, format_duration, format_size
from app.media import (
    build_range_response,
    cleanup_stream_cache,
    resolve_hls_playlist,
    resolve_hls_segment,
    resolve_stream_path,
)
from app.scanner import Scanner


BASE_DIR = Path(__file__).resolve().parent
LOGGER = logging.getLogger(__name__)
TIMELINE_MIN_YEAR = 2010


def create_app() -> FastAPI:
    config_dir = Path(os.getenv("APP_CONFIG_DIR", "/config"))
    data_dir = Path(os.getenv("APP_DATA_DIR", "/data"))
    db = Database(data_dir)
    db.init()
    scanner = Scanner(db, data_dir)

    app = FastAPI(title="VideoRecBack")
    app.state.config_dir = config_dir
    app.state.data_dir = data_dir
    app.state.db = db
    app.state.scanner = scanner
    app.state.maintenance_task = None
    app.state.manual_scan_pending = False
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=BASE_DIR / "templates")
    templates.env.filters["duration"] = format_duration
    templates.env.filters["size"] = format_size
    templates.env.filters["date"] = format_date

    @app.on_event("startup")
    async def startup() -> None:
        sync_settings_to_db(db, load_settings(config_dir))
        app.state.maintenance_task = asyncio.create_task(background_maintenance(app))

    @app.on_event("shutdown")
    async def shutdown() -> None:
        task = app.state.maintenance_task
        if task:
            task.cancel()

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        settings = load_settings(config_dir)
        filters = read_filters(request)
        rows = query_videos(db, filters)
        stats = summarize_videos(rows)
        timeline_labels = get_timeline_labels(db)
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "settings": settings,
                "videos": rows,
                "filters": filters,
                "stats": stats,
                "view_urls": build_view_urls(filters),
                "timeline_groups": group_by_date(rows),
                "timeline_rail": build_timeline_rail(rows, timeline_labels),
                "folder_browser": build_folder_browser(rows, filters),
                "calendar_model": build_calendar_model(rows, filters),
                "scan_running": is_scan_running(app),
            },
        )

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request):
        return templates.TemplateResponse(
            request,
            "settings.html",
            {
                "settings": load_settings(config_dir),
                "panorama_refresh": request.query_params.get("panorama_refresh"),
                "panorama_recheck": request.query_params.get("panorama_recheck"),
            },
        )

    @app.post("/settings")
    async def update_settings(
        site_title: str = Form(...),
        video_root: str = Form(...),
        scan_interval_hours: int = Form(...),
        default_volume_percent: int = Form(...),
        default_quality: str = Form("original"),
        stream_cache_retention_days: int = Form(...),
        show_date: str | None = Form(None),
        show_size: str | None = Form(None),
        show_duration: str | None = Form(None),
        video_extensions: str = Form(...),
        ignore_dotfiles: str | None = Form(None),
        ignore_name_patterns: str = Form(""),
    ):
        settings = Settings(
            site_title=site_title.strip() or "视频归档",
            video_root=video_root.strip() or "/media",
            scan_interval_hours=int(scan_interval_hours),
            default_volume_percent=clamp_percent(default_volume_percent),
            default_quality=normalize_quality(default_quality),
            stream_cache_retention_days=clamp_days(stream_cache_retention_days),
            show_date=show_date == "on",
            show_size=show_size == "on",
            show_duration=show_duration == "on",
            video_extensions=normalize_extensions(video_extensions),
            ignore_dotfiles=ignore_dotfiles == "on",
            ignore_name_patterns=normalize_ignore_patterns(ignore_name_patterns),
        )
        save_settings(config_dir, settings)
        sync_settings_to_db(db, settings)
        return RedirectResponse("/", status_code=303)

    @app.post("/settings/refresh-panorama-thumbnails")
    async def refresh_panorama_thumbnails():
        with db.connect() as conn:
            rows = conn.execute(
                """
                SELECT id
                FROM videos
                WHERE type = 'panorama' AND missing = 0
                ORDER BY id ASC
                """
            ).fetchall()
        for row in rows:
            await asyncio.to_thread(scanner.rebuild_video_thumbnail, int(row["id"]), "panorama")
        return RedirectResponse(f"/settings?panorama_refresh={len(rows)}", status_code=303)

    @app.post("/settings/recheck-panorama-types")
    async def recheck_panorama_types():
        changed = await asyncio.to_thread(scanner.recheck_panorama_types)
        return RedirectResponse(f"/settings?panorama_recheck={changed}", status_code=303)

    @app.post("/scan")
    async def trigger_scan():
        if is_scan_running(app):
            return RedirectResponse("/?scan=running", status_code=303)
        settings = load_settings(config_dir)
        app.state.manual_scan_pending = True
        asyncio.create_task(run_manual_scan(app, settings))
        return RedirectResponse("/?scan=running", status_code=303)

    @app.post("/scan-queue")
    async def queue_incremental_scan(
        path: str = Form(...),
        action: str = Form("upsert"),
    ):
        clean_path = path.strip()
        if not clean_path:
            raise HTTPException(status_code=400, detail="Path is required")
        if action not in {"upsert", "delete"}:
            raise HTTPException(status_code=400, detail="Invalid scan action")
        await scanner.enqueue(clean_path, action)
        summary = await scanner.process_queue(load_settings(config_dir), limit=25)
        return {
            "queued": clean_path,
            "action": action,
            "seen": summary.seen,
            "indexed": summary.indexed,
            "deleted": summary.deleted,
            "skipped": summary.skipped,
            "errors": summary.errors,
        }

    @app.post("/timeline-labels")
    async def create_timeline_label(
        year: int = Form(...),
        quarter: int = Form(...),
        label: str = Form(...),
        color: str = Form("#16a394"),
    ):
        clean_label = label.strip()[:80]
        if not clean_label or quarter not in {1, 2, 3, 4} or year < 1970 or year > 9999:
            raise HTTPException(status_code=400, detail="Invalid timeline label")
        clean_color = color.strip().lower()
        if not re.fullmatch(r"#[0-9a-f]{6}", clean_color):
            clean_color = "#16a394"
        with db.connect() as conn:
            conn.execute(
                "INSERT INTO timeline_labels(year, quarter, label, color) VALUES (?, ?, ?, ?)",
                (year, quarter, clean_label, clean_color),
            )
        return RedirectResponse(f"/#timeline-{year}-q{quarter}", status_code=303)

    @app.post("/timeline-labels/{label_id}")
    async def update_timeline_label(
        label_id: int,
        year: int = Form(...),
        quarter: int = Form(...),
        label: str = Form(...),
        color: str = Form("#16a394"),
    ):
        clean_label = label.strip()[:80]
        if not clean_label or quarter not in {1, 2, 3, 4} or year < 1970 or year > 9999:
            raise HTTPException(status_code=400, detail="Invalid timeline label")
        clean_color = color.strip().lower()
        if not re.fullmatch(r"#[0-9a-f]{6}", clean_color):
            clean_color = "#16a394"
        with db.connect() as conn:
            cursor = conn.execute(
                """
                UPDATE timeline_labels
                SET label = ?, color = ?
                WHERE id = ? AND year = ? AND quarter = ?
                """,
                (clean_label, clean_color, label_id, year, quarter),
            )
            updated = cursor.rowcount
        if updated == 0:
            raise HTTPException(status_code=404, detail="Timeline label not found")
        return RedirectResponse(f"/#timeline-{year}-q{quarter}", status_code=303)

    @app.post("/timeline-labels/{label_id}/delete")
    async def delete_timeline_label(
        label_id: int,
        year: int = Form(...),
        quarter: int = Form(...),
    ):
        if quarter not in {1, 2, 3, 4} or year < 1970 or year > 9999:
            raise HTTPException(status_code=400, detail="Invalid timeline label")
        with db.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM timeline_labels WHERE id = ? AND year = ? AND quarter = ?",
                (label_id, year, quarter),
            )
            deleted = cursor.rowcount
        if deleted == 0:
            raise HTTPException(status_code=404, detail="Timeline label not found")
        return RedirectResponse(f"/#timeline-{year}-q{quarter}", status_code=303)

    @app.get("/video/{video_id}", response_class=HTMLResponse)
    async def video_detail(request: Request, video_id: int):
        video = get_video(db, video_id)
        return templates.TemplateResponse(
            request,
            "detail.html",
            {"video": video, "settings": load_settings(config_dir)},
        )

    @app.post("/video/{video_id}/type")
    async def update_video_type(video_id: int, video_type: str = Form(...)):
        if video_type not in {"flat", "panorama"}:
            raise HTTPException(status_code=400, detail="Invalid video type")
        with db.connect() as conn:
            video = conn.execute("SELECT path, duration_seconds FROM videos WHERE id = ?", (video_id,)).fetchone()
            if video is None:
                raise HTTPException(status_code=404, detail="Video not found")
            conn.execute(
                """
                UPDATE videos
                SET type = ?, thumb_status = 'pending', thumb_version = 0, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (video_type, video_id),
            )
        LOGGER.info("Video %s type changed to %s; rebuilding only this thumbnail, no full scan.", video_id, video_type)
        await asyncio.to_thread(scanner.rebuild_video_thumbnail, video_id, video_type)
        return RedirectResponse(f"/video/{video_id}", status_code=303)

    @app.get("/video/{video_id}/play", response_class=HTMLResponse)
    async def play_page(request: Request, video_id: int):
        video = get_video(db, video_id)
        return templates.TemplateResponse(
            request,
            "play.html",
            {
                "video": video,
                "settings": load_settings(config_dir),
                "embed": request.query_params.get("embed") == "1",
            },
        )

    @app.get("/media/{video_id}")
    async def media(request: Request, video_id: int, quality: str = "original"):
        video = get_video(db, video_id)
        stream_path = await asyncio.to_thread(resolve_stream_path, video, data_dir, quality)
        media_type = "video/mp4" if quality != "original" else None
        return build_range_response(request, stream_path, media_type=media_type)

    @app.get("/media/{video_id}/hls/{quality}/{start_ms}/index.m3u8")
    async def hls_playlist(video_id: int, quality: str, start_ms: int):
        video = get_video(db, video_id)
        playlist = await asyncio.to_thread(resolve_hls_playlist, video, data_dir, quality, start_ms)
        return FileResponse(
            playlist,
            media_type="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/media/{video_id}/hls/{quality}/{start_ms}/{segment}")
    async def hls_segment(video_id: int, quality: str, start_ms: int, segment: str):
        video = get_video(db, video_id)
        path = await asyncio.to_thread(resolve_hls_segment, video, data_dir, quality, start_ms, segment)
        return FileResponse(path, media_type="video/mp2t", headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/thumb/{video_id}.webp")
    async def thumb(video_id: int):
        video = get_video(db, video_id)
        thumb_path = video["thumb_path"]
        if video["thumb_status"] != "ready" or not thumb_path or not Path(thumb_path).exists():
            raise HTTPException(status_code=404, detail="Thumbnail is not ready")
        return FileResponse(thumb_path, media_type="image/webp")

    return app


async def background_maintenance(app: FastAPI) -> None:
    auto_scan_disabled_logged = False
    while True:
        settings = load_settings(app.state.config_dir)
        await app.state.scanner.process_queue(settings)
        await asyncio.to_thread(
            cleanup_stream_cache,
            stream_cache_dir(app),
            settings.stream_cache_retention_days,
        )
        if settings.scan_interval_hours <= 0:
            if not auto_scan_disabled_logged:
                LOGGER.info("Automatic full scan is disabled; interval=%s, no root scan will run.", settings.scan_interval_hours)
                auto_scan_disabled_logged = True
            await asyncio.sleep(300)
            continue

        auto_scan_disabled_logged = False
        remaining_sleep = settings.scan_interval_hours * 3600
        cache_check_seconds = settings.stream_cache_retention_days * 86400
        while remaining_sleep > 0:
            sleep_seconds = min(remaining_sleep, cache_check_seconds)
            await asyncio.sleep(sleep_seconds)
            remaining_sleep -= sleep_seconds
            settings = load_settings(app.state.config_dir)
            await asyncio.to_thread(
                cleanup_stream_cache,
                stream_cache_dir(app),
                settings.stream_cache_retention_days,
            )
            if settings.scan_interval_hours <= 0:
                LOGGER.info("Automatic full scan was disabled before scheduled run; skipping root scan.")
                break
            if remaining_sleep > 0:
                await app.state.scanner.process_queue(settings)
        if settings.scan_interval_hours <= 0:
            continue
        LOGGER.info("Running scheduled full scan for root %s.", settings.video_root)
        await app.state.scanner.scan(settings)


def stream_cache_dir(app: FastAPI) -> Path:
    return Path(getattr(app.state, "data_dir", Path(app.state.config_dir).parent / "data")) / "cache"


def is_scan_running(app: FastAPI) -> bool:
    scanner = app.state.scanner
    return bool(getattr(app.state, "manual_scan_pending", False) or getattr(scanner, "is_running", False))


async def run_manual_scan(app: FastAPI, settings: Settings) -> None:
    try:
        await app.state.scanner.scan(settings)
    except Exception:
        LOGGER.exception("Manual scan failed.")
    finally:
        app.state.manual_scan_pending = False


def sync_settings_to_db(db: Database, settings: Settings) -> None:
    db.sync_settings(
        {
            "site_title": settings.site_title,
            "video_root": settings.video_root,
            "scan_interval_hours": settings.scan_interval_hours,
            "default_volume_percent": settings.default_volume_percent,
            "default_quality": settings.default_quality,
            "stream_cache_retention_days": settings.stream_cache_retention_days,
            "show_date": int(settings.show_date),
            "show_size": int(settings.show_size),
            "show_duration": int(settings.show_duration),
            "video_extensions": ",".join(settings.video_extensions),
            "ignore_dotfiles": int(settings.ignore_dotfiles),
            "ignore_name_patterns": ",".join(settings.ignore_name_patterns),
        }
    )


def get_video(db: Database, video_id: int):
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return row


def read_filters(request: Request) -> dict[str, str]:
    params = request.query_params
    view = params.get("view", "timeline") if params.get("view", "timeline") in {"timeline", "folders", "calendar"} else "timeline"
    requested_zoom = params.get("calendar_zoom")
    calendar_zoom = requested_zoom if requested_zoom in {"year", "month", "day"} else "year"
    calendar_year = params.get("calendar_year", "").strip()
    calendar_month = params.get("calendar_month", "").strip()
    return {
        "view": view,
        "type": params.get("type", "all") if params.get("type", "all") in {"all", "flat", "panorama"} else "all",
        "duration": params.get("duration", "all") if params.get("duration", "all") in {"all", "short", "medium", "long"} else "all",
        "aspect": params.get("aspect", "all") if params.get("aspect", "all") in {"all", "wide", "vertical", "square"} else "all",
        "folder": params.get("folder", "").strip("/"),
        "calendar_zoom": calendar_zoom,
        "calendar_year": calendar_year if calendar_year.isdigit() and len(calendar_year) == 4 else "",
        "calendar_month": calendar_month if calendar_month.isdigit() and 1 <= int(calendar_month) <= 12 else "",
        "q": params.get("q", "").strip(),
    }


def query_videos(db: Database, filters: dict[str, str]):
    clauses = ["missing = 0"]
    values: list[object] = []
    if filters["type"] != "all":
        clauses.append("type = ?")
        values.append(filters["type"])
    if filters["duration"] == "short":
        clauses.append("duration_seconds IS NOT NULL AND duration_seconds < 60")
    elif filters["duration"] == "medium":
        clauses.append("duration_seconds IS NOT NULL AND duration_seconds >= 60 AND duration_seconds < 600")
    elif filters["duration"] == "long":
        clauses.append("duration_seconds IS NOT NULL AND duration_seconds >= 600")
    if filters["aspect"] == "wide":
        clauses.append("aspect_ratio IS NOT NULL AND aspect_ratio >= 1.4")
    elif filters["aspect"] == "vertical":
        clauses.append("aspect_ratio IS NOT NULL AND aspect_ratio < 0.8")
    elif filters["aspect"] == "square":
        clauses.append("aspect_ratio IS NOT NULL AND aspect_ratio >= 0.8 AND aspect_ratio < 1.4")
    if filters["q"]:
        clauses.append("(name LIKE ? OR folder LIKE ? OR relative_path LIKE ?)")
        like = f"%{filters['q']}%"
        values.extend([like, like, like])
    if filters["view"] == "calendar" and filters["calendar_year"]:
        year_start = datetime(int(filters["calendar_year"]), 1, 1).timestamp()
        year_end = datetime(int(filters["calendar_year"]) + 1, 1, 1).timestamp()
        clauses.append("mtime >= ? AND mtime < ?")
        values.extend([year_start, year_end])
        if filters["calendar_month"]:
            year = int(filters["calendar_year"])
            month = int(filters["calendar_month"])
            next_year = year + 1 if month == 12 else year
            next_month = 1 if month == 12 else month + 1
            month_start = datetime(year, month, 1).timestamp()
            month_end = datetime(next_year, next_month, 1).timestamp()
            clauses.append("mtime >= ? AND mtime < ?")
            values.extend([month_start, month_end])

    sql = f"""
        SELECT *
        FROM videos
        WHERE {' AND '.join(clauses)}
        ORDER BY mtime DESC, id DESC
    """
    with db.connect() as conn:
        return conn.execute(sql, values).fetchall()


def build_view_urls(filters: dict[str, str]) -> dict[str, str]:
    urls: dict[str, str] = {}
    for view in ("timeline", "folders", "calendar"):
        next_filters = {
            key: value
            for key, value in filters.items()
            if value and value != "all" and key not in {"folder", "calendar_zoom", "calendar_year", "calendar_month"}
        }
        next_filters["view"] = view
        urls[view] = "/?" + urlencode(next_filters)
    return urls


def summarize_videos(rows) -> dict[str, int]:
    return {
        "total": len(rows),
        "flat": sum(1 for row in rows if row["type"] == "flat"),
        "panorama": sum(1 for row in rows if row["type"] == "panorama"),
    }


def group_by_date(rows):
    groups: dict[str, dict[str, object]] = {}
    for row in rows:
        date = datetime.fromtimestamp(row["mtime"])
        label = date.strftime("%Y年%m月%d日")
        entry = groups.setdefault(
            label,
            {
                "label": label,
                "year": date.year,
                "quarter": (date.month - 1) // 3 + 1,
                "month": date.month,
                "day": date.day,
                "anchor": f"timeline-{date:%Y-%m-%d}",
                "videos": [],
            },
        )
        entry["videos"].append(row)
    return list(groups.values())


def build_timeline_rail(rows, labels: dict[tuple[int, int], list[dict[str, object]]] | None = None) -> list[dict[str, object]]:
    labels = labels or {}
    day_counts: dict[tuple[int, int, int], int] = defaultdict(int)
    month_counts: dict[tuple[int, int], int] = defaultdict(int)
    quarters: dict[tuple[int, int], int] = defaultdict(int)
    halves: dict[tuple[int, int], int] = defaultdict(int)
    target_anchors: dict[tuple[str, tuple[int, ...]], tuple[float, str]] = {}

    def set_target(kind: str, key: tuple[int, ...], timestamp: float, date: datetime) -> None:
        target_key = (kind, key)
        anchor = f"#timeline-{date:%Y-%m-%d}"
        if target_key not in target_anchors or timestamp > target_anchors[target_key][0]:
            target_anchors[target_key] = (timestamp, anchor)

    def target(kind: str, key: tuple[int, ...], fallback: str) -> str:
        return target_anchors.get((kind, key), (0, fallback))[1]

    for row in rows:
        date = datetime.fromtimestamp(row["mtime"])
        display_date = date if date.year >= TIMELINE_MIN_YEAR else datetime(TIMELINE_MIN_YEAR, 1, 1)
        year = display_date.year
        month = display_date.month
        day = display_date.day
        display_quarter = (display_date.month - 1) // 3 + 1
        half = 1 if display_date.month <= 6 else 2
        timestamp = float(row["mtime"])
        day_key = (year, month, day)
        month_key = (year, month)
        quarter_key = (year, display_quarter)
        half_key = (year, half)
        day_counts[day_key] += 1
        month_counts[month_key] += 1
        quarters[quarter_key] += 1
        halves[half_key] += 1
        set_target("day", day_key, timestamp, date)
        set_target("month", month_key, timestamp, date)
        set_target("quarter", quarter_key, timestamp, date)
        set_target("half", half_key, timestamp, date)

    if not quarters:
        return []

    if len(day_counts) <= 48:
        granularity = "day"
    elif len(month_counts) <= 36:
        granularity = "month"
    elif len(quarters) <= 28:
        granularity = "quarter"
    else:
        granularity = "half"

    years = sorted({year for year, _quarter in quarters} | {year for year, _half in halves}, reverse=True)
    result = []
    for year in years:
        marks = []
        if granularity == "day":
            keys = sorted((key for key in day_counts if key[0] == year), reverse=True)
            for key in keys:
                y, month, day = key
                quarter = (month - 1) // 3 + 1
                marks.append(
                    {
                        "year": y,
                        "quarter": quarter,
                        "period": f"{y}-{month:02d}-{day:02d}",
                        "label": "2010前" if y == TIMELINE_MIN_YEAR and month == 1 and day == 1 else f"{month}/{day}",
                        "count": day_counts[key],
                        "labels": labels.get((y, quarter), []),
                        "href": f"#timeline-{y}-{month:02d}-{day:02d}",
                        "target": target("day", key, f"#timeline-{y}-{month:02d}-{day:02d}"),
                    }
                )
        elif granularity == "month":
            keys = sorted((key for key in month_counts if key[0] == year), reverse=True)
            for key in keys:
                y, month = key
                quarter = (month - 1) // 3 + 1
                marks.append(
                    {
                        "year": y,
                        "quarter": quarter,
                        "period": f"{y}-{month:02d}",
                        "label": f"{month}月",
                        "count": month_counts[key],
                        "labels": labels.get((y, quarter), []),
                        "href": f"#timeline-{y}-{month:02d}",
                        "target": target("month", key, f"#timeline-{y}-{month:02d}"),
                    }
                )
        elif granularity == "quarter":
            keys = sorted((key for key in quarters if key[0] == year), reverse=True)
            for y, quarter in keys:
                marks.append(
                    {
                        "year": y,
                        "quarter": quarter,
                        "period": f"{y}-q{quarter}",
                        "label": f"Q{quarter}",
                        "count": quarters[(y, quarter)],
                        "labels": labels.get((y, quarter), []),
                        "href": f"#timeline-{y}-q{quarter}",
                        "target": target("quarter", (y, quarter), f"#timeline-{y}-q{quarter}"),
                    }
                )
        else:
            keys = sorted((key for key in halves if key[0] == year), reverse=True)
            for y, half in keys:
                quarter = 3 if half == 2 else 1
                marks.append(
                    {
                        "year": y,
                        "quarter": quarter,
                        "period": f"{y}-h{half}",
                        "label": f"H{half}",
                        "count": halves[(y, half)],
                        "labels": [],
                        "href": f"#timeline-{y}-h{half}",
                        "target": target("half", (y, half), f"#timeline-{y}-h{half}"),
                    }
                )
        result.append({"year": year, "granularity": granularity, "marks": marks, "quarters": marks})
    return result


def get_timeline_labels(db: Database) -> dict[tuple[int, int], list[dict[str, object]]]:
    grouped: dict[tuple[int, int], list[dict[str, object]]] = defaultdict(list)
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT id, year, quarter, label, color FROM timeline_labels ORDER BY created_at ASC, id ASC"
        ).fetchall()
    for row in rows:
        grouped[(row["year"], row["quarter"])].append(
            {
                "id": row["id"],
                "label": row["label"],
                "color": row["color"],
            }
        )
    return grouped


def build_folder_browser(rows, filters: dict[str, str]) -> dict[str, object]:
    current = filters.get("folder", "").strip("/")
    current_prefix = f"{current}/" if current else ""
    child_dirs: dict[str, dict[str, object]] = {}
    files: list = []
    for row in rows:
        folder = (row["folder"] or "").strip("/")
        if folder == current:
            files.append(row)
            continue
        if current and not folder.startswith(current_prefix):
            continue
        remainder = folder[len(current_prefix):] if current_prefix else folder
        if not remainder:
            continue
        child_name = remainder.split("/", 1)[0]
        child_path = f"{current_prefix}{child_name}".strip("/")
        entry = child_dirs.setdefault(
            child_path,
            {
                "name": child_name,
                "path": child_path,
                "url": folder_url(filters, child_path),
                "count": 0,
                "cover": row,
            },
        )
        entry["count"] = int(entry["count"]) + 1

    breadcrumbs = [{"label": "全部文件", "url": folder_url(filters, "")}]
    parts = current.split("/") if current else []
    for index, part in enumerate(parts):
        path = "/".join(parts[: index + 1])
        breadcrumbs.append({"label": part, "url": folder_url(filters, path)})
    return {
        "current": current,
        "breadcrumbs": breadcrumbs,
        "folders": sorted(child_dirs.values(), key=lambda item: str(item["name"]).lower()),
        "files": files,
    }


def folder_url(filters: dict[str, str], folder: str) -> str:
    values = {
        key: value
        for key, value in filters.items()
        if value and value != "all" and key not in {"folder", "calendar_zoom", "calendar_year", "calendar_month"}
    }
    values["view"] = "folders"
    if folder:
        values["folder"] = folder
    return "/?" + urlencode(values)


def build_calendar_model(rows, filters: dict[str, str]) -> dict[str, object]:
    zoom = filters.get("calendar_zoom", "day")
    selected_year = filters.get("calendar_year", "")
    selected_month = filters.get("calendar_month", "")
    if zoom == "year":
        years: dict[str, list] = defaultdict(list)
        for row in rows:
            years[datetime.fromtimestamp(row["mtime"]).strftime("%Y")].append(row)
        return {
            "zoom": zoom,
            "zoom_urls": calendar_zoom_urls(filters),
            "selected_year": selected_year,
            "selected_month": selected_month,
            "groups": [
                {
                    "label": f"{year}年",
                    "count": len(videos),
                    "cover": videos[0],
                    "url": calendar_url(filters, "month", year=year),
                }
                for year, videos in years.items()
            ],
        }

    if zoom == "month":
        months: dict[str, list] = defaultdict(list)
        for row in rows:
            date = datetime.fromtimestamp(row["mtime"])
            months[date.strftime("%m")].append(row)
        return {
            "zoom": zoom,
            "zoom_urls": calendar_zoom_urls(filters),
            "selected_year": selected_year,
            "selected_month": selected_month,
            "groups": [
                {
                    "label": f"{int(month)}月",
                    "count": len(videos),
                    "cover": videos[0],
                    "url": calendar_url(filters, "day", year=selected_year, month=month),
                }
                for month, videos in months.items()
            ],
        }

    months: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for row in rows:
        date = datetime.fromtimestamp(row["mtime"])
        months[date.strftime("%Y年%m月")][date.strftime("%d日")].append(row)
    return {
        "zoom": zoom,
        "zoom_urls": calendar_zoom_urls(filters),
        "selected_year": selected_year,
        "selected_month": selected_month,
        "months": [
            {
                "label": month,
                "days": [{"label": day, "videos": videos} for day, videos in days.items()],
            }
            for month, days in months.items()
        ],
    }


def calendar_url(filters: dict[str, str], zoom: str, year: str | None = None, month: str | None = None) -> str:
    values = {
        key: value
        for key, value in filters.items()
        if value and value != "all" and key not in {"folder", "calendar_zoom", "calendar_year", "calendar_month"}
    }
    values["view"] = "calendar"
    values["calendar_zoom"] = zoom
    if year:
        values["calendar_year"] = year
    elif zoom in {"month", "day"} and filters.get("calendar_year"):
        values["calendar_year"] = filters["calendar_year"]
    if month:
        values["calendar_month"] = str(int(month))
    elif zoom == "day" and filters.get("calendar_month"):
        values["calendar_month"] = filters["calendar_month"]
    return "/?" + urlencode(values)


def calendar_zoom_urls(filters: dict[str, str]) -> dict[str, str]:
    urls = {"year": calendar_url(filters, "year")}
    urls["month"] = calendar_url(filters, "month") if filters.get("calendar_year") else "#"
    urls["day"] = calendar_url(filters, "day") if filters.get("calendar_year") and filters.get("calendar_month") else "#"
    return urls


app = create_app()
