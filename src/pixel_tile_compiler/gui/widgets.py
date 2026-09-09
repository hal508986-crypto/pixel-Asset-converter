"""GUI共通の入力部品。

スクロール中のホイール誤爆を防ぐため、数値入力と選択部品はホイールで値を変えない。
ホイールは常に「囲んでいるスクロール領域を動かす操作」として扱い、値の変更は
クリック入力・矢印キー・増減ボタンで行う。
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QListWidget,
    QListWidgetItem,
    QSpinBox,
    QWidget,
)


def enclosing_scroll_area(widget: QWidget) -> QAbstractScrollArea | None:
    """widgetを内包する最も近いスクロール領域を返す。無ければNone。"""
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QAbstractScrollArea):
            return parent
        parent = parent.parentWidget()
    return None


class _NoWheelMixin:
    """ホイールで値を変えず、スクロール操作として扱う振る舞い。

    最寄りのスクロール領域へ明示的に転送してから受理する。受理することでQtの
    ホイール伝播が止まり、同じ操作が二重にスクロールへ効くことを防ぐ。
    囲むスクロール領域が無いときは受理せず、上位の判断に委ねる。
    """

    def wheelEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        scroll_area = enclosing_scroll_area(self)  # type: ignore[arg-type]
        if scroll_area is None:
            event.ignore()
            return
        QApplication.sendEvent(scroll_area.viewport(), event)
        event.accept()


class NoWheelSpinBox(_NoWheelMixin, QSpinBox):
    """ホイールで値が変わらない整数スピンボックス。"""

    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)


class NoWheelDoubleSpinBox(_NoWheelMixin, QDoubleSpinBox):
    """ホイールで値が変わらない実数スピンボックス。"""

    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)


class NoWheelComboBox(_NoWheelMixin, QComboBox):
    """ホイールで選択が変わらないコンボボックス。"""

    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)


def swatch_text_color(red: int, green: int, blue: int) -> str:
    """色見本の上で読める文字色を返す。暗い色なら白、明るい色なら黒。"""
    return "#ffffff" if red + green + blue < 390 else "#20252c"


class PaletteSwatchList(QListWidget):
    """RGB paletteを色見本の並びとして見せる一覧。

    地形の基準paletteと、コンパイル結果の実測paletteの両方で使う。
    """

    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.setFlow(QListWidget.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setSelectionMode(QListWidget.SelectionMode.NoSelection)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setSpacing(2)

    def set_colors(self, colors) -> None:  # type: ignore[no-untyped-def]
        """色見本を差し替える。空なら一覧も空にする。"""
        self.clear()
        for red, green, blue in colors:
            item = QListWidgetItem(f"#{red:02X}{green:02X}{blue:02X}")
            item.setBackground(QColor(red, green, blue))
            item.setForeground(QColor(swatch_text_color(red, green, blue)))
            item.setToolTip(f"RGB ({red}, {green}, {blue})")
            # 色そのものを見比べられるよう、文字幅ではなく一定の面積を与える
            item.setSizeHint(QSize(86, 34))
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.addItem(item)
