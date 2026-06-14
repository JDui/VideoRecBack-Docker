from pathlib import Path

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

    monkeypatch.setattr(thumbnails, "require_tool", lambda name: name)
    monkeypatch.setattr(thumbnails, "run_ffmpeg", commands.append)

    thumbnails.generate_panorama_thumbnail(Path("/videos/demo.mp4"), tmp_path / "thumb.webp", 10)

    vf_index = commands[0].index("-vf") + 1
    assert commands[0][vf_index] == (
        "v360=input=equirect:output=fisheye:w=720:h=720:yaw=0:pitch=0:roll=0:h_fov=180:v_fov=180,scale=720:720"
    )
