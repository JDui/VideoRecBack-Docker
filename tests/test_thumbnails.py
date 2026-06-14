from pathlib import Path

from PIL import Image

from app import thumbnails
from app.thumbnails import ThumbnailValidation, midpoint, panorama_sample_times, sample_times


def test_sample_times_for_flat_video():
    assert sample_times(100) == [2.0, 25.0, 50.0, 75.0]


def test_sample_times_for_short_video():
    assert sample_times(1) == [0.75, 0.25, 0.5, 0.75]


def test_midpoint():
    assert midpoint(100) == 50


def test_panorama_sample_times_try_midpoint_first():
    assert panorama_sample_times(100) == [50, 20, 35, 65, 80]


def test_panorama_thumbnail_uses_front_fisheye(monkeypatch, tmp_path):
    commands = []
    renders = []

    monkeypatch.setattr(thumbnails, "require_tool", lambda name: name)
    monkeypatch.setattr(thumbnails, "run_ffmpeg", commands.append)
    monkeypatch.setattr(thumbnails, "render_panorama_thumbnail", lambda frame, output: renders.append((frame, output)))
    monkeypatch.setattr(thumbnails, "validate_thumbnail", lambda output: ThumbnailValidation(True, "ok", 960, 720, 120, 30))

    thumbnails.generate_panorama_thumbnail(Path("/videos/demo.mp4"), tmp_path / "thumb.webp", 10)

    vf_index = commands[0].index("-vf") + 1
    assert commands[0][vf_index] == "scale=1440:720:force_original_aspect_ratio=increase,crop=1440:720"
    assert "-filter_complex" not in commands[0]
    assert renders[0][0].name == "panorama-frame-0.png"
    assert renders[0][1] == tmp_path / "thumb.webp"


def test_panorama_thumbnail_retries_invalid_frame(monkeypatch, tmp_path):
    commands = []
    validations = [
        ThumbnailValidation(False, "blank", 960, 720, 0, 0),
        ThumbnailValidation(True, "ok", 960, 720, 120, 20),
    ]

    monkeypatch.setattr(thumbnails, "require_tool", lambda name: name)
    monkeypatch.setattr(thumbnails, "run_ffmpeg", commands.append)
    monkeypatch.setattr(thumbnails, "render_panorama_thumbnail", lambda frame, output: output.write_bytes(b"webp"))
    monkeypatch.setattr(thumbnails, "validate_thumbnail", lambda output: validations.pop(0))

    result = thumbnails.generate_panorama_thumbnail_debug(Path("/videos/demo.mp4"), tmp_path / "thumb.webp", 10)

    timestamps = [commands[index][commands[index].index("-ss") + 1] for index in range(len(commands))]
    assert timestamps == ["5.000", "2.000"]
    assert result["valid"] is True
    assert result["attempts"][0]["reason"] == "blank"


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


def test_validate_thumbnail_rejects_blank(tmp_path):
    output_path = tmp_path / "blank.webp"
    Image.new("RGB", (960, 720), "black").save(output_path)

    validation = thumbnails.validate_thumbnail(output_path)

    assert validation.valid is False
    assert validation.reason == "blank brightness"
