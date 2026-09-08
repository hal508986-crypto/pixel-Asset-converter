import json
import threading

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
        purpose="character",
        canvas_size=(128, 128),
        palette_token="0123456789abcdef-rest",
    ) == tmp_path / "compiled" / source.stem / "character_128x128_b24_shared-0123456789ab"

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


def test_main_window_shows_animation_restore_backup_error(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication

    import pixel_tile_compiler.gui.main_window as main_window_module
    from pixel_tile_compiler.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    source = tmp_path / "idle.png"
    source.write_bytes(b"placeholder")
    backup_root = tmp_path / ".output.previous-recovery"

    def fail_animation_compile(*args, **kwargs):
        raise RuntimeError(
            "出力の入れ替えに失敗し、旧出力を復元できませんでした。"
            f"復旧用バックアップを保持しています: {backup_root}"
        )

    monkeypatch.setattr(main_window_module, "compile_character_animation_sheet", fail_animation_compile)
    window.show()
    app.processEvents()
    try:
        assert window.set_source_path(source)
        window.purpose.setCurrentIndex(window.purpose.findData("character_animation"))
        app.processEvents()

        window.compile_image()
        deadline = 5000
        elapsed = 0
        while window._compile_thread is not None and elapsed < deadline:
            app.processEvents()
            QThread.msleep(10)
            elapsed += 10

        assert "コンパイルできませんでした" in window.status.text()
        assert "復元できませんでした" in window.status.text()
        assert "復旧用バックアップ" in window.status.text()
        assert str(backup_root) in window.status.text()
    finally:
        window.close()


def test_main_window_keeps_animation_actions_visible_with_scrollable_settings(monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from pixel_tile_compiler.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.resize(1440, 860)
    window.show()
    app.processEvents()
    try:
        window.purpose.setCurrentIndex(window.purpose.findData("character_animation"))
        window.animation_placement_mode.setCurrentIndex(
            window.animation_placement_mode.findData("preserve_motion")
        )
        app.processEvents()
        assert window.animation_controls_scroll.widgetResizable()
        assert window.animation_controls_scroll.verticalScrollBar().maximum() > 0
        assert window.minimumSizeHint().height() <= 860
        assert window.compile_button.geometry().bottom() < window.height()
        assert window.compile_progress.isVisible()
        assert window.compile_button.parentWidget() is not window.animation_controls_scroll.widget()
    finally:
        window.close()


def test_main_window_keeps_animation_settings_within_narrow_controls_view(monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPoint
    from PySide6.QtWidgets import QApplication

    from pixel_tile_compiler.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.resize(800, 600)
    window.show()
    app.processEvents()
    try:
        window.purpose.setCurrentIndex(window.purpose.findData("character_animation"))
        window.animation_placement_mode.setCurrentIndex(
            window.animation_placement_mode.findData("preserve_motion")
        )
        app.processEvents()
        viewport = window.animation_controls_scroll.viewport()
        button_origin = window.animation_source_origin_pick_button.mapTo(viewport, QPoint(0, 0))
        assert button_origin.x() + window.animation_source_origin_pick_button.width() <= viewport.width()
    finally:
        window.close()


def test_main_window_origin_guides_select_points_and_expose_scale_modes(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from PIL import Image

    from pixel_tile_compiler.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    source = tmp_path / "origin.png"
    Image.new("RGBA", (40, 20), (80, 140, 220, 255)).save(source)
    window.show()
    app.processEvents()
    try:
        assert window.set_source_path(source)
        window.purpose.setCurrentIndex(window.purpose.findData("character_animation"))
        window.animation_placement_mode.setCurrentIndex(
            window.animation_placement_mode.findData("preserve_motion")
        )
        window.animation_columns.setValue(2)
        window.animation_rows.setValue(1)
        app.processEvents()
        assert window.animation_scale_mode.currentData() == "auto"
        assert not window.animation_scale.isEnabled()
        assert not window.animation_source_origin_set.isChecked()
        assert not window.animation_output_origin_set.isChecked()

        window.start_source_origin_pick()
        image, (left, top, width, height) = window.source_preview._display_geometry()
        del image
        QTest.mouseClick(
            window.source_preview,
            Qt.MouseButton.LeftButton,
            pos=QPoint(left + (width * 3) // 4, top + height // 2),
        )
        window.start_output_origin_pick()
        window.canvas.set_zoom(1)
        app.processEvents()
        QTest.mouseClick(
            window.canvas.viewport(),
            Qt.MouseButton.LeftButton,
            pos=window.canvas.mapFromScene(QPointF(128, 180)),
        )
        app.processEvents()

        assert window.animation_source_origin_set.isChecked()
        assert (window.animation_source_origin_x.value(), window.animation_source_origin_y.value()) == (10, 10)
        assert window.animation_output_origin_set.isChecked()
        assert (window.animation_output_origin_x.value(), window.animation_output_origin_y.value()) == (128, 180)
        window.animation_scale_mode.setCurrentIndex(window.animation_scale_mode.findData("fixed"))
        app.processEvents()
        assert window.animation_scale.isEnabled()
    finally:
        window.close()


def test_main_window_does_not_show_stale_async_result_after_setting_change(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication
    from PIL import Image

    import pixel_tile_compiler.gui.main_window as main_window_module
    from pixel_tile_compiler.gui.main_window import MainWindow

    source = tmp_path / "delayed.png"
    Image.new("RGBA", (64, 64), (80, 140, 220, 255)).save(source)
    started = threading.Event()
    release = threading.Event()
    original_compile = main_window_module.PixelTileCompiler.compile

    def delayed_compile(self, source_path, config):
        started.set()
        assert release.wait(5)
        return original_compile(self, source_path, config)

    monkeypatch.setattr(main_window_module.PixelTileCompiler, "compile", delayed_compile)
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        assert window.set_source_path(source)
        window.output_root_field.setText(str(tmp_path / "outputs"))
        window.compile_image()
        assert started.wait(2)
        window.canvas_size.setCurrentIndex(1)
        app.processEvents()
        release.set()
        deadline = 5000
        elapsed = 0
        while window._compile_thread is not None and elapsed < deadline:
            app.processEvents()
            QThread.msleep(10)
            elapsed += 10

        assert window._compile_thread is None
        assert window._compiled_canvas_size is None
        assert "現在のプレビュー" in window.status.text()
    finally:
        release.set()
        window.close()


def test_main_window_compiles_animation_asynchronously_and_can_play_frames(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication
    from PIL import Image

    from pixel_tile_compiler.gui.main_window import MainWindow

    source = tmp_path / "animation.png"
    sheet = Image.new("RGBA", (40, 20), (0, 0, 0, 0))
    for frame in range(2):
        for y in range(3, 15):
            for x in range(frame * 20 + 5, frame * 20 + 13):
                sheet.putpixel((x, y), (80 + frame * 40, 140, 220, 255))
    sheet.save(source)

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        assert window.set_source_path(source)
        window.output_root_field.setText(str(tmp_path / "outputs"))
        window.purpose.setCurrentIndex(window.purpose.findData("character_animation"))
        window.animation_columns.setValue(2)
        window.animation_rows.setValue(1)
        window.animation_placement_mode.setCurrentIndex(
            window.animation_placement_mode.findData("preserve_motion")
        )
        window.animation_source_origin_set.setChecked(True)
        window.animation_source_origin_x.setValue(10)
        window.animation_source_origin_y.setValue(10)
        window.animation_output_origin_set.setChecked(True)
        window.animation_output_origin_x.setValue(32)
        window.animation_output_origin_y.setValue(56)
        window.animation_scale_mode.setCurrentIndex(window.animation_scale_mode.findData("fixed"))
        window.animation_scale.setValue(1.0)
        app.processEvents()

        window.compile_image()
        deadline = 5000
        elapsed = 0
        while window._compile_thread is not None and elapsed < deadline:
            app.processEvents()
            QThread.msleep(10)
            elapsed += 10

        assert window._compile_thread is None
        assert "完了" in window.status.text()
        assert window.animation_play_button.isEnabled()
        initial_index = window._animation_frame_index
        window.animation_play_timer.setInterval(1)
        window.animation_play_button.click()
        QThread.msleep(20)
        app.processEvents()
        assert window._animation_frame_index != initial_index
        window.animation_play_button.click()
    finally:
        window.close()


def test_main_window_applies_map_palette_to_character_compile(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication
    from PIL import Image

    from pixel_tile_compiler.gui.main_window import MainWindow

    source = tmp_path / "character.png"
    image = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
    for y in range(24, 104):
        for x in range(40, 88):
            image.putpixel((x, y), (30, 120, 80, 255) if y < 64 else (180, 70, 50, 255))
    image.save(source)
    shared = ((30, 120, 80), (180, 70, 50))

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        assert window.set_source_path(source)
        window.output_root_field.setText(str(tmp_path / "outputs"))
        window.set_shared_palette(shared, source_label="マップタイル基準")
        assert window.shared_palette_colors == shared
        assert window.shared_palette_info.text().startswith("マップタイル基準")

        window.compile_image()
        deadline = 5000
        elapsed = 0
        while window._compile_thread is not None and elapsed < deadline:
            app.processEvents()
            QThread.msleep(10)
            elapsed += 10

        assert window._compile_thread is None
        assert "完了" in window.status.text()
        output_dirs = list((tmp_path / "outputs" / source.stem).iterdir())
        assert len(output_dirs) == 1
        metadata = json.loads((output_dirs[0] / "metadata.json").read_text(encoding="utf-8"))
        assert metadata["config"]["palette_colors"] == [list(color) for color in shared]
    finally:
        window.close()


def test_main_window_receives_terrain_reference_palette(monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from pixel_tile_compiler.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    app.processEvents()
    terrain_window = None
    try:
        window.open_terrain_batch()
        terrain_window = window._terrain_batch_window
        assert terrain_window is not None
        colors = ((12, 34, 56), (200, 180, 160))
        terrain_window.reference_palette_changed.emit(colors)
        app.processEvents()
        assert window.shared_palette_colors == colors
        assert window.shared_palette_view.count() == len(colors)
        terrain_window.reference_palette_changed.emit(())
        app.processEvents()
        assert window.shared_palette_colors == ()
    finally:
        if terrain_window is not None:
            terrain_window.close()
        window.close()
