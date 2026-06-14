from __future__ import annotations

from pathlib import Path


PANORAMA_MARKERS = ("_360", "-360", " 360", ".360", "vr", "equirect", "panorama", "panarama", "全景")


def detect_video_type(path: str | Path) -> str:
    path_text = str(path).lower()
    if any(marker in path_text for marker in PANORAMA_MARKERS):
        return "panorama"
    return "flat"
