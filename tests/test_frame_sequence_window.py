from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

pytest.importorskip("PySide6")

SIZE = 96


@pytest.fixture()
def qt(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def _write_sequence(directory: Path, count: int = 5, background=(92, 156, 155)) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(6)
    for index in range(count):
        array = np.zeros((SIZE, SIZE, 4), np.uint8)
        array[:, :, :3] = np.clip(np.array(background) + rng.integers(-2, 3, (SIZE, SIZE, 3)), 0, 255)
        array[:, :, 3] = 255
        top = 30 - (index % 3) * 5
        array[top:top + 30, 40:56, :3] = (190, 50, 60)
        array[top - 10:top, 43:53, :3] = (240, 200, 70)
        array[top + 30:SIZE - 14, 42 + index % 3:48 + index % 3, :3] = (60, 80, 200)
        Image.fromarray(array, "RGBA").save(directory / f"frame_{index + 1:05d}_.png")
    return directory


def _wait_until_idle(app, window, timeout: float = 90.0) -> None:
    deadline = time.time() + timeout
    while window.is_running() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    assert not window.is_running(), "処理が終わりませんでした"


def _window(qt, tmp_path: Path):
    from pixel_tile_compiler.gui.frame_sequence_window import FrameSequenceWindow

    window = FrameSequenceWindow(output_root=tmp_path / "output")
    window.show()
    qt.processEvents()
    return window


def test_defaults_are_the_recommended_video_setup(qt, tmp_path: Path) -> None:
    window = _window(qt, tmp_path)
    try:
        assert (window.canvas_width.value(), window.canvas_height.value()) == (512, 512)
        assert window.palette.value() == 24 and window.fps.value() == 24.0
        assert window.key_background.isChecked() and window.background_color_auto.isChecked()
        assert window.background_mode.currentData() == "auto"
        assert window.stabilize.isChecked() and window.stabilize_margin.value() == 12.0
        assert window.gif.isChecked() and not window.play_once.isChecked()
        assert not window.background_color.isEnabled()  # 自動推定中は手入力できない
        assert not window.cancel_button.isEnabled() and not window.open_folder_button.isEnabled()
    finally:
        window.close()


def test_choosing_a_folder_shows_count_size_and_estimated_background(qt, tmp_path: Path) -> None:
    window = _window(qt, tmp_path)
    try:
        assert window.set_input_dir(_write_sequence(tmp_path / "walk"))
        text = window.input_info.text()
        assert "5枚" in text and "96×96" in text and "#" in text and "画像内の背景色をすべて消す" in text
        assert window.output_field.text() == str(tmp_path / "output" / "walk_frames")
        assert window.preview.frame_count == 1 and not window.preview.is_playing
        # 出力先を手で直したあとは、フォルダを選び直しても上書きしない
        window.output_field.textEdited.emit("x")
        window.output_field.setText(str(tmp_path / "mine"))
        window.set_input_dir(_write_sequence(tmp_path / "run"))
        assert window.output_field.text() == str(tmp_path / "mine")
        white = _write_sequence(tmp_path / "white", background=(255, 255, 255))
        window.set_input_dir(white)
        assert "外周に繋がる部分だけ消す" in window.input_info.text()
    finally:
        window.close()


def test_unreadable_or_unestimatable_folders_explain_what_to_do(qt, tmp_path: Path) -> None:
    window = _window(qt, tmp_path)
    try:
        (tmp_path / "empty").mkdir()
        assert not window.set_input_dir(tmp_path / "empty")
        assert "PNG連番が見つかりません" in window.input_info.text()
        noisy = tmp_path / "noisy"
        noisy.mkdir()
        rng = np.random.default_rng(0)
        Image.fromarray(rng.integers(0, 256, (SIZE, SIZE, 4), dtype=np.uint8), "RGBA").save(noisy / "a.png")
        assert window.set_input_dir(noisy)
        assert "背景色を明示指定" in window.input_info.text()
        assert not window.background_color_auto.isChecked() and window.background_color.isEnabled()
    finally:
        window.close()


def test_canvas_preset_and_size_fields_stay_in_sync(qt, tmp_path: Path) -> None:
    window = _window(qt, tmp_path)
    try:
        window.canvas_preset.setCurrentIndex(2)  # 128
        assert (window.canvas_width.value(), window.canvas_height.value()) == (128, 128)
        window.canvas_width.setValue(200)
        assert window.canvas_preset.currentData() is None  # カスタム
        window.canvas_width.setValue(64)
        window.canvas_height.setValue(64)
        assert window.canvas_preset.currentData() == (64, 64)
    finally:
        window.close()


def test_dependent_controls_follow_their_checkboxes(qt, tmp_path: Path) -> None:
    window = _window(qt, tmp_path)
    try:
        window.key_background.setChecked(False)
        assert not any(w.isEnabled() for w in (window.background_mode, window.tolerance, window.choke, window.background_color))
        window.key_background.setChecked(True)
        window.background_color_auto.setChecked(False)
        assert window.background_color.isEnabled()
        window.stabilize.setChecked(False)
        assert not window.stabilize_margin.isEnabled()
        window.gif.setChecked(False)
        assert not window.fps.isEnabled() and not window.play_once.isEnabled()
    finally:
        window.close()


def test_build_request_maps_every_setting_and_explains_missing_input(qt, tmp_path: Path) -> None:
    window = _window(qt, tmp_path)
    try:
        with pytest.raises(ValueError, match="フォルダを選んでください"):
            window.build_request()
        window.set_input_dir(_write_sequence(tmp_path / "in"))
        window.canvas_preset.setCurrentIndex(3)
        window.palette.setValue(16)
        window.fps.setValue(12.0)
        window.play_once.setChecked(True)
        window.tolerance.setValue(45)
        window.choke.setValue(1)
        window.background_mode.setCurrentIndex(2)
        window.stabilize.setChecked(False)
        window.outline.setCurrentIndex(1)
        window.detail.setCurrentIndex(2)
        window.remove_isolated.setChecked(False)
        request = window.build_request()
        assert request.canvas_size == (64, 64) and request.palette_budget == 16 and request.fps == 12.0
        assert request.play_once and request.background_tolerance == 45 and request.choke_px == 1
        assert request.background_mode == "global" and request.stabilize_margin == 0.0
        assert request.outline == "black" and request.detail == "detailed"
        assert request.background_color is None and not request.remove_isolated
        window.background_color_auto.setChecked(False)
        with pytest.raises(ValueError, match="背景色を #RRGGBB"):
            window.build_request()
        window.background_color.setText("green")
        with pytest.raises(ValueError, match="#RRGGBB"):
            window.build_request()
        window.background_color.setText("#5C9C9B")
        assert window.build_request().background_color == "#5C9C9B"
    finally:
        window.close()


def test_run_produces_outputs_and_plays_the_result(qt, tmp_path: Path) -> None:
    window = _window(qt, tmp_path)
    try:
        window.set_input_dir(_write_sequence(tmp_path / "in"))
        window.canvas_preset.setCurrentIndex(3)  # 64
        window.start_run()
        assert window.is_running() and not window.run_button.isEnabled() and window.cancel_button.isEnabled()
        assert not window.tolerance.isEnabled()  # 実行中は設定を触れない
        _wait_until_idle(qt, window)
        out = tmp_path / "output" / "in_frames"
        assert (out / "animation.gif").exists() and len(list((out / "final_frames").glob("*.png"))) == 5
        assert "5フレーム" in window.summary.text() and "検証済み" in window.summary.text()
        assert "完了" in window.status.text() and window.run_button.isEnabled() and not window.cancel_button.isEnabled()
        assert window.preview.frame_count == 5 and window.preview.is_playing
        assert window.open_folder_button.isEnabled() and window.open_gif_button.isEnabled()
        assert window.tolerance.isEnabled()
        window.play_button.click()
        assert not window.preview.is_playing and window.play_button.text() == "再生"
        window.play_button.click()
        assert window.preview.is_playing
    finally:
        window.close()


def test_cancel_stops_without_leaving_output(qt, tmp_path: Path) -> None:
    window = _window(qt, tmp_path)
    try:
        window.set_input_dir(_write_sequence(tmp_path / "in", count=8))
        window.canvas_preset.setCurrentIndex(3)
        window.start_run()
        window.cancel_run()
        _wait_until_idle(qt, window)
        assert "中止しました" in window.status.text()
        assert not (tmp_path / "output" / "in_frames").exists()
        assert window.run_button.isEnabled() and not window.open_folder_button.isEnabled()
    finally:
        window.close()


def test_existing_output_needs_confirmation_and_declining_changes_nothing(qt, tmp_path: Path) -> None:
    window = _window(qt, tmp_path)
    try:
        window.set_input_dir(_write_sequence(tmp_path / "in"))
        window.canvas_preset.setCurrentIndex(3)
        window.start_run()
        _wait_until_idle(qt, window)
        out = tmp_path / "output" / "in_frames"
        before = (out / "animation.gif").read_bytes()
        asked: list[Path] = []
        window.confirm_overwrite = lambda path: asked.append(path) or False
        window.fps.setValue(12.0)
        window.start_run()
        assert asked == [out] and not window.is_running()
        assert "変更していません" in window.status.text() and (out / "animation.gif").read_bytes() == before
        window.confirm_overwrite = lambda path: True
        window.start_run()
        _wait_until_idle(qt, window)
        assert (out / "animation.gif").read_bytes() != before  # 置き換わった
    finally:
        window.close()


def test_invalid_settings_and_failures_are_shown_not_raised(qt, tmp_path: Path) -> None:
    window = _window(qt, tmp_path)
    try:
        window.start_run()
        assert "実行できません" in window.status.text() and not window.is_running()
        source = _write_sequence(tmp_path / "in")
        window.set_input_dir(source)
        for path in source.glob("*.png"):
            path.unlink()
        window.start_run()
        _wait_until_idle(qt, window)
        assert "失敗しました" in window.status.text() and "PNG連番が見つかりません" in window.status.text()
        assert window.run_button.isEnabled()
    finally:
        window.close()


def test_preview_uses_the_same_frame_timing_as_the_gif(qt, tmp_path: Path) -> None:
    from pixel_tile_compiler.gui.frame_sequence_window import FrameAnimationPreview

    paths = []
    for index in range(4):
        path = tmp_path / f"F{index}.png"
        Image.new("RGBA", (8, 8), (index * 40, 0, 0, 255)).save(path)
        paths.append(path)
    preview = FrameAnimationPreview()
    preview.set_frames(paths, fps=24, play=False)
    assert preview._durations == (40, 40, 50, 40) and not preview.is_playing
    preview.set_frames(paths, fps=24)
    assert preview.is_playing
    preview.pause()
    preview.set_frames([], play=True)
    assert preview.frame_count == 0 and not preview.is_playing
    preview.set_frames([tmp_path / "missing.png"], play=False)
    preview.grab()  # 画像が無くても描画で落ちない


def test_main_window_offers_the_frame_sequence_window_for_character_purposes_only(qt, tmp_path: Path) -> None:
    from pixel_tile_compiler.gui.frame_sequence_window import FrameSequenceWindow
    from pixel_tile_compiler.gui.main_window import MainWindow

    window = MainWindow()
    window.show()
    qt.processEvents()
    try:
        window.purpose.setCurrentIndex(window.purpose.findData("character_animation"))
        qt.processEvents()
        assert window.frame_sequence_button.isVisible()
        window.purpose.setCurrentIndex(window.purpose.findData("terrain"))
        qt.processEvents()
        assert not window.frame_sequence_button.isVisible() and window.terrain_batch_button.isVisible()
        window.output_root_field.setText(str(tmp_path / "saved"))
        window.open_frame_sequence()
        assert isinstance(window._frame_sequence_window, FrameSequenceWindow)
        assert window._frame_sequence_window.isVisible()
        assert window._frame_sequence_window.output_field.text() == str(tmp_path / "saved")
        first = window._frame_sequence_window
        window.open_frame_sequence()
        assert window._frame_sequence_window is first  # 使い回す
        first.close()
    finally:
        window.close()


def test_framing_controls_follow_the_mode_and_map_to_the_request(qt, tmp_path: Path) -> None:
    window = _window(qt, tmp_path)
    try:
        window.set_input_dir(_write_sequence(tmp_path / "in"))
        # 既定は従来どおり（全フレームが収まる）
        request = window.build_request()
        assert request.fit_percentile == 100.0 and request.character_height is None and not request.canvas_auto
        assert not window.fit_percentile.isEnabled() and not window.character_height.isEnabled()
        assert window.canvas_width.isEnabled() and window.write_trimmed.isChecked()
        # 上位N%
        window.framing_mode.setCurrentIndex(1)
        assert window.fit_percentile.isEnabled() and not window.character_height.isEnabled()
        window.fit_percentile.setValue(70)
        assert window.build_request().fit_percentile == 70.0
        # 身長指定 + Canvas自動: 幅・高さは無効になる
        window.framing_mode.setCurrentIndex(2)
        assert window.character_height.isEnabled() and window.height_reference.isEnabled()
        assert not window.fit_percentile.isEnabled() and not window.reference_frame.isEnabled()
        assert window.canvas_auto.isChecked() and not window.canvas_width.isEnabled() and not window.canvas_preset.isEnabled()
        window.character_height.setValue(150)
        request = window.build_request()
        assert request.character_height == 150.0 and request.canvas_auto and request.height_reference == "median"
        assert request.fit_percentile == 100.0
        # Canvas固定に戻すと幅・高さが使える
        window.canvas_auto.setChecked(False)
        assert window.canvas_width.isEnabled() and not window.build_request().canvas_auto
        # 基準はフレーム番号でも指定できる
        window.height_reference.setCurrentIndex(2)
        assert window.reference_frame.isEnabled()
        window.reference_frame.setValue(3)
        assert window.build_request().height_reference == "3"
        window.height_reference.setCurrentIndex(1)
        assert window.build_request().height_reference == "first"
        window.write_trimmed.setChecked(False)
        assert window.build_request().write_trimmed is False
    finally:
        window.close()


def test_run_with_character_height_and_auto_canvas_reports_the_output_size(qt, tmp_path: Path) -> None:
    window = _window(qt, tmp_path)
    try:
        window.set_input_dir(_write_sequence(tmp_path / "in"))
        window.framing_mode.setCurrentIndex(2)
        window.character_height.setValue(40)
        window.tolerance.setValue(45)
        window.start_run()
        assert not window.framing_mode.isEnabled() and not window.character_height.isEnabled()  # 実行中は触れない
        _wait_until_idle(qt, window)
        out = tmp_path / "output" / "in_frames"
        report = json.loads((out / "bbox_report.json").read_text(encoding="utf-8"))
        width, height = report["output_frame_size"]
        assert f"{width}×{height}px" in window.summary.text() and "倍率" in window.summary.text()
        assert "切り詰め画像＋オフセット" in window.summary.text()
        assert (out / "trim_manifest.json").exists() and len(list((out / "trimmed_frames").glob("*.png"))) == 5
        assert window.framing_mode.isEnabled() and window.character_height.isEnabled()
        assert report["framing"]["mode"] == "character_height" and report["framing"]["canvas_auto"] is True
    finally:
        window.close()
