from __future__ import annotations

from pathlib import Path
import time

import pytest
from PIL import Image

from pixel_tile_compiler.config import CompilerConfig
from pixel_tile_compiler.gui.terrain_batch_model import TerrainBatch, TerrainBatchItem, TerrainBatchResult
from pixel_tile_compiler.gui.terrain_batch_service import TerrainBatchService


def _source(path: Path) -> None:
    Image.new("RGBA", (64, 64), (42, 96, 54, 255)).save(path)


def test_terrain_batch_window_keeps_row_selection_separate_from_target_check(tmp_path: Path, monkeypatch) -> None:
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from pixel_tile_compiler.gui.terrain_batch_window import TerrainBatchWindow

    app = QApplication.instance() or QApplication([])
    source = tmp_path / "terrain.png"
    _source(source)
    batch = TerrainBatchService().create_batch(
        [source],
        CompilerConfig(
            output_root=tmp_path / "unused",
            palette_budget=24,
            pixelization_mode="nearest",
            repeat_opt_enabled=False,
            debug_enabled=False,
        ),
        output_root=tmp_path / "batches",
    )
    window = TerrainBatchWindow(service=TerrainBatchService())
    window.set_batch(batch)
    window.show()
    app.processEvents()
    try:
        assert window.pixelization_mode.currentData() == "nearest"
        assert window.palette.value() == 24
        assert window.repeat_opt.currentData() is False
        assert window.table.currentRow() == 0
        assert window.table.item(0, 0).checkState() == Qt.CheckState.Checked

        window.table.setCurrentCell(0, 1)
        window.table.item(0, 0).setCheckState(Qt.CheckState.Unchecked)
        app.processEvents()

        assert window.table.currentRow() == 0
        assert window.batch.items[0].checked is False
        assert window.reference_button.isEnabled() is False
    finally:
        window.close()


def test_main_window_shows_batch_entry_only_for_terrain(monkeypatch) -> None:
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from pixel_tile_compiler.gui.main_window import MainWindow

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    app.processEvents()
    try:
        window.purpose.setCurrentIndex(0)
        app.processEvents()
        assert window.terrain_batch_button.isHidden()
        window.purpose.setCurrentIndex(1)
        app.processEvents()
        assert window.terrain_batch_button.isVisible()
    finally:
        window.close()


def test_batch_window_runs_initial_and_fixed_actions_offscreen(tmp_path: Path, monkeypatch) -> None:
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from pixel_tile_compiler.gui.terrain_batch_window import TerrainBatchWindow

    app = QApplication.instance() or QApplication([])
    source = tmp_path / "terrain.png"
    _source(source)
    service = TerrainBatchService()
    batch = service.create_batch(
        [source],
        CompilerConfig(output_root=tmp_path / "unused", debug_enabled=False),
        output_root=tmp_path / "batches",
    )
    window = TerrainBatchWindow(service=service)
    window.set_batch(batch)
    window.show()
    app.processEvents()

    def wait_for_idle() -> None:
        deadline = time.monotonic() + 10
        while window._running and time.monotonic() < deadline:
            app.processEvents()
        assert window._running is False

    try:
        window.run_initial()
        wait_for_idle()
        assert batch.items[0].latest_success is not None
        window.set_reference_from_selection()
        assert window.reference_result_id is not None
        window.run_fixed()
        wait_for_idle()
        assert batch.items[0].latest_success.phase == "fixed"
    finally:
        if window._thread is not None:
            window._thread.quit()
            window._thread.wait(2000)
        window.close()


def test_batch_completion_refreshes_selected_preview_without_row_switch(tmp_path: Path, monkeypatch) -> None:
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from pixel_tile_compiler.gui.terrain_batch_window import TerrainBatchWindow

    app = QApplication.instance() or QApplication([])
    source = tmp_path / "terrain.png"
    _source(source)
    service = TerrainBatchService()
    batch = service.create_batch(
        [source],
        CompilerConfig(output_root=tmp_path / "unused", debug_enabled=False),
        output_root=tmp_path / "batches",
    )
    window = TerrainBatchWindow(service=service)
    window.set_batch(batch)

    def wait_for_idle() -> None:
        deadline = time.monotonic() + 10
        while window._running and time.monotonic() < deadline:
            app.processEvents()
        assert window._running is False

    try:
        window.run_initial()
        wait_for_idle()
        initial = batch.items[0].latest_success
        assert initial is not None
        assert window.final_preview.path == initial.final_path

        window.set_reference_from_selection()
        window.run_fixed()
        wait_for_idle()
        fixed = batch.items[0].latest_success
        assert fixed is not None
        assert fixed.phase == "fixed"
        assert window.final_preview.path == fixed.final_path
    finally:
        if window._thread is not None:
            window._thread.quit()
            window._thread.wait(2000)
        window.close()


def test_batch_initial_run_uses_settings_changed_after_images_were_added(tmp_path: Path, monkeypatch) -> None:
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from pixel_tile_compiler.gui.terrain_batch_window import TerrainBatchWindow

    app = QApplication.instance() or QApplication([])
    source = tmp_path / "terrain.png"
    _source(source)
    service = TerrainBatchService()
    batch = service.create_batch(
        [source],
        CompilerConfig(
            output_root=tmp_path / "unused",
            palette_budget=24,
            pixelization_mode="nearest",
            repeat_opt_enabled=False,
            debug_enabled=False,
        ),
        output_root=tmp_path / "batches",
    )
    window = TerrainBatchWindow(service=service)
    window.set_batch(batch)
    window.palette.setValue(8)
    window.pixelization_mode.setCurrentIndex(window.pixelization_mode.findData("region"))
    window.run_initial()
    deadline = time.monotonic() + 10
    while window._running and time.monotonic() < deadline:
        app.processEvents()
    try:
        assert window._running is False
        result = batch.items[0].latest_success
        assert result is not None
        assert result.config_snapshot["palette_budget"] == 8
        assert result.config_snapshot["pixelization_mode"] == "region"
        window.set_reference_from_selection()
        window.pixelization_mode.setCurrentIndex(window.pixelization_mode.findData("nearest"))
        window.run_fixed()
        deadline = time.monotonic() + 10
        while window._running and time.monotonic() < deadline:
            app.processEvents()
        fixed = batch.items[0].latest_success
        assert fixed is not None
        assert fixed.phase == "fixed"
        assert fixed.config_snapshot["pixelization_mode"] == "region"
        assert fixed.config_snapshot["repeat_opt_enabled"] is False
    finally:
        if window._thread is not None:
            window._thread.quit()
            window._thread.wait(2000)
        window.close()


def test_batch_window_can_switch_between_before_and_after_final_previews(tmp_path: Path, monkeypatch) -> None:
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from pixel_tile_compiler.gui.terrain_batch_window import TerrainBatchWindow

    app = QApplication.instance() or QApplication([])
    source = tmp_path / "terrain.png"
    _source(source)
    service = TerrainBatchService()
    batch = service.create_batch(
        [source],
        CompilerConfig(output_root=tmp_path / "unused", debug_enabled=False),
        output_root=tmp_path / "batches",
    )
    window = TerrainBatchWindow(service=service)
    window.set_batch(batch)
    window.run_initial()
    deadline = time.monotonic() + 10
    while window._running and time.monotonic() < deadline:
        app.processEvents()
    initial = batch.items[0].latest_success
    assert initial is not None
    window.set_reference_from_selection()
    window.run_fixed()
    deadline = time.monotonic() + 10
    while window._running and time.monotonic() < deadline:
        app.processEvents()
    fixed = batch.items[0].latest_success
    try:
        assert fixed is not None
        assert fixed.phase == "fixed"
        assert window.comparison_mode.findData("before") >= 0
        window.comparison_mode.setCurrentIndex(window.comparison_mode.findData("before"))
        app.processEvents()
        assert window.final_preview.path == initial.final_path
        window.comparison_mode.setCurrentIndex(window.comparison_mode.findData("after"))
        app.processEvents()
        assert window.final_preview.path == fixed.final_path
    finally:
        if window._thread is not None:
            window._thread.quit()
            window._thread.wait(2000)
        window.close()


def test_batch_table_shows_each_actual_palette_as_color_swatches(tmp_path: Path, monkeypatch) -> None:
    pytest.importorskip("PySide6")
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QWidget

    from pixel_tile_compiler.gui.terrain_batch_window import TerrainBatchWindow

    app = QApplication.instance() or QApplication([])
    source = tmp_path / "terrain.png"
    final = tmp_path / "final.png"
    _source(source)
    Image.new("RGBA", (2, 1), (20, 30, 40, 255)).save(final)
    colors = ((20, 30, 40),)
    result = TerrainBatchResult(
        result_id="result-1",
        phase="legacy",
        status="success",
        source_sha256="source-hash",
        final_path=final,
        actual_palette=colors,
    )
    batch = TerrainBatch(
        batch_id="batch-1",
        items=[
            TerrainBatchItem(
                item_id="item-1",
                display_name=source.name,
                source_path=source,
                source_sha256="source-hash",
                source_dimensions=(64, 64),
                config_snapshot=None,
                results=[result],
                status="success",
            )
        ],
        read_only=True,
    )
    window = TerrainBatchWindow()
    window.set_batch(batch)
    app.processEvents()
    try:
        swatches = window.table.cellWidget(0, 4)
        assert isinstance(swatches, QWidget)
        labels = swatches.findChildren(QLabel)
        assert len(labels) == 1
        assert labels[0].toolTip() == "RGB (20, 30, 40) / #141E28"
    finally:
        window.close()
