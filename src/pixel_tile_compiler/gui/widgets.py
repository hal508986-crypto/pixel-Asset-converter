"""GUI共通の入力部品。

スクロール中のホイール誤爆を防ぐため、数値入力と選択部品はホイールで値を変えない。
ホイールは常に「囲んでいるスクロール領域を動かす操作」として扱い、値の変更は
クリック入力・矢印キー・増減ボタンで行う。
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QApplication,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSlider,
    QSpinBox,
    QVBoxLayout,
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


class NoWheelSlider(_NoWheelMixin, QSlider):
    """ホイールで値が変わらないスライダー。"""

    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(Qt.Orientation.Horizontal, parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)


# palette上限の目安。数値だけでは何色が何を意味するのか読めないため、
# 代表値に用途の見出しを添える。
PALETTE_BUDGET_ANCHORS: tuple[tuple[int, str], ...] = (
    (4, "4 GB風"),
    (24, "24 レトロ"),
    (64, "64 絵画寄り"),
)
PALETTE_BUDGET_MIN = 4
PALETTE_BUDGET_MAX = 64


class PaletteBudgetSlider(QWidget):
    """palette上限をスライダーで選ぶ。現在値と代表値の目安を併記する。

    `value` / `setValue` / `valueChanged` はスピンボックスと同じ形で使える。
    """

    valueChanged = Signal(int)

    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.slider = NoWheelSlider()
        self.slider.setRange(PALETTE_BUDGET_MIN, PALETTE_BUDGET_MAX)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(4)
        self.slider.setTickInterval(4)
        self.slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider.setMinimumWidth(180)
        self.readout = QLabel()
        self.readout.setObjectName("paletteReadout")
        self.readout.setMinimumWidth(52)

        # 目安は凡例として1行にまとめる。等分割で並べると、24が実際の目盛り位置
        # （4〜64のうち33%）ではなく中央に見えてしまうため。
        self.legend = QLabel(" / ".join(text for _value, text in PALETTE_BUDGET_ANCHORS))
        self.legend.setObjectName("mutedText")

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        row.addWidget(self.slider, 1)
        row.addWidget(self.readout)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addLayout(row)
        layout.addWidget(self.legend)

        self.slider.valueChanged.connect(self._on_slider_changed)
        self.setValue(24)

    def _on_slider_changed(self, value: int) -> None:
        self.readout.setText(self.readout_text())
        self.valueChanged.emit(int(value))

    def value(self) -> int:
        """現在のpalette上限を返す。"""
        return int(self.slider.value())

    def setValue(self, value: int) -> None:  # noqa: N802 - Qtの命名に合わせる
        """palette上限を設定する。範囲外は端に丸める。"""
        clamped = max(PALETTE_BUDGET_MIN, min(PALETTE_BUDGET_MAX, int(value)))
        self.slider.setValue(clamped)
        self.readout.setText(self.readout_text())

    def readout_text(self) -> str:
        """スライダーの右に出す現在値の表示文字列。"""
        return f"{self.value()}色"

    @staticmethod
    def anchor_labels() -> tuple[tuple[int, str], ...]:
        """代表値と、その色数が向く用途の見出し。"""
        return PALETTE_BUDGET_ANCHORS
