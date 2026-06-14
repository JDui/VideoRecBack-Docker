from app.media import parse_range


def test_parse_range_open_ended():
    assert parse_range("bytes=10-", 100) == (10, 99)


def test_parse_range_suffix():
    assert parse_range("bytes=-20", 100) == (80, 99)


def test_parse_range_clamps_end():
    assert parse_range("bytes=10-120", 100) == (10, 99)

