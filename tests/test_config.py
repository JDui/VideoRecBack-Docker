from pathlib import Path

from app.config import (
    Settings,
    clamp_days,
    load_settings,
    normalize_quality,
    normalize_hls_cache_max_mb,
    normalize_hls_encoder,
    normalize_intranet_host,
    normalize_intranet_port,
    normalize_thumbnail_resolution,
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


def test_normalize_thumbnail_resolution_accepts_known_values():
    assert normalize_thumbnail_resolution("720") == 720
    assert normalize_thumbnail_resolution("bad") == 576


def test_normalize_hls_encoder_and_cache_limit():
    assert normalize_hls_encoder("h264_qsv") == "h264_qsv"
    assert normalize_hls_encoder("bad") == "libx264_ultrafast"
    assert normalize_hls_cache_max_mb("128") == 256
    assert normalize_hls_cache_max_mb("8192") == 8192


def test_normalize_intranet_host_and_port():
    assert normalize_intranet_host("http://192.168.31.20:8080/path") == "192.168.31.20"
    assert normalize_intranet_host("https://nas.local") == "nas.local"
    assert normalize_intranet_port("8080") == "8080"
    assert normalize_intranet_port("70000") == ""
    assert normalize_intranet_port("bad") == ""


def test_settings_round_trip(tmp_path: Path):
    settings = Settings(
        site_title="家庭视频",
        video_root="/media/archive",
        scan_interval_hours=3,
        default_volume_percent=35,
        default_flat_quality="ultra",
        default_panorama_quality="low",
        thumbnail_resolution=720,
        flat_hls_encoder="h264_qsv",
        panorama_hls_encoder="libx264_veryfast",
        hls_cache_max_mb=8192,
        stream_cache_retention_days=9,
        show_date=False,
        show_size=True,
        show_duration=False,
        video_extensions=["mp4", ".webm"],
        ignore_dotfiles=False,
        ignore_name_patterns=["Thumbs.db", "*.tmp.mp4"],
        intranet_keepalive_enabled=True,
        intranet_probe_host="192.168.31.1",
        intranet_redirect_port="8080",
    )
    save_settings(tmp_path, settings)

    loaded = load_settings(tmp_path)

    assert loaded.site_title == "家庭视频"
    assert loaded.video_root == "/media/archive"
    assert loaded.scan_interval_hours == 3
    assert loaded.default_volume_percent == 35
    assert loaded.default_flat_quality == "ultra"
    assert loaded.default_panorama_quality == "low"
    assert loaded.thumbnail_resolution == 720
    assert loaded.flat_hls_encoder == "h264_qsv"
    assert loaded.panorama_hls_encoder == "libx264_veryfast"
    assert loaded.hls_cache_max_mb == 8192
    assert loaded.stream_cache_retention_days == 9
    assert loaded.show_date is False
    assert loaded.show_size is True
    assert loaded.show_duration is False
    assert loaded.video_extensions == [".mp4", ".webm"]
    assert loaded.ignore_dotfiles is False
    assert loaded.ignore_name_patterns == ["Thumbs.db", "*.tmp.mp4"]
    assert loaded.intranet_keepalive_enabled is True
    assert loaded.intranet_probe_host == "192.168.31.1"
    assert loaded.intranet_redirect_port == "8080"


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


def test_legacy_default_quality_migrates_to_split_defaults(tmp_path: Path):
    (tmp_path / "settings.json").write_text('{"default_quality":"ultra"}\n', encoding="utf-8")

    loaded = load_settings(tmp_path)

    assert loaded.default_flat_quality == "ultra"
    assert loaded.default_panorama_quality == "ultra"


def test_legacy_hls_encoder_migrates_to_split_defaults(tmp_path: Path):
    (tmp_path / "settings.json").write_text('{"hls_encoder":"h264_qsv"}\n', encoding="utf-8")

    loaded = load_settings(tmp_path)

    assert loaded.flat_hls_encoder == "h264_qsv"
    assert loaded.panorama_hls_encoder == "h264_qsv"
