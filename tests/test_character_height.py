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
    _write_trimmed_frames,
)
from pixel_tile_compiler.pixelizer.character_body import (
    body_kernel_size,
    measure_body,
    reference_body_height,
)
from pixel_tile_compiler.pixelizer.character_frame_sequence import (
    _with_character_height,
    compile_character_frame_directory,
    default_sequence_config,
)
from pixel_tile_compiler.pixelizer.frame_sequence_job import FrameSequenceRequest

SIZE = 200
BG = (92, 156, 155)
FOOT = 170


def _mask_frame(top: int = 100, spear: bool = False) -> Image.Image:
    array = np.zeros((SIZE, SIZE, 4), np.uint8)
    array[top:FOOT, 88:112] = (190, 50, 60, 255)  # 本体（幅24）
    if spear:
        array[140:142, 5:88] = (60, 60, 200, 255)  # 2pxの細い槍が左へ長く伸びる（この画像の開き処理の直径3pxで消える細さ）
    return Image.fromarray(array, "RGBA")


# 立ち(高さ70)が6コマ、しゃがみ(高さ45)が2コマ、槍を突き出すコマが1つ
def _frames() -> list[Image.Image]:
    return [_mask_frame(100), _mask_frame(100), _mask_frame(100), _mask_frame(100),
            _mask_frame(125), _mask_frame(125), _mask_frame(100), _mask_frame(100, spear=True), _mask_frame(100)]


def test_measure_body_ignores_thin_parts_and_handles_edge_cases() -> None:
    with_spear = np.asarray(_mask_frame(100, spear=True))[..., 3]
    plain = np.asarray(_mask_frame(100))[..., 3]
    assert measure_body(plain) == (88, 100, 112, FOOT)
    left, top, right, bottom = measure_body(with_spear)  # type: ignore[misc]
    assert (top, right, bottom) == (100, 112, FOOT) and 86 <= left <= 88  # 槍の先(x=5)は除かれ、根元の1px程度だけが残る
    assert measure_body(np.zeros((40, 40), np.uint8)) is None
    tiny = np.zeros((200, 200), np.uint8)
    tiny[50:52, 50:52] = 255  # 開き処理で消える小ささ → 可視全体にフォールバック
    assert measure_body(tiny) == (50, 50, 52, 52)
    assert body_kernel_size((512, 512)) == 9 and body_kernel_size((64, 64)) == 3 and body_kernel_size((100, 300)) % 2 == 1


def test_reference_body_height_modes_and_errors() -> None:
    heights = [70.0, 70.0, 45.0, 90.0, 70.0]
    assert reference_body_height(heights) == 70.0
    assert reference_body_height(heights, "first") == 70.0
    assert reference_body_height(heights, 4) == 90.0
    for bad in (0, 6, "last", 2.5):
        with pytest.raises(ValueError):
            reference_body_height(heights, bad)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="身長を測れる"):
        reference_body_height([])


def _config(canvas: int = 128) -> CharacterAnimationConfig:
    return default_sequence_config((canvas, canvas))


def test_scale_comes_from_the_body_height_and_the_feet_are_fixed() -> None:
    frames = tuple(_frames())
    config, framing = _with_character_height(_config(), frames, 35.0, "median", canvas_auto=False)
    assert config.scale_override == pytest.approx(35 / 70)  # 中央値70（しゃがみの45に引っ張られない）
    assert config.source_origin == (100.0, float(FOOT)) and config.allow_clipping is True
    assert framing["reference_body_height_src_px"] == 70.0
    assert framing["body_height_src_px"] == {"min": 45.0, "max": 70.0, "median": 70.0}
    crouch_ref, _ = _with_character_height(_config(), frames, 35.0, 5, canvas_auto=False)
    assert crouch_ref.scale_override == pytest.approx(35 / 45)  # しゃがみのコマを基準に指定できる


def test_character_height_errors() -> None:
    frames = tuple(_frames())
    with pytest.raises(ValueError, match="収まりません"):
        _with_character_height(_config(64), frames, 80.0, "median", canvas_auto=False)
    with pytest.raises(ValueError, match="preserve_motion"):
        _with_character_height(replace(_config(), placement_mode="legacy_foot"), frames, 30.0, "median", False)
    with pytest.raises(ValueError, match="空のフレーム"):
        empty = Image.new("RGBA", (SIZE, SIZE))
        _with_character_height(_config(), (*frames, empty), 30.0, 3, False)
    with pytest.raises(ValueError, match="可視画素"):
        _with_character_height(_config(), (Image.new("RGBA", (SIZE, SIZE)),), 30.0, "median", False)
    with pytest.raises(ValueError, match="上限"):
        _with_character_height(_config(), frames, 3000.0, "median", canvas_auto=True)


def test_canvas_auto_is_the_tightest_canvas_that_holds_every_frame() -> None:
    frames = tuple(_frames())
    config, framing = _with_character_height(_config(), frames, 35.0, "median", canvas_auto=True)
    scale = 35 / 70
    left = 100 - 5  # 槍の先が左へ95px
    right = 112 - 100
    up = FOOT - 100
    width, height = config.canvas_size
    assert width == int(np.ceil(2 + left * scale + right * scale + 2)) and height == int(np.ceil(2 + up * scale + 2))
    assert config.output_origin == (pytest.approx(2 + left * scale), pytest.approx(2 + up * scale))
    assert config.allow_clipping is False and framing["canvas_size"] == [width, height]


def _write_directory(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(1)
    for index, frame in enumerate(_frames()):
        array = np.asarray(frame)
        opaque = array[..., 3] > 0
        background = np.clip(np.array(BG) + rng.integers(-2, 3, (SIZE, SIZE, 3)), 0, 255).astype(np.uint8)
        out = np.dstack([background, np.full((SIZE, SIZE), 255, np.uint8)])
        out[opaque] = array[opaque]
        Image.fromarray(out, "RGBA").save(directory / f"f{index:03d}.png")
    return directory


def _body_height(path: Path) -> int:
    box = measure_body(np.asarray(Image.open(path).convert("RGBA"))[..., 3])
    return box[3] - box[1]


def test_pipeline_keeps_the_size_and_feet_fixed_while_the_height_changes_with_the_pose(tmp_path: Path) -> None:
    source = _write_directory(tmp_path / "in")
    result = compile_character_frame_directory(
        source, tmp_path / "out", background_tolerance=45, gif_fps=None,
        character_height=35, canvas_auto=True, palette_budget=16,
    )
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert len({frame["scale"] for frame in report["frames"]}) == 1  # 倍率は全フレーム共通
    assert not any(frame.get("clipped") for frame in report["frames"])  # 見切れなし
    heights = [_body_height(path) for path in result.final_frame_paths]
    assert heights[0] == heights[1] == heights[2] == 35  # 立ちは指定どおり
    assert 21 <= heights[4] <= 24 and heights[4] == heights[5]  # しゃがみは縦が縮む（45×0.5≈22.5）
    bottoms = []
    for path in result.final_frame_paths:
        box = measure_body(np.asarray(Image.open(path).convert("RGBA"))[..., 3])
        bottoms.append(box[3])
    assert len(set(bottoms)) == 1  # 足元は全フレームで同じ
    assert report["framing"]["mode"] == "character_height" and report["framing"]["canvas_auto"] is True
    width, height = report["output_frame_size"]
    assert Image.open(result.final_frame_paths[0]).size == (width, height)


def test_trimmed_frames_reproduce_the_canvas_exactly_with_offsets_from_the_pivot(tmp_path: Path) -> None:
    source = _write_directory(tmp_path / "in")
    result = compile_character_frame_directory(
        source, tmp_path / "out", background_tolerance=45, gif_fps=24,
        character_height=35, canvas_auto=True, palette_budget=16,
    )
    root = tmp_path / "out"
    manifest = json.loads((root / "trim_manifest.json").read_text(encoding="utf-8"))
    width, height = manifest["canvas_size"]
    pivot_x, pivot_y = manifest["pivot"]
    assert manifest["frame_count"] == 9 and manifest["fps"] == 24 and len(manifest["durations_ms"]) == 9
    assert sorted(p.name for p in (root / "trimmed_frames").glob("*.png")) == [f"F00{i}.png" for i in range(1, 10)]
    for entry in manifest["frames"]:
        canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        tile = Image.open(root / entry["file"])
        assert tile.size == (entry["width"], entry["height"])
        x = round(pivot_x + entry["offset_from_pivot"][0])
        y = round(pivot_y + entry["offset_from_pivot"][1])
        canvas.alpha_composite(tile, (x, y))
        want = np.asarray(Image.open(root / "final_frames" / f"{entry['frame_id']}.png").convert("RGBA"))
        assert np.array_equal(np.asarray(canvas), want)
        assert tile.getchannel("A").getbbox() == (0, 0, tile.width, tile.height)  # 余白なしに切り詰められている
    assert json.loads(result.report_path.read_text(encoding="utf-8"))["trimmed"]["frames"] == 9


def test_trim_manifest_handles_empty_frames_and_can_be_disabled(tmp_path: Path) -> None:
    final = tmp_path / "F1.png"
    Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(final)
    prepared = type("P", (), {"transform": None, "scale": 1.0})()
    (tmp_path / "out").mkdir()
    info = _write_trimmed_frames(tmp_path / "out", [final], lambda i: f"F{i + 1:03d}", (10, 10), prepared, None)  # type: ignore[arg-type]
    manifest = json.loads((tmp_path / "out" / "trim_manifest.json").read_text(encoding="utf-8"))
    assert info["frames"] == 1 and manifest["pivot"] is None and manifest["durations_ms"] is None
    assert manifest["frames"][0] == {"frame_id": "F001", "file": None, "empty": True}
    source = _write_directory(tmp_path / "in")
    off = compile_character_frame_directory(
        source, tmp_path / "off", background_tolerance=45, gif_fps=None, canvas_size=(64, 64), write_trimmed=False
    )
    assert not (tmp_path / "off" / "trimmed_frames").exists() and not (tmp_path / "off" / "trim_manifest.json").exists()
    assert "trimmed" not in json.loads(off.report_path.read_text(encoding="utf-8"))


def test_job_validation_and_cli(tmp_path: Path) -> None:
    source = _write_directory(tmp_path / "in")
    base = dict(input_dir=source, output_dir=tmp_path / "o")
    for overrides, message in (
        ({"canvas_auto": True}, "Canvas自動"),
        ({"character_height": 30.0, "fit_percentile": 80.0}, "同時に使えません"),
        ({"character_height": 30.0, "scale": 0.5}, "固定倍率"),
        ({"character_height": 0.0}, "キャラの身長"),
        ({"character_height": 30.0, "height_reference": "last"}, "身長の基準"),
        ({"character_height": 30.0, "height_reference": "0"}, "身長の基準"),
    ):
        with pytest.raises(ValueError, match=message):
            FrameSequenceRequest(**base, **overrides).validate()
    FrameSequenceRequest(**base, character_height=30.0, height_reference="3", canvas_auto=True).validate()
    ok = CliRunner().invoke(app, [
        "compile-character-frames", str(source), "-o", str(tmp_path / "cli"), "--background-tolerance", "45",
        "--no-gif", "--character-height", "35", "--canvas-auto", "--height-reference", "median",
    ])
    assert ok.exit_code == 0, ok.output
    assert "出力サイズ:" in ok.output and "切り詰め画像+オフセット:" in ok.output
    assert (tmp_path / "cli" / "trim_manifest.json").exists()
    bad = CliRunner().invoke(app, ["compile-character-frames", str(source), "-o", str(tmp_path / "x"), "--canvas-auto"])
    assert bad.exit_code != 0 and "Canvas自動" in bad.output
    no_trim = CliRunner().invoke(app, [
        "compile-character-frames", str(source), "-o", str(tmp_path / "nt"), "--background-tolerance", "45",
        "--no-gif", "--width", "64", "--height", "64", "--no-trim",
    ])
    assert no_trim.exit_code == 0 and not (tmp_path / "nt" / "trim_manifest.json").exists()
