import pytest

from pixel_tile_compiler.gui.canvas import CanvasState
from pixel_tile_compiler.gui.policy import resolve_character_gui_profile


def test_canvas_state_uses_integer_zoom_and_pixel_coordinates():
    state = CanvasState(zoom=8)

    assert state.grid_spacing == 8
    assert state.pixel_at(23, 41) == (2, 5)
    assert state.zoom_in() == 16
    assert state.zoom_out() == 8


def test_canvas_state_tracks_square_canvas_and_clamps_coordinates():
    state = CanvasState(zoom=4, canvas_size=(128, 128))

    assert state.canvas_size == (128, 128)
    assert state.pixel_at(-1, -1) == (0, 0)
    assert state.pixel_at(9999, 9999) == (127, 127)
    assert state.set_canvas_size((64, 64)) == (64, 64)
    assert state.pixel_at(9999, 9999) == (63, 63)


def test_character_gui_profile_defaults_to_native_128_b24():
    profile = resolve_character_gui_profile()

    assert profile.canvas_size == (128, 128)
    assert profile.palette_budget == 24
    assert profile.detail_level == "balanced"
    assert profile.tile_mode == "object"
    assert profile.pixelization_mode == "nearest"


def test_character_gui_profile_accepts_only_square_experimental_sizes():
    assert resolve_character_gui_profile((64, 64)).canvas_size == (64, 64)

    with pytest.raises(ValueError, match="square"):
        resolve_character_gui_profile((128, 96))
