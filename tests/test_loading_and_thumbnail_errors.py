from importlib import import_module

from fastapi.testclient import TestClient


def make_app(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    return import_module("app.main").create_app()


def test_streaming_loader_precedes_page_data(monkeypatch, tmp_path):
    app = make_app(monkeypatch, tmp_path)
    response = TestClient(app).get("/library")
    assert response.status_code == 200
    assert response.headers["x-accel-buffering"] == "no"
    assert response.text.count("<!doctype html>") == 1
    assert response.text.index("VideoRecBackLoading") < response.text.index("/static/styles.css")
    assert 'data-player-frame' in response.text
    assert '/static/inline-player.js' in response.text
    assert '/static/navigation.js' in response.text


def test_thumbnail_errors_pages_only_existing_failed_videos(monkeypatch, tmp_path):
    app = make_app(monkeypatch, tmp_path)
    with app.state.db.connect() as conn:
        conn.executemany(
            "INSERT INTO videos(path, name, mtime, missing, thumb_status, thumb_error) VALUES (?, ?, ?, 0, 'error', ?)",
            [(f"/media/{i}.mp4", f"failed-{i}.mp4", i, "decoder failed") for i in range(53)],
        )
        conn.execute("INSERT INTO videos(path, name, mtime, missing, thumb_status) VALUES ('/ready.mp4', 'ready', 100, 0, 'ready')")
        conn.execute("INSERT INTO videos(path, name, mtime, missing, thumb_status) VALUES ('/missing.mp4', 'missing', 100, 1, 'error')")
    client = TestClient(app)
    first = client.get('/home/thumbnail-errors').json()
    assert first['total'] == 53
    assert len(first['videos']) == 50
    assert first['next_offset'] == 50
    assert first['videos'][0]['name'] == 'failed-52.mp4'
    assert first['videos'][0]['thumb_error'] == 'decoder failed'
    second = client.get('/home/thumbnail-errors?offset=50').json()
    assert len(second['videos']) == 3
    assert second['next_offset'] is None
    assert client.get('/home/thumbnail-errors?offset=-1').status_code == 400
    assert client.get('/home/thumbnail-errors?offset=invalid').status_code == 422


def test_empty_thumbnail_errors(monkeypatch, tmp_path):
    client = TestClient(make_app(monkeypatch, tmp_path))
    assert client.get('/home/thumbnail-errors').json() == {'total': 0, 'videos': [], 'next_offset': None}
    home = client.get('/').text
    assert 'data-thumbnail-errors-dialog' in home
    assert 'data-home-video' not in home
    assert 'home-player-shell' in home


def test_loader_is_yielded_before_query_starts(monkeypatch, tmp_path):
    import asyncio
    from starlette.requests import Request

    app = make_app(monkeypatch, tmp_path)
    main = import_module('app.main')
    query = main.query_videos
    called = []

    def tracked_query(*args, **kwargs):
        called.append(True)
        return query(*args, **kwargs)

    monkeypatch.setattr(main, 'query_videos', tracked_query)
    endpoint = next(route.endpoint for route in app.routes if getattr(route, 'path', None) == '/library')
    request = Request({'type': 'http', 'method': 'GET', 'path': '/library', 'scheme': 'http', 'server': ('localhost', 80), 'query_string': b'', 'headers': [], 'app': app})

    async def read_stream():
        response = await endpoint(request)
        first = await anext(response.body_iterator)
        assert 'VideoRecBackLoading' in first
        assert not called
        remaining = await anext(response.body_iterator)
        assert called
        assert 'data-timeline-root' not in remaining
        assert 'class="library-pane"' in remaining

    asyncio.run(read_stream())


def test_stream_failure_offers_retry_instead_of_stuck_loader(monkeypatch, tmp_path):
    app = make_app(monkeypatch, tmp_path)

    def fail(*args, **kwargs):
        raise RuntimeError('temporary database failure')

    monkeypatch.setattr(import_module('app.main'), 'home_content', fail)
    response = TestClient(app).get('/')
    assert '载入失败，请刷新重试' in response.text
    assert '重新载入' in response.text
    assert 'temporary database failure' not in response.text
