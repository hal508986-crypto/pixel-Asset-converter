from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from pixel_tile_compiler.asset.acceptance import validate_tile_contract, validate_masked_transition_tile
from pixel_tile_compiler.asset.pipeline import compile_generated_sheet
from pixel_tile_compiler.generation.presets import build_dirt_road_network_spec, build_grass_surface_spec
from pixel_tile_compiler.sheet.splitter import GridSplitter


def _high_resolution_sheet(columns: int = 2, rows: int = 2, cell_size: int = 128) -> Image.Image:
    image = Image.new("RGBA", (columns * cell_size, rows * cell_size), (62, 120, 58, 255))
    draw = ImageDraw.Draw(image)
    for row in range(rows):
        for column in range(columns):
            left = column * cell_size
            top = row * cell_size
            draw.rectangle(
                (left + 12, top + 12, left + cell_size - 13, top + cell_size - 13),
                fill=(70 + column * 30, 110 + row * 20, 48, 255),
            )
            draw.line((left + 16, top + 16, left + cell_size - 16, top + cell_size - 16), fill=(210, 170, 70, 255), width=3)
    return image


def _road_tile(size: int, *, broken: bool = False) -> Image.Image:
    image = Image.new("RGBA", (size, size), (70, 130, 60, 255))
    draw = ImageDraw.Draw(image)
    left = int(size * 0.39)
    right = int(size * 0.61) - 1
    draw.rectangle((left, 0, right, size - 1), fill=(148, 94, 52, 255))
    if broken:
        image.putpixel((size // 2, 0), (70, 130, 60, 255))
    return image


def test_source_sheet_cells_are_available_before_64px_normalization() -> None:
    sheet = _high_resolution_sheet()

    source_cells = GridSplitter().extract_source_cells(sheet, columns=2, rows=2, crop_policy="center")
    normalized = GridSplitter().split(sheet, columns=2, rows=2, crop_policy="center", tile_size=64)

    assert all(tile.image.size == (128, 128) for tile in source_cells)
    assert all(tile.image.size == (64, 64) for tile in normalized.tiles)
    assert source_cells[0].source_box == (0, 0, 128, 128)


def test_compile_generated_sheet_compiles_high_resolution_cells_with_shared_settings(tmp_path: Path) -> None:
    source = tmp_path / "sheet.png"
    _high_resolution_sheet(columns=4, rows=4).save(source)
    output = tmp_path / "compiled"

    result = compile_generated_sheet(
        build_grass_surface_spec(),
        source,
        output,
        palette_budget=8,
        debug_enabled=False,
    )

    assert result.validation["status"] == "accepted"
    assert result.map_path.exists()
    assert Image.open(result.map_path).size == (256, 256)
    assert {Image.open(path).size for path in (output / "tiles").glob("*.png")} == {(64, 64)}
    assert Image.open(output / "source_cells" / "grass_surface_00_00.png").size == (128, 128)
    settings = json.loads(result.settings_path.read_text(encoding="utf-8"))
    assert settings["compile_path"] == "high_resolution_cell_to_pixel_tile"
    assert settings["shared_palette"]["enabled"] is True
    assert settings["source_sheet"]["sha256"]


def test_compile_generated_sheet_is_pixel_deterministic_for_same_input_and_seed(tmp_path: Path) -> None:
    source = tmp_path / "sheet.png"
    _high_resolution_sheet(columns=4, rows=4).save(source)
    spec = build_grass_surface_spec()

    first = compile_generated_sheet(spec, source, tmp_path / "first", palette_budget=8, debug_enabled=False)
    second = compile_generated_sheet(spec, source, tmp_path / "second", palette_budget=8, debug_enabled=False)

    assert first.map_path.read_bytes() == second.map_path.read_bytes()
    assert sorted(path.read_bytes() for path in (first.output_root / "tiles").glob("*.png")) == sorted(
        path.read_bytes() for path in (second.output_root / "tiles").glob("*.png")
    )


def test_final_png_gate_rejects_a_broken_network_connector() -> None:
    spec = build_dirt_road_network_spec()
    tile_spec = next(tile for tile in spec.tiles if tile.id == "road_ns")
    source = _road_tile(128)
    valid = _road_tile(64)
    broken = _road_tile(64, broken=True)

    accepted = validate_tile_contract(tile_spec, source, valid, palette_budget=8, network_contract=spec.network_contracts["road"])
    rejected = validate_tile_contract(tile_spec, source, broken, palette_budget=8, network_contract=spec.network_contracts["road"])

    assert accepted["status"] == "accepted"
    assert rejected["status"] == "rejected"
    assert rejected["final_png"]["connector_validation"]["status"] == "rejected"


def test_masked_grass_dirt_transition_gate_checks_final_boundary(tmp_path: Path) -> None:
    from pixel_tile_compiler.transition_network.masks import build_transition_mask
    from pixel_tile_compiler.transition_network.transition import TransitionTileCompiler, TransitionTileConfig

    result = TransitionTileCompiler(
        TransitionTileConfig(output_root=tmp_path / "transition", source_size=96, variants=1, palette_budget=8, debug_enabled=False)
    ).build_pair(
        Image.new("RGBA", (96, 96), (70, 130, 60, 255)),
        Image.new("RGBA", (96, 96), (150, 90, 50, 255)),
        "grass",
        "dirt",
        "grass_dirt",
    )
    tile = next(item for item in result.tiles if item.spec.tile_id == "grass_to_dirt_ns_v00")
    mask = build_transition_mask((96, 96), "NS", boundary=0.5)

    accepted = validate_masked_transition_tile(tile.source_image, tile.pixel_image, mask, "NS", palette_budget=8)

    assert accepted["status"] == "accepted"
    assert accepted["input_mask"]["status"] == "accepted"
    assert accepted["final_png"]["boundary_validation"]["status"] == "accepted"


def test_masked_transition_gate_rejects_wrong_final_dimensions() -> None:
    from pixel_tile_compiler.transition_network.masks import build_transition_mask

    source = Image.new("RGBA", (96, 96), (70, 130, 60, 255))
    source_mask = build_transition_mask((96, 96), "NS", boundary=0.5)
    final = Image.new("RGBA", (32, 32), (150, 90, 50, 255))

    result = validate_masked_transition_tile(source, final, source_mask, "NS", palette_budget=8)

    assert result["status"] == "rejected"
    assert result["final_png"]["status"] == "rejected"
    assert result["final_png"]["boundary_validation"]["status"] == "unverified"


def test_unsupported_semantic_is_unverified_and_not_accepted() -> None:
    spec = build_grass_surface_spec().model_copy(update={"grid_columns": 1, "grid_rows": 1})
    tile_spec = spec.tiles[0].model_copy(update={"semantic": "transition"})
    image = Image.new("RGBA", (64, 64), (80, 120, 60, 255))

    result = validate_tile_contract(tile_spec, image, image, palette_budget=8, network_contract=None)

    assert result["status"] == "rejected"
    assert result["status_reason"] == "unverified_semantic_role"


@pytest.mark.parametrize("module_name", ["tileset", "network", "transition"])
def test_contract_compilers_disable_self_repeatability(module_name: str, tmp_path: Path) -> None:
    if module_name == "tileset":
        from pixel_tile_compiler.tileset.compiler import TilesetSourceCompiler
        from pixel_tile_compiler.tileset.source_tile import TilesetConfig

        source = tmp_path / "grass.png"
        Image.new("RGBA", (96, 96), (80, 120, 60, 255)).save(source)
        result = TilesetSourceCompiler().build(
            source,
            TilesetConfig(
                output_root=tmp_path / "tileset",
                source_tile_size=64,
                patch_size=32,
                patch_overlap=8,
                strip_width=8,
                variants=1,
                edge_types=1,
                map_columns=1,
                map_rows=1,
                palette_budget=8,
                debug_enabled=False,
            ),
        )
        metadata = json.loads((result.output_root / "pixel_artifacts" / "tile_00" / "metadata.json").read_text(encoding="utf-8"))
    elif module_name == "network":
        from pixel_tile_compiler.transition_network.network import NetworkTileCompiler, NetworkTileConfig
        from pixel_tile_compiler.transition_network.masks import ROAD_TOPOLOGIES

        config = NetworkTileConfig(output_root=tmp_path / "network", source_size=64, variants=1, topologies=(ROAD_TOPOLOGIES[0],), palette_budget=8, debug_enabled=False)
        result = NetworkTileCompiler(config).build(Image.new("RGBA", (64, 64), (80, 120, 60, 255)), Image.new("RGBA", (64, 64), (150, 90, 50, 255)))
        metadata = json.loads((result.output_root / "pixel_artifacts" / f"{ROAD_TOPOLOGIES[0].lower()}_v00" / "metadata.json").read_text(encoding="utf-8"))
    else:
        from pixel_tile_compiler.transition_network.transition import TransitionTileCompiler, TransitionTileConfig

        config = TransitionTileConfig(output_root=tmp_path / "transition", source_size=64, variants=1, palette_budget=8, debug_enabled=False)
        result = TransitionTileCompiler(config).build_pair(
            Image.new("RGBA", (64, 64), (80, 120, 60, 255)),
            Image.new("RGBA", (64, 64), (40, 80, 120, 255)),
            "grass",
            "water",
            "grass_water",
        )
        metadata = json.loads((result.output_root / "pixel_artifacts" / "grass_to_water_ns_v00" / "metadata.json").read_text(encoding="utf-8"))

    assert metadata["config"]["tile_mode"] == "directional"
    assert metadata["repeatability_optimization"]["applied"] is False
