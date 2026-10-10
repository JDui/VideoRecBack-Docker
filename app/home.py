from __future__ import annotations

from datetime import datetime

from app.db import Database


def memory_date_values(now: datetime | None = None) -> tuple[str, float]:
    today = now or datetime.now()
    return today.strftime("%m-%d"), datetime(today.year, 1, 1).timestamp()


def random_videos(db: Database, exclude: list[int] | None = None, selected: list[int] | None = None):
    previous = list(dict.fromkeys(exclude or []))[:3]
    preferred = list(dict.fromkeys(selected or []))[:3]
    with db.connect() as conn:
        if preferred:
            ordering = " ".join(f"WHEN ? THEN {index}" for index in range(len(preferred)))
            return conn.execute(
                f"""
                SELECT * FROM videos WHERE missing = 0 AND exclude_random = 0
                ORDER BY CASE id {ordering} ELSE 3 END, RANDOM() LIMIT 3
                """,
                preferred,
            ).fetchall()
        if not previous:
            return conn.execute(
                "SELECT * FROM videos WHERE missing = 0 AND exclude_random = 0 ORDER BY RANDOM() LIMIT 3"
            ).fetchall()
        placeholders = ",".join("?" for _ in previous)
        return conn.execute(
            f"""
            SELECT * FROM videos WHERE missing = 0 AND exclude_random = 0
            ORDER BY (id IN ({placeholders})), RANDOM() LIMIT 3
            """,
            previous,
        ).fetchall()


def library_summary(db: Database) -> dict[str, int]:
    with db.connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS total,
                COALESCE(SUM(type = 'panorama'), 0) AS panorama,
                COALESCE(SUM(favorite = 1), 0) AS favorites,
                COALESCE(SUM(thumb_status = 'ready'), 0) AS ready,
                COALESCE(SUM(thumb_status = 'error'), 0) AS errors,
                COALESCE(SUM(thumb_status NOT IN ('ready', 'error')), 0) AS pending,
                COALESCE(SUM(size_bytes), 0) AS size_bytes
            FROM videos WHERE missing = 0
            """
        ).fetchone()
    return dict(row)


def home_content(db: Database, now: datetime | None = None) -> dict:
    today = now or datetime.now()
    month_day, year_start = memory_date_values(today)
    with db.connect() as conn:
        recent = conn.execute(
            "SELECT * FROM videos WHERE missing = 0 ORDER BY mtime DESC, id DESC LIMIT 6"
        ).fetchall()
        favorites = conn.execute(
            """
            SELECT * FROM videos WHERE missing = 0 AND favorite = 1
            ORDER BY mtime DESC, id DESC LIMIT 3
            """
        ).fetchall()
        memories = conn.execute(
            """
            SELECT * FROM videos WHERE missing = 0 AND exclude_memories = 0 AND mtime < ?
                AND strftime('%m-%d', mtime, 'unixepoch', 'localtime') = ?
            ORDER BY mtime DESC, id DESC LIMIT 2
            """,
            (year_start, month_day),
        ).fetchall()
    return {
        "random": random_videos(db),
        "recent": recent,
        "favorites": favorites,
        "memories": memories,
        "summary": library_summary(db),
        "today": today.strftime("%Y年%m月%d日"),
        "today_short": today.strftime("%m月%d日"),
        "weekday": "星期" + "一二三四五六日"[today.weekday()],
    }
