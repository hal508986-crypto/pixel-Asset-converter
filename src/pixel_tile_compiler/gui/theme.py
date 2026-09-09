"""GUIのDesign Tokenとスタイル定義。

色・余白・角丸を役割名で1か所に集約し、画面側は役割名だけを参照する。
個別のウィジェットに色をベタ書きしない。
"""

from __future__ import annotations

from string import Template

UI_TOKENS: dict[str, str] = {
    # 背景と面
    "background": "#1d2127",
    "surface": "#252a31",
    "surface_raised": "#2d333b",
    "field": "#303640",
    # 操作部品
    "control": "#343c48",
    "control_hover": "#414b59",
    "control_pressed": "#2b323c",
    "control_disabled": "#2a2f36",
    # 罫線
    "border": "#3a424d",
    "border_strong": "#48515d",
    # 文字
    "text": "#eef1f5",
    "text_strong": "#f4f6f8",
    "muted": "#9da6b2",
    "text_disabled": "#78818d",
    # 強調
    "accent": "#f0c674",
    "primary": "#3565a8",
    "primary_hover": "#4379c1",
    "primary_border": "#5d8ed5",
    "focus": "#77a9ff",
    "warning": "#e3b341",
    # 形
    "radius_sm": "4px",
    "radius_md": "6px",
    "radius_lg": "8px",
}

_STYLESHEET = Template(
    """
    QMainWindow, QWidget { background: $background; color: $text; }
    QLabel { font-size: 14px; }
    QLabel#sectionTitle { font-size: 17px; font-weight: 700; color: $accent; }
    QLabel#subsectionTitle { font-size: 13px; font-weight: 700; color: $accent; }
    QLabel#mutedText { color: $muted; font-size: 12px; }
    QLabel#statusText { color: $muted; }
    QLabel#warningText { color: $warning; font-size: 12px; }

    QFrame#headerPanel, QFrame#actionPanel {
        background: $surface;
        border: 1px solid $border;
        border-radius: $radius_lg;
    }

    QGroupBox {
        border: 1px solid $border;
        border-radius: $radius_lg;
        margin-top: 12px;
        padding: 14px 10px 10px 10px;
        background: $surface;
        font-size: 14px;
        font-weight: 600;
    }
    QGroupBox::title {
        subcontrol-origin: margin;
        left: 12px;
        padding: 0 5px;
        color: $accent;
    }

    QTabWidget::pane {
        border: 1px solid $border;
        border-radius: $radius_lg;
        background: $surface;
        top: -1px;
    }
    QTabBar::tab {
        background: transparent;
        color: $muted;
        border: 1px solid transparent;
        border-top-left-radius: $radius_md;
        border-top-right-radius: $radius_md;
        padding: 7px 16px;
        margin-right: 2px;
        font-size: 13px;
        font-weight: 600;
    }
    QTabBar::tab:hover:!selected { color: $text; background: $control; }
    QTabBar::tab:selected {
        background: $surface;
        color: $accent;
        border: 1px solid $border;
        border-bottom-color: $surface;
    }
    QTabBar::tab:disabled { color: $text_disabled; }
    QTabBar::tab:focus { border: 1px solid $focus; }

    QSplitter::handle { background: $border; }
    QSplitter::handle:horizontal { width: 6px; margin: 2px 0; }
    QSplitter::handle:vertical { height: 6px; margin: 0 2px; }
    QSplitter::handle:hover { background: $primary_border; }

    QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
        min-height: 30px;
        border: 1px solid $border_strong;
        border-radius: $radius_sm;
        padding: 2px 8px;
        background: $field;
        color: $text_strong;
    }
    QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QLineEdit:focus {
        border: 1px solid $focus;
    }
    QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QLineEdit:disabled {
        background: $control_disabled;
        color: $text_disabled;
        border-color: $border;
    }

    QPushButton {
        min-height: 32px;
        border: 1px solid #556171;
        border-radius: $radius_md;
        padding: 0 14px;
        background: $control;
        color: $text_strong;
        font-weight: 600;
    }
    QPushButton:hover { background: $control_hover; }
    QPushButton:pressed { background: $control_pressed; }
    QPushButton:focus { border: 1px solid $focus; }
    QPushButton:disabled { color: $text_disabled; background: $control_disabled; }
    QPushButton#primaryButton { background: $primary; border-color: $primary_border; }
    QPushButton#primaryButton:hover { background: $primary_hover; }
    QPushButton#primaryButton:disabled { background: $control_disabled; border-color: $border; }

    QPushButton#segmentButton {
        min-height: 30px;
        padding: 0 16px;
        background: transparent;
        border: 1px solid $border_strong;
        color: $muted;
        font-weight: 600;
    }
    QPushButton#segmentButton:hover { background: $control; color: $text; }
    QPushButton#segmentButton:checked {
        background: $primary;
        border-color: $primary_border;
        color: $text_strong;
    }

    QProgressBar {
        min-height: 30px;
        border: 1px solid $border_strong;
        border-radius: $radius_sm;
        background: $field;
        color: $text;
        text-align: center;
    }
    QProgressBar::chunk { background: $primary; border-radius: 3px; }

    QCheckBox { spacing: 6px; }
    QScrollArea { border: none; background: transparent; }
    QGraphicsView { border: 1px solid $border; border-radius: $radius_md; }
    QGraphicsView, QLabel { outline: none; }
    """
)


def build_stylesheet() -> str:
    """Design Tokenを差し込んだアプリ全体のスタイルシートを返す。"""
    return _STYLESHEET.substitute(UI_TOKENS)
