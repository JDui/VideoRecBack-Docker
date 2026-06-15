import os
from pathlib import Path
from types import SimpleNamespace

from app.media import (
    STREAM_QUALITIES,
    build_hls_command,
    cleanup_stream_cache,
    generate_stream_cache,
    parse_range,
    resolve_hls_playlist,
    resolve_stream_path,
)


def test_parse_range_open_ended():
    assert parse_range("bytes=10-", 100) == (10, 99)


def test_parse_range_suffix():
    assert parse_range("bytes=-20", 100) == (80, 99)


def test_parse_range_clamps_end():
    assert parse_range("bytes=10-120", 100) == (10, 99)


def test_generate_stream_cache_uses_expected_low_quality_command(monkeypatch, tmp_path):
    source = tmp_path / "input.mp4"
    output = tmp_path / "output.mp4"
    source.write_bytes(b"source")
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        Path(command[-1]).write_bytes(b"cache")
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("app.media.subprocess.run", run)

    generate_stream_cache(source, output, quality=STREAM_QUALITIES["low"])

    command = commands[0]
    assert "-b:v" in command
    assert command[command.index("-b:v") + 1] == "3M"
    assert "-vf" in command
    assert "min(1920,iw)" in command[command.index("-vf") + 1]
    assert "min(1080,ih)" in command[command.index("-vf") + 1]
    assert output.exists()


def test_ultra_quality_preserves_source_resolution_without_cap(tmp_path):
    source = tmp_path / "input.mp4"
    output_dir = tmp_path / "hls"
    command = build_hls_command(source, output_dir, STREAM_QUALITIES["ultra"], 0)

    assert "-b:v" in command
    assert command[command.index("-b:v") + 1] == "8M"
    assert "-vf" in command
    assert "trunc(iw/2)*2" in command[command.index("-vf") + 1]
    assert "min(" not in command[command.index("-vf") + 1]


def test_resolve_stream_path_generates_high_quality_cache(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    source = tmp_path / "video.mp4"
    source.write_bytes(b"source")
    video = {"id": 7, "path": str(source)}

    def generate(source_path, output_path, quality):
        output_path.write_bytes(f"{quality.height}".encode())

    monkeypatch.setattr("app.media.generate_stream_cache", generate)

    output = resolve_stream_path(video, data_dir, "high")

    assert output.name.startswith("stream-7-high-")
    assert output.read_bytes() == b"720"


def test_build_hls_command_starts_at_requested_time(tmp_path):
    source = tmp_path / "input.mp4"
    output_dir = tmp_path / "hls"
    command = build_hls_command(source, output_dir, STREAM_QUALITIES["high"], 12.345)

    assert "-ss" in command
    assert command[command.index("-ss") + 1] == "12.345"
    assert "-f" in command
    assert command[command.index("-f") + 1] == "hls"
    assert "-hls_time" in command
    assert command[command.index("-hls_time") + 1] == "2"
    assert "-hls_playlist_type" in command
    assert command[command.index("-hls_playlist_type") + 1] == "event"
    assert "-hls_flags" in command
    assert "independent_segments" in command[command.index("-hls_flags") + 1]
    assert "-g" in command
    assert command[command.index("-g") + 1] == "60"
    assert str(output_dir / "segment-%05d.ts") in command


def test_resolve_hls_playlist_waits_for_first_segment(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    source = tmp_path / "video.mp4"
    source.write_bytes(b"source")
    video = {"id": 7, "path": str(source)}

    def start_hls(source_path, output_dir, quality, start_at):
        segment = output_dir / "segment-00000.ts"
        segment.write_bytes(b"segment")
        (output_dir / "index.m3u8").write_text(
            "#EXTM3U\n#EXTINF:2.0,\nsegment-00000.ts\n",
            encoding="utf-8",
        )

    monkeypatch.setattr("app.media.start_hls_transcode", start_hls)

    playlist = resolve_hls_playlist(video, data_dir, "low", start_ms=12_345, timeout=0.1)

    assert playlist.name == "index.m3u8"
    assert playlist.parent.name.startswith("hls-7-low-12345-")


def test_cleanup_stream_cache_deletes_old_files_only_when_due(tmp_path):
    cache_dir = tmp_path / "cache"
    stream_dir = cache_dir / "streams"
    stream_dir.mkdir(parents=True)
    old_file = stream_dir / "stream-1-low-old.mp4"
    new_file = stream_dir / "stream-1-low-new.mp4"
    old_hls_dir = stream_dir / "hls-1-low-old"
    old_file.write_bytes(b"old")
    new_file.write_bytes(b"new")
    old_hls_dir.mkdir()
    (old_hls_dir / "segment-00000.ts").write_bytes(b"segment")
    now = 2_000_000.0
    old_mtime = now - 3 * 86400
    new_mtime = now - 3600
    old_file.touch()
    new_file.touch()
    old_hls_dir.touch()
    os.utime(old_file, (old_mtime, old_mtime))
    os.utime(new_file, (new_mtime, new_mtime))
    os.utime(old_hls_dir, (old_mtime, old_mtime))

    deleted = cleanup_stream_cache(cache_dir, 2, now=now)
    second_deleted = cleanup_stream_cache(cache_dir, 2, now=now + 60)

    assert deleted == 2
    assert second_deleted == 0
    assert not old_file.exists()
    assert not old_hls_dir.exists()
    assert new_file.exists()
