from __future__ import annotations

import hashlib
import mimetypes
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, Request, status
from fastapi.responses import StreamingResponse


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
    height: int
    width: int
    bitrate: str
    audio_bitrate: str
    segment_seconds: int = 2


STREAM_QUALITIES = {
    "low": StreamQuality(height=1080, width=1920, bitrate="5M", audio_bitrate="160k"),
    "high": StreamQuality(height=720, width=1280, bitrate="1M", audio_bitrate="96k"),
}
HLS_READY_TIMEOUT_SECONDS = 45.0
_HLS_JOBS: dict[str, subprocess.Popen] = {}
_HLS_JOBS_LOCK = threading.Lock()


def resolve_stream_path(video, data_dir: Path, quality: str) -> Path:
    if quality == "original":
        return Path(video["path"])
    if quality not in STREAM_QUALITIES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid stream quality")

    source = Path(video["path"])
    if not source.exists() or not source.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video file is missing")

    output = stream_cache_path(video, source, data_dir, quality)
    if output.exists() and output.stat().st_size > 0:
        return output

    output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output.with_suffix(".tmp.mp4")
    temp_output.unlink(missing_ok=True)
    try:
        generate_stream_cache(source, temp_output, STREAM_QUALITIES[quality])
        temp_output.replace(output)
    except OSError as exc:
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
            "stream-v1",
        ]
    )
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return data_dir / "cache" / "streams" / f"stream-{video['id']}-{quality}-{digest}.mp4"


def resolve_hls_playlist(
    video,
    data_dir: Path,
    quality: str,
    start_ms: int = 0,
    timeout: float = HLS_READY_TIMEOUT_SECONDS,
) -> Path:
    if quality not in STREAM_QUALITIES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid stream quality")

    source = Path(video["path"])
    if not source.exists() or not source.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video file is missing")

    start_ms = max(0, int(start_ms))
    output_dir = hls_cache_dir(video, source, data_dir, quality, start_ms)
    playlist = output_dir / "index.m3u8"
    if hls_playlist_ready(playlist):
        return playlist

    output_dir.mkdir(parents=True, exist_ok=True)
    start_hls_transcode(source, output_dir, STREAM_QUALITIES[quality], start_ms / 1000)
    return wait_for_hls_playlist(playlist, output_dir, timeout)


def resolve_hls_segment(video, data_dir: Path, quality: str, start_ms: int, segment: str) -> Path:
    if quality not in STREAM_QUALITIES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid stream quality")
    if not segment.startswith("segment-") or not segment.endswith(".ts"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid HLS segment")
    source = Path(video["path"])
    if not source.exists() or not source.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video file is missing")
    path = hls_cache_dir(video, source, data_dir, quality, max(0, int(start_ms))) / segment
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="HLS segment is not ready")
    return path


def hls_cache_dir(video, source: Path, data_dir: Path, quality: str, start_ms: int) -> Path:
    stat = source.stat()
    key = "|".join(
        [
            str(video["id"]),
            str(source),
            str(stat.st_mtime_ns),
            str(stat.st_size),
            quality,
            str(max(0, int(start_ms))),
            "hls-v1",
        ]
    )
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return data_dir / "cache" / "streams" / f"hls-{video['id']}-{quality}-{max(0, int(start_ms))}-{digest}"


def start_hls_transcode(source: Path, output_dir: Path, quality: StreamQuality, start_at: float) -> None:
    key = str(output_dir)
    with _HLS_JOBS_LOCK:
        process = _HLS_JOBS.get(key)
        if process and process.poll() is None:
            return

        for stale in output_dir.glob("segment-*.ts"):
            stale.unlink(missing_ok=True)
        (output_dir / "index.m3u8").unlink(missing_ok=True)

        command = build_hls_command(source, output_dir, quality, start_at)
        _HLS_JOBS[key] = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def build_hls_command(source: Path, output_dir: Path, quality: StreamQuality, start_at: float) -> list[str]:
    return [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{max(0, start_at):.3f}",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        (
            f"scale=w='min({quality.width},iw)':h='min({quality.height},ih)':"
            "force_original_aspect_ratio=decrease:force_divisible_by=2"
        ),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-b:v",
        quality.bitrate,
        "-maxrate",
        quality.bitrate,
        "-bufsize",
        f"{int(quality.bitrate.rstrip('M')) * 2}M",
        "-pix_fmt",
        "yuv420p",
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
        str(output_dir / "segment-%05d.ts"),
        str(output_dir / "index.m3u8"),
    ]


def wait_for_hls_playlist(playlist: Path, output_dir: Path, timeout: float) -> Path:
    deadline = time.time() + max(0.1, timeout)
    key = str(output_dir)
    while time.time() < deadline:
        if hls_playlist_ready(playlist):
            return playlist
        with _HLS_JOBS_LOCK:
            process = _HLS_JOBS.get(key)
            exited = process is not None and process.poll() is not None
        if exited and not hls_playlist_ready(playlist):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="HLS transcode failed before producing a playable segment",
            )
        time.sleep(0.2)
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="HLS stream is still preparing",
    )


def hls_playlist_ready(playlist: Path) -> bool:
    if not playlist.exists() or playlist.stat().st_size == 0:
        return False
    try:
        lines = playlist.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return False
    for line in lines:
        if not line or line.startswith("#") or not line.endswith(".ts"):
            continue
        segment = playlist.parent / line
        if segment.exists() and segment.stat().st_size > 0:
            return True
    return False


def generate_stream_cache(source: Path, output: Path, quality: StreamQuality) -> None:
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
        (
            f"scale=w='min({quality.width},iw)':h='min({quality.height},ih)':"
            "force_original_aspect_ratio=decrease:force_divisible_by=2"
        ),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-b:v",
        quality.bitrate,
        "-maxrate",
        quality.bitrate,
        "-bufsize",
        f"{int(quality.bitrate.rstrip('M')) * 2}M",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        quality.audio_bitrate,
        "-movflags",
        "+faststart",
        str(output),
    ]
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=3600)
    if result.returncode != 0:
        output.unlink(missing_ok=True)
        raise OSError(result.stderr.strip() or "ffmpeg stream transcode failed")
    if not output.exists() or output.stat().st_size == 0:
        raise OSError("ffmpeg did not create a stream cache file")


def cleanup_stream_cache(cache_dir: Path, retention_days: int, now: float | None = None, force: bool = False) -> int:
    now = time.time() if now is None else now
    retention_days = max(1, int(retention_days))
    interval_seconds = retention_days * 86400
    marker = cache_dir / ".stream-cache-cleanup"
    if not force and marker.exists() and now - marker.stat().st_mtime < interval_seconds:
        return 0

    stream_dir = cache_dir / "streams"
    deleted = 0
    cutoff = now - interval_seconds
    if stream_dir.exists():
        for path in stream_dir.glob("stream-*.mp4"):
            if not path.is_file() or path.stat().st_mtime >= cutoff:
                continue
            path.unlink(missing_ok=True)
            deleted += 1
        for path in stream_dir.glob("hls-*"):
            if not path.is_dir() or path.stat().st_mtime >= cutoff:
                continue
            for child in path.glob("*"):
                if child.is_file():
                    child.unlink(missing_ok=True)
            try:
                path.rmdir()
            except OSError:
                continue
            deleted += 1

    cache_dir.mkdir(parents=True, exist_ok=True)
    marker.touch()
    return deleted
