"""Canvasサイズ・palette上限・detail_level・背景・構図の5軸マトリクスstudyの契約テスト。

仕様: docs/spec/canvas_scale_and_palette_budget_spec.md 2節・4.4節・4.5節・6節P0
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import pytest
from PIL import Image

from pixel_tile_compiler.character_study.canvas_scale import (
    CanvasScaleStudyConfig,
    CanvasScaleStudyRunner,
    _display_scale_factor,
    _edge_color_changes,
    _normalize_cell_for_display,
)


def _write_character_source(path: Path, size: tuple[int, int] = (160, 160)) -> None:
    """検証用の合成キャラクター素材を作る（透過背景＋不透明な矩形の被写体）。"""
    width, height = size
    image = Image.new("RGBA", size, (240, 240, 240, 0))
    left, top = int(width * 0.2), int(height * 0.1)
    right, bottom = int(width * 0.8), int(height * 0.9)
    for y in range(top, bottom):
        for x in range(left, right):
            image.putpixel((x, y), (70 + (x % 5) * 8, 100 + (y % 7) * 6, 160, 255))
    image.putpixel((left + 10, top + 10), (240, 210, 170, 255))
    image.putpixel((right - 10, top + 10), (240, 210, 170, 255))
    image.save(path)


def test_canvas_scale_study_accepts_configured_canvas_sizes(tmp_path: Path) -> None:
    """canvas_sizesを設定から読み込める。既定は64/128/256のまま。"""
    source = tmp_path / "source.png"
    _write_character_source(source)

    default_config = CanvasScaleStudyConfig(source=source, output_root=tmp_path / "out_default")
    assert default_config.canvas_sizes == ((64, 64), (128, 128), (256, 256))

    custom_config = CanvasScaleStudyConfig(
        source=source,
        output_root=tmp_path / "out_custom",
        canvas_sizes=((64, 64), (256, 256)),
    )
    assert custom_config.canvas_sizes == ((64, 64), (256, 256))
    assert custom_config.to_dict()["study"]["canvas_sizes"] == [[64, 64], [256, 256]]


def test_canvas_scale_study_accepts_non_square_canvas(tmp_path: Path) -> None:
    """非正方Canvas（256x128）を指定して実際に成果物が出る。"""
    source = tmp_path / "source.png"
    _write_character_source(source)
    output_root = tmp_path / "out"
    config = CanvasScaleStudyConfig(
        source=source,
        output_root=output_root,
        canvas_sizes=((256, 128),),
        palette_budgets=(24,),
        detail_levels=("balanced",),
        background_modes=("auto",),
        composition_modes=("single_frame",),
    )

    result = CanvasScaleStudyRunner().run(config)

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert len(manifest["cells"]) == 1
    cell = manifest["cells"][0]
    final_path = result.output_root / cell["final_path"]
    with Image.open(final_path) as final_image:
        assert final_image.size == (256, 128)


def test_canvas_scale_study_rejects_out_of_range_canvas(tmp_path: Path) -> None:
    """各辺が16未満・512超のcanvas_sizesはValueError。"""
    source = tmp_path / "source.png"
    _write_character_source(source)

    with pytest.raises(ValueError, match="canvas_sizes"):
        CanvasScaleStudyConfig(source=source, canvas_sizes=((8, 64),))

    with pytest.raises(ValueError, match="canvas_sizes"):
        CanvasScaleStudyConfig(source=source, canvas_sizes=((64, 600),))


def test_canvas_scale_study_accepts_palette_budgets_up_to_64(tmp_path: Path) -> None:
    """palette_budgetsは4〜64を受け付け、65はValueError。"""
    source = tmp_path / "source.png"
    _write_character_source(source)

    config = CanvasScaleStudyConfig(source=source, palette_budgets=(4, 64))
    assert config.palette_budgets == (4, 64)

    with pytest.raises(ValueError, match="palette_budgets"):
        CanvasScaleStudyConfig(source=source, palette_budgets=(65,))


def test_canvas_scale_study_rejects_duplicate_budgets(tmp_path: Path) -> None:
    """palette_budgetsに重複があればValueError。"""
    source = tmp_path / "source.png"
    _write_character_source(source)

    with pytest.raises(ValueError, match="重複"):
        CanvasScaleStudyConfig(source=source, palette_budgets=(16, 16))


def test_study_metrics_include_uniform_ratio_and_occupancy(tmp_path: Path) -> None:
    """指標JSONに4.4節の項目が揃う（uniform_2x2_ratio・occupancy・edge_color_changes等）。"""
    source = tmp_path / "source.png"
    _write_character_source(source)
    output_root = tmp_path / "out"
    config = CanvasScaleStudyConfig(
        source=source,
        output_root=output_root,
        canvas_sizes=((64, 64), (128, 128)),
        palette_budgets=(24,),
        detail_levels=("balanced",),
        background_modes=("auto",),
        composition_modes=("single_frame",),
    )

    result = CanvasScaleStudyRunner().run(config)

    metrics = json.loads(result.metrics_path.read_text(encoding="utf-8"))
    cells = metrics["cells"]
    assert len(cells) == 2
    for cell in cells:
        assert isinstance(cell["measured_palette"], int)
        assert cell["measured_palette"] <= 24
        assert 0.0 <= cell["uniform_2x2_ratio"] <= 1.0
        assert cell["subject_bbox"] is None or len(cell["subject_bbox"]) == 4
        assert 0.0 <= cell["occupancy_height"] <= 1.0
        assert 0.0 <= cell["occupancy_width"] <= 1.0
        assert 0.0 <= cell["edge_color_changes"] <= 1.0
        assert cell["elapsed_seconds"] >= 0.0


def test_edge_color_changes_is_higher_for_a_noisy_image() -> None:
    """隣接画素の色変化率は、ノイズ画像の方が単色ベタ画像より高い。"""
    rng = random.Random(42)
    size = (32, 32)

    solid = Image.new("RGBA", size, (100, 120, 140, 255))

    noisy = Image.new("RGBA", size, (0, 0, 0, 255))
    for y in range(size[1]):
        for x in range(size[0]):
            noisy.putpixel(
                (x, y),
                (rng.randint(0, 255), rng.randint(0, 255), rng.randint(0, 255), 255),
            )

    solid_ratio = _edge_color_changes(solid)
    noisy_ratio = _edge_color_changes(noisy)

    assert solid_ratio == 0.0
    assert noisy_ratio > solid_ratio


def test_edge_color_changes_returns_zero_when_no_visible_pixels() -> None:
    """可視画素どうしの隣接ペアが無ければ0.0を返す（分母0の回避）。"""
    empty = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    assert _edge_color_changes(empty) == 0.0


def test_comparison_sheet_normalizes_display_scale() -> None:
    """64は4倍・128は2倍・256は等倍に拡大し、表示サイズが揃う。"""
    assert _display_scale_factor(64, 64) == 4
    assert _display_scale_factor(128, 128) == 2
    assert _display_scale_factor(256, 256) == 1

    small = Image.new("RGBA", (64, 64), (10, 20, 30, 255))
    medium = Image.new("RGBA", (128, 128), (10, 20, 30, 255))
    large = Image.new("RGBA", (256, 256), (10, 20, 30, 255))

    small_display = _normalize_cell_for_display(small, (64, 64))
    medium_display = _normalize_cell_for_display(medium, (128, 128))
    large_display = _normalize_cell_for_display(large, (256, 256))

    assert small_display.size == (256, 256)
    assert medium_display.size == (256, 256)
    assert large_display.size == (256, 256)


def test_canvas_scale_study_records_background_and_composition(tmp_path: Path) -> None:
    """背景と構図の両軸がmanifest/metricsに残る。"""
    source = tmp_path / "source.png"
    _write_character_source(source)
    output_root = tmp_path / "out"
    config = CanvasScaleStudyConfig(
        source=source,
        output_root=output_root,
        canvas_sizes=((64, 64),),
        palette_budgets=(24,),
        detail_levels=("balanced",),
        background_modes=("auto", "alpha"),
        composition_modes=("single_frame",),
    )

    result = CanvasScaleStudyRunner().run(config)

    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    metrics = json.loads(result.metrics_path.read_text(encoding="utf-8"))

    manifest_backgrounds = {cell["background_mode"] for cell in manifest["cells"]}
    manifest_compositions = {cell["composition_mode"] for cell in manifest["cells"]}
    metrics_backgrounds = {cell["background_mode"] for cell in metrics["cells"]}

    assert manifest_backgrounds == {"auto", "alpha"}
    assert manifest_compositions == {"single_frame"}
    assert metrics_backgrounds == {"auto", "alpha"}
    assert result.sheet_path.exists()
    assert (result.output_root / "summary.md").exists()


def test_generated_comparison_sheet_lays_out_one_normalized_cell_per_condition(tmp_path: Path) -> None:
    """実際に生成されるシートが、行=Canvas・列=palette で正規化された寸法になること。

    ヘルパー単体ではなく、runnerが書き出したPNGそのものを検査する。
    """
    source = tmp_path / "character.png"
    _write_character_source(source)
    config = CanvasScaleStudyConfig(
        source=source,
        output_root=tmp_path / "output",
        canvas_sizes=((64, 64), (128, 128)),
        palette_budgets=(16, 24),
    )
    result = CanvasScaleStudyRunner().run(config)

    with Image.open(result.sheet_path) as sheet:
        width, height = sheet.size
    # 列=palette上限2件、行=Canvas2件。1セルの表示箱は256px、見出し帯が20px。
    assert width == 256 * 2
    assert height == (256 + 20) * 2
