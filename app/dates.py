from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


@lru_cache(maxsize=16)
def _timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        logging.getLogger(__name__).warning("Invalid archive timezone %r; using Asia/Shanghai", name)
        return ZoneInfo("Asia/Shanghai")


def archive_timezone() -> ZoneInfo:
    return _timezone(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Asia/Shanghai")


def archive_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(archive_timezone())
    return now.replace(tzinfo=archive_timezone()) if now.tzinfo is None else now.astimezone(archive_timezone())


def archive_datetime(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, archive_timezone())


def archive_date(timestamp: float, pattern: str = "%Y-%m-%d") -> str:
    return archive_datetime(timestamp).strftime(pattern)


def date_boundary(value: str, *, next_day: bool = False) -> float:
    day = datetime.strptime(value, "%Y-%m-%d")
    if next_day:
        day += timedelta(days=1)
    return day.replace(tzinfo=archive_timezone()).timestamp()
