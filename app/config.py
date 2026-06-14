from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_VIDEO_EXTENSIONS = [".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"]


@dataclass(slots=True)
class Settings:
    site_title: str = "视频归档"
    video_root: str = "/media"
    scan_interval_hours: int = 150
    default_volume_percent: int = 20
    show_date: bool = True
    show_size: bool = True
    show_duration: bool = True
    video_extensions: list[str] = field(default_factory=lambda: DEFAULT_VIDEO_EXTENSIONS.copy())


def config_path(config_dir: Path) -> Path:
    return config_dir / "settings.json"


def load_settings(config_dir: Path) -> Settings:
    path = config_path(config_dir)
    if not path.exists():
        settings = Settings()
        save_settings(config_dir, settings)
        return settings

    raw = json.loads(path.read_text(encoding="utf-8"))
    legacy_minutes = raw.get("scan_interval_minutes")
    interval_hours = raw.get("scan_interval_hours")
    if interval_hours is None and legacy_minutes is not None:
        interval_hours = max(1, round(int(legacy_minutes) / 60))
    if interval_hours is None:
        interval_hours = 150
    return Settings(
        site_title=str(raw.get("site_title") or "视频归档"),
        video_root=str(raw.get("video_root") or "/media"),
        scan_interval_hours=int(interval_hours),
        default_volume_percent=clamp_percent(raw.get("default_volume_percent", 20)),
        show_date=bool(raw.get("show_date", True)),
        show_size=bool(raw.get("show_size", True)),
        show_duration=bool(raw.get("show_duration", True)),
        video_extensions=normalize_extensions(raw.get("video_extensions")),
    )


def save_settings(config_dir: Path, settings: Settings) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = asdict(settings)
    payload["video_extensions"] = normalize_extensions(payload["video_extensions"])
    payload["default_volume_percent"] = clamp_percent(payload.get("default_volume_percent", 20))
    payload["scan_interval_hours"] = int(payload.get("scan_interval_hours", 150))
    config_path(config_dir).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def clamp_percent(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 20
    return max(0, min(100, number))


def normalize_extensions(value: Any) -> list[str]:
    if isinstance(value, str):
        candidates = [item.strip() for item in value.split(",")]
    elif isinstance(value, list):
        candidates = [str(item).strip() for item in value]
    else:
        candidates = DEFAULT_VIDEO_EXTENSIONS

    normalized: list[str] = []
    for item in candidates:
        if not item:
            continue
        ext = item.lower()
        if not ext.startswith("."):
            ext = f".{ext}"
        if ext not in normalized:
            normalized.append(ext)
    return normalized or DEFAULT_VIDEO_EXTENSIONS.copy()
