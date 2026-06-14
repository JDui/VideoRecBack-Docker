from __future__ import annotations

from pathlib import Path


PANORAMA_MARKERS = ("_360", "-360", " 360", ".360", "vr", "equirect", "panorama", "panarama", "全景")
PANORAMA_ASPECT_RATIO = 2.0
PANORAMA_ASPECT_TOLERANCE = 0.05


def detect_video_type(path: str | Path, width: int | None = None, height: int | None = None) -> str:
    path_text = str(path).lower()
    if any(marker in path_text for marker in PANORAMA_MARKERS):
        return "panorama"
    if is_panorama_aspect_ratio(width, height):
        return "panorama"
    return "flat"


def is_panorama_aspect_ratio(width: int | None, height: int | None) -> bool:
    if not width or not height:
        return False
    return abs((width / height) - PANORAMA_ASPECT_RATIO) <= PANORAMA_ASPECT_TOLERANCE
