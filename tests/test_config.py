from pathlib import Path

from app.config import (
    Settings,
    clamp_days,
    load_settings,
    normalize_quality,
    normalize_extensions,
    normalize_ignore_patterns,
    save_settings,
)


def test_normalize_extensions_adds_dot_and_deduplicates():
    assert normalize_extensions("mp4, .mkv, MP4") == [".mp4", ".mkv"]


def test_normalize_ignore_patterns_splits_lines_commas_and_deduplicates():
    assert normalize_ignore_patterns("Thumbs.db\n*.tmp.mp4,Thumbs.db") == ["Thumbs.db", "*.tmp.mp4"]


def test_clamp_days_bounds_cache_retention():
    assert clamp_days("0") == 1
    assert clamp_days("999") == 365
    assert clamp_days("bad") == 7


def test_normalize_quality_accepts_known_values():
    assert normalize_quality("ultra") == "ultra"
    assert normalize_quality("bad") == "original"


def test_settings_round_trip(tmp_path: Path):
    settings = Settings(
        site_title="家庭视频",
        video_root="/media/archive",
        scan_interval_hours=3,
        default_volume_percent=35,
        default_quality="ultra",
        stream_cache_retention_days=9,
        show_date=False,
        show_size=True,
        show_duration=False,
        video_extensions=["mp4", ".webm"],
        ignore_dotfiles=False,
        ignore_name_patterns=["Thumbs.db", "*.tmp.mp4"],
    )
    save_settings(tmp_path, settings)

    loaded = load_settings(tmp_path)

    assert loaded.site_title == "家庭视频"
    assert loaded.video_root == "/media/archive"
    assert loaded.scan_interval_hours == 3
    assert loaded.default_volume_percent == 35
    assert loaded.default_quality == "ultra"
    assert loaded.stream_cache_retention_days == 9
    assert loaded.show_date is False
    assert loaded.show_size is True
    assert loaded.show_duration is False
    assert loaded.video_extensions == [".mp4", ".webm"]
    assert loaded.ignore_dotfiles is False
    assert loaded.ignore_name_patterns == ["Thumbs.db", "*.tmp.mp4"]


def test_default_volume_is_clamped(tmp_path: Path):
    settings = Settings(default_volume_percent=130)
    save_settings(tmp_path, settings)

    loaded = load_settings(tmp_path)

    assert loaded.default_volume_percent == 100


def test_default_scan_interval_is_low_frequency(tmp_path: Path):
    settings = load_settings(tmp_path)

    assert settings.scan_interval_hours == 150


def test_scan_interval_allows_zero(tmp_path: Path):
    save_settings(tmp_path, Settings(scan_interval_hours=0))

    loaded = load_settings(tmp_path)

    assert loaded.scan_interval_hours == 0
