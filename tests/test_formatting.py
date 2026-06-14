from app.formatting import format_duration, format_size


def test_format_duration():
    assert format_duration(65) == "1:05"
    assert format_duration(3661) == "1:01:01"
    assert format_duration(None) == "未知"


def test_format_size():
    assert format_size(0) == "0 B"
    assert format_size(1024 * 1024) == "1.0 MB"

