import asyncio
from types import SimpleNamespace

import pytest

from app.config import Settings


def test_background_maintenance_disables_full_scan(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    from app import main

    sleeps = []

    class Scanner:
        def __init__(self):
            self.processed = 0
            self.scanned = 0

        async def process_queue(self, settings):
            self.processed += 1

        async def scan(self, settings):
            self.scanned += 1

    async def sleep(delay):
        sleeps.append(delay)
        raise asyncio.CancelledError

    scanner = Scanner()
    app = SimpleNamespace(state=SimpleNamespace(config_dir=tmp_path / "config", scanner=scanner))
    monkeypatch.setattr(main, "load_settings", lambda config_dir: Settings(scan_interval_hours=0))
    monkeypatch.setattr(main.asyncio, "sleep", sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main.background_maintenance(app))

    assert scanner.processed == 1
    assert scanner.scanned == 0
    assert sleeps == [300]


def test_background_maintenance_runs_after_positive_interval(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    from app import main

    sleeps = []

    class Scanner:
        def __init__(self):
            self.processed = 0
            self.scanned = 0

        async def process_queue(self, settings):
            self.processed += 1

        async def scan(self, settings):
            self.scanned += 1

    async def sleep(delay):
        sleeps.append(delay)
        if len(sleeps) > 1:
            raise asyncio.CancelledError

    scanner = Scanner()
    app = SimpleNamespace(state=SimpleNamespace(config_dir=tmp_path / "config", scanner=scanner))
    monkeypatch.setattr(main, "load_settings", lambda config_dir: Settings(scan_interval_hours=2))
    monkeypatch.setattr(main.asyncio, "sleep", sleep)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(main.background_maintenance(app))

    assert scanner.processed == 2
    assert scanner.scanned == 1
    assert sleeps == [7200, 7200]
