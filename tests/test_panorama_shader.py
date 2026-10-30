from pathlib import Path


def test_panorama_shader_preserves_left_to_right_equirectangular_orientation():
    player_source = (Path(__file__).parents[1] / "app" / "static" / "player.js").read_text(encoding="utf-8")

    assert "fract(0.5 + lon / (2.0 * PI))" in player_source
    assert "fract(0.5 - lon / (2.0 * PI))" not in player_source
