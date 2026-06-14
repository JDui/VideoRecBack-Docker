from datetime import datetime
from importlib import import_module


def load_main(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "data"))
    return import_module("app.main")


def test_calendar_defaults_to_year(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)

    class Request:
        query_params = {"view": "calendar"}

    filters = main.read_filters(Request())

    assert filters["view"] == "calendar"
    assert filters["calendar_zoom"] == "year"


def test_timeline_rail_groups_by_quarter(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    rows = [
        {"mtime": datetime(2026, 1, 10).timestamp()},
        {"mtime": datetime(2026, 3, 20).timestamp()},
        {"mtime": datetime(2025, 11, 5).timestamp()},
    ]

    rail = main.build_timeline_rail(rows)

    assert rail == [
        {
            "year": 2026,
            "quarters": [
                {"year": 2026, "quarter": 1, "label": "Q1", "count": 2, "labels": []},
                {"year": 2026, "quarter": 2, "label": "Q2", "count": 0, "labels": []},
                {"year": 2026, "quarter": 3, "label": "Q3", "count": 0, "labels": []},
                {"year": 2026, "quarter": 4, "label": "Q4", "count": 0, "labels": []},
            ],
        },
        {
            "year": 2025,
            "quarters": [
                {"year": 2025, "quarter": 1, "label": "Q1", "count": 0, "labels": []},
                {"year": 2025, "quarter": 2, "label": "Q2", "count": 0, "labels": []},
                {"year": 2025, "quarter": 3, "label": "Q3", "count": 0, "labels": []},
                {"year": 2025, "quarter": 4, "label": "Q4", "count": 1, "labels": []},
            ],
        },
    ]


def test_timeline_rail_attaches_labels(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    rows = [{"mtime": datetime(2026, 1, 10).timestamp()}]

    rail = main.build_timeline_rail(rows, {(2026, 1): [{"label": "春节", "color": "#ff0000"}]})

    assert rail[0]["quarters"][0]["labels"] == [{"label": "春节", "color": "#ff0000"}]


def test_timeline_rail_fills_empty_quarters(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    rows = [
        {"mtime": datetime(2026, 1, 10).timestamp()},
        {"mtime": datetime(2025, 7, 8).timestamp()},
    ]

    rail = main.build_timeline_rail(rows)

    assert [
        (year["year"], quarter["quarter"], quarter["count"])
        for year in rail
        for quarter in year["quarters"]
    ] == [
        (2026, 1, 1),
        (2026, 2, 0),
        (2026, 3, 0),
        (2026, 4, 0),
        (2025, 1, 0),
        (2025, 2, 0),
        (2025, 3, 1),
        (2025, 4, 0),
    ]


def test_timeline_groups_include_quarter_anchor(monkeypatch, tmp_path):
    main = load_main(monkeypatch, tmp_path)
    rows = [{"mtime": datetime(2026, 7, 8).timestamp()}]

    groups = main.group_by_date(rows)

    assert groups[0]["year"] == 2026
    assert groups[0]["quarter"] == 3
