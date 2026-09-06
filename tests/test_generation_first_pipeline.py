from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image
from typer.testing import CliRunner

from pixel_tile_compiler.asset.pipeline import process_generated_sheet, validate_asset_package
from pixel_tile_compiler.cli import app
from pixel_tile_compiler.generation.adapter import GeneratedImage, ImageGenerationAdapter, McpImageGenerationAdapter
from pixel_tile_compiler.generation.pipeline import GenerationFirstPipeline
from pixel_tile_compiler.generation.presets import build_dirt_road_network_spec, build_grass_surface_spec
from pixel_tile_compiler.generation.request_compiler import GenerationRequestCompiler
from pixel_tile_compiler.generation.spec import TileSpec, TilesetSpec
from pixel_tile_compiler.sheet.splitter import GridSplitter


def _sheet(path: Path, width: int = 258, height: int = 258) -> Path:
    image = Image.new("RGBA", (width, height), (50, 110, 60, 255))
    for y in range(height):
        for x in range(width):
            image.putpixel((x, y), ((x * 3) % 256, (y * 5) % 256, ((x + y) * 2) % 256, 255))
    image.save(path)
    return path


def test_tileset_spec_rejects_duplicate_cells_and_requires_complete_grid() -> None:
    base = build_grass_surface_spec()
    duplicate = list(base.tiles)
    duplicate[1] = duplicate[0].model_copy(update={"id": "duplicate"})

    invalid = base.model_dump(mode="json")
    invalid["tiles"] = [tile.model_dump(mode="json") for tile in duplicate]
    with pytest.raises(ValueError, match="duplicate cell"):
        TilesetSpec.model_validate(invalid)


def test_grass_and_road_presets_emit_complete_4x4_contracts() -> None:
    grass = build_grass_surface_spec()
    road = build_dirt_road_network_spec()

    assert len(grass.tiles) == 16
    assert len(road.tiles) == 16
    assert {tile.semantic for tile in grass.tiles} == {"surface"}
    assert {tile.semantic for tile in road.tiles} == {"network"}
    assert {tile.network for tile in road.tiles} == {"road"}
    assert {tuple(tile.connectors) for tile in road.tiles} >= {(), ("N",), ("N", "E", "S", "W")}


def test_repository_tileset_specs_are_canonical_and_complete() -> None:
    root = Path(__file__).resolve().parents[1]
    grass = TilesetSpec.from_json_file(root / "specs" / "grass_surface_v1.json")
    road = TilesetSpec.from_json_file(root / "specs" / "dirt_road_network_v1.json")

    assert (grass.tileset_id, grass.grid_columns, grass.grid_rows, len(grass.tiles)) == (
        "grass_surface_v1",
        4,
        4,
        16,
    )
    assert (road.tileset_id, road.grid_columns, road.grid_rows, len(road.tiles)) == (
        "dirt_road_network_v1",
        4,
        4,
        16,
    )
    assert set(road.network_contracts) == {"road"}
    assert grass.model_dump(mode="json") == build_grass_surface_spec().model_dump(mode="json")
    assert road.model_dump(mode="json") == build_dirt_road_network_spec().model_dump(mode="json")


def test_generation_request_is_compiled_from_spec_not_prompt_text() -> None:
    request = GenerationRequestCompiler().compile(build_grass_surface_spec())
    payload = request.model_dump(mode="json")

    assert set(
        (
            "task",
            "intent",
            "output_contract",
            "art_direction",
            "palette_direction",
            "shared_surface_contract",
            "shared_edge_contract",
            "feature_scale_contract",
            "material_contracts",
            "network_contracts",
            "tile_manifest",
            "global_consistency_rules",
            "strict_negative_constraints",
            "split_contract",
            "final_instruction",
        )
    ) <= payload.keys()
    assert payload["output_contract"]["grid_columns"] == 4
    assert len(payload["tile_manifest"]) == 16
    assert "4x4" in payload["final_instruction"]


def test_grid_splitter_crops_non_divisible_sheet_deterministically_and_normalizes() -> None:
    image = Image.new("RGB", (258, 258), (10, 20, 30))

    result = GridSplitter().split(image, columns=4, rows=4, crop_policy="center")

    assert result.crop_box == (1, 1, 257, 257)
    assert len(result.tiles) == 16
    assert all(tile.image.size == (64, 64) for tile in result.tiles)
    assert result.tiles[0].row == 0 and result.tiles[0].column == 0


def test_process_generated_sheet_creates_immutable_raw_sheet_tiles_manifest_and_previews(tmp_path: Path) -> None:
    source = _sheet(tmp_path / "generated.png")
    output = tmp_path / "package"
    spec = build_grass_surface_spec()

    result = process_generated_sheet(spec, source, output)

    assert result.validation["status"] in {"accepted", "warning"}
    assert (output / "sheet_raw.png").read_bytes() == source.read_bytes()
    assert (output / "sheet_normalized.png").exists()
    normalization = json.loads((output / "sheet_normalization.json").read_text(encoding="utf-8"))
    assert normalization["crop_box"] == [1, 1, 257, 257]
    assert normalization["method"] == "deterministic_center_crop"
    assert len(list((output / "tiles").glob("*.png"))) == 16
    assert (output / "manifest.json").exists()
    assert (output / "previews" / "repeat_preview.png").exists()
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["tile_size_px"] == 64
    assert len(manifest["tiles"]) == 16
    assert validate_asset_package(output)["status"] in {"accepted", "warning"}


def test_generated_image_provenance_contract_is_hashable(tmp_path: Path) -> None:
    source = _sheet(tmp_path / "generated.png")
    generated = GeneratedImage.from_path(source, generator="fixture", model="test-model")

    assert generated.raw_sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert generated.raw_dimensions == (258, 258)
    assert generated.generator == "fixture"


def test_mcp_adapter_boundary_accepts_injected_generator_without_core_mcp_dependency(tmp_path: Path) -> None:
    source = _sheet(tmp_path / "generated.png")
    request = GenerationRequestCompiler().compile(build_grass_surface_spec())

    def generate(request, output_root: Path) -> Path:
        assert request.task == "generate_game_tileset_sheet"
        output_root.mkdir(parents=True, exist_ok=True)
        target = output_root / "mcp_result.png"
        target.write_bytes(source.read_bytes())
        return target

    generated = McpImageGenerationAdapter(generate, generator="mcp-fixture", model="fixture-model").generate(
        request,
        tmp_path / "generation",
    )

    assert generated.generator == "mcp-fixture"
    assert generated.model == "fixture-model"
    assert generated.raw_path.name == "sheet_raw.png"
    assert generated.raw_sha256 == hashlib.sha256(source.read_bytes()).hexdigest()


def test_cli_generation_first_commands_process_spec_and_sheet(tmp_path: Path) -> None:
    spec_path = tmp_path / "grass.json"
    build_grass_surface_spec().write_json(spec_path)
    source = _sheet(tmp_path / "generated.png")
    output = tmp_path / "package"

    request_result = CliRunner().invoke(app, ["compile-generation-request", "--spec", str(spec_path)])
    process_result = CliRunner().invoke(
        app,
        ["process-generated-sheet", "--spec", str(spec_path), "--image", str(source), "--output", str(output)],
    )

    assert request_result.exit_code == 0, request_result.stdout
    assert process_result.exit_code == 0, process_result.stdout
    assert (output / "manifest.json").exists()


def test_cli_generate_tileset_refuses_to_fabricate_without_adapter(tmp_path: Path) -> None:
    spec_path = tmp_path / "grass.json"
    build_grass_surface_spec().write_json(spec_path)

    result = CliRunner().invoke(app, ["generate-tileset", "--spec", str(spec_path), "--output", str(tmp_path / "package")])

    assert result.exit_code != 0
    assert "No MCP image generation adapter" in result.output


def test_generation_first_pipeline_preserves_adapter_provenance(tmp_path: Path) -> None:
    source = _sheet(tmp_path / "generated.png")
    result = GenerationFirstPipeline().run(
        build_grass_surface_spec(),
        tmp_path / "package",
        _MockAdapter(source),
    )

    provenance = json.loads((result.output_root / "generation_manifest.json").read_text(encoding="utf-8"))
    assert provenance["generator"] == "mock"
    assert provenance["model"] == "fixture"


class _MockAdapter(ImageGenerationAdapter):
    def __init__(self, image: Path) -> None:
        self.image = image

    def generate(self, request, output_root: Path) -> GeneratedImage:
        output_root.mkdir(parents=True, exist_ok=True)
        target = output_root / "sheet_raw.png"
        target.write_bytes(self.image.read_bytes())
        return GeneratedImage.from_path(target, generator="mock", model="fixture")
