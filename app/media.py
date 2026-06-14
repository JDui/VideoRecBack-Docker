from __future__ import annotations

import mimetypes
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
