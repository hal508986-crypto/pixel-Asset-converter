import json
from pathlib import Path
import threading

import pytest

from pixel_tile_compiler.gui.canvas import CanvasState
from pixel_tile_compiler.gui.input import first_supported_image_path
from pixel_tile_compiler.gui.policy import (
    GUI_CHARACTER_CANVAS_PRESETS,
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


def test_character_gui_profile_accepts_the_new_presets():
    # 先頭2件は既存GUIと同じ順序（index 0 が推奨の128、index 1 が64）であること
    assert GUI_CHARACTER_CANVAS_PRESETS[0][1] == (128, 128)
    assert GUI_CHARACTER_CANVAS_PRESETS[1][1] == (64, 64)

    for _label, size in GUI_CHARACTER_CANVAS_PRESETS:
        assert resolve_character_gui_profile(size).canvas_size == size

    # 非正方も通ること
    assert resolve_character_gui_profile((256, 128)).canvas_size == (256, 128)
    assert resolve_character_gui_profile((224, 126)).canvas_size == (224, 126)


def test_character_gui_profile_rejects_out_of_range_canvas():
    with pytest.raises(ValueError):
        resolve_character_gui_profile((15, 64))

    with pytest.raises(ValueError):
        resolve_character_gui_profile((64, 513))

    with pytest.raises(ValueError):
        resolve_character_gui_profile((0, 0))

    assert resolve_character_gui_profile((16, 512)).canvas_size == (16, 512)


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
        window.settings_tabs.setCurrentIndex(window.SPLIT_TAB_INDEX)
        app.processEvents()
        assert [window.animation_split_mode.itemData(index) for index in range(window.animation_split_mode.count())] == [
            "fixed_grid",
            "alpha_gap_auto",
            "row_alpha_gap",
            "row_alpha_components",
            "hybrid",
        ]
        assert window.animation_columns.isVisible()
        assert window.animation_rows.isVisible()

        window.animation_split_mode.setCurrentIndex(window.animation_split_mode.findData("alpha_gap_auto"))
        app.processEvents()
        assert window.animation_columns.isHidden()
        assert window.animation_rows.isHidden()

        window.animation_split_mode.setCurrentIndex(window.animation_split_mode.findData("row_alpha_gap"))
        app.processEvents()
        assert window.animation_columns.isVisible()
        assert window.animation_rows.isVisible()

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
        assert window.animation_controls_scroll.widget() is window.settings_tabs
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
        window.settings_tabs.setCurrentIndex(window.PLACEMENT_TAB_INDEX)
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
        QTest.mouseClick(
            window.source_preview,
            Qt.MouseButton.LeftButton,
            pos=_preview_pos_for_pixel(window.source_preview, 30, 10),
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


def test_main_window_row_split_origin_click_uses_logical_coordinates(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from PIL import Image

    from pixel_tile_compiler.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    source = tmp_path / "row-origin.png"
    image = Image.new("RGBA", (40, 20), (0, 0, 0, 0))
    for y in range(5, 15):
        for x in range(2, 8):
            image.putpixel((x, y), (80, 140, 220, 255))
        for x in range(24, 32):
            image.putpixel((x, y), (120, 180, 220, 255))
    image.save(source)
    window.show()
    app.processEvents()
    try:
        assert window.set_source_path(source)
        window.purpose.setCurrentIndex(window.purpose.findData("character_animation"))
        window.animation_split_mode.setCurrentIndex(window.animation_split_mode.findData("row_alpha_gap"))
        window.animation_placement_mode.setCurrentIndex(
            window.animation_placement_mode.findData("preserve_motion")
        )
        window.animation_columns.setValue(2)
        window.animation_rows.setValue(1)
        app.processEvents()

        window.start_source_origin_pick()
        QTest.mouseClick(
            window.source_preview,
            Qt.MouseButton.LeftButton,
            pos=_preview_pos_for_pixel(window.source_preview, 30, 10),
        )
        app.processEvents()

        # クリックした元絵座標は(30,10)。2コマ目の論理原点(20,0)からの座標は(10,10)。
        assert (window.animation_source_origin_x.value(), window.animation_source_origin_y.value()) == (10, 10)
        assert window._source_origin_display_point() == (30, 10)
    finally:
        window.close()


def test_main_window_surfaces_animation_warnings(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from types import SimpleNamespace
    from PySide6.QtWidgets import QApplication
    from PIL import Image

    from pixel_tile_compiler.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    frame = tmp_path / "F1.png"
    preview = tmp_path / "preview.png"
    Image.new("RGBA", (64, 64), (80, 140, 220, 255)).save(frame)
    Image.new("RGBA", (64, 64), (80, 140, 220, 255)).save(preview)
    window._compile_context = {
        "kind": "animation",
        "configuration_revision": window._configuration_revision,
        "canvas_size": (64, 64),
        "output": tmp_path,
        "placement_mode": "preserve_motion",
    }
    try:
        window._on_compile_succeeded(
            SimpleNamespace(
                final_frame_paths=(frame,),
                preview_8x_path=preview,
                preview_scale=1,
                warnings=("等分割へfallbackしました", "X境界が可視maskを横切ります"),
            )
        )
        assert "警告" in window.status.text()
        assert "等分割へfallbackしました" in window.status.text()
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


def _component_assignment_source(tmp_path):
    from PIL import Image

    source = tmp_path / "sheet.png"
    image = Image.new("RGBA", (64, 40), (0, 0, 0, 0))
    for y in range(5, 25):
        for x in range(5, 19):
            image.putpixel((x, y), (220, 50, 50, 255))
        for x in range(38, 52):
            image.putpixel((x, y), (50, 80, 220, 255))
    for y in range(7, 20):
        image.putpixel((27, y), (255, 255, 255, 255))
    for y in range(25, 34):
        for x in range(3, 17):
            image.putpixel((x, y), (220, 50, 50, 255))
    image.save(source)
    return source


def _preview_pos_for_pixel(preview, pixel_x: int, pixel_y: int):
    """元絵プレビュー上で、指定した画素の中心に当たるウィジェット座標を返す。"""
    from PySide6.QtCore import QPoint

    image, (left, top, width, height) = preview._display_geometry()
    return QPoint(
        left + int((pixel_x + 0.5) * width / image.width()),
        top + int((pixel_y + 0.5) * height / image.height()),
    )


def _prepare_component_assignment_window(window, source, mode="row_alpha_components"):
    window.show()
    window.set_source_path(source)
    window.purpose.setCurrentIndex(window.purpose.findData("character_animation"))
    window.animation_split_mode.setCurrentIndex(window.animation_split_mode.findData(mode))
    window.animation_columns.setValue(2)
    window.animation_rows.setValue(1)


def _assign_component_rows(window, satellite_id, satellite_frame):
    for component_id, combo in window._component_assignment_combos.items():
        frame_id = satellite_frame if component_id == satellite_id else (
            "F1" if window._component_analysis.components[component_id - 1].bbox[0] < 30 else "F2"
        )
        combo.setCurrentIndex(combo.findData(frame_id))


def test_main_window_component_assignment_change_requires_reconfirmation(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import pixel_tile_compiler.gui.main_window as main_window_module
    from pixel_tile_compiler.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    source = _component_assignment_source(tmp_path)
    try:
        _prepare_component_assignment_window(window, source)
        window.analyze_component_assignments()
        assert window.wait_for_analysis()
        satellite_id = next(
            component.component_id
            for component in window._component_analysis.components
            if component.bbox == (27, 7, 28, 20)
        )
        _assign_component_rows(window, satellite_id, "F1")
        window.confirm_component_assignments()
        assert window._component_assignment_confirmed is True
        assert int(window._component_analysis.owner_labels[7, 27]) == 1

        window._origin_pick_target = "source"
        window._on_source_origin_clicked((27, 7))
        assert window._source_origin_frame_index == 0
        confirmed_overlay = window._component_analysis.overlay
        combo = window._component_assignment_combos[satellite_id]
        combo.setCurrentIndex(combo.findData("F2"))
        app.processEvents()
        assert window._component_assignment_confirmed is False
        assert window._component_analysis.overlay is not confirmed_overlay
        assert window.source_preview._image is not None
        assert window._source_origin_frame_index is None
        assert window.animation_source_origin_set.isChecked() is False
        window._origin_pick_target = "source"
        window._on_source_origin_clicked((27, 7))
        assert window._source_origin_frame_index is None

        started = []
        monkeypatch.setattr(window, "_start_compile", lambda operation, context: started.append((operation, context)))
        window.compile_image()
        assert not started
        assert "未解決" in window.status.text()
        assert "再度割り当て" in window.animation_component_assignment_status.text()

        window.confirm_component_assignments()
        assert window._component_assignment_confirmed is True
        assert int(window._component_analysis.owner_labels[7, 27]) == 2
        window._origin_pick_target = "source"
        window._on_source_origin_clicked((27, 7))
        assert window._source_origin_frame_index == 1

        captured = {}
        def fake_compile(*args, **kwargs):
            captured["assignments"] = kwargs["component_assignments"]
            return object()

        monkeypatch.setattr(main_window_module, "compile_character_animation_sheet", fake_compile)
        monkeypatch.setattr(window, "_start_compile", lambda operation, context: operation())
        window.compile_image()
        assert captured["assignments"][satellite_id] == "F2"
    finally:
        window.close()


def test_main_window_component_assignment_candidates_are_visible_and_ties_stay_unset(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel

    from pixel_tile_compiler.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    source = _component_assignment_source(tmp_path)
    try:
        _prepare_component_assignment_window(window, source)
        window.analyze_component_assignments()
        assert window.wait_for_analysis()
        labels = [label.text() for label in window.animation_component_assignment_rows.findChildren(QLabel)]
        assert any("候補" in text and "F1" in text and "F2" in text for text in labels)
        satellite_id = next(
            component.component_id
            for component in window._component_analysis.components
            if component.bbox == (27, 7, 28, 20)
        )
        assert window._component_assignment_combos[satellite_id].currentData() is None
        assert window._component_assignment_confirmed is False
    finally:
        window.close()


def test_main_window_hybrid_pending_can_be_confirmed_and_reexecuted(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import pixel_tile_compiler.gui.main_window as main_window_module
    from pixel_tile_compiler.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    source = _component_assignment_source(tmp_path)
    try:
        _prepare_component_assignment_window(window, source, mode="hybrid")
        window.analyze_component_assignments()
        assert window.wait_for_analysis()
        assert window._component_analysis.status == "needs_assignment"
        assert window.animation_component_group.isVisible()
        satellite_id = next(
            component.component_id
            for component in window._component_analysis.components
            if component.bbox == (27, 7, 28, 20)
        )
        _assign_component_rows(window, satellite_id, "F2")
        window.confirm_component_assignments()
        assert window._component_assignment_confirmed is True
        captured = {}

        def fake_compile(*args, **kwargs):
            captured["config"] = kwargs["config"]
            captured["assignments"] = kwargs["component_assignments"]
            return object()

        monkeypatch.setattr(main_window_module, "compile_character_animation_sheet", fake_compile)
        monkeypatch.setattr(window, "_start_compile", lambda operation, context: operation())
        window.compile_image()
        assert captured["config"].split_mode == "hybrid"
        assert captured["assignments"][satellite_id] == "F2"
    finally:
        window.close()


def test_main_window_component_split_cards_preview_and_rect_selection(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    import pixel_tile_compiler.gui.main_window as main_window_module
    from pixel_tile_compiler.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    source = _component_assignment_source(tmp_path)
    try:
        _prepare_component_assignment_window(window, source)
        window.analyze_component_assignments()
        assert window.wait_for_analysis()
        app.processEvents()

        # コマカード (F1, F2) が生成されている
        cards_layout = window.animation_frame_cards_layout
        assert cards_layout.count() == 2

        # 元絵クリックでパーツ選択
        window._on_source_preview_clicked((27, 7))
        assert len(window._selected_component_ids) == 1
        assert len(window.source_preview._highlight_boxes) == 1
        assert "選択中: C" in window.animation_component_selection_info.text()

        # 矩形選択で交差パーツ選択
        window._on_source_preview_rect_selected((0, 0, 64, 40))
        assert len(window._selected_component_ids) >= 2
        assert len(window.source_preview._highlight_boxes) >= 2

        # 割り当て変更
        window._on_source_preview_clicked((27, 7))
        window.animation_component_target_combo.setCurrentIndex(
            window.animation_component_target_combo.findData("F2")
        )
        window.reassign_selected_components()
        app.processEvents()
        assert window._component_assignment_confirmed is False
        assert window._component_assignment_overrides.get(window._selected_component_ids[0]) == "F2"

        # 一括確定
        window.confirm_component_assignments()
        assert window._component_assignment_confirmed is True
        assert "全コマの割り当てを確定しました" in window.animation_component_assignment_status.text()

        # コンパイル実行
        captured = {}
        def fake_compile(*args, **kwargs):
            captured["confirm_components"] = kwargs.get("confirm_components")
            captured["assignments"] = kwargs.get("component_assignments")
            captured["cells_override"] = kwargs.get("cells_override")
            return object()

        monkeypatch.setattr(main_window_module, "compile_character_animation_sheet", fake_compile)
        monkeypatch.setattr(window, "_start_compile", lambda operation, context: operation())
        window.compile_image()
        assert captured["confirm_components"] is True
        assert captured["assignments"][window._selected_component_ids[0]] == "F2"
        assert captured["cells_override"] is not None
    finally:
        window.close()


def test_main_window_confirm_and_compile_without_manual_override(monkeypatch, tmp_path):
    """[P1-1 回帰テスト] 手動変更なしの「元絵読み込み→解析→一括確定→実コンパイル」が正常成功することを検証する。"""
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication

    from pixel_tile_compiler.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    source = _component_assignment_source(tmp_path)
    output_dir = tmp_path / "compiled_output"
    try:
        window.show()
        assert window.set_source_path(source)
        window.output_root_field.setText(str(output_dir))
        window.purpose.setCurrentIndex(window.purpose.findData("character_animation"))
        window.animation_split_mode.setCurrentIndex(window.animation_split_mode.findData("row_alpha_components"))
        window.animation_columns.setValue(2)
        window.animation_rows.setValue(1)
        app.processEvents()

        # 1. 解析を実行
        window.analyze_component_assignments()
        assert window.wait_for_analysis()
        assert window._component_analysis is not None
        # 要確認パーツ（衛星パーツ）があるため初期は needs_assignment
        assert window._component_analysis.status == "needs_assignment"

        # 2. 手動変更なしで一括確定
        window.confirm_component_assignments()
        assert window._component_assignment_confirmed is True
        assert window._component_analysis.status == "resolved"

        # 3. 実コンパイルを実行
        window.compile_image()
        # 非同期コンパイルの完了を待機
        deadline = 10000
        elapsed = 0
        while window._compile_thread is not None and elapsed < deadline:
            app.processEvents()
            QThread.msleep(10)
            elapsed += 10

        assert window._compile_thread is None
        assert "完了:" in window.status.text()

        # 出力アーティファクトの存在確認
        expected_output = output_dir / source.stem / "character_animation_128x128_b24"
        report_path = expected_output / "bbox_report.json"
        assert report_path.exists(), f"bbox_report.json not found in {expected_output}"
        aligned_sheet = expected_output / "aligned_sheet.png"
        assert aligned_sheet.exists(), f"aligned_sheet.png not found in {expected_output}"
    finally:
        window.close()


def test_main_window_analysis_worker_keeps_gui_responsive_and_discards_stale_revision(monkeypatch, tmp_path):
    """[P2 回帰テスト] 解析ワーカーがGUIスレッドをブロックせず、設定変更時に古い解析結果を破棄することを検証する。"""
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QThread, QTimer
    from PySide6.QtWidgets import QApplication

    import pixel_tile_compiler.gui.main_window as main_window_module
    from pixel_tile_compiler.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    source = _component_assignment_source(tmp_path)
    try:
        _prepare_component_assignment_window(window, source)

        # タイマーの稼働確認用カウンタ
        timer_ticks = 0
        def on_tick():
            nonlocal timer_ticks
            timer_ticks += 1

        timer = QTimer()
        timer.setInterval(20)
        timer.timeout.connect(on_tick)
        timer.start()

        # 時間のかかるモック解析処理を定義
        original_analyze = main_window_module.analyze_component_split
        def slow_analyze(*args, **kwargs):
            # ワーカースレッド内で0.3秒スリープ
            QThread.msleep(300)
            return original_analyze(*args, **kwargs)

        monkeypatch.setattr(main_window_module, "analyze_component_split", slow_analyze)

        # 1. 非同期解析を開始
        window.analyze_component_assignments()
        assert window._analysis_thread is not None

        # 2. 解析処理中もGUIイベントループが回り、タイマーが動作し続けることを検証
        while window._analysis_thread is not None and window._analysis_thread.isRunning():
            app.processEvents()
            QThread.msleep(10)

        timer.stop()
        # 300msの間、20msタイマーが複数回（少なくとも5回以上）発火していること
        assert timer_ticks >= 5, f"Timer should have ticked multiple times during async analysis, got {timer_ticks}"
        assert window.wait_for_analysis()
        assert window._component_analysis is not None

        # 3. 世代管理テスト: 解析中に設定変更が発生した場合
        old_revision = window._configuration_revision
        window.analyze_component_assignments()
        assert window._analysis_thread is not None

        # 解析が走っている間に設定を変更して revision を進める
        window.animation_columns.setValue(4)
        assert window._configuration_revision > old_revision

        # 解析の完了を待機
        assert window.wait_for_analysis()
        # 古い解析結果（columns=2向け）は破棄され、反映されていないこと
        assert window._component_analysis is None
    finally:
        window.close()


def test_main_window_analysis_worker_concurrency_rapid_calls_and_close_safety(monkeypatch, tmp_path):
    """[P1 回帰テスト] 解析ワーカーの連打・解析中再設定・ウィンドウクローズでプロセスがクラッシュせず最新結果を採用することを検証する。"""
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QThread
    from PySide6.QtWidgets import QApplication

    import pixel_tile_compiler.gui.main_window as main_window_module
    from pixel_tile_compiler.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    source = _component_assignment_source(tmp_path)

    original_analyze = main_window_module.analyze_component_split

    def slow_analyze(*args, **kwargs):
        QThread.msleep(150)
        return original_analyze(*args, **kwargs)

    monkeypatch.setattr(main_window_module, "analyze_component_split", slow_analyze)

    # 1. 連打テスト（同一設定で複数回連続呼び出し）
    window = MainWindow()
    try:
        _prepare_component_assignment_window(window, source)
        assert window.analyze_component_assignments()
        # すぐに連打
        assert window.analyze_component_assignments()
        assert window.analyze_component_assignments()
        # クラッシュせず安全に完了すること
        assert window.wait_for_analysis()
        assert window._component_analysis is not None
        assert window._component_analysis.columns == 2

        # 2. 解析中の再設定テスト（実行中に設定を変更して新ワーカーを起動）
        window.analyze_component_assignments()
        first_worker = window._analysis_thread
        assert first_worker is not None

        # 実行中に設定を変更（columns=4）して新しい解析を開始
        window.animation_columns.setValue(4)
        assert window.analyze_component_assignments()
        second_worker = window._analysis_thread
        assert second_worker is not first_worker
        # 両方のワーカーが _analysis_workers に追跡されていること
        assert first_worker in window._analysis_workers
        assert second_worker in window._analysis_workers

        # 全ワーカーの完了を待機
        assert window.wait_for_analysis()
        # 最終的に採用された結果は最新の columns=4 であること
        assert window._component_analysis is not None
        assert window._component_analysis.columns == 4

        # 3. 解析中のウィンドウクローズテスト
        window.analyze_component_assignments()
        assert window._analysis_thread is not None
        assert len(window._analysis_workers) >= 1
        window.close()
        # closeEvent により即座の破棄が保留（_close_pending）されること
        assert window._close_pending is True
        # 保留された終了がワーカー完了後に再実行され、安全に閉じること
        assert window.wait_for_close()
        assert not any(w.isRunning() for w in window._analysis_workers)
        assert window._close_pending is False
    finally:
        window.close()


def test_main_window_close_during_long_analysis_in_subprocess_preserves_thread_and_exits_cleanly(tmp_path: Path):
    """[P1 回帰テスト] 2秒超（2.5秒）の解析ワーカー実行中にウィンドウ終了を行っても、終了が保留され破棄前に安全に完了して正常終了することを別プロセスで検証する。"""
    import os
    import subprocess
    import sys

    source = _component_assignment_source(tmp_path)
    script = f"""
import sys
import time
from pathlib import Path
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

import pixel_tile_compiler.gui.main_window as main_window_module
from pixel_tile_compiler.gui.main_window import MainWindow

# 2.5秒かかる解析をシミュレート（2秒のタイムアウトより長い）
original_analyze = main_window_module.analyze_component_split
def slow_analyze(*args, **kwargs):
    time.sleep(2.5)
    return original_analyze(*args, **kwargs)

main_window_module.analyze_component_split = slow_analyze

app = QApplication(sys.argv)
window = MainWindow()
window.show()

source_path = Path(r"{source.as_posix()}")
window.source_path = source_path
window.purpose.setCurrentText("character")
window.animation_split_mode.setCurrentText("row_alpha_components")
window.animation_columns.setValue(2)
window.animation_rows.setValue(1)

# 解析開始
started = window.analyze_component_assignments()
assert started, "Analysis did not start"
assert len(window._analysis_workers) >= 1, "No active workers"

# 300ms 後にウィンドウ終了を要求（この時点ではワーカーはまだ2.2秒実行中）
QTimer.singleShot(300, window.close)

t0 = time.time()
exit_code = app.exec()
elapsed = time.time() - t0

print(f"ELAPSED:{{elapsed:.2f}}")
print(f"EXIT_CODE:{{exit_code}}")
assert elapsed >= 2.3, f"Window closed prematurely after {{elapsed:.2f}}s (before worker finished)!"
sys.exit(exit_code)
"""
    runner_script = tmp_path / "run_subprocess_close.py"
    runner_script.write_text(script, encoding="utf-8")

    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONPATH"] = "src;."

    result = subprocess.run(
        [sys.executable, str(runner_script)],
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )
    assert result.returncode == 0, f"Subprocess failed with code {result.returncode}:\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
    assert "ELAPSED:" in result.stdout
    for line in result.stdout.splitlines():
        if line.startswith("ELAPSED:"):
            elapsed = float(line.split(":")[1])
            assert elapsed >= 2.3, f"Elapsed time was {elapsed}s, which is less than 2.3s!"

