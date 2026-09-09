"""背景の扱いと構図の選択に関する契約テスト。

仕様: docs/spec/canvas_scale_and_palette_budget_spec.md 4.5節
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from pixel_tile_compiler.config import CanvasSpec, compiler_config_for_purpose
from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler


def _opaque_busy_source(path: Path, size: tuple[int, int] = (192, 192)) -> Path:
    """全面が絵で、背景と被写体の境界が定義できない素材を作る。"""
    image = Image.new("RGBA", size, (0, 0, 0, 255))
    for y in range(size[1]):
        for x in range(size[0]):
            image.putpixel(
                (x, y),
                (
                    (x * 7 + y * 3) % 256,
                    (x * 3 + y * 11) % 256,
                    (x * 5 + y * 5) % 256,
                    255,
                ),
            )
    image.save(path)
    return path


def _cutout_source(path: Path, size: tuple[int, int] = (192, 192)) -> Path:
    """透過背景に縦長の被写体だけがある素材を作る。"""
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    for y in range(20, 170):
        for x in range(80, 112):
            image.putpixel((x, y), (200, 80 + (y % 5) * 10, 90, 255))
    image.save(path)
    return path


def _visible_ratio(path: Path) -> float:
    with Image.open(path) as opened:
        array = np.array(opened.convert("RGBA"))
    return float((array[..., 3] > 0).mean())


def _visible_bbox(path: Path) -> tuple[int, int, int, int]:
    with Image.open(path) as opened:
        bbox = opened.convert("RGBA").getchannel("A").getbbox()
    return bbox


def test_character_purpose_defaults_are_unchanged():
    """指定しなければ従来どおりの既定で動く。"""
    config = compiler_config_for_purpose("character", canvas=CanvasSpec(128, 128))
    assert config.background_mode == "auto"
    assert config.character_input_mode == "single_frame"
    assert config.tile_mode == "object"
    assert config.pixelization_mode == "nearest"


def test_character_purpose_keeps_the_caller_background_mode():
    """呼び出し側が指定した背景の扱いを握り潰さない。"""
    config = compiler_config_for_purpose(
        "character", canvas=CanvasSpec(128, 128), background_mode="alpha"
    )
    assert config.background_mode == "alpha"


def test_character_purpose_keeps_the_caller_input_mode():
    """呼び出し側が指定した構図を握り潰さない。"""
    config = compiler_config_for_purpose(
        "character", canvas=CanvasSpec(128, 128), character_input_mode="pre_aligned"
    )
    assert config.character_input_mode == "pre_aligned"


def test_character_purpose_still_forces_the_object_nearest_path(tmp_path: Path):
    """キャラクター経路の骨格（object/nearest）は呼び出し側から変えさせない。"""
    config = compiler_config_for_purpose(
        "character",
        canvas=CanvasSpec(128, 128),
        tile_mode="repeatable",
        pixelization_mode="region",
    )
    assert config.tile_mode == "object"
    assert config.pixelization_mode == "nearest"


def test_full_frame_composition_keeps_every_visible_pixel(tmp_path: Path):
    """全面が絵の素材でも、画面全体構図なら画素を失わない。"""
    source = _opaque_busy_source(tmp_path / "busy.png")
    config = compiler_config_for_purpose(
        "character",
        output_root=tmp_path / "full_frame",
        canvas=CanvasSpec(64, 64),
        palette_budget=24,
        background_mode="alpha",
        character_input_mode="pre_aligned",
    )
    result = PixelTileCompiler().compile(source, config)
    assert _visible_ratio(result.final_path) == 1.0


def test_full_frame_composition_leaves_no_margin(tmp_path: Path):
    """画面全体構図では被写体がCanvas全面に及び、余白が発生しない。"""
    source = _opaque_busy_source(tmp_path / "busy.png")
    config = compiler_config_for_purpose(
        "character",
        output_root=tmp_path / "full_frame",
        canvas=CanvasSpec(64, 64),
        palette_budget=24,
        background_mode="alpha",
        character_input_mode="pre_aligned",
    )
    result = PixelTileCompiler().compile(source, config)
    assert _visible_bbox(result.final_path) == (0, 0, 64, 64)


def test_subject_composition_still_anchors_to_the_bottom_frame(tmp_path: Path):
    """被写体構図の既存の収まり（54/64比のフレームと足元固定）は変わらない。"""
    source = _cutout_source(tmp_path / "cutout.png")
    config = compiler_config_for_purpose(
        "character",
        output_root=tmp_path / "subject",
        canvas=CanvasSpec(128, 128),
        palette_budget=24,
        character_input_mode="single_frame",
    )
    result = PixelTileCompiler().compile(source, config)
    left, top, right, bottom = _visible_bbox(result.final_path)
    # フレーム高さは _scale_half_up(54, 128, 64) = 108 に収まる
    assert bottom - top <= 108
    # 下端は bottom_margin = _scale_half_up(7, 128, 64) = 14 の分だけ空く
    assert 128 - bottom >= 1
    assert left > 0 and right < 128


def test_full_frame_composition_rescues_a_source_that_subject_fitting_destroys(tmp_path: Path):
    """全面が絵の素材では、既定の切り抜き構図が画素を失い、画面全体構図が救う。

    実素材（柵、甲冑のクローズアップ）で可視率が7%・4%まで落ちた事象の回帰。
    """
    source = _opaque_busy_source(tmp_path / "busy.png")
    ratios = {}
    for label, background_mode, input_mode in (
        ("subject", "auto", "single_frame"),
        ("full", "alpha", "pre_aligned"),
    ):
        config = compiler_config_for_purpose(
            "character",
            output_root=tmp_path / label,
            canvas=CanvasSpec(64, 64),
            palette_budget=24,
            background_mode=background_mode,
            character_input_mode=input_mode,
        )
        result = PixelTileCompiler().compile(source, config)
        ratios[label] = _visible_ratio(result.final_path)
    assert ratios["full"] == 1.0
    assert ratios["full"] > ratios["subject"]


def test_cli_exposes_the_composition_choice(tmp_path: Path):
    """CLIから構図を選べる。既定は従来どおり被写体を収める。"""
    from typer.testing import CliRunner

    from pixel_tile_compiler.cli import app

    source = _opaque_busy_source(tmp_path / "busy.png")
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "compile", str(source),
            "--output", str(tmp_path / "cli_full"),
            "--purpose", "character",
            "--tile-mode", "object",
            "--pixelization", "nearest",
            "--background", "alpha",
            "--character-input-mode", "pre_aligned",
            "--width", "64", "--height", "64", "--palette", "24",
        ],
    )
    assert result.exit_code == 0, result.output
    assert _visible_ratio(tmp_path / "cli_full" / "final.png") == 1.0


def test_cli_rejects_an_unknown_composition(tmp_path: Path):
    from typer.testing import CliRunner

    from pixel_tile_compiler.cli import app

    source = _opaque_busy_source(tmp_path / "busy.png")
    result = CliRunner().invoke(
        app,
        ["compile", str(source), "--output", str(tmp_path / "cli_bad"),
         "--character-input-mode", "sideways"],
    )
    assert result.exit_code != 0
