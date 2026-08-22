"""Minimal but usable PySide6 GUI for the MVP."""

from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from pixel_tile_compiler.config import CompilerConfig
from pixel_tile_compiler.gui.canvas import CanvasState
from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler


def _pixmap(path: Path, scale: int = 1) -> QPixmap:
    image = QImage(str(path)).convertToFormat(QImage.Format.Format_RGBA8888)
    if scale > 1:
        image = image.scaled(image.width() * scale, image.height() * scale, Qt.KeepAspectRatio, Qt.FastTransformation)
    return QPixmap.fromImage(image)


class PixelCanvas(QGraphicsView):
    """Nearest-neighbor canvas with a foreground pixel grid."""

    def __init__(self) -> None:
        super().__init__()
        self.state = CanvasState()
        self.setScene(QGraphicsScene(self))
        self.setBackgroundBrush(QColor("#202020"))
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        self.setMinimumSize(520, 520)
        self._item: QGraphicsPixmapItem | None = None

    def set_image(self, path: Path) -> None:
        self.scene().clear()
        self._item = self.scene().addPixmap(_pixmap(path, self.state.zoom))
        self.scene().setSceneRect(self._item.boundingRect())
        self.viewport().update()

    def drawForeground(self, painter: QPainter, rect) -> None:  # type: ignore[no-untyped-def]
        del rect
        if self.state.zoom < 4:
            return
        painter.setPen(QColor(255, 255, 255, 45))
        size = 64 * self.state.zoom
        for value in range(65):
            position = value * self.state.zoom
            painter.drawLine(position, 0, position, size)
            painter.drawLine(0, position, size, position)


class MainWindow(QMainWindow):
    """GUI shell; all conversion logic remains in PixelTileCompiler."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Pixel Tile Compiler")
        self.resize(1280, 760)
        self.source_path: Path | None = None
        self.canvas = PixelCanvas()
        self.source_preview = QLabel("入力画像")
        self.result_preview = QLabel("変換結果")
        self.tile_preview = QLabel("3×3タイル")
        self.status = QLabel("準備完了")
        self.palette = QSpinBox()
        self.palette.setRange(4, 32)
        self.palette.setValue(16)
        self._build_ui()

    def _build_ui(self) -> None:
        open_button = QPushButton("画像を開く")
        open_button.clicked.connect(self.open_image)
        compile_button = QPushButton("コンパイル")
        compile_button.clicked.connect(self.compile_image)
        form = QFormLayout()
        form.addRow("パレット", self.palette)
        form.addRow(open_button)
        form.addRow(compile_button)
        settings = QWidget()
        settings.setLayout(form)

        previews = QVBoxLayout()
        for label in (self.source_preview, self.result_preview, self.tile_preview):
            label.setAlignment(Qt.AlignCenter)
            label.setMinimumSize(180, 180)
            previews.addWidget(label)
        preview_widget = QWidget()
        preview_widget.setLayout(previews)

        root = QHBoxLayout()
        root.addWidget(settings, 1)
        root.addWidget(self.canvas, 3)
        root.addWidget(preview_widget, 1)
        container = QWidget()
        container.setLayout(root)
        outer = QVBoxLayout()
        outer.addWidget(container)
        outer.addWidget(self.status)
        wrapper = QWidget()
        wrapper.setLayout(outer)
        self.setCentralWidget(wrapper)

    def open_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "画像を開く", "", "画像 (*.png *.jpg *.jpeg *.webp)")
        if path:
            self.source_path = Path(path)
            self.source_preview.setPixmap(_pixmap(self.source_path, 1).scaled(180, 180, Qt.KeepAspectRatio, Qt.FastTransformation))
            self.status.setText(f"入力: {self.source_path.name}")

    def compile_image(self) -> None:
        if self.source_path is None:
            self.status.setText("先に画像を開いてください")
            return
        output = Path("output") / self.source_path.stem
        result = PixelTileCompiler().compile(
            self.source_path,
            CompilerConfig(output_root=output, palette_budget=self.palette.value()),
        )
        self.canvas.set_image(result.final_path)
        self.result_preview.setPixmap(_pixmap(result.final_path, 2))
        tile = output / "debug" / "08_tile_preview.png"
        if tile.exists():
            self.tile_preview.setPixmap(_pixmap(tile, 1).scaled(180, 180, Qt.KeepAspectRatio, Qt.FastTransformation))
        self.status.setText(f"完了: {result.metrics.actual_palette_count}色")
