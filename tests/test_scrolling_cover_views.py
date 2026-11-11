from fastapi.testclient import TestClient

from app.config import Settings, save_settings
from app.main import create_app


def test_scrolling_cover_preview_works_without_a_video_library(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    app = create_app()
    response = TestClient(app).get("/settings")
    assert response.status_code == 200
    assert 'src="/static/cover-preview.svg"' in response.text
    assert 'name="scrolling_cover_cycle_ms"' in response.text
    assert '一轮总时长' in response.text
    assert 'action="/settings/generate-scrolling-thumbnails"' in response.text
    assert 'data-cover-preview' in response.text


def test_scrolling_cover_applies_to_lists_and_preserves_full_detail_cover(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    app = create_app()
    save_settings(app.state.config_dir, Settings(
        scan_interval_hours=0, scrolling_cover_enabled=True, scrolling_cover_cycle_ms=3200,
    ))
    with app.state.db.connect() as conn:
        conn.executemany(
            """INSERT INTO videos(id, path, name, type, width, height, mtime, thumb_status, thumb_path, favorite)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'ready', ?, 1)""",
            [(1, 'quad.mp4', 'quad.mp4', 'flat', 1920, 1080, 1, 'quad.webp'),
             (2, 'triple.mp4', 'triple.mp4', 'flat', 1080, 1920, 2, 'triple.webp'),
             (3, 'panorama.mp4', 'panorama.mp4', 'panorama', 3840, 1920, 3, 'panorama.webp')],
        )
    client = TestClient(app)
    for url in ('/', '/library', '/library?view=favorites'):
        page = client.get(url)
        assert page.status_code == 200
        assert 'data-scrolling-cover-enabled="1"' in page.text
        assert 'data-scrolling-cover-cycle-ms="3200"' in page.text
        assert 'data-cover-sequence-src="/scrolling-thumb/2.webp?' in page.text
        assert 'data-scrolling-cover data-cover-columns="3" data-cover-rows="1"' in page.text
        assert 'data-scrolling-cover data-cover-columns="2" data-cover-rows="2"' in page.text
    preview = client.get('/settings')
    assert preview.status_code == 200
    assert '/thumb/2.webp' in preview.text
    assert '视频库预览' in preview.text
    for video_id in (1, 2, 3):
        detail = client.get(f'/video/{video_id}')
        assert detail.status_code == 200
        assert f'/thumb/{video_id}.webp' in detail.text
        assert 'data-scrolling-cover data-cover-columns=' not in detail.text
        assert 'is-scrolling-cover' not in detail.text
        assert '/scrolling-thumb/' not in detail.text
    panorama = client.get('/library?type=panorama')
    assert panorama.status_code == 200
    assert 'data-scrolling-cover data-cover-columns=' not in panorama.text
