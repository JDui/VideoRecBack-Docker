import asyncio
from types import SimpleNamespace

import httpx

from app.db import Database
from app.scanner import Scanner, ScanSummary
from app.thumbnails import ProbeResult, VideoToolError


def test_hdr_recheck_updates_only_hdr_and_preserves_failed_records(monkeypatch, tmp_path):
    db = Database(tmp_path / 'data')
    db.init()
    scanner = Scanner(db, tmp_path / 'data')
    for name in ('hdr', 'sdr', 'failed', 'missing'):
        path = tmp_path / f'{name}.mp4'
        path.write_bytes(b'fixture')
        with db.connect() as conn:
            conn.execute(
                """INSERT INTO videos(path,name,type,mtime,width,height,duration_seconds,
                   favorite,thumb_status,thumb_path,thumb_version,hdr_format,hdr_checked,missing)
                   VALUES (?,?,'flat',1,640,360,3,1,'ready','cached.webp',8,'PQ',1,?)""",
                (str(path), name, int(name == 'missing')),
            )
    probed = []

    def probe(path):
        probed.append(path.stem)
        if path.stem == 'failed':
            raise VideoToolError('unreadable')
        return ProbeResult(20, 3840, 1920, hdr_format='HDR10' if path.stem == 'hdr' else None)

    monkeypatch.setattr('app.scanner.probe_video', probe)
    monkeypatch.setattr('app.scanner.generate_thumbnail', lambda *args: (_ for _ in ()).throw(AssertionError('must not rebuild')))
    with db.connect() as conn:
        before = [dict(row) for row in conn.execute('SELECT * FROM videos ORDER BY id')]
    result = asyncio.run(scanner.recheck_all_video_metadata(hdr_only=True))
    with db.connect() as conn:
        after = [dict(row) for row in conn.execute('SELECT * FROM videos ORDER BY id')]
    assert (result.seen, result.indexed, result.errors) == (3, 2, 1)
    assert probed == ['hdr', 'sdr', 'failed']
    assert [row['hdr_format'] for row in after] == ['HDR10', None, 'PQ', 'PQ']
    for old, new in zip(before, after):
        assert {k: v for k, v in old.items() if k not in {'hdr_format', 'hdr_checked', 'updated_at'}} == {
            k: v for k, v in new.items() if k not in {'hdr_format', 'hdr_checked', 'updated_at'}
        }
    assert not scanner.is_running


def test_hdr_route_runs_in_background_and_rejects_duplicate_work(monkeypatch, tmp_path):
    monkeypatch.setenv('APP_CONFIG_DIR', str(tmp_path / 'config'))
    monkeypatch.setenv('APP_DATA_DIR', str(tmp_path / 'data'))
    from app.main import create_app

    async def scenario():
        app = create_app()
        started, finish = asyncio.Event(), asyncio.Event()

        async def recheck(*, hdr_only=False):
            assert hdr_only
            started.set()
            await finish.wait()
            return ScanSummary(seen=3, indexed=2, errors=1)

        monkeypatch.setattr(app.state.scanner, 'recheck_all_video_metadata', recheck)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            app.state.manual_scan_pending = True
            assert 'busy' in (await client.post('/settings/recheck-hdr-standards')).headers['location']
            assert not started.is_set()
            app.state.manual_scan_pending = False
            response = await client.post('/settings/recheck-hdr-standards')
            assert response.status_code == 303
            await started.wait()
            assert (await client.get('/settings/hdr-status')).json()['status'] == 'running'
            assert 'busy' in (await client.post('/settings/recheck-hdr-standards')).headers['location']
            assert 'running' in (await client.post('/settings/refresh-thumbnails')).headers['location']
            assert not (await client.post('/scan', headers={'accept': 'application/json'})).json()['started']
            finish.set()
            for _ in range(10):
                await asyncio.sleep(0)
                if not app.state.metadata_recheck_pending:
                    break
            assert (await client.get('/settings/hdr-status')).json() == {'status': 'complete', 'checked': 2, 'errors': 1}
            assert not app.state.metadata_recheck_pending
            page = await client.get('/settings')
            assert 'HDR 标准校验完成：成功 2 个，失败 1 个' in page.text
            assert 'action="/settings/recheck-hdr-standards"' in page.text

    asyncio.run(scenario())


def test_hdr_job_releases_busy_state_on_failure(monkeypatch, tmp_path):
    monkeypatch.setenv('APP_CONFIG_DIR', str(tmp_path / 'config'))
    monkeypatch.setenv('APP_DATA_DIR', str(tmp_path / 'data'))
    from app.main import run_hdr_recheck

    async def fail(**kwargs):
        raise RuntimeError('probe worker failed')

    state = SimpleNamespace(metadata_recheck_pending=True, scanner=SimpleNamespace(recheck_all_video_metadata=fail))
    asyncio.run(run_hdr_recheck(SimpleNamespace(state=state)))
    assert state.hdr_recheck_status == {'status': 'failed'}
    assert not state.metadata_recheck_pending


def test_incomplete_frame_probe_keeps_existing_hdr_label(monkeypatch, tmp_path):
    db = Database(tmp_path / 'data'); db.init()
    scanner = Scanner(db, tmp_path / 'data')
    path = tmp_path / 'hdr.mp4'; path.write_bytes(b'fixture')
    with db.connect() as conn:
        conn.execute("INSERT INTO videos(path,name,hdr_format,hdr_checked) VALUES (?,?,'HDR10+',1)", (str(path), path.name))
    monkeypatch.setattr('app.scanner.probe_video', lambda path: ProbeResult(1, 192, 64, hdr_format='PQ', hdr_probe_complete=False))
    result = asyncio.run(scanner.recheck_all_video_metadata(hdr_only=True))
    assert (result.indexed, result.errors) == (0, 1)
    with db.connect() as conn:
        assert conn.execute('SELECT hdr_format FROM videos').fetchone()[0] == 'HDR10+'
