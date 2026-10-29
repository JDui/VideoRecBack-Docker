from __future__ import annotations

import hashlib
import mimetypes
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.thumbnails import VideoToolError
from app.transcode_color import TranscodeColor, transcode_color


def get_transcode_color(source: Path) -> TranscodeColor:
    try:
        return transcode_color(source)
    except (VideoToolError, OSError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def build_range_response(request: Request, path: Path, media_type: str | None = None) -> StreamingResponse:
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video file is missing")

    media_type = media_type or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    file_size = path.stat().st_size
    range_header = request.headers.get("range")
    if not range_header:
        headers = {"Accept-Ranges": "bytes", "Content-Length": str(file_size)}
        return StreamingResponse(iter_file(path, 0, file_size - 1), media_type=media_type, headers=headers)

    start, end = parse_range(range_header, file_size)
    length = end - start + 1
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Content-Length": str(length),
    }
    return StreamingResponse(
        iter_file(path, start, end),
        media_type=media_type,
        headers=headers,
        status_code=status.HTTP_206_PARTIAL_CONTENT,
    )


def parse_range(header: str, file_size: int) -> tuple[int, int]:
    unit, _, range_value = header.partition("=")
    if unit.strip().lower() != "bytes" or not range_value:
        raise HTTPException(status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE)

    start_text, _, end_text = range_value.partition("-")
    if not start_text and not end_text:
        raise HTTPException(status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE)

    try:
        if start_text:
            start = int(start_text)
            end = int(end_text) if end_text else file_size - 1
        else:
            suffix = int(end_text)
            start = max(0, file_size - suffix)
            end = file_size - 1
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE) from exc

    if start >= file_size or end < start:
        raise HTTPException(status_code=status.HTTP_416_REQUESTED_RANGE_NOT_SATISFIABLE)
    return start, min(end, file_size - 1)


def iter_file(path: Path, start: int, end: int):
    with path.open("rb") as handle:
        handle.seek(start)
        remaining = end - start + 1
        while remaining > 0:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


@dataclass(frozen=True, slots=True)
class StreamQuality:
    height: int | None
    width: int | None
    bitrate: str
    audio_bitrate: str
    segment_seconds: int = 2


@dataclass(frozen=True, slots=True)
class EncoderProfile:
    codec: str
    preset: str | None = None
    hardware_device: str | None = None


STREAM_QUALITIES = {
    "ultra": StreamQuality(height=None, width=None, bitrate="8M", audio_bitrate="192k"),
    "low": StreamQuality(height=1080, width=1920, bitrate="3M", audio_bitrate="160k"),
    "high": StreamQuality(height=720, width=1280, bitrate="1M", audio_bitrate="96k"),
}
ENCODER_PROFILES = {
    "h264_qsv": EncoderProfile(codec="h264_qsv", preset="veryfast"),
    "h264_vaapi": EncoderProfile(codec="h264_vaapi", preset=None, hardware_device="/dev/dri/renderD128"),
    "libx264_ultrafast": EncoderProfile(codec="libx264", preset="ultrafast"),
    "libx264_veryfast": EncoderProfile(codec="libx264", preset="veryfast"),
}
HLS_READY_TIMEOUT_SECONDS = 75.0
HLS_IDLE_TIMEOUT_SECONDS = 90.0


@dataclass(slots=True)
class HlsJob:
    process: subprocess.Popen
    last_seen: float


_HLS_JOBS: dict[str, HlsJob] = {}
_HLS_JOBS_LOCK = threading.Lock()
_HLS_WATCHDOG_STARTED = False
_STREAM_CACHE_LOCKS = [threading.Lock() for _ in range(32)]


def resolve_stream_path(video, data_dir: Path, quality: str) -> Path:
    if quality == "original":
        return Path(video["path"])
    if quality not in STREAM_QUALITIES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid stream quality")

    source = Path(video["path"])
    if not source.exists() or not source.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video file is missing")

    output = stream_cache_path(video, source, data_dir, quality)
    lock_index = int(hashlib.sha1(str(output).encode()).hexdigest(), 16) % len(_STREAM_CACHE_LOCKS)
    lock = _STREAM_CACHE_LOCKS[lock_index]
    with lock:
        if output.exists() and output.stat().st_size > 0:
            return output
        output.parent.mkdir(parents=True, exist_ok=True)
        temp_output = output.with_suffix(".tmp.mp4")
        temp_output.unlink(missing_ok=True)
        try:
            generate_stream_cache(source, temp_output, STREAM_QUALITIES[quality])
            temp_output.replace(output)
        except (OSError, subprocess.TimeoutExpired) as exc:
            temp_output.unlink(missing_ok=True)
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc
    return output


def stream_cache_path(video, source: Path, data_dir: Path, quality: str) -> Path:
    stat = source.stat()
    key = "|".join(
        [
            str(video["id"]),
            str(source),
            str(stat.st_mtime_ns),
            str(stat.st_size),
            quality,
            "stream-v3-hdr",
        ]
    )
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return data_dir / "cache" / "streams" / f"stream-{video['id']}-{quality}-{digest}.mp4"


def resolve_hls_playlist(
    video,
    data_dir: Path,
    quality: str,
    start_ms: int = 0,
    encoder: str = "libx264_ultrafast",
    max_cache_mb: int = 4096,
    timeout: float = HLS_READY_TIMEOUT_SECONDS,
) -> Path:
    if quality not in STREAM_QUALITIES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid stream quality")

    source = Path(video["path"])
    if not source.exists() or not source.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video file is missing")

    start_ms = max(0, int(start_ms))
    clean_encoder = normalize_encoder(encoder)
    color = get_transcode_color(source)
    output_dir = hls_cache_dir(video, source, data_dir, quality, start_ms, clean_encoder)
    playlist = output_dir / "index.m3u8"
    if hls_playlist_ready(playlist):
        key = str(output_dir)
        if hls_playlist_complete(playlist) or hls_job_running(key):
            touch_hls_job(key)
            return playback_playlist(playlist, color)
        cleanup_hls_dir(output_dir)

    output_dir.mkdir(parents=True, exist_ok=True)
    cleanup_stream_cache(data_dir / "cache", max_cache_mb)
    stop_related_hls_jobs(video["id"], quality, except_key=str(output_dir))
    start_hls_transcode(source, output_dir, STREAM_QUALITIES[quality], start_ms / 1000, clean_encoder, color=color)
    return playback_playlist(wait_for_hls_playlist(playlist, output_dir, timeout), color)


def resolve_hls_segment(
    video,
    data_dir: Path,
    quality: str,
    start_ms: int,
    segment: str,
    encoder: str = "libx264_ultrafast",
) -> Path:
    if quality not in STREAM_QUALITIES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid stream quality")
    if segment not in {"init.mp4", "stream.m3u8"} and not re.fullmatch(r"segment-\d+\.(ts|m4s)", segment):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid HLS segment")
    source = Path(video["path"])
    if not source.exists() or not source.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video file is missing")
    filename = "index.m3u8" if segment == "stream.m3u8" else segment
    path = hls_cache_dir(video, source, data_dir, quality, max(0, int(start_ms)), normalize_encoder(encoder)) / filename
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="HLS segment is not ready")
    touch_hls_job(str(path.parent))
    return path


def hls_cache_dir(
    video,
    source: Path,
    data_dir: Path,
    quality: str,
    start_ms: int,
    encoder: str = "libx264_ultrafast",
) -> Path:
    stat = source.stat()
    key = "|".join(
        [
            str(video["id"]),
            str(source),
            str(stat.st_mtime_ns),
            str(stat.st_size),
            quality,
            normalize_encoder(encoder),
            str(max(0, int(start_ms))),
            "hls-v4-hdr",
        ]
    )
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return data_dir / "cache" / "streams" / f"hls-{video['id']}-{quality}-{max(0, int(start_ms))}-{digest}"


def start_hls_transcode(
    source: Path,
    output_dir: Path,
    quality: StreamQuality,
    start_at: float,
    encoder: str = "libx264_ultrafast",
    *, color: TranscodeColor | None = None,
) -> None:
    key = str(output_dir)
    ensure_hls_watchdog()
    with _HLS_JOBS_LOCK:
        job = _HLS_JOBS.get(key)
        if job and job.process.poll() is None:
            job.last_seen = time.time()
            return

        cleanup_hls_dir(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        color = color or get_transcode_color(source)
        command = build_hls_command(source, output_dir, quality, start_at, encoder, color=color)
        process = subprocess.Popen(
            command,
            cwd=output_dir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _HLS_JOBS[key] = HlsJob(process, time.time())


def build_hls_command(
    source: Path,
    output_dir: Path,
    quality: StreamQuality,
    start_at: float,
    encoder: str = "libx264_ultrafast",
    *, color: TranscodeColor = TranscodeColor(),
) -> list[str]:
    source, output_dir = source.resolve(), output_dir.resolve()
    profile = ENCODER_PROFILES[normalize_encoder(encoder)]
    # HDR always uses the verified CPU Main10 path; H.264 hardware profiles are SDR only.
    if color.hdr:
        profile = EncoderProfile("libx265", "ultrafast" if "ultrafast" in encoder else "veryfast")
    video_filter = (
        f"scale=w='min({quality.width},iw)':h='min({quality.height},ih)':"
        "force_original_aspect_ratio=decrease:force_divisible_by=2"
        if quality.width and quality.height
        else "scale=w='trunc(iw/2)*2':h='trunc(ih/2)*2'"
    )
    if color.hdr:
        # Drop dynamic metadata before resizing/re-encoding; validated static metadata
        # is explicitly restored through x265. Never attach old DV RPU to new pixels.
        video_filter += color.scale_options() + ",sidedata=mode=delete"
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{max(0, start_at):.3f}",
    ]
    if profile.hardware_device and profile.codec.endswith("_vaapi"):
        command.extend(["-vaapi_device", profile.hardware_device])
        video_filter = f"{video_filter},format=nv12,hwupload"
    command.extend([
        "-i",
        str(source),
    ])
    command.extend([
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        video_filter,
        "-c:v",
        profile.codec,
    ])
    if profile.preset:
        command.extend(["-preset", profile.preset])
    command.extend([
        "-b:v",
        quality.bitrate,
        "-maxrate",
        quality.bitrate,
        "-bufsize",
        f"{int(quality.bitrate.rstrip('M')) * 2}M",
        "-pix_fmt",
        "yuv420p10le" if color.hdr else "yuv420p",
        "-g",
        str(quality.segment_seconds * 30),
        "-sc_threshold",
        "0",
        "-c:a",
        "aac",
        "-b:a",
        quality.audio_bitrate,
        "-f",
        "hls",
        "-hls_time",
        str(quality.segment_seconds),
        "-hls_playlist_type",
        "event",
        "-hls_list_size",
        "0",
        "-hls_flags",
        "independent_segments+temp_file",
        "-hls_segment_filename",
        str(output_dir / ("segment-%05d.m4s" if color.hdr else "segment-%05d.ts")),
        str(output_dir / "index.m3u8"),
    ])
    if color.hdr:
        command[-1:-1] = [
            "-profile:v", "main10", *color.ffmpeg_args(),
            "-x265-params", color.x265_params(),
            "-hls_segment_type", "fmp4", "-hls_fmp4_init_filename", "init.mp4",
            "-master_pl_name", "master-source.m3u8",
        ]
    return command


def playback_playlist(playlist: Path, color: TranscodeColor) -> Path:
    if not color.hdr:
        return playlist
    # FFmpeg supplies the actual codec string/level and bandwidth. Add HDR signalling
    # and use a distinct route for the media playlist to avoid recursive master loads.
    master = playlist.parent / "master-source.m3u8"
    if not master.exists():
        raise HTTPException(status_code=503, detail="HDR stream is still preparing")
    lines = master.read_text(encoding="utf-8").splitlines()
    if 'index.m3u8' not in lines or not any('CODECS="hvc1.' in line for line in lines):
        raise HTTPException(status_code=503, detail="HDR stream metadata is still preparing")
    lines = [line + f',VIDEO-RANGE={color.video_range}' if line.startswith('#EXT-X-STREAM-INF:')
             else 'stream.m3u8' if line == 'index.m3u8' else line for line in lines]
    target = playlist.parent / "hdr.m3u8"
    # The contents are immutable for this source/quality; avoid racing file rewrites.
    with _HLS_JOBS_LOCK:
        if not target.exists():
            target.write_text('\n'.join(lines) + '\n', encoding="utf-8")
    return target


def wait_for_hls_playlist(playlist: Path, output_dir: Path, timeout: float) -> Path:
    deadline = time.time() + max(0.1, timeout)
    key = str(output_dir)
    while time.time() < deadline:
        if hls_playlist_ready(playlist):
            if hls_playlist_complete(playlist) or hls_job_running(key):
                touch_hls_job(key)
                return playlist
        with _HLS_JOBS_LOCK:
            job = _HLS_JOBS.get(key)
            exited = job is not None and job.process.poll() is not None
        if exited and not hls_playlist_ready(playlist):
            cleanup_hls_dir(output_dir)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="HLS transcode failed before producing a playable segment",
            )
        if exited and hls_playlist_ready(playlist) and not hls_playlist_complete(playlist):
            cleanup_hls_dir(output_dir)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="HLS transcode stopped before finishing the playlist",
            )
        time.sleep(0.2)
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="HLS stream is still preparing",
    )


def normalize_encoder(encoder: str) -> str:
    return encoder if encoder in ENCODER_PROFILES else "libx264_ultrafast"


def hls_job_key(video, data_dir: Path, quality: str, start_ms: int, encoder: str) -> str:
    source = Path(video["path"])
    if not source.exists() or not source.is_file():
        return ""
    return str(hls_cache_dir(video, source, data_dir, quality, max(0, int(start_ms)), normalize_encoder(encoder)))


def record_hls_heartbeat(video, data_dir: Path, quality: str, start_ms: int, encoder: str = "libx264_ultrafast") -> None:
    key = hls_job_key(video, data_dir, quality, start_ms, encoder)
    if not key:
        return
    touch_hls_job(key)


def touch_hls_job(key: str) -> None:
    with _HLS_JOBS_LOCK:
        job = _HLS_JOBS.get(key)
        if job and job.process.poll() is None:
            job.last_seen = time.time()


def hls_job_running(key: str) -> bool:
    with _HLS_JOBS_LOCK:
        job = _HLS_JOBS.get(key)
        return bool(job and job.process.poll() is None)


def stop_hls_transcode(video, data_dir: Path, quality: str, start_ms: int, encoder: str = "libx264_ultrafast") -> None:
    key = hls_job_key(video, data_dir, quality, start_ms, encoder)
    if key:
        stop_hls_job(key)


def stop_hls_job(key: str) -> None:
    with _HLS_JOBS_LOCK:
        job = _HLS_JOBS.pop(key, None)
    path = Path(key)
    if not job:
        return
    if job.process.poll() is not None:
        playlist = path / "index.m3u8"
        if not hls_playlist_complete(playlist):
            cleanup_hls_dir(path)
        return
    job.process.terminate()
    try:
        job.process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        job.process.kill()
    if not hls_playlist_complete(path / "index.m3u8"):
        cleanup_hls_dir(path)


def stop_related_hls_jobs(video_id: object, quality: str, except_key: str = "") -> None:
    prefix = f"hls-{video_id}-{quality}-"
    with _HLS_JOBS_LOCK:
        keys = [
            key
            for key, job in _HLS_JOBS.items()
            if key != except_key and Path(key).name.startswith(prefix) and job.process.poll() is None
        ]
    for key in keys:
        stop_hls_job(key)


def cleanup_hls_dir(path: Path) -> None:
    if not path.exists() or not path.is_dir():
        return
    for child in path.glob("*"):
        if child.is_file():
            child.unlink(missing_ok=True)
    try:
        path.rmdir()
    except OSError:
        pass


def ensure_hls_watchdog() -> None:
    global _HLS_WATCHDOG_STARTED
    if _HLS_WATCHDOG_STARTED:
        return
    _HLS_WATCHDOG_STARTED = True
    thread = threading.Thread(target=hls_watchdog_loop, name="hls-watchdog", daemon=True)
    thread.start()


def hls_watchdog_loop() -> None:
    while True:
        now = time.time()
        stale_keys: list[str] = []
        with _HLS_JOBS_LOCK:
            for key, job in list(_HLS_JOBS.items()):
                if job.process.poll() is not None:
                    stale_keys.append(key)
                elif now - job.last_seen > HLS_IDLE_TIMEOUT_SECONDS:
                    stale_keys.append(key)
        for key in stale_keys:
            stop_hls_job(key)
        time.sleep(1.0)


def hls_playlist_ready(playlist: Path) -> bool:
    if not playlist.exists() or playlist.stat().st_size == 0:
        return False
    try:
        lines = playlist.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return False
    for line in lines:
        if line.startswith('#EXT-X-MAP:'):
            match = re.search(r'URI="([^"]+)"', line)
            if not match or match.group(1) != "init.mp4":
                return False
            init = playlist.parent / "init.mp4"
            if not init.exists() or init.stat().st_size == 0:
                return False
    for line in lines:
        if not re.fullmatch(r"segment-\d+\.(ts|m4s)", line):
            continue
        segment = playlist.parent / line
        if segment.exists() and segment.stat().st_size > 0:
            return True
    return False


def hls_playlist_complete(playlist: Path) -> bool:
    if not playlist.exists() or playlist.stat().st_size == 0:
        return False
    try:
        return "#EXT-X-ENDLIST" in playlist.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


def generate_stream_cache(source: Path, output: Path, quality: StreamQuality) -> None:
    color = get_transcode_color(source)
    video_filter = (
        f"scale=w='min({quality.width},iw)':h='min({quality.height},ih)':"
        "force_original_aspect_ratio=decrease:force_divisible_by=2"
        if quality.width and quality.height
        else "scale=w='trunc(iw/2)*2':h='trunc(ih/2)*2'"
    )
    if color.hdr:
        video_filter += color.scale_options() + ",sidedata=mode=delete"
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        video_filter,
        "-c:v",
        "libx265" if color.hdr else "libx264",
        "-preset",
        "veryfast",
        "-b:v",
        quality.bitrate,
        "-maxrate",
        quality.bitrate,
        "-bufsize",
        f"{int(quality.bitrate.rstrip('M')) * 2}M",
        "-pix_fmt",
        "yuv420p10le" if color.hdr else "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        quality.audio_bitrate,
        "-movflags",
        "+faststart",
        str(output),
    ]
    if color.hdr:
        command[-1:-1] = ["-profile:v", "main10", *color.ffmpeg_args(), "-x265-params",
                          color.x265_params()]
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        output.unlink(missing_ok=True)
        raise OSError(result.stderr.strip() or "ffmpeg stream transcode failed")
    if not output.exists() or output.stat().st_size == 0:
        raise OSError("ffmpeg did not create a stream cache file")


def cleanup_stream_cache(cache_dir: Path, max_cache_mb: int = 4096, now: float | None = None, force: bool = False) -> int:
    del now, force
    stream_dir = cache_dir / "streams"
    max_bytes = max(256, int(max_cache_mb)) * 1024 * 1024
    entries: list[tuple[float, int, Path]] = []
    total_size = 0
    with _HLS_JOBS_LOCK:
        active_paths = {Path(key).resolve(strict=False) for key, job in _HLS_JOBS.items() if job.process.poll() is None}
    if stream_dir.exists():
        for path in stream_dir.glob("stream-*.mp4"):
            if not path.is_file():
                continue
            size = path.stat().st_size
            total_size += size
            entries.append((path.stat().st_mtime, size, path))
        for path in stream_dir.glob("hls-*"):
            if not path.is_dir():
                continue
            resolved = path.resolve(strict=False)
            if resolved in active_paths:
                continue
            size = sum(child.stat().st_size for child in path.glob("*") if child.is_file())
            total_size += size
            entries.append((path.stat().st_mtime, size, path))

    deleted = 0
    for _mtime, size, path in sorted(entries, key=lambda item: item[0]):
        if total_size <= max_bytes:
            break
        if path.is_dir():
            for child in path.glob("*"):
                if child.is_file():
                    child.unlink(missing_ok=True)
            try:
                path.rmdir()
            except OSError:
                continue
        else:
            path.unlink(missing_ok=True)
        total_size -= size
        deleted += 1

    cache_dir.mkdir(parents=True, exist_ok=True)
    return deleted
