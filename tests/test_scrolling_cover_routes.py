import asyncio

import httpx
from fastapi.testclient import TestClient
from PIL import Image

from app.main import create_app
from app.scanner import ScanSummary


def test_dedicated_sequence_route_never_replaces_main_cover_and_revalidates_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    app = create_app()
    cache = app.state.data_dir / "cache"
    cache.mkdir(exist_ok=True)
    main = cache / "1.webp"
    Image.new("RGB", (160, 90), "red").save(main)
    original = main.read_bytes()
    with app.state.db.connect() as conn:
        conn.execute("""INSERT INTO videos(id,path,name,type,thumb_status,thumb_path)
                        VALUES (1,'unused.mp4','unused.mp4','flat','ready',?)""", (str(main),))
    client = TestClient(app)
    missing = client.get('/scrolling-thumb/1.webp')
    assert missing.status_code == 404 and missing.headers['cache-control'] == 'no-store'
    sequence = cache / '1-scrolling.webp'
    Image.new("RGB", (624, 1104), "blue").save(sequence)
    served = client.get('/scrolling-thumb/1.webp')
    assert served.status_code == 200 and served.headers['content-type'] == 'image/webp'
    assert served.headers['cache-control'] == 'no-cache'
    assert served.content == sequence.read_bytes()
    assert client.get('/thumb/1.webp').content == original
    with app.state.db.connect() as conn:
        conn.execute("UPDATE videos SET type='panorama' WHERE id=1")
    assert client.get('/scrolling-thumb/1.webp').status_code == 404


def test_sequence_maintenance_reports_progress_and_blocks_overlapping_jobs(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    app = create_app()

    async def scenario():
        started, finish = asyncio.Event(), asyncio.Event()

        async def generate(settings, progress):
            progress(ScanSummary(seen=1, indexed=1), 2)
            started.set()
            await finish.wait()
            return ScanSummary(seen=2, indexed=1, errors=1)

        monkeypatch.setattr(app.state.scanner, 'rebuild_scrolling_thumbnails', generate)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post('/settings/generate-scrolling-thumbnails')
            assert response.status_code == 303 and 'started#maintenance' in response.headers['location']
            await started.wait()
            assert (await client.get('/settings/scrolling-thumbnail-status')).json()['rebuilt'] == 1
            duplicate = await client.post('/settings/generate-scrolling-thumbnails')
            assert 'busy#maintenance' in duplicate.headers['location']
            finish.set()
            for _ in range(10):
                await asyncio.sleep(0)
                if not app.state.thumbnail_refresh_pending:
                    break
            assert not app.state.thumbnail_refresh_pending
            assert (await client.get('/settings/scrolling-thumbnail-status')).json() == {
                'status': 'complete', 'checked': 2, 'rebuilt': 1, 'errors': 1, 'skipped': 0,
            }
            assert '滚动封面生成完成' in (await client.get('/settings')).text

    asyncio.run(scenario())
