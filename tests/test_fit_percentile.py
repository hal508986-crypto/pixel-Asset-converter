from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from typer.testing import CliRunner

from pixel_tile_compiler.cli import app
from pixel_tile_compiler.pixelizer.character_animation import (
    CharacterAnimationConfig,
    align_character_frames,
)
from pixel_tile_compiler.pixelizer.character_frame_sequence import (
    _with_derived_origins,
    compile_character_frame_directory,
    default_sequence_config,
)
from pixel_tile_compiler.pixelizer.frame_sequence_job import FrameSequenceRequest

SIZE = 200
BG = (92, 156, 155)


def _body_frames(count: int = 10, outlier_index: int | None = 3) -> list[Image.Image]:
    """中央に小さなキャラ（幅24・高さ50）。outlier_indexのコマだけ、槍が左へ大きく突き出す。"""
    frames = []
    for index in range(count):
        array = np.zeros((SIZE, SIZE, 4), np.uint8)
        array[120:170, 88:112] = (190, 50, 60, 255)  # 本体
        if index == outlier_index:
            array[140:146, 5:88] = (60, 60, 200, 255)  # 左へ伸びる長い槍
        frames.append(Image.fromarray(array, "RGBA"))
    return frames


def _config(canvas: int = 64) -> CharacterAnimationConfig:
    return default_sequence_config((canvas, canvas))


def test_derivation_with_percentile_100_is_the_previous_behaviour() -> None:
    frames = tuple(_body_frames())
    base = _with_derived_origins(_config(), frames)
    same = _with_derived_origins(_config(), frames, 100.0)
    assert base == same and base.scale_override is None and base.allow_clipping is False
    union_center = (5 + 112) / 2
    assert base.source_origin == (union_center, 170.0)  # 槍に引っ張られた中心


def test_percentile_below_100_enlarges_the_body_and_allows_clipping() -> None:
    frames = tuple(_body_frames())
    whole = _with_derived_origins(_config(), frames, 100.0)
    typical = _with_derived_origins(_config(), frames, 80.0)
    assert typical.allow_clipping is True
    assert typical.source_origin[0] == 100.0  # 各フレームの中心の中央値=キャラ本体の位置
    # 100%だと槍のせいで倍率が決まる（自動フィット）。80%は本体基準で、明示倍率になる
    fit_scale_100 = align_character_frames(list(frames), replace(whole, frame_count=len(frames))).scale
    assert typical.scale_override is not None and typical.scale_override > fit_scale_100 * 1.5
    # 上位に入れる割合が下がるほど、倍率は下がらない（単調）
    scales = [_with_derived_origins(_config(), frames, p).scale_override for p in (95, 80, 50)]
    assert scales == sorted(scales)


def test_explicit_scale_is_kept_and_invalid_percentiles_are_rejected() -> None:
    frames = tuple(_body_frames())
    kept = _with_derived_origins(replace(_config(), scale_override=0.9), frames, 70.0)
    assert kept.scale_override == 0.9 and kept.allow_clipping is True
    for bad in (0, -5, 101):
        with pytest.raises(ValueError, match="fit_percentile"):
            _with_derived_origins(_config(), frames, bad)
    assert _with_derived_origins(replace(_config(), source_origin=(1.0, 2.0), output_origin=(3.0, 4.0)), frames, 50.0).scale_override is None


def test_core_reports_clipped_frames_and_only_when_clipping_is_allowed() -> None:
    frames = _body_frames()
    config = replace(
        _config(), frame_count=len(frames), source_origin=(100.0, 170.0), output_origin=(32.0, 62.0),
        scale_override=0.5, remove_isolated_components=False,
    )
    with pytest.raises(ValueError, match="見切れ"):
        align_character_frames(frames, config)
    result = align_character_frames(frames, replace(config, allow_clipping=True))
    flags = [report.clipped for report in result.frame_reports]
    assert flags == [False, False, False, True] + [False] * 6
    assert any(warning.startswith("F4:") and "left" in warning for warning in result.warnings)
    assert "allow_clipping" not in config.as_dict()  # 既定では既存のレポートに項目を足さない
    assert replace(config, allow_clipping=True).as_dict()["allow_clipping"] is True


def _write(directory: Path, frames: list[Image.Image]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(3)
    for index, frame in enumerate(frames):
        array = np.asarray(frame).copy()
        background = np.clip(np.array(BG) + rng.integers(-2, 3, (SIZE, SIZE, 3)), 0, 255).astype(np.uint8)
        opaque = array[:, :, 3] > 0
        out = np.dstack([background, np.full((SIZE, SIZE), 255, np.uint8)])
        out[opaque] = array[opaque]
        Image.fromarray(out, "RGBA").save(directory / f"f{index:03d}.png")
    return directory


def test_pipeline_makes_the_body_bigger_and_flags_only_the_extreme_frame(tmp_path: Path) -> None:
    source = _write(tmp_path / "in", _body_frames())
    kwargs = dict(canvas_size=(64, 64), background_tolerance=45, gif_fps=None)
    whole = compile_character_frame_directory(source, tmp_path / "whole", **kwargs)
    close = compile_character_frame_directory(source, tmp_path / "close", fit_percentile=80, **kwargs)
    whole_report = json.loads(whole.report_path.read_text(encoding="utf-8"))
    close_report = json.loads(close.report_path.read_text(encoding="utf-8"))
    assert close_report["common_scale"] > whole_report["common_scale"] * 1.5
    assert close_report["framing"] == {"fit_percentile": 80, "clipping_allowed": True}
    assert whole_report["framing"] == {"fit_percentile": 100.0, "clipping_allowed": False}
    clipped = [f["frame_id"] for f in close_report["frames"] if f.get("clipped")]
    assert clipped == ["F004"] and any("F004" in warning and "見切れ" in warning for warning in close.warnings)
    assert not [f for f in whole_report["frames"] if f.get("clipped")]

    def body_height(result) -> int:
        alpha = np.asarray(Image.open(result.final_frame_paths[0]).convert("RGBA"))[..., 3] > 0
        rows = np.nonzero(alpha.any(axis=1))[0]
        return int(rows.max() - rows.min() + 1)

    assert body_height(close) > body_height(whole) * 1.5  # キャラ本体が大きく写る


def test_job_and_cli_expose_the_setting(tmp_path: Path) -> None:
    source = _write(tmp_path / "in", _body_frames())
    with pytest.raises(ValueError, match="百分位"):
        FrameSequenceRequest(input_dir=source, output_dir=tmp_path / "o", fit_percentile=0).validate()
    with pytest.raises(ValueError, match="百分位"):
        FrameSequenceRequest(input_dir=source, output_dir=tmp_path / "o", fit_percentile=150).validate()
    assert FrameSequenceRequest(input_dir=source, output_dir=tmp_path / "o").fit_percentile == 100.0
    result = CliRunner().invoke(app, [
        "compile-character-frames", str(source), "-o", str(tmp_path / "cli"), "--width", "64", "--height", "64",
        "--background-tolerance", "45", "--no-gif", "--fit-percentile", "80",
    ])
    assert result.exit_code == 0, result.output
    assert "見切れ" in result.output  # 見切れたコマは警告で分かる
    report = json.loads((tmp_path / "cli" / "bbox_report.json").read_text(encoding="utf-8"))
    assert report["framing"]["fit_percentile"] == 80.0
    bad = CliRunner().invoke(app, ["compile-character-frames", str(source), "-o", str(tmp_path / "x"), "--fit-percentile", "0"])
    assert bad.exit_code != 0
