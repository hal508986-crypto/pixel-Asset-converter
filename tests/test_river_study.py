from pathlib import Path

from PIL import Image

from pixel_tile_compiler.transition_network.graph import NetworkGraph, NetworkSpec, NetworkType
from pixel_tile_compiler.transition_network.river_study import (
    RiverGraphStudyConfig,
    RiverGraphStudyRunner,
)


def source(color: tuple[int, int, int], size: int = 96) -> Image.Image:
    image = Image.new("RGBA", (size, size), (*color, 255))
    pixels = image.load()
    for y in range(size):
        for x in range(size):
            variation = (x * 5 + y * 7) % 10
            pixels[x, y] = tuple(min(255, channel + variation) for channel in color) + (255,)
    return image


def test_river_study_builds_comparison_maps_debug_outputs_and_manifest(tmp_path: Path):
    sources = {
        "grass": tmp_path / "grass.png",
        "forest_canopy": tmp_path / "forest.png",
        "water": tmp_path / "water.png",
        "bank": tmp_path / "bank.png",
    }
    source((70, 130, 65)).save(sources["grass"])
    source((30, 70, 100)).save(sources["forest_canopy"])
    source((30, 130, 175)).save(sources["water"])
    source((120, 85, 50)).save(sources["bank"])
    graph = NetworkGraph.from_mapping(
        {
            "width": 3,
            "height": 3,
            "edges": [
                {"source": [0, 1], "target": [1, 1], "directed": True},
                {"source": [1, 1], "target": [2, 1], "directed": True},
                {"source": [1, 0], "target": [1, 1], "directed": True},
            ],
        }
    )
    config = RiverGraphStudyConfig(
        output_root=tmp_path / "river-study",
        graph=graph,
        surface_map=(("grass", "grass", "forest_canopy"),) * 3,
        sources=sources,
        network=NetworkSpec(network_type=NetworkType.RIVER, material="water", width_ratio=0.26, variants_per_topology=1),
        source_size=96,
        variants=1,
        pixelize=False,
        debug_enabled=False,
        seed=42,
    )

    result = RiverGraphStudyRunner().run(config)

    assert Image.open(result.output_root / "maps" / "river_renderer.png").size == (192, 192)
    assert len(result.manifest["cells"]) == 9
    assert result.metrics["methods"]["river_renderer"]["graph_fidelity_score"] == 1.0
    assert result.metrics["methods"]["river_renderer"]["flow_fidelity_score"] == 1.0
    assert result.metrics["methods"]["river_renderer"]["bank_continuity_score"] > 0.0
    for relative in (
        "logical_river_graph.json",
        "logical_river_graph_preview.png",
        "topology_debug.png",
        "debug/river_centerline.png",
        "debug/river_body_mask.png",
        "debug/river_bank_mask.png",
        "debug/river_source_preview.png",
        "maps/road_like_river_baseline.png",
        "maps/river_renderer_variants.png",
        "maps/comparison.png",
        "summary/report.md",
    ):
        assert (result.output_root / relative).exists(), relative
