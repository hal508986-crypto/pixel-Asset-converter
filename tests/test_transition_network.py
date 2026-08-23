from pathlib import Path

import numpy as np
from PIL import Image

from pixel_tile_compiler.transition_network.assembler import (
    assemble_semantic_grid,
    validate_semantic_adjacency,
)
from pixel_tile_compiler.transition_network.contracts import (
    EdgeSemanticProfile,
    SemanticEdgeContract,
    SemanticTile,
    SemanticTileSpec,
)
from pixel_tile_compiler.transition_network.masks import (
    ROAD_TOPOLOGIES,
    build_road_mask,
    build_transition_mask,
)
from pixel_tile_compiler.transition_network.metrics import (
    network_map_metrics,
    transition_map_metrics,
)
from pixel_tile_compiler.transition_network.network import NetworkTileCompiler, NetworkTileConfig
from pixel_tile_compiler.transition_network.transition import TransitionTileCompiler, TransitionTileConfig
from pixel_tile_compiler.transition_network.study import (
    TransitionNetworkStudyConfig,
    TransitionNetworkStudyRunner,
)


def make_surface(color: tuple[int, int, int], size: tuple[int, int] = (96, 96)) -> Image.Image:
    return Image.new("RGBA", size, (*color, 255))


def make_road(size: tuple[int, int] = (96, 96)) -> Image.Image:
    image = Image.new("RGBA", size, (104, 72, 44, 255))
    pixels = image.load()
    for y in range(size[1]):
        for x in range(size[0]):
            value = (x * 7 + y * 11) % 18
            pixels[x, y] = (104 + value, 72 + value // 2, 44 + value // 3, 255)
    return image


def test_semantic_transition_contract_exposes_actual_outside_materials():
    compiler = TransitionTileCompiler(
        TransitionTileConfig(output_root=Path("unused"), source_size=96, variants=1, pixelize=False)
    )
    result = compiler.build_pair(
        make_surface((60, 120, 60)),
        make_surface((30, 70, 100)),
        material_a="grass",
        material_b="forest_canopy",
        family_id="grass_forest",
    )

    ns = next(tile for tile in result.tiles if tile.spec.orientation == "NS" and tile.spec.material_a == "grass")

    assert ns.spec.semantic_contract.north.material == "grass"
    assert ns.spec.semantic_contract.south.material == "forest_canopy"
    assert ns.spec.semantic_contract.east.materials == ("grass", "forest_canopy")
    assert ns.spec.semantic_contract.orientation == "NS"


def test_road_masks_connect_only_the_declared_topology_boundaries():
    assert set(ROAD_TOPOLOGIES) >= {"NS", "EW", "NE", "NW", "SE", "SW"}

    for topology in ROAD_TOPOLOGIES:
        mask = build_road_mask((64, 64), topology, width=0.25)
        assert mask.shape == (64, 64)
        assert mask.dtype == np.bool_
        assert bool(mask.any())
        if topology == "NS":
            assert mask[:, 31:33].mean() > 0.8
            assert mask[31:33, :].mean() < 0.5
        if topology == "EW":
            assert mask[31:33, :].mean() > 0.8
            assert mask[:, 31:33].mean() < 0.5


def test_transition_masks_follow_the_explicit_orientation():
    ns = build_transition_mask((10, 10), "NS", boundary=0.5)
    ew = build_transition_mask((10, 10), "EW", boundary=0.5)

    assert ns.shape == (10, 10)
    assert ns[:, :].mean() == 0.5
    assert ns[:5, :].all()
    assert not ns[5:, :].any()
    assert ew[:, :5].all()
    assert not ew[:, 5:].any()


def test_network_compiler_emits_required_topologies_and_pixel_tiles(tmp_path: Path):
    config = NetworkTileConfig(
        output_root=tmp_path / "network",
        source_size=96,
        variants=1,
        pixelize=True,
        debug_enabled=False,
    )
    result = NetworkTileCompiler(config).build(
        make_surface((70, 130, 65)),
        make_road(),
        base_material="grass",
    )

    assert {tile.spec.topology for tile in result.tiles} >= {"NS", "EW", "NE", "NW", "SE", "SW"}
    assert all(tile.pixel_image is not None and tile.pixel_image.size == (64, 64) for tile in result.tiles)
    assert (tmp_path / "network" / "manifest.json").exists()


def test_semantic_assembler_accepts_contract_compatible_neighbors():
    grass = EdgeSemanticProfile(role="surface", material="grass")
    forest = EdgeSemanticProfile(role="surface", material="forest_canopy")
    transition = EdgeSemanticProfile(
        role="transition_boundary",
        material="grass",
        materials=("grass", "forest_canopy"),
        feature_type="material_boundary",
        orientation="NS",
    )
    first = SemanticTile(
        SemanticTileSpec("grass", "surface", SemanticEdgeContract(grass, grass, grass, grass)),
        make_surface((60, 120, 60)),
    )
    boundary = SemanticTile(
        SemanticTileSpec(
            "boundary",
            "transition",
            SemanticEdgeContract(grass, transition, forest, transition),
        ),
        make_surface((60, 100, 80)),
    )
    last = SemanticTile(
        SemanticTileSpec("forest", "surface", SemanticEdgeContract(forest, forest, forest, forest)),
        make_surface((30, 70, 100)),
    )

    grid = assemble_semantic_grid(
        [[first, first], [boundary, boundary], [last, last]],
        columns=2,
        rows=3,
        seed=42,
    )

    assert validate_semantic_adjacency(grid)


def test_network_and_transition_metrics_are_concrete():
    image = make_surface((80, 100, 60), (128, 128))
    mask = build_road_mask((128, 128), "NS", width=0.25)
    network = network_map_metrics(image, mask, columns=2, rows=2, tile_size=64)
    transition = transition_map_metrics(image, mask, columns=2, rows=2, tile_size=64)

    assert 0 <= network["road_continuity_score"] <= 1
    assert 0 <= network["road_center_alignment_score"] <= 1
    assert 0 <= network["road_width_stability_score"] <= 1
    assert 0 <= network["road_break_risk"] <= 1
    assert 0 <= network["road_presence_score"] <= 1
    assert 0 <= transition["boundary_continuity_score"] <= 1
    assert 0 <= transition["semantic_clarity_score"] <= 1
    assert 0 <= transition["boundary_abruptness_score"] <= 1
    assert 0 <= transition["boundary_presence_score"] <= 1


def test_transition_compiler_includes_forest_road_handoff(tmp_path: Path):
    result = TransitionTileCompiler(
        TransitionTileConfig(
            output_root=tmp_path / "transition",
            source_size=96,
            variants=1,
            pixelize=False,
        )
    ).build_network_handoff(
        make_surface((30, 70, 100)),
        make_road(),
        base_material="forest_canopy",
    )

    assert result.family_id == "forest_road"
    assert any(tile.spec.material_a == "forest_canopy" and tile.spec.material_b == "dirt_road" for tile in result.tiles)


def test_study_runner_writes_manifest_maps_metrics_and_ranking(tmp_path: Path):
    source_paths = {
        "grass": tmp_path / "grass.png",
        "forest_canopy": tmp_path / "forest.png",
        "dirt_road": tmp_path / "road.png",
    }
    make_surface((70, 130, 65)).save(source_paths["grass"])
    make_surface((30, 70, 100)).save(source_paths["forest_canopy"])
    make_road().save(source_paths["dirt_road"])
    config = TransitionNetworkStudyConfig(
        output_root=tmp_path / "study",
        sources=source_paths,
        map_columns=3,
        map_rows=4,
        source_size=96,
        surface_variants=1,
        surface_edge_types=1,
        network_variants=1,
        transition_variants=1,
        pixelize=False,
        debug_enabled=False,
    )

    result = TransitionNetworkStudyRunner().run(config)

    assert result.manifest_path.exists()
    assert result.ranking_path.exists()
    assert result.montage_path.exists()
    assert (config.output_root / "maps" / "road_demo_forest" / "contract_aware.png").exists()
    assert (config.output_root / "metrics" / "comparison_table.json").exists()
