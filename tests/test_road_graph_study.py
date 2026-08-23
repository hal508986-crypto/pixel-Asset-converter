from pathlib import Path

from PIL import Image

from pixel_tile_compiler.transition_network.graph import NetworkGraph, NetworkSpec
from pixel_tile_compiler.transition_network.road_graph import (
    RoadGraphStudyConfig,
    RoadGraphStudyRunner,
)


def make_source(color: tuple[int, int, int], size: int = 96) -> Image.Image:
    image = Image.new("RGBA", (size, size), (*color, 255))
    pixels = image.load()
    for y in range(size):
        for x in range(size):
            value = (x * 3 + y * 5) % 9
            pixels[x, y] = (min(255, color[0] + value), min(255, color[1] + value), min(255, color[2] + value), 255)
    return image


def test_road_graph_study_builds_640_map_manifest_debug_and_metrics(tmp_path: Path):
    sources = {
        "grass": tmp_path / "grass.png",
        "forest_canopy": tmp_path / "forest.png",
        "dirt_road": tmp_path / "road.png",
    }
    make_source((70, 130, 65)).save(sources["grass"])
    make_source((30, 70, 100)).save(sources["forest_canopy"])
    make_source((104, 72, 44)).save(sources["dirt_road"])
    graph = NetworkGraph.from_paths(
        width=3,
        height=3,
        paths=[[[0, 1], [1, 1], [2, 1]], [[1, 0], [1, 1], [1, 2]]],
    )
    surface_map = (
        ("grass", "grass", "forest_canopy"),
        ("grass", "grass", "forest_canopy"),
        ("grass", "grass", "forest_canopy"),
    )
    config = RoadGraphStudyConfig(
        output_root=tmp_path / "study",
        graph=graph,
        surface_map=surface_map,
        sources=sources,
        network=NetworkSpec(variants_per_topology=1),
        tile_size=64,
        source_size=96,
        surface_variants=1,
        surface_edge_types=1,
        pixelize=False,
        debug_enabled=False,
        seed=42,
    )

    result = RoadGraphStudyRunner().run(config)

    assert result.manifest_path.exists()
    assert result.metrics_path.exists()
    assert result.logical_graph_preview_path.exists()
    assert result.topology_debug_path.exists()
    assert Image.open(result.output_root / "maps" / "graph_resolved.png").size == (192, 192)
    assert len(result.manifest["cells"]) == 9
    assert result.metrics["methods"]["graph_resolved"]["graph_fidelity_score"] == 1.0
    assert (result.output_root / "summary" / "report.md").exists()
