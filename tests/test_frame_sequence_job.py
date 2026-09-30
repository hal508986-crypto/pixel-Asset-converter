from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from pixel_tile_compiler.pixelizer.frame_sequence_job import (
    FrameSequenceCancelled,
    FrameSequenceRequest,
    inspect_frame_directory,
    run_frame_sequence,
)

SIZE = 96


def _write_sequence(directory: Path, count: int = 5, background=(92, 156, 155)) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(4)
    for index in range(count):
        array = np.zeros((SIZE, SIZE, 4), np.uint8)
        array[:, :, :3] = np.clip(np.array(background) + rng.integers(-2, 3, (SIZE, SIZE, 3)), 0, 255)
        array[:, :, 3] = 255
        top = 30 - (index % 3) * 5
        array[top:top + 30, 40:56, :3] = (190, 50, 60)
        array[top - 10:top, 43:53, :3] = (240, 200, 70)
        array[top + 30:SIZE - 14, 42 + index % 3:48 + index % 3, :3] = (60, 80, 200)
        Image.fromarray(array, "RGBA").save(directory / f"frame_{index + 1:05d}_.png")


def _request(tmp_path: Path, **overrides) -> FrameSequenceRequest:
    settings = {"input_dir": tmp_path / "in", "output_dir": tmp_path / "out", "canvas_size": (64, 64), **overrides}
    return FrameSequenceRequest(**settings)


def test_defaults_match_the_recommended_video_setup(tmp_path: Path) -> None:
    request = _request(tmp_path)
    assert request.stabilize_margin == 12.0 and request.background_mode == "auto"
    assert request.fps == 24.0 and request.write_gif and request.key_background
    config = replace(request, canvas_size=(512, 512)).to_config()
    assert config.canvas_size == (512, 512) and config.placement_mode == "preserve_motion"


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"palette_budget": 2}, "パレット上限"),
        ({"fps": 500.0}, "fps"),
        ({"canvas_size": (0, 64)}, "出力サイズ"),
        ({"canvas_size": (9999, 64)}, "出力サイズ"),
        ({"background_mode": "nope"}, "背景の消し方"),
        ({"background_tolerance": 300}, "許容差"),
        ({"choke_px": -1}, "輪郭"),
        ({"stabilize_margin": -1.0}, "安定化"),
        ({"outline": "red"}, "outline"),
        ({"detail": "x"}, "character_detail"),
        ({"background_color": "green"}, "#RRGGBB"),
        ({"scale": 0.0}, "倍率"),
        ({"loop": -1}, "ループ"),
    ],
)
def test_validate_rejects_bad_settings_with_actionable_messages(tmp_path: Path, overrides, message) -> None:
    _write_sequence(tmp_path / "in")
    with pytest.raises(ValueError, match=message):
        _request(tmp_path, **overrides).validate()
    assert not (tmp_path / "out").exists()


def test_validate_rejects_missing_input_and_output_equal_to_input(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="入力フォルダが見つかりません"):
        _request(tmp_path).validate()
    _write_sequence(tmp_path / "in")
    with pytest.raises(ValueError, match="別に"):
        FrameSequenceRequest(input_dir=tmp_path / "in", output_dir=tmp_path / "in").validate()
    _request(tmp_path, fps=500.0, write_gif=False).validate()  # GIFを作らないならfpsは検査しない


def test_inspect_reports_count_size_and_estimated_background(tmp_path: Path) -> None:
    _write_sequence(tmp_path / "in", count=4)
    inspection = inspect_frame_directory(tmp_path / "in")
    assert inspection.frame_count == 4 and inspection.frame_size == (SIZE, SIZE)
    assert inspection.first_frame.name == "frame_00001_.png"
    assert inspection.background_color is not None and inspection.border_coverage > 0.9
    assert inspection.suggested_mode == "global" and inspection.background_error is None
    white = tmp_path / "white"
    _write_sequence(white, background=(255, 255, 255))
    assert inspect_frame_directory(white).suggested_mode == "connected"


def test_inspect_keeps_going_when_the_background_cannot_be_estimated(tmp_path: Path) -> None:
    (tmp_path / "noisy").mkdir()
    rng = np.random.default_rng(0)
    Image.fromarray(rng.integers(0, 256, (SIZE, SIZE, 4), dtype=np.uint8), "RGBA").save(tmp_path / "noisy" / "a.png")
    inspection = inspect_frame_directory(tmp_path / "noisy")
    assert inspection.frame_count == 1 and inspection.background_color is None
    assert "背景色を明示指定" in inspection.background_error
    assert inspect_frame_directory(tmp_path / "noisy", estimate_background=False).background_error is None
    with pytest.raises(ValueError, match="PNG連番が見つかりません"):
        (tmp_path / "empty").mkdir()
        inspect_frame_directory(tmp_path / "empty")


def test_run_returns_a_summary_and_reports_progress_in_order(tmp_path: Path) -> None:
    _write_sequence(tmp_path / "in")
    events: list[tuple[str, int, int]] = []
    summary = run_frame_sequence(_request(tmp_path), progress=lambda *event: events.append(event))
    assert summary.frame_count == 5 and summary.final_dir == tmp_path / "out" / "final_frames"
    assert summary.gif_path == tmp_path / "out" / "animation.gif" and summary.gif_bytes > 0
    assert summary.gif_total_ms == 210 and summary.palette_colors <= 24  # 5枚@24fps
    assert summary.background_mode == "global" and summary.background_color.startswith("#")
    assert summary.swapped_px is not None and summary.sheets_written is True
    stages = [stage for stage, _, _ in events]
    assert stages[:2] == ["load", "key"] and stages[-1] == "finalize"
    assert [(d, t) for s, d, t in events if s == "compile"] == [(i, 5) for i in range(1, 6)]
    off = run_frame_sequence(replace(_request(tmp_path), output_dir=tmp_path / "off", stabilize_margin=0.0, write_gif=False))
    assert off.swapped_px is None and off.gif_path is None and off.gif_total_ms is None


def test_cancelling_midway_leaves_existing_output_untouched(tmp_path: Path) -> None:
    _write_sequence(tmp_path / "in")
    run_frame_sequence(_request(tmp_path))
    before = {p.name: p.read_bytes() for p in sorted((tmp_path / "out" / "final_frames").glob("*.png"))}
    gif_before = (tmp_path / "out" / "animation.gif").read_bytes()

    def cancel_on_third_frame(stage: str, done: int, total: int) -> None:
        if stage == "compile" and done == 3:
            raise FrameSequenceCancelled()

    with pytest.raises(FrameSequenceCancelled):
        run_frame_sequence(_request(tmp_path, palette_budget=8), progress=cancel_on_third_frame)
    after = {p.name: p.read_bytes() for p in sorted((tmp_path / "out" / "final_frames").glob("*.png"))}
    assert after == before and (tmp_path / "out" / "animation.gif").read_bytes() == gif_before
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".out.staging")]  # 一時領域も残らない
    # 出力がまだ無い状態で中止しても、出力フォルダは作られない
    with pytest.raises(FrameSequenceCancelled):
        run_frame_sequence(replace(_request(tmp_path), output_dir=tmp_path / "fresh"), progress=cancel_on_third_frame)
    assert not (tmp_path / "fresh").exists()


def test_run_rejects_invalid_settings_before_touching_anything(tmp_path: Path) -> None:
    _write_sequence(tmp_path / "in")
    with pytest.raises(ValueError, match="パレット上限"):
        run_frame_sequence(_request(tmp_path, palette_budget=99))
    assert not (tmp_path / "out").exists()
