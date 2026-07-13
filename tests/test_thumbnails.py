from pathlib import Path

from PIL import Image

from app import thumbnails
from app.thumbnails import (
    ThumbnailValidation,
    detect_bit_depth,
    detect_chroma_subsampling,
    flat_thumbnail_size,
    generate_preview_thumbnail,
    midpoint,
    panorama_sample_times,
    panorama_thumbnail_size,
    sample_times,
)


def test_sample_times_for_flat_video():
    assert sample_times(100) == [2.0, 25.0, 50.0, 75.0]


def test_sample_times_for_short_video():
    assert sample_times(1) == [0.75, 0.25, 0.5, 0.75]


def test_midpoint():
    assert midpoint(100) == 50


def test_panorama_sample_times_try_midpoint_first():
    assert panorama_sample_times(100) == [50, 20, 35, 65, 80]


def test_thumbnail_sizes_are_height_based():
    assert flat_thumbnail_size(720) == (1280, 720)
    assert panorama_thumbnail_size(480) == (640, 480)


def test_detect_bit_depth_from_probe_stream():
    assert detect_bit_depth({"bits_per_raw_sample": "10", "pix_fmt": "yuv420p10le"}) == 10
    assert detect_bit_depth({"bits_per_raw_sample": "0", "pix_fmt": "p010le"}) == 10
    assert detect_bit_depth({"bits_per_raw_sample": "", "profile": "Main 10"}) == 10
    assert detect_bit_depth({"pix_fmt": "yuv420p"}) is None


def test_detect_chroma_subsampling_from_pixel_format():
    assert detect_chroma_subsampling({"pix_fmt": "yuv420p10le"}) == "420"
    assert detect_chroma_subsampling({"pix_fmt": "yuv422p"}) == "422"
    assert detect_chroma_subsampling({"pix_fmt": "p010le"}) == "420"
    assert detect_chroma_subsampling({"pix_fmt": "gbrp10le"}) == "444"


def test_panorama_thumbnail_uses_front_fisheye(monkeypatch, tmp_path):
    commands = []
    renders = []

    monkeypatch.setattr(thumbnails, "require_tool", lambda name: name)
    monkeypatch.setattr(thumbnails, "run_ffmpeg", commands.append)
    monkeypatch.setattr(thumbnails, "render_panorama_thumbnail", lambda frame, output, *args: renders.append((frame, output)))
    monkeypatch.setattr(thumbnails, "validate_thumbnail", lambda output, *args: ThumbnailValidation(True, "ok", 768, 576, 120, 30))

    thumbnails.generate_panorama_thumbnail(Path("/videos/demo.mp4"), tmp_path / "thumb.webp", 10)

    vf_index = commands[0].index("-vf") + 1
    assert commands[0][vf_index] == "scale=1440:720:force_original_aspect_ratio=increase,crop=1440:720"
    assert "-filter_complex" not in commands[0]
    assert renders[0][0].name == "panorama-frame-0.png"
    assert renders[0][1] == tmp_path / "thumb.webp"


def test_flat_thumbnail_uses_configured_resolution_and_quality(monkeypatch, tmp_path):
    commands = []

    monkeypatch.setattr(thumbnails, "require_tool", lambda name: name)
    monkeypatch.setattr(thumbnails, "run_ffmpeg", commands.append)

    thumbnails.generate_flat_thumbnail(Path("/videos/demo.mp4"), tmp_path / "thumb.webp", 10, 720)

    vf_index = commands[0].index("-vf") + 1
    quality_index = commands[0].index("-quality") + 1
    assert commands[0][vf_index] == "scale=640:360:force_original_aspect_ratio=increase,crop=640:360"
    assert commands[0][quality_index] == "60"


def test_preview_thumbnail_is_resized_and_cached(tmp_path):
    source = tmp_path / "source.webp"
    output = tmp_path / "preview.webp"
    Image.new("RGB", (1280, 720), "red").save(source, format="WEBP")

    generate_preview_thumbnail(source, output)
    first_mtime = output.stat().st_mtime_ns
    generate_preview_thumbnail(source, output)

    with Image.open(output) as image:
        assert image.size == (512, 288)
    assert output.stat().st_mtime_ns == first_mtime


def test_run_ffmpeg_limits_decoder_and_encoder_threads(monkeypatch):
    captured = []

    class Result:
        returncode = 0
        stderr = ""

    monkeypatch.setattr(thumbnails.subprocess, "run", lambda command, **kwargs: captured.append(command) or Result())

    thumbnails.run_ffmpeg(["ffmpeg", "-i", "input.mp4", "output.webp"])

    command = captured[0]
    assert command.count("-threads") == 2
    assert all(command[index + 1] == "1" for index, value in enumerate(command) if value == "-threads")
    assert command[command.index("-filter_threads") + 1] == "1"


def test_panorama_thumbnail_retries_invalid_frame(monkeypatch, tmp_path):
    commands = []
    validations = [
        ThumbnailValidation(False, "blank", 768, 576, 0, 0),
        ThumbnailValidation(True, "ok", 768, 576, 120, 20),
    ]

    monkeypatch.setattr(thumbnails, "require_tool", lambda name: name)
    monkeypatch.setattr(thumbnails, "run_ffmpeg", commands.append)
    monkeypatch.setattr(thumbnails, "render_panorama_thumbnail", lambda frame, output, *args: output.write_bytes(b"webp"))
    monkeypatch.setattr(thumbnails, "validate_thumbnail", lambda output, *args: validations.pop(0))

    result = thumbnails.generate_panorama_thumbnail_debug(Path("/videos/demo.mp4"), tmp_path / "thumb.webp", 10)

    timestamps = [commands[index][commands[index].index("-ss") + 1] for index in range(len(commands))]
    assert timestamps == ["5.000", "2.000"]
    assert result["valid"] is True
    assert result["attempts"][0]["reason"] == "blank"


def test_panorama_thumbnail_renders_576p_webp(tmp_path):
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
        assert image.size == (768, 576)
        assert image.format == "WEBP"
        center = image.getpixel((384, 288))
        corner = image.getpixel((20, 20))
        edge = image.getpixel((384, 40))
    assert center != corner
    assert edge != corner


def test_validate_thumbnail_rejects_blank(tmp_path):
    output_path = tmp_path / "blank.webp"
    Image.new("RGB", (768, 576), "black").save(output_path)

    validation = thumbnails.validate_thumbnail(output_path)

    assert validation.valid is False
    assert validation.reason == "blank brightness"
