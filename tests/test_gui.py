import pytest

from pixel_tile_compiler.gui.canvas import CanvasState
from pixel_tile_compiler.gui.input import first_supported_image_path
from pixel_tile_compiler.gui.policy import (
    build_output_path,
    resolve_character_animation_gui_profile,
    resolve_character_gui_profile,
    resolve_terrain_gui_profile,
)


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


def test_character_animation_gui_profile_scales_shared_layout_for_64_and_128():
    profile_64 = resolve_character_animation_gui_profile((64, 64))
    profile_128 = resolve_character_animation_gui_profile((128, 128))

    assert profile_64.canvas_size == (64, 64)
    assert profile_64.frame_count == 4
    assert profile_64.fit_within == (54, 54)
    assert profile_64.bottom_margin == 6
    assert profile_128.canvas_size == (128, 128)
    assert profile_128.fit_within == (108, 108)
    assert profile_128.bottom_margin == 12


def test_first_supported_image_path_ignores_non_images_and_missing_files(tmp_path):
    unsupported = tmp_path / "notes.txt"
    unsupported.write_text("not an image", encoding="utf-8")
    source = tmp_path / "character.PNG"
    source.write_bytes(b"placeholder")

    assert first_supported_image_path(
        [tmp_path / "missing.png", unsupported, source]
    ) == source


def test_build_output_path_uses_selected_root_and_source_stem(tmp_path):
    source = tmp_path / "アリア_MAP駒_正面_生成原画_v3.png"

    assert build_output_path(
        tmp_path / "compiled",
        source,
        purpose="character",
        canvas_size=(128, 128),
    ) == tmp_path / "compiled" / source.stem / "character_128x128_b24"

    assert build_output_path(
        tmp_path / "compiled",
        source,
        purpose="character_animation",
        canvas_size=(128, 128),
    ) == tmp_path / "compiled" / source.stem / "character_animation_128x128_b24"


def test_terrain_gui_profile_defaults_to_source_preserving_without_repeat_optimization():
    profile = resolve_terrain_gui_profile()

    assert profile.pixelization_mode == "nearest"
    assert profile.label == "元絵を保持"
    assert profile.repeat_opt_enabled is False

    region = resolve_terrain_gui_profile("region")
    assert region.pixelization_mode == "region"
    assert region.label == "領域を整理"


def test_terrain_output_path_identifies_mode_palette_and_repeat_setting(tmp_path):
    source = tmp_path / "grass.png"

    nearest_off = build_output_path(
        tmp_path / "compiled",
        source,
        purpose="terrain",
        canvas_size=(64, 64),
        pixelization_mode="nearest",
        palette_budget=24,
        repeat_opt_enabled=False,
    )
    region_off = build_output_path(
        tmp_path / "compiled",
        source,
        purpose="terrain",
        canvas_size=(64, 64),
        pixelization_mode="region",
        palette_budget=24,
        repeat_opt_enabled=False,
    )
    nearest_on = build_output_path(
        tmp_path / "compiled",
        source,
        purpose="terrain",
        canvas_size=(64, 64),
        pixelization_mode="nearest",
        palette_budget=24,
        repeat_opt_enabled=True,
    )

    assert nearest_off != region_off != nearest_on
    assert nearest_off.name == "terrain_64x64_nearest_24c_repeat-off"
    assert region_off.name == "terrain_64x64_region_24c_repeat-off"
    assert nearest_on.name == "terrain_64x64_nearest_24c_repeat-on"


def test_terrain_output_path_keeps_legacy_call_compatible(tmp_path):
    source = tmp_path / "grass.png"

    assert build_output_path(
        tmp_path / "compiled",
        source,
        purpose="terrain",
        canvas_size=(64, 64),
    ) == tmp_path / "compiled" / source.stem / "terrain_64x64"


def test_main_window_exposes_terrain_modes_and_hides_them_for_character(monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from pixel_tile_compiler.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        window.purpose.setCurrentIndex(window.purpose.findData("terrain"))
        app.processEvents()
        assert window.pixelization_mode.isVisible()
        assert window.pixelization_mode.currentData() == "nearest"
        assert window.pixelization_mode.currentText() == "元絵を保持"
        assert window.repeat_opt.currentData() is False

        window.purpose.setCurrentIndex(window.purpose.findData("character"))
        app.processEvents()
        assert window.pixelization_mode.isHidden()
    finally:
        window.close()


def test_main_window_exposes_character_animation_and_canvas_size_selection(monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from pixel_tile_compiler.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        window.purpose.setCurrentIndex(window.purpose.findData("character_animation"))
        app.processEvents()
        assert window.canvas_size.isVisible()
        assert window.canvas_size.currentData() == (128, 128)
        assert "共通bbox" in window.auto_profile.text()
        assert window.pixelization_mode.isHidden()

        window.canvas_size.setCurrentIndex(1)
        app.processEvents()
        assert window.canvas_size.currentData() == (64, 64)
        assert "64×64" in window.auto_profile.text()
    finally:
        window.close()


def test_main_window_exposes_animation_split_modes_and_fixed_grid_controls(monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from pixel_tile_compiler.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        window.purpose.setCurrentIndex(window.purpose.findData("character_animation"))
        app.processEvents()
        assert [window.animation_split_mode.itemData(index) for index in range(window.animation_split_mode.count())] == [
            "fixed_grid",
            "alpha_gap_auto",
            "hybrid",
        ]
        assert window.animation_columns.isVisible()
        assert window.animation_rows.isVisible()

        window.animation_split_mode.setCurrentIndex(window.animation_split_mode.findData("alpha_gap_auto"))
        app.processEvents()
        assert window.animation_columns.isHidden()
        assert window.animation_rows.isHidden()

        window.animation_split_mode.setCurrentIndex(window.animation_split_mode.findData("hybrid"))
        app.processEvents()
        assert window.animation_columns.isVisible()
        assert window.animation_rows.isVisible()
    finally:
        window.close()
