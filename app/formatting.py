from __future__ import annotations

from datetime import datetime


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "未知"
    total = max(0, int(round(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_size(size: int | None) -> str:
    if not size:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    index = 0
    while value >= 1024 and index < len(units) - 1:
        value /= 1024
        index += 1
    if index == 0:
        return f"{int(value)} {units[index]}"
    return f"{value:.1f} {units[index]}"


def format_bitrate(bits_per_second: int | None) -> str:
    if not bits_per_second:
        return "未知"
    units = ["bps", "Kbps", "Mbps", "Gbps"]
    value = float(bits_per_second)
    index = 0
    while value >= 1000 and index < len(units) - 1:
        value /= 1000
        index += 1
    if index == 0:
        return f"{int(value)} {units[index]}"
    return f"{value:.1f} {units[index]}"


def format_date(timestamp: float | None) -> str:
    if not timestamp:
        return "未知日期"
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
