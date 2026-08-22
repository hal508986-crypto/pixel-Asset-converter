from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from pixel_tile_compiler.config import MapCompilerConfig
from pixel_tile_compiler.map.analysis import analyze_global_map
from pixel_tile_compiler.map.compiler import (
    MapCompiler,
    compute_context_box,
    compute_tile_box,
    correct_shared_boundaries,
)
from pixel_tile_compiler.map.metrics import measure_map_metrics


def create_map_source(path: Path, size: tuple[int, int] = (512, 640)) -> None:
    image = Image.new("RGBA", size, (92, 145, 55, 255))
    draw = ImageDraw.Draw(image)
    road_left = size[0] // 2 - size[0] // 10
    road_right = size[0] // 2 + size[0] // 10
    draw.rectangle((road_left, 0, road_right, size[1]), fill=(157, 111, 63, 255))
    draw.line((road_left + 12, 0, road_left + 20, size[1]), fill=(190, 146, 83, 255), width=8)
    draw.ellipse((size[0] // 8, size[1] // 5, size[0] // 4, size[1] // 3), fill=(58, 112, 50, 255))
    image.save(path)


def test_grid_and_context_boxes_clip_at_map_edges():
    image_size = (400, 500)

    assert compute_tile_box(image_size, columns=4, rows=5, tile_x=2, tile_y=3) == (200, 300, 300, 400)
    assert compute_context_box(image_size, columns=4, rows=5, tile_x=2, tile_y=3, margin_tiles=1) == (
        100,
        200,
        400,
        500,
    )
    assert compute_context_box(image_size, columns=4, rows=5, tile_x=0, tile_y=0, margin_tiles=1) == (
        0,
        0,
        200,
        200,
    )
    assert compute_context_box((401, 501), columns=4, rows=5, tile_x=2, tile_y=3, margin_tiles=1) == (
        100,
        200,
        401,
        501,
    )


def test_map_compiler_reassembles_twenty_tiles_with_layout_and_metrics(tmp_path: Path):
    source = tmp_path / "map.png"
    create_map_source(source)

    result = MapCompiler().compile(
        source,
        MapCompilerConfig(
            output_root=tmp_path / "map-output",
            columns=4,
            rows=5,
            context_margin_tiles=1,
            global_palette_budget=24,
            shared_palette_enabled=True,
        ),
    )

    assert Image.open(result.final_path).size == (256, 320)
    tile_paths = list(result.tiles_dir.glob("tile_*.png"))
    assert len(tile_paths) == 20
    assert {Image.open(path).size for path in tile_paths} == {(64, 64)}
    assert (result.final_path.parent / "map_layout.json").exists()
    assert (result.final_path.parent / "global_analysis.json").exists()
    assert (result.final_path.parent / "metrics.json").exists()
    assert (result.final_path.parent / "grid_overlay.png").exists()
    assert all(tile["context_bbox"] for tile in result.layout["tiles"])
    assert all(tile["context_used"] for tile in result.layout["tiles"])
    assert result.metrics.neighbor_color_discontinuity >= 0


def test_shared_palette_limits_all_tiles_to_global_palette(tmp_path: Path):
    source = tmp_path / "map.png"
    create_map_source(source)
    result = MapCompiler().compile(
        source,
        MapCompilerConfig(output_root=tmp_path / "map-output", global_palette_budget=24, shared_palette_enabled=True),
    )

    global_palette = {tuple(color) for color in result.global_analysis["palette"]}
    for tile_path in result.tiles_dir.glob("tile_*.png"):
        colors = {pixel[:3] for pixel in Image.open(tile_path).convert("RGBA").getdata() if pixel[3] != 0}
        assert colors <= global_palette


def test_context_margin_zero_keeps_source_and_context_boxes_equal(tmp_path: Path):
    source = tmp_path / "map.png"
    create_map_source(source)
    result = MapCompiler().compile(
        source,
        MapCompilerConfig(output_root=tmp_path / "map-output", context_margin_tiles=0),
    )

    for tile in result.layout["tiles"]:
        assert tile["context_bbox"] == tile["source_bbox"]
        assert tile["context_used"] is False


def test_map_compiler_is_deterministic(tmp_path: Path):
    source = tmp_path / "map.png"
    create_map_source(source)
    first = MapCompiler().compile(
        source,
        MapCompilerConfig(output_root=tmp_path / "first", context_margin_tiles=1),
    )
    second = MapCompiler().compile(
        source,
        MapCompilerConfig(output_root=tmp_path / "second", context_margin_tiles=1),
    )

    assert first.final_path.read_bytes() == second.final_path.read_bytes()
    assert (first.final_path.parent / "metrics.json").read_bytes() == (second.final_path.parent / "metrics.json").read_bytes()


def test_context_api_returns_only_the_central_tile(tmp_path: Path):
    source = tmp_path / "map.png"
    create_map_source(source, size=(256, 320))
    image = Image.open(source).convert("RGBA")
    config = MapCompilerConfig(output_root=tmp_path / "analysis", global_palette_budget=16)
    analysis = analyze_global_map(image, config)

    tile = MapCompiler().compile_tile_with_context(
        image,
        tile_x=1,
        tile_y=2,
        context_margin=1,
        global_analysis=analysis,
        shared_palette=True,
    )

    assert tile.size == (64, 64)


def test_context_api_can_disable_shared_palette(tmp_path: Path):
    source = tmp_path / "map.png"
    create_map_source(source, size=(256, 320))
    image = Image.open(source).convert("RGBA")
    config = MapCompilerConfig(output_root=tmp_path / "analysis", global_palette_budget=16)
    analysis = analyze_global_map(image, config)

    tile = MapCompiler().compile_tile_with_context(
        image,
        tile_x=1,
        tile_y=2,
        context_margin=0,
        global_analysis=analysis,
        shared_palette=False,
    )

    assert tile.size == (64, 64)


def test_shared_boundary_correction_reduces_grid_color_discontinuity():
    image = Image.new("RGBA", (128, 64), (25, 25, 25, 255))
    ImageDraw.Draw(image).rectangle((64, 0, 127, 63), fill=(220, 220, 220, 255))
    palette = ((25, 25, 25), (120, 120, 120), (220, 220, 220))

    before = measure_map_metrics(image, columns=2, rows=1)
    corrected = correct_shared_boundaries(image, columns=2, rows=1, tile_size=64, palette=palette)
    after = measure_map_metrics(corrected, columns=2, rows=1)

    assert after.neighbor_color_discontinuity < before.neighbor_color_discontinuity


def test_shared_boundary_correction_preserves_semantic_boundary():
    image = Image.new("RGBA", (128, 64), (25, 25, 25, 255))
    ImageDraw.Draw(image).rectangle((64, 0, 127, 63), fill=(220, 220, 220, 255))
    semantic_map = np.full((64, 128), "grass", dtype="<U8")
    semantic_map[:, 64:] = "road"

    corrected = correct_shared_boundaries(
        image,
        columns=2,
        rows=1,
        tile_size=64,
        palette=((25, 25, 25), (120, 120, 120), (220, 220, 220)),
        semantic_map=semantic_map,
        source_size=(128, 64),
    )

    assert corrected.getpixel((63, 0))[:3] == (25, 25, 25)
    assert corrected.getpixel((64, 0))[:3] == (220, 220, 220)
