"""Character-first PySide6 workbench for the pixel compiler."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, cast

from PySide6.QtCore import QPoint, QThread, QTimer, QSize, Qt, Signal
from dataclasses import replace
import numpy as np
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QFileDialog,
    QCheckBox,
    QApplication,
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
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)
from PIL import Image

from pixel_tile_compiler.config import CanvasSpec, compiler_config_for_purpose
from pixel_tile_compiler.gui.canvas import CanvasState, ZOOMS
from pixel_tile_compiler.gui.input import first_supported_image_path
from pixel_tile_compiler.gui.theme import build_stylesheet
from pixel_tile_compiler.gui.policy import (
    GUI_ANIMATION_SPLIT_OPTIONS,
    GUI_CANVAS_MAX_SIDE,
    GUI_CANVAS_MIN_SIDE,
    GUI_CHARACTER_CANVAS_PRESETS,
    GUI_TERRAIN_PIXELIZATION_OPTIONS,
    build_output_path,
    resolve_character_animation_gui_profile,
    resolve_character_gui_profile,
    resolve_terrain_gui_profile,
)
from pixel_tile_compiler.palette_contract import (
    extract_final_palette,
    load_palette_json,
    palette_id,
    validate_reference_palette,
)
from pixel_tile_compiler.pipeline.compiler import CompilationResult, PixelTileCompiler
from pixel_tile_compiler.pixelizer.character_animation import (
    CharacterAnimationConfig,
    CharacterAnimationCompileResult,
    animation_source_frame_coordinates,
    compile_character_animation_sheet,
    load_character_animation_report,
    load_component_assignment_data,
    load_component_assignments,
    map_source_point_to_frame,
    save_component_assignments,
)
from pixel_tile_compiler.sheet.alpha_projection import split_sprite_sheet
from pixel_tile_compiler.gui.widgets import (
    NoWheelComboBox,
    NoWheelDoubleSpinBox,
    NoWheelSpinBox,
    PaletteSwatchList,
)
from pixel_tile_compiler.sheet.component_split import (
    ComponentCell,
    ComponentSplitResult,
    analyze_component_split,
    render_component_split_overlay,
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
        self.setMinimumSize(200, 150)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFrameShadow(QFrame.Shadow.Plain)
        self.setToolTip("透明部分は市松模様で表示します")
        self._guide_point: tuple[float, float] | None = None

    def set_image(self, path: Path | None) -> None:
        self._path = Path(path) if path is not None else None
        self._image = None
        self.update()

    def set_pil_image(self, image: Image.Image | None) -> None:
        self._path = None
        if image is None:
            self._image = None
        else:
            rgba = image.convert("RGBA")
            self._image = QImage(
                rgba.tobytes(),
                rgba.width,
                rgba.height,
                rgba.width * 4,
                QImage.Format.Format_RGBA8888,
            ).copy()
        self.update()

    def set_guide_point(self, point: tuple[float, float] | None) -> None:
        self._guide_point = point
        self.update()

    def _display_geometry(self) -> tuple[QImage, tuple[int, int, int, int]] | None:
        if hasattr(self, "_image") and self._image is not None:
            image = self._image
        elif self._path is not None and self._path.exists():
            image = QImage(str(self._path)).convertToFormat(QImage.Format.Format_RGBA8888)
        else:
            return None
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
    """Image preview that accepts one supported local image by drag-and-drop,
    supports component click/drag-rectangle selection, and displays highlight/suspicious boxes."""

    image_dropped = Signal(object)
    image_point_clicked = Signal(object)
    rect_selected = Signal(object)

    def __init__(self, empty_text: str) -> None:
        super().__init__(empty_text)
        self.setAcceptDrops(True)
        self.setToolTip("元絵をここへドロップできます。透明部分は市松模様で表示します")
        self._highlight_boxes: list[tuple[int, int, int, int]] = []
        self._suspicious_boxes: list[tuple[int, int, int, int]] = []
        self._drag_start: QPoint | None = None
        self._drag_current: QPoint | None = None
        self._is_dragging: bool = False

    def set_highlight_boxes(self, boxes: list[tuple[int, int, int, int]]) -> None:
        self._highlight_boxes = list(boxes)
        self.update()

    def set_suspicious_boxes(self, boxes: list[tuple[int, int, int, int]]) -> None:
        self._suspicious_boxes = list(boxes)
        self.update()

    def clear_boxes(self) -> None:
        self._highlight_boxes.clear()
        self._suspicious_boxes.clear()
        self.update()

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
                _image, (left, top, width, height) = geometry
                pos = event.position()
                if left <= pos.x() < left + width and top <= pos.y() < top + height:
                    self._drag_start = pos.toPoint()
                    self._drag_current = self._drag_start
                    self._is_dragging = False
                    event.accept()
                    return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self._drag_start is not None and (event.buttons() & Qt.MouseButton.LeftButton):
            pos = event.position().toPoint()
            delta = (pos - self._drag_start).manhattanLength()
            if delta > 3:
                self._is_dragging = True
                self._drag_current = pos
                self.update()
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if event.button() == Qt.MouseButton.LeftButton and self._drag_start is not None:
            geometry = self._display_geometry()
            if geometry is not None:
                image, (left, top, width, height) = geometry
                if self._is_dragging and self._drag_current is not None:
                    x0_screen = min(self._drag_start.x(), self._drag_current.x())
                    x1_screen = max(self._drag_start.x(), self._drag_current.x())
                    y0_screen = min(self._drag_start.y(), self._drag_current.y())
                    y1_screen = max(self._drag_start.y(), self._drag_current.y())

                    x0 = max(0, min(image.width(), int((x0_screen - left) * image.width() / max(1, width))))
                    x1 = max(0, min(image.width(), int((x1_screen - left) * image.width() / max(1, width)) + 1))
                    y0 = max(0, min(image.height(), int((y0_screen - top) * image.height() / max(1, height))))
                    y1 = max(0, min(image.height(), int((y1_screen - top) * image.height() / max(1, height)) + 1))
                    self._drag_start = None
                    self._drag_current = None
                    self._is_dragging = False
                    self.update()
                    if x1 > x0 and y1 > y0:
                        self.rect_selected.emit((x0, y0, x1, y1))
                    event.accept()
                    return
                else:
                    pos = event.position()
                    x = min(image.width() - 1, max(0, int((pos.x() - left) * image.width() / max(1, width))))
                    y = min(image.height() - 1, max(0, int((pos.y() - top) * image.height() / max(1, height))))
                    self._drag_start = None
                    self._drag_current = None
                    self._is_dragging = False
                    self.image_point_clicked.emit((x, y))
                    event.accept()
                    return
            self._drag_start = None
            self._drag_current = None
            self._is_dragging = False
            self.update()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        super().paintEvent(event)
        geometry = self._display_geometry()
        if geometry is None:
            return
        image, (left, top, width, height) = geometry
        painter = QPainter(self)
        try:
            if self._suspicious_boxes:
                pen = QPen(QColor("#ffaa00"), 2, Qt.PenStyle.DashLine)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                for x0, y0, x1, y1 in self._suspicious_boxes:
                    sx0 = left + int(x0 * width / max(1, image.width()))
                    sy0 = top + int(y0 * height / max(1, image.height()))
                    sx1 = left + int(x1 * width / max(1, image.width()))
                    sy1 = top + int(y1 * height / max(1, image.height()))
                    painter.drawRect(sx0, sy0, max(1, sx1 - sx0), max(1, sy1 - sy0))

            if self._highlight_boxes:
                pen = QPen(QColor("#00ffcc"), 2, Qt.PenStyle.SolidLine)
                painter.setPen(pen)
                painter.setBrush(QColor(0, 255, 204, 40))
                for x0, y0, x1, y1 in self._highlight_boxes:
                    sx0 = left + int(x0 * width / max(1, image.width()))
                    sy0 = top + int(y0 * height / max(1, image.height()))
                    sx1 = left + int(x1 * width / max(1, image.width()))
                    sy1 = top + int(y1 * height / max(1, image.height()))
                    painter.drawRect(sx0, sy0, max(1, sx1 - sx0), max(1, sy1 - sy0))

            if self._is_dragging and self._drag_start is not None and self._drag_current is not None:
                rx = min(self._drag_start.x(), self._drag_current.x())
                ry = min(self._drag_start.y(), self._drag_current.y())
                rw = abs(self._drag_current.x() - self._drag_start.x())
                rh = abs(self._drag_current.y() - self._drag_start.y())
                painter.setPen(QPen(QColor("#4488ff"), 1, Qt.PenStyle.DashLine))
                painter.setBrush(QColor(68, 136, 255, 60))
                painter.drawRect(rx, ry, rw, rh)
        finally:
            painter.end()


class PixelCanvas(QGraphicsView):
    """Nearest-neighbor canvas with a dynamic grid and alpha checkerboard."""

    point_clicked = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.state = CanvasState(zoom=4, canvas_size=(128, 128))
        self.setScene(QGraphicsScene(self))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        self.setMinimumSize(200, 200)
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


class _AnalysisWorker(QThread):
    """Run one component split analysis away from the Qt GUI thread."""

    succeeded = Signal(object, int)
    failed = Signal(str, int)

    def __init__(
        self,
        operation: Callable[[], ComponentSplitResult],
        revision: int,
        signature: tuple[object, ...] = (),
    ) -> None:
        super().__init__()
        self._operation = operation
        self._revision = revision
        self._signature = signature

    def run(self) -> None:
        try:
            self.succeeded.emit(self._operation(), self._revision)
        except Exception as exc:  # pragma: no cover
            self.failed.emit(str(exc), self._revision)


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
        self._analysis_thread: _AnalysisWorker | None = None
        self._analysis_workers: set[_AnalysisWorker] = set()
        self._close_pending: bool = False
        self._settings_pane_fitted: bool = False
        self._configuration_revision = 0
        self._animation_frame_paths: tuple[Path, ...] = ()
        self._animation_frame_index = 0
        self._source_origin_frame_box: tuple[int, int, int, int] | None = None
        self._source_origin_frame_index: int | None = None
        self._source_origin_frame_logical_origin: tuple[int, int] | None = None
        self._source_origin_frame_signature: tuple[object, ...] | None = None
        self._component_analysis: ComponentSplitResult | None = None
        self._component_analysis_signature: tuple[object, ...] | None = None
        self._component_assignment_confirmed: bool = False
        self._component_assignment_overrides: dict[int, str] = {}
        self._selected_component_ids: list[int] = []
        self._suspicious_index: int = 0
        self._component_assignment_combos: dict[int, QComboBox] = {}
        self.default_output_root = Path.cwd() / "output"
        self.source_preview = SourceImagePreview("元絵を読み込んでください\nまたはここにドロップ")
        self.source_preview.image_dropped.connect(self.set_source_path)
        self.source_preview.image_point_clicked.connect(self._on_source_preview_clicked)
        self.source_preview.rect_selected.connect(self._on_source_preview_rect_selected)
        self.result_preview = ImagePreview("コンパイル結果")
        self.tile_preview = ImagePreview("タイルプレビュー")
        self.canvas = PixelCanvas()
        self.canvas.point_clicked.connect(self._on_output_origin_clicked)
        self.status = QLabel("元絵を読み込むと始められます")
        self.metrics = QLabel("出力情報はここに表示されます")
        self.shared_palette_info = QLabel("未設定（地形タイルの基準paletteを反映できます）")
        self.shared_palette_info.setWordWrap(True)
        self.shared_palette_view = PaletteSwatchList()
        self.shared_palette_view.setMaximumHeight(66)
        self.output_palette_info = QLabel("コンパイルすると、使用したpaletteをここに表示します")
        self.output_palette_info.setWordWrap(True)
        self.output_palette_view = PaletteSwatchList()
        self.shared_palette_load_button = QPushButton("読み込む")
        self.shared_palette_load_button.setToolTip("地形の一括コンパイルが書き出したpalette.jsonを読み込みます")
        self.shared_palette_load_button.clicked.connect(self.load_shared_palette)
        self.shared_palette_clear_button = QPushButton("解除")
        self.shared_palette_clear_button.setToolTip("基準paletteの指定を外し、自動paletteへ戻します")
        self.shared_palette_clear_button.clicked.connect(self.clear_shared_palette)
        self.output_root_field = QLineEdit(str(self.default_output_root))
        self.output_root_field.setToolTip("コンパイル結果を保存するフォルダ")
        self.output_root_field.setCursorPosition(0)
        self.output_root_field.textChanged.connect(self._on_output_root_changed)
        self.output_browse_button = QPushButton("参照...")
        self.output_browse_button.clicked.connect(self.choose_output_directory)
        self.purpose = NoWheelComboBox()
        self.purpose.addItem("キャラクター", userData="character")
        self.purpose.addItem("キャラクターアニメーション", userData="character_animation")
        self.purpose.addItem("地形（64×64）", userData="terrain")
        self.purpose.currentIndexChanged.connect(self._update_purpose_controls)
        self.canvas_size_label = QLabel("出力Canvasサイズ")
        self.canvas_size = NoWheelComboBox()
        for label, size in GUI_CHARACTER_CANVAS_PRESETS:
            self.canvas_size.addItem(label, userData=size)
        # 末尾は自由入力。サイズは幅・高さの入力から取る。
        self.canvas_size.addItem("自由入力", userData=None)
        self.canvas_size.currentIndexChanged.connect(self._update_canvas_selection)
        self.canvas_size.currentIndexChanged.connect(self._update_canvas_controls)
        self.canvas_width = NoWheelSpinBox()
        self.canvas_width.setRange(GUI_CANVAS_MIN_SIDE, GUI_CANVAS_MAX_SIDE)
        self.canvas_width.setValue(128)
        self.canvas_height = NoWheelSpinBox()
        self.canvas_height.setRange(GUI_CANVAS_MIN_SIDE, GUI_CANVAS_MAX_SIDE)
        self.canvas_height.setValue(128)
        self.canvas_custom_label = QLabel("幅 × 高さ")
        self.canvas_width.valueChanged.connect(self._update_canvas_selection)
        self.canvas_height.valueChanged.connect(self._update_canvas_selection)
        self.canvas_width.valueChanged.connect(self._update_canvas_controls)
        self.canvas_height.valueChanged.connect(self._update_canvas_controls)
        self.canvas_effective_note = QLabel(
            "実効解像度は短辺で決まります（256×128の被写体は128×128相当）"
        )
        self.canvas_effective_note.setObjectName("mutedText")
        self.canvas_effective_note.setWordWrap(True)
        self.background_mode = NoWheelComboBox()
        self.background_mode.addItem("単色背景を自動で透過にする", userData="auto")
        self.background_mode.addItem("元絵の透明をそのまま使う", userData="alpha")
        self.background_mode.addItem("指定した色を透過にする", userData="color")
        self.background_mode_label = QLabel("背景の扱い")
        self.background_mode.currentIndexChanged.connect(self._update_purpose_controls)
        self.background_color_field = QLineEdit("#ffffff")
        self.background_color_field.setToolTip("透過にする色を #RRGGBB で指定します")
        self.background_color_field.setMaximumWidth(120)
        self.background_color_label = QLabel("背景色")
        self.composition_mode = NoWheelComboBox()
        self.composition_mode.addItem("被写体を収める（余白あり）", userData="single_frame")
        self.composition_mode.addItem("画面全体をそのまま使う（余白なし）", userData="pre_aligned")
        self.composition_mode.setToolTip(
            "画面全体をそのまま使うと、元絵の隅々まで出力へ入ります。"
            "背景ごと1枚のアセットにしたいときに選んでください"
        )
        self.composition_mode_label = QLabel("構図")
        self.composition_mode.currentIndexChanged.connect(self._update_purpose_controls)
        self.animation_split_mode = NoWheelComboBox()
        for label, mode in GUI_ANIMATION_SPLIT_OPTIONS:
            self.animation_split_mode.addItem(label, userData=mode)
        self.animation_split_mode_label = QLabel("アニメーション分割方式")
        self.animation_component_group = QGroupBox("パーツの割り当て（コマ抽出プレビュー）")
        self.animation_component_assignment_status = QLabel("パーツ分割を選ぶと確認できます")
        self.animation_component_assignment_status.setWordWrap(True)
        self.animation_component_analyze_button = QPushButton("パーツを解析")
        self.animation_component_analyze_button.clicked.connect(self.analyze_component_assignments)
        self.animation_component_confirm_button = QPushButton("全コマの割り当てを確定")
        self.animation_component_confirm_button.setObjectName("primaryButton")
        self.animation_component_confirm_button.clicked.connect(self.confirm_component_assignments)
        self.animation_component_save_button = QPushButton("割り当てを保存...")
        self.animation_component_save_button.clicked.connect(self.save_component_assignment_file)
        self.animation_component_load_button = QPushButton("割り当てを読み込む...")
        self.animation_component_load_button.clicked.connect(self.load_component_assignment_file)
        self.animation_component_selected_frame = NoWheelComboBox()
        self.animation_component_assignment_rows = QWidget()
        QVBoxLayout(self.animation_component_assignment_rows)

        # コマカード用スクロールエリア
        self.animation_frame_cards_scroll = QScrollArea()
        self.animation_frame_cards_scroll.setWidgetResizable(True)
        self.animation_frame_cards_scroll.setMinimumHeight(120)
        self.animation_frame_cards_container = QWidget()
        self.animation_frame_cards_layout = QHBoxLayout(self.animation_frame_cards_container)
        self.animation_frame_cards_layout.setContentsMargins(4, 4, 4, 4)
        self.animation_frame_cards_layout.setSpacing(8)
        self.animation_frame_cards_scroll.setWidget(self.animation_frame_cards_container)

        # 選択したパーツの割り当てパネル
        self.animation_component_selection_info = QLabel("元絵をクリックまたはドラッグしてパーツを選択できます")
        self.animation_component_selection_info.setWordWrap(True)
        self.animation_component_target_combo = NoWheelComboBox()
        self.animation_component_reassign_button = QPushButton("割り当てを変更")
        self.animation_component_reassign_button.clicked.connect(self.reassign_selected_components)
        self.animation_component_next_suspicious_button = QPushButton("次の要確認パーツへ ⚠️")
        self.animation_component_next_suspicious_button.clicked.connect(self.focus_next_suspicious)
        self.animation_component_reset_button = QPushButton("変更をリセット")
        self.animation_component_reset_button.clicked.connect(self.reset_component_overrides)

        self.animation_columns = NoWheelSpinBox()
        self.animation_columns.setRange(1, 64)
        self.animation_columns.setValue(4)
        self.animation_columns_label = QLabel("分割列数")
        self.animation_rows = NoWheelSpinBox()
        self.animation_rows.setRange(1, 64)
        self.animation_rows.setValue(1)
        self.animation_rows_label = QLabel("分割行数")
        self.animation_placement_mode = NoWheelComboBox()
        self.animation_placement_mode.addItem("足元をそろえる（待機向き）", userData="legacy_foot")
        self.animation_placement_mode.addItem("移動を保持する（戦闘向き）", userData="preserve_motion")
        self.animation_placement_mode_label = QLabel("配置方式")
        self.animation_width = NoWheelSpinBox()
        self.animation_width.setRange(1, 4096)
        self.animation_width.setValue(256)
        self.animation_width_label = QLabel("出力Canvas幅")
        self.animation_height = NoWheelSpinBox()
        self.animation_height.setRange(1, 4096)
        self.animation_height.setValue(192)
        self.animation_height_label = QLabel("出力Canvas高さ")
        self.animation_source_origin_x = NoWheelSpinBox()
        self.animation_source_origin_x.setRange(-4096, 4096)
        self.animation_source_origin_x.setValue(0)
        self.animation_source_origin_y = NoWheelSpinBox()
        self.animation_source_origin_y.setRange(-4096, 4096)
        self.animation_source_origin_y.setValue(0)
        self.animation_source_origin_x.setFixedWidth(64)
        self.animation_source_origin_y.setFixedWidth(64)
        self.animation_source_origin_y_label = QLabel("元絵の原点Y")
        self.animation_source_origin_label = QLabel("元絵の原点（x, y）")
        self.animation_source_origin_set = QCheckBox("指定")
        self.animation_source_origin_pick_button = QPushButton("元絵から指定")
        self.animation_source_origin_pick_button.clicked.connect(self.start_source_origin_pick)
        self.animation_output_origin_x = NoWheelSpinBox()
        self.animation_output_origin_x.setRange(-4096, 4096)
        self.animation_output_origin_x.setValue(0)
        self.animation_output_origin_y = NoWheelSpinBox()
        self.animation_output_origin_y.setRange(-4096, 4096)
        self.animation_output_origin_y.setValue(0)
        self.animation_output_origin_x.setFixedWidth(64)
        self.animation_output_origin_y.setFixedWidth(64)
        self.animation_output_origin_y_label = QLabel("出力Canvasの原点Y")
        self.animation_output_origin_label = QLabel("出力Canvasの原点（x, y）")
        self.animation_output_origin_set = QCheckBox("指定")
        self.animation_output_origin_pick_button = QPushButton("ドットプレビューから指定")
        self.animation_output_origin_pick_button.clicked.connect(self.start_output_origin_pick)
        self.animation_scale_mode = NoWheelComboBox()
        self.animation_scale_mode.addItem("自動（Canvasに合わせる）", userData="auto")
        self.animation_scale_mode.addItem("固定", userData="fixed")
        self.animation_scale_mode_label = QLabel("拡大率の決め方")
        self.animation_scale = NoWheelDoubleSpinBox()
        self.animation_scale.setRange(0.01, 100.0)
        self.animation_scale.setSingleStep(0.05)
        self.animation_scale.setDecimals(3)
        self.animation_scale.setValue(1.0)
        self.animation_scale_label = QLabel("拡大率")
        self.animation_shared_palette = NoWheelComboBox()
        self.animation_shared_palette.addItem("有効", userData=True)
        self.animation_shared_palette.addItem("無効", userData=False)
        self.animation_shared_palette_label = QLabel("コマ間でpaletteを統一")
        self.animation_palette = NoWheelSpinBox()
        self.animation_palette.setRange(4, 64)
        self.animation_palette.setValue(24)
        self.animation_palette_label = QLabel("palette上限（色数）")
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
        self.palette = NoWheelSpinBox()
        self.palette.setRange(4, 64)
        self.palette.setValue(24)
        self.palette_label = QLabel("palette上限（色数）")
        self.pixelization_mode = NoWheelComboBox()
        for label, mode in GUI_TERRAIN_PIXELIZATION_OPTIONS:
            self.pixelization_mode.addItem(label, userData=mode)
        self.pixelization_mode_label = QLabel("ドット化の方法")
        self.repeat_opt = NoWheelComboBox()
        self.repeat_opt.addItem("有効", userData=True)
        self.repeat_opt.addItem("無効", userData=False)
        self.repeat_opt.setCurrentIndex(1)
        self.repeat_opt_label = QLabel("繰り返し最適化")
        self.secondary_preview_label = QLabel("タイル繰り返し確認")
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
        self.zoom = NoWheelComboBox()
        for value in ZOOMS:
            self.zoom.addItem(f"{value}×", userData=value)
        self.zoom.setCurrentIndex(ZOOMS.index(4))
        self.zoom.setToolTip("ドットプレビューの表示倍率")
        self.zoom.currentIndexChanged.connect(self._change_zoom)
        self._build_ui()
        self._update_purpose_controls()
        self._fit_settings_pane_to_purpose()
        self._apply_ui_font()

    @property
    def shared_palette_colors(self) -> tuple[tuple[int, int, int], ...]:
        """地形workflowから共有された正確なRGB paletteを返す。"""
        return self._shared_palette_colors

    def _connect_configuration_revision_signals(self) -> None:
        """コンパイル結果がどの設定世代のものか追跡する。"""
        for widget in (
            self.animation_split_mode,
        ):
            widget.currentIndexChanged.connect(self._invalidate_component_split)
        for widget in (
            self.animation_columns,
            self.animation_rows,
        ):
            widget.valueChanged.connect(self._invalidate_component_split)

        for widget in (
            self.purpose,
            self.canvas_size,
            self.animation_placement_mode,
            self.animation_scale_mode,
            self.animation_shared_palette,
            self.pixelization_mode,
            self.repeat_opt,
            self.background_mode,
            self.composition_mode,
        ):
            widget.currentIndexChanged.connect(self._mark_configuration_changed)
        for widget in (
            self.animation_width,
            self.animation_height,
            self.animation_source_origin_x,
            self.animation_source_origin_y,
            self.animation_output_origin_x,
            self.animation_output_origin_y,
            self.animation_scale,
            self.animation_palette,
            self.palette,
            self.canvas_width,
            self.canvas_height,
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

    def _invalidate_component_split(self, *_args: object) -> None:
        """分割方式や列・行数の変更時にパーツ解析結果を破棄し世代を進める。"""
        self._configuration_revision += 1
        self._component_analysis = None
        self._component_analysis_signature = None
        self._component_assignment_confirmed = False
        self._component_assignment_overrides.clear()

    def _apply_ui_font(self) -> None:
        """Prefer a Windows Japanese UI font so labels never fall back to tofu boxes."""
        for family in ("Yu Gothic UI", "Meiryo UI", "Noto Sans JP", "MS UI Gothic"):
            if QFontDatabase.hasFamily(family):
                font = QFont(family)
                font.setPointSize(10)
                self.setFont(font)
                return

    # ---- 画面構築 -------------------------------------------------------

    SPLIT_TAB_INDEX = 1
    PLACEMENT_TAB_INDEX = 2

    def _build_ui(self) -> None:
        """上下分割ワークベンチを組み立てる。

        上段=元絵とプレビュー、下段=用途別の設定タブ、最下段=実行バーと状態表示。
        """
        self.open_button = QPushButton("元絵を読み込む")
        self.open_button.setObjectName("primaryButton")
        self.open_button.clicked.connect(self.open_image)
        self.compile_button = QPushButton("コンパイルする")
        self.compile_button.setObjectName("primaryButton")
        self.compile_button.clicked.connect(self.compile_image)
        self.compile_button.setEnabled(False)
        self.terrain_batch_button = QPushButton("地形をまとめてコンパイル")
        self.terrain_batch_button.clicked.connect(self.open_terrain_batch)

        self.workbench_splitter = QSplitter(Qt.Orientation.Vertical)
        self.workbench_splitter.setObjectName("workbenchSplitter")
        self.workbench_splitter.setChildrenCollapsible(False)
        self.workbench_splitter.addWidget(self._build_preview_area())
        self.workbench_splitter.addWidget(self._build_settings_area())
        # 縦に余った分はプレビュー側が取り、設定側は必要な高さを保つ。
        self.workbench_splitter.setStretchFactor(0, 1)
        self.workbench_splitter.setStretchFactor(1, 0)
        self.workbench_splitter.setSizes([560, 290])
        self.purpose.currentIndexChanged.connect(self._fit_settings_pane_to_purpose)

        root = QVBoxLayout()
        root.setContentsMargins(14, 12, 14, 10)
        root.setSpacing(10)
        root.addWidget(self._build_header())
        root.addWidget(self.workbench_splitter, 1)
        root.addWidget(self._build_action_bar())
        container = QWidget()
        container.setLayout(root)
        self.setCentralWidget(container)
        self.setStyleSheet(build_stylesheet())

    def showEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        """初回表示時に、実際のレイアウト寸法で下段の高さを合わせ直す。"""
        super().showEvent(event)
        if not self._settings_pane_fitted:
            self._settings_pane_fitted = True
            self._fit_settings_pane_to_purpose()

    def _fit_settings_pane_to_purpose(self, *_args: object) -> None:
        """有効な設定タブが収まる高さへ下段を合わせ、余った縦をプレビューへ回す。"""
        tab_bar_height = self.settings_tabs.tabBar().sizeHint().height()
        needed = tab_bar_height + 24
        for index in range(self.settings_tabs.count()):
            if self.settings_tabs.isTabEnabled(index):
                page_height = self.settings_tabs.widget(index).sizeHint().height()
                needed = max(needed, page_height + tab_bar_height + 16)
        total = sum(self.workbench_splitter.sizes())
        needed = max(self.animation_controls_scroll.minimumHeight(), min(needed, total - 240))
        self.workbench_splitter.setSizes([total - needed, needed])

    def selected_canvas_size(self) -> tuple[int, int]:
        """現在選ばれている出力Canvasサイズを返す。自由入力なら幅・高さの値。"""
        preset = self.canvas_size.currentData()
        if preset is not None:
            return (int(preset[0]), int(preset[1]))
        return (self.canvas_width.value(), self.canvas_height.value())

    def _build_header(self) -> QWidget:
        """用途セグメントと元絵の読み込み導線を並べたヘッダ。"""
        header = QFrame()
        header.setObjectName("headerPanel")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)
        purpose_caption = QLabel("用途")
        purpose_caption.setObjectName("mutedText")
        layout.addWidget(purpose_caption)

        self.purpose_segment_group = QButtonGroup(self)
        self.purpose_segment_group.setExclusive(True)
        self.purpose_segments: dict[str, QPushButton] = {}
        for index in range(self.purpose.count()):
            key = str(self.purpose.itemData(index))
            button = QPushButton(self.purpose.itemText(index))
            button.setObjectName("segmentButton")
            button.setCheckable(True)
            button.setChecked(index == self.purpose.currentIndex())
            button.clicked.connect(lambda _checked=False, target=key: self._select_purpose(target))
            self.purpose_segment_group.addButton(button)
            layout.addWidget(button)
            self.purpose_segments[key] = button

        # purposeコンボは選択状態の正本として残し、操作はセグメントに集約する。
        layout.addWidget(self.purpose)
        self.purpose.setVisible(False)
        self.purpose.currentIndexChanged.connect(self._sync_purpose_segments)

        layout.addStretch(1)
        layout.addWidget(self.open_button)
        return header

    def _select_purpose(self, purpose: str) -> None:
        """用途セグメントの選択をpurposeコンボへ反映する。"""
        index = self.purpose.findData(purpose)
        if index >= 0:
            self.purpose.setCurrentIndex(index)
        self._sync_purpose_segments()

    def _sync_purpose_segments(self, *_args: object) -> None:
        """purposeコンボの現在値をセグメントの選択状態へ反映する。"""
        current = self.purpose.currentData()
        for key, button in self.purpose_segments.items():
            button.setChecked(key == current)

    def _build_preview_area(self) -> QSplitter:
        """上段: 元絵・プレビュータブ・パーツ割り当てを横に並べる。"""
        self.preview_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.preview_splitter.setObjectName("previewSplitter")
        self.preview_splitter.setChildrenCollapsible(False)

        source_group = QGroupBox("元絵")
        source_layout = QVBoxLayout(source_group)
        source_layout.setContentsMargins(0, 4, 0, 0)
        source_layout.addWidget(self.source_preview, 1)
        source_group.setMinimumWidth(200)
        self.preview_splitter.addWidget(source_group)

        self.preview_tabs = QTabWidget()
        self.preview_tabs.setObjectName("previewTabs")
        self.preview_tabs.setDocumentMode(True)

        canvas_page = QWidget()
        canvas_layout = QVBoxLayout(canvas_page)
        canvas_layout.setContentsMargins(8, 8, 8, 8)
        canvas_layout.setSpacing(6)
        canvas_layout.addWidget(self.canvas, 1)
        zoom_row = QHBoxLayout()
        zoom_row.setSpacing(8)
        zoom_caption = QLabel("プレビュー倍率")
        zoom_caption.setObjectName("mutedText")
        zoom_row.addWidget(zoom_caption)
        zoom_row.addWidget(self.zoom)
        zoom_row.addStretch(1)
        zoom_hint = QLabel("Ctrl+ホイールで倍率")
        zoom_hint.setObjectName("mutedText")
        zoom_hint.setToolTip("ドットプレビュー上でCtrlを押しながらホイールを回すと倍率が変わります")
        zoom_row.addWidget(zoom_hint)
        canvas_layout.addLayout(zoom_row)
        self.preview_tabs.addTab(canvas_page, "ドットプレビュー")

        result_page = QWidget()
        result_layout = QVBoxLayout(result_page)
        result_layout.setContentsMargins(8, 8, 8, 8)
        result_layout.addWidget(self.result_preview, 1)
        self.preview_tabs.addTab(result_page, "コンパイル結果")

        sheet_page = QWidget()
        sheet_layout = QVBoxLayout(sheet_page)
        sheet_layout.setContentsMargins(8, 8, 8, 8)
        sheet_layout.setSpacing(6)
        sheet_layout.addWidget(self.secondary_preview_label)
        sheet_layout.addWidget(self.tile_preview, 1)
        self.sheet_tab_index = self.preview_tabs.addTab(sheet_page, "タイル繰り返し確認")

        palette_page = QWidget()
        palette_layout = QVBoxLayout(palette_page)
        palette_layout.setContentsMargins(8, 8, 8, 8)
        palette_layout.setSpacing(6)
        palette_layout.addWidget(self.output_palette_info)
        palette_layout.addWidget(self.output_palette_view, 1)
        self.preview_tabs.addTab(palette_page, "palette")

        self.preview_splitter.addWidget(self.preview_tabs)
        self.preview_splitter.addWidget(self._build_component_panel())
        # 横に余った分はプレビュー側が取り、元絵とパーツ割り当ては指定した幅を保つ。
        self.preview_splitter.setStretchFactor(0, 0)
        self.preview_splitter.setStretchFactor(1, 1)
        self.preview_splitter.setStretchFactor(2, 0)
        self.preview_splitter.setSizes([320, 640, 400])
        return self.preview_splitter

    def _build_component_panel(self) -> QWidget:
        """パーツの割り当てパネル。元絵と並べてパーツを選び直せる位置に置く。"""
        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(8)
        content_layout.addWidget(self.animation_component_assignment_status)
        content_layout.addWidget(self.animation_component_analyze_button)

        cards_title = QLabel("コマ抽出プレビュー (F1〜Fn)")
        cards_title.setObjectName("subsectionTitle")
        content_layout.addWidget(cards_title)
        content_layout.addWidget(self.animation_frame_cards_scroll)

        edit_box = QGroupBox("選択したパーツの割り当て")
        edit_layout = QVBoxLayout(edit_box)
        edit_layout.addWidget(self.animation_component_selection_info)
        reassign_row = QHBoxLayout()
        reassign_row.addWidget(QLabel("割り当て先:"))
        reassign_row.addWidget(self.animation_component_target_combo, 1)
        reassign_row.addWidget(self.animation_component_reassign_button)
        edit_layout.addLayout(reassign_row)
        action_row = QHBoxLayout()
        action_row.addWidget(self.animation_component_next_suspicious_button)
        action_row.addWidget(self.animation_component_reset_button)
        edit_layout.addLayout(action_row)
        content_layout.addWidget(edit_box)
        self.animation_component_assignment_rows.setVisible(False)
        content_layout.addWidget(self.animation_component_assignment_rows)
        content_layout.addStretch(1)

        scroll = QScrollArea()
        scroll.setObjectName("componentScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(content)
        self.animation_component_scroll = scroll

        group_layout = QVBoxLayout(self.animation_component_group)
        group_layout.setContentsMargins(0, 4, 0, 0)
        group_layout.setSpacing(6)
        group_layout.addWidget(scroll, 1)
        # 確定とファイル操作はスクロールの外に固定し、常に押せる位置に置く。
        group_layout.addWidget(self.animation_component_confirm_button)
        file_row = QHBoxLayout()
        file_row.setSpacing(6)
        file_row.addWidget(self.animation_component_load_button)
        file_row.addWidget(self.animation_component_save_button)
        group_layout.addLayout(file_row)
        self.animation_component_group.setMinimumWidth(280)
        self.animation_component_group.setVisible(False)
        return self.animation_component_group

    def _build_settings_area(self) -> QScrollArea:
        """下段: 用途別の設定を4タブに分け、横幅を使って折り返さずに見せる。"""
        self.settings_tabs = QTabWidget()
        self.settings_tabs.setObjectName("settingsTabs")
        self.settings_tabs.setDocumentMode(True)
        self.settings_tabs.addTab(self._build_basic_settings_page(), "基本")
        self.settings_tabs.addTab(self._build_split_settings_page(), "分割")
        self.settings_tabs.addTab(self._build_placement_settings_page(), "配置・原点")
        self.settings_tabs.addTab(self._build_palette_settings_page(), "palette・保存先")

        scroll = QScrollArea()
        scroll.setObjectName("animationControlsScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(self.settings_tabs)
        scroll.setMinimumHeight(110)
        self.animation_controls_scroll = scroll
        return scroll

    @staticmethod
    def _settings_page(columns: list[list[object]]) -> QWidget:
        """設定タブ1枚を、横に並ぶ複数のフォーム列として組む。

        各項目は (ラベル, 入力) のタプル、または行全体を占める単独ウィジェット。
        """
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(8, 10, 8, 10)
        layout.setSpacing(16)
        for column in columns:
            form = QFormLayout()
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            form.setHorizontalSpacing(10)
            form.setVerticalSpacing(8)
            for entry in column:
                if isinstance(entry, tuple):
                    MainWindow._constrain_field_width(entry[1])
                    form.addRow(entry[0], entry[1])
                else:
                    form.addRow(entry)
            holder = QWidget()
            holder.setLayout(form)
            layout.addWidget(holder, 1)
        return page

    @staticmethod
    def _constrain_field_width(field: QWidget) -> None:
        """数値入力と選択肢が列幅いっぱいに間延びしないよう上限を与える。"""
        if isinstance(field, (NoWheelSpinBox, NoWheelDoubleSpinBox)):
            field.setMaximumWidth(120)
        elif isinstance(field, NoWheelComboBox):
            field.setMaximumWidth(320)

    def _build_basic_settings_page(self) -> QWidget:
        """用途に関わらず必ず効く設定と、現在の自動最適化の要約。"""
        auto_profile_caption = QLabel("この設定での出力")
        auto_profile_caption.setObjectName("subsectionTitle")
        custom_row = QWidget()
        custom_layout = QHBoxLayout(custom_row)
        custom_layout.setContentsMargins(0, 0, 0, 0)
        custom_layout.setSpacing(4)
        custom_layout.addWidget(self.canvas_width)
        custom_layout.addWidget(QLabel("×"))
        custom_layout.addWidget(self.canvas_height)
        custom_layout.addStretch(1)
        self.canvas_custom_row = custom_row

        return self._settings_page(
            [
                [
                    (self.canvas_size_label, self.canvas_size),
                    (self.canvas_custom_label, custom_row),
                    (self.background_mode_label, self.background_mode),
                    (self.background_color_label, self.background_color_field),
                    (self.composition_mode_label, self.composition_mode),
                    (self.pixelization_mode_label, self.pixelization_mode),
                    (self.palette_label, self.palette),
                    (self.repeat_opt_label, self.repeat_opt),
                ],
                [auto_profile_caption, self.auto_profile, self.canvas_effective_note],
            ]
        )

    def _build_split_settings_page(self) -> QWidget:
        """アニメーションSheetをコマへ割る設定。"""
        self.animation_split_hint = QLabel(
            "分割設定は「キャラクターアニメーション」でのみ使います。\n"
            "パーツ分割を選ぶと、右側のパーツ割り当てパネルでコマの割り当てを直せます。"
        )
        self.animation_split_hint.setObjectName("mutedText")
        self.animation_split_hint.setWordWrap(True)
        return self._settings_page(
            [
                [
                    (self.animation_split_mode_label, self.animation_split_mode),
                    (self.animation_columns_label, self.animation_columns),
                    (self.animation_rows_label, self.animation_rows),
                ],
                [self.animation_split_hint],
            ]
        )

    def _build_placement_settings_page(self) -> QWidget:
        """出力Canvasへの置き方と、ソース・出力それぞれの原点。"""
        self.animation_source_origin_row = self._build_origin_row(
            self.animation_source_origin_set,
            self.animation_source_origin_x,
            self.animation_source_origin_y,
            self.animation_source_origin_pick_button,
        )
        self.animation_output_origin_row = self._build_origin_row(
            self.animation_output_origin_set,
            self.animation_output_origin_x,
            self.animation_output_origin_y,
            self.animation_output_origin_pick_button,
        )

        self.animation_origin_group = QGroupBox("原点")
        origin_layout = QVBoxLayout(self.animation_origin_group)
        origin_layout.setContentsMargins(0, 4, 0, 0)
        origin_layout.setSpacing(4)
        origin_layout.addWidget(self.animation_source_origin_label)
        origin_layout.addWidget(self.animation_source_origin_row)
        origin_layout.addWidget(self.animation_output_origin_label)
        origin_layout.addWidget(self.animation_output_origin_row)
        origin_layout.addStretch(1)

        return self._settings_page(
            [
                [
                    (self.animation_placement_mode_label, self.animation_placement_mode),
                    (self.animation_width_label, self.animation_width),
                    (self.animation_height_label, self.animation_height),
                    (self.animation_scale_mode_label, self.animation_scale_mode),
                    (self.animation_scale_label, self.animation_scale),
                ],
                [self.animation_origin_group],
            ]
        )

    @staticmethod
    def _build_origin_row(
        enable_box: QCheckBox,
        x_spin: QWidget,
        y_spin: QWidget,
        pick_button: QPushButton,
    ) -> QWidget:
        """「指定 x , y  画像から指定」を1行にまとめた原点入力。"""
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(enable_box)
        layout.addWidget(x_spin)
        layout.addWidget(QLabel(","))
        layout.addWidget(y_spin)
        layout.addWidget(pick_button)
        layout.addStretch(1)
        return row

    def _build_palette_settings_page(self) -> QWidget:
        """色数と基準palette、そして書き出し先。"""
        output_row = QWidget()
        output_row_layout = QHBoxLayout(output_row)
        output_row_layout.setContentsMargins(0, 0, 0, 0)
        output_row_layout.setSpacing(6)
        output_row_layout.addWidget(self.output_root_field, 1)
        output_row_layout.addWidget(self.output_browse_button)

        self.shared_palette_group = QGroupBox("地形の基準palette")
        shared_palette_layout = QVBoxLayout(self.shared_palette_group)
        shared_palette_layout.setContentsMargins(0, 4, 0, 0)
        shared_palette_layout.setSpacing(6)
        shared_palette_layout.addWidget(self.shared_palette_info)
        shared_palette_layout.addWidget(self.shared_palette_view)
        shared_palette_buttons = QHBoxLayout()
        shared_palette_buttons.setSpacing(6)
        shared_palette_buttons.addWidget(self.shared_palette_load_button)
        shared_palette_buttons.addWidget(self.shared_palette_clear_button)
        shared_palette_layout.addLayout(shared_palette_buttons)
        self.shared_palette_group.setToolTip(
            "地形のfinal.pngから実測したRGBだけを使います。未設定なら従来の自動paletteです。"
        )

        return self._settings_page(
            [
                [
                    (self.animation_palette_label, self.animation_palette),
                    (self.animation_shared_palette_label, self.animation_shared_palette),
                    ("保存先", output_row),
                ],
                [self.shared_palette_group],
            ]
        )

    def _build_action_bar(self) -> QWidget:
        """実行操作と、直近の結果・状態をまとめた最下段。"""
        self.metrics.setWordWrap(True)
        self.status.setWordWrap(True)
        self.status.setObjectName("statusText")
        panel = QFrame()
        panel.setObjectName("actionPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self.compile_button)
        row.addWidget(self.compile_progress, 1)
        row.addWidget(self.animation_play_button)
        row.addWidget(self.animation_playback_label)
        row.addWidget(self.terrain_batch_button)
        layout.addLayout(row)
        layout.addWidget(self.metrics)
        layout.addWidget(self.status)
        return panel

    def _update_settings_tab_availability(self) -> None:
        """用途に対して意味のない設定タブを無効化し、選択中なら基本へ戻す。"""
        is_animation = self.purpose.currentData() == "character_animation"
        for index in (self.SPLIT_TAB_INDEX, self.PLACEMENT_TAB_INDEX):
            self.settings_tabs.setTabEnabled(index, is_animation)
        if not self.settings_tabs.isTabEnabled(self.settings_tabs.currentIndex()):
            self.settings_tabs.setCurrentIndex(0)

    def _update_purpose_controls(self, *_args: object, clear_result: bool = True) -> None:
        purpose = self.purpose.currentData()
        is_character = purpose in {"character", "character_animation"}
        is_animation = purpose == "character_animation"
        is_motion = is_animation and self.animation_placement_mode.currentData() == "preserve_motion"
        self._update_canvas_controls()
        # 移動を保持する配置では「配置・原点」タブの幅・高さが実際の出力を決める。
        self.canvas_size.setEnabled(is_character and not is_motion)
        self.canvas_size.setToolTip(
            "移動を保持する配置では、「配置・原点」タブの出力Canvas幅・高さが使われます"
            if is_motion
            else "出力する論理ドットの一辺"
        )
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
        sheet_title = "アニメーションシート" if is_animation else "タイル繰り返し確認"
        self.secondary_preview_label.setText(sheet_title)
        self.preview_tabs.setTabText(self.sheet_tab_index, sheet_title)
        if not is_animation:
            self._stop_animation_playback()
        self._update_settings_tab_availability()
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

    def _update_canvas_controls(self, *_args: object) -> None:
        """出力Canvasと背景・構図まわりの表示を、現在の選択に合わせる。"""
        is_character = self.purpose.currentData() in {"character", "character_animation"}
        is_custom_canvas = is_character and self.canvas_size.currentData() is None
        is_full_frame = self.composition_mode.currentData() == "pre_aligned"
        width, height = self.selected_canvas_size()
        self.canvas_size_label.setVisible(is_character)
        self.canvas_size.setVisible(is_character)
        self.canvas_custom_label.setVisible(is_custom_canvas)
        self.canvas_custom_row.setVisible(is_custom_canvas)
        self.background_mode_label.setVisible(is_character)
        self.background_mode.setVisible(is_character)
        show_background_color = is_character and self.background_mode.currentData() == "color"
        self.background_color_label.setVisible(show_background_color)
        self.background_color_field.setVisible(show_background_color)
        self.composition_mode_label.setVisible(is_character)
        self.composition_mode.setVisible(is_character)
        # 非正方は短辺が実効解像度を決める。画面全体構図では余白が出ないので出さない。
        self.canvas_effective_note.setVisible(
            is_character and not is_full_frame and width != height
        )

    def _on_terrain_setting_changed(self, _value: object = None) -> None:
        if self.purpose.currentData() != "terrain":
            return
        self._update_purpose_controls()

    def _update_canvas_selection(self) -> None:
        purpose = self.purpose.currentData()
        if purpose not in {"character", "character_animation"}:
            return
        canvas_size = self.selected_canvas_size()
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
                scale_label = "自動" if self.animation_scale_mode.currentData() == "auto" else f"固定{self.animation_scale.value():g}倍"
                self.auto_profile.setText(
                    f"移動を保持 / {canvas_size[0]}×{canvas_size[1]} / {columns}列×{rows}行 / {self.animation_palette.value()}色 / {scale_label}"
                )
                self._clear_stale_result(canvas_size)
                return
            columns = self.animation_columns.value()
            rows = self.animation_rows.value()
            profile = resolve_character_animation_gui_profile(canvas_size, frame_count=columns * rows)
            self.canvas.set_canvas_size(profile.canvas_size)
            self.canvas.set_guide_point(None)
            split_mode = self.animation_split_mode.currentData()
            if split_mode in {"fixed_grid", "row_alpha_gap"}:
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
        show_grid = is_animation and split_mode in {"fixed_grid", "row_alpha_gap", "row_alpha_components", "hybrid"}
        show_components = is_animation and split_mode in {"row_alpha_components", "hybrid"}
        for widget in (self.animation_split_mode_label, self.animation_split_mode):
            widget.setVisible(is_animation)
        for widget in (
            self.animation_columns_label,
            self.animation_columns,
            self.animation_rows_label,
            self.animation_rows,
        ):
            widget.setVisible(show_grid)
        self.animation_component_group.setVisible(show_components)
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


    def _refresh_component_preview_cards(self, result: ComponentSplitResult) -> None:
        """F1〜Fnのコマ抽出プレビューカードを更新する。"""
        layout = self.animation_frame_cards_layout
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        frame_count = self.animation_columns.value() * self.animation_rows.value()
        self.animation_component_target_combo.clear()
        for i in range(frame_count):
            self.animation_component_target_combo.addItem(f"F{i + 1}", userData=f"F{i + 1}")

        suspicious_cids = {area.component_id for area in result.suspicious_components}

        for i in range(frame_count):
            frame_id = f"F{i + 1}"
            card = QFrame()
            card.setFrameShape(QFrame.Shape.StyledPanel)
            card.setStyleSheet(
                "QFrame { background: #1e2228; border: 1px solid #444c56; border-radius: 6px; padding: 4px; } "
                "QFrame:hover { border: 1px solid #77a9ff; }"
            )
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(4, 4, 4, 4)
            card_layout.setSpacing(2)

            title = QLabel(frame_id)
            title.setAlignment(Qt.AlignmentFlag.AlignCenter)
            title.setStyleSheet("font-weight: bold; color: #f0c674;")
            card_layout.addWidget(title)

            thumb_label = QLabel()
            thumb_label.setFixedSize(64, 64)
            thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            thumb_label.setStyleSheet("background: #2d333b; border-radius: 4px;")

            if i < len(result.frames):
                frame_img = result.frames[i]
                rgba = frame_img.convert("RGBA")
                qimg = QImage(rgba.tobytes(), rgba.width, rgba.height, rgba.width * 4, QImage.Format.Format_RGBA8888)
                pix = QPixmap.fromImage(qimg).scaled(60, 60, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.FastTransformation)
                thumb_label.setPixmap(pix)
            card_layout.addWidget(thumb_label, 0, Qt.AlignmentFlag.AlignCenter)

            frame_comps = [c for c in result.components if c.frame_id == frame_id]
            px_count = sum(c.area for c in frame_comps)
            px_label = QLabel(f"{px_count:,} px")
            px_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            px_label.setStyleSheet("font-size: 11px; color: #adbac7;")
            card_layout.addWidget(px_label)

            has_suspicious = any(c.component_id in suspicious_cids for c in frame_comps)
            if has_suspicious:
                warn_label = QLabel("⚠️ 確認推奨")
                warn_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                warn_label.setStyleSheet("font-size: 10px; color: #e3b341; font-weight: bold;")
                card_layout.addWidget(warn_label)

            card.mousePressEvent = lambda _event, fid=frame_id: self._on_frame_card_clicked(fid)
            layout.addWidget(card)

    def _on_frame_card_clicked(self, frame_id: str) -> None:
        """コマカードがクリックされたら、そのコマに属する全パーツをハイライトする。"""
        if self._component_analysis is None:
            return
        cids = [c.component_id for c in self._component_analysis.components if c.frame_id == frame_id]
        self._select_components(cids)
        self.animation_component_target_combo.setCurrentIndex(
            self.animation_component_target_combo.findData(frame_id)
        )

    def _on_source_preview_clicked(self, point: object) -> None:
        """元絵プレビューがクリックされたときのハンドラ（原点指定またはパーツ選択）。"""
        if self._origin_pick_target == "source":
            self._on_source_origin_clicked(point)
            return

        if self._component_analysis is None or self.source_path is None:
            return

        x, y = int(point[0]), int(point[1])
        labels = self._component_analysis.labels
        h, w = labels.shape
        if not (0 <= y < h and 0 <= x < w):
            return

        label = int(labels[y, x])
        if label == 0:
            for r in range(1, 5):
                y0, y1 = max(0, y - r), min(h, y + r + 1)
                x0, x1 = max(0, x - r), min(w, x + r + 1)
                patch = labels[y0:y1, x0:x1]
                nonzeros = patch[patch > 0]
                if nonzeros.size > 0:
                    label = int(nonzeros[0])
                    break

        if label > 0:
            self._select_components([label])
        else:
            self._select_components([])

    def _on_source_preview_rect_selected(self, rect: object) -> None:
        """元絵プレビューで矩形ドラッグ選択されたときのハンドラ。"""
        if self._component_analysis is None:
            return

        x0, y0, x1, y1 = rect  # type: ignore[misc]
        matched_cids: list[int] = []
        for comp in self._component_analysis.components:
            cx0, cy0, cx1, cy1 = comp.bbox
            if not (cx1 <= x0 or cx0 >= x1 or cy1 <= y0 or cy0 >= y1):
                matched_cids.append(comp.component_id)

        self._select_components(matched_cids)

    def _select_components(self, comp_ids: list[int]) -> None:
        """指定されたパーツID群を選択状態にし、ハイライトと編集パネルを更新する。"""
        self._selected_component_ids = comp_ids
        if not comp_ids or self._component_analysis is None:
            self.source_preview.set_highlight_boxes([])
            self.animation_component_selection_info.setText("元絵をクリックまたはドラッグしてパーツを選択できます")
            return

        comp_dict = {c.component_id: c for c in self._component_analysis.components}
        selected_comps = [comp_dict[cid] for cid in comp_ids if cid in comp_dict]
        boxes = [c.bbox for c in selected_comps]
        self.source_preview.set_highlight_boxes(boxes)

        total_px = sum(c.area for c in selected_comps)
        if len(selected_comps) == 1:
            c = selected_comps[0]
            current_frame = self._component_assignment_overrides.get(c.component_id, c.frame_id or "未割当")
            cand_text = "、".join(c.candidate_frame_ids) if c.candidate_frame_ids else "なし"
            row_num = (c.row + 1) if c.row is not None else 1
            self.animation_component_selection_info.setText(
                f"選択中: C{c.component_id} (行{row_num}, {c.area}px, bbox={c.bbox})\n"
                f"現在: {current_frame} (候補: {cand_text})"
            )
            if current_frame and current_frame != "未割当":
                idx = self.animation_component_target_combo.findData(current_frame)
                if idx >= 0:
                    self.animation_component_target_combo.setCurrentIndex(idx)
        else:
            self.animation_component_selection_info.setText(
                f"選択中: {len(selected_comps)}個のパーツ (合計画素: {total_px:,} px)\n"
                f"まとめて割り当て先Fnを変更できます"
            )

    def reassign_selected_components(self) -> None:
        """選択されているパーツの割り当て先を変更し、再解析プレビューする。"""
        if not self._selected_component_ids:
            self.status.setText("先に元絵上でパーツを選択してください")
            return

        target_frame = self.animation_component_target_combo.currentData()
        if not target_frame:
            return

        for cid in self._selected_component_ids:
            self._component_assignment_overrides[cid] = str(target_frame)
            if cid in self._component_assignment_combos:
                combo = self._component_assignment_combos[cid]
                combo.setCurrentIndex(combo.findData(target_frame))

        self._component_assignment_confirmed = False
        self.analyze_component_assignments()
        self.status.setText(f"選択したパーツの割り当てを {target_frame} に変更しました。内容を確認後、確定してください。")

    def reset_component_overrides(self) -> None:
        """パーツ割り当ての手動変更をリセットする。"""
        self._component_assignment_overrides.clear()
        self._component_assignment_confirmed = False
        for combo in self._component_assignment_combos.values():
            combo.setCurrentIndex(0)
        self.analyze_component_assignments()
        self.status.setText("パーツ割り当ての手動変更をリセットしました")

    def focus_next_suspicious(self) -> None:
        """次の要確認パーツにフォーカスする。"""
        if self._component_analysis is None or not self._component_analysis.suspicious_components:
            self.status.setText("要確認の領域はありません")
            return

        areas = self._component_analysis.suspicious_components
        self._suspicious_index = (self._suspicious_index) % len(areas)
        area = areas[self._suspicious_index]
        self._suspicious_index += 1

        self._select_components([area.component_id])
        reason = area.suspicious_reason or "境界近傍"
        self.status.setText(f"要確認パーツ C{area.component_id} を選択しました ({reason})")

    def _set_component_assignment_rows(self, result: ComponentSplitResult) -> None:
        """テスト互換用: _component_assignment_combos および animation_component_assignment_rows を構築する。"""
        layout = self.animation_component_assignment_rows.layout()
        if layout is None:
            layout = QVBoxLayout(self.animation_component_assignment_rows)
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        self._component_assignment_combos.clear()
        frame_count = self.animation_columns.value() * self.animation_rows.value()
        for comp in result.components:
            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            candidate_text = (
                "候補: " + "、".join(comp.candidate_frame_ids)
                if comp.candidate_frame_ids
                else "候補なし"
            )
            if len(comp.candidate_frame_ids) > 1:
                candidate_text += "（同率のため未指定）"
            label = QLabel(f"C{comp.component_id} {comp.bbox} / {candidate_text}")
            row_layout.addWidget(label)

            combo = NoWheelComboBox()
            combo.addItem("未指定", userData=None)
            for f_idx in range(frame_count):
                combo.addItem(f"F{f_idx + 1}", userData=f"F{f_idx + 1}")
            assigned = self._component_assignment_overrides.get(comp.component_id, None)
            if assigned is None:
                if len(comp.candidate_frame_ids) == 1:
                    assigned = comp.candidate_frame_ids[0]
                elif len(comp.candidate_frame_ids) == 0:
                    assigned = comp.frame_id
                else:
                    assigned = None
            if assigned:
                combo.setCurrentIndex(combo.findData(assigned))
            combo.currentIndexChanged.connect(
                lambda _idx, cid=comp.component_id, cb=combo: self._on_compat_combo_changed(cid, cb)
            )
            row_layout.addWidget(combo)
            layout.addWidget(row)
            self._component_assignment_combos[comp.component_id] = combo

    def _on_compat_combo_changed(self, comp_id: int, combo: QComboBox) -> None:
        val = combo.currentData()
        if val is not None:
            self._component_assignment_overrides[comp_id] = str(val)
        else:
            self._component_assignment_overrides.pop(comp_id, None)
        self._mark_configuration_changed()
        self._component_assignment_confirmed = False
        self._source_origin_frame_box = None
        self._source_origin_frame_index = None
        self._source_origin_frame_logical_origin = None
        self._source_origin_frame_signature = None
        self.animation_source_origin_set.setChecked(False)

        if self._component_analysis is not None and self.source_path is not None:
            components = tuple(
                replace(
                    c,
                    frame_id=self._component_assignment_overrides.get(c.component_id, c.frame_id),
                )
                for c in self._component_analysis.components
            )
            try:
                with Image.open(self.source_path) as opened:
                    overlay = render_component_split_overlay(opened, self._component_analysis.labels, components)
            except OSError:
                overlay = self._component_analysis.overlay.copy()

            self._component_analysis = replace(
                self._component_analysis,
                status="needs_assignment",
                reason="割り当てを変更しました。再度割り当てを確定してください。",
                components=components,
                overlay=overlay,
            )
            self.source_preview.set_pil_image(overlay)

        self.animation_component_assignment_status.setText(
            "割り当てを変更しました。再度割り当てを確定してください。"
        )
        self._update_animation_geometry_controls()

    def _animation_split_signature(self) -> tuple[object, ...]:
        return (
            self.source_path,
            self.animation_split_mode.currentData(),
            self.animation_columns.value(),
            self.animation_rows.value(),
            tuple(sorted(self._component_assignment_overrides.items())),
        )

    def _component_assignment_config(self) -> CharacterAnimationConfig:
        split_mode = self.animation_split_mode.currentData()
        if split_mode not in {"row_alpha_components", "hybrid"}:
            split_mode = "row_alpha_components"
        return CharacterAnimationConfig(
            frame_count=self.animation_columns.value() * self.animation_rows.value(),
            split_mode=split_mode,
            grid_columns=self.animation_columns.value(),
            grid_rows=self.animation_rows.value(),
        )

    def _apply_component_analysis(self, result: ComponentSplitResult, *, confirmed: bool = False) -> None:
        self._component_analysis = result
        self._component_analysis_signature = self._animation_split_signature()
        self._component_assignment_confirmed = confirmed
        self.source_preview.set_pil_image(result.overlay)
        self._set_component_assignment_rows(result)
        self._refresh_component_preview_cards(result)
        suspicious_boxes = [area.bbox for area in result.suspicious_components]
        self.source_preview.set_suspicious_boxes(suspicious_boxes)
        self._update_animation_split_controls()

        if confirmed:
            self.animation_component_assignment_status.setText(
                "全コマの割り当てを確定しました。コンパイル可能です。"
            )
        elif self._component_assignment_overrides:
            self.animation_component_assignment_status.setText(
                "割り当てを変更しました。再度割り当てを確定してください。"
            )
        elif result.status == "resolved":
            self.animation_component_assignment_status.setText(
                "パーツをコマへ分けました。確定するとコンパイルできます。"
            )
        else:
            self.animation_component_assignment_status.setText(result.reason)

    def analyze_component_assignments(
        self,
        *,
        cells_override: Sequence[Mapping[str, object] | ComponentCell] | None = None,
        sync: bool = False,
    ) -> bool:
        if self.source_path is None:
            self.status.setText("先に元絵を読み込んでください")
            self._component_analysis_signature = None
            return False

        config = self._component_assignment_config()
        source_path = self.source_path
        overrides = dict(self._component_assignment_overrides)
        revision = self._configuration_revision

        def _do_analyze() -> ComponentSplitResult:
            with Image.open(source_path) as opened:
                columns = config.grid_columns or config.frame_count
                rows = config.grid_rows or 1
                return analyze_component_split(
                    opened,
                    columns=columns,
                    rows=rows,
                    alpha_threshold=config.alpha_threshold,
                    remove_small_components=config.remove_isolated_components,
                    min_component_area_px=config.min_component_area_px,
                    empty_column_threshold=config.empty_column_threshold,
                    empty_row_threshold=config.empty_row_threshold,
                    min_gutter_width_px=config.min_gutter_width_px,
                    assignments=overrides or None,
                    cells_override=cells_override,
                )

        if sync:
            try:
                result = _do_analyze()
                self._apply_component_analysis(result)
                self.status.setText("パーツを解析し、コマ抽出プレビューを更新しました")
                return True
            except (OSError, ValueError) as exc:
                self._component_analysis = None
                self._component_analysis_signature = None
                self.status.setText(f"パーツを解析できませんでした: {exc}")
                return False

        current_signature = (
            self._animation_split_signature(),
            tuple(
                (
                    c.get("index") if isinstance(c, Mapping) else getattr(c, "index", None),
                    c.get("visible_pixel_count") if isinstance(c, Mapping) else getattr(c, "visible_pixel_count", None),
                )
                for c in cells_override
            )
            if cells_override
            else None,
        )

        # 同一設定・同一リビジョンで既に解析が実行中なら重複起動を抑止（連打対策）
        if (
            self._analysis_thread is not None
            and self._analysis_thread.isRunning()
            and self._analysis_thread._revision == revision
            and self._analysis_thread._signature == current_signature
        ):
            return True

        self.compile_progress.setRange(0, 0)
        self.compile_progress.setFormat("解析中...")
        self.status.setText("パーツを解析中...")

        worker = _AnalysisWorker(_do_analyze, revision, current_signature)
        worker.succeeded.connect(lambda res, rev, w=worker: self._on_analysis_succeeded(res, rev, w))
        worker.failed.connect(lambda msg, rev, w=worker: self._on_analysis_failed(msg, rev, w))
        worker.finished.connect(lambda w=worker: self._on_analysis_worker_finished(w))
        self._analysis_workers.add(worker)
        self._analysis_thread = worker
        worker.start()
        return True

    def _on_analysis_succeeded(
        self, result: ComponentSplitResult, revision: int, worker: _AnalysisWorker
    ) -> None:
        if worker is not self._analysis_thread or revision != self._configuration_revision:
            return
        self.compile_progress.setRange(0, 1)
        self.compile_progress.setValue(1)
        self.compile_progress.setFormat("完了")
        self._apply_component_analysis(result)
        self.status.setText("パーツを解析し、コマ抽出プレビューを更新しました")

    def _on_analysis_failed(self, message: str, revision: int, worker: _AnalysisWorker) -> None:
        if worker is not self._analysis_thread or revision != self._configuration_revision:
            return
        self.compile_progress.setRange(0, 1)
        self.compile_progress.setValue(1)
        self.compile_progress.setFormat("失敗")
        self._component_analysis = None
        self._component_analysis_signature = None
        self.status.setText(f"パーツを解析できませんでした: {message}")

    def _has_active_workers(self) -> bool:
        """実行中の解析ワーカーまたはコンパイルスレッドが存在するか判定する。"""
        if any(w.isRunning() for w in tuple(self._analysis_workers)):
            return True
        if self._compile_thread is not None and self._compile_thread.isRunning():
            return True
        return False

    def _on_analysis_worker_finished(self, worker: _AnalysisWorker) -> None:
        self._analysis_workers.discard(worker)
        if self._analysis_thread is worker:
            self._analysis_thread = None
        worker.deleteLater()
        if self._close_pending and not self._has_active_workers():
            self.close()

    def wait_for_analysis(self, timeout_ms: int = 5000) -> bool:
        """テストや同期待機用にすべての解析ワーカーの完了を待機する。"""
        if not self._analysis_workers and self._analysis_thread is None:
            return True
        app = QApplication.instance()
        elapsed = 0
        while (bool(self._analysis_workers) or self._analysis_thread is not None) and elapsed < timeout_ms:
            if app is not None:
                app.processEvents()
            QThread.msleep(10)
            elapsed += 10
        return not bool(self._analysis_workers) and self._analysis_thread is None

    def wait_for_close(self, timeout_ms: int = 10000) -> bool:
        """終了保留中（_close_pending）の場合、ワーカー完了とウィンドウクローズ完了を待機する。"""
        if not self._close_pending and not self._has_active_workers():
            return True
        app = QApplication.instance()
        elapsed = 0
        while (self._close_pending or self._has_active_workers()) and elapsed < timeout_ms:
            if app is not None:
                app.processEvents()
            QThread.msleep(10)
            elapsed += 10
        return not self._close_pending and not self._has_active_workers()


    def confirm_component_assignments(self) -> None:
        if self.source_path is None:
            self.status.setText("先に元絵を読み込んでください")
            return
        if (
            self._component_analysis is not None
            and self._component_analysis_signature == self._animation_split_signature()
        ):
            confirmed_result = replace(
                self._component_analysis,
                status="resolved",
                is_confirmed=True,
                assignment_status="user_confirmed",
                assignment_source="user_confirmed",
                reason="",
            )
            self._apply_component_analysis(confirmed_result, confirmed=True)
            self.status.setText("全コマの割り当てを確定しました。コンパイルできます")
            return

        try:
            config = self._component_assignment_config()
            with Image.open(self.source_path) as opened:
                columns = config.grid_columns or config.frame_count
                rows = config.grid_rows or 1
                result = analyze_component_split(
                    opened,
                    columns=columns,
                    rows=rows,
                    alpha_threshold=config.alpha_threshold,
                    remove_small_components=config.remove_isolated_components,
                    min_component_area_px=config.min_component_area_px,
                    empty_column_threshold=config.empty_column_threshold,
                    empty_row_threshold=config.empty_row_threshold,
                    min_gutter_width_px=config.min_gutter_width_px,
                    assignments=self._component_assignment_overrides or None,
                    confirmed=True,
                )
            self._apply_component_analysis(result, confirmed=True)
            self.status.setText("全コマの割り当てを確定しました。コンパイルできます")
        except (OSError, ValueError) as exc:
            self.status.setText(f"パーツの割り当てを確定できませんでした: {exc}")

    def _selected_component_assignments(self) -> dict[int, str]:
        return dict(self._component_assignment_overrides)

    def save_component_assignment_file(self) -> None:
        if self.source_path is None:
            self.status.setText("先に元絵を読み込んでください")
            return
        try:
            assignments = self._selected_component_assignments()
            path, _ = QFileDialog.getSaveFileName(self, "割り当てを保存", "component_assignments.json", "JSON (*.json)")
            if not path:
                return
            cells = self._component_analysis.cells if self._component_analysis else None
            save_component_assignments(
                self.source_path,
                self._component_assignment_config(),
                assignments,
                Path(path),
                cells=cells,
            )
            self.status.setText(f"割り当てを保存しました: {Path(path).name}")
        except (OSError, ValueError) as exc:
            self.status.setText(f"割り当てを保存できませんでした: {exc}")

    def load_component_assignment_file(self) -> None:
        if self.source_path is None:
            self.status.setText("先に元絵を読み込んでください")
            return
        path, _ = QFileDialog.getOpenFileName(self, "割り当てを読み込む", "", "JSON (*.json)")
        if not path:
            return
        try:
            config = self._component_assignment_config()
            data = load_component_assignment_data(self.source_path, config, Path(path))
            self._component_assignment_overrides.clear()
            self._component_assignment_overrides.update(data.assignments)
            self.analyze_component_assignments(cells_override=data.cells, sync=True)
            self.confirm_component_assignments()
            self.status.setText(f"割り当てを読み込みました: {Path(path).name}")
        except (OSError, ValueError) as exc:
            self.status.setText(f"割り当てを読み込めませんでした: {exc}")

    def _source_frame_coordinates(
        self,
    ) -> tuple[tuple[tuple[int, int, int, int], tuple[int, int]], ...]:
        """現在の分割設定でsource矩形と論理原点を取得する。"""
        if self.source_path is None:
            return ()
        if (
            self.animation_split_mode.currentData() in {"row_alpha_components", "hybrid"}
            and self._component_analysis is not None
            and self._component_analysis_signature == self._animation_split_signature()
            and self._component_analysis.status == "resolved"
        ):
            return tuple(
                (cell.source_box, cell.logical_origin)
                for cell in self._component_analysis.cells
            )
        columns = self.animation_columns.value()
        rows = self.animation_rows.value()
        config = CharacterAnimationConfig(
            frame_count=columns * rows,
            split_mode=self.animation_split_mode.currentData(),  # type: ignore[arg-type]
            grid_columns=columns,
            grid_rows=rows,
        )
        with Image.open(self.source_path) as opened:
            return animation_source_frame_coordinates(opened, config)

    def _source_origin_display_point(self) -> tuple[int, int] | None:
        """フレーム内原点を元絵プレビュー上の座標へ戻す。"""
        if not self.animation_source_origin_set.isChecked():
            return None
        box = self._source_origin_frame_box
        logical_origin = self._source_origin_frame_logical_origin
        if (
            box is None
            or logical_origin is None
            or self._source_origin_frame_signature != self._animation_split_signature()
        ):
            try:
                coordinates = self._source_frame_coordinates()
                frame_index = self._source_origin_frame_index or 0
                box, logical_origin = coordinates[frame_index]
            except (OSError, ValueError, IndexError):
                box = None
                logical_origin = None
        local = (self.animation_source_origin_x.value(), self.animation_source_origin_y.value())
        if box is None:
            return local
        origin = logical_origin or (box[0], box[1])
        return (origin[0] + local[0], origin[1] + local[1])

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
            self.animation_origin_group,
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
        self.status.setText("元絵プレビュー上で元絵の原点をクリックしてください")

    def start_output_origin_pick(self) -> None:
        self._origin_pick_target = "output"
        self.status.setText("ドットプレビュー上で出力原点をクリックしてください")

    def _on_source_origin_clicked(self, point: object) -> None:
        if self._origin_pick_target != "source":
            return
        if (
            self.animation_split_mode.currentData() in {"row_alpha_components", "hybrid"}
            and (
                self._component_analysis is None
                or self._component_analysis.status != "resolved"
                or not self._component_assignment_confirmed
                or self._component_analysis_signature != self._animation_split_signature()
            )
        ):
            self.status.setText("パーツの割り当てが未確定です。割り当てを確定してから原点を指定してください")
            return
        x, y = point  # type: ignore[misc]
        try:
            frame_coordinates = self._source_frame_coordinates()
            frame_boxes = tuple(box for box, _origin in frame_coordinates)
            logical_origins = tuple(origin for _box, origin in frame_coordinates)
            owner_labels = (
                self._component_analysis.owner_labels
                if self.animation_split_mode.currentData() in {"row_alpha_components", "hybrid"}
                and self._component_analysis is not None
                and self._component_analysis.status == "resolved"
                and self._component_analysis_signature == self._animation_split_signature()
                else None
            )
            selected_frame_index = (
                int(self.animation_component_selected_frame.currentData())
                if owner_labels is not None and self.animation_component_selected_frame.currentData() is not None
                else None
            )
            frame_index, logical_point = map_source_point_to_frame(
                (int(x), int(y)),
                frame_boxes,
                logical_origins=logical_origins,
                owner_labels=owner_labels,
                selected_frame_index=selected_frame_index,
            )
        except (OSError, ValueError) as exc:
            self.status.setText(f"元絵の原点を指定できませんでした: {exc}")
            return
        self._source_origin_frame_box = frame_boxes[frame_index]
        self._source_origin_frame_index = frame_index
        self._source_origin_frame_logical_origin = logical_origins[frame_index]
        self._source_origin_frame_signature = self._animation_split_signature()
        self.animation_source_origin_set.setChecked(True)
        self.animation_source_origin_x.setValue(logical_point[0])
        self.animation_source_origin_y.setValue(logical_point[1])
        self._origin_pick_target = None
        self._update_animation_geometry_controls()
        self.status.setText(
            f"F{frame_index + 1}の元絵の原点を指定しました: ({logical_point[0]}, {logical_point[1]})"
        )

    def _on_output_origin_clicked(self, point: object) -> None:
        if self._origin_pick_target != "output":
            return
        x, y = point  # type: ignore[misc]
        self.animation_output_origin_set.setChecked(True)
        self.animation_output_origin_x.setValue(int(x))
        self.animation_output_origin_y.setValue(int(y))
        self._origin_pick_target = None
        self.status.setText(f"出力Canvasの原点を指定しました: ({int(x)}, {int(y)})")

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

    def show_output_palette(self, final_paths) -> None:  # type: ignore[no-untyped-def]
        """出力PNGから実測したRGBを色見本として表示する。複数コマは和集合を取る。"""
        measured: set[tuple[int, int, int]] = set()
        for path in final_paths:
            try:
                measured.update(extract_final_palette(path))
            except (OSError, ValueError):
                continue
        colors = tuple(sorted(measured))
        self.output_palette_view.set_colors(colors)
        if not colors:
            self.output_palette_info.setText("コンパイルすると、使用したpaletteをここに表示します")
            return
        identity = f" / palette ID {palette_id(colors)[:12]}" if len(colors) <= 64 else ""
        self.output_palette_info.setText(f"実測 {len(colors)}色{identity}")

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
        self.show_output_palette(())
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
        source_label: str = "基準palette",
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
        self.status.setText(f"{source_label}をキャラクターの基準paletteに設定しました")

    def clear_shared_palette(self) -> None:
        """キャラクターpaletteの自動選択へ戻す。"""
        self._mark_configuration_changed()
        self._shared_palette_colors = ()
        self.shared_palette_info.setText("未設定（地形タイルの基準paletteを反映できます）")
        self.shared_palette_view.clear()
        self._clear_stale_result()
        self.status.setText("キャラクターの基準paletteを解除しました")

    def load_shared_palette(self) -> None:
        """terrain batchが出力したpalette.jsonを読み込む。"""
        path, _ = QFileDialog.getOpenFileName(self, "基準paletteを読み込む", "", "palette.json (*.json)")
        if not path:
            return
        try:
            self.set_shared_palette(load_palette_json(Path(path)), source_label=Path(path).name)
        except (OSError, ValueError) as exc:
            self.status.setText(f"基準paletteを読み込めませんでした: {exc}")

    def _set_shared_palette_view(self, colors) -> None:  # type: ignore[no-untyped-def]
        self.shared_palette_view.set_colors(colors)

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
        self._source_origin_frame_index = None
        self._source_origin_frame_logical_origin = None
        self._source_origin_frame_signature = None
        self._component_analysis = None
        self._component_analysis_signature = None
        self._component_assignment_confirmed = False
        self._component_assignment_overrides.clear()
        self._selected_component_ids.clear()
        self._component_assignment_combos.clear()
        self.source_preview.clear_boxes()
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
            self.show_output_palette(animation.final_frame_paths)
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
                completion_message = "完了: 明示原点・共通倍率で移動を保持したアニメーションを出力しました"
            else:
                completion_message = "完了: 分割・共通bbox・足元をそろえてアニメーションを出力しました"
            if animation.warnings:
                self.status.setText(f"警告付きで{completion_message}: {' / '.join(animation.warnings)}")
            else:
                self.status.setText(completion_message)
            return

        compilation = cast(CompilationResult, result)
        width, height = cast(tuple[int, int], context["canvas_size"])
        output = cast(Path, context["output"])
        purpose = cast(str, context["purpose"])
        self.canvas.set_image(compilation.final_path, (width, height))
        self._compiled_canvas_size = (width, height)
        self.result_preview.set_image(compilation.final_path)
        self.show_output_palette((compilation.final_path,))
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
        if self._close_pending and not self._has_active_workers():
            self.close()

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
                        raise ValueError("移動を保持する配置では、元絵とドットプレビューの両方で原点を指定してください")
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
                        self.selected_canvas_size(),
                        frame_count=columns * rows,
                    )
                    width, height = profile.canvas_size
                    fit_within = profile.fit_within
                    bottom_margin = profile.bottom_margin
                    source_origin = None
                    output_origin = None
                    scale_override = None
                    shared_palette_enabled = bool(self.animation_shared_palette.currentData())
                shared_palette = self._shared_palette_colors or None
                shared_palette_enabled = shared_palette_enabled or shared_palette is not None
                output = build_output_path(
                    output_root,
                    source,
                    purpose="character_animation",
                    canvas_size=(width, height),
                    palette_token=palette_id(shared_palette) if shared_palette is not None else None,
                )
                component_assignments = None
                cells_override = None
                split_mode = self.animation_split_mode.currentData()
                if split_mode in {"row_alpha_components", "hybrid"}:
                    current_signature = self._animation_split_signature()
                    if self._component_analysis_signature != current_signature:
                        self.analyze_component_assignments(sync=True)
                    if self._component_analysis is not None:
                        if self._component_analysis.status != "resolved" or not self._component_assignment_confirmed:
                            self.status.setText(
                                "未解決のパーツがあります。色分けを確認して割り当てを確定してください。"
                            )
                            return
                        component_assignments = self._selected_component_assignments() or None
                        cells_override = self._component_analysis.cells
                    elif split_mode == "row_alpha_components":
                        return

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
                confirm_comp = self._component_assignment_confirmed
                operation = lambda: compile_character_animation_sheet(
                    source,
                    output,
                    config=config,
                    palette_budget=palette_budget,
                    palette_colors=shared_palette,
                    character_detail_level="balanced",
                    debug_enabled=True,
                    component_assignments=component_assignments,
                    cells_override=cells_override,
                    confirm_components=confirm_comp,
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
                profile = resolve_character_gui_profile(self.selected_canvas_size())
                width, height = profile.canvas_size
                shared_palette = self._shared_palette_colors or None
                output = build_output_path(
                    output_root,
                    source,
                    purpose="character",
                    canvas_size=(width, height),
                    palette_token=palette_id(shared_palette) if shared_palette is not None else None,
                )
                background_mode = self.background_mode.currentData()
                config = compiler_config_for_purpose(
                    "character",
                    output_root=output,
                    canvas=CanvasSpec(width, height),
                    palette_budget=max(profile.palette_budget, len(shared_palette or ())),
                    palette_colors=shared_palette,
                    character_detail_level=profile.detail_level,  # type: ignore[arg-type]
                    background_mode=background_mode,
                    background_color=(
                        self.background_color_field.text().strip()
                        if background_mode == "color"
                        else None
                    ),
                    character_input_mode=self.composition_mode.currentData(),
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
        if self._has_active_workers():
            self._close_pending = True
            self.setEnabled(False)
            self.status.setText("処理の完了を待って終了しています...")
            event.ignore()
            return
        self._close_pending = False
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
