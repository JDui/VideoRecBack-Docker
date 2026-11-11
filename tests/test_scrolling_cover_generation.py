import subprocess
from pathlib import Path

import pytest
from PIL import Image

from app import thumbnails


def test_scrolling_cover_has_sixteen_chronological_276p_tiles(tmp_path):
    source = tmp_path / "sequence.mkv"
    subprocess.run([
        thumbnails.require_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=black:size=36x64:rate=16:duration=1,geq=lum=16+N*12:cb=128:cr=128",
        "-c:v", "ffv1", str(source),
    ], check=True, capture_output=True, timeout=30)
    output = tmp_path / "scrolling.webp"
    thumbnails.generate_scrolling_thumbnail(source, output, 1)
    with Image.open(output) as sheet:
        assert sheet.format == "WEBP"
        assert sheet.size == (156 * 4, 276 * 4)
        levels = [sheet.getpixel((index % 4 * 156 + 78, index // 4 * 276 + 138))[0]
                  for index in range(16)]
        assert all(right > left for left, right in zip(levels, levels[1:]))


def test_short_single_frame_video_fills_all_sixteen_tiles(tmp_path):
    source = tmp_path / "single.mkv"
    subprocess.run([
        thumbnails.require_tool("ffmpeg"), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "color=red:size=64x36:rate=1", "-frames:v", "1",
        "-c:v", "ffv1", str(source),
    ], check=True, capture_output=True, timeout=30)
    output = tmp_path / "scrolling.webp"
    thumbnails.generate_scrolling_thumbnail(source, output, None)
    with Image.open(output) as sheet:
        assert sheet.size == (490 * 4, 276 * 4)
        for index in range(16):
            red, green, blue = sheet.getpixel((index % 4 * 490 + 245, index // 4 * 276 + 138))
            assert red > 180 and green < 60 and blue < 60


def test_long_sequence_seeks_in_one_process_uses_quality_50_and_preserves_existing_on_failure(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr(thumbnails, "require_tool", lambda name: name)
    monkeypatch.setattr(thumbnails, "probe_video_geometry", lambda path: (1920, 1080))

    def fake_run(command):
        commands.append(command)
        Image.new("RGB", (490 * 4, 276 * 4), "blue").save(command[-1], "WEBP")

    monkeypatch.setattr(thumbnails, "run_ffmpeg", fake_run)
    output = tmp_path / "scrolling.webp"
    thumbnails.generate_scrolling_thumbnail(Path("long.mp4"), output, 3600)
    assert len(commands) == 1
    command = commands[0]
    assert command.count("-i") == 16
    timestamps = [float(command[index + 1]) for index, part in enumerate(command) if part == "-ss"]
    assert timestamps == sorted(timestamps)
    assert len(set(timestamps)) == 16 and timestamps[-1] < 3600
    assert command[command.index("-quality") + 1] == "50"
    previous = output.read_bytes()

    def fail(command):
        Path(command[-1]).write_bytes(b"partial")
        raise thumbnails.VideoToolError("fixture failure")

    monkeypatch.setattr(thumbnails, "run_ffmpeg", fail)
    with pytest.raises(thumbnails.VideoToolError):
        thumbnails.generate_scrolling_thumbnail(Path("bad.mp4"), output, 5)
    assert output.read_bytes() == previous
    assert not list(tmp_path.glob("scrolling-*"))
