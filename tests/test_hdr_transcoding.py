import json
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from app.media import (STREAM_QUALITIES, build_hls_command, generate_stream_cache,
                       hls_playlist_ready, playback_playlist, resolve_hls_segment, hls_cache_dir)
from app.thumbnails import ProbeResult, VideoToolError, probe_video
from app.transcode_color import color_from_probe, transcode_color


def hdr_probe(**kwargs):
    return replace(ProbeResult(1, 192, 64, 10, 'hevc', '420', 100000, 'PQ',
                              'smpte2084', 'bt2020', 'bt2020nc', 'tv'), **kwargs)


@pytest.mark.parametrize('encoder', ['libx264_ultrafast', 'h264_qsv', 'h264_vaapi'])
def test_hdr_never_uses_eight_bit_h264_encoder(encoder, tmp_path):
    command = build_hls_command(tmp_path / 'in.mp4', tmp_path, STREAM_QUALITIES['high'], 12.5,
                                encoder, color=color_from_probe(hdr_probe()))
    assert command[command.index('-c:v') + 1] == 'libx265'
    assert command[command.index('-pix_fmt') + 1] == 'yuv420p10le'
    assert command[command.index('-hls_segment_type') + 1] == 'fmp4'
    assert command[command.index('-ss') + 1] == '12.500'
    assert '-vaapi_device' not in command


def test_unsupported_dolby_vision_and_incomplete_hdr_cannot_silently_transcode():
    with pytest.raises(VideoToolError, match='杜比视界'):
        color_from_probe(hdr_probe(hdr_format='Dolby Vision', side_data=[
            {'dv_profile': 5, 'dv_bl_signal_compatibility_id': 0}]))
    with pytest.raises(VideoToolError, match='探测未完成'):
        color_from_probe(hdr_probe(hdr_probe_complete=False))
    with pytest.raises(VideoToolError, match='色彩参数'):
        color_from_probe(hdr_probe(color_range=None))
    with pytest.raises(VideoToolError, match='位深'):
        color_from_probe(hdr_probe(bit_depth=8))
    hlg = color_from_probe(hdr_probe(hdr_format='Dolby Vision', color_transfer='arib-std-b67', side_data=[
        {'dv_profile': 8, 'dv_bl_signal_compatibility_id': 4}]))
    assert hlg.output_format == 'HLG'
    assert '动态元数据不保留' in hlg.note


def test_fmp4_readiness_requires_initialization_and_routes_are_bounded(tmp_path):
    source = tmp_path / 'in.mp4'; source.write_bytes(b'fixture')
    video = {'id': 1, 'path': str(source)}
    directory = hls_cache_dir(video, source, tmp_path, 'high', 0)
    directory.mkdir(parents=True)
    playlist = directory / 'index.m3u8'
    playlist.write_text('#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\n#EXTINF:2,\nsegment-00000.m4s\n')
    (directory / 'segment-00000.m4s').write_bytes(b'fragment')
    assert not hls_playlist_ready(playlist)
    (directory / 'init.mp4').write_bytes(b'init')
    assert hls_playlist_ready(playlist)
    assert resolve_hls_segment(video, tmp_path, 'high', 0, 'stream.m3u8') == playlist
    assert resolve_hls_segment(video, tmp_path, 'high', 0, 'init.mp4').name == 'init.mp4'
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        resolve_hls_segment(video, tmp_path, 'high', 0, 'segment-../../secret.ts')


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='FFmpeg required')
@pytest.mark.parametrize('transfer,expected,value_range', [('smpte2084', 'HDR10', 'tv'), ('arib-std-b67', 'HLG', 'tv'), ('smpte2084', 'HDR10', 'pc')])
def test_real_hdr_transcodes_keep_ten_bit_signal_and_metadata(tmp_path, transfer, expected, value_range):
    def run(command, **kwargs):
        return subprocess.run(command, check=True, capture_output=True, timeout=40, **kwargs)
    source = tmp_path / 'hdr.mp4'
    params = f'pools=1:frame-threads=1:repeat-headers=1:colorprim=9:colormatrix=9:transfer={16 if transfer == "smpte2084" else 18}'
    params += f':range={"full" if value_range == "pc" else "limited"}'
    if expected == 'HDR10':
        params += ':master-display=G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1):max-cll=1000,400'
    run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=192x64:rate=24:duration=1',
         '-vf', f'scale=in_range=limited:out_range={"full" if value_range == "pc" else "limited"}',
         '-pix_fmt', 'yuv420p10le', '-c:v', 'libx265', '-preset', 'ultrafast', '-x265-params', params,
         '-color_trc', transfer, '-color_primaries', 'bt2020', '-colorspace', 'bt2020nc', '-color_range', value_range, str(source)])
    color = transcode_color(source)
    assert color.output_format == expected
    download = tmp_path / 'download.mp4'
    generate_stream_cache(source, download, STREAM_QUALITIES['high'])
    hls = tmp_path / 'hls'; hls.mkdir()
    run(build_hls_command(source, hls, STREAM_QUALITIES['high'], 0, color=color), cwd=hls)
    assert hls_playlist_ready(hls / 'index.m3u8')
    master = playback_playlist(hls / 'index.m3u8', color).read_text()
    assert f'VIDEO-RANGE={color.video_range}' in master
    assert 'CODECS="hvc1.' in master
    assert 'stream.m3u8' in master and '\nindex.m3u8' not in master
    # Decode the actual initialization + media fragment, not just the command arguments.
    combined = tmp_path / 'fragment.mp4'
    combined.write_bytes((hls / 'init.mp4').read_bytes() + next(hls.glob('segment-*.m4s')).read_bytes())
    for output in [download, combined]:
        probe = probe_video(output)
        assert (probe.codec_name, probe.bit_depth, probe.hdr_format) == ('hevc', 10, expected)
        assert (probe.color_transfer, probe.color_primaries, probe.color_space, probe.color_range) == (transfer, 'bt2020', 'bt2020nc', value_range)
        if expected == 'HDR10':
            mastering = next(item for item in probe.side_data if item.get('side_data_type') == 'Mastering display metadata')
            assert mastering['max_luminance'] == '10000000/10000'
            light = next(item for item in probe.side_data if item.get('side_data_type') == 'Content light level metadata')
            assert (light['max_content'], light['max_average']) == (1000, 400)


def test_main_probe_timeout_is_a_per_file_error(monkeypatch, tmp_path):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('ffprobe', 60)
    monkeypatch.setattr('app.thumbnails.require_tool', lambda name: name)
    monkeypatch.setattr('app.thumbnails.subprocess.run', timeout)
    with pytest.raises(VideoToolError):
        probe_video(tmp_path / 'bad.mp4')
