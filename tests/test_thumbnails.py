from pathlib import Path

from PIL import Image

from app import thumbnails
from app.thumbnails import midpoint, sample_times


def test_sample_times_for_flat_video():
    assert sample_times(100) == [2.0, 25.0, 50.0, 75.0]


def test_sample_times_for_short_video():
    assert sample_times(1) == [0.75, 0.25, 0.5, 0.75]


def test_midpoint():
    assert midpoint(100) == 50


def test_panorama_thumbnail_uses_front_fisheye(monkeypatch, tmp_path):
    commands = []
    renders = []

    monkeypatch.setattr(thumbnails, "require_tool", lambda name: name)
    monkeypatch.setattr(thumbnails, "run_ffmpeg", commands.append)
    monkeypatch.setattr(thumbnails, "render_panorama_thumbnail", lambda frame, output: renders.append((frame, output)))

    thumbnails.generate_panorama_thumbnail(Path("/videos/demo.mp4"), tmp_path / "thumb.webp", 10)

    vf_index = commands[0].index("-vf") + 1
    assert commands[0][vf_index] == "scale=1440:720:force_original_aspect_ratio=increase,crop=1440:720"
    assert "-filter_complex" not in commands[0]
    assert renders[0][0].name == "panorama-frame.png"
    assert renders[0][1] == tmp_path / "thumb.webp"


def test_panorama_thumbnail_renders_720p_webp(tmp_path):
    frame_path = tmp_path / "frame.png"
    output_path = tmp_path / "thumb.webp"
    frame = Image.new("RGB", (1440, 720))
    pixels = frame.load()
    for y in range(frame.height):
        for x in range(frame.width):
            pixels[x, y] = (x * 255 // frame.width, y * 255 // frame.height, 220 if x > frame.width // 2 else 40)
    frame.save(frame_path)

    thumbnails.render_panorama_thumbnail(frame_path, output_path)

    with Image.open(output_path) as image:
        assert image.size == (960, 720)
        assert image.format == "WEBP"
        center = image.getpixel((480, 360))
        corner = image.getpixel((20, 20))
        edge = image.getpixel((480, 40))
    assert center != corner
    assert edge != corner
