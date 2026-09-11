import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication

import pixel_tile_compiler.gui.main_window as main_window_module
from pixel_tile_compiler.gui.main_window import MainWindow
from tests.test_gui import (
    _assign_component_rows,
    _component_assignment_source,
    _prepare_component_assignment_window,
)


def _wait_for_compile_finished(window: MainWindow, timeout_ms: int = 5000) -> bool:
    app = QApplication.instance()
    elapsed = 0
    while window._compile_thread is not None and elapsed < timeout_ms:
        if app is not None:
            app.processEvents()
        QThread.msleep(10)
        elapsed += 10
    return window._compile_thread is None


def test_reanalysis_keeps_the_confirmation_when_nothing_changed(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    _app = QApplication.instance() or QApplication([])
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

        # 設定を変えずに再度「パーツを解析」を実行
        window.analyze_component_assignments()
        assert window.wait_for_analysis()

        # 中身が同一の resolved なので確定が維持されていること
        assert window._component_assignment_confirmed is True
    finally:
        window.close()


def test_reanalysis_keeps_the_compile_path_open(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    _app = QApplication.instance() or QApplication([])
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

        # 再解析を実行
        window.analyze_component_assignments()
        assert window.wait_for_analysis()

        captured = {}

        def fake_compile(*args, **kwargs):
            captured["called"] = True
            captured["assignments"] = kwargs.get("component_assignments")
            return object()

        monkeypatch.setattr(main_window_module, "compile_character_animation_sheet", fake_compile)
        monkeypatch.setattr(window, "_start_compile", lambda operation, context: operation())

        window.compile_image()
        assert _wait_for_compile_finished(window)

        assert captured.get("called") is True
        assert "未解決" not in window.status.text()
    finally:
        window.close()


def test_reanalysis_does_not_claim_the_assignment_changed(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    _app = QApplication.instance() or QApplication([])
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

        # 再解析を実行
        window.analyze_component_assignments()
        assert window.wait_for_analysis()

        # 「割り当てを変更しました」と虚偽の表示にならないこと
        status_text = window.animation_component_assignment_status.text()
        assert "割り当てを変更しました" not in status_text
        assert "確定しました" in status_text or "確定するとコンパイルできます" in status_text
    finally:
        window.close()


def test_changing_an_assignment_still_requires_reconfirmation(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

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

        # 割り当てを実際に変更する
        combo = window._component_assignment_combos[satellite_id]
        combo.setCurrentIndex(combo.findData("F2"))
        app.processEvents()

        # 確定が外れていること
        assert window._component_assignment_confirmed is False
        assert "再度割り当てを確定" in window.animation_component_assignment_status.text()

        # コンパイルが未解決でブロックされること
        started = []
        monkeypatch.setattr(window, "_start_compile", lambda op, ctx: started.append((op, ctx)))
        window.compile_image()
        assert not started
        assert "未解決" in window.status.text()
    finally:
        window.close()


def test_changing_the_split_settings_still_clears_the_analysis(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    _app = QApplication.instance() or QApplication([])
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

        # 分割列数を変更
        window.animation_columns.setValue(3)

        # 解析結果および確定状態が破棄されること
        assert window._component_analysis is None
        assert window._component_assignment_confirmed is False
    finally:
        window.close()


def _insufficient_candidates_source(tmp_path):
    from PIL import Image

    source = tmp_path / "insufficient.png"
    img = Image.new("RGBA", (400, 300), (0, 0, 0, 0))
    for r in range(3):
        cols = 3 if r == 1 else 4  # 2行目は3コマしかない！
        for c in range(cols):
            x0 = c * 100 + 20
            y0 = r * 100 + 20
            for y in range(y0, y0 + 60):
                for x in range(x0, x0 + 60):
                    img.putpixel((x, y), (200, 100, 50, 255))
    img.save(source)
    return source


def test_insufficient_candidates_shows_reason_and_blocks_confirmation(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    _app = QApplication.instance() or QApplication([])
    window = MainWindow()
    source = _insufficient_candidates_source(tmp_path)
    try:
        _prepare_component_assignment_window(window, source)
        window.animation_columns.setValue(4)
        window.animation_rows.setValue(3)
        window.analyze_component_assignments()
        assert window.wait_for_analysis()

        # ステータスに理由（2行目の本体候補が不足）が表示されていること
        status_text = window.animation_component_assignment_status.text()
        assert "本体候補が不足" in status_text

        # 確定ボタンを押しても確定されず、エラーが表示されること
        window.confirm_component_assignments()
        assert window._component_assignment_confirmed is False
        assert "確定できません" in window.status.text() or "本体候補が不足" in window.status.text()
    finally:
        window.close()


def test_zero_px_cards_show_not_extracted_warning(monkeypatch, tmp_path):
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    _app = QApplication.instance() or QApplication([])
    window = MainWindow()
    source = _insufficient_candidates_source(tmp_path)
    try:
        _prepare_component_assignment_window(window, source)
        window.animation_columns.setValue(4)
        window.animation_rows.setValue(3)
        window.analyze_component_assignments()
        assert window.wait_for_analysis()

        # カードプレビューのラベルを確認
        layout = window.animation_frame_cards_layout
        card0 = layout.itemAt(0).widget()
        card_texts = [l.text() for l in card0.findChildren(type(window.animation_component_assignment_status))]
        joined = " ".join(card_texts)
        assert "未抽出" in joined or "候補不足" in joined
    finally:
        window.close()

