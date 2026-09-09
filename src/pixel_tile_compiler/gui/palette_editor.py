"""基準paletteを手で組むためのエディタ。

仕様: docs/spec/palette_editor_spec.md

色の補間・近似・自動生成は行わない。利用者が選んだ色だけを、既存のpalette契約
（重複なし・昇順・1〜64色）へ通して扱う。
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QColorDialog,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

from pixel_tile_compiler.gui.palette_presets import PALETTE_PRESETS
from pixel_tile_compiler.gui.widgets import NoWheelComboBox, PaletteSwatchList
from pixel_tile_compiler.palette_contract import (
    load_palette_json,
    normalize_palette,
    save_palette_json,
    validate_reference_palette,
)

PALETTE_COLOR_LIMIT = 64

RGB = tuple[int, int, int]


class PaletteEditorDialog(QDialog):
    """色見本を直接編集して基準paletteを組むダイアログ。"""

    def __init__(self, colors=(), output_palette=(), parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self.setWindowTitle("基準paletteを編集")
        self.setMinimumWidth(520)
        self._colors: tuple[RGB, ...] = ()
        self._output_palette: tuple[RGB, ...] = tuple(normalize_palette(output_palette))

        self.preset_combo = NoWheelComboBox()
        self.preset_combo.addItem("（選択してください）", userData=None)
        for preset in PALETTE_PRESETS:
            self.preset_combo.addItem(f"{preset.name}（{len(preset.colors)}色）", userData=preset.name)
        self.preset_combo.currentIndexChanged.connect(self._on_preset_selected)

        self.swatches = PaletteSwatchList()
        self.swatches.setSelectionMode(PaletteSwatchList.SelectionMode.SingleSelection)
        self.swatches.setMinimumHeight(150)

        self.count_label = QLabel()
        self.count_label.setObjectName("mutedText")

        self.add_button = QPushButton("色を追加")
        self.add_button.clicked.connect(self.add_color)
        self.change_button = QPushButton("選択色を変更")
        self.change_button.clicked.connect(self.change_selected_color)
        self.remove_button = QPushButton("選択色を削除")
        self.remove_button.clicked.connect(self.remove_selected_color)
        self.import_button = QPushButton("出力paletteから取り込む")
        self.import_button.clicked.connect(self.import_output_palette)
        self.import_button.setEnabled(bool(self._output_palette))
        self.load_button = QPushButton("読み込む...")
        self.load_button.clicked.connect(self._on_load_clicked)
        self.save_button = QPushButton("保存...")
        self.save_button.clicked.connect(self._on_save_clicked)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        preset_row = QHBoxLayout()
        preset_row.addWidget(QLabel("プリセット"))
        preset_row.addWidget(self.preset_combo, 1)

        edit_row = QHBoxLayout()
        edit_row.setSpacing(6)
        for widget in (self.add_button, self.change_button, self.remove_button):
            edit_row.addWidget(widget)
        edit_row.addStretch(1)

        file_row = QHBoxLayout()
        file_row.setSpacing(6)
        file_row.addWidget(self.import_button)
        file_row.addStretch(1)
        file_row.addWidget(self.load_button)
        file_row.addWidget(self.save_button)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)
        layout.addLayout(preset_row)
        layout.addWidget(self.swatches, 1)
        layout.addWidget(self.count_label)
        layout.addLayout(edit_row)
        layout.addLayout(file_row)
        layout.addWidget(buttons)

        self.set_colors(colors or ((0, 0, 0),))

    # ---- 色の状態 -------------------------------------------------------

    def colors(self) -> tuple[RGB, ...]:
        """現在の色一覧を、契約どおり重複なし昇順で返す。"""
        return self._colors

    def set_colors(self, colors) -> None:  # type: ignore[no-untyped-def]
        """色一覧を差し替える。空にはしない。"""
        normalized = tuple(normalize_palette(colors))
        if not normalized:
            return
        self._colors = normalized[:PALETTE_COLOR_LIMIT]
        self._refresh()

    def _refresh(self) -> None:
        self.swatches.set_colors(self._colors)
        self.count_label.setText(f"{len(self._colors)}色 / 上限{PALETTE_COLOR_LIMIT}色")
        self.add_button.setEnabled(len(self._colors) < PALETTE_COLOR_LIMIT)
        self.remove_button.setEnabled(len(self._colors) > 1)

    def select_index(self, index: int) -> None:
        """色見本の選択位置を変える。"""
        self.swatches.setCurrentRow(int(index))

    def selected_index(self) -> int:
        """選択中の位置。未選択なら -1。"""
        return int(self.swatches.currentRow())

    # ---- 操作 -----------------------------------------------------------

    def pick_color(self, initial: RGB | None = None) -> RGB | None:
        """色選択ダイアログを開いて色を返す。テストではここを差し替える。"""
        start = QColor(*(initial or (255, 255, 255)))
        chosen = QColorDialog.getColor(start, self, "色を選ぶ")
        if not chosen.isValid():
            return None
        return (chosen.red(), chosen.green(), chosen.blue())

    def add_color(self) -> None:
        """色を1つ増やす。上限に達している、または重複する色は増やさない。"""
        if len(self._colors) >= PALETTE_COLOR_LIMIT:
            return
        picked = self.pick_color()
        if picked is None or picked in self._colors:
            return
        self.set_colors((*self._colors, picked))

    def change_selected_color(self) -> None:
        """選択中の色を選び直す。"""
        index = self.selected_index()
        if not 0 <= index < len(self._colors):
            return
        picked = self.pick_color(self._colors[index])
        if picked is None or picked in self._colors:
            return
        remaining = [color for position, color in enumerate(self._colors) if position != index]
        self.set_colors((*remaining, picked))

    def remove_selected_color(self) -> None:
        """選択中の色を取り除く。最後の1色は残す。"""
        index = self.selected_index()
        if not 0 <= index < len(self._colors) or len(self._colors) <= 1:
            return
        self.set_colors(
            tuple(color for position, color in enumerate(self._colors) if position != index)
        )

    def import_output_palette(self) -> None:
        """直近のコンパイル結果の実測paletteで置き換える。"""
        if not self._output_palette:
            return
        self.set_colors(self._output_palette)

    def apply_preset(self, name: str | None) -> None:
        """プリセット名の色セットで置き換える。Noneなら何もしない。"""
        if name is None:
            return
        for preset in PALETTE_PRESETS:
            if preset.name == name:
                self.set_colors(preset.colors)
                return

    def _on_preset_selected(self, _index: int) -> None:
        self.apply_preset(self.preset_combo.currentData())

    # ---- 入出力 ---------------------------------------------------------

    def load_from(self, path: Path | str) -> None:
        """palette.jsonを読み込んで置き換える。"""
        self.set_colors(load_palette_json(path))

    def save_to(self, path: Path | str) -> Path:
        """現在の色一覧をpalette.jsonとして書き出す。"""
        return save_palette_json(path, self._colors)

    def _on_load_clicked(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "paletteを読み込む", "", "palette.json (*.json)")
        if not path:
            return
        try:
            self.load_from(Path(path))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "読み込めません", f"paletteを読み込めませんでした: {exc}")

    def _on_save_clicked(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "paletteを保存", "palette.json", "palette.json (*.json)")
        if not path:
            return
        try:
            self.save_to(Path(path))
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "保存できません", f"paletteを保存できませんでした: {exc}")

    def accept(self) -> None:
        """契約を通ることを確かめてから閉じる。"""
        try:
            validate_reference_palette(self._colors)
        except ValueError as exc:
            QMessageBox.warning(self, "確定できません", f"paletteが不正です: {exc}")
            return
        super().accept()


__all__ = ["PALETTE_COLOR_LIMIT", "PaletteEditorDialog"]
