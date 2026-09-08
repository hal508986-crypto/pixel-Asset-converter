"""PySide6 window for terrain-only batch palette comparison."""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, Signal, Slot
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import (
    QFileDialog,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QAbstractItemView,
    QVBoxLayout,
    QWidget,
    QMainWindow,
)

from pixel_tile_compiler.config import CanvasSpec, CompilerConfig, compiler_config_for_purpose
from pixel_tile_compiler.gui.policy import GUI_TERRAIN_PIXELIZATION_OPTIONS, resolve_terrain_gui_profile
from pixel_tile_compiler.gui.terrain_batch_model import TerrainBatch, TerrainBatchProgress, TerrainBatchRun
from pixel_tile_compiler.palette_contract import palette_id, validate_reference_palette
from pixel_tile_compiler.gui.terrain_batch_service import TerrainBatchService, load_legacy_manifest


CHECKER_LIGHT = QColor("#d6d9dd")
CHECKER_DARK = QColor("#b9bec5")
STATUS_LABELS = {
    "waiting": "待機中",
    "running": "処理中",
    "success": "成功",
    "failed": "失敗",
    "cancelled": "中止",
    "partial": "一部失敗",
}


def _draw_batch_checkerboard(painter: QPainter, rect, tile_size: int = 8) -> None:  # type: ignore[no-untyped-def]
    left = int(rect.left()) - int(rect.left()) % tile_size
    top = int(rect.top()) - int(rect.top()) % tile_size
    right = int(rect.right()) + tile_size
    bottom = int(rect.bottom()) + tile_size
    painter.setPen(Qt.PenStyle.NoPen)
    for y in range(top, bottom, tile_size):
        for x in range(left, right, tile_size):
            color = CHECKER_LIGHT if ((x // tile_size + y // tile_size) % 2 == 0) else CHECKER_DARK
            painter.fillRect(x, y, tile_size, tile_size, color)


class BatchImagePreview(QWidget):
    """Small nearest-neighbor preview that keeps alpha visible."""

    def __init__(self, empty_text: str) -> None:
        super().__init__()
        self._path: Path | None = None
        self._empty_text = empty_text
        self.setMinimumSize(220, 160)
        self.setToolTip("透明部分は市松模様で表示します")

    def set_image(self, path: Path | None) -> None:
        self._path = Path(path) if path is not None else None
        self.update()

    @property
    def path(self) -> Path | None:
        return self._path

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        del event
        painter = QPainter(self)
        _draw_batch_checkerboard(painter, self.rect())
        if self._path is None or not self._path.exists():
            painter.setPen(QColor("#434a54"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._empty_text)
            return
        image = QImage(str(self._path)).convertToFormat(QImage.Format.Format_RGBA8888)
        scaled = image.scaled(
            max(1, self.width() - 16),
            max(1, self.height() - 16),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        painter.drawImage((self.width() - scaled.width()) // 2, (self.height() - scaled.height()) // 2, scaled)


class PaletteSwatches(QWidget):
    """Inline, visible RGB swatches for one row's actual final palette."""

    def __init__(self, colors) -> None:  # type: ignore[no-untyped-def]
        super().__init__()
        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)
        layout.setSpacing(3)
        if not colors:
            layout.addWidget(QLabel("なし"))
            return
        for red, green, blue in colors:
            hex_color = f"#{red:02X}{green:02X}{blue:02X}"
            label = QLabel(hex_color)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setMinimumWidth(58)
            label.setFixedHeight(24)
            label.setStyleSheet(
                f"background-color: rgb({red}, {green}, {blue}); "
                f"color: {'#ffffff' if red + green + blue < 390 else '#20252c'}; "
                "border: 1px solid #6d7682; border-radius: 3px;"
            )
            label.setToolTip(f"RGB ({red}, {green}, {blue}) / {hex_color}")
            layout.addWidget(label)
        layout.addStretch(1)


class TerrainBatchWorker(QObject):
    """Run one already-frozen batch snapshot away from the Qt GUI thread."""

    progress = Signal(object)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(
        self,
        service: TerrainBatchService,
        batch: TerrainBatch,
        snapshot,
        cancel_event: threading.Event,
    ) -> None:
        super().__init__()
        self.service = service
        self.batch = batch
        self.snapshot = snapshot
        self.cancel_event = cancel_event

    @Slot()
    def run(self) -> None:
        try:
            result = self.service.execute(
                self.batch,
                self.snapshot,
                on_progress=self.progress.emit,
                cancel_requested=self.cancel_event.is_set,
            )
        except Exception as exc:  # pragma: no cover - GUI boundary
            self.failed.emit(str(exc))
            return
        self.finished.emit(result)


class TerrainBatchWindow(QMainWindow):
    """Terrain-only batch workflow; the core service remains Qt independent."""

    reference_palette_changed = Signal(object)

    def __init__(
        self,
        *,
        service: TerrainBatchService | None = None,
        output_root: Path | str | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("地形タイル一括コンパイル")
        self.resize(1280, 820)
        self.service = service or TerrainBatchService()
        self.batch: TerrainBatch | None = None
        self.reference_result_id: str | None = None
        self._running = False
        self._cancel_event = threading.Event()
        self._thread: QThread | None = None
        self._worker: TerrainBatchWorker | None = None

        self.output_root_field = QLineEdit(str(output_root or (Path.cwd() / "Output" / "batches")))
        self.output_browse_button = QPushButton("参照...")
        self.output_browse_button.clicked.connect(self.choose_output_directory)
        self.pixelization_mode = QComboBox()
        for label, mode in GUI_TERRAIN_PIXELIZATION_OPTIONS:
            self.pixelization_mode.addItem(label, userData=mode)
        self.palette = QSpinBox()
        self.palette.setRange(4, 64)
        self.palette.setValue(24)
        self.repeat_opt = QComboBox()
        self.repeat_opt.addItem("有効", userData=True)
        self.repeat_opt.addItem("無効", userData=False)
        self.repeat_opt.setCurrentIndex(1)

        self.add_button = QPushButton("画像を追加")
        self.add_button.clicked.connect(self.add_images)
        self.load_manifest_button = QPushButton("試作結果を読み込む")
        self.load_manifest_button.clicked.connect(self.load_manifest)
        self.remove_button = QPushButton("選択項目を削除")
        self.remove_button.clicked.connect(self.remove_selected)
        self.run_button = QPushButton("まとめて変換")
        self.run_button.setObjectName("primaryButton")
        self.run_button.clicked.connect(self.run_initial)
        self.reference_button = QPushButton("基準パレットにする")
        self.reference_button.clicked.connect(self.set_reference_from_selection)
        self.clear_reference_button = QPushButton("基準を解除")
        self.clear_reference_button.clicked.connect(self.clear_reference)
        self.fixed_button = QPushButton("基準パレットで再変換")
        self.fixed_button.setObjectName("primaryButton")
        self.fixed_button.clicked.connect(self.run_fixed)
        self.cancel_button = QPushButton("現在の処理後に中止")
        self.cancel_button.clicked.connect(self.cancel_run)
        self.cancel_button.setEnabled(False)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["対象", "ファイル名", "状態", "使用色数", "パレット"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.currentCellChanged.connect(self._on_current_row_changed)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.setMinimumHeight(250)
        self.table.setAlternatingRowColors(True)

        self.original_preview = BatchImagePreview("元絵")
        self.final_preview = BatchImagePreview("final.png")
        self.comparison_mode = QComboBox()
        self.comparison_mode.addItem("統一後", userData="after")
        self.comparison_mode.addItem("統一前", userData="before")
        self.comparison_mode.currentIndexChanged.connect(self._on_comparison_mode_changed)
        self.selected_info = QLabel("項目を選択すると比較を表示します")
        self.reference_info = QLabel("基準パレット: 未選択")
        self.reference_palette = QListWidget()
        self.reference_palette.setFlow(QListWidget.Flow.LeftToRight)
        self.reference_palette.setMaximumHeight(66)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.status = QLabel("画像を追加するか、既存の試作結果を読み込んでください")
        self.batch_info = QLabel("バッチ未作成")
        self._build_ui()
        self._update_actions()

    def _build_ui(self) -> None:
        source_buttons = QHBoxLayout()
        source_buttons.addWidget(self.add_button)
        source_buttons.addWidget(self.load_manifest_button)
        source_buttons.addWidget(self.remove_button)

        settings = QGroupBox("1. 一括変換条件")
        form = QFormLayout(settings)
        form.addRow("変換方法", self.pixelization_mode)
        form.addRow("パレット色数", self.palette)
        form.addRow("繰り返し最適化", self.repeat_opt)
        output_row = QWidget()
        output_layout = QHBoxLayout(output_row)
        output_layout.setContentsMargins(0, 0, 0, 0)
        output_layout.addWidget(self.output_root_field, 1)
        output_layout.addWidget(self.output_browse_button)
        form.addRow("保存先", output_row)

        list_group = QGroupBox("2. 対象画像")
        list_layout = QVBoxLayout(list_group)
        list_layout.addLayout(source_buttons)
        list_layout.addWidget(self.table)
        list_layout.addWidget(self.batch_info)

        compare_group = QGroupBox("3. 選択項目の比較")
        compare_layout = QVBoxLayout(compare_group)
        compare_layout.addWidget(self.selected_info)
        compare_mode_row = QHBoxLayout()
        compare_mode_row.addWidget(QLabel("比較表示"))
        compare_mode_row.addWidget(self.comparison_mode)
        compare_mode_row.addStretch(1)
        compare_layout.addLayout(compare_mode_row)
        previews = QHBoxLayout()
        before = QVBoxLayout()
        before.addWidget(QLabel("元絵"))
        before.addWidget(self.original_preview)
        after = QVBoxLayout()
        after.addWidget(QLabel("final.png"))
        after.addWidget(self.final_preview)
        previews.addLayout(before)
        previews.addLayout(after)
        compare_layout.addLayout(previews)

        palette_group = QGroupBox("4. 基準パレット")
        palette_layout = QVBoxLayout(palette_group)
        palette_layout.addWidget(self.reference_button)
        palette_layout.addWidget(self.clear_reference_button)
        palette_layout.addWidget(self.reference_info)
        palette_layout.addWidget(self.reference_palette)
        palette_layout.addWidget(QLabel("実測したfinal.pngの可視RGBだけを基準にします。色数は水増ししません。"))

        action_row = QHBoxLayout()
        action_row.addWidget(self.run_button)
        action_row.addWidget(self.fixed_button)
        action_row.addWidget(self.cancel_button)
        action_row.addWidget(self.progress, 1)

        root = QVBoxLayout()
        root.setContentsMargins(16, 14, 16, 14)
        root.addWidget(settings)
        root.addWidget(list_group, 1)
        root.addWidget(compare_group)
        root.addWidget(palette_group)
        root.addLayout(action_row)
        root.addWidget(self.status)
        container = QWidget()
        container.setLayout(root)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setWidget(container)
        self.setCentralWidget(scroll_area)
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #1d2127; color: #eef1f5; }
            QGroupBox { border: 1px solid #3a424d; border-radius: 8px; margin-top: 12px; padding: 12px 10px 10px; background: #252a31; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; color: #f0c674; }
            QComboBox, QSpinBox, QLineEdit { min-height: 30px; border: 1px solid #48515d; border-radius: 5px; padding: 2px 8px; background: #303640; color: #f4f6f8; }
            QPushButton { min-height: 34px; border: 1px solid #556171; border-radius: 6px; padding: 0 12px; background: #343c48; color: #f4f6f8; font-weight: 600; }
            QPushButton:disabled { color: #78818d; background: #2a2f36; }
            QPushButton#primaryButton { background: #3565a8; border-color: #5d8ed5; }
            QTableWidget, QListWidget { background: #20252c; border: 1px solid #48515d; alternate-background-color: #292f38; }
            """
        )

    def _config(self) -> CompilerConfig:
        profile = resolve_terrain_gui_profile(self.pixelization_mode.currentData())
        return compiler_config_for_purpose(
            "terrain",
            output_root=Path("unused"),
            canvas=CanvasSpec(64, 64),
            palette_budget=self.palette.value(),
            pixelization_mode=profile.pixelization_mode,
            repeat_opt_enabled=bool(self.repeat_opt.currentData()),
            debug_enabled=True,
        )

    def _output_root(self) -> Path:
        value = self.output_root_field.text().strip()
        if not value:
            raise ValueError("保存先を指定してください")
        return Path(value).expanduser()

    def set_batch(self, batch: TerrainBatch) -> None:
        """Set the model displayed by the window, including imported read-only state."""
        self.batch = batch
        self.reference_result_id = None
        self.reference_info.setText("基準パレット: 未選択")
        self.reference_palette.clear()
        self.reference_palette_changed.emit(())
        self._sync_controls_from_batch()
        self._refresh_table()
        self.status.setText("バッチを読み込みました。行を選択して比較できます")

    def add_images(self) -> None:
        if self._running:
            return
        paths, _ = QFileDialog.getOpenFileNames(self, "地形画像を追加", "", "画像 (*.png *.jpg *.jpeg *.webp)")
        if not paths:
            return
        try:
            if self.batch is None:
                self.batch = self.service.create_batch(paths, self._config(), output_root=self._output_root())
            else:
                self._ensure_writable()
                added = self.service.add_sources(self.batch, paths, self._config())
                if not added:
                    raise ValueError("新しく追加できる画像がありません")
            self._refresh_table()
            self.status.setText(f"{len(paths)}枚の画像を受け付けました")
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.setText(f"画像を追加できませんでした: {exc}")
        self._update_actions()

    def load_manifest(self) -> None:
        if self._running:
            return
        path, _ = QFileDialog.getOpenFileName(self, "試作manifestを読み込む", "", "JSON (*.json)")
        if not path:
            return
        try:
            self.set_batch(load_legacy_manifest(Path(path)))
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.setText(f"試作manifestを読み込めませんでした: {exc}")

    def remove_selected(self) -> None:
        if self._running or self.batch is None:
            return
        row = self.table.currentRow()
        if row < 0 or row >= len(self.batch.items):
            return
        try:
            self._ensure_writable()
            item_id = self.batch.items[row].item_id
            self.service.remove_items(self.batch, [item_id])
            self.reference_result_id = None
            self._refresh_table()
            self.status.setText("選択項目を削除しました")
        except RuntimeError as exc:
            self.status.setText(str(exc))
        self._update_actions()

    def choose_output_directory(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "保存先を選択", self.output_root_field.text())
        if path:
            self.output_root_field.setText(path)

    def _ensure_writable(self) -> None:
        if self.batch is None:
            raise ValueError("先に画像を追加するか試作結果を読み込んでください")
        if self.batch.read_only:
            self.batch = self.service.make_writable(self.batch, output_root=self._output_root())

    def run_initial(self) -> None:
        if self._running:
            return
        try:
            self._ensure_writable()
            assert self.batch is not None
            target_ids = [item.item_id for item in self.batch.items if item.checked]
            snapshot = self.service.prepare_initial_run(self.batch, target_ids, config=self._config())
            self._start_execution(snapshot)
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.setText(f"一括変換を開始できませんでした: {exc}")

    def set_reference_from_selection(self) -> None:
        if self.batch is None:
            return
        row = self.table.currentRow()
        if row < 0 or row >= len(self.batch.items):
            return
        item = self.batch.items[row]
        result = item.latest_success
        if result is None:
            self.status.setText("成功したfinal.pngがある項目を選択してください")
            return
        try:
            colors = validate_reference_palette(result.actual_palette)
        except ValueError as exc:
            self.status.setText(f"基準パレットにできません: {exc}")
            return
        self.reference_result_id = result.result_id
        self.reference_info.setText(f"基準: {item.display_name} / {result.result_id} / {len(colors)}色 / {palette_id(colors)[:12]}")
        self._set_palette_list(colors)
        self.reference_palette_changed.emit(colors)
        self.status.setText("基準パレットを設定しました。対象をチェックして再変換できます")
        self._update_actions()

    def clear_reference(self) -> None:
        if self._running:
            return
        self.reference_result_id = None
        self.reference_info.setText("基準パレット: 未選択")
        self.reference_palette.clear()
        self.reference_palette_changed.emit(())
        self.status.setText("基準パレットを解除しました")
        self._update_actions()

    def run_fixed(self) -> None:
        if self._running or self.batch is None or self.reference_result_id is None:
            return
        try:
            self._ensure_writable()
            assert self.batch is not None
            target_ids = [item.item_id for item in self.batch.items if item.checked]
            snapshot = self.service.prepare_fixed_run(
                self.batch,
                reference_result_id=self.reference_result_id,
                target_item_ids=target_ids,
            )
            self._start_execution(snapshot)
        except (OSError, ValueError, RuntimeError) as exc:
            self.status.setText(f"固定パレット再変換を開始できませんでした: {exc}")

    def cancel_run(self) -> None:
        if self._running:
            self._cancel_event.set()
            self.status.setText("中止を受け付けました。現在の項目の完了後に停止します")

    def _start_execution(self, snapshot) -> None:
        assert self.batch is not None
        self._running = True
        self._cancel_event.clear()
        self.progress.setRange(0, len(snapshot.items))
        self.progress.setValue(0)
        self._set_processing_controls(False)
        self._refresh_table(select_current=True)
        self._thread = QThread(self)
        self._worker = TerrainBatchWorker(self.service, self.batch, snapshot, self._cancel_event)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._thread.finished.connect(self._thread.deleteLater)
        self._thread.start()
        self.status.setText(f"{len(snapshot.items)}件の処理を開始しました")

    @Slot(object)
    def _on_progress(self, update: TerrainBatchProgress) -> None:
        self.progress.setValue(update.index)
        self.status.setText(f"{update.index}/{update.total}: {STATUS_LABELS.get(update.message, update.message)}")
        self._refresh_table(select_current=True)

    @Slot(object)
    def _on_finished(self, run: TerrainBatchRun) -> None:
        self._finish_worker()
        self._refresh_table(select_current=True)
        self.status.setText(f"完了: {STATUS_LABELS.get(run.status, run.status)} / {len(run.results)}件")

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self._finish_worker()
        self.status.setText(f"一括変換に失敗しました: {message}")

    def _finish_worker(self) -> None:
        self._running = False
        self._set_processing_controls(True)
        if self._thread is not None:
            self._thread.quit()
        self._worker = None
        self._thread = None
        self._update_actions()

    def _set_processing_controls(self, enabled: bool) -> None:
        for widget in (
            self.add_button,
            self.load_manifest_button,
            self.remove_button,
            self.output_root_field,
            self.output_browse_button,
            self.pixelization_mode,
            self.palette,
            self.repeat_opt,
            self.run_button,
            self.reference_button,
            self.fixed_button,
            self.clear_reference_button,
        ):
            widget.setEnabled(enabled)
        self.cancel_button.setEnabled(not enabled)

    def _refresh_table(self, *, select_current: bool = False) -> None:
        if self.batch is None:
            self.table.setRowCount(0)
            self.batch_info.setText("バッチ未作成")
            self._update_actions()
            return
        current = self.table.currentRow()
        self.table.blockSignals(True)
        self.table.setRowCount(len(self.batch.items))
        for row, item in enumerate(self.batch.items):
            check = QTableWidgetItem()
            flags = check.flags()
            if not self._running:
                flags |= Qt.ItemFlag.ItemIsUserCheckable
            else:
                flags &= ~Qt.ItemFlag.ItemIsUserCheckable
            check.setFlags(flags)
            check.setCheckState(Qt.CheckState.Checked if item.checked else Qt.CheckState.Unchecked)
            self.table.setItem(row, 0, check)
            self.table.setItem(row, 1, QTableWidgetItem(item.display_name))
            self.table.setItem(row, 2, QTableWidgetItem(STATUS_LABELS.get(item.status, item.status)))
            result = item.latest_success
            count = result.actual_palette_count if result is not None else 0
            self.table.setItem(row, 3, QTableWidgetItem(str(count) if count else "-"))
            self.table.setCellWidget(row, 4, PaletteSwatches(result.actual_palette if result is not None else ()))
            self.table.setRowHeight(row, 30)
        self.table.blockSignals(False)
        if self.table.rowCount():
            row = current if select_current and 0 <= current < self.table.rowCount() else max(0, min(current, self.table.rowCount() - 1))
            self.table.setCurrentCell(row, 1)
            self._refresh_selected_row_detail()
        self.batch_info.setText(f"{len(self.batch.items)}件 / {'読み取り専用' if self.batch.read_only else '保存可能'}")
        self._update_actions()

    def _refresh_selected_row_detail(self) -> None:
        """Refresh detail and previews even when the current cell did not change."""
        self._on_current_row_changed(self.table.currentRow())

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self.batch is None or self._running or item.column() != 0:
            return
        row = item.row()
        if 0 <= row < len(self.batch.items):
            self.batch.items[row].checked = item.checkState() == Qt.CheckState.Checked
            self._update_actions()

    def _on_current_row_changed(self, row: int, *_args) -> None:  # type: ignore[no-untyped-def]
        if self.batch is None or not 0 <= row < len(self.batch.items):
            self.original_preview.set_image(None)
            self.final_preview.set_image(None)
            return
        item = self.batch.items[row]
        result = item.latest_success
        self.original_preview.set_image(item.source_path)
        self._update_comparison_preview(item, result)
        result_id = result.result_id if result is not None else "結果なし"
        self.selected_info.setText(
            f"{item.display_name} / {STATUS_LABELS.get(item.status, item.status)} / {result_id} / {result.actual_palette_count if result else 0}色"
        )
        self._update_actions()

    def _on_comparison_mode_changed(self, _index: int) -> None:
        if self.batch is None:
            return
        row = self.table.currentRow()
        if not 0 <= row < len(self.batch.items):
            return
        item = self.batch.items[row]
        self._update_comparison_preview(item, item.latest_success)

    def _update_comparison_preview(self, item, latest) -> None:  # type: ignore[no-untyped-def]
        result = latest
        if self.comparison_mode.currentData() == "before" and latest is not None and latest.previous_result_id:
            result = item.result_by_id(latest.previous_result_id)
        self.final_preview.set_image(result.final_path if result is not None else None)

    def _set_palette_list(self, colors) -> None:  # type: ignore[no-untyped-def]
        self.reference_palette.clear()
        for red, green, blue in colors:
            item = QListWidgetItem(f"#{red:02X}{green:02X}{blue:02X}")
            item.setBackground(QColor(red, green, blue))
            item.setForeground(QColor("#ffffff" if red + green + blue < 390 else "#20252c"))
            item.setToolTip(f"RGB ({red}, {green}, {blue})")
            self.reference_palette.addItem(item)

    def _update_actions(self) -> None:
        if self._running:
            return
        has_batch = self.batch is not None and bool(self.batch.items)
        has_targets = has_batch and any(item.checked for item in self.batch.items)
        reference = self.batch.result_by_id(self.reference_result_id) if has_batch and self.reference_result_id else None
        has_reference = False
        if reference is not None and reference.status == "success":
            try:
                validate_reference_palette(reference.actual_palette)
            except ValueError:
                has_reference = False
            else:
                has_reference = True
        self.run_button.setEnabled(bool(has_targets))
        selected = self.batch.items[self.table.currentRow()] if has_batch and 0 <= self.table.currentRow() < len(self.batch.items) else None
        selected_result = selected.latest_success if selected is not None else None
        selected_eligible = False
        if selected_result is not None:
            try:
                validate_reference_palette(selected_result.actual_palette)
            except ValueError:
                selected_eligible = False
            else:
                selected_eligible = True
        self.reference_button.setEnabled(selected_eligible)
        self.fixed_button.setEnabled(bool(has_reference and has_targets))
        self.clear_reference_button.setEnabled(has_reference)
        self.palette.setEnabled(not has_reference)

    def _sync_controls_from_batch(self) -> None:
        if self.batch is None:
            return
        config = next((item.runtime_config for item in self.batch.items if item.runtime_config is not None), None)
        if config is None:
            return
        mode_index = self.pixelization_mode.findData(config.pixelization_mode)
        if mode_index >= 0:
            self.pixelization_mode.setCurrentIndex(mode_index)
        self.palette.setValue(config.palette_budget)
        self.repeat_opt.setCurrentIndex(0 if config.repeat_opt_enabled else 1)


__all__ = ["BatchImagePreview", "TerrainBatchWindow", "TerrainBatchWorker"]
