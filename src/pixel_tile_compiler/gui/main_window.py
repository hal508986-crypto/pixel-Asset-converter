"""Character-first PySide6 workbench for the pixel compiler."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QComboBox,
    QFormLayout,
    QFrame,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pixel_tile_compiler.config import CanvasSpec, compiler_config_for_purpose
from pixel_tile_compiler.gui.canvas import CanvasState, ZOOMS
from pixel_tile_compiler.gui.input import first_supported_image_path
from pixel_tile_compiler.gui.policy import (
    GUI_ANIMATION_SPLIT_OPTIONS,
    GUI_TERRAIN_PIXELIZATION_OPTIONS,
    build_output_path,
    resolve_character_animation_gui_profile,
    resolve_character_gui_profile,
    resolve_terrain_gui_profile,
)
from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler
from pixel_tile_compiler.pixelizer.character_animation import (
    CharacterAnimationConfig,
    compile_character_animation_sheet,
)


CHECKER_LIGHT = QColor("#d6d9dd")
CHECKER_DARK = QColor("#b9bec5")


def _draw_checkerboard(painter: QPainter, rect, tile_size: int = 8) -> None:  # type: ignore[no-untyped-def]
    """Paint the conventional transparent-image checkerboard."""
    left = int(rect.left()) - int(rect.left()) % tile_size
    top = int(rect.top()) - int(rect.top()) % tile_size
    right = int(rect.right()) + tile_size
    bottom = int(rect.bottom()) + tile_size
    painter.setPen(Qt.PenStyle.NoPen)
    for y in range(top, bottom, tile_size):
        for x in range(left, right, tile_size):
            color = CHECKER_LIGHT if ((x // tile_size + y // tile_size) % 2 == 0) else CHECKER_DARK
            painter.fillRect(x, y, tile_size, tile_size, color)


class ImagePreview(QLabel):
    """Fit an image into a panel while keeping alpha visible over a checkerboard."""

    def __init__(self, empty_text: str) -> None:
        super().__init__()
        self._path: Path | None = None
        self._empty_text = empty_text
        self.setMinimumSize(220, 170)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Plain)
        self.setToolTip("透明部分は市松模様で表示します")

    def set_image(self, path: Path | None) -> None:
        self._path = Path(path) if path is not None else None
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        del event
        painter = QPainter(self)
        _draw_checkerboard(painter, self.rect())
        if self._path is None or not self._path.exists():
            painter.setPen(QColor("#434a54"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._empty_text)
            return
        image = QImage(str(self._path)).convertToFormat(QImage.Format.Format_RGBA8888)
        available = QSize(max(1, self.width() - 20), max(1, self.height() - 20))
        scaled = image.scaled(
            available,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        left = (self.width() - scaled.width()) // 2
        top = (self.height() - scaled.height()) // 2
        painter.drawImage(left, top, scaled)


class SourceImagePreview(ImagePreview):
    """Image preview that accepts one supported local image by drag-and-drop."""

    image_dropped = Signal(object)

    def __init__(self, empty_text: str) -> None:
        super().__init__(empty_text)
        self.setAcceptDrops(True)
        self.setToolTip("元絵をここへドロップできます。透明部分は市松模様で表示します")

    @staticmethod
    def _path_from_event(event) -> Path | None:  # type: ignore[no-untyped-def]
        if not event.mimeData().hasUrls():
            return None
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        return first_supported_image_path(paths)

    def dragEnterEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self._path_from_event(event) is None:
            event.ignore()
            return
        event.acceptProposedAction()

    def dropEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        path = self._path_from_event(event)
        if path is None:
            event.ignore()
            return
        self.image_dropped.emit(path)
        event.acceptProposedAction()


class PixelCanvas(QGraphicsView):
    """Nearest-neighbor canvas with a dynamic grid and alpha checkerboard."""

    def __init__(self) -> None:
        super().__init__()
        self.state = CanvasState(zoom=4, canvas_size=(128, 128))
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        self.setMinimumSize(560, 560)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self._item: QGraphicsPixmapItem | None = None
        self._source_image: QImage | None = None
        self._refresh_scene()

    def set_canvas_size(self, canvas_size: tuple[int, int]) -> None:
        self.state.set_canvas_size(canvas_size)
        self._refresh_scene()

    def set_zoom(self, zoom: int) -> None:
        if zoom not in ZOOMS:
            raise ValueError("zoom must be one of 1, 2, 4, 8, 16, 32")
        self.state.zoom = zoom
        self._refresh_scene()

    def zoom_in(self) -> int:
        zoom = self.state.zoom_in()
        self._refresh_scene()
        return zoom

    def zoom_out(self) -> int:
        zoom = self.state.zoom_out()
        self._refresh_scene()
        return zoom

    def set_image(self, path: Path, canvas_size: tuple[int, int] | None = None) -> None:
        if canvas_size is not None:
            self.state.set_canvas_size(canvas_size)
        self._source_image = QImage(str(path)).convertToFormat(QImage.Format.Format_RGBA8888)
        self._refresh_scene()

    def clear_image(self) -> None:
        self._source_image = None
        self._refresh_scene()

    def wheelEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            if event.angleDelta().y() > 0:
                self.zoom_in()
            else:
                self.zoom_out()
            event.accept()
            return
        super().wheelEvent(event)

    def drawBackground(self, painter: QPainter, rect) -> None:  # type: ignore[no-untyped-def]
        _draw_checkerboard(painter, rect)

    def drawForeground(self, painter: QPainter, rect) -> None:  # type: ignore[no-untyped-def]
        del rect
        if self.state.zoom < 4:
            return
        width, height = self.state.canvas_size
        pixel_size = self.state.zoom
        painter.setPen(QColor(35, 40, 48, 80))
        for column in range(width + 1):
            x = column * pixel_size
            painter.drawLine(x, 0, x, height * pixel_size)
        for row in range(height + 1):
            y = row * pixel_size
            painter.drawLine(0, y, width * pixel_size, y)

    def _refresh_scene(self) -> None:
        self.scene().clear()
        width, height = self.state.canvas_size
        scene_size = QSize(width * self.state.zoom, height * self.state.zoom)
        if self._source_image is not None and not self._source_image.isNull():
            scaled = self._source_image.scaled(
                scene_size,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            self._item = self.scene().addPixmap(QPixmap.fromImage(scaled))
        else:
            self._item = None
        self.scene().setSceneRect(0, 0, scene_size.width(), scene_size.height())
        self.viewport().update()


class MainWindow(QMainWindow):
    """Character-first GUI; compiler behavior remains in the core pipeline."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Pixel Tile Compiler")
        self.resize(1440, 860)
        self.source_path: Path | None = None
        self._compiled_canvas_size: tuple[int, int] | None = None
        self._terrain_batch_window = None
        self.default_output_root = Path.cwd() / "output"
        self.source_preview = SourceImagePreview("元絵を読み込んでください\nまたはここにドロップ")
        self.source_preview.image_dropped.connect(self.set_source_path)
        self.result_preview = ImagePreview("コンパイル結果")
        self.tile_preview = ImagePreview("タイルプレビュー")
        self.canvas = PixelCanvas()
        self.status = QLabel("元絵を読み込むと始められます")
        self.metrics = QLabel("出力情報はここに表示されます")
        self.output_root_field = QLineEdit(str(self.default_output_root))
        self.output_root_field.setToolTip("コンパイル結果を保存するフォルダ")
        self.output_root_field.setCursorPosition(0)
        self.output_root_field.textChanged.connect(self._on_output_root_changed)
        self.output_browse_button = QPushButton("参照...")
        self.output_browse_button.clicked.connect(self.choose_output_directory)
        self.purpose = QComboBox()
        self.purpose.addItem("キャラクター", userData="character")
        self.purpose.addItem("キャラクター待機アニメーション", userData="character_animation")
        self.purpose.addItem("地形（64×64）", userData="terrain")
        self.purpose.currentIndexChanged.connect(self._update_purpose_controls)
        self.canvas_size = QComboBox()
        self.canvas_size.addItem("128 × 128（推奨）", userData=(128, 128))
        self.canvas_size.addItem("64 × 64", userData=(64, 64))
        self.canvas_size.currentIndexChanged.connect(self._update_canvas_selection)
        self.animation_split_mode = QComboBox()
        for label, mode in GUI_ANIMATION_SPLIT_OPTIONS:
            self.animation_split_mode.addItem(label, userData=mode)
        self.animation_split_mode_label = QLabel("アニメーション分割方式")
        self.animation_columns = QSpinBox()
        self.animation_columns.setRange(1, 64)
        self.animation_columns.setValue(4)
        self.animation_columns_label = QLabel("分割列数")
        self.animation_rows = QSpinBox()
        self.animation_rows.setRange(1, 64)
        self.animation_rows.setValue(1)
        self.animation_rows_label = QLabel("分割行数")
        self.animation_split_mode.currentIndexChanged.connect(self._update_purpose_controls)
        self.animation_columns.valueChanged.connect(self._update_purpose_controls)
        self.animation_rows.valueChanged.connect(self._update_purpose_controls)
        self.auto_profile = QLabel()
        self.palette = QSpinBox()
        self.palette.setRange(4, 64)
        self.palette.setValue(24)
        self.palette_label = QLabel("地形palette")
        self.pixelization_mode = QComboBox()
        for label, mode in GUI_TERRAIN_PIXELIZATION_OPTIONS:
            self.pixelization_mode.addItem(label, userData=mode)
        self.pixelization_mode_label = QLabel("地形の変換方法")
        self.repeat_opt = QComboBox()
        self.repeat_opt.addItem("有効", userData=True)
        self.repeat_opt.addItem("無効", userData=False)
        self.repeat_opt.setCurrentIndex(1)
        self.repeat_opt_label = QLabel("繰り返し最適化")
        self.secondary_preview_label = QLabel("繰り返し確認")
        self.pixelization_mode.currentIndexChanged.connect(self._update_purpose_controls)
        self.palette.valueChanged.connect(self._on_terrain_setting_changed)
        self.repeat_opt.currentIndexChanged.connect(self._on_terrain_setting_changed)
        self.zoom = QComboBox()
        for value in ZOOMS:
            self.zoom.addItem(f"{value}×", userData=value)
        self.zoom.setCurrentIndex(ZOOMS.index(4))
        self.zoom.currentIndexChanged.connect(self._change_zoom)
        self._build_ui()
        self._update_purpose_controls()
        self._apply_ui_font()

    def _apply_ui_font(self) -> None:
        """Prefer a Windows Japanese UI font so labels never fall back to tofu boxes."""
        for family in ("Yu Gothic UI", "Meiryo UI", "Noto Sans JP", "MS UI Gothic"):
            if QFontDatabase.hasFamily(family):
                font = QFont(family)
                font.setPointSize(10)
                self.setFont(font)
                return

    def _build_ui(self) -> None:
        open_button = QPushButton("元絵を読み込む")
        open_button.setObjectName("primaryButton")
        open_button.clicked.connect(self.open_image)
        self.compile_button = QPushButton("コンパイルする")
        self.compile_button.setObjectName("primaryButton")
        self.compile_button.clicked.connect(self.compile_image)
        self.compile_button.setEnabled(False)
        self.terrain_batch_button = QPushButton("地形をまとめて変換")
        self.terrain_batch_button.clicked.connect(self.open_terrain_batch)

        source_group = QGroupBox("1. 元絵")
        source_layout = QVBoxLayout(source_group)
        source_layout.addWidget(open_button)
        source_layout.addWidget(self.source_preview)

        settings_group = QGroupBox("2. 出力設定")
        settings_form = QFormLayout(settings_group)
        settings_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        settings_form.addRow("用途", self.purpose)
        settings_form.addRow("論理ピクセル", self.canvas_size)
        settings_form.addRow(self.animation_split_mode_label, self.animation_split_mode)
        settings_form.addRow(self.animation_columns_label, self.animation_columns)
        settings_form.addRow(self.animation_rows_label, self.animation_rows)
        settings_form.addRow("自動最適化", self.auto_profile)
        settings_form.addRow(self.pixelization_mode_label, self.pixelization_mode)
        settings_form.addRow(self.palette_label, self.palette)
        settings_form.addRow(self.repeat_opt_label, self.repeat_opt)
        output_row = QWidget()
        output_row_layout = QHBoxLayout(output_row)
        output_row_layout.setContentsMargins(0, 0, 0, 0)
        output_row_layout.setSpacing(6)
        output_row_layout.addWidget(self.output_root_field, 1)
        output_row_layout.addWidget(self.output_browse_button)
        settings_form.addRow("保存先", output_row)
        settings_form.addRow(self.compile_button)
        settings_form.addRow(self.terrain_batch_button)

        controls = QVBoxLayout()
        controls.addWidget(source_group)
        controls.addWidget(settings_group)
        controls.addStretch(1)
        controls_panel = QFrame()
        controls_panel.setObjectName("controlsPanel")
        controls_panel.setLayout(controls)
        controls_panel.setMinimumWidth(280)
        controls_panel.setMaximumWidth(340)

        canvas_title = QLabel("ドットプレビュー")
        canvas_title.setObjectName("sectionTitle")
        zoom_row = QHBoxLayout()
        zoom_row.addWidget(QLabel("プレビュー倍率"))
        zoom_row.addWidget(self.zoom)
        zoom_row.addStretch(1)
        zoom_hint = QLabel("Ctrl + ホイールでも変更できます")
        zoom_hint.setObjectName("mutedText")
        zoom_row.addWidget(zoom_hint)
        canvas_panel = QVBoxLayout()
        canvas_panel.addWidget(canvas_title)
        canvas_panel.addWidget(self.canvas, 1)
        canvas_panel.addLayout(zoom_row)
        canvas_widget = QWidget()
        canvas_widget.setLayout(canvas_panel)

        output_group = QGroupBox("3. 出力確認")
        output_layout = QVBoxLayout(output_group)
        output_layout.addWidget(QLabel("コンパイル結果"))
        output_layout.addWidget(self.result_preview)
        output_layout.addWidget(self.secondary_preview_label)
        output_layout.addWidget(self.tile_preview)
        output_layout.addStretch(1)
        output_panel = QFrame()
        output_panel.setObjectName("outputPanel")
        output_panel.setLayout(QVBoxLayout())
        output_panel.layout().addWidget(output_group)  # type: ignore[union-attr]
        output_panel.setMinimumWidth(270)
        output_panel.setMaximumWidth(340)

        body = QHBoxLayout()
        body.setSpacing(14)
        body.addWidget(controls_panel)
        body.addWidget(canvas_widget, 1)
        body.addWidget(output_panel)

        footer = QVBoxLayout()
        footer.addWidget(self.metrics)
        footer.addWidget(self.status)

        root = QVBoxLayout()
        root.setContentsMargins(18, 16, 18, 14)
        root.addLayout(body, 1)
        root.addLayout(footer)
        container = QWidget()
        container.setLayout(root)
        self.setCentralWidget(container)
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #1d2127; color: #eef1f5; }
            QGroupBox {
                border: 1px solid #3a424d;
                border-radius: 8px;
                margin-top: 12px;
                padding: 14px 10px 10px 10px;
                background: #252a31;
                font-size: 14px;
                font-weight: 600;
            }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; color: #f0c674; }
            QLabel { font-size: 14px; }
            QLabel#sectionTitle { font-size: 18px; font-weight: 700; color: #f0c674; }
            QLabel#mutedText { color: #9da6b2; font-size: 12px; }
            QComboBox, QSpinBox, QLineEdit {
                min-height: 32px;
                border: 1px solid #48515d;
                border-radius: 5px;
                padding: 2px 8px;
                background: #303640;
                color: #f4f6f8;
            }
            QComboBox:focus, QSpinBox:focus { border: 1px solid #77a9ff; }
            QPushButton {
                min-height: 36px;
                border: 1px solid #556171;
                border-radius: 6px;
                padding: 0 14px;
                background: #343c48;
                color: #f4f6f8;
                font-weight: 600;
            }
            QPushButton:hover { background: #414b59; }
            QPushButton:pressed { background: #2b323c; }
            QPushButton:disabled { color: #78818d; background: #2a2f36; }
            QPushButton#primaryButton { background: #3565a8; border-color: #5d8ed5; }
            QPushButton#primaryButton:hover { background: #4379c1; }
            QGraphicsView, QLabel { outline: none; }
            """
        )

    def _update_purpose_controls(self) -> None:
        purpose = self.purpose.currentData()
        is_character = purpose in {"character", "character_animation"}
        is_animation = purpose == "character_animation"
        self.canvas_size.setEnabled(is_character)
        self.pixelization_mode.setEnabled(not is_character)
        self.palette.setEnabled(not is_character)
        self.repeat_opt.setEnabled(not is_character)
        self.pixelization_mode_label.setVisible(not is_character)
        self.pixelization_mode.setVisible(not is_character)
        self.palette_label.setVisible(not is_character)
        self.palette.setVisible(not is_character)
        self.repeat_opt_label.setVisible(not is_character)
        self.repeat_opt.setVisible(not is_character)
        self.terrain_batch_button.setVisible(not is_character)
        self.secondary_preview_label.setText("アニメーションシート（8倍）" if is_animation else "繰り返し確認")
        self._update_animation_split_controls()
        if is_character:
            self.repeat_opt.setCurrentIndex(1)
            self._update_canvas_selection()
        else:
            profile = resolve_terrain_gui_profile(self.pixelization_mode.currentData())
            self.auto_profile.setText(f"地形は64×64 / {profile.label} / {self.palette.value()}色")
            self.canvas.set_canvas_size((64, 64))
        self._clear_stale_result()

    def _on_terrain_setting_changed(self, _value: object = None) -> None:
        if self.purpose.currentData() != "terrain":
            return
        self._update_purpose_controls()

    def _update_canvas_selection(self) -> None:
        purpose = self.purpose.currentData()
        if purpose not in {"character", "character_animation"}:
            return
        canvas_size = self.canvas_size.currentData()
        if purpose == "character_animation":
            columns = self.animation_columns.value()
            rows = self.animation_rows.value()
            profile = resolve_character_animation_gui_profile(canvas_size, frame_count=columns * rows)
            self.canvas.set_canvas_size(profile.canvas_size)
            split_mode = self.animation_split_mode.currentData()
            if split_mode == "fixed_grid":
                split_summary = f"{columns}列×{rows}行"
            elif split_mode == "hybrid":
                split_summary = f"自動推定（失敗時{columns}列×{rows}行）"
            else:
                split_summary = "列・行を自動推定"
            self.auto_profile.setText(
                f"B24 / {profile.canvas_size[0]}×{profile.canvas_size[1]} / {split_summary} / 共通bbox・足元固定"
            )
            self._clear_stale_result(profile.canvas_size)
            return
        profile = resolve_character_gui_profile(canvas_size)
        self.canvas.set_canvas_size(profile.canvas_size)
        self.auto_profile.setText(
            f"B24 / {profile.canvas_size[0]}×{profile.canvas_size[1]} / バランス / 元絵から直接"
        )
        self._clear_stale_result(profile.canvas_size)

    def _update_animation_split_controls(self) -> None:
        is_animation = self.purpose.currentData() == "character_animation"
        split_mode = self.animation_split_mode.currentData()
        show_grid = is_animation and split_mode in {"fixed_grid", "hybrid"}
        for widget in (self.animation_split_mode_label, self.animation_split_mode):
            widget.setVisible(is_animation)
        for widget in (
            self.animation_columns_label,
            self.animation_columns,
            self.animation_rows_label,
            self.animation_rows,
        ):
            widget.setVisible(show_grid)
        self.animation_split_mode.setEnabled(is_animation)
        self.animation_columns.setEnabled(show_grid)
        self.animation_rows.setEnabled(show_grid)

    def _clear_stale_result(self, selected_size: tuple[int, int] | None = None) -> None:
        if self._compiled_canvas_size is None:
            return
        if selected_size is not None and self._compiled_canvas_size == selected_size:
            return
        self._compiled_canvas_size = None
        self.canvas.clear_image()
        self.result_preview.set_image(None)
        self.tile_preview.set_image(None)
        self.metrics.setText("出力設定を変更しました")
        self.status.setText("設定を変更しました。再コンパイルしてください")

    def _change_zoom(self) -> None:
        self.canvas.set_zoom(int(self.zoom.currentData()))

    def _selected_output_root(self) -> Path:
        raw_path = self.output_root_field.text().strip()
        if not raw_path:
            raise ValueError("保存先を指定してください")
        return Path(raw_path).expanduser()

    def _on_output_root_changed(self, _text: str) -> None:
        if self._compiled_canvas_size is None:
            return
        self._clear_stale_result()
        self.status.setText("保存先を変更しました。再コンパイルしてください")

    def choose_output_directory(self) -> None:
        current = self.output_root_field.text().strip() or str(self.default_output_root)
        path = QFileDialog.getExistingDirectory(self, "保存先を選択", current)
        if not path:
            return
        self.output_root_field.setText(path)
        self.output_root_field.setCursorPosition(0)
        self._clear_stale_result()
        self.status.setText("保存先を変更しました。再コンパイルしてください")

    def set_source_path(self, path: Path | str) -> bool:
        source = first_supported_image_path([path])
        if source is None:
            self.status.setText("対応している画像ファイルを指定してください（PNG/JPEG/WebP）")
            return False
        self.source_path = source
        self.source_preview.set_image(source)
        self._clear_stale_result()
        self.compile_button.setEnabled(True)
        self.status.setText(f"元絵を読み込みました: {source.name}")
        return True

    def open_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "元絵を読み込む",
            "",
            "画像 (*.png *.jpg *.jpeg *.webp)",
        )
        if not path:
            return
        self.set_source_path(path)

    def compile_image(self) -> None:
        if self.source_path is None:
            self.status.setText("先に元絵を読み込んでください")
            return
        try:
            output_root = self._selected_output_root()
            purpose = self.purpose.currentData()
            if purpose == "character_animation":
                columns = self.animation_columns.value()
                rows = self.animation_rows.value()
                profile = resolve_character_animation_gui_profile(
                    self.canvas_size.currentData(),
                    frame_count=columns * rows,
                )
                width, height = profile.canvas_size
                output = build_output_path(
                    output_root,
                    self.source_path,
                    purpose="character_animation",
                    canvas_size=profile.canvas_size,
                )
                animation = compile_character_animation_sheet(
                    self.source_path,
                    output,
                    config=CharacterAnimationConfig(
                        frame_count=profile.frame_count,
                        split_mode=self.animation_split_mode.currentData(),  # type: ignore[arg-type]
                        grid_columns=columns,
                        grid_rows=rows,
                        canvas_size=profile.canvas_size,
                        fit_within=profile.fit_within,
                        bottom_margin=profile.bottom_margin,
                    ),
                    palette_budget=profile.palette_budget,
                    character_detail_level=profile.detail_level,
                    debug_enabled=True,
                )
                first_frame = animation.frame_paths[0]
                self.canvas.set_image(first_frame, profile.canvas_size)
                self._compiled_canvas_size = profile.canvas_size
                self.result_preview.set_image(first_frame)
                self.tile_preview.set_image(animation.preview_8x_path)
                self.metrics.setText(
                    " / ".join(
                        [
                            f"出力 {width}×{height}",
                            f"{len(animation.frame_paths)}フレーム（{width * len(animation.frame_paths)}×{height} Sheet）",
                            f"フレーム集約 {animation.final_frame_paths[0].parent}",
                            f"保存先 {output}",
                        ]
                    )
                )
                self.status.setText("完了: 分割・共通bbox・足元アンカーで待機アニメーションを出力しました")
                return
            if purpose == "character":
                profile = resolve_character_gui_profile(self.canvas_size.currentData())
                width, height = profile.canvas_size
                output = build_output_path(
                    output_root,
                    self.source_path,
                    purpose="character",
                    canvas_size=(width, height),
                )
                config = compiler_config_for_purpose(
                    "character",
                    output_root=output,
                    canvas=CanvasSpec(width, height),
                    palette_budget=profile.palette_budget,
                    character_detail_level=profile.detail_level,  # type: ignore[arg-type]
                    debug_enabled=True,
                )
            else:
                width, height = 64, 64
                terrain_profile = resolve_terrain_gui_profile(self.pixelization_mode.currentData())
                palette_budget = self.palette.value()
                repeat_opt_enabled = bool(self.repeat_opt.currentData())
                output = build_output_path(
                    output_root,
                    self.source_path,
                    purpose="terrain",
                    canvas_size=(width, height),
                    pixelization_mode=terrain_profile.pixelization_mode,
                    palette_budget=palette_budget,
                    repeat_opt_enabled=repeat_opt_enabled,
                )
                config = compiler_config_for_purpose(
                    "terrain",
                    output_root=output,
                    canvas=CanvasSpec(width, height),
                    palette_budget=palette_budget,
                    pixelization_mode=terrain_profile.pixelization_mode,
                    repeat_opt_enabled=repeat_opt_enabled,
                    debug_enabled=True,
                )
            result = PixelTileCompiler().compile(self.source_path, config)
        except (OSError, ValueError) as exc:
            self.status.setText(f"コンパイルできませんでした: {exc}")
            return

        self.canvas.set_image(result.final_path, (width, height))
        self._compiled_canvas_size = (width, height)
        self.result_preview.set_image(result.final_path)
        tile = output / "debug" / "09_tile_preview.png"
        if not tile.exists():
            tile = output / "debug" / "08_tile_preview.png"
        self.tile_preview.set_image(tile if tile.exists() else None)
        self.metrics.setText(
            " / ".join(
                [
                    f"出力 {width}×{height}",
                    f"可視RGB {result.metrics.actual_palette_count}色",
                    f"保存先 {output}",
                ]
            )
        )
        if purpose == "character":
            self.status.setText("完了: B24をCanvasに合わせて自動適用しました")
        else:
            self.status.setText("完了: 64×64地形タイルを出力しました")

    def open_terrain_batch(self) -> None:
        """Open the terrain-only batch window without changing single-image behavior."""
        try:
            output_root = self._selected_output_root()
        except ValueError as exc:
            self.status.setText(f"一括変換を開けませんでした: {exc}")
            return
        if self._terrain_batch_window is None:
            from pixel_tile_compiler.gui.terrain_batch_window import TerrainBatchWindow

            self._terrain_batch_window = TerrainBatchWindow(output_root=output_root)
        self._terrain_batch_window.show()
        self._terrain_batch_window.raise_()
        self._terrain_batch_window.activateWindow()
