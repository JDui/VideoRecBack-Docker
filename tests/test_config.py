from pathlib import Path

from app.config import Settings, load_settings, normalize_extensions, save_settings


def test_normalize_extensions_adds_dot_and_deduplicates():
    assert normalize_extensions("mp4, .mkv, MP4") == [".mp4", ".mkv"]


def test_settings_round_trip(tmp_path: Path):
    settings = Settings(
        site_title="家庭视频",
        video_root="/media/archive",
        scan_interval_hours=3,
        default_volume_percent=35,
        show_date=False,
        show_size=True,
        show_duration=False,
        video_extensions=["mp4", ".webm"],
    )
    save_settings(tmp_path, settings)

    loaded = load_settings(tmp_path)

    assert loaded.site_title == "家庭视频"
    assert loaded.video_root == "/media/archive"
    assert loaded.scan_interval_hours == 3
    assert loaded.default_volume_percent == 35
    assert loaded.show_date is False
    assert loaded.show_size is True
    assert loaded.show_duration is False
    assert loaded.video_extensions == [".mp4", ".webm"]


def test_default_volume_is_clamped(tmp_path: Path):
    settings = Settings(default_volume_percent=130)
    save_settings(tmp_path, settings)

    loaded = load_settings(tmp_path)

    assert loaded.default_volume_percent == 100


def test_default_scan_interval_is_low_frequency(tmp_path: Path):
    settings = load_settings(tmp_path)

    assert settings.scan_interval_hours == 150
