"""Keep source HDR signal characteristics when encoding browser streams."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass, replace
from fractions import Fraction
from functools import lru_cache
from pathlib import Path

from app.thumbnails import ProbeResult, VideoToolError, probe_video, require_tool


@dataclass(frozen=True)
class TranscodeColor:
    transfer: str = ""
    primaries: str = ""
    matrix: str = ""
    range: str = "tv"
    output_format: str = "SDR"
    x265_metadata: tuple[str, ...] = ()
    note: str = ""
    encoder_options: tuple[str, ...] = ()

    @property
    def hdr(self) -> bool:
        return bool(self.transfer)

    @property
    def video_range(self) -> str:
        return "HLG" if self.transfer == "arib-std-b67" else "PQ"

    def ffmpeg_args(self) -> list[str]:
        if not self.hdr:
            return []
        return ["-color_trc", self.transfer, "-color_primaries", self.primaries,
                "-colorspace", self.matrix, "-color_range", self.range, "-tag:v", "hvc1", *self.encoder_options]

    def x265_params(self) -> str:
        primaries = {"bt709": 1, "bt2020": 9, "smpte431": 11, "smpte432": 12}[self.primaries]
        matrix = {"bt709": 1, "bt2020nc": 9, "bt2020c": 10}[self.matrix]
        transfer = 16 if self.transfer == "smpte2084" else 18
        return ':'.join(["repeat-headers=1", "scenecut=0", f"colorprim={primaries}",
                         f"transfer={transfer}", f"colormatrix={matrix}",
                         f"range={'full' if self.range == 'pc' else 'limited'}", *self.x265_metadata])

    def scale_options(self) -> str:
        value_range = 'full' if self.range == 'pc' else 'limited'
        matrix = 'bt2020' if self.matrix == 'bt2020nc' else 'bt709'
        return f':in_range={value_range}:out_range={value_range}:in_color_matrix={matrix}:out_color_matrix={matrix}'


def transcode_color(source: Path) -> TranscodeColor:
    stat = source.stat()
    return _cached_color(str(source.resolve()), stat.st_mtime_ns, stat.st_size)


@lru_cache(maxsize=128)
def _cached_color(path: str, mtime_ns: int, size: int) -> TranscodeColor:
    color = color_from_probe(probe_video(Path(path)))
    return replace(color, encoder_options=_hdr_encoder_options()) if color.hdr else color


@lru_cache(maxsize=1)
def _hdr_encoder_options() -> tuple[str, ...]:
    try:
        result = subprocess.run([require_tool('ffmpeg'), '-hide_banner', '-h', 'encoder=libx265'],
                                capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VideoToolError('无法检查 HDR 编码器，请重试或使用原画。') from exc
    help_text = result.stdout + result.stderr
    if result.returncode or 'yuv420p10le' not in help_text:
        raise VideoToolError('服务端缺少 HEVC 10bit 编码器，请使用原画。')
    # New FFmpeg versions can automatically encode Dolby RPU. This path deliberately
    # produces base HDR; older builds have no such switch and never encoded the RPU.
    return ('-dolbyvision', '0') if '-dolbyvision ' in help_text else ()


def color_from_probe(probe: ProbeResult) -> TranscodeColor:
    if not probe.hdr_format and probe.color_transfer not in {"smpte2084", "arib-std-b67"}:
        return TranscodeColor()
    if not probe.hdr_probe_complete:
        raise VideoToolError("HDR 元数据探测未完成，请重试或播放原画。")
    sides = probe.side_data or []
    if probe.hdr_format == "Dolby Vision":
        config = next((item for item in sides if "dv_profile" in item), {})
        # Profile 8.1/8.4 explicitly declare an HDR10/HLG compatible base layer.
        profile = config.get("dv_profile")
        compatibility = config.get("dv_bl_signal_compatibility_id")
        expected = {1: "smpte2084", 4: "arib-std-b67"}.get(compatibility)
        if profile != 8 or not expected or probe.color_transfer != expected:
            raise VideoToolError("此杜比视界格式尚不能安全保留 HDR 转码，请播放或下载原画。")
    if (not probe.bit_depth or probe.bit_depth < 10
            or probe.color_transfer not in {"smpte2084", "arib-std-b67"}
            or probe.color_primaries not in {"bt2020", "bt709", "smpte431", "smpte432"}
            or probe.color_space not in {"bt2020nc", "bt709"}
            or probe.color_range not in {"tv", "pc"}):
        raise VideoToolError("HDR 位深或色彩参数不完整或不受支持，无法安全转码，请播放原画。")

    metadata = []
    mastering = next((item for item in sides if item.get("side_data_type") == "Mastering display metadata"), None)
    light = next((item for item in sides if item.get("side_data_type") == "Content light level metadata"), None)
    try:
        if mastering:
            def coordinate(name: str) -> str:
                return str(round(Fraction(str(mastering[name])) * 50000))
            points = ''.join(f'{letter}({coordinate(name + "_x")},{coordinate(name + "_y")})'
                             for letter, name in [('G', 'green'), ('B', 'blue'), ('R', 'red'), ('WP', 'white_point')])
            maximum = round(Fraction(str(mastering['max_luminance'])) * 10000)
            minimum = round(Fraction(str(mastering['min_luminance'])) * 10000)
            metadata.append(f'master-display={points}L({maximum},{minimum})')
        if light:
            metadata.append(f'max-cll={int(light["max_content"])},{int(light["max_average"])}')
    except (KeyError, ValueError, ZeroDivisionError) as exc:
        raise VideoToolError("HDR 静态元数据无效，请播放原画。") from exc
    output = ("HLG" if probe.color_transfer == "arib-std-b67" else
              "HDR10" if mastering and probe.color_primaries == "bt2020" else "PQ")
    note = ""
    if probe.hdr_format in {"Dolby Vision", "HDR10+", "HDR Vivid"}:
        note = f"{probe.hdr_format} 转码保留 {output} 基础 HDR，动态元数据不保留；完整标准请使用原画。"
    return TranscodeColor(probe.color_transfer, probe.color_primaries, probe.color_space,
                          probe.color_range, output, tuple(metadata), note)
