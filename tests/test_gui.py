from pixel_tile_compiler.gui.canvas import CanvasState


def test_canvas_state_uses_integer_zoom_and_pixel_coordinates():
    state = CanvasState(zoom=8)

    assert state.grid_spacing == 8
    assert state.pixel_at(23, 41) == (2, 5)
    assert state.zoom_in() == 16
    assert state.zoom_out() == 8
