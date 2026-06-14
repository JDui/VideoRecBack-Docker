from app.video_types import detect_video_type


def test_detect_panorama_by_marker():
    assert detect_video_type("/media/trip_360.mp4") == "panorama"
    assert detect_video_type("/media/room-equirect.mkv") == "panorama"


def test_detect_flat_by_default():
    assert detect_video_type("/media/movie.mp4") == "flat"


def test_detect_panorama_by_two_to_one_aspect_ratio():
    assert detect_video_type("/media/movie.mp4", width=3840, height=1920) == "panorama"
    assert detect_video_type("/media/movie.mp4", width=1920, height=1080) == "flat"
