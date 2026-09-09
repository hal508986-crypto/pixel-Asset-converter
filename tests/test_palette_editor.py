"""基準paletteの書き出し・プリセット・エディタの契約テスト。

仕様: docs/spec/palette_editor_spec.md
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pixel_tile_compiler.palette_contract import load_palette_json, palette_id


def test_save_palette_json_round_trips_through_the_contract(tmp_path: Path):
    from pixel_tile_compiler.palette_contract import save_palette_json

    colors = ((15, 56, 15), (155, 188, 15), (48, 98, 48))
    path = save_palette_json(tmp_path / "palette.json", colors)

    assert path.exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    # 契約どおり昇順へ正規化される
    assert payload["colors"] == [[15, 56, 15], [48, 98, 48], [155, 188, 15]]
    assert payload["palette_id"] == palette_id(colors)
    assert load_palette_json(path) == tuple(sorted(colors))


def test_save_palette_json_rejects_an_invalid_palette(tmp_path: Path):
    from pixel_tile_compiler.palette_contract import save_palette_json

    with pytest.raises(ValueError):
        save_palette_json(tmp_path / "empty.json", ())
    with pytest.raises(ValueError):
        save_palette_json(tmp_path / "too_many.json", tuple((i, 0, 0) for i in range(65)))
    with pytest.raises(ValueError):
        save_palette_json(tmp_path / "bad.json", ((0, 0, 300),))


def test_every_preset_satisfies_the_palette_contract():
    from pixel_tile_compiler.gui.palette_presets import PALETTE_PRESETS
    from pixel_tile_compiler.palette_contract import validate_reference_palette

    assert PALETTE_PRESETS
    for preset in PALETTE_PRESETS:
        normalized = validate_reference_palette(preset.colors)
        # 重複を含めない（正規化で減らない）
        assert len(normalized) == len(preset.colors), preset.name
        assert 1 <= len(normalized) <= 64, preset.name


def test_preset_names_avoid_hardware_and_product_names():
    """プリセット名に実機名・製品名を入れない（仕様4.3節・N-2）。"""
    from pixel_tile_compiler.gui.palette_presets import PALETTE_PRESETS

    forbidden = (
        "ゲームボーイ", "game boy", "gameboy", "ファミコン", "famicom", "nes",
        "スーファミ", "snes", "メガドライブ", "genesis", "pc-98", "msx",
        "commodore", "c64", "任天堂", "nintendo", "sega", "セガ",
    )
    for preset in PALETTE_PRESETS:
        lowered = preset.name.lower()
        for word in forbidden:
            assert word not in lowered, f"{preset.name} に {word} が含まれる"


def test_presets_declare_that_the_colors_are_self_authored():
    """出典が明記されていること（RC-01の根拠を残す）。"""
    from pixel_tile_compiler.gui.palette_presets import PALETTE_PRESET_PROVENANCE

    assert "本リポジトリで作成" in PALETTE_PRESET_PROVENANCE


def _app():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _dialog(monkeypatch, colors=((10, 20, 30), (200, 210, 220)), output_palette=()):
    from pixel_tile_compiler.gui.palette_editor import PaletteEditorDialog

    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    _app()
    return PaletteEditorDialog(colors=colors, output_palette=output_palette)


def test_palette_editor_adds_changes_and_removes_colors(monkeypatch):
    pytest.importorskip("PySide6")
    dialog = _dialog(monkeypatch)
    try:
        dialog.pick_color = lambda _initial=None: (90, 100, 110)
        dialog.add_color()
        assert (90, 100, 110) in dialog.colors()
        assert len(dialog.colors()) == 3

        dialog.select_index(0)
        dialog.pick_color = lambda _initial=None: (1, 2, 3)
        dialog.change_selected_color()
        assert (1, 2, 3) in dialog.colors()

        before = len(dialog.colors())
        dialog.select_index(0)
        dialog.remove_selected_color()
        assert len(dialog.colors()) == before - 1
    finally:
        dialog.close()


def test_palette_editor_refuses_duplicate_colors(monkeypatch):
    pytest.importorskip("PySide6")
    dialog = _dialog(monkeypatch, colors=((10, 20, 30),))
    try:
        dialog.pick_color = lambda _initial=None: (10, 20, 30)
        dialog.add_color()
        assert dialog.colors() == ((10, 20, 30),)
    finally:
        dialog.close()


def test_palette_editor_keeps_at_least_one_color(monkeypatch):
    pytest.importorskip("PySide6")
    dialog = _dialog(monkeypatch, colors=((10, 20, 30),))
    try:
        dialog.select_index(0)
        dialog.remove_selected_color()
        assert dialog.colors() == ((10, 20, 30),)
    finally:
        dialog.close()


def test_palette_editor_stops_adding_at_the_upper_limit(monkeypatch):
    pytest.importorskip("PySide6")
    full = tuple((value, 0, 0) for value in range(64))
    dialog = _dialog(monkeypatch, colors=full)
    try:
        assert len(dialog.colors()) == 64
        assert not dialog.add_button.isEnabled()
        dialog.pick_color = lambda _initial=None: (0, 1, 2)
        dialog.add_color()
        assert len(dialog.colors()) == 64
    finally:
        dialog.close()


def test_palette_editor_loads_a_preset(monkeypatch):
    pytest.importorskip("PySide6")
    from pixel_tile_compiler.gui.palette_presets import PALETTE_PRESETS

    dialog = _dialog(monkeypatch)
    try:
        target = PALETTE_PRESETS[0]
        dialog.apply_preset(target.name)
        assert dialog.colors() == tuple(sorted(target.colors))
    finally:
        dialog.close()


def test_palette_editor_imports_the_output_palette(monkeypatch):
    pytest.importorskip("PySide6")
    measured = ((5, 5, 5), (250, 250, 250), (128, 64, 32))
    dialog = _dialog(monkeypatch, output_palette=measured)
    try:
        assert dialog.import_button.isEnabled()
        dialog.import_output_palette()
        assert dialog.colors() == tuple(sorted(measured))
    finally:
        dialog.close()


def test_palette_editor_disables_import_without_an_output_palette(monkeypatch):
    pytest.importorskip("PySide6")
    dialog = _dialog(monkeypatch, output_palette=())
    try:
        assert not dialog.import_button.isEnabled()
    finally:
        dialog.close()


def test_palette_editor_round_trips_through_palette_json(monkeypatch, tmp_path: Path):
    pytest.importorskip("PySide6")
    dialog = _dialog(monkeypatch, colors=((10, 20, 30), (40, 50, 60)))
    try:
        path = tmp_path / "mine.json"
        dialog.save_to(path)
        dialog.apply_preset(None)
        dialog.set_colors(((1, 1, 1),))
        dialog.load_from(path)
        assert dialog.colors() == ((10, 20, 30), (40, 50, 60))
    finally:
        dialog.close()


def test_main_window_opens_the_palette_editor_and_applies_the_result(monkeypatch, tmp_path: Path):
    """編集ボタンからダイアログを開き、OKした色が基準paletteになる。"""
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QDialog

    import pixel_tile_compiler.gui.main_window as main_window_module
    from pixel_tile_compiler.gui.main_window import MainWindow

    app = _app()
    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        chosen = ((10, 20, 30), (200, 210, 220), (99, 99, 99))

        def fake_exec(dialog):
            dialog.set_colors(chosen)
            return QDialog.DialogCode.Accepted

        monkeypatch.setattr(
            main_window_module.PaletteEditorDialog, "exec", fake_exec, raising=False
        )
        window.edit_shared_palette()
        app.processEvents()

        assert window.shared_palette_colors == tuple(sorted(chosen))
        assert window.shared_palette_view.count() == 3
        assert "3色" in window.shared_palette_info.text()
    finally:
        window.close()


def test_main_window_palette_editor_cancel_keeps_the_current_palette(monkeypatch):
    """キャンセルすると基準paletteは変わらない。"""
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QDialog

    import pixel_tile_compiler.gui.main_window as main_window_module
    from pixel_tile_compiler.gui.main_window import MainWindow

    app = _app()
    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        window.set_shared_palette(((1, 2, 3), (4, 5, 6)), source_label="事前")
        before = window.shared_palette_colors

        def fake_exec(dialog):
            dialog.set_colors(((90, 90, 90),))
            return QDialog.DialogCode.Rejected

        monkeypatch.setattr(
            main_window_module.PaletteEditorDialog, "exec", fake_exec, raising=False
        )
        window.edit_shared_palette()
        app.processEvents()
        assert window.shared_palette_colors == before
    finally:
        window.close()
