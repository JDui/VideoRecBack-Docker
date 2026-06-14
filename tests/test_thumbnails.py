from app.thumbnails import midpoint, sample_times


def test_sample_times_for_flat_video():
    assert sample_times(100) == [2.0, 25.0, 50.0, 75.0]


def test_sample_times_for_short_video():
    assert sample_times(1) == [0.75, 0.25, 0.5, 0.75]


def test_midpoint():
    assert midpoint(100) == 50
