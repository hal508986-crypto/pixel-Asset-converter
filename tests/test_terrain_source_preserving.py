from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from pixel_tile_compiler.config import CanvasSpec, compiler_config_for_purpose
from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler
from pixel_tile_compiler.pixelizer.palette import quantize_palette
from scripts.terrain_source_preserving_compare import compare


def _source_image() -> Image.Image:
    image = Image.new("RGBA", (128, 128), (54, 112, 48, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 17, 41), fill=(174, 214, 66, 255))
    draw.rectangle((101, 83, 127, 127), fill=(22, 67, 45, 255))
    draw.line((8, 112, 56, 71), fill=(210, 235, 94, 255), width=5)
    draw.rectangle((62, 8, 72, 24), fill=(18, 53, 42, 255))
    return image


def test_terrain_nearest_without_repeat_uses_direct_source_pixels(tmp_path: Path) -> None:
    source = _source_image()
    expected = quantize_palette(
        source.resize((64, 64), Image.Resampling.NEAREST),
        budget=8,
    )

    result = PixelTileCompiler().compile_image(
        source,
        compiler_config_for_purpose(
            "terrain",
            output_root=tmp_path / "nearest",
            canvas=CanvasSpec(64, 64),
            palette_budget=8,
            pixelization_mode="nearest",
            repeat_opt_enabled=False,
            work_size=256,
            debug_enabled=False,
        ),
    )

    assert Image.open(result.final_path).convert("RGBA").tobytes() == expected.tobytes()
    assert result.metadata["config"]["pixelization_mode"] == "nearest"
    assert result.metadata["repeatability_optimization"]["applied"] is False


def test_terrain_metadata_tracks_source_and_actual_palette(tmp_path: Path) -> None:
    source_path = tmp_path / "fixed-source.png"
    _source_image().save(source_path)

    result = PixelTileCompiler().compile(
        source_path,
        compiler_config_for_purpose(
            "terrain",
            output_root=tmp_path / "metadata",
            palette_budget=8,
            pixelization_mode="nearest",
            repeat_opt_enabled=False,
            debug_enabled=False,
        ),
    )
    metadata = result.metadata

    assert metadata["source_image"]["sha256"] == hashlib.sha256(source_path.read_bytes()).hexdigest()
    assert metadata["source_image"]["dimensions"] == [128, 128]
    assert metadata["output_canvas"] == {"width": 64, "height": 64}
    assert metadata["transformation"]["pixelization_mode"] == "nearest"
    assert metadata["transformation"]["palette_budget"] == 8
    assert metadata["transformation"]["actual_palette_count"] == result.metrics.actual_palette_count
    assert len(metadata["transformation"]["palette_colors"]) == result.metrics.actual_palette_count
    assert metadata["transformation"]["repeat_opt_enabled"] is False
    assert metadata["transformation"]["repeat_opt_applied"] is False


def test_terrain_comparison_rejects_non_empty_output_directory(tmp_path: Path) -> None:
    source_path = tmp_path / "fixed-source.png"
    _source_image().save(source_path)
    output_root = tmp_path / "comparison"
    output_root.mkdir()
    (output_root / "keep.txt").write_text("preserve this record", encoding="utf-8")

    with pytest.raises(FileExistsError, match="must be empty"):
        compare(source_path, output_root)
