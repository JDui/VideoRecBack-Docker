from datetime import datetime
from html.parser import HTMLParser
from importlib import import_module

from fastapi.testclient import TestClient

from app.config import Settings, save_settings


class HomeCardParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.cards = []
        self.current_card = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "article" and "home-video" in values.get("class", "").split():
            self.current_card = {"timeline_url": values.get("data-home-timeline-url")}
            self.cards.append(self.current_card)
        elif self.current_card is not None and "data-home-video" in values:
            self.current_card["video_id"] = int(values["data-home-video"])

    def handle_endtag(self, tag):
        if tag == "article":
            self.current_card = None


def test_home_random_cards_expose_timeline_dates_when_date_display_is_disabled(monkeypatch, tmp_path):
    config_dir = tmp_path / "config"
    monkeypatch.setenv("APP_CONFIG_DIR", str(config_dir))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    save_settings(config_dir, Settings(scan_interval_hours=0, show_date=False))
    app = import_module("app.main").create_app()
    dates = [datetime(2025, 12, 31, 23, 59), datetime(2026, 1, 1, 0, 1), datetime(2024, 2, 29, 12)]
    with app.state.db.connect() as conn:
        conn.executemany(
            "INSERT INTO videos(path, name, mtime, missing, type, size_bytes) VALUES (?, ?, ?, 0, 'flat', 1)",
            [(str(tmp_path / f"video-{index}.mp4"), f"video-{index}.mp4", date.timestamp())
             for index, date in enumerate(dates)],
        )

    expected = {index + 1: f"/library#timeline-{date:%Y-%m-%d}" for index, date in enumerate(dates)}
    with TestClient(app) as client:
        for url in ("/", "/home/random"):
            response = client.get(url)
            assert response.status_code == 200
            parser = HomeCardParser()
            parser.feed(response.json()["html"] if url == "/home/random" else response.text)
            assert {card["video_id"] for card in parser.cards} == set(expected)
            assert all(card["timeline_url"] == expected[card["video_id"]] for card in parser.cards)
            if url == "/home/random":
                assert len(parser.cards) == 3
