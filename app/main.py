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
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import (
    Settings,
    clamp_days,
    clamp_percent,
    load_settings,
    normalize_extensions,
    normalize_ignore_patterns,
    normalize_hls_cache_max_mb,
    normalize_hls_encoder,
    normalize_intranet_host,
    normalize_intranet_port,
    normalize_intranet_redirect_protocol,
    normalize_quality,
    normalize_thumbnail_resolution,
    save_settings,
)
from app.db import Database
from app.formatting import format_bitrate, format_date, format_duration, format_size
from app.media import (
    build_range_response,
    record_hls_heartbeat,
    resolve_hls_playlist,
    resolve_hls_segment,
    resolve_stream_path,
    stop_hls_transcode,
)
from app.scanner import Scanner, calculate_aspect_ratio, is_tenbit_depth
from app.thumbnails import VideoToolError, generate_preview_thumbnail, probe_video


BASE_DIR = Path(__file__).resolve().parent
LOGGER = logging.getLogger(__name__)
TIMELINE_MIN_YEAR = 2010
TIMELINE_PAGE_SIZE = 180
INTRANET_HEALTH_GIF = bytes.fromhex("47494638396101000100800000000000ffffff21f90401000000002c00000000010001000002024401003b")
INTRANET_HEALTH_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Allow-Private-Network": "true",
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}


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
    app.state.media_task = None
    app.state.preview_thumbnail_lock = asyncio.Lock()
    app.state.manual_scan_pending = False
    app.state.thumbnail_refresh_pending = False
    app.state.metadata_recheck_pending = False
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
    templates = Jinja2Templates(directory=BASE_DIR / "templates")
    templates.env.filters["duration"] = format_duration
    templates.env.filters["size"] = format_size
    templates.env.filters["date"] = format_date
    templates.env.filters["bitrate"] = format_bitrate

    @app.on_event("startup")
    async def startup() -> None:
        sync_settings_to_db(db, load_settings(config_dir))
        app.state.maintenance_task = asyncio.create_task(background_maintenance(app))
        app.state.media_task = asyncio.create_task(background_media_worker(app))

    @app.on_event("shutdown")
    async def shutdown() -> None:
        for task in (app.state.maintenance_task, app.state.media_task):
            if task:
                task.cancel()

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request):
        settings = load_settings(config_dir)
        filters = read_filters(request)
        all_rows = query_videos(db, filters)
        rows = all_rows
        timeline_has_more = False
        timeline_next_cursor = None
        if filters["view"] == "timeline" and len(all_rows) > TIMELINE_PAGE_SIZE:
            rows = all_rows[:TIMELINE_PAGE_SIZE]
            timeline_has_more = True
            timeline_next_cursor = timeline_cursor(rows[-1])
        stats = summarize_videos(all_rows)
        timeline_groups = build_timeline_groups(rows)
        timeline_rail = build_timeline_rail(all_rows)
        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "settings": settings,
                "videos": rows,
                "filters": filters,
                "stats": stats,
                "view_urls": build_view_urls(filters),
                "timeline_groups": timeline_groups,
                "timeline_rail": timeline_rail,
                "timeline_cache": build_timeline_cache(timeline_groups, timeline_rail, filters),
                "timeline_has_more": timeline_has_more,
                "timeline_next_cursor": timeline_next_cursor,
                "timeline_batch_url": build_timeline_batch_url(filters),
                "folder_browser": build_folder_browser(all_rows, filters),
                "calendar_model": build_calendar_model(all_rows, filters),
                "scan_running": is_scan_running(app),
            },
        )

    @app.get("/timeline-batch")
    async def timeline_batch(request: Request):
        settings = load_settings(config_dir)
        filters = read_filters(request)
        filters["view"] = "timeline"
        try:
            cursor = (
                float(request.query_params["cursor_mtime"]),
                int(request.query_params["cursor_id"]),
            )
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Invalid timeline cursor")
        rows = query_videos(db, filters, cursor=cursor, limit=TIMELINE_PAGE_SIZE + 1)
        has_more = len(rows) > TIMELINE_PAGE_SIZE
        page_rows = rows[:TIMELINE_PAGE_SIZE]
        html = templates.get_template("_timeline_batch.html").render(
            settings=settings,
            timeline_groups=build_timeline_groups(page_rows),
        )
        return {
            "html": html,
            "has_more": has_more,
            "next_cursor": timeline_cursor(page_rows[-1]) if has_more and page_rows else None,
        }

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request):
        return templates.TemplateResponse(
            request,
            "settings.html",
            {
                "settings": load_settings(config_dir),
                "thumbnail_refresh": request.query_params.get("thumbnail_refresh"),
                "panorama_recheck": request.query_params.get("panorama_recheck"),
                "metadata_recheck": request.query_params.get("metadata_recheck"),
            },
        )

    @app.post("/settings")
    async def update_settings(
        site_title: str = Form(...),
        video_root: str = Form(...),
        scan_interval_hours: int = Form(...),
        default_volume_percent: int = Form(...),
        default_flat_quality: str = Form("original"),
        default_panorama_quality: str = Form("original"),
        thumbnail_resolution: int = Form(576),
        flat_hls_encoder: str = Form("libx264_ultrafast"),
        panorama_hls_encoder: str = Form("libx264_ultrafast"),
        hls_cache_max_mb: int = Form(4096),
        stream_cache_retention_days: int = Form(7),
        show_date: str | None = Form(None),
        show_size: str | None = Form(None),
        show_duration: str | None = Form(None),
        video_extensions: str = Form(...),
        ignore_dotfiles: str | None = Form(None),
        ignore_name_patterns: str = Form(""),
        intranet_keepalive_enabled: str | None = Form(None),
        intranet_redirect_host: str = Form(""),
        intranet_redirect_port: str = Form(""),
        intranet_redirect_protocol: str = Form("http"),
    ):
        settings = Settings(
            site_title=site_title.strip() or "视频归档",
            video_root=video_root.strip() or "/media",
            scan_interval_hours=int(scan_interval_hours),
            default_volume_percent=clamp_percent(default_volume_percent),
            default_flat_quality=normalize_quality(default_flat_quality),
            default_panorama_quality=normalize_quality(default_panorama_quality),
            thumbnail_resolution=normalize_thumbnail_resolution(thumbnail_resolution),
            flat_hls_encoder=normalize_hls_encoder(flat_hls_encoder),
            panorama_hls_encoder=normalize_hls_encoder(panorama_hls_encoder),
            hls_cache_max_mb=normalize_hls_cache_max_mb(hls_cache_max_mb),
            stream_cache_retention_days=clamp_days(stream_cache_retention_days),
            show_date=show_date == "on",
            show_size=show_size == "on",
            show_duration=show_duration == "on",
            video_extensions=normalize_extensions(video_extensions),
            ignore_dotfiles=ignore_dotfiles == "on",
            ignore_name_patterns=normalize_ignore_patterns(ignore_name_patterns),
            intranet_keepalive_enabled=intranet_keepalive_enabled == "on",
            intranet_redirect_host=normalize_intranet_host(intranet_redirect_host),
            intranet_redirect_port=normalize_intranet_port(intranet_redirect_port),
            intranet_redirect_protocol=normalize_intranet_redirect_protocol(intranet_redirect_protocol),
        )
        save_settings(config_dir, settings)
        sync_settings_to_db(db, settings)
        return RedirectResponse("/", status_code=303)

    @app.post("/settings/refresh-thumbnails")
    async def refresh_thumbnails():
        if is_background_busy(app):
            return RedirectResponse("/settings?thumbnail_refresh=running", status_code=303)
        settings = load_settings(config_dir)
        app.state.thumbnail_refresh_pending = True
        asyncio.create_task(run_thumbnail_refresh(app, settings))
        with db.connect() as conn:
            count = conn.execute("SELECT COUNT(*) AS count FROM videos WHERE missing = 0").fetchone()["count"]
        return RedirectResponse(f"/settings?thumbnail_refresh={count}", status_code=303)

    @app.post("/settings/recheck-panorama-types")
    async def recheck_panorama_types():
        changed = await asyncio.to_thread(scanner.recheck_panorama_types)
        return RedirectResponse(f"/settings?panorama_recheck={changed}", status_code=303)

    @app.post("/settings/recheck-all-video-data")
    async def recheck_all_video_data():
        if is_background_busy(app):
            return RedirectResponse("/settings?metadata_recheck=running", status_code=303)
        app.state.metadata_recheck_pending = True
        asyncio.create_task(run_metadata_recheck(app))
        with db.connect() as conn:
            count = conn.execute("SELECT COUNT(*) AS count FROM videos WHERE missing = 0").fetchone()["count"]
        return RedirectResponse(f"/settings?metadata_recheck={count}", status_code=303)

    @app.get("/settings/connectivity-test/ping")
    async def connectivity_ping():
        return {"ok": True, "server_time": time.time()}

    @app.get("/settings/connectivity-test/download")
    async def connectivity_download(size: int = 2 * 1024 * 1024):
        del size
        total = 2 * 1024 * 1024
        chunk = b"0" * (256 * 1024)

        def generate():
            remaining = total
            while remaining > 0:
                next_chunk = chunk[: min(len(chunk), remaining)]
                remaining -= len(next_chunk)
                yield next_chunk

        return StreamingResponse(
            generate(),
            media_type="application/octet-stream",
            headers={
                "Cache-Control": "no-store",
                "Content-Length": str(total),
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.options("/intranet/health")
    async def intranet_health_options():
        return Response(status_code=204, headers=INTRANET_HEALTH_HEADERS)

    @app.get("/intranet/health")
    async def intranet_health():
        return JSONResponse({"ok": True, "service": "videorecback"}, headers=INTRANET_HEALTH_HEADERS)

    @app.get("/intranet/health.gif")
    async def intranet_health_gif():
        return Response(INTRANET_HEALTH_GIF, media_type="image/gif", headers=INTRANET_HEALTH_HEADERS)

    @app.post("/scan")
    async def trigger_scan():
        if (
            is_scan_running(app)
            or bool(getattr(app.state, "thumbnail_refresh_pending", False))
            or bool(getattr(app.state, "metadata_recheck_pending", False))
        ):
            return RedirectResponse("/?scan=running", status_code=303)
        settings = load_settings(config_dir)
        app.state.manual_scan_pending = True
        asyncio.create_task(run_manual_scan(app, settings))
        return RedirectResponse("/?scan=running", status_code=303)

    @app.get("/scan/status")
    async def scan_status():
        pending_media = scanner.pending_media_jobs()
        indexing = bool(getattr(app.state, "manual_scan_pending", False) or scanner.is_running)
        processing_media = bool(scanner.is_processing_media or pending_media > 0)
        return {
            "scanning": indexing or processing_media,
            "indexing": indexing,
            "processing_media": processing_media,
            "pending_media": pending_media,
        }

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

    @app.post("/video/{video_id}/favorite")
    async def update_video_favorite(video_id: int, favorite: int = Form(...)):
        try:
            next_value = 1 if int(favorite) else 0
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid favorite value") from exc
        with db.connect() as conn:
            result = conn.execute(
                """
                UPDATE videos
                SET favorite = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (next_value, video_id),
            )
            if result.rowcount == 0:
                raise HTTPException(status_code=404, detail="Video not found")
        return {"ok": True, "favorite": bool(next_value)}

    @app.get("/video/{video_id}/play", response_class=HTMLResponse)
    async def play_page(request: Request, video_id: int):
        video = await asyncio.to_thread(ensure_tenbit_status, db, video_id)
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
        video = await asyncio.to_thread(ensure_tenbit_status, db, video_id)
        stream_path = await asyncio.to_thread(resolve_stream_path, video, data_dir, quality)
        media_type = "video/mp4" if quality != "original" else None
        return build_range_response(request, stream_path, media_type=media_type)

    @app.get("/media/{video_id}/hls/{quality}/{start_ms}/index.m3u8")
    async def hls_playlist(video_id: int, quality: str, start_ms: int):
        video = await asyncio.to_thread(ensure_tenbit_status, db, video_id)
        settings = load_settings(config_dir)
        playlist = await asyncio.to_thread(
            resolve_hls_playlist,
            video,
            data_dir,
            quality,
            start_ms,
            hls_encoder_for_video(settings, video),
            settings.hls_cache_max_mb,
        )
        return FileResponse(
            playlist,
            media_type="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "no-cache"},
        )

    @app.get("/media/{video_id}/hls/{quality}/{start_ms}/{segment}")
    async def hls_segment(video_id: int, quality: str, start_ms: int, segment: str):
        video = get_video(db, video_id)
        settings = load_settings(config_dir)
        path = await asyncio.to_thread(
            resolve_hls_segment,
            video,
            data_dir,
            quality,
            start_ms,
            segment,
            hls_encoder_for_video(settings, video),
        )
        return FileResponse(path, media_type="video/mp2t", headers={"Cache-Control": "public, max-age=86400"})

    @app.post("/media/{video_id}/hls/{quality}/{start_ms}/heartbeat")
    async def hls_heartbeat(video_id: int, quality: str, start_ms: int):
        video = get_video(db, video_id)
        settings = load_settings(config_dir)
        await asyncio.to_thread(record_hls_heartbeat, video, data_dir, quality, start_ms, hls_encoder_for_video(settings, video))
        return {"ok": True}

    @app.post("/media/{video_id}/hls/{quality}/{start_ms}/stop")
    async def hls_stop(video_id: int, quality: str, start_ms: int):
        video = get_video(db, video_id)
        settings = load_settings(config_dir)
        await asyncio.to_thread(stop_hls_transcode, video, data_dir, quality, start_ms, hls_encoder_for_video(settings, video))
        return {"ok": True}

    @app.get("/thumb/{video_id}.webp")
    async def thumb(video_id: int, preview: int = 0):
        video = get_video(db, video_id)
        thumb_path = video["thumb_path"]
        if video["thumb_status"] != "ready" or not thumb_path or not Path(thumb_path).exists():
            raise HTTPException(status_code=404, detail="Thumbnail is not ready")
        selected_path = Path(thumb_path)
        if preview:
            preview_path = data_dir / "cache" / f"{video_id}-preview.webp"
            if not preview_path.exists():
                async with app.state.preview_thumbnail_lock:
                    if not preview_path.exists():
                        try:
                            await asyncio.to_thread(generate_preview_thumbnail, selected_path, preview_path)
                        except OSError:
                            LOGGER.exception("Preview thumbnail generation failed for video %s.", video_id)
            if preview_path.exists():
                selected_path = preview_path
        return FileResponse(
            selected_path,
            media_type="image/webp",
            headers={"Cache-Control": "public, max-age=31536000, immutable"},
        )

    return app


async def background_maintenance(app: FastAPI) -> None:
    auto_scan_disabled_logged = False
    while True:
        settings = load_settings(app.state.config_dir)
        await app.state.scanner.process_queue(settings)
        if settings.scan_interval_hours <= 0:
            if not auto_scan_disabled_logged:
                LOGGER.info("Automatic full scan is disabled; interval=%s, no root scan will run.", settings.scan_interval_hours)
                auto_scan_disabled_logged = True
            await asyncio.sleep(300)
            continue

        auto_scan_disabled_logged = False
        remaining_sleep = settings.scan_interval_hours * 3600
        while remaining_sleep > 0:
            await asyncio.sleep(remaining_sleep)
            remaining_sleep = 0
            settings = load_settings(app.state.config_dir)
            if settings.scan_interval_hours <= 0:
                LOGGER.info("Automatic full scan was disabled before scheduled run; skipping root scan.")
                break
        if settings.scan_interval_hours <= 0:
            continue
        LOGGER.info("Running scheduled full scan for root %s.", settings.video_root)
        await app.state.scanner.scan(settings)


async def background_media_worker(app: FastAPI) -> None:
    while True:
        if getattr(app.state, "manual_scan_pending", False):
            await asyncio.sleep(0.25)
            continue
        settings = load_settings(app.state.config_dir)
        try:
            summary = await app.state.scanner.process_media_jobs(settings, limit=1)
        except Exception:
            LOGGER.exception("Background media job failed unexpectedly.")
            await asyncio.sleep(2.0)
            continue
        await asyncio.sleep(1.0 if summary.seen else 2.0)


def is_scan_running(app: FastAPI) -> bool:
    scanner = app.state.scanner
    return bool(
        getattr(app.state, "manual_scan_pending", False)
        or getattr(scanner, "is_running", False)
        or getattr(scanner, "is_processing_media", False)
        or scanner.pending_media_jobs() > 0
    )


def is_background_busy(app: FastAPI) -> bool:
    scanner = app.state.scanner
    return (
        is_scan_running(app)
        or bool(getattr(scanner, "is_processing_media", False))
        or scanner.pending_media_jobs() > 0
        or bool(getattr(app.state, "thumbnail_refresh_pending", False))
        or bool(getattr(app.state, "metadata_recheck_pending", False))
    )


async def run_manual_scan(app: FastAPI, settings: Settings) -> None:
    try:
        await app.state.scanner.scan(settings)
    except Exception:
        LOGGER.exception("Manual scan failed.")
    finally:
        app.state.manual_scan_pending = False


async def run_thumbnail_refresh(app: FastAPI, settings: Settings) -> None:
    try:
        await asyncio.to_thread(app.state.scanner.rebuild_all_thumbnails, settings)
    except Exception:
        LOGGER.exception("Thumbnail refresh failed.")
    finally:
        app.state.thumbnail_refresh_pending = False


async def run_metadata_recheck(app: FastAPI) -> None:
    try:
        await app.state.scanner.recheck_all_video_metadata()
    except Exception:
        LOGGER.exception("Video metadata recheck failed.")
    finally:
        app.state.metadata_recheck_pending = False


def sync_settings_to_db(db: Database, settings: Settings) -> None:
    db.sync_settings(
        {
            "site_title": settings.site_title,
            "video_root": settings.video_root,
            "scan_interval_hours": settings.scan_interval_hours,
            "default_volume_percent": settings.default_volume_percent,
            "default_flat_quality": settings.default_flat_quality,
            "default_panorama_quality": settings.default_panorama_quality,
            "thumbnail_resolution": settings.thumbnail_resolution,
            "flat_hls_encoder": settings.flat_hls_encoder,
            "panorama_hls_encoder": settings.panorama_hls_encoder,
            "hls_cache_max_mb": settings.hls_cache_max_mb,
            "stream_cache_retention_days": settings.stream_cache_retention_days,
            "show_date": int(settings.show_date),
            "show_size": int(settings.show_size),
            "show_duration": int(settings.show_duration),
            "video_extensions": ",".join(settings.video_extensions),
            "ignore_dotfiles": int(settings.ignore_dotfiles),
            "ignore_name_patterns": ",".join(settings.ignore_name_patterns),
            "intranet_keepalive_enabled": int(settings.intranet_keepalive_enabled),
            "intranet_redirect_host": settings.intranet_redirect_host,
            "intranet_redirect_port": settings.intranet_redirect_port,
            "intranet_redirect_protocol": settings.intranet_redirect_protocol,
        }
    )
    with db.connect() as conn:
        conn.execute("DELETE FROM app_settings WHERE key IN ('hls_encoder', 'intranet_probe_host')")


def hls_encoder_for_video(settings: Settings, video) -> str:
    try:
        video_type = video["type"]
    except (KeyError, TypeError):
        video_type = getattr(video, "type", "flat")
    if video_type == "panorama":
        return settings.panorama_hls_encoder
    return settings.flat_hls_encoder


def get_video(db: Database, video_id: int):
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Video not found")
    return row


def ensure_tenbit_status(db: Database, video_id: int):
    video = get_video(db, video_id)
    if video["is_10bit"] is not None:
        return video
    try:
        probe = probe_video(Path(video["path"]))
    except (VideoToolError, OSError):
        return video
    is_10bit = is_tenbit_depth(probe.bit_depth) or 0
    with db.connect() as conn:
        conn.execute(
            """
            UPDATE videos
            SET duration_seconds = COALESCE(?, duration_seconds),
                width = COALESCE(?, width),
                height = COALESCE(?, height),
                aspect_ratio = COALESCE(?, aspect_ratio),
                bit_depth = ?,
                is_10bit = ?,
                video_codec = COALESCE(?, video_codec),
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                probe.duration_seconds,
                probe.width,
                probe.height,
                calculate_aspect_ratio(probe.width, probe.height),
                probe.bit_depth,
                is_10bit,
                probe.codec_name,
                video_id,
            ),
        )
    return get_video(db, video_id)


def read_filters(request: Request) -> dict[str, str]:
    params = request.query_params
    view = (
        params.get("view", "timeline")
        if params.get("view", "timeline") in {"timeline", "folders", "calendar", "favorites"}
        else "timeline"
    )
    requested_zoom = params.get("calendar_zoom")
    calendar_zoom = requested_zoom if requested_zoom in {"year", "month", "day"} else "year"
    calendar_year = params.get("calendar_year", "").strip()
    calendar_month = params.get("calendar_month", "").strip()
    date_from = params.get("date_from", "").strip()
    date_to = params.get("date_to", "").strip()
    return {
        "view": view,
        "type": params.get("type", "all") if params.get("type", "all") in {"all", "flat", "panorama"} else "all",
        "duration": params.get("duration", "all") if params.get("duration", "all") in {"all", "short", "medium", "long"} else "all",
        "aspect": params.get("aspect", "all") if params.get("aspect", "all") in {"all", "wide", "vertical", "square"} else "all",
        "folder": params.get("folder", "").strip("/"),
        "calendar_zoom": calendar_zoom,
        "calendar_year": calendar_year if calendar_year.isdigit() and len(calendar_year) == 4 else "",
        "calendar_month": calendar_month if calendar_month.isdigit() and 1 <= int(calendar_month) <= 12 else "",
        "date_from": date_from if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_from) else "",
        "date_to": date_to if re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_to) else "",
        "q": params.get("q", "").strip(),
    }


def query_videos(
    db: Database,
    filters: dict[str, str],
    cursor: tuple[float, int] | None = None,
    limit: int | None = None,
):
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
    if filters["view"] == "favorites":
        clauses.append("favorite = 1")
    if filters["q"]:
        clauses.append("(name LIKE ? OR folder LIKE ? OR relative_path LIKE ?)")
        like = f"%{filters['q']}%"
        values.extend([like, like, like])
    if filters.get("date_from"):
        clauses.append("mtime >= ?")
        values.append(datetime.strptime(filters["date_from"], "%Y-%m-%d").timestamp())
    if filters.get("date_to"):
        clauses.append("mtime < ?")
        values.append((datetime.strptime(filters["date_to"], "%Y-%m-%d").timestamp() + 86400))
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
    if cursor is not None:
        clauses.append("(mtime < ? OR (mtime = ? AND id < ?))")
        values.extend([cursor[0], cursor[0], cursor[1]])

    sql = f"""
        SELECT *,
            strftime('timeline-%Y-%m-%d', mtime, 'unixepoch', 'localtime') AS timeline_day_anchor
        FROM videos
        WHERE {' AND '.join(clauses)}
        ORDER BY mtime DESC, id DESC
    """
    if limit is not None:
        sql += " LIMIT ?"
        values.append(max(1, int(limit)))
    with db.connect() as conn:
        return conn.execute(sql, values).fetchall()


def timeline_cursor(row) -> dict[str, int | float]:
    return {"mtime": float(row["mtime"]), "id": int(row["id"])}


def build_timeline_batch_url(filters: dict[str, str]) -> str:
    values = {
        key: value
        for key, value in filters.items()
        if value and value != "all" and key not in {"folder", "calendar_zoom", "calendar_year", "calendar_month"}
    }
    values["view"] = "timeline"
    return "/timeline-batch?" + urlencode(values)


def build_view_urls(filters: dict[str, str]) -> dict[str, str]:
    urls: dict[str, str] = {}
    for view in ("timeline", "folders", "calendar", "favorites"):
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


def timeline_group_config(rows) -> tuple[str, str, str, str]:
    return "month", "%Y-%m", "%Y年%m月", "timeline-%Y-%m"


def build_timeline_groups(rows):
    granularity, key_format, label_format, anchor_format = timeline_group_config(rows)

    groups: dict[str, dict[str, object]] = {}
    for row in rows:
        date = datetime.fromtimestamp(row["mtime"])
        key = date.strftime(key_format)
        label = date.strftime(label_format)
        day_key = date.strftime("%Y-%m-%d")
        entry = groups.setdefault(
            key,
            {
                "key": key,
                "label": label,
                "granularity": granularity,
                "year": date.year,
                "quarter": (date.month - 1) // 3 + 1,
                "month": date.month,
                "day": date.day,
                "anchor": date.strftime(anchor_format),
                "day_groups": {},
                "videos": [],
            },
        )
        entry["videos"].append(row)
        day_entry = entry["day_groups"].setdefault(
            day_key,
            {
                "key": day_key,
                "anchor": f"timeline-{date:%Y-%m-%d}",
                "label": date.strftime("%m月%d日"),
                "year": date.year,
                "month": date.month,
                "day": date.day,
                "videos": [],
            },
        )
        day_entry["videos"].append(row)
    result = list(groups.values())
    for group in result:
        group["day_groups"] = list(group["day_groups"].values())
    return result


def group_by_date(rows):
    return build_timeline_groups(rows)


def build_timeline_rail(rows) -> list[dict[str, object]]:
    day_counts: dict[tuple[int, int, int], int] = defaultdict(int)
    month_counts: dict[tuple[int, int], int] = defaultdict(int)
    year_counts: dict[int, int] = defaultdict(int)
    target_anchors: dict[tuple[str, tuple[int, ...]], tuple[float, str]] = {}

    def set_target(kind: str, key: tuple[int, ...], timestamp: float, date: datetime) -> None:
        target_key = (kind, key)
        if kind == "day":
            anchor = f"#timeline-{date:%Y-%m-%d}"
        elif kind == "month":
            anchor = f"#timeline-{date:%Y-%m}"
        else:
            anchor = f"#timeline-{date:%Y-%m}"
        if target_key not in target_anchors or timestamp > target_anchors[target_key][0]:
            target_anchors[target_key] = (timestamp, anchor)

    def target(kind: str, key: tuple[int, ...], fallback: str) -> str:
        return target_anchors.get((kind, key), (0, fallback))[1]

    for row in rows:
        date = datetime.fromtimestamp(row["mtime"])
        year = date.year if date.year >= TIMELINE_MIN_YEAR else TIMELINE_MIN_YEAR
        month = date.month if date.year >= TIMELINE_MIN_YEAR else 1
        day = date.day if date.year >= TIMELINE_MIN_YEAR else 1
        timestamp = float(row["mtime"])
        day_key = (year, month, day)
        month_key = (year, month)
        day_counts[day_key] += 1
        month_counts[month_key] += 1
        year_counts[year] += 1
        set_target("day", day_key, timestamp, date)
        set_target("month", month_key, timestamp, date)
        set_target("year", (year,), timestamp, date)

    if not year_counts:
        return []

    result: list[dict[str, object]] = []
    years = sorted(year_counts, reverse=True)
    for year in years:
        marks = [
            {
                "kind": "year",
                "year": year,
                "month": 1,
                "day": 1,
                "period": f"{year}",
                "label": f"{year}",
                "count": year_counts[year],
                "href": f"#timeline-{year}",
                "target": target("year", (year,), f"#timeline-{year}"),
            }
        ]
        for key in sorted((key for key in month_counts if key[0] == year), reverse=True):
            y, month = key
            marks.append(
                {
                    "kind": "month",
                    "year": y,
                    "month": month,
                    "day": 1,
                    "period": f"{y}-{month:02d}",
                    "label": f"{month}月",
                    "count": month_counts[key],
                    "href": f"#timeline-{y}-{month:02d}",
                    "target": target("month", key, f"#timeline-{y}-{month:02d}"),
                }
            )
        for key in sorted((key for key in day_counts if key[0] == year), reverse=True):
            y, month, day = key
            marks.append(
                {
                    "kind": "day",
                    "year": y,
                    "month": month,
                    "day": day,
                    "period": f"{y}-{month:02d}-{day:02d}",
                    "label": "2010前" if y == TIMELINE_MIN_YEAR and month == 1 and day == 1 else f"{month}/{day}",
                    "count": day_counts[key],
                    "href": f"#timeline-{y}-{month:02d}-{day:02d}",
                    "target": target("day", key, f"#timeline-{y}-{month:02d}-{day:02d}"),
                }
            )
        marks.sort(key=lambda item: (item["year"], item["month"], item["day"], {"day": 3, "month": 2, "year": 1}[item["kind"]]), reverse=True)
        result.append({"year": year, "marks": marks})
    return result


def build_timeline_cache(groups, rail, filters: dict[str, str]) -> dict[str, object]:
    del rail
    return {
        "filters": {
            "view": filters.get("view", "timeline"),
            "type": filters.get("type", "all"),
            "duration": filters.get("duration", "all"),
            "aspect": filters.get("aspect", "all"),
            "date_from": filters.get("date_from", ""),
            "date_to": filters.get("date_to", ""),
            "q": filters.get("q", ""),
        },
        "groups": [
            {
                "key": group["key"],
                "anchor": group["anchor"],
                "label": group["label"],
                "granularity": group["granularity"],
                "year": group["year"],
                "month": group["month"],
                "day": group["day"],
                "count": len(group["videos"]),
                "days": [
                    {
                        "anchor": day["anchor"],
                        "label": day["label"],
                        "year": day["year"],
                        "month": day["month"],
                        "day": day["day"],
                        "count": len(day["videos"]),
                    }
                    for day in group["day_groups"]
                ],
            }
            for group in groups
        ],
    }


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
