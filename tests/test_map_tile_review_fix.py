from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pytest
from PIL import Image, ImageDraw
from typer.testing import CliRunner

from pixel_tile_compiler.asset.acceptance import validate_tile_contract, validate_tileset_contract
from pixel_tile_compiler.cli import app
from pixel_tile_compiler.generation.presets import build_dirt_road_network_spec, build_grass_surface_spec


BACKGROUND = (70, 130, 60, 255)
ROAD = (148, 94, 52, 255)


def _road_mask(size: int, topology: str, *, broken_rows: Iterable[int] = ()) -> np.ndarray:
    mask = np.zeros((size, size), dtype=bool)
    center_start = int(size * 0.39)
    center_end = int(size * 0.61)
    half = size // 2
    if "N" in topology:
        mask[:half, center_start:center_end] = True
    if "E" in topology:
        mask[center_start:center_end, half:] = True
    if "S" in topology:
        mask[half:, center_start:center_end] = True
    if "W" in topology:
        mask[center_start:center_end, :half] = True
    for row in broken_rows:
        mask[row, :] = False
    return mask


def _road_image(topology: str, *, broken_rows: Iterable[int] = ()) -> Image.Image:
    mask = _road_mask(64, topology, broken_rows=broken_rows)
    image = Image.new("RGBA", (64, 64), BACKGROUND)
    pixels = np.asarray(image).copy()
    pixels[mask] = ROAD
    return Image.fromarray(pixels, mode="RGBA")


def _road_contract(tile_id: str):
    spec = build_dirt_road_network_spec()
    tile = next(item for item in spec.tiles if item.id == tile_id)
    return tile, spec.network_contracts["road"]


def test_review_negative_extra_connector_is_rejected() -> None:
    tile, contract = _road_contract("road_ns")
    image = _road_image("NS")
    ImageDraw.Draw(image).rectangle((39, 25, 63, 37), fill=ROAD)

    result = validate_tile_contract(tile, _road_image("NS"), image, palette_budget=8, network_contract=contract)

    assert result["status"] == "rejected"
    assert result["final_png"]["connector_validation"]["edges"]["E"]["status"] == "rejected"


def test_review_negative_overwide_connector_is_rejected() -> None:
    tile, contract = _road_contract("road_ns")
    image = Image.new("RGBA", (64, 64), BACKGROUND)
    ImageDraw.Draw(image).rectangle((16, 0, 46, 63), fill=ROAD)

    result = validate_tile_contract(tile, image, image, palette_budget=8, network_contract=contract)

    assert result["status"] == "rejected"
    assert result["final_png"]["connector_validation"]["edges"]["N"]["status"] == "rejected"


def test_review_negative_disconnected_middle_is_rejected() -> None:
    tile, contract = _road_contract("road_ns")
    source = _road_image("NS")
    final = _road_image("NS", broken_rows=range(29, 35))

    result = validate_tile_contract(tile, source, final, palette_budget=8, network_contract=contract)

    assert result["status"] == "rejected"
    assert result["final_png"]["connector_validation"]["connectivity"]["status"] == "rejected"


@pytest.mark.parametrize("topology", ["N", "NS", "NE", "NES", "NESW"])
def test_valid_network_shapes_preserve_declared_connectors(topology: str) -> None:
    tile, contract = _road_contract(f"road_{topology.lower()}")
    image = _road_image(topology)

    result = validate_tile_contract(tile, image, image, palette_budget=8, network_contract=contract)

    assert result["status"] == "accepted"
    assert result["final_png"]["connector_validation"]["connectivity"]["status"] == "accepted"
    assert all(corner is False for corner in result["final_png"]["connector_validation"]["corners"].values())


def test_valid_road_package_accepts_all_existing_sixteen_topologies() -> None:
    spec = build_dirt_road_network_spec()
    images = {
        tile.id: _road_image("".join(tile.connectors))
        for tile in spec.tiles
    }

    result = validate_tileset_contract(
        spec.tiles,
        images,
        images,
        spec.network_contracts,
        palette_budget=8,
        shared_edge_contract=spec.shared_edge_contract,
    )

    assert result["status"] == "accepted"
    assert result["checked_tile_count"] == 16
    assert result["shared_edge_validation"]["status"] == "not_applicable"
    assert result["tiles"]["road_empty"]["input_mask"]["status"] == "accepted"
    assert result["tiles"]["road_empty"]["input_mask"]["empty_evidence"]["status"] == "accepted"


def test_unobservable_nonempty_network_mask_is_not_accepted() -> None:
    tile, contract = _road_contract("road_ns")
    image = Image.new("RGBA", (64, 64), BACKGROUND)

    result = validate_tile_contract(tile, image, image, palette_budget=8, network_contract=contract)

    assert result["status"] == "rejected"
    assert result["input_mask"]["status"] == "unverified"
    assert result["final_png"]["connector_validation"]["status"] == "unverified"


def test_review_negative_surface_shared_edges_are_rejected() -> None:
    spec = build_grass_surface_spec()
    contract = spec.shared_edge_contract.model_copy(update={"validation_mode": "exact_rgb"})
    images = {
        tile.id: Image.new("RGBA", (64, 64), (230, 230, 20, 255) if index % 2 else (20, 100, 20, 255))
        for index, tile in enumerate(spec.tiles)
    }

    result = validate_tileset_contract(
        spec.tiles,
        images,
        images,
        spec.network_contracts,
        palette_budget=8,
        shared_edge_contract=contract,
    )

    assert result["status"] == "rejected"
    assert result["shared_edge_validation"]["status"] == "rejected"
    assert result["shared_edge_validation"]["checked_pair_count"] == 512


def test_valid_surface_shared_edge_set_is_accepted_for_all_reorderable_pairs() -> None:
    spec = build_grass_surface_spec()
    contract = spec.shared_edge_contract.model_copy(update={"validation_mode": "exact_rgb"})
    images = {tile.id: Image.new("RGBA", (64, 64), (20, 100, 20, 255)) for tile in spec.tiles}

    result = validate_tileset_contract(
        spec.tiles,
        images,
        images,
        spec.network_contracts,
        palette_budget=8,
        shared_edge_contract=contract,
    )

    assert result["status"] == "accepted"
    assert result["shared_edge_validation"]["status"] == "accepted"
    assert result["shared_edge_validation"]["directions"] == ["E/W", "S/N"]


def test_surface_contract_role_mismatch_rejects_the_tileset() -> None:
    spec = build_grass_surface_spec()
    contract = spec.shared_edge_contract.model_copy(update={"role": "network"})
    images = {tile.id: Image.new("RGBA", (64, 64), (20, 100, 20, 255)) for tile in spec.tiles}

    result = validate_tileset_contract(
        spec.tiles,
        images,
        images,
        spec.network_contracts,
        palette_budget=8,
        shared_edge_contract=contract,
    )

    assert result["status"] == "rejected"
    assert result["shared_edge_validation"]["status"] == "rejected"
    assert result["shared_edge_validation"]["reason"] == "surface_contract_role_mismatch"
    assert result["shared_edge_validation"]["checked_pair_count"] == 0


def test_compile_generated_sheet_cli_does_not_succeed_for_rejected_gate(tmp_path) -> None:
    source = tmp_path / "source.png"
    sheet = Image.new("RGBA", (256, 256), BACKGROUND)
    draw = ImageDraw.Draw(sheet)
    for row in range(4):
        for column in range(4):
            color = (20, 100, 20, 255) if (row + column) % 2 == 0 else (230, 230, 20, 255)
            draw.rectangle(
                (column * 64, row * 64, column * 64 + 63, row * 64 + 63),
                fill=color,
            )
    sheet.save(source)

    result = CliRunner().invoke(
        app,
        [
            "compile-generated-sheet",
            "--spec",
            "specs/grass_surface_v1.json",
            "--image",
            str(source),
            "--output",
            str(tmp_path / "compiled"),
            "--palette",
            "16",
            "--no-debug",
        ],
    )

    assert result.exit_code == 1, result.stdout
    assert "rejected" in result.stdout
