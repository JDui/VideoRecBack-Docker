from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path


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
            "v360=input=equirect:output=fisheye:w=720:h=720:h_fov=180:v_fov=180,hflip,scale=720:720",
            "-compression_level",
            "4",
            "-quality",
            "72",
            "-y",
            str(output_path),
        ]
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
