"""Character-first PySide6 workbench for the pixel compiler."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, cast

from PySide6.QtCore import QThread, QTimer, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
from PIL import Image

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
from pixel_tile_compiler.palette_contract import load_palette_json, palette_id, validate_reference_palette
from pixel_tile_compiler.pipeline.compiler import CompilationResult, PixelTileCompiler
from pixel_tile_compiler.pixelizer.character_animation import (
    CharacterAnimationConfig,
    CharacterAnimationCompileResult,
    animation_source_frame_boxes,
    compile_character_animation_sheet,
    map_source_point_to_frame,
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
        self._guide_point: tuple[float, float] | None = None

    def set_image(self, path: Path | None) -> None:
        self._path = Path(path) if path is not None else None
        self.update()

    def set_guide_point(self, point: tuple[float, float] | None) -> None:
        self._guide_point = point
        self.update()

    def _display_geometry(self) -> tuple[QImage, tuple[int, int, int, int]] | None:
        if self._path is None or not self._path.exists():
            return None
        image = QImage(str(self._path)).convertToFormat(QImage.Format.Format_RGBA8888)
        if image.isNull():
            return None
        available = QSize(max(1, self.width() - 20), max(1, self.height() - 20))
        scaled_size = image.size().scaled(available, Qt.AspectRatioMode.KeepAspectRatio)
        left = (self.width() - scaled_size.width()) // 2
        top = (self.height() - scaled_size.height()) // 2
        return image, (left, top, scaled_size.width(), scaled_size.height())

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        del event
        painter = QPainter(self)
        _draw_checkerboard(painter, self.rect())
        geometry = self._display_geometry()
        if geometry is None:
            painter.setPen(QColor("#434a54"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self._empty_text)
            return
        image, (left, top, width, height) = geometry
        scaled = image.scaled(
            QSize(width, height),
            Qt.AspectRatioMode.IgnoreAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        painter.drawImage(left, top, scaled)
        if self._guide_point is not None:
            guide_x, guide_y = self._guide_point
            x = left + int((guide_x + 0.5) * width / max(1, image.width()))
            y = top + int((guide_y + 0.5) * height / max(1, image.height()))
            painter.setPen(QColor("#ffcc66"))
            painter.drawLine(x - 8, y, x + 8, y)
            painter.drawLine(x, y - 8, x, y + 8)


class SourceImagePreview(ImagePreview):
    """Image preview that accepts one supported local image by drag-and-drop."""

    image_dropped = Signal(object)
    image_point_clicked = Signal(object)

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

    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.button() == Qt.MouseButton.LeftButton:
            geometry = self._display_geometry()
            if geometry is not None:
                image, (left, top, width, height) = geometry
                position = event.position()
                if left <= position.x() < left + width and top <= position.y() < top + height:
                    x = min(image.width() - 1, max(0, int((position.x() - left) * image.width() / width)))
                    y = min(image.height() - 1, max(0, int((position.y() - top) * image.height() / height)))
                    self.image_point_clicked.emit((x, y))
                    event.accept()
                    return
        super().mousePressEvent(event)


class PixelCanvas(QGraphicsView):
    """Nearest-neighbor canvas with a dynamic grid and alpha checkerboard."""

    point_clicked = Signal(object)

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
        self._guide_point: tuple[float, float] | None = None
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

    def set_guide_point(self, point: tuple[float, float] | None) -> None:
        self._guide_point = point
        self.viewport().update()

    def mousePressEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.button() == Qt.MouseButton.LeftButton:
            scene_point = self.mapToScene(event.position().toPoint())
            width, height = self.state.canvas_size
            zoom = self.state.zoom
            x = min(width, max(0, int(scene_point.x() / zoom + 0.5)))
            y = min(height, max(0, int(scene_point.y() / zoom + 0.5)))
            self.point_clicked.emit((x, y))
            event.accept()
            return
        super().mousePressEvent(event)

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
        width, height = self.state.canvas_size
        pixel_size = self.state.zoom
        if self.state.zoom >= 4:
            painter.setPen(QColor(35, 40, 48, 80))
            for column in range(width + 1):
                x = column * pixel_size
                painter.drawLine(x, 0, x, height * pixel_size)
            for row in range(height + 1):
                y = row * pixel_size
                painter.drawLine(0, y, width * pixel_size, y)
        if self._guide_point is not None:
            guide_x, guide_y = self._guide_point
            x = int(guide_x * pixel_size)
            y = int(guide_y * pixel_size)
            painter.setPen(QColor("#ffcc66"))
            painter.drawLine(x - pixel_size * 2, y, x + pixel_size * 2, y)
            painter.drawLine(x, y - pixel_size * 2, x, y + pixel_size * 2)

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


class _CompileWorker(QThread):
    """Run one compiler operation away from the Qt GUI thread."""

    succeeded = Signal(object)
    failed = Signal(str)

    def __init__(self, operation: Callable[[], object]) -> None:
        super().__init__()
        self._operation = operation

    def run(self) -> None:
        try:
            self.succeeded.emit(self._operation())
        except Exception as exc:  # pragma: no cover - asserted through Qt signal tests
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    """Character-first GUI; compiler behavior remains in the core pipeline."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Pixel Tile Compiler")
        self.resize(1440, 860)
        self.source_path: Path | None = None
        self._compiled_canvas_size: tuple[int, int] | None = None
        self._terrain_batch_window = None
        self._shared_palette_colors: tuple[tuple[int, int, int], ...] = ()
        self._origin_pick_target: str | None = None
        self._compile_thread: _CompileWorker | None = None
        self._compile_context: dict[str, object] | None = None
        self._configuration_revision = 0
        self._animation_frame_paths: tuple[Path, ...] = ()
        self._animation_frame_index = 0
        self._source_origin_frame_box: tuple[int, int, int, int] | None = None
        self._source_origin_frame_signature: tuple[object, ...] | None = None
        self.default_output_root = Path.cwd() / "output"
        self.source_preview = SourceImagePreview("元絵を読み込んでください\nまたはここにドロップ")
        self.source_preview.image_dropped.connect(self.set_source_path)
        self.source_preview.image_point_clicked.connect(self._on_source_origin_clicked)
        self.result_preview = ImagePreview("コンパイル結果")
        self.tile_preview = ImagePreview("タイルプレビュー")
        self.canvas = PixelCanvas()
        self.canvas.point_clicked.connect(self._on_output_origin_clicked)
        self.status = QLabel("元絵を読み込むと始められます")
        self.metrics = QLabel("出力情報はここに表示されます")
        self.shared_palette_info = QLabel("未設定（地形タイルの基準paletteを反映できます）")
        self.shared_palette_info.setWordWrap(True)
        self.shared_palette_view = QListWidget()
        self.shared_palette_view.setFlow(QListWidget.Flow.LeftToRight)
        self.shared_palette_view.setMaximumHeight(66)
        self.shared_palette_load_button = QPushButton("palette.jsonを読み込む")
        self.shared_palette_load_button.clicked.connect(self.load_shared_palette)
        self.shared_palette_clear_button = QPushButton("共有を解除")
        self.shared_palette_clear_button.clicked.connect(self.clear_shared_palette)
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
        self.animation_placement_mode = QComboBox()
        self.animation_placement_mode.addItem("待機互換（足元固定）", userData="legacy_foot")
        self.animation_placement_mode.addItem("戦闘（移動を保持）", userData="preserve_motion")
        self.animation_placement_mode_label = QLabel("配置方式")
        self.animation_width = QSpinBox()
        self.animation_width.setRange(1, 4096)
        self.animation_width.setValue(256)
        self.animation_width_label = QLabel("戦闘Canvas幅")
        self.animation_height = QSpinBox()
        self.animation_height.setRange(1, 4096)
        self.animation_height.setValue(192)
        self.animation_height_label = QLabel("戦闘Canvas高さ")
        self.animation_source_origin_x = QSpinBox()
        self.animation_source_origin_x.setRange(-4096, 4096)
        self.animation_source_origin_x.setValue(0)
        self.animation_source_origin_y = QSpinBox()
        self.animation_source_origin_y.setRange(-4096, 4096)
        self.animation_source_origin_y.setValue(0)
        self.animation_source_origin_x.setFixedWidth(88)
        self.animation_source_origin_y.setFixedWidth(88)
        self.animation_source_origin_y_label = QLabel("ソース原点Y")
        self.animation_source_origin_label = QLabel("ソース原点（x,y）")
        self.animation_source_origin_set = QCheckBox("指定")
        self.animation_source_origin_pick_button = QPushButton("画像から指定")
        self.animation_source_origin_pick_button.clicked.connect(self.start_source_origin_pick)
        self.animation_output_origin_x = QSpinBox()
        self.animation_output_origin_x.setRange(-4096, 4096)
        self.animation_output_origin_x.setValue(0)
        self.animation_output_origin_y = QSpinBox()
        self.animation_output_origin_y.setRange(-4096, 4096)
        self.animation_output_origin_y.setValue(0)
        self.animation_output_origin_x.setFixedWidth(88)
        self.animation_output_origin_y.setFixedWidth(88)
        self.animation_output_origin_y_label = QLabel("出力原点Y")
        self.animation_output_origin_label = QLabel("出力原点（x,y）")
        self.animation_output_origin_set = QCheckBox("指定")
        self.animation_output_origin_pick_button = QPushButton("Canvasから指定")
        self.animation_output_origin_pick_button.clicked.connect(self.start_output_origin_pick)
        self.animation_scale_mode = QComboBox()
        self.animation_scale_mode.addItem("自動fit", userData="auto")
        self.animation_scale_mode.addItem("固定倍率", userData="fixed")
        self.animation_scale_mode_label = QLabel("倍率調整")
        self.animation_scale = QDoubleSpinBox()
        self.animation_scale.setRange(0.01, 100.0)
        self.animation_scale.setSingleStep(0.05)
        self.animation_scale.setDecimals(3)
        self.animation_scale.setValue(1.0)
        self.animation_scale_label = QLabel("戦闘倍率")
        self.animation_shared_palette = QComboBox()
        self.animation_shared_palette.addItem("有効", userData=True)
        self.animation_shared_palette.addItem("無効", userData=False)
        self.animation_shared_palette_label = QLabel("共有palette")
        self.animation_palette = QSpinBox()
        self.animation_palette.setRange(4, 64)
        self.animation_palette.setValue(24)
        self.animation_palette_label = QLabel("アニメーションpalette上限")
        self.auto_profile = QLabel()
        self.auto_profile.setWordWrap(True)
        self.animation_source_origin_set.toggled.connect(self._update_purpose_controls)
        self.animation_output_origin_set.toggled.connect(self._update_purpose_controls)
        self.animation_split_mode.currentIndexChanged.connect(self._update_purpose_controls)
        self.animation_columns.valueChanged.connect(self._update_purpose_controls)
        self.animation_rows.valueChanged.connect(self._update_purpose_controls)
        self.animation_placement_mode.currentIndexChanged.connect(self._update_purpose_controls)
        for widget in (
            self.animation_width,
            self.animation_height,
            self.animation_source_origin_x,
            self.animation_source_origin_y,
            self.animation_output_origin_x,
            self.animation_output_origin_y,
            self.animation_scale,
        ):
            widget.valueChanged.connect(self._update_purpose_controls)
        self.animation_scale_mode.currentIndexChanged.connect(self._update_purpose_controls)
        self.animation_shared_palette.currentIndexChanged.connect(self._update_purpose_controls)
        self.animation_palette.valueChanged.connect(self._update_purpose_controls)
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
        self.animation_play_button = QPushButton("▶ 再生")
        self.animation_play_button.setEnabled(False)
        self.animation_play_button.clicked.connect(self._toggle_animation_playback)
        self.animation_playback_label = QLabel("未再生")
        self.animation_play_timer = QTimer(self)
        self.animation_play_timer.setInterval(160)
        self.animation_play_timer.timeout.connect(self._advance_animation_frame)
        self.compile_progress = QProgressBar()
        self.compile_progress.setRange(0, 1)
        self.compile_progress.setValue(0)
        self.compile_progress.setFormat("待機中")
        self.compile_progress.setTextVisible(True)
        self.pixelization_mode.currentIndexChanged.connect(self._update_purpose_controls)
        self.palette.valueChanged.connect(self._on_terrain_setting_changed)
        self.repeat_opt.currentIndexChanged.connect(self._on_terrain_setting_changed)
        self._connect_configuration_revision_signals()
        self.zoom = QComboBox()
        for value in ZOOMS:
            self.zoom.addItem(f"{value}×", userData=value)
        self.zoom.setCurrentIndex(ZOOMS.index(4))
        self.zoom.currentIndexChanged.connect(self._change_zoom)
        self._build_ui()
        self._update_purpose_controls()
        self._apply_ui_font()

    @property
    def shared_palette_colors(self) -> tuple[tuple[int, int, int], ...]:
        """地形workflowから共有された正確なRGB paletteを返す。"""
        return self._shared_palette_colors

    def _connect_configuration_revision_signals(self) -> None:
        """コンパイル結果がどの設定世代のものか追跡する。"""
        for widget in (
            self.purpose,
            self.canvas_size,
            self.animation_split_mode,
            self.animation_placement_mode,
            self.animation_scale_mode,
            self.animation_shared_palette,
            self.pixelization_mode,
            self.repeat_opt,
        ):
            widget.currentIndexChanged.connect(self._mark_configuration_changed)
        for widget in (
            self.animation_columns,
            self.animation_rows,
            self.animation_width,
            self.animation_height,
            self.animation_source_origin_x,
            self.animation_source_origin_y,
            self.animation_output_origin_x,
            self.animation_output_origin_y,
            self.animation_scale,
            self.animation_palette,
            self.palette,
        ):
            widget.valueChanged.connect(self._mark_configuration_changed)
        for widget in (
            self.animation_source_origin_set,
            self.animation_output_origin_set,
        ):
            widget.toggled.connect(self._mark_configuration_changed)

    def _mark_configuration_changed(self, *_args: object) -> None:
        """設定変更の世代を進め、実行中の古い結果を識別できるようにする。"""
        self._configuration_revision += 1

    def _apply_ui_font(self) -> None:
        """Prefer a Windows Japanese UI font so labels never fall back to tofu boxes."""
        for family in ("Yu Gothic UI", "Meiryo UI", "Noto Sans JP", "MS UI Gothic"):
            if QFontDatabase.hasFamily(family):
                font = QFont(family)
                font.setPointSize(10)
                self.setFont(font)
                return

    def _build_ui(self) -> None:
        self.open_button = QPushButton("元絵を読み込む")
        self.open_button.setObjectName("primaryButton")
        self.open_button.clicked.connect(self.open_image)
        self.compile_button = QPushButton("コンパイルする")
        self.compile_button.setObjectName("primaryButton")
        self.compile_button.clicked.connect(self.compile_image)
        self.compile_button.setEnabled(False)
        self.terrain_batch_button = QPushButton("地形をまとめて変換")
        self.terrain_batch_button.clicked.connect(self.open_terrain_batch)

        source_group = QGroupBox("1. 元絵")
        source_layout = QVBoxLayout(source_group)
        source_layout.addWidget(self.open_button)
        source_layout.addWidget(self.source_preview)

        settings_group = QGroupBox("2. 出力設定")
        settings_form = QFormLayout(settings_group)
        settings_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        settings_form.addRow("用途", self.purpose)
        settings_form.addRow("論理ピクセル", self.canvas_size)
        settings_form.addRow(self.animation_split_mode_label, self.animation_split_mode)
        settings_form.addRow(self.animation_columns_label, self.animation_columns)
        settings_form.addRow(self.animation_rows_label, self.animation_rows)
        settings_form.addRow(self.animation_placement_mode_label, self.animation_placement_mode)
        settings_form.addRow(self.animation_width_label, self.animation_width)
        settings_form.addRow(self.animation_height_label, self.animation_height)
        self.animation_source_origin_row = QWidget()
        source_origin_layout = QVBoxLayout(self.animation_source_origin_row)
        source_origin_layout.setContentsMargins(0, 0, 0, 0)
        source_origin_values = QHBoxLayout()
        source_origin_values.addWidget(self.animation_source_origin_set)
        source_origin_values.addWidget(self.animation_source_origin_x)
        source_origin_values.addWidget(QLabel(","))
        source_origin_values.addWidget(self.animation_source_origin_y)
        source_origin_layout.addLayout(source_origin_values)
        source_origin_layout.addWidget(self.animation_source_origin_pick_button)
        settings_form.addRow(self.animation_source_origin_label, self.animation_source_origin_row)
        self.animation_output_origin_row = QWidget()
        output_origin_layout = QVBoxLayout(self.animation_output_origin_row)
        output_origin_layout.setContentsMargins(0, 0, 0, 0)
        output_origin_values = QHBoxLayout()
        output_origin_values.addWidget(self.animation_output_origin_set)
        output_origin_values.addWidget(self.animation_output_origin_x)
        output_origin_values.addWidget(QLabel(","))
        output_origin_values.addWidget(self.animation_output_origin_y)
        output_origin_layout.addLayout(output_origin_values)
        output_origin_layout.addWidget(self.animation_output_origin_pick_button)
        settings_form.addRow(self.animation_output_origin_label, self.animation_output_origin_row)
        settings_form.addRow(self.animation_scale_mode_label, self.animation_scale_mode)
        settings_form.addRow(self.animation_scale_label, self.animation_scale)
        settings_form.addRow(self.animation_palette_label, self.animation_palette)
        settings_form.addRow(self.animation_shared_palette_label, self.animation_shared_palette)
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

        shared_palette_group = QGroupBox("共有palette")
        shared_palette_layout = QVBoxLayout(shared_palette_group)
        shared_palette_layout.addWidget(self.shared_palette_info)
        shared_palette_layout.addWidget(self.shared_palette_view)
        shared_palette_buttons = QVBoxLayout()
        shared_palette_buttons.addWidget(self.shared_palette_load_button)
        shared_palette_buttons.addWidget(self.shared_palette_clear_button)
        shared_palette_layout.addLayout(shared_palette_buttons)
        shared_palette_description = QLabel(
            "地形のfinal.pngから実測したRGBだけを使います。未設定なら従来の自動paletteです。"
        )
        shared_palette_description.setWordWrap(True)
        shared_palette_layout.addWidget(shared_palette_description)
        self.shared_palette_group = shared_palette_group

        controls_content = QWidget()
        controls = QVBoxLayout(controls_content)
        controls.addWidget(source_group)
        controls.addWidget(settings_group)
        controls.addWidget(shared_palette_group)
        controls.addStretch(1)
        controls_scroll = QScrollArea()
        controls_scroll.setObjectName("animationControlsScroll")
        controls_scroll.setWidgetResizable(True)
        controls_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        controls_scroll.setWidget(controls_content)
        controls_panel = QFrame()
        controls_panel.setObjectName("controlsPanel")
        controls_panel_layout = QVBoxLayout(controls_panel)
        controls_panel_layout.setContentsMargins(0, 0, 0, 0)
        controls_panel_layout.addWidget(controls_scroll)
        self.animation_controls_scroll = controls_scroll
        controls_panel.setMinimumWidth(320)
        controls_panel.setMaximumWidth(390)

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

        action_panel = QFrame()
        action_panel.setObjectName("actionPanel")
        action_layout = QHBoxLayout(action_panel)
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.addWidget(self.compile_button)
        action_layout.addWidget(self.compile_progress, 1)
        action_layout.addWidget(self.animation_play_button)
        action_layout.addWidget(self.animation_playback_label)
        action_layout.addWidget(self.terrain_batch_button)

        footer = QVBoxLayout()
        footer.addWidget(self.metrics)
        footer.addWidget(self.status)

        root = QVBoxLayout()
        root.setContentsMargins(18, 16, 18, 14)
        root.addLayout(body, 1)
        root.addWidget(action_panel)
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

    def _update_purpose_controls(self, *_args: object, clear_result: bool = True) -> None:
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
        self.shared_palette_group.setVisible(is_character)
        self.animation_play_button.setVisible(is_animation)
        self.animation_playback_label.setVisible(is_animation)
        self.secondary_preview_label.setText("アニメーションシート" if is_animation else "繰り返し確認")
        if not is_animation:
            self._stop_animation_playback()
        self._update_animation_split_controls()
        self._update_animation_geometry_controls()
        if is_character:
            self.repeat_opt.setCurrentIndex(1)
            self._update_canvas_selection()
        else:
            profile = resolve_terrain_gui_profile(self.pixelization_mode.currentData())
            self.auto_profile.setText(f"地形は64×64 / {profile.label} / {self.palette.value()}色")
            self.canvas.set_canvas_size((64, 64))
        if clear_result:
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
            if self.animation_placement_mode.currentData() == "preserve_motion":
                canvas_size = (self.animation_width.value(), self.animation_height.value())
                self.canvas.set_canvas_size(canvas_size)
                self.canvas.set_guide_point(
                    (self.animation_output_origin_x.value(), self.animation_output_origin_y.value())
                    if self.animation_output_origin_set.isChecked()
                    else None
                )
                columns = self.animation_columns.value()
                rows = self.animation_rows.value()
                scale_label = "自動fit" if self.animation_scale_mode.currentData() == "auto" else f"固定{self.animation_scale.value():g}倍"
                self.auto_profile.setText(
                    f"戦闘 / {canvas_size[0]}×{canvas_size[1]} / {columns}列×{rows}行 / {self.animation_palette.value()}色 / {scale_label}"
                )
                self._clear_stale_result(canvas_size)
                return
            columns = self.animation_columns.value()
            rows = self.animation_rows.value()
            profile = resolve_character_animation_gui_profile(canvas_size, frame_count=columns * rows)
            self.canvas.set_canvas_size(profile.canvas_size)
            self.canvas.set_guide_point(None)
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

    def _animation_split_signature(self) -> tuple[object, ...]:
        """元絵の分割結果を識別するGUI設定の組を返す。"""
        return (
            self.source_path,
            self.animation_split_mode.currentData(),
            self.animation_columns.value(),
            self.animation_rows.value(),
        )

    def _source_frame_boxes(self) -> tuple[tuple[int, int, int, int], ...]:
        """現在の分割設定で元絵上のフレーム矩形を取得する。"""
        if self.source_path is None:
            return ()
        columns = self.animation_columns.value()
        rows = self.animation_rows.value()
        config = CharacterAnimationConfig(
            frame_count=columns * rows,
            split_mode=self.animation_split_mode.currentData(),  # type: ignore[arg-type]
            grid_columns=columns,
            grid_rows=rows,
        )
        with Image.open(self.source_path) as opened:
            return animation_source_frame_boxes(opened, config)

    def _source_origin_display_point(self) -> tuple[int, int] | None:
        """フレーム内原点を元絵プレビュー上の座標へ戻す。"""
        if not self.animation_source_origin_set.isChecked():
            return None
        box = self._source_origin_frame_box
        if box is None or self._source_origin_frame_signature != self._animation_split_signature():
            try:
                box = self._source_frame_boxes()[0]
            except (OSError, ValueError, IndexError):
                box = None
        local = (self.animation_source_origin_x.value(), self.animation_source_origin_y.value())
        if box is None:
            return local
        return (box[0] + local[0], box[1] + local[1])

    def _update_animation_geometry_controls(self) -> None:
        is_animation = self.purpose.currentData() == "character_animation"
        is_motion = is_animation and self.animation_placement_mode.currentData() == "preserve_motion"
        for widget in (
            self.animation_placement_mode_label,
            self.animation_placement_mode,
        ):
            widget.setVisible(is_animation)
        for widget in (
            self.animation_width_label,
            self.animation_width,
            self.animation_height_label,
            self.animation_height,
            self.animation_source_origin_label,
            self.animation_source_origin_row,
            self.animation_output_origin_label,
            self.animation_output_origin_row,
            self.animation_scale_mode_label,
            self.animation_scale_mode,
            self.animation_scale_label,
            self.animation_scale,
            self.animation_palette_label,
            self.animation_palette,
            self.animation_shared_palette_label,
            self.animation_shared_palette,
        ):
            widget.setVisible(is_motion)
        for widget in (
            self.animation_source_origin_set,
            self.animation_source_origin_pick_button,
            self.animation_output_origin_set,
            self.animation_output_origin_pick_button,
        ):
            widget.setEnabled(is_motion)
        for widget in (
            self.animation_source_origin_x,
            self.animation_source_origin_y,
        ):
            widget.setEnabled(is_motion and self.animation_source_origin_set.isChecked())
        for widget in (
            self.animation_output_origin_x,
            self.animation_output_origin_y,
        ):
            widget.setEnabled(is_motion and self.animation_output_origin_set.isChecked())
        self.animation_scale.setEnabled(is_motion and self.animation_scale_mode.currentData() == "fixed")
        self.animation_scale_mode.setEnabled(is_motion)
        self.source_preview.set_guide_point(self._source_origin_display_point() if is_motion else None)
        self.canvas.set_guide_point(
            (self.animation_output_origin_x.value(), self.animation_output_origin_y.value())
            if is_motion and self.animation_output_origin_set.isChecked()
            else None
        )

    def start_source_origin_pick(self) -> None:
        self._origin_pick_target = "source"
        self.status.setText("元絵プレビュー上でソース原点をクリックしてください")

    def start_output_origin_pick(self) -> None:
        self._origin_pick_target = "output"
        self.status.setText("ドットプレビュー上で出力原点をクリックしてください")

    def _on_source_origin_clicked(self, point: object) -> None:
        if self._origin_pick_target != "source":
            return
        x, y = point  # type: ignore[misc]
        try:
            frame_boxes = self._source_frame_boxes()
            frame_index, local_point = map_source_point_to_frame(
                (int(x), int(y)),
                frame_boxes,
            )
        except (OSError, ValueError) as exc:
            self.status.setText(f"ソース原点を指定できませんでした: {exc}")
            return
        self._source_origin_frame_box = frame_boxes[frame_index]
        self._source_origin_frame_signature = self._animation_split_signature()
        self.animation_source_origin_set.setChecked(True)
        self.animation_source_origin_x.setValue(local_point[0])
        self.animation_source_origin_y.setValue(local_point[1])
        self._origin_pick_target = None
        self._update_animation_geometry_controls()
        self.status.setText(
            f"F{frame_index + 1}のソース原点を指定しました: ({local_point[0]}, {local_point[1]})"
        )

    def _on_output_origin_clicked(self, point: object) -> None:
        if self._origin_pick_target != "output":
            return
        x, y = point  # type: ignore[misc]
        self.animation_output_origin_set.setChecked(True)
        self.animation_output_origin_x.setValue(int(x))
        self.animation_output_origin_y.setValue(int(y))
        self._origin_pick_target = None
        self.status.setText(f"出力原点を指定しました: ({int(x)}, {int(y)})")

    def _show_animation_frame(self) -> None:
        if not self._animation_frame_paths:
            return
        path = self._animation_frame_paths[self._animation_frame_index]
        canvas_size = self._compiled_canvas_size
        self.result_preview.set_image(path)
        if canvas_size is not None:
            self.canvas.set_image(path, canvas_size)
        self.animation_playback_label.setText(
            f"F{self._animation_frame_index + 1}/{len(self._animation_frame_paths)}"
        )

    def _toggle_animation_playback(self) -> None:
        if not self._animation_frame_paths:
            return
        if self.animation_play_timer.isActive():
            self._stop_animation_playback()
            return
        self.animation_play_timer.start()
        self.animation_play_button.setText("■ 停止")

    def _stop_animation_playback(self) -> None:
        self.animation_play_timer.stop()
        self.animation_play_button.setText("▶ 再生")

    def _advance_animation_frame(self) -> None:
        if not self._animation_frame_paths:
            self._stop_animation_playback()
            return
        self._animation_frame_index = (self._animation_frame_index + 1) % len(self._animation_frame_paths)
        self._show_animation_frame()

    def _clear_stale_result(self, selected_size: tuple[int, int] | None = None) -> None:
        if self._compiled_canvas_size is None:
            return
        if selected_size is not None and self._compiled_canvas_size == selected_size:
            return
        self._compiled_canvas_size = None
        self._stop_animation_playback()
        self._animation_frame_paths = ()
        self._animation_frame_index = 0
        self.animation_play_button.setEnabled(False)
        self.animation_playback_label.setText("未再生")
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

    def set_shared_palette(
        self,
        colors,
        *,
        source_label: str = "共有palette",
    ) -> None:  # type: ignore[no-untyped-def]
        """キャラクターコンパイルに使う可視RGB paletteを固定する。"""
        normalized = validate_reference_palette(colors)
        self._mark_configuration_changed()
        self._shared_palette_colors = normalized
        self.shared_palette_info.setText(
            f"{source_label} / {len(normalized)}色 / {palette_id(normalized)[:12]}"
        )
        self._set_shared_palette_view(normalized)
        self._clear_stale_result()
        self.status.setText(f"{source_label}をキャラクター用共有paletteに設定しました")

    def clear_shared_palette(self) -> None:
        """キャラクターpaletteの自動選択へ戻す。"""
        self._mark_configuration_changed()
        self._shared_palette_colors = ()
        self.shared_palette_info.setText("未設定（地形タイルの基準paletteを反映できます）")
        self.shared_palette_view.clear()
        self._clear_stale_result()
        self.status.setText("キャラクター用共有paletteを解除しました")

    def load_shared_palette(self) -> None:
        """terrain batchが出力したpalette.jsonを読み込む。"""
        path, _ = QFileDialog.getOpenFileName(self, "共有paletteを読み込む", "", "palette.json (*.json)")
        if not path:
            return
        try:
            self.set_shared_palette(load_palette_json(Path(path)), source_label=Path(path).name)
        except (OSError, ValueError) as exc:
            self.status.setText(f"共有paletteを読み込めませんでした: {exc}")

    def _set_shared_palette_view(self, colors) -> None:  # type: ignore[no-untyped-def]
        self.shared_palette_view.clear()
        for red, green, blue in colors:
            item = QListWidgetItem(f"#{red:02X}{green:02X}{blue:02X}")
            item.setBackground(QColor(red, green, blue))
            item.setForeground(QColor("#ffffff" if red + green + blue < 390 else "#20252c"))
            item.setToolTip(f"RGB ({red}, {green}, {blue})")
            self.shared_palette_view.addItem(item)

    def _on_output_root_changed(self, _text: str) -> None:
        self._mark_configuration_changed()
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
        self._mark_configuration_changed()
        self.source_path = source
        self.source_preview.set_image(source)
        self._source_origin_frame_box = None
        self._source_origin_frame_signature = None
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

    def _start_compile(self, operation: Callable[[], object], context: dict[str, object]) -> None:
        if self._compile_thread is not None:
            return
        self._compile_context = {
            **context,
            "configuration_revision": self._configuration_revision,
        }
        self._set_compile_controls_enabled(False)
        self.compile_button.setEnabled(False)
        self.animation_play_button.setEnabled(False)
        self.compile_progress.setRange(0, 0)
        self.compile_progress.setFormat("変換中...")
        self.status.setText("変換中...")
        worker = _CompileWorker(operation)
        worker.succeeded.connect(self._on_compile_succeeded)
        worker.failed.connect(self._on_compile_failed)
        worker.finished.connect(self._on_compile_finished)
        self._compile_thread = worker
        worker.start()

    def _on_compile_succeeded(self, result: object) -> None:
        context = self._compile_context
        if context is None:
            return
        if context.get("configuration_revision") != self._configuration_revision:
            self._clear_stale_result()
            self.compile_progress.setRange(0, 1)
            self.compile_progress.setValue(1)
            self.compile_progress.setFormat("設定変更")
            self.status.setText(
                "変換は完了しましたが、実行中に設定が変更されたため現在のプレビューへ反映しませんでした。"
                "再コンパイルしてください"
            )
            return
        self.compile_progress.setRange(0, 1)
        self.compile_progress.setValue(1)
        self.compile_progress.setFormat("完了")
        if context["kind"] == "animation":
            animation = cast(CharacterAnimationCompileResult, result)
            width, height = cast(tuple[int, int], context["canvas_size"])
            output = cast(Path, context["output"])
            placement_mode = cast(str, context["placement_mode"])
            self._compiled_canvas_size = (width, height)
            self._animation_frame_paths = tuple(animation.final_frame_paths)
            self._animation_frame_index = 0
            self._show_animation_frame()
            self.animation_play_button.setEnabled(bool(self._animation_frame_paths))
            self.tile_preview.set_image(animation.preview_8x_path)
            self.secondary_preview_label.setText(f"アニメーションシート（{animation.preview_scale}倍）")
            self.metrics.setText(
                " / ".join(
                    [
                        f"出力 {width}×{height}",
                        f"{len(animation.final_frame_paths)}フレーム（{width * len(animation.final_frame_paths)}×{height} Sheet）",
                        f"フレーム集約 {animation.final_frame_paths[0].parent}",
                        f"保存先 {output}",
                    ]
                )
            )
            if placement_mode == "preserve_motion":
                self.status.setText("完了: 明示原点・共通倍率で移動を保持した戦闘アニメーションを出力しました")
            else:
                self.status.setText("完了: 分割・共通bbox・足元アンカーで待機アニメーションを出力しました")
            return

        compilation = cast(CompilationResult, result)
        width, height = cast(tuple[int, int], context["canvas_size"])
        output = cast(Path, context["output"])
        purpose = cast(str, context["purpose"])
        self.canvas.set_image(compilation.final_path, (width, height))
        self._compiled_canvas_size = (width, height)
        self.result_preview.set_image(compilation.final_path)
        tile = output / "debug" / "09_tile_preview.png"
        if not tile.exists():
            tile = output / "debug" / "08_tile_preview.png"
        self.tile_preview.set_image(tile if tile.exists() else None)
        self.metrics.setText(
            " / ".join(
                [
                    f"出力 {width}×{height}",
                    f"可視RGB {compilation.metrics.actual_palette_count}色",
                    f"保存先 {output}",
                ]
            )
        )
        if purpose == "character":
            self.status.setText("完了: B24をCanvasに合わせて自動適用しました")
        else:
            self.status.setText("完了: 64×64地形タイルを出力しました")

    def _on_compile_failed(self, message: str) -> None:
        self.compile_progress.setRange(0, 1)
        self.compile_progress.setValue(1)
        self.compile_progress.setFormat("失敗")
        self.status.setText(f"コンパイルできませんでした: {message}")

    def _on_compile_finished(self) -> None:
        worker = self._compile_thread
        self._compile_thread = None
        self._compile_context = None
        if worker is not None:
            worker.deleteLater()
        self._set_compile_controls_enabled(True)
        self.compile_button.setEnabled(self.source_path is not None)

    def _set_compile_controls_enabled(self, enabled: bool) -> None:
        """コンパイル中は入力変更を受け付けず、結果の世代を固定する。"""
        for widget in (
            self.open_button,
            self.source_preview,
            self.purpose,
            self.canvas_size,
            self.animation_split_mode,
            self.animation_columns,
            self.animation_rows,
            self.animation_placement_mode,
            self.animation_width,
            self.animation_height,
            self.animation_source_origin_set,
            self.animation_source_origin_x,
            self.animation_source_origin_y,
            self.animation_source_origin_pick_button,
            self.animation_output_origin_set,
            self.animation_output_origin_x,
            self.animation_output_origin_y,
            self.animation_output_origin_pick_button,
            self.animation_scale_mode,
            self.animation_scale,
            self.animation_shared_palette,
            self.animation_palette,
            self.pixelization_mode,
            self.palette,
            self.repeat_opt,
            self.output_root_field,
            self.output_browse_button,
            self.terrain_batch_button,
            self.shared_palette_load_button,
            self.shared_palette_clear_button,
        ):
            widget.setEnabled(enabled)
        if enabled:
            self._update_purpose_controls(clear_result=False)

    def compile_image(self) -> None:
        if self.source_path is None:
            self.status.setText("先に元絵を読み込んでください")
            return
        if self._compile_thread is not None:
            return
        try:
            source = self.source_path
            output_root = self._selected_output_root()
            purpose = self.purpose.currentData()
            if purpose == "character_animation":
                columns = self.animation_columns.value()
                rows = self.animation_rows.value()
                placement_mode = self.animation_placement_mode.currentData()
                if placement_mode == "preserve_motion":
                    if (
                        not self.animation_source_origin_set.isChecked()
                        or not self.animation_output_origin_set.isChecked()
                    ):
                        raise ValueError("戦闘モードでは画像／Canvas上で両方の原点を指定してください")
                    width, height = self.animation_width.value(), self.animation_height.value()
                    fit_within = (width, height)
                    bottom_margin = 0
                    source_origin = (
                        float(self.animation_source_origin_x.value()),
                        float(self.animation_source_origin_y.value()),
                    )
                    output_origin = (
                        float(self.animation_output_origin_x.value()),
                        float(self.animation_output_origin_y.value()),
                    )
                    scale_override = (
                        None
                        if self.animation_scale_mode.currentData() == "auto"
                        else float(self.animation_scale.value())
                    )
                    shared_palette_enabled = bool(self.animation_shared_palette.currentData())
                else:
                    profile = resolve_character_animation_gui_profile(
                        self.canvas_size.currentData(),
                        frame_count=columns * rows,
                    )
                    width, height = profile.canvas_size
                    fit_within = profile.fit_within
                    bottom_margin = profile.bottom_margin
                    source_origin = None
                    output_origin = None
                    scale_override = None
                    shared_palette_enabled = False
                shared_palette = self._shared_palette_colors or None
                shared_palette_enabled = shared_palette_enabled or shared_palette is not None
                output = build_output_path(
                    output_root,
                    source,
                    purpose="character_animation",
                    canvas_size=(width, height),
                    palette_token=palette_id(shared_palette) if shared_palette is not None else None,
                )
                config = CharacterAnimationConfig(
                    frame_count=columns * rows,
                    split_mode=self.animation_split_mode.currentData(),  # type: ignore[arg-type]
                    grid_columns=columns,
                    grid_rows=rows,
                    canvas_size=(width, height),
                    fit_within=fit_within,
                    bottom_margin=bottom_margin,
                    placement_mode=placement_mode,  # type: ignore[arg-type]
                    source_origin=source_origin,
                    output_origin=output_origin,
                    scale_override=scale_override,
                    shared_palette_enabled=shared_palette_enabled,
                )
                palette_budget = max(self.animation_palette.value(), len(shared_palette or ()))
                operation = lambda: compile_character_animation_sheet(
                    source,
                    output,
                    config=config,
                    palette_budget=palette_budget,
                    palette_colors=shared_palette,
                    character_detail_level="balanced",
                    debug_enabled=True,
                )
                self._start_compile(
                    operation,
                    {
                        "kind": "animation",
                        "canvas_size": (width, height),
                        "output": output,
                        "placement_mode": placement_mode,
                    },
                )
                return
            if purpose == "character":
                profile = resolve_character_gui_profile(self.canvas_size.currentData())
                width, height = profile.canvas_size
                shared_palette = self._shared_palette_colors or None
                output = build_output_path(
                    output_root,
                    source,
                    purpose="character",
                    canvas_size=(width, height),
                    palette_token=palette_id(shared_palette) if shared_palette is not None else None,
                )
                config = compiler_config_for_purpose(
                    "character",
                    output_root=output,
                    canvas=CanvasSpec(width, height),
                    palette_budget=max(profile.palette_budget, len(shared_palette or ())),
                    palette_colors=shared_palette,
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
                    source,
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
            operation = lambda: PixelTileCompiler().compile(source, config)
            self._start_compile(
                operation,
                {
                    "kind": "single",
                    "purpose": purpose,
                    "canvas_size": (width, height),
                    "output": output,
                },
            )
        except (OSError, RuntimeError, ValueError) as exc:
            self._on_compile_failed(str(exc))

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self._compile_thread is not None and self._compile_thread.isRunning():
            self._compile_thread.wait()
        event.accept()

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
            self._terrain_batch_window.reference_palette_changed.connect(self._on_terrain_palette_changed)
        self._terrain_batch_window.show()
        self._terrain_batch_window.raise_()
        self._terrain_batch_window.activateWindow()

    def _on_terrain_palette_changed(self, colors: object) -> None:
        """地形ウィンドウで選択した可視RGB paletteを転送する。"""
        if colors:
            self.set_shared_palette(colors, source_label="マップタイル基準")
        else:
            self.clear_shared_palette()
