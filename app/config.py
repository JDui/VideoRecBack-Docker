from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_VIDEO_EXTENSIONS = [".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v"]
DEFAULT_IGNORE_NAME_PATTERNS = ["Thumbs.db", "desktop.ini", "._*"]
QUALITY_OPTIONS = ("original", "ultra", "low", "high")
THUMBNAIL_RESOLUTION_OPTIONS = (480, 576, 720)


@dataclass(slots=True)
class Settings:
    site_title: str = "视频归档"
    video_root: str = "/media"
    scan_interval_hours: int = 150
    default_volume_percent: int = 20
    default_flat_quality: str = "original"
    default_panorama_quality: str = "original"
    thumbnail_resolution: int = 576
    stream_cache_retention_days: int = 7
    show_date: bool = True
    show_size: bool = True
    show_duration: bool = True
    video_extensions: list[str] = field(default_factory=lambda: DEFAULT_VIDEO_EXTENSIONS.copy())
    ignore_dotfiles: bool = True
    ignore_name_patterns: list[str] = field(default_factory=lambda: DEFAULT_IGNORE_NAME_PATTERNS.copy())


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
    legacy_quality = normalize_quality(raw.get("default_quality"))
    return Settings(
        site_title=str(raw.get("site_title") or "视频归档"),
        video_root=str(raw.get("video_root") or "/media"),
        scan_interval_hours=int(interval_hours),
        default_volume_percent=clamp_percent(raw.get("default_volume_percent", 20)),
        default_flat_quality=normalize_quality(raw.get("default_flat_quality", legacy_quality)),
        default_panorama_quality=normalize_quality(raw.get("default_panorama_quality", legacy_quality)),
        thumbnail_resolution=normalize_thumbnail_resolution(raw.get("thumbnail_resolution", 576)),
        stream_cache_retention_days=clamp_days(raw.get("stream_cache_retention_days", 7)),
        show_date=bool(raw.get("show_date", True)),
        show_size=bool(raw.get("show_size", True)),
        show_duration=bool(raw.get("show_duration", True)),
        video_extensions=normalize_extensions(raw.get("video_extensions")),
        ignore_dotfiles=bool(raw.get("ignore_dotfiles", True)),
        ignore_name_patterns=normalize_ignore_patterns(raw.get("ignore_name_patterns")),
    )


def save_settings(config_dir: Path, settings: Settings) -> None:
    config_dir.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = asdict(settings)
    payload["video_extensions"] = normalize_extensions(payload["video_extensions"])
    payload["ignore_name_patterns"] = normalize_ignore_patterns(payload.get("ignore_name_patterns"))
    payload["default_volume_percent"] = clamp_percent(payload.get("default_volume_percent", 20))
    legacy_quality = normalize_quality(payload.get("default_quality"))
    payload.pop("default_quality", None)
    payload["default_flat_quality"] = normalize_quality(payload.get("default_flat_quality", legacy_quality))
    payload["default_panorama_quality"] = normalize_quality(payload.get("default_panorama_quality", legacy_quality))
    payload["thumbnail_resolution"] = normalize_thumbnail_resolution(payload.get("thumbnail_resolution", 576))
    payload["stream_cache_retention_days"] = clamp_days(payload.get("stream_cache_retention_days", 7))
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


def clamp_days(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 7
    return max(1, min(365, number))


def normalize_quality(value: Any) -> str:
    text = str(value or "original").strip()
    return text if text in QUALITY_OPTIONS else "original"


def normalize_thumbnail_resolution(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = 576
    return number if number in THUMBNAIL_RESOLUTION_OPTIONS else 576


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


def normalize_ignore_patterns(value: Any) -> list[str]:
    if isinstance(value, str):
        candidates = [item.strip() for item in value.replace("\n", ",").split(",")]
    elif isinstance(value, list):
        candidates = [str(item).strip() for item in value]
    else:
        candidates = DEFAULT_IGNORE_NAME_PATTERNS

    normalized: list[str] = []
    for item in candidates:
        if item and item not in normalized:
            normalized.append(item)
    return normalized
