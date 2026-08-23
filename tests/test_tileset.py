from pathlib import Path

from PIL import Image, ImageDraw

from pixel_tile_compiler.tileset.compiler import TilesetSourceCompiler
from pixel_tile_compiler.tileset.edge_contract import EdgeContract
from pixel_tile_compiler.tileset.evaluator import assemble_contract_map, validate_contract_adjacency, validate_edge_profiles
from pixel_tile_compiler.tileset.patches import PatchDatabase
from pixel_tile_compiler.tileset.quilting import minimum_error_vertical_seam
from pixel_tile_compiler.tileset.source_tile import SourceTileSynthesizer, TilesetConfig
from pixel_tile_compiler.tileset.validator import validate_material_source


def make_grass_source(size: tuple[int, int] = (96, 96)) -> Image.Image:
    image = Image.new("RGBA", size, (72, 132, 62, 255))
    draw = ImageDraw.Draw(image)
    for y in range(0, size[1], 8):
        draw.line((0, y, size[0] - 1, y), fill=(82, 145, 68, 255), width=1)
    for x in range(4, size[0], 13):
        draw.line((x, 12, x + 3, 7), fill=(45, 105, 45, 255), width=1)
    return image


def test_material_source_validation_reports_metrics():
    report = validate_material_source(make_grass_source())

    assert report.status in {"accepted", "warning", "rejected"}
    assert report.metrics.brightness_spatial_variance >= 0
    assert report.metrics.color_spatial_variance >= 0
    assert report.metrics.texture_density_variance >= 0
    assert 0 <= report.metrics.center_dominance <= 1
    assert 0 <= report.metrics.large_landmark_risk <= 1


def test_patch_database_has_deterministic_metadata_and_size():
    source = make_grass_source()
    first = PatchDatabase.from_image(source, patch_size=32, overlap=8, seed=42)
    second = PatchDatabase.from_image(source, patch_size=32, overlap=8, seed=42)

    assert first.patches
    assert all(patch.image.size == (32, 32) for patch in first.patches)
    assert [patch.position for patch in first.patches] == [patch.position for patch in second.patches]
    assert first.patches[0].mean_lab == second.patches[0].mean_lab


def test_minimum_error_seam_prefers_low_error_path():
    error = [[10.0, 1.0, 10.0], [10.0, 1.0, 10.0], [10.0, 1.0, 10.0]]

    seam = minimum_error_vertical_seam(error)

    assert seam == [1, 1, 1]


def test_edge_contract_is_explicit_and_serializable():
    contract = EdgeContract("grass_A", "grass_B", "grass_C", "grass_A")

    assert contract.north == "grass_A"
    assert contract.as_dict() == {
        "north": "grass_A",
        "east": "grass_B",
        "south": "grass_C",
        "west": "grass_A",
    }


def test_source_edge_profiles_are_compatible_for_same_edge_id():
    config = TilesetConfig(source_tile_size=128, patch_size=48, patch_overlap=12, strip_width=16, variants=8)
    source = make_grass_source((128, 128))
    database = PatchDatabase.from_image(source, patch_size=48, overlap=12, seed=42)
    tiles = SourceTileSynthesizer(database, config, source).synthesize()

    validation = validate_edge_profiles(tiles)

    assert validation["risk_score"] == 0


def test_generated_contract_map_has_only_compatible_neighbors():
    config = TilesetConfig(source_tile_size=128, patch_size=48, patch_overlap=12, strip_width=16, variants=8)
    source = make_grass_source((128, 128))
    database = PatchDatabase.from_image(source, patch_size=48, overlap=12, seed=42)
    tiles = SourceTileSynthesizer(database, config, source).synthesize()

    grid = assemble_contract_map(tiles, columns=10, rows=10, seed=42)

    assert validate_contract_adjacency(grid)


def test_tileset_config_rejects_edge_strip_that_cannot_fit():
    try:
        TilesetConfig(source_tile_size=128, strip_width=65)
    except ValueError as exc:
        assert "strip_width" in str(exc)
    else:
        raise AssertionError("invalid strip width was accepted")


def test_tileset_config_serialization_keeps_palette_and_seed():
    config = TilesetConfig(palette_budget=24, seed=123)

    serialized = config.as_dict()

    assert serialized["palette_budget"] == 24
    assert serialized["seed"] == 123


def test_tileset_compiler_exports_contract_map_and_comparison(tmp_path: Path):
    source_path = tmp_path / "grass_master.png"
    make_grass_source((128, 128)).save(source_path)
    output = tmp_path / "experiment"
    config = TilesetConfig(
        output_root=output,
        variants=8,
        edge_types=3,
        source_tile_size=128,
        patch_size=48,
        patch_overlap=12,
        strip_width=16,
        palette_budget=8,
        map_columns=3,
        map_rows=2,
        seed=42,
    )

    result = TilesetSourceCompiler().build(source_path, config)

    assert len(result.source_tiles) == 8
    assert all(tile.image.size == (128, 128) for tile in result.source_tiles)
    assert all(tile.spec.edge_contract for tile in result.source_tiles)
    assert all(Image.open(path).size == (64, 64) for path in result.pixel_tile_paths)
    assert Image.open(result.map_path).size == (3 * 64, 2 * 64)
    assert result.comparison_path.exists()
    assert result.metrics_path.exists()
    assert result.metrics["contract_validation"]["compatible_map"] is True


def test_tileset_compiler_is_deterministic(tmp_path: Path):
    source_path = tmp_path / "grass_master.png"
    make_grass_source((128, 128)).save(source_path)
    config = TilesetConfig(
        output_root=tmp_path / "first",
        variants=8,
        edge_types=3,
        source_tile_size=128,
        patch_size=48,
        patch_overlap=12,
        strip_width=16,
        palette_budget=8,
        map_columns=3,
        map_rows=2,
        seed=42,
    )

    first = TilesetSourceCompiler().build(source_path, config)
    second = TilesetSourceCompiler().build(source_path, TilesetConfig(**{**config.as_dict(), "output_root": tmp_path / "second"}))

    assert first.map_path.read_bytes() == second.map_path.read_bytes()
    assert first.metrics_path.read_bytes() == second.metrics_path.read_bytes()
