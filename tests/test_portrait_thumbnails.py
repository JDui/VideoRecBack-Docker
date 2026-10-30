import asyncio
import shutil
import subprocess
from datetime import datetime

import httpx
import pytest
from PIL import Image

from app.config import Settings
from app.db import Database
from app.scanner import Scanner, ScanSummary
from app.thumbnails import VideoToolError, generate_flat_thumbnail, probe_video, probe_video_geometry, require_tool


@pytest.mark.skipif(not shutil.which('ffmpeg') or not shutil.which('ffprobe'), reason='FFmpeg required')
def test_real_portrait_and_rotated_phone_keep_full_frame(tmp_path):
    # Distinct top and bottom bands reveal cropping, including after phone autorotation.
    source = tmp_path / 'portrait.png'
    picture = Image.new('RGB', (96, 160), (30, 150, 50))
    picture.paste((240, 30, 30), (0, 0, 96, 32))
    picture.paste((30, 30, 240), (0, 128, 96, 160))
    picture.save(source)
    sideways = tmp_path / 'sideways.png'
    picture.transpose(Image.Transpose.ROTATE_270).save(sideways)
    ffmpeg = require_tool('ffmpeg')

    def encode(*args):
        subprocess.run([ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', *map(str, args)],
                       check=True, capture_output=True, timeout=30)

    portrait, landscape, rotated = [tmp_path / name for name in ('portrait.mp4', 'landscape.mp4', 'rotated.mp4')]
    for image, video in ((source, portrait), (sideways, landscape)):
        encode('-loop', '1', '-i', image, '-t', '1', '-r', '8', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', video)
    try:
        encode('-display_rotation:v:0', '90', '-i', landscape, '-c', 'copy', rotated)
    except subprocess.CalledProcessError:
        # Older FFmpeg releases write the display matrix via the rotate metadata tag.
        encode('-i', landscape, '-c', 'copy', '-metadata:s:v:0', 'rotate=90', rotated)
    assert probe_video_geometry(landscape) == (160, 96)
    for video in (portrait, rotated):
        assert probe_video_geometry(video) == (96, 160)
        probe = probe_video(video)
        assert (probe.width, probe.height) == (96, 160)
        output = tmp_path / f'{video.stem}.webp'
        generate_flat_thumbnail(video, output, 1, 480)
        with Image.open(output) as cover:
            assert cover.size == (576, 240)
            for x in (72, 216, 360, 504):
                top = cover.getpixel((x, 20))
                bottom = cover.getpixel((x, 220))
                    assert top[0] > top[2] + 100
                    assert bottom[2] > bottom[0] + 100


def test_portrait_rebuild_targets_display_orientation_and_preserves_failures(monkeypatch, tmp_path):
    db = Database(tmp_path / 'data'); db.init()
    scanner = Scanner(db, tmp_path / 'data')
    scanner.cache_dir.mkdir()
    names = ('portrait', 'rotated', 'landscape', 'panorama', 'missing', 'failed')
    for index, name in enumerate(names, 1):
        source = tmp_path / f'{name}.mp4'; source.write_bytes(b'fixture')
        cover = scanner.cache_dir / f'{index}.webp'
        Image.new('RGB', (160, 90), 'red').save(cover)
        Image.new('RGB', (160, 90), 'red').save(scanner.cache_dir / f'{index}-preview.webp')
        with db.connect() as conn:
            conn.execute('''INSERT INTO videos(path,name,type,missing,width,height,aspect_ratio,
                thumb_status,thumb_version,thumb_revision,thumb_path,hdr_format,favorite)
                VALUES (?,?,?,?,160,90,1.777777,'ready',8,1,?,'HLG',1)''',
                (str(source), name, 'panorama' if name == 'panorama' else 'flat', int(name == 'missing'), str(cover)))
    with db.connect() as conn:
        before = [dict(row) for row in conn.execute('SELECT * FROM videos ORDER BY id')]
    old_files = {path.name: path.read_bytes() for path in scanner.cache_dir.glob('*.webp')}
    probed, rebuilt, progress = [], [], []

    def geometry(path):
        probed.append(path.stem)
        return (160, 90) if path.stem == 'landscape' else (90, 160)

    def generate(path, output, kind, duration, height):
        rebuilt.append(path.stem)
        assert kind == 'flat' and height == 480
        if path.stem == 'failed':
            raise VideoToolError('decode failed')
        Image.new('RGB', (270, 480), 'blue').save(output)

    monkeypatch.setattr('app.scanner.probe_video_geometry', geometry)
    monkeypatch.setattr('app.scanner.generate_thumbnail', generate)
    summary = asyncio.run(scanner.rebuild_portrait_thumbnails(Settings(thumbnail_resolution=480),
        lambda result, total: progress.append((result.seen, total))))
    assert (summary.seen, summary.indexed, summary.errors, summary.skipped) == (4, 2, 1, 1)
    assert probed == ['portrait', 'rotated', 'landscape', 'failed']
    assert rebuilt == ['portrait', 'rotated', 'failed']
    assert progress[-1] == (4, 4) and not scanner.is_running
    with db.connect() as conn:
        after = [dict(row) for row in conn.execute('SELECT * FROM videos ORDER BY id')]
    for old, new in zip(before, after):
        if new['name'] in {'portrait', 'rotated'}:
            assert (new['width'], new['height']) == (90, 160)
            assert new['thumb_revision'] > old['thumb_revision']
            assert new['hdr_format'] == old['hdr_format'] and new['favorite'] == 1
            with Image.open(new['thumb_path']) as cover:
                assert cover.size == (270, 480)
        else:
            assert new == old
            for suffix in ('', '-preview'):
                filename = f"{new['id']}{suffix}.webp"
                assert (scanner.cache_dir / filename).read_bytes() == old_files[filename]


def test_portrait_maintenance_status_and_duplicate_guard(monkeypatch, tmp_path):
    monkeypatch.setenv('APP_CONFIG_DIR', str(tmp_path / 'config'))
    monkeypatch.setenv('APP_DATA_DIR', str(tmp_path / 'data'))
    from app.main import create_app

    async def scenario():
        app = create_app()
        started, finish = asyncio.Event(), asyncio.Event()

        async def rebuild(settings, progress):
            progress(ScanSummary(seen=1, indexed=1), 3)
            started.set()
            await finish.wait()
            return ScanSummary(seen=3, indexed=2, skipped=1)

        monkeypatch.setattr(app.state.scanner, 'rebuild_portrait_thumbnails', rebuild)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post('/settings/refresh-portrait-thumbnails')
            assert response.status_code == 303 and 'started#maintenance' in response.headers['location']
            await started.wait()
            assert (await client.get('/settings/portrait-thumbnail-status')).json()['rebuilt'] == 1
            assert 'busy' in (await client.post('/settings/refresh-portrait-thumbnails')).headers['location']
            assert 'busy' in (await client.post('/settings/recheck-hdr-standards')).headers['location']
            finish.set()
            for _ in range(10):
                await asyncio.sleep(0)
                if not app.state.thumbnail_refresh_pending:
                    break
            assert not app.state.thumbnail_refresh_pending
            assert (await client.get('/settings/portrait-thumbnail-status')).json() == {
                'status': 'complete', 'checked': 3, 'rebuilt': 2, 'errors': 0, 'skipped': 1}
            assert '竖屏缩略图重载完成：检查 3 个，重建 2 个，失败 0 个' in (await client.get('/settings')).text

    asyncio.run(scenario())


def test_specific_hdr_labels_and_thumbnail_revision_reach_all_views(monkeypatch, tmp_path):
    monkeypatch.setenv('APP_CONFIG_DIR', str(tmp_path / 'config'))
    monkeypatch.setenv('APP_DATA_DIR', str(tmp_path / 'data'))
    from app.main import create_app

    async def scenario():
        app = create_app()
        cover = tmp_path / 'cover.webp'
        Image.new('RGB', (90, 160), 'green').save(cover)
        with app.state.db.connect() as conn:
            conn.execute('''INSERT INTO videos(path,name,mtime,type,width,height,thumb_status,thumb_version,
                thumb_revision,thumb_path,hdr_format,hdr_checked,bit_depth,favorite,folder)
                VALUES (?,'phone.mp4',?,'flat',90,160,'ready',8,123,?,'Dolby Vision',1,10,1,'Phone')''',
                (str(tmp_path / 'phone.mp4'), datetime.now().timestamp(), str(cover)))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            for route in ('/', '/library', '/library?view=favorites', '/video/1', '/video/1/play'):
                page = await client.get(route)
                assert page.status_code == 200
                assert '杜比视界' in page.text
                assert '>HDR</span>' not in page.text
                assert 'r=123' in page.text
                if route != '/video/1/play':
                    assert 'class="portrait-thumbnail"' in page.text
            assert 'r=123' in (await client.get('/library?view=folders')).text
            assert 'immutable' in (await client.get('/thumb/1.webp?r=123')).headers['cache-control']
            for route in ('/thumb/1.webp', '/thumb/1.webp?r=1'):
                assert (await client.get(route)).headers['cache-control'] == 'no-cache'

    asyncio.run(scenario())
