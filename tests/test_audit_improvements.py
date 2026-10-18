from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from html import unescape
from importlib import import_module
from urllib.parse import parse_qs, urlsplit
import re

import pytest
from fastapi.testclient import TestClient

from app.dates import archive_date, date_boundary
from app.home import home_content, memory_date_values
from app.thumbnail_errors import describe_thumbnail_error


@pytest.fixture
def archive_app(monkeypatch, tmp_path):
    monkeypatch.setenv('APP_CONFIG_DIR', str(tmp_path / 'config'))
    monkeypatch.setenv('APP_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('APP_TIMEZONE', 'Asia/Shanghai')
    return import_module('app.main').create_app()


def insert_video(app, video_id, timestamp, *, name=None, folder='东京/Action3', status='ready', missing=0):
    with app.state.db.connect() as conn:
        conn.execute('''INSERT INTO videos(id, path, name, folder, relative_path, mtime,
                        thumb_status, missing, is_10bit, thumb_error)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?)''',
                     (video_id, f'/media/{video_id}.mp4', name or f'clip-{video_id}.mp4', folder,
                      f'{folder}/{name or video_id}', timestamp, status, missing,
                      'moov atom not found' if status == 'error' else None))


def test_archive_timezone_aligns_memories_calendar_dates_and_filters(archive_app, monkeypatch):
    main = import_module('app.main')
    instant = datetime(2025, 12, 31, 16, 5, tzinfo=timezone.utc).timestamp()
    insert_video(archive_app, 1, instant)
    now = datetime(2026, 12, 31, 16, 5, tzinfo=timezone.utc)
    assert archive_date(instant) == '2026-01-01'
    content = home_content(archive_app.state.db, now)
    assert content['today_short'] == '01月01日'
    assert [row['id'] for row in content['memories']] == [1]
    monkeypatch.setattr(main, 'memory_date_values', lambda: memory_date_values(now))
    filters = main.read_filters(type('Request', (), {'query_params': {
        'view': 'calendar', 'calendar_year': '2026', 'calendar_month': '1',
        'calendar_zoom': 'day', 'date_from': '2026-01-01', 'date_to': '2026-01-01'}})())
    rows = main.query_videos(archive_app.state.db, filters)
    assert [row['timeline_day_anchor'] for row in rows] == ['timeline-2026-01-01']
    assert main.build_timeline_date_index(rows)[0]['date'] == '2026-01-01'
    assert main.build_calendar_model(rows, filters)['months'][0]['label'] == '2026年01月'
    filters['view'] = 'memories'
    assert [row['id'] for row in main.query_videos(archive_app.state.db, filters)] == [1]


@pytest.mark.parametrize('day,hours', [('2026-03-08', 23), ('2026-11-01', 25)])
def test_date_range_uses_next_local_midnight_during_dst(monkeypatch, day, hours):
    monkeypatch.setenv('APP_TIMEZONE', 'America/New_York')
    assert date_boundary(day, next_day=True) - date_boundary(day) == hours * 3600


def test_search_is_literal_and_preserved_with_context(archive_app):
    insert_video(archive_app, 1, 1, name='take_100%.mp4')
    insert_video(archive_app, 2, 2, name='takeX100extra.mp4')
    client = TestClient(archive_app)
    response = client.get('/library', params={'q': '_100%', 'view': 'folders', 'folder': '东京', 'type': 'flat'})
    assert response.status_code == 200
    assert '1 个视频' in response.text
    assert '2 个视频' not in response.text
    assert 'value="_100%"' in response.text
    assert 'data-library-search' in response.text
    links = re.findall(r'<a href="([^"]+)"[^>]*>日历</a>', response.text)
    params = parse_qs(urlsplit(unescape(links[0])).query)
    assert params['q'] == ['_100%']
    assert params['type'] == ['flat']
    assert 'onchange="this.form.submit()"' not in response.text
    assert '清空搜索与筛选' in response.text
    clear_link = re.search(r'<a href="([^"]+)">清空搜索与筛选</a>', response.text).group(1)
    cleared = parse_qs(urlsplit(unescape(clear_link)).query)
    assert cleared['folder'] == ['东京']
    assert 'q' not in cleared and 'type' not in cleared


def test_invalid_and_reversed_dates_do_not_break_library(archive_app):
    insert_video(archive_app, 1, 1)
    client = TestClient(archive_app)
    assert '没有匹配的视频' in client.get('/library?date_from=2026-10-18&date_to=2026-10-17').text
    invalid = client.get('/library?date_from=2026-02-30&calendar_year=9999')
    assert invalid.status_code == 200
    assert 'clip-1.mp4' in invalid.text


def test_thumbnail_retry_is_atomic_and_preserves_ready_or_active_work(archive_app):
    for index, status, missing in [(1, 'error', 0), (2, 'ready', 0), (3, 'error', 1), (4, 'error', 0)]:
        insert_video(archive_app, index, index, status=status, missing=missing)
    with archive_app.state.db.connect() as conn:
        conn.execute("INSERT INTO media_jobs(video_id, status, attempts) VALUES (4, 'running', 2)")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: archive_app.state.scanner.retry_failed_thumbnails(), range(2)))
    assert sorted(len(ids) for ids in results) == [0, 1]
    with archive_app.state.db.connect() as conn:
        assert conn.execute('SELECT thumb_status FROM videos WHERE id=2').fetchone()[0] == 'ready'
        assert conn.execute('SELECT thumb_status FROM videos WHERE id=3').fetchone()[0] == 'error'
        assert tuple(conn.execute('SELECT status, attempts FROM media_jobs WHERE video_id=4').fetchone()) == ('running', 2)
        assert conn.execute('SELECT COUNT(*) FROM media_jobs WHERE video_id=1').fetchone()[0] == 1


def test_retry_api_exposes_queue_success_and_failure_states(archive_app):
    insert_video(archive_app, 1, 1, status='error')
    insert_video(archive_app, 2, 2)
    client = TestClient(archive_app)
    first = client.get('/home/thumbnail-errors').json()['videos'][0]
    assert first['reason'] == '无法读取视频容器信息'
    assert first['failed_at_label'] == '此前失败，时间未记录'
    assert client.post('/home/thumbnail-errors/retry', data={'video_id': 1}).json()['queued'] == 1
    assert client.post('/home/thumbnail-errors/retry', data={'video_id': 1}).json()['queued'] == 0
    state = client.get('/home/thumbnail-status?ids=1').json()['videos'][0]
    assert state['job_status'] == 'pending' and state['thumb_status'] == 'pending'
    scanner = archive_app.state.scanner
    settings = import_module('app.config').Settings()
    for _ in range(3):
        scanner._process_media_jobs_sync(settings, 1)
    failed = client.get('/home/thumbnail-status?ids=1').json()['videos'][0]
    assert failed['job_status'] == 'error' and failed['thumb_status'] == 'error'
    assert failed['thumb_failed_at']
    assert failed['reason'] == '暂时无法读取原视频'
    assert client.post('/home/thumbnail-errors/retry', data={'video_id': 1}).json()['queued'] == 1
    with archive_app.state.db.connect() as conn:
        conn.execute("UPDATE videos SET thumb_status='ready', thumb_error=NULL WHERE id=1")
        conn.execute('DELETE FROM media_jobs WHERE video_id=1')
    assert client.get('/home/thumbnail-status?ids=1').json()['videos'][0]['thumb_status'] == 'ready'
    assert client.post('/home/thumbnail-errors/retry', data={'video_id': 2}).json()['queued'] == 0
    assert client.post('/home/thumbnail-errors/retry', data={'video_id': 999}).status_code == 404
    assert client.get('/home/thumbnail-status?ids=bad').status_code == 400
    assert client.get('/home/thumbnail-status?ids=0').status_code == 400


def test_calendar_disabled_levels_are_not_actionable_links(archive_app):
    insert_video(archive_app, 1, 1)
    html = TestClient(archive_app).get('/library?view=calendar').text
    assert '<span class="disabled" aria-disabled="true"' in html
    assert 'href="#"' not in re.search(r'<div class="calendar-zoom">(.*?)</div>', html, re.S).group(1)
    assert '查看月份' in html


def test_player_settings_group_metadata_without_sequence_navigation(archive_app):
    insert_video(archive_app, 2, 5, folder='旅行/东京')
    client = TestClient(archive_app)
    page = client.get('/video/2/play', params={'return': '/library?view=favorites#timeline-video-2'}).text
    menu = re.search(r'<details[^>]+data-player-settings-menu>(.*?)</details>', page, re.S).group(1)
    assert '文件修改日期' in menu
    assert '所属目录' in menu and '旅行/东京' in menu
    assert 'href="/video/2"' in menu
    assert 'player-sequence' not in page
    assert 'data-player-neighbor' not in page
    assert 'data-player-archive-info' not in page
    assert 'data-return-url="/library?view=favorites#timeline-video-2"' in page
    assert 'data-player-settings-menu' not in client.get('/video/2/play?embed=1').text
    main = import_module('app.main')
    assert main.safe_return_url('//example.com') == '/library'
    assert main.safe_return_url('///example.com') == '/library'
    assert main.safe_return_url('http://[') == '/library'
    assert main.safe_return_url('/\\example.com') == '/library'
    assert main.safe_return_url('/library?view=favorites') == '/library?view=favorites'


@pytest.mark.parametrize('error,reason', [
    ('WebPAnimEncoderAssemble failed', '封面图像编码失败'),
    ('Invalid NAL unit size', '视频解码失败'),
    ('moov atom not found', '无法读取视频容器信息'),
])
def test_thumbnail_error_summaries(error, reason):
    assert describe_thumbnail_error(error)['reason'] == reason


def test_folder_results_count_only_selected_tree(archive_app):
    insert_video(archive_app, 1, 1, folder='旅行')
    insert_video(archive_app, 2, 2, folder='旅行/东京')
    insert_video(archive_app, 3, 3, folder='旅行备份')
    page = TestClient(archive_app).get('/library', params={'view': 'folders', 'folder': '旅行', 'q': 'clip'}).text
    assert '2 个结果' in page
    assert 'clip-3.mp4' not in page
