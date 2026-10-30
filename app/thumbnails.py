from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter, ImageOps, ImageStat

from app.video_types import detect_video_type

DEFAULT_THUMBNAIL_HEIGHT = 576
WEBP_QUALITY = "60"
WEBP_COMPRESSION_LEVEL = "4"
FFMPEG_THUMBNAIL_THREADS = "1"
PREVIEW_MAX_SIZE = (512, 384)


class VideoToolError(RuntimeError):
    pass


@dataclass(slots=True)
class ProbeResult:
    duration_seconds: float | None
    width: int | None
    height: int | None
    bit_depth: int | None = None
    codec_name: str | None = None
    chroma_subsampling: str | None = None
    average_bitrate: int | None = None
    hdr_format: str | None = None
    color_transfer: str | None = None
    color_primaries: str | None = None
    color_space: str | None = None
    color_range: str | None = None
    side_data: list[dict[str, object]] | None = None
    hdr_probe_complete: bool = True


@dataclass(slots=True)
class ThumbnailValidation:
    valid: bool
    reason: str
    width: int | None = None
    height: int | None = None
    brightness: float | None = None
    contrast: float | None = None


def require_tool(name: str) -> str:
    binary = shutil.which(name)
    if not binary:
        raise VideoToolError(f"{name} is not installed")
    return binary


def probe_video(path: Path) -> ProbeResult:
    ffprobe = require_tool("ffprobe")
    try:
        return _probe_video(ffprobe, path)
    except (subprocess.TimeoutExpired, ValueError, TypeError) as exc:
        raise VideoToolError(f"Unable to probe video: {exc}") from exc


def _probe_video(ffprobe: str, path: Path) -> ProbeResult:
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,sample_aspect_ratio,bits_per_raw_sample,bits_per_sample,pix_fmt,codec_name,profile,bit_rate,color_transfer,color_primaries,color_space,color_range:stream_tags=rotate:stream_side_data:format=duration,bit_rate",
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
    format_data = payload.get("format", {})
    raw_duration = format_data.get("duration")
    stream = next((item for item in payload.get("streams", []) if item.get("width") and item.get("height")), {})
    if not stream:
        raise VideoToolError("No usable video stream")
    hdr_probe_complete = True
    # HEVC static/dynamic HDR metadata is commonly frame SEI, not stream side data.
    # Bound decoding by packet count and wall time; an optional HDR probe must not
    # discard otherwise usable duration/dimensions when the decoder is unsupported.
    if str(stream.get("color_transfer") or "") in {"smpte2084", "arib-std-b67"}:
        side_data = probe_hdr_frame_side_data(ffprobe, path)
        hdr_probe_complete = side_data is not None
        stream = {**stream, "side_data_list": list(stream.get("side_data_list") or []) + (side_data or [])}
    width, height = display_dimensions(stream) if stream.get('width') and stream.get('height') else (None, None)
    return ProbeResult(
        float(raw_duration) if raw_duration else None,
        width,
        height,
        detect_bit_depth(stream),
        str(stream.get("codec_name") or "") or None,
        detect_chroma_subsampling(stream),
        parse_positive_int(format_data.get("bit_rate")) or parse_positive_int(stream.get("bit_rate")),
        detect_hdr_format(stream),
        stream.get("color_transfer"),
        stream.get("color_primaries"),
        stream.get("color_space"),
        stream.get("color_range"),
        stream.get("side_data_list") or [],
        hdr_probe_complete,
    )


def display_dimensions(stream: dict) -> tuple[int, int]:
    width, height = int(stream['width']), int(stream['height'])
    try:
        sar = Fraction(str(stream.get('sample_aspect_ratio', '1:1')).replace(':', '/'))
        if sar > 0:
            width = max(1, round(width * sar))
    except (ValueError, ZeroDivisionError):
        pass
    rotation = next((item['rotation'] for item in (stream.get('side_data_list') or []) if 'rotation' in item),
                    (stream.get('tags') or {}).get('rotate', 0))
    try:
        if round(float(rotation) / 90) % 2:
            width, height = height, width
    except (TypeError, ValueError):
        pass
    return width, height


def probe_video_geometry(path: Path) -> tuple[int, int]:
    """Read display orientation without the more expensive HDR frame probe."""
    try:
        result = subprocess.run([require_tool('ffprobe'), '-v', 'error', '-select_streams', 'v:0',
            '-show_entries', 'stream=width,height,sample_aspect_ratio:stream_tags=rotate:stream_side_data=rotation',
            '-of', 'json', str(path)], capture_output=True, text=True, timeout=15, check=False)
        if result.returncode:
            raise VideoToolError(result.stderr.strip() or 'Unable to read video orientation')
        streams = json.loads(result.stdout).get('streams', [])
        if not streams or not streams[0].get('width') or not streams[0].get('height'):
            raise VideoToolError('No usable video dimensions')
        return display_dimensions(streams[0])
    except (subprocess.TimeoutExpired, ValueError, KeyError, TypeError) as exc:
        raise VideoToolError(f'Unable to read video orientation: {exc}') from exc


def probe_hdr_frame_side_data(ffprobe: str, path: Path) -> list[dict[str, object]] | None:
    try:
        result = subprocess.run(
            [ffprobe, "-v", "error", "-threads", "1", "-select_streams", "v:0",
             "-read_intervals", "%+#32", "-show_frames", "-show_entries",
             "frame=:frame_side_data", "-of", "json", str(path)],
            check=False, capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return None
        payload = json.loads(result.stdout or "{}")
        return [item for frame in payload.get("frames", [])
                for item in frame.get("side_data_list", []) if isinstance(item, dict)]
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None


def detect_hdr_format(stream: dict[str, object]) -> str | None:
    transfer = str(stream.get("color_transfer") or "").strip().lower()
    primaries = str(stream.get("color_primaries") or "").strip().lower()
    side_data = stream.get("side_data_list") or []
    side_types = [
        str(item.get("side_data_type") or "").lower()
        for item in side_data
        if isinstance(item, dict)
    ] if isinstance(side_data, list) else []
    side_text = " ".join(side_types)
    has_dolby_vision = bool(stream.get("dv_profile") or stream.get("dv_bl_signal_compatibility_id")) or any(
        marker in side_text for marker in ("dolby vision", "dovi configuration", "dovi metadata")
    )
    if has_dolby_vision:
        return "Dolby Vision"
    if "cuva 005" in side_text or "dynamic hdr vivid" in side_text or "hdr vivid" in side_text:
        return "HDR Vivid"
    if "smpte2094-40" in side_text or "hdr10+" in side_text:
        return "HDR10+"
    if transfer == "arib-std-b67":
        return "HLG"
    if transfer == "smpte2084":
        has_static_metadata = any(
            marker in side_text for marker in ("mastering display metadata", "content light level metadata")
        )
        if primaries in {"bt2020", "bt2020nc", "bt2020_ncl"} and has_static_metadata:
            return "HDR10"
        return "PQ"
    return None


def detect_bit_depth(stream: dict[str, object]) -> int | None:
    for key in ("bits_per_raw_sample", "bits_per_sample"):
        value = parse_positive_int(stream.get(key))
        if value:
            return value
    pixel_format = str(stream.get("pix_fmt") or "").lower()
    profile = str(stream.get("profile") or "").lower()
    for candidate in (16, 14, 12, 10, 8):
        if f"{candidate}" in pixel_format or f"{candidate}" in profile:
            return candidate
    return None


def detect_chroma_subsampling(stream: dict[str, object]) -> str | None:
    pixel_format = str(stream.get("pix_fmt") or "").lower()
    match = re.search(r"yuvj?(\d{3})", pixel_format)
    if match:
        return match.group(1)
    if pixel_format.startswith(("p010", "p012", "p016", "nv12", "nv21")):
        return "420"
    if pixel_format.startswith(("yuyv422", "uyvy422")):
        return "422"
    if pixel_format.startswith("gbr"):
        return "444"
    return None


def parse_positive_int(value: object) -> int | None:
    try:
        number = int(str(value or "").strip())
    except ValueError:
        return None
    return number if number > 0 else None


def flat_thumbnail_size(height: int = DEFAULT_THUMBNAIL_HEIGHT) -> tuple[int, int]:
    clean_height = normalize_thumbnail_height(height)
    width = round(clean_height * 16 / 9)
    if width % 2:
        width += 1
    return width, clean_height


def flat_tile_size(height: int = DEFAULT_THUMBNAIL_HEIGHT) -> tuple[int, int]:
    width, clean_height = flat_thumbnail_size(height)
    return width // 2, clean_height // 2


def panorama_thumbnail_size(height: int = DEFAULT_THUMBNAIL_HEIGHT) -> tuple[int, int]:
    clean_height = normalize_thumbnail_height(height)
    width = round(clean_height * 4 / 3)
    if width % 2:
        width += 1
    return width, clean_height


def normalize_thumbnail_height(height: int | None) -> int:
    try:
        value = int(height or DEFAULT_THUMBNAIL_HEIGHT)
    except (TypeError, ValueError):
        value = DEFAULT_THUMBNAIL_HEIGHT
    return value if value in {480, 576, 720} else DEFAULT_THUMBNAIL_HEIGHT


def generate_thumbnail(
    video_path: Path,
    output_path: Path,
    video_type: str,
    duration: float | None,
    height: int = DEFAULT_THUMBNAIL_HEIGHT,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if video_type == "panorama":
        generate_panorama_thumbnail(video_path, output_path, duration, height)
    else:
        generate_flat_thumbnail(video_path, output_path, duration, height)


def generate_preview_thumbnail(source_path: Path, output_path: Path) -> Path:
    if output_path.exists() and output_path.stat().st_mtime_ns >= source_path.stat().st_mtime_ns:
        return output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = output_path.with_suffix(".tmp.webp")
    try:
        with Image.open(source_path) as source:
            preview = source.convert("RGB")
            preview.thumbnail(PREVIEW_MAX_SIZE, Image.Resampling.LANCZOS)
            preview.save(
                temporary_path,
                format="WEBP",
                quality=int(WEBP_QUALITY),
                method=int(WEBP_COMPRESSION_LEVEL),
            )
        temporary_path.replace(output_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return output_path


def generate_flat_thumbnail(
    video_path: Path,
    output_path: Path,
    duration: float | None,
    height: int = DEFAULT_THUMBNAIL_HEIGHT,
) -> None:
    ffmpeg = require_tool("ffmpeg")
    times = sample_times(duration)
    tile_width, tile_height = flat_tile_size(height)
    width, display_height = probe_video_geometry(video_path)
    portrait = display_height > width
    if portrait:
        tile_height = normalize_thumbnail_height(height) // 2
        tile_width = max(2, round(tile_height * width / display_height / 2) * 2)
        tile_width *= 2
    video_filter = (
        f"scale={tile_width}:{tile_height},setsar=1"
        if portrait else
        f"scale={tile_width}:{tile_height}:force_original_aspect_ratio=increase,crop={tile_width}:{tile_height}"
    )
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
                    video_filter,
                    "-compression_level",
                    WEBP_COMPRESSION_LEVEL,
                    "-quality",
                    WEBP_QUALITY,
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
                (
                    f"xstack=inputs=4:layout=0_0|{tile_width}_0|{tile_width * 2}_0|{tile_width * 3}_0"
                    if portrait else
                    f"xstack=inputs=4:layout=0_0|{tile_width}_0|0_{tile_height}|{tile_width}_{tile_height}"
                ),
                "-frames:v",
                "1",
                "-compression_level",
                WEBP_COMPRESSION_LEVEL,
                "-quality",
                WEBP_QUALITY,
                "-y",
                str(output_path),
            ]
        )
        run_ffmpeg(command)


def generate_panorama_thumbnail(
    video_path: Path,
    output_path: Path,
    duration: float | None,
    height: int = DEFAULT_THUMBNAIL_HEIGHT,
) -> None:
    result = generate_panorama_thumbnail_debug(video_path, output_path, duration, height)
    if not result["valid"]:
        reasons = "; ".join(f"{item['timestamp']:.3f}s: {item['reason']}" for item in result["attempts"])
        raise VideoToolError(f"Unable to generate valid panorama thumbnail: {reasons}")


def generate_panorama_thumbnail_debug(
    video_path: Path,
    output_path: Path,
    duration: float | None,
    height: int = DEFAULT_THUMBNAIL_HEIGHT,
) -> dict[str, object]:
    ffmpeg = require_tool("ffmpeg")
    attempts: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory() as tmp:
        for index, timestamp in enumerate(panorama_sample_times(duration)):
            frame_path = Path(tmp) / f"panorama-frame-{index}.png"
            attempt = {"timestamp": timestamp, "frame": str(frame_path), "output": str(output_path)}
            try:
                extract_panorama_frame(ffmpeg, video_path, frame_path, timestamp)
                render_panorama_thumbnail(frame_path, output_path, height)
                validation = validate_thumbnail(output_path, panorama_thumbnail_size(height))
                attempt.update(
                    {
                        "width": validation.width,
                        "height": validation.height,
                        "brightness": validation.brightness,
                        "contrast": validation.contrast,
                        "valid": validation.valid,
                        "reason": validation.reason,
                    }
                )
                attempts.append(attempt)
                if validation.valid:
                    return {
                        "input": str(video_path),
                        "is_panorama": detect_video_type(str(video_path)) == "panorama",
                        "timestamp": timestamp,
                        "output": str(output_path),
                        "width": validation.width,
                        "height": validation.height,
                        "brightness": validation.brightness,
                        "contrast": validation.contrast,
                        "valid": True,
                        "reason": validation.reason,
                        "attempts": attempts,
                    }
                output_path.unlink(missing_ok=True)
            except (VideoToolError, OSError) as exc:
                output_path.unlink(missing_ok=True)
                attempt.update({"valid": False, "reason": str(exc)})
                attempts.append(attempt)

    last = attempts[-1] if attempts else {"reason": "no attempts"}
    return {
        "input": str(video_path),
        "is_panorama": detect_video_type(str(video_path)) == "panorama",
        "timestamp": None,
        "output": str(output_path),
        "width": None,
        "height": None,
        "brightness": None,
        "contrast": None,
        "valid": False,
        "reason": last["reason"],
        "attempts": attempts,
    }


def extract_panorama_frame(ffmpeg: str, video_path: Path, frame_path: Path, timestamp: float) -> None:
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


def render_panorama_thumbnail(
    frame_path: Path,
    output_path: Path,
    height: int = DEFAULT_THUMBNAIL_HEIGHT,
) -> None:
    with Image.open(frame_path) as raw_frame:
        frame = raw_frame.convert("RGB")

    canvas_size = panorama_thumbnail_size(height)
    ball_size = min(canvas_size[1] - 48, round(canvas_size[1] * 0.918))
    background = ImageOps.fit(frame, canvas_size, method=Image.Resampling.BICUBIC)
    background = background.filter(ImageFilter.GaussianBlur(radius=30))
    background = ImageEnhance.Color(background).enhance(1.16)
    background = ImageEnhance.Brightness(background).enhance(0.92).convert("RGBA")

    ball = rasterize_fisheye_ball(frame, ball_size)
    background.alpha_composite(ball, ((canvas_size[0] - ball_size) // 2, (canvas_size[1] - ball_size) // 2))
    background.convert("RGB").save(
        output_path,
        format="WEBP",
        quality=int(WEBP_QUALITY),
        method=int(WEBP_COMPRESSION_LEVEL),
    )


def validate_thumbnail(
    path: Path,
    expected_size: tuple[int, int] | None = None,
) -> ThumbnailValidation:
    expected_size = expected_size or panorama_thumbnail_size()
    if not path.exists() or path.stat().st_size <= 0:
        return ThumbnailValidation(False, "missing output")
    try:
        with Image.open(path) as image:
            image.load()
            width, height = image.size
            if image.size != expected_size:
                return ThumbnailValidation(False, "unexpected dimensions", width, height)
            if image.mode in {"RGBA", "LA"}:
                alpha = image.getchannel("A")
                alpha_min, alpha_max = alpha.getextrema()
                if alpha_max == 0 or alpha_min < 8:
                    return ThumbnailValidation(False, "invalid transparency", width, height)
            gray = ImageOps.grayscale(image.convert("RGB"))
            stat = ImageStat.Stat(gray)
            brightness = float(stat.mean[0])
            contrast = float(stat.stddev[0])
            if brightness < 3 or brightness > 252:
                return ThumbnailValidation(False, "blank brightness", width, height, brightness, contrast)
            if contrast < 1.0:
                return ThumbnailValidation(False, "blank contrast", width, height, brightness, contrast)
            return ThumbnailValidation(True, "ok", width, height, brightness, contrast)
    except OSError as exc:
        return ThumbnailValidation(False, f"decode failed: {exc}")


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


def panorama_sample_times(duration: float | None) -> list[float]:
    if not duration or duration <= 0:
        return [2.0]
    safe_duration = max(duration - 0.25, 0.1)
    candidates = [midpoint(duration)] + [duration * fraction for fraction in (0.2, 0.35, 0.5, 0.65, 0.8)]
    normalized: list[float] = []
    seen: set[float] = set()
    for timestamp in candidates:
        safe_timestamp = max(0.1, min(timestamp, safe_duration))
        key = round(safe_timestamp, 3)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(safe_timestamp)
    return normalized


def midpoint(duration: float | None) -> float:
    if not duration or duration <= 0:
        return 2.0
    return max(0.1, min(duration * 0.5, max(duration - 0.25, 0.1)))


def run_ffmpeg(command: list[str]) -> None:
    limited_command = [
        command[0],
        "-threads",
        FFMPEG_THUMBNAIL_THREADS,
        "-filter_threads",
        FFMPEG_THUMBNAIL_THREADS,
        *command[1:-1],
        "-threads",
        FFMPEG_THUMBNAIL_THREADS,
        command[-1],
    ]
    result = subprocess.run(limited_command, check=False, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise VideoToolError(result.stderr.strip() or "ffmpeg failed")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate and validate a panorama thumbnail.")
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--duration", type=float, default=None)
    args = parser.parse_args()

    result = generate_panorama_thumbnail_debug(args.input, args.output, args.duration)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
