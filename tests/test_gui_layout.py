"""GUIレイアウト契約とホイール誤爆ガードのテスト。"""

from __future__ import annotations

import pytest


def _wheel_event(delta: int = 120):
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QWheelEvent

    return QWheelEvent(
        QPointF(10.0, 10.0),
        QPointF(10.0, 10.0),
        QPoint(0, 0),
        QPoint(0, delta),
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
        Qt.ScrollPhase.NoScrollPhase,
        False,
    )


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def test_no_wheel_inputs_never_change_their_value_on_wheel(monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from pixel_tile_compiler.gui.widgets import (
        NoWheelComboBox,
        NoWheelDoubleSpinBox,
        NoWheelSpinBox,
    )

    _app()
    spin = NoWheelSpinBox()
    spin.setRange(0, 100)
    spin.setValue(10)
    QApplication.sendEvent(spin, _wheel_event())
    QApplication.sendEvent(spin, _wheel_event(-120))
    assert spin.value() == 10

    double_spin = NoWheelDoubleSpinBox()
    double_spin.setRange(0.0, 10.0)
    double_spin.setValue(1.5)
    QApplication.sendEvent(double_spin, _wheel_event())
    assert double_spin.value() == pytest.approx(1.5)

    combo = NoWheelComboBox()
    combo.addItems(["A", "B", "C"])
    combo.setCurrentIndex(1)
    QApplication.sendEvent(combo, _wheel_event())
    assert combo.currentIndex() == 1


def test_no_wheel_input_outside_a_scroll_area_leaves_the_event_to_its_parent(monkeypatch):
    """囲むスクロール領域が無ければ受理せず、上位の判断へ委ねる。"""
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from pixel_tile_compiler.gui.widgets import NoWheelSpinBox

    _app()
    spin = NoWheelSpinBox()
    spin.setRange(0, 100)
    spin.setValue(10)
    event = _wheel_event()
    QApplication.sendEvent(spin, event)
    assert not event.isAccepted()
    assert spin.value() == 10


def test_wheel_over_guarded_input_scrolls_the_surrounding_scroll_area(monkeypatch):
    """スクロール領域に載せた数値入力の上でホイールを回すと、値ではなく領域が動く。"""
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QScrollArea, QVBoxLayout, QWidget

    from pixel_tile_compiler.gui.widgets import NoWheelSpinBox

    app = _app()
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    content = QWidget()
    layout = QVBoxLayout(content)
    spins = []
    for _ in range(40):
        spin = NoWheelSpinBox()
        spin.setRange(0, 100)
        spin.setValue(10)
        layout.addWidget(spin)
        spins.append(spin)
    scroll.setWidget(content)
    scroll.resize(200, 200)
    scroll.show()
    app.processEvents()
    try:
        assert scroll.verticalScrollBar().maximum() > 0
        before = scroll.verticalScrollBar().value()
        QApplication.sendEvent(spins[0], _wheel_event(-120))
        app.processEvents()
        assert scroll.verticalScrollBar().value() > before
        assert spins[0].value() == 10
    finally:
        scroll.close()


def _guarded_input_violations(window):
    from PySide6.QtWidgets import QAbstractSpinBox, QComboBox

    from pixel_tile_compiler.gui.widgets import (
        NoWheelComboBox,
        NoWheelDoubleSpinBox,
        NoWheelSpinBox,
    )

    guarded = (NoWheelSpinBox, NoWheelDoubleSpinBox, NoWheelComboBox)
    candidates = window.findChildren(QAbstractSpinBox) + window.findChildren(QComboBox)
    return [widget for widget in candidates if not isinstance(widget, guarded)]


def test_main_window_uses_wheel_guarded_inputs_everywhere(monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from pixel_tile_compiler.gui.main_window import MainWindow

    _app()
    window = MainWindow()
    try:
        assert _guarded_input_violations(window) == []
    finally:
        window.close()


def test_terrain_batch_window_uses_wheel_guarded_inputs_everywhere(monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from pixel_tile_compiler.gui.terrain_batch_window import TerrainBatchWindow

    _app()
    window = TerrainBatchWindow()
    try:
        assert _guarded_input_violations(window) == []
    finally:
        window.close()


def test_main_window_builds_workbench_split_layout(monkeypatch):
    """上部=プレビュー群、下部=設定タブ の上下分割ワークベンチであること。"""
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt

    from pixel_tile_compiler.gui.main_window import MainWindow

    app = _app()
    window = MainWindow()
    window.resize(1440, 900)
    window.show()
    app.processEvents()
    try:
        assert window.workbench_splitter.orientation() == Qt.Orientation.Vertical
        assert window.preview_splitter.orientation() == Qt.Orientation.Horizontal
        assert window.workbench_splitter.count() == 2
        assert window.workbench_splitter.widget(0) is window.preview_splitter
        assert window.workbench_splitter.indexOf(window.animation_controls_scroll) == 1
        assert window.preview_splitter.isAncestorOf(window.source_preview)
        assert window.preview_splitter.isAncestorOf(window.animation_component_group)
        assert window.animation_controls_scroll.isAncestorOf(window.settings_tabs)
        assert not window.animation_controls_scroll.isAncestorOf(window.source_preview)
        sizes = window.workbench_splitter.sizes()
        assert sizes[0] > sizes[1]
    finally:
        window.close()


def test_main_window_preview_tabs_hold_canvas_and_outputs(monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from pixel_tile_compiler.gui.main_window import MainWindow

    app = _app()
    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        titles = [window.preview_tabs.tabText(index) for index in range(window.preview_tabs.count())]
        assert titles == ["ドットプレビュー", "コンパイル結果", "タイル繰り返し確認", "palette"]
        assert window.preview_tabs.isAncestorOf(window.canvas)
        assert window.preview_tabs.isAncestorOf(window.result_preview)
        assert window.preview_tabs.isAncestorOf(window.tile_preview)
    finally:
        window.close()


def test_main_window_settings_tabs_expose_grouped_pages(monkeypatch):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from pixel_tile_compiler.gui.main_window import MainWindow

    app = _app()
    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        titles = [window.settings_tabs.tabText(index) for index in range(window.settings_tabs.count())]
        assert titles == ["基本", "分割", "配置・原点", "palette・保存先"]
        assert window.settings_tabs.isAncestorOf(window.canvas_size)
        assert window.settings_tabs.isAncestorOf(window.animation_columns)
        assert window.settings_tabs.isAncestorOf(window.animation_source_origin_pick_button)
        assert window.settings_tabs.isAncestorOf(window.output_root_field)
        assert window.settings_tabs.isAncestorOf(window.shared_palette_group)
    finally:
        window.close()


def test_main_window_purpose_segments_stay_in_sync_with_purpose(monkeypatch):
    """用途は3択セグメントで切り替え、purposeコンボと双方向に同期する。"""
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from pixel_tile_compiler.gui.main_window import MainWindow

    app = _app()
    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        assert set(window.purpose_segments) == {"character", "character_animation", "terrain"}
        window.purpose.setCurrentIndex(window.purpose.findData("terrain"))
        app.processEvents()
        assert window.purpose_segments["terrain"].isChecked()
        assert not window.purpose_segments["character"].isChecked()

        window.purpose_segments["character_animation"].click()
        app.processEvents()
        assert window.purpose.currentData() == "character_animation"
        assert window.purpose_segments["character_animation"].isChecked()
    finally:
        window.close()


def test_main_window_fits_in_a_small_window_without_clipping(monkeypatch):
    """800×600でも横あふれせず、実行バーが画面内に残る。"""
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt

    from pixel_tile_compiler.gui.main_window import MainWindow

    app = _app()
    window = MainWindow()
    window.resize(800, 600)
    window.show()
    app.processEvents()
    try:
        assert window.minimumSizeHint().width() <= 800
        assert window.minimumSizeHint().height() <= 600
        assert (
            window.animation_controls_scroll.horizontalScrollBarPolicy()
            == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        assert window.compile_button.geometry().bottom() < window.height()
        assert window.centralWidget().width() <= window.width()
    finally:
        window.close()


def test_settings_pane_height_follows_the_purpose(monkeypatch):
    """設定が少ない用途では下段を縮め、余った縦をプレビューへ回す。"""
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from pixel_tile_compiler.gui.main_window import MainWindow

    app = _app()
    window = MainWindow()
    window.resize(1440, 900)
    window.show()
    app.processEvents()
    try:
        window.purpose.setCurrentIndex(window.purpose.findData("character_animation"))
        app.processEvents()
        animation_preview, animation_settings = window.workbench_splitter.sizes()

        window.purpose.setCurrentIndex(window.purpose.findData("character"))
        app.processEvents()
        character_preview, character_settings = window.workbench_splitter.sizes()

        # 誤差ではなく体感できる差であること
        assert animation_settings - character_settings >= 40
        assert character_preview - animation_preview >= 40
    finally:
        window.close()


def test_palette_swatch_list_renders_one_item_per_color(monkeypatch):
    """色見本は1色1項目で、hex表記と背景色を持つ。"""
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from pixel_tile_compiler.gui.widgets import PaletteSwatchList

    _app()
    view = PaletteSwatchList()
    view.set_colors(((255, 0, 0), (0, 128, 255)))
    assert view.count() == 2
    assert view.item(0).text() == "#FF0000"
    assert view.item(1).text() == "#0080FF"
    assert view.item(1).background().color().getRgb()[:3] == (0, 128, 255)

    view.set_colors(())
    assert view.count() == 0


def test_output_canvas_size_row_is_hidden_for_terrain(monkeypatch):
    """地形の出力は64×64固定なので、出力Canvasサイズの行自体を出さない。"""
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from pixel_tile_compiler.gui.main_window import MainWindow

    app = _app()
    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        window.purpose.setCurrentIndex(window.purpose.findData("terrain"))
        app.processEvents()
        assert window.canvas_size.isHidden()
        assert window.canvas_size_label.isHidden()

        window.purpose.setCurrentIndex(window.purpose.findData("character"))
        app.processEvents()
        assert window.canvas_size.isVisible()
        assert window.canvas_size_label.isVisible()
    finally:
        window.close()


def test_output_canvas_size_is_disabled_when_placement_decides_it(monkeypatch):
    """移動を保持する配置では幅・高さが別にあるので、こちらは触らせない。"""
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from pixel_tile_compiler.gui.main_window import MainWindow

    app = _app()
    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        window.purpose.setCurrentIndex(window.purpose.findData("character_animation"))
        window.animation_placement_mode.setCurrentIndex(
            window.animation_placement_mode.findData("legacy_foot")
        )
        app.processEvents()
        assert window.canvas_size.isEnabled()

        window.animation_placement_mode.setCurrentIndex(
            window.animation_placement_mode.findData("preserve_motion")
        )
        app.processEvents()
        assert not window.canvas_size.isEnabled()
        assert "配置・原点" in window.canvas_size.toolTip()
    finally:
        window.close()


def test_sheet_tab_title_follows_the_purpose(monkeypatch):
    """3枚目のプレビュータブは中身が入れ替わるので、名前も合わせる。"""
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from pixel_tile_compiler.gui.main_window import MainWindow

    app = _app()
    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        sheet_index = window.preview_tabs.indexOf(window.tile_preview.parentWidget())
        window.purpose.setCurrentIndex(window.purpose.findData("terrain"))
        app.processEvents()
        assert window.preview_tabs.tabText(sheet_index) == "タイル繰り返し確認"

        window.purpose.setCurrentIndex(window.purpose.findData("character_animation"))
        app.processEvents()
        assert window.preview_tabs.tabText(sheet_index) == "アニメーションシート"
    finally:
        window.close()


def test_main_window_shows_the_measured_output_palette(monkeypatch, tmp_path):
    """コンパイル結果の実測paletteを、キャラクター側でも色見本で見せる。"""
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PIL import Image

    from pixel_tile_compiler.gui.main_window import MainWindow

    app = _app()
    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        titles = [window.preview_tabs.tabText(index) for index in range(window.preview_tabs.count())]
        assert "palette" in titles
        assert window.output_palette_view.count() == 0

        final = tmp_path / "final.png"
        image = Image.new("RGBA", (2, 2), (0, 0, 0, 0))
        image.putpixel((0, 0), (255, 0, 0, 255))
        image.putpixel((1, 0), (0, 128, 255, 255))
        image.save(final)

        window.show_output_palette([final])
        app.processEvents()
        assert window.output_palette_view.count() == 2
        assert "2色" in window.output_palette_info.text()

        # 設定を変えて結果が古くなったら色見本も消える
        window._compiled_canvas_size = (128, 128)
        window._clear_stale_result()
        app.processEvents()
        assert window.output_palette_view.count() == 0
    finally:
        window.close()


def test_output_palette_unions_every_animation_frame(monkeypatch, tmp_path):
    """アニメーションは全コマの和集合を実測paletteとして見せる。"""
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PIL import Image

    from pixel_tile_compiler.gui.main_window import MainWindow

    app = _app()
    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        frames = []
        for index, color in enumerate(((255, 0, 0, 255), (0, 255, 0, 255), (255, 0, 0, 255))):
            path = tmp_path / f"frame_{index}.png"
            Image.new("RGBA", (1, 1), color).save(path)
            frames.append(path)

        window.show_output_palette(frames)
        app.processEvents()
        assert window.output_palette_view.count() == 2
        assert "2色" in window.output_palette_info.text()
    finally:
        window.close()


def _canvas_index(window, size):
    """出力Canvasサイズのindexを返す。findDataはタプルのuserDataに効かないため走査する。"""
    for index in range(window.canvas_size.count()):
        if window.canvas_size.itemData(index) == size:
            return index
    raise AssertionError(f"プリセットに {size} がありません")


def test_main_window_exposes_extended_canvas_presets(monkeypatch):
    """出力Canvasサイズに256と非正方のプリセットが並び、自由入力も選べる。"""
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from pixel_tile_compiler.gui.main_window import MainWindow

    app = _app()
    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        sizes = [
            window.canvas_size.itemData(index)
            for index in range(window.canvas_size.count())
        ]
        assert (256, 256) in sizes
        assert (256, 128) in sizes
        assert (224, 126) in sizes
        # 既存の並び（先頭が推奨の128、次が64）は維持する
        assert sizes[0] == (128, 128)
        assert sizes[1] == (64, 64)
        # 末尾は自由入力（サイズを持たない）
        assert sizes[-1] is None
        assert "自由入力" in window.canvas_size.itemText(window.canvas_size.count() - 1)
    finally:
        window.close()


def test_main_window_custom_canvas_size_drives_the_profile(monkeypatch):
    """自由入力を選ぶと幅・高さの入力が現れ、その値が出力Canvasになる。"""
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from pixel_tile_compiler.gui.main_window import MainWindow

    app = _app()
    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        assert window.selected_canvas_size() == (128, 128)
        # 親の行ごと隠れるため、子ウィジェット自身のisHiddenではなくisVisibleで見る
        assert not window.canvas_width.isVisible()

        window.canvas_size.setCurrentIndex(window.canvas_size.count() - 1)
        app.processEvents()
        assert window.canvas_width.isVisible()
        assert window.canvas_height.isVisible()

        window.canvas_width.setValue(320)
        window.canvas_height.setValue(180)
        app.processEvents()
        assert window.selected_canvas_size() == (320, 180)
        assert window.canvas.state.canvas_size == (320, 180)
    finally:
        window.close()


def test_main_window_warns_effective_resolution_for_non_square(monkeypatch):
    """非正方を選ぶと、実効解像度が短辺で決まることを表示する。"""
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from pixel_tile_compiler.gui.main_window import MainWindow

    app = _app()
    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        window.canvas_size.setCurrentIndex(_canvas_index(window, (128, 128)))
        app.processEvents()
        assert window.canvas_effective_note.isHidden()

        window.canvas_size.setCurrentIndex(_canvas_index(window, (256, 128)))
        app.processEvents()
        assert window.canvas_effective_note.isVisible()
        assert "短辺" in window.canvas_effective_note.text()
    finally:
        window.close()


def test_main_window_exposes_background_and_composition_choices(monkeypatch):
    """背景3択と構図2択がGUIに並ぶ。"""
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from pixel_tile_compiler.gui.main_window import MainWindow

    app = _app()
    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        backgrounds = [
            window.background_mode.itemData(index)
            for index in range(window.background_mode.count())
        ]
        compositions = [
            window.composition_mode.itemData(index)
            for index in range(window.composition_mode.count())
        ]
        assert backgrounds == ["auto", "alpha", "color"]
        assert compositions == ["single_frame", "pre_aligned"]
        # 既定は従来の挙動
        assert window.background_mode.currentData() == "auto"
        assert window.composition_mode.currentData() == "single_frame"
        assert window.settings_tabs.isAncestorOf(window.background_mode)
        assert window.settings_tabs.isAncestorOf(window.composition_mode)
    finally:
        window.close()


def test_main_window_background_color_field_appears_only_for_color_mode(monkeypatch):
    """背景色の入力は「指定した色を透過にする」を選んだときだけ出す。"""
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from pixel_tile_compiler.gui.main_window import MainWindow

    app = _app()
    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        assert window.background_color_field.isHidden()
        window.background_mode.setCurrentIndex(window.background_mode.findData("color"))
        app.processEvents()
        assert window.background_color_field.isVisible()
    finally:
        window.close()


def test_main_window_hides_the_margin_note_for_full_frame_composition(monkeypatch):
    """画面全体構図では余白が出ないので、短辺の注記も出さない。"""
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    from pixel_tile_compiler.gui.main_window import MainWindow

    app = _app()
    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        window.canvas_size.setCurrentIndex(_canvas_index(window, (256, 128)))
        app.processEvents()
        assert window.canvas_effective_note.isVisible()

        window.composition_mode.setCurrentIndex(
            window.composition_mode.findData("pre_aligned")
        )
        app.processEvents()
        assert window.canvas_effective_note.isHidden()
    finally:
        window.close()


def test_main_window_background_and_composition_reach_the_compiler_config(monkeypatch, tmp_path):
    """GUIの選択がそのままコンパイラ設定へ渡る。"""
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PIL import Image

    import pixel_tile_compiler.gui.main_window as main_window_module
    from pixel_tile_compiler.gui.main_window import MainWindow

    app = _app()
    window = MainWindow()
    source = tmp_path / "asset.png"
    Image.new("RGBA", (64, 64), (120, 90, 60, 255)).save(source)
    window.show()
    app.processEvents()
    try:
        assert window.set_source_path(source)
        window.output_root_field.setText(str(tmp_path / "out"))
        window.background_mode.setCurrentIndex(window.background_mode.findData("alpha"))
        window.composition_mode.setCurrentIndex(
            window.composition_mode.findData("pre_aligned")
        )
        window.canvas_size.setCurrentIndex(_canvas_index(window, (64, 64)))
        app.processEvents()

        captured = {}
        real_compile = main_window_module.PixelTileCompiler.compile

        def fake_compile(self, source_path, config):
            captured["config"] = config
            return real_compile(self, source_path, config)

        monkeypatch.setattr(main_window_module.PixelTileCompiler, "compile", fake_compile)
        monkeypatch.setattr(window, "_start_compile", lambda operation, context: operation())
        window.compile_image()
        app.processEvents()

        assert captured["config"].background_mode == "alpha"
        assert captured["config"].character_input_mode == "pre_aligned"
        assert captured["config"].canvas.size == (64, 64)
    finally:
        window.close()
