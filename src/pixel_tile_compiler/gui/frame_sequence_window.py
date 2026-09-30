"""PySide6 window: PNG連番 → 背景除去 → コンパイル → 透過GIF。

処理そのものは pixelizer.frame_sequence_job（CLIと共通）が担い、このウィンドウは
設定の入力・進捗表示・中止・結果のアニメプレビューだけを受け持つ。
"""

from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QDesktopServices, QImage, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QCheckBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from pixel_tile_compiler.gui.theme import build_stylesheet
from pixel_tile_compiler.gui.widgets import NoWheelComboBox, NoWheelDoubleSpinBox, NoWheelSpinBox
from pixel_tile_compiler.io.gif_export import gif_durations_ms
from pixel_tile_compiler.pixelizer.frame_sequence_job import (
    FrameSequenceCancelled,
    FrameSequenceInspection,
    FrameSequenceRequest,
    FrameSequenceSummary,
    inspect_frame_directory,
    run_frame_sequence,
)

CANVAS_PRESETS: tuple[tuple[str, tuple[int, int] | None], ...] = (
    ("512 × 512（既定）", (512, 512)),
    ("256 × 256", (256, 256)),
    ("128 × 128", (128, 128)),
    ("64 × 64", (64, 64)),
    ("カスタム", None),
)
BACKGROUND_MODE_LABELS = (
    ("自動", "auto", "鮮やかな背景（緑・青緑など）は画像内の背景色をすべて消し、白・グレーは外周に繋がる部分だけ消します"),
    ("外周に繋がる部分だけ", "connected", "キャラの内側にある背景色と同じ色（白いハイライトなど）を残します。白背景向け"),
    ("画像内の背景色をすべて", "global", "腕と体のあいだなど、外周に繋がらない背景も消します。緑・青緑背景向け"),
)
OUTLINE_LABELS = (("なし", "off"), ("黒", "black"), ("白", "white"))
DETAIL_LABELS = (("少なめ", "sparse"), ("標準", "balanced"), ("多め", "detailed"))
FRAMING_MODES = (
    ("全フレームが収まる倍率（従来）", "union",
     "Canvasにすべてのフレームがはみ出さず収まる最大の倍率にします。槍を大きく突き出すコマなどがあると、キャラ本体が小さくなります"),
    ("上位N%のフレームが収まる倍率（極端なコマは見切れる）", "percentile",
     "指定した割合のフレームが収まる倍率にして、はみ出す極端なコマ（槍の突き出しなど）は見切れを許します。キャラ本体が大きくなります"),
    ("キャラの身長を指定（倍率と足元を全フレーム固定）", "height",
     "アルファから測ったキャラ本体の高さを基準に倍率を決めます。踏み込み・跳躍で縦幅が変わってもサイズはブレず、同じキャラの別動作でも同じ身長にすればサイズが揃います"),
)
HEIGHT_REFERENCE_LABELS = (
    ("中央値（既定）", "median", "本体の高さの中央値。一部のコマだけ踏み込み・しゃがみでも影響を受けにくい"),
    ("最初のフレーム", "first", "立ちポーズから始まる素材向け"),
    ("フレーム番号を指定", "frame", "基準にしたいポーズのコマの番号（1始まり）"),
)
STAGE_LABELS = {
    "load": "PNG連番を読み込み中…",
    "key": "背景を除去中…",
    "finalize": "色の安定化・GIF書き出し中…",
}
CHECKER_LIGHT = QColor("#d6d9dd")
CHECKER_DARK = QColor("#b9bec5")


class FrameAnimationPreview(QWidget):
    """最近傍で拡大し、透過を市松模様で見せるアニメプレビュー。GIFと同じ各フレーム時間で再生する。"""

    frame_changed = Signal(int)

    def __init__(self, empty_text: str = "入力フォルダを選ぶと先頭フレームを表示します") -> None:
        super().__init__()
        self._empty_text = empty_text
        self._paths: list[Path] = []
        self._images: dict[int, QImage] = {}
        self._durations: tuple[int, ...] = ()
        self._index = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._advance)
        self.setMinimumSize(280, 280)
        self.setToolTip("透明部分は市松模様で表示します")

    def set_frames(self, paths: list[Path], fps: float | None = None, *, play: bool = True) -> None:
        self._timer.stop()
        self._paths = [Path(path) for path in paths]
        self._images.clear()
        self._index = 0
        fps = fps if fps and 0 < fps <= 100 else 24.0
        self._durations = gif_durations_ms(len(self._paths), fps) if self._paths else ()
        self.update()
        if play and len(self._paths) > 1:
            self.play()

    @property
    def frame_count(self) -> int:
        return len(self._paths)

    @property
    def current_index(self) -> int:
        return self._index

    @property
    def is_playing(self) -> bool:
        return self._timer.isActive()

    def play(self) -> None:
        if len(self._paths) > 1 and not self._timer.isActive():
            self._timer.start(self._durations[self._index])

    def pause(self) -> None:
        self._timer.stop()

    def toggle(self) -> bool:
        self.pause() if self.is_playing else self.play()
        return self.is_playing

    def _advance(self) -> None:
        self._index = (self._index + 1) % len(self._paths)
        self.frame_changed.emit(self._index)
        self.update()
        self._timer.start(self._durations[self._index])

    def _image(self, index: int) -> QImage | None:
        if index not in self._images:
            path = self._paths[index]
            if not path.exists():
                return None
            self._images[index] = QImage(str(path)).convertToFormat(QImage.Format.Format_RGBA8888)
        return self._images[index]

    def paintEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        del event
        painter = QPainter(self)
        rect = self.rect()
        size = 8
        painter.setPen(Qt.PenStyle.NoPen)
        for y in range(0, rect.height() + size, size):
            for x in range(0, rect.width() + size, size):
                painter.fillRect(x, y, size, size, CHECKER_LIGHT if (x // size + y // size) % 2 == 0 else CHECKER_DARK)
        image = self._image(self._index) if self._paths else None
        if image is None:
            painter.setPen(QColor("#434a54"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self._empty_text)
            return
        scaled = image.scaled(
            max(1, rect.width() - 16),
            max(1, rect.height() - 16),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        painter.drawImage((rect.width() - scaled.width()) // 2, (rect.height() - scaled.height()) // 2, scaled)


class FrameSequenceWorker(QObject):
    """ジョブを別スレッドで実行する。中止は進捗通知の中で例外を投げて行う（既存の出力は壊れない）。"""

    progress = Signal(str, int, int)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, request: FrameSequenceRequest, cancel_event: threading.Event) -> None:
        super().__init__()
        self._request = request
        self._cancel_event = cancel_event

    def _on_progress(self, stage: str, done: int, total: int) -> None:
        if self._cancel_event.is_set():
            raise FrameSequenceCancelled()
        self.progress.emit(stage, done, total)

    @Slot()
    def run(self) -> None:
        try:
            summary = run_frame_sequence(self._request, progress=self._on_progress)
        except FrameSequenceCancelled:
            self.cancelled.emit()
        except (OSError, ValueError, RuntimeError) as exc:
            self.failed.emit(str(exc))
        else:
            self.finished.emit(summary)


class FrameSequenceWindow(QMainWindow):
    """PNG連番→GIFの設定・実行・確認ウィンドウ。"""

    def __init__(self, output_root: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle("PNG連番からGIFを作成")
        self.setStyleSheet(build_stylesheet())
        self.resize(1240, 800)
        self._output_root = Path(output_root) if output_root is not None else Path("output")
        self._output_touched = False
        self._inspection: FrameSequenceInspection | None = None
        self._summary: FrameSequenceSummary | None = None
        self._thread: QThread | None = None
        self._worker: FrameSequenceWorker | None = None
        self._cancel_event = threading.Event()
        # テストで差し替えられるよう、上書き確認は関数として持つ
        self.confirm_overwrite = self._ask_overwrite

        self._build_widgets()
        self._build_layout()
        self._update_enabled_states()

    # ---- 部品 -------------------------------------------------------------------------
    def _build_widgets(self) -> None:
        self.input_field = QLineEdit()
        self.input_field.setReadOnly(True)
        self.input_field.setPlaceholderText("PNG連番が入ったフォルダ")
        self.input_browse_button = QPushButton("参照…")
        self.input_browse_button.clicked.connect(self.browse_input)
        self.input_info = QLabel("フォルダを選ぶと、枚数・サイズ・背景色の推定結果を表示します")
        self.input_info.setWordWrap(True)
        self.input_info.setMinimumHeight(88)  # 枚数・サイズ・背景色の推定結果（折り返して最大4行程度）
        self.input_info.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.output_field = QLineEdit(str(self._output_root))
        self.output_field.textEdited.connect(self._on_output_edited)
        self.output_browse_button = QPushButton("参照…")
        self.output_browse_button.clicked.connect(self.browse_output)

        self.canvas_preset = NoWheelComboBox()
        for label, size in CANVAS_PRESETS:
            self.canvas_preset.addItem(label, userData=size)
        self.canvas_width = NoWheelSpinBox()
        self.canvas_height = NoWheelSpinBox()
        for spin in (self.canvas_width, self.canvas_height):
            spin.setRange(16, 4096)
            spin.setValue(512)
            spin.valueChanged.connect(self._on_canvas_spin_changed)
        self.canvas_preset.currentIndexChanged.connect(self._on_canvas_preset_changed)
        self.palette = NoWheelSpinBox()
        self.palette.setRange(4, 64)
        self.palette.setValue(24)

        self.framing_mode = NoWheelComboBox()
        for index, (label, value, tip) in enumerate(FRAMING_MODES):
            self.framing_mode.addItem(label, userData=value)
            self.framing_mode.setItemData(index, tip, Qt.ItemDataRole.ToolTipRole)
        self.fit_percentile = NoWheelDoubleSpinBox()
        self.fit_percentile.setRange(1.0, 100.0)
        self.fit_percentile.setDecimals(0)
        self.fit_percentile.setValue(70.0)
        self.fit_percentile.setSuffix(" %")
        self.character_height = NoWheelDoubleSpinBox()
        self.character_height.setRange(8.0, 4096.0)
        self.character_height.setDecimals(0)
        self.character_height.setValue(200.0)
        self.character_height.setSuffix(" px")
        self.character_height.setToolTip("出力でのキャラ本体（頭〜足）の高さ")
        self.height_reference = NoWheelComboBox()
        for index, (label, value, tip) in enumerate(HEIGHT_REFERENCE_LABELS):
            self.height_reference.addItem(label, userData=value)
            self.height_reference.setItemData(index, tip, Qt.ItemDataRole.ToolTipRole)
        self.reference_frame = NoWheelSpinBox()
        self.reference_frame.setRange(1, 9999)
        self.reference_frame.setValue(1)
        self.canvas_auto = QCheckBox("Canvasを自動で決める（見切れなし・余白ほぼなし。幅・高さは無視）")
        self.canvas_auto.setChecked(True)
        self.foot_lock = QCheckBox("足元の高さを全フレームでそろえる（接地のずれを補正）")
        self.foot_lock.setToolTip(
            "各フレームの本体の下端を基準の足元にそろえます（縦の平行移動のみ。倍率は変わりません）。"
            "踏み込み中などに元動画の接地線がずれて足が浮いて見える素材向け。跳躍のような本物の浮きも打ち消すので注意"
        )
        self.write_trimmed = QCheckBox("切り詰めた画像とオフセットも出力する（ゲーム用）")
        self.write_trimmed.setChecked(True)
        self.write_trimmed.setToolTip("trimmed_frames/ と trim_manifest.json。各フレームを可視範囲に切り詰め、足元（ピボット）からの位置を記録します")
        for widget in (self.framing_mode, self.height_reference, self.canvas_auto):
            if isinstance(widget, QCheckBox):
                widget.toggled.connect(self._update_enabled_states)
            else:
                widget.currentIndexChanged.connect(self._update_enabled_states)

        self.key_background = QCheckBox("単色背景を透過にする（透過済みの連番ならオフ）")
        self.key_background.setChecked(True)
        self.background_mode = NoWheelComboBox()
        for index, (label, value, tip) in enumerate(BACKGROUND_MODE_LABELS):
            self.background_mode.addItem(label, userData=value)
            self.background_mode.setItemData(index, tip, Qt.ItemDataRole.ToolTipRole)
        self.background_color_auto = QCheckBox("背景色を全フレームの外周から自動で推定する")
        self.background_color_auto.setChecked(True)
        self.background_color = QLineEdit()
        self.background_color.setPlaceholderText("#RRGGBB")
        self.background_color.setMaxLength(7)
        self.tolerance = NoWheelSpinBox()
        self.tolerance.setRange(0, 255)
        self.tolerance.setValue(30)
        self.tolerance.setToolTip("背景色とみなす色の距離。背景に色ムラやノイズがあるときは大きめ（目安30〜45）")
        self.choke = NoWheelSpinBox()
        self.choke.setRange(0, 8)
        self.choke.setToolTip("輪郭を内側へ削る画素数。背景との混色の縁を落とす（画像端に接する輪郭は削らない）")
        for widget in (self.key_background, self.background_color_auto):
            widget.toggled.connect(self._update_enabled_states)

        self.stabilize = QCheckBox("色のちらつきを抑える（時間方向の安定化）")
        self.stabilize.setChecked(True)
        self.stabilize_margin = NoWheelDoubleSpinBox()
        self.stabilize_margin.setRange(1.0, 100.0)
        self.stabilize_margin.setValue(12.0)
        self.stabilize_margin.setToolTip("大きいほど強く抑える。上げすぎると本物の色の変化も潰す（目安12〜20）")
        self.stabilize.toggled.connect(self._update_enabled_states)

        self.gif = QCheckBox("透過GIFも出力する")
        self.gif.setChecked(True)
        self.fps = NoWheelDoubleSpinBox()
        self.fps.setRange(1.0, 100.0)
        self.fps.setValue(24.0)
        self.play_once = QCheckBox("ループせず1回だけ再生")
        self.gif.toggled.connect(self._update_enabled_states)

        self.remove_isolated = QCheckBox("微小な孤立点を除去する")
        self.remove_isolated.setChecked(True)
        self.outline = NoWheelComboBox()
        for label, value in OUTLINE_LABELS:
            self.outline.addItem(label, userData=value)
        self.detail = NoWheelComboBox()
        for label, value in DETAIL_LABELS:
            self.detail.addItem(label, userData=value)
        self.detail.setCurrentIndex(1)

        self.run_button = QPushButton("コンパイルしてGIFを作る")
        self.run_button.setObjectName("primaryButton")
        self.run_button.clicked.connect(self.start_run)
        self.cancel_button = QPushButton("中止")
        self.cancel_button.clicked.connect(self.cancel_run)
        self.cancel_button.setEnabled(False)
        self.progress = QProgressBar()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.status = QLabel("")
        self.status.setWordWrap(True)

        self.preview = FrameAnimationPreview()
        self.play_button = QPushButton("一時停止")
        self.play_button.clicked.connect(self._on_play_clicked)
        self.play_button.setEnabled(False)
        self.frame_label = QLabel("")
        self.summary = QLabel("結果はここに表示します")
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.open_folder_button = QPushButton("出力フォルダを開く")
        self.open_folder_button.clicked.connect(self.open_output_folder)
        self.open_folder_button.setEnabled(False)
        self.open_gif_button = QPushButton("GIFを開く")
        self.open_gif_button.clicked.connect(self.open_gif)
        self.open_gif_button.setEnabled(False)
        self.preview.frame_changed.connect(self._on_preview_frame)

    def _build_layout(self) -> None:
        def row(*widgets: QWidget) -> QWidget:
            holder = QWidget()
            layout = QHBoxLayout(holder)
            layout.setContentsMargins(0, 0, 0, 0)
            for widget in widgets:
                layout.addWidget(widget, 1 if isinstance(widget, QLineEdit) else 0)
            return holder

        io_group = QGroupBox("入出力")
        io_form = QFormLayout(io_group)
        io_form.addRow("PNG連番フォルダ", row(self.input_field, self.input_browse_button))
        io_form.addRow(self.input_info)
        io_form.addRow("出力先", row(self.output_field, self.output_browse_button))

        size_group = QGroupBox("出力サイズ・色")
        size_form = QFormLayout(size_group)
        size_form.addRow("出力サイズ", self.canvas_preset)
        size_form.addRow("幅 × 高さ", row(self.canvas_width, QLabel("×"), self.canvas_height))
        size_form.addRow("パレット色数", self.palette)

        framing_group = QGroupBox("キャラの大きさ（フレーミング）")
        framing_form = QFormLayout(framing_group)
        framing_form.addRow("決め方", self.framing_mode)
        framing_form.addRow("収める割合", self.fit_percentile)
        framing_form.addRow("キャラの身長", self.character_height)
        framing_form.addRow("身長の基準", self.height_reference)
        framing_form.addRow("基準フレーム番号", self.reference_frame)
        framing_form.addRow(self.canvas_auto)
        framing_form.addRow(self.foot_lock)
        framing_form.addRow(self.write_trimmed)

        bg_group = QGroupBox("背景の除去")
        bg_form = QFormLayout(bg_group)
        bg_form.addRow(self.key_background)
        bg_form.addRow("消し方", self.background_mode)
        bg_form.addRow(self.background_color_auto)
        bg_form.addRow("背景色", self.background_color)
        bg_form.addRow("許容差", self.tolerance)
        bg_form.addRow("輪郭を削る(px)", self.choke)

        stab_group = QGroupBox("ちらつき対策")
        stab_form = QFormLayout(stab_group)
        stab_form.addRow(self.stabilize)
        stab_form.addRow("強さ(margin)", self.stabilize_margin)

        gif_group = QGroupBox("GIF")
        gif_form = QFormLayout(gif_group)
        gif_form.addRow(self.gif)
        gif_form.addRow("fps", self.fps)
        gif_form.addRow(self.play_once)

        misc_group = QGroupBox("詳細")
        misc_form = QFormLayout(misc_group)
        misc_form.addRow(self.remove_isolated)
        misc_form.addRow("アウトライン", self.outline)
        misc_form.addRow("ディテール量", self.detail)

        for form in (io_form, size_form, framing_form, bg_form, stab_form, gif_form, misc_form):
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        for combo in self.findChildren(QComboBox):
            combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            combo.setMinimumContentsLength(10)
        settings = QWidget()
        settings_layout = QVBoxLayout(settings)
        for group in (io_group, size_group, framing_group, bg_group, stab_group, gif_group, misc_group):
            settings_layout.addWidget(group)
        settings_layout.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setWidget(settings)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMinimumWidth(470)
        scroll.setMaximumWidth(560)

        controls = QHBoxLayout()
        controls.addWidget(self.run_button, 1)
        controls.addWidget(self.cancel_button)
        playback = QHBoxLayout()
        playback.addWidget(self.play_button)
        playback.addWidget(self.frame_label, 1)
        buttons = QHBoxLayout()
        buttons.addWidget(self.open_folder_button)
        buttons.addWidget(self.open_gif_button)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.addWidget(self.preview, 1)
        right_layout.addLayout(playback)
        right_layout.addWidget(self.summary)
        right_layout.addLayout(buttons)
        right_layout.addLayout(controls)
        right_layout.addWidget(self.progress)
        right_layout.addWidget(self.status)

        central = QWidget()
        layout = QHBoxLayout(central)
        layout.addWidget(scroll, 0)
        layout.addWidget(right, 1)
        self.setCentralWidget(central)

    # ---- 入力 -------------------------------------------------------------------------
    def browse_input(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "PNG連番のフォルダを選択", self.input_field.text() or "")
        if directory:
            self.set_input_dir(Path(directory))

    def browse_output(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "出力先を選択", self.output_field.text() or "")
        if directory:
            self.output_field.setText(directory)
            self._output_touched = True

    def _on_output_edited(self, _text: str) -> None:
        self._output_touched = True

    def set_input_dir(self, directory: Path) -> bool:
        """フォルダを読み、枚数・サイズ・推定背景色を表示する。読めなければ理由を表示してFalse。"""
        directory = Path(directory)
        self.input_field.setText(str(directory))
        try:
            inspection = inspect_frame_directory(directory, tolerance=self.tolerance.value())
        except (OSError, ValueError) as exc:
            self._inspection = None
            self.input_info.setText(f"読み込めません: {exc}")
            self.preview.set_frames([])
            return False
        self._inspection = inspection
        width, height = inspection.frame_size
        lines = [f"{inspection.frame_count}枚 / {width}×{height}px"]
        if inspection.background_color is not None:
            mode = "画像内の背景色をすべて消す" if inspection.suggested_mode == "global" else "外周に繋がる部分だけ消す"
            lines.append(
                f"推定した背景色 {inspection.background_color}（外周の{inspection.border_coverage:.1%}が一致）→ 自動では「{mode}」"
            )
        elif inspection.background_error:
            lines.append(f"背景色を推定できません: {inspection.background_error}")
            self.background_color_auto.setChecked(False)
        self.input_info.setText("\n".join(lines))
        self.preview.set_frames([inspection.first_frame], play=False)
        self.frame_label.setText("")
        if not self._output_touched:
            self.output_field.setText(str(self._output_root / f"{directory.name}_frames"))
        return True

    # ---- 設定 -------------------------------------------------------------------------
    def _on_canvas_preset_changed(self) -> None:
        size = self.canvas_preset.currentData()
        if size is None:
            return
        for spin, value in ((self.canvas_width, size[0]), (self.canvas_height, size[1])):
            spin.blockSignals(True)
            spin.setValue(value)
            spin.blockSignals(False)

    def _on_canvas_spin_changed(self) -> None:
        current = (self.canvas_width.value(), self.canvas_height.value())
        for index in range(self.canvas_preset.count()):
            if self.canvas_preset.itemData(index) == current:
                self.canvas_preset.blockSignals(True)
                self.canvas_preset.setCurrentIndex(index)
                self.canvas_preset.blockSignals(False)
                return
        self.canvas_preset.blockSignals(True)
        self.canvas_preset.setCurrentIndex(self.canvas_preset.count() - 1)
        self.canvas_preset.blockSignals(False)

    def _update_enabled_states(self) -> None:
        idle = not self.is_running()
        mode = self.framing_mode.currentData()
        is_height = mode == "height"
        self.framing_mode.setEnabled(idle)
        self.fit_percentile.setEnabled(idle and mode == "percentile")
        for widget in (self.character_height, self.height_reference, self.canvas_auto, self.foot_lock):
            widget.setEnabled(idle and is_height)
        self.reference_frame.setEnabled(idle and is_height and self.height_reference.currentData() == "frame")
        auto = is_height and self.canvas_auto.isChecked()
        for widget in (self.canvas_preset, self.canvas_width, self.canvas_height):
            widget.setEnabled(idle and not auto)
        self.write_trimmed.setEnabled(idle)
        keyed = self.key_background.isChecked()
        for widget in (self.background_mode, self.background_color_auto, self.tolerance, self.choke):
            widget.setEnabled(keyed and not self.is_running())
        self.background_color.setEnabled(keyed and not self.background_color_auto.isChecked() and not self.is_running())
        self.stabilize_margin.setEnabled(self.stabilize.isChecked() and not self.is_running())
        self.fps.setEnabled(self.gif.isChecked() and not self.is_running())
        self.play_once.setEnabled(self.gif.isChecked() and not self.is_running())

    def build_request(self) -> FrameSequenceRequest:
        """画面の設定から実行内容を作る。入力が足りない・不正なときは理由つきのValueError。"""
        if not self.input_field.text():
            raise ValueError("PNG連番のフォルダを選んでください")
        if not self.output_field.text().strip():
            raise ValueError("出力先を指定してください")
        color = None
        if self.key_background.isChecked() and not self.background_color_auto.isChecked():
            color = self.background_color.text().strip() or None
            if color is None:
                raise ValueError("背景色を #RRGGBB で入力するか、自動推定にしてください")
        mode = self.framing_mode.currentData()
        reference: str = self.height_reference.currentData()
        if reference == "frame":
            reference = str(self.reference_frame.value())
        request = FrameSequenceRequest(
            input_dir=Path(self.input_field.text()),
            output_dir=Path(self.output_field.text().strip()),
            canvas_size=(self.canvas_width.value(), self.canvas_height.value()),
            palette_budget=self.palette.value(),
            fps=self.fps.value(),
            write_gif=self.gif.isChecked(),
            play_once=self.play_once.isChecked(),
            key_background=self.key_background.isChecked(),
            background_color=color,
            background_mode=self.background_mode.currentData(),
            background_tolerance=self.tolerance.value(),
            choke_px=self.choke.value(),
            stabilize_margin=self.stabilize_margin.value() if self.stabilize.isChecked() else 0.0,
            remove_isolated=self.remove_isolated.isChecked(),
            fit_percentile=self.fit_percentile.value() if mode == "percentile" else 100.0,
            character_height=self.character_height.value() if mode == "height" else None,
            height_reference=reference,
            canvas_auto=mode == "height" and self.canvas_auto.isChecked(),
            foot_lock=mode == "height" and self.foot_lock.isChecked(),
            write_trimmed=self.write_trimmed.isChecked(),
            outline=self.outline.currentData(),
            detail=self.detail.currentData(),
        )
        request.validate()
        return request

    # ---- 実行 -------------------------------------------------------------------------
    def is_running(self) -> bool:
        return self._thread is not None

    def _ask_overwrite(self, output_dir: Path) -> bool:
        answer = QMessageBox.question(
            self,
            "出力先を置き換えますか",
            f"{output_dir} には以前の出力があります。\n段階フォルダ・GIF・レポートを新しい結果に置き換えます（それ以外のファイルは残します）。",
        )
        return answer == QMessageBox.StandardButton.Yes

    def start_run(self) -> None:
        if self.is_running():
            return
        try:
            request = self.build_request()
        except ValueError as exc:
            self.status.setText(f"実行できません: {exc}")
            return
        if (request.output_dir / "final_frames").exists() and not self.confirm_overwrite(request.output_dir):
            self.status.setText("中止しました（出力先は変更していません）")
            return
        self._cancel_event.clear()
        self._summary = None
        self._worker = FrameSequenceWorker(request, self._cancel_event)
        self._thread = QThread(self)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        self._worker.failed.connect(self._on_failed)
        self._worker.cancelled.connect(self._on_cancelled)
        self.preview.pause()
        self.progress.setRange(0, 0)
        self.status.setText("処理を開始しました…")
        self.run_button.setEnabled(False)
        self.cancel_button.setEnabled(True)
        self._update_enabled_states()
        self._thread.start()

    def cancel_run(self) -> None:
        if self.is_running():
            self._cancel_event.set()
            self.cancel_button.setEnabled(False)
            self.status.setText("中止しています…（次のフレームの処理が終わり次第止まります）")

    @Slot(str, int, int)
    def _on_progress(self, stage: str, done: int, total: int) -> None:
        if stage == "compile":
            self.progress.setRange(0, max(1, total))
            self.progress.setValue(done)
            self.status.setText(f"コンパイル中… {done}/{total}フレーム")
        else:
            self.progress.setRange(0, 0)
            self.status.setText(STAGE_LABELS.get(stage, stage))

    def _finish_thread(self) -> None:
        thread, worker = self._thread, self._worker
        self._thread = None
        self._worker = None
        if thread is not None:
            thread.quit()
            thread.wait(5000)
            thread.deleteLater()
        if worker is not None:
            worker.deleteLater()
        self.progress.setRange(0, 1)
        self.progress.setValue(0)
        self.run_button.setEnabled(True)
        self.cancel_button.setEnabled(False)
        self._update_enabled_states()

    @Slot(object)
    def _on_finished(self, summary: FrameSequenceSummary) -> None:
        self._finish_thread()
        self._summary = summary
        self.progress.setRange(0, 1)
        self.progress.setValue(1)
        lines = [
            f"{summary.frame_count}フレーム / {summary.canvas_size[0]}×{summary.canvas_size[1]}px"
            f"（元絵に対する倍率 {summary.scale:.3f}）/ パレット{summary.palette_colors}色"
        ]
        if summary.gif_path is not None and summary.gif_bytes is not None:
            lines.append(f"GIF {summary.gif_bytes / 1024:.0f}KB / {(summary.gif_total_ms or 0) / 1000:.2f}秒（書き出し後に再読込して画素一致を検証済み）")
        if summary.background_color:
            lines.append(f"背景色 {summary.background_color}（{summary.background_mode}）")
        if summary.trimmed:
            lines.append("切り詰め画像＋オフセット: trimmed_frames/ と trim_manifest.json を出力")
        if summary.swapped_px is not None:
            lines.append(f"色の安定化: {summary.swapped_px:,}画素を前フレームの色に揃えた")
        lines.extend(f"⚠ {warning}" for warning in summary.warnings)
        self.summary.setText("\n".join(lines))
        self.status.setText(f"完了: {summary.output_root}")
        fps = self.fps.value() if self.gif.isChecked() else 24.0
        self.preview.set_frames(list(summary.final_frame_paths), fps)
        self.play_button.setEnabled(summary.frame_count > 1)
        self.play_button.setText("一時停止")
        self.open_folder_button.setEnabled(True)
        self.open_gif_button.setEnabled(summary.gif_path is not None)

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self._finish_thread()
        self.status.setText(f"失敗しました: {message}")

    @Slot()
    def _on_cancelled(self) -> None:
        self._finish_thread()
        self.status.setText("中止しました（出力先は変更していません）")

    # ---- 結果 -------------------------------------------------------------------------
    def _on_play_clicked(self) -> None:
        self.play_button.setText("一時停止" if self.preview.toggle() else "再生")

    def _on_preview_frame(self, index: int) -> None:
        self.frame_label.setText(f"{index + 1}/{self.preview.frame_count}")

    def open_output_folder(self) -> None:
        if self._summary is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._summary.output_root)))

    def open_gif(self) -> None:
        if self._summary is not None and self._summary.gif_path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._summary.gif_path)))

    def closeEvent(self, event) -> None:  # type: ignore[no-untyped-def]
        if self.is_running():
            self._cancel_event.set()
            if self._thread is not None:
                self._thread.quit()
                self._thread.wait(15000)
        self.preview.pause()
        event.accept()


__all__ = ["FrameAnimationPreview", "FrameSequenceWindow", "FrameSequenceWorker"]
