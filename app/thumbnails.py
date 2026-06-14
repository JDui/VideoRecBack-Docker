from __future__ import annotations

import json
import math
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps


class VideoToolError(RuntimeError):
    pass


@dataclass(slots=True)
class ProbeResult:
    duration_seconds: float | None
    width: int | None
    height: int | None


def require_tool(name: str) -> str:
    binary = shutil.which(name)
    if not binary:
        raise VideoToolError(f"{name} is not installed")
    return binary


def probe_video(path: Path) -> ProbeResult:
    ffprobe = require_tool("ffprobe")
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "stream=width,height:format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        raise VideoToolError(result.stderr.strip() or "ffprobe failed")
    payload = json.loads(result.stdout or "{}")
    raw_duration = payload.get("format", {}).get("duration")
    stream = next((item for item in payload.get("streams", []) if item.get("width") and item.get("height")), {})
    return ProbeResult(
        float(raw_duration) if raw_duration else None,
        int(stream["width"]) if stream.get("width") else None,
        int(stream["height"]) if stream.get("height") else None,
    )


def generate_thumbnail(video_path: Path, output_path: Path, video_type: str, duration: float | None) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if video_type == "panorama":
        generate_panorama_thumbnail(video_path, output_path, duration)
    else:
        generate_flat_thumbnail(video_path, output_path, duration)


def generate_flat_thumbnail(video_path: Path, output_path: Path, duration: float | None) -> None:
    ffmpeg = require_tool("ffmpeg")
    times = sample_times(duration)
    with tempfile.TemporaryDirectory() as tmp:
        frame_paths: list[Path] = []
        for index, timestamp in enumerate(times):
            frame_path = Path(tmp) / f"frame-{index}.webp"
            run_ffmpeg(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-ss",
                    f"{timestamp:.3f}",
                    "-i",
                    str(video_path),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=640:360:force_original_aspect_ratio=increase,crop=640:360",
                    "-compression_level",
                    "4",
                    "-quality",
                    "72",
                    "-y",
                    str(frame_path),
                ]
            )
            frame_paths.append(frame_path)

        command = [ffmpeg, "-hide_banner", "-loglevel", "error"]
        for frame_path in frame_paths:
            command.extend(["-i", str(frame_path)])
        command.extend(
            [
                "-filter_complex",
                "xstack=inputs=4:layout=0_0|640_0|0_360|640_360",
                "-frames:v",
                "1",
                "-compression_level",
                "4",
                "-quality",
                "72",
                "-y",
                str(output_path),
            ]
        )
        run_ffmpeg(command)


def generate_panorama_thumbnail(video_path: Path, output_path: Path, duration: float | None) -> None:
    ffmpeg = require_tool("ffmpeg")
    timestamp = midpoint(duration)
    with tempfile.TemporaryDirectory() as tmp:
        frame_path = Path(tmp) / "panorama-frame.png"
        run_ffmpeg(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{timestamp:.3f}",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-vf",
                "scale=1440:720:force_original_aspect_ratio=increase,crop=1440:720",
                "-y",
                str(frame_path),
            ]
        )
        render_panorama_thumbnail(frame_path, output_path)


def render_panorama_thumbnail(frame_path: Path, output_path: Path) -> None:
    with Image.open(frame_path) as raw_frame:
        frame = raw_frame.convert("RGB")

    canvas_size = (960, 720)
    ball_size = 660
    background = ImageOps.fit(frame, canvas_size, method=Image.Resampling.BICUBIC)
    background = background.filter(ImageFilter.GaussianBlur(radius=30))
    background = ImageEnhance.Color(background).enhance(1.16)
    background = ImageEnhance.Brightness(background).enhance(0.92).convert("RGBA")

    ball = rasterize_fisheye_ball(frame, ball_size)
    background.alpha_composite(ball, ((canvas_size[0] - ball_size) // 2, (canvas_size[1] - ball_size) // 2))
    background.convert("RGB").save(output_path, format="WEBP", quality=72, method=4)


def rasterize_fisheye_ball(frame: Image.Image, size: int) -> Image.Image:
    source = frame.convert("RGB")
    source_pixels = source.load()
    width, height = source.size
    radius = (size - 1) / 2
    center = radius
    inner_radius = radius * 0.91
    edge_radius = radius * 0.985
    max_theta = math.radians(208 / 2)
    ball = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pixels = ball.load()

    for y in range(size):
        ny = (y - center) / radius
        for x in range(size):
            nx = (x - center) / radius
            rho = math.hypot(nx, ny)
            if rho > edge_radius / radius:
                continue
            theta = rho * max_theta
            if rho <= 0.0001:
                direction_x = 0.0
                direction_y = 0.0
                direction_z = 1.0
            else:
                scale = math.sin(theta) / rho
                direction_x = nx * scale
                direction_y = -ny * scale
                direction_z = math.cos(theta)

            lon = math.atan2(direction_x, direction_z)
            lat = math.asin(max(-1.0, min(1.0, direction_y)))
            source_x = (0.5 + lon / (2 * math.pi)) * width
            source_y = (0.5 - lat / math.pi) * (height - 1)
            red, green, blue = sample_equirect(source_pixels, width, height, source_x, source_y)
            alpha = 255 if rho * radius <= inner_radius else round(
                255 * (edge_radius - rho * radius) / max(1.0, edge_radius - inner_radius)
            )
            shade = 0.86 + 0.24 * max(0.0, direction_z)
            rim = max(0.0, (rho - 0.72) / 0.28)
            shade *= 1.0 - rim * 0.16
            pixels[x, y] = (
                max(0, min(255, round(red * shade))),
                max(0, min(255, round(green * shade))),
                max(0, min(255, round(blue * shade))),
                max(0, min(255, alpha)),
            )

    return ball.filter(ImageFilter.GaussianBlur(radius=0.35))


def sample_equirect(pixels, width: int, height: int, x: float, y: float) -> tuple[int, int, int]:
    x0 = math.floor(x) % width
    x1 = (x0 + 1) % width
    y0 = max(0, min(height - 1, math.floor(y)))
    y1 = max(0, min(height - 1, y0 + 1))
    fx = x - math.floor(x)
    fy = y - math.floor(y)
    top = blend_rgb(pixels[x0, y0], pixels[x1, y0], fx)
    bottom = blend_rgb(pixels[x0, y1], pixels[x1, y1], fx)
    return blend_rgb(top, bottom, fy)


def blend_rgb(left: tuple[int, int, int], right: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    return (
        round(left[0] * (1 - amount) + right[0] * amount),
        round(left[1] * (1 - amount) + right[1] * amount),
        round(left[2] * (1 - amount) + right[2] * amount),
    )


def sample_times(duration: float | None) -> list[float]:
    if not duration or duration <= 0:
        return [2.0, 2.0, 2.0, 2.0]
    safe_duration = max(duration - 0.25, 0.1)
    return [
        min(2.0, safe_duration),
        max(0.1, min(duration * 0.25, safe_duration)),
        max(0.1, min(duration * 0.5, safe_duration)),
        max(0.1, min(duration * 0.75, safe_duration)),
    ]


def midpoint(duration: float | None) -> float:
    if not duration or duration <= 0:
        return 2.0
    return max(0.1, min(duration * 0.5, max(duration - 0.25, 0.1)))


def run_ffmpeg(command: list[str]) -> None:
    result = subprocess.run(command, check=False, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise VideoToolError(result.stderr.strip() or "ffmpeg failed")
