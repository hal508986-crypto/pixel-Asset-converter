from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from typer.testing import CliRunner

from pixel_tile_compiler.cli import app
from pixel_tile_compiler.pixelizer.character_frame_sequence import compile_character_frame_directory
from pixel_tile_compiler.pixelizer.temporal_stabilize import stabilize_palette_flicker

A = (200, 60, 60)
B = (180, 80, 60)  # Aとの距離 ≈ 28


def _frame(color, size=8, alpha=255) -> Image.Image:
    array = np.zeros((size, size, 4), np.uint8)
    array[2:6, 2:6, :3] = color
    array[2:6, 2:6, 3] = alpha
    return Image.fromarray(array, "RGBA")


def _colors(frames, y=3, x=3):
    return [tuple(np.asarray(f)[y, x, :3]) for f in frames]


def test_flip_flopping_near_the_palette_boundary_is_held_steady() -> None:
    # 元の色はAとBのあいだで少しだけ交互に揺れ、最近色（最終）が A,B,A,B… と入れ替わる
    near_a, near_b = (194, 66, 60), (186, 74, 60)  # どちらも自分側の色に近い（Aまで8.5、Bまで19.8）
    source = [_frame(near_a if i % 2 == 0 else near_b) for i in range(6)]
    final = [_frame(A if i % 2 == 0 else B) for i in range(6)]
    result = stabilize_palette_flicker(source, final, margin=12)
    assert len(set(_colors(result.frames))) == 1
    assert result.swapped_px[0] == 0 and sum(result.swapped_px) == 16 * 3  # 4x4画素 × 入れ替えが必要な3フレーム
    assert result.report_as_dict()["total_swapped_px"] == 48
    # 差を許さない小さいmarginでは従来どおり入れ替わる
    tight = stabilize_palette_flicker(source, final, margin=1)
    assert _colors(tight.frames) == _colors(final)


def test_a_real_colour_change_is_not_suppressed() -> None:
    source = [_frame(A), _frame(A), _frame(B), _frame(B)]  # 元の色が本当に変わる（距離≈28 > margin）
    final = [_frame(A), _frame(A), _frame(B), _frame(B)]
    assert _colors(stabilize_palette_flicker(source, final, margin=12).frames) == [A, A, B, B]
    # 大きすぎるmarginは本物の変化も潰す（margin選択の注意点）
    assert _colors(stabilize_palette_flicker(source, final, margin=40).frames) == [A, A, A, A]


def test_alpha_is_never_changed_and_only_previous_colours_are_used() -> None:
    source = [_frame((190, 70, 60), alpha=255) for _ in range(4)]
    final = [_frame(A if i % 2 == 0 else B, alpha=255 if i != 2 else 0) for i in range(4)]
    result = stabilize_palette_flicker(source, final, margin=12)
    for got, want in zip(result.frames, final):
        assert np.array_equal(np.asarray(got)[..., 3], np.asarray(want)[..., 3])
    seen = {tuple(px[:3]) for f in result.frames for px in np.asarray(f).reshape(-1, 4) if px[3]}
    assert seen <= {A, B}


def test_protected_outline_colour_is_neither_replaced_nor_copied() -> None:
    outline = (0, 0, 0)
    source = [_frame((190, 70, 60)) for _ in range(3)]
    final = [_frame(outline), _frame(A), _frame(outline)]
    kept = stabilize_palette_flicker(source, final, margin=200, protect_colors=[outline])
    assert _colors(kept.frames) == [outline, A, outline]
    unprotected = stabilize_palette_flicker(source, final, margin=200)
    assert _colors(unprotected.frames)[1] == outline  # 保護しなければ前の色に引きずられる


def test_first_frame_unchanged_deterministic_and_validated() -> None:
    source = [_frame((190, 70, 60)) for _ in range(3)]
    final = [_frame(A), _frame(B), _frame(A)]
    one = stabilize_palette_flicker(source, final)
    two = stabilize_palette_flicker(source, final)
    assert [f.tobytes() for f in one.frames] == [f.tobytes() for f in two.frames]
    assert one.frames[0].tobytes() == final[0].tobytes()
    with pytest.raises(ValueError, match="margin"):
        stabilize_palette_flicker(source, final, margin=0)
    with pytest.raises(ValueError, match="same"):
        stabilize_palette_flicker(source[:2], final)
    with pytest.raises(ValueError, match="same size"):
        stabilize_palette_flicker([Image.new("RGBA", (4, 4))], [Image.new("RGBA", (5, 5))])


def _noisy_sequence(directory: Path, count: int = 8) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(9)
    for index in range(count):
        array = np.zeros((96, 96, 4), np.uint8)
        array[:, :, :3] = (92, 156, 155)
        array[:, :, 3] = 255
        body = np.zeros((96, 96), bool)
        body[26:66, 36:60] = True
        body[16:26, 40:56] = True
        base = np.array([(150, 60, 60), (175, 70, 60)])[(np.arange(96)[None, :] // 6 + np.arange(96)[:, None] // 6) % 2]
        noise = rng.integers(-9, 10, (96, 96, 3))  # 量子化の境界を跨ぐ程度のノイズ
        array[body, :3] = np.clip(base + noise, 0, 255)[body]
        Image.fromarray(array, "RGBA").save(directory / f"f{index:03d}.png")


def test_pipeline_option_reduces_toggles_without_touching_alpha_or_palette(tmp_path: Path) -> None:
    _noisy_sequence(tmp_path / "in")
    kwargs = dict(canvas_size=(64, 64), background_tolerance=45, palette_budget=6, gif_fps=None)
    plain = compile_character_frame_directory(tmp_path / "in", tmp_path / "plain", stabilize_margin=None, **kwargs)
    steady = compile_character_frame_directory(tmp_path / "in", tmp_path / "steady", stabilize_margin=12, **kwargs)

    def load(result):
        return [np.asarray(Image.open(p).convert("RGBA")) for p in result.final_frame_paths]

    plain_frames, steady_frames = load(plain), load(steady)
    for a, b in zip(plain_frames, steady_frames):
        assert np.array_equal(a[..., 3], b[..., 3])  # 輪郭は不変

    def toggles(frames):
        total = 0
        for a, b, c in zip(frames, frames[1:], frames[2:]):
            v = (a[..., 3] > 0) & (b[..., 3] > 0) & (c[..., 3] > 0)
            total += int(((a[v][:, :3] == c[v][:, :3]).all(1) & (a[v][:, :3] != b[v][:, :3]).any(1)).sum())
        return total

    assert toggles(plain_frames) > 0 and toggles(steady_frames) < toggles(plain_frames) * 0.5
    report = json.loads(steady.report_path.read_text(encoding="utf-8"))
    assert report["stabilization"]["margin"] == 12 and report["stabilization"]["total_swapped_px"] > 0
    assert report["final_palette"]["within_budget"] is True
    shared = {tuple(c) for c in report["shared_palette"]["colors"]}
    assert {tuple(c) for c in report["final_palette"]["colors"]} <= shared
    assert "stabilization" not in json.loads(plain.report_path.read_text(encoding="utf-8"))
    # compiled/ は生のコンパイラ出力のまま。sheetは安定化後のfinal_framesから作られる
    raw = np.asarray(Image.open(steady.frame_paths[3]).convert("RGBA"))
    assert np.array_equal(raw, np.asarray(Image.open(plain.frame_paths[3]).convert("RGBA")))
    sheet = np.asarray(Image.open(steady.compiled_sheet_path).convert("RGBA"))
    assert np.array_equal(sheet[:, 3 * 64:4 * 64], steady_frames[3])


def test_cli_stabilize_margin_flag(tmp_path: Path) -> None:
    _noisy_sequence(tmp_path / "in", count=4)
    result = CliRunner().invoke(app, [
        "compile-character-frames", str(tmp_path / "in"), "-o", str(tmp_path / "out"),
        "--width", "64", "--height", "64", "--background-tolerance", "45", "--stabilize-margin", "12", "--no-gif",
    ])
    assert result.exit_code == 0, result.output
    report = json.loads((tmp_path / "out" / "bbox_report.json").read_text(encoding="utf-8"))
    assert report["stabilization"]["margin"] == 12.0
    off = CliRunner().invoke(app, [
        "compile-character-frames", str(tmp_path / "in"), "-o", str(tmp_path / "off"),
        "--width", "64", "--height", "64", "--background-tolerance", "45", "--no-gif", "--stabilize-margin", "0",
    ])
    assert off.exit_code == 0
    assert "stabilization" not in json.loads((tmp_path / "off" / "bbox_report.json").read_text(encoding="utf-8"))
    default = CliRunner().invoke(app, [
        "compile-character-frames", str(tmp_path / "in"), "-o", str(tmp_path / "default"),
        "--width", "64", "--height", "64", "--background-tolerance", "45", "--no-gif",
    ])
    assert default.exit_code == 0
    assert json.loads((tmp_path / "default" / "bbox_report.json").read_text(encoding="utf-8"))["stabilization"]["margin"] == 12.0
