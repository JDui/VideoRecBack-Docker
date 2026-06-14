from app.video_types import detect_video_type


def test_detect_panorama_from_parent_directory():
    assert detect_video_type("/media/PANARAMA/aaa/VID_20201011_153357_00_002.mp4") == "panorama"

