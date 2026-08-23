from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from pixel_tile_compiler.transition_network.contracts import (
    EdgeSemanticProfile,
    SemanticEdgeContract,
    contracts_compatible,
)
from pixel_tile_compiler.transition_network.graph import (
    GridPoint,
    NetworkEdge,
    NetworkGraph,
    NetworkTopology,
    NetworkType,
    NetworkTypeRules,
)
from pixel_tile_compiler.transition_network.river import (
    RiverFlowResolver,
    RiverGraphValidationError,
    RiverRenderSpec,
    RiverNetworkRenderer,
    build_river_geometry,
    validate_river_graph,
)


def p(x: int, y: int) -> GridPoint:
    return GridPoint(x, y)


def directed_path(points: list[tuple[int, int]]) -> tuple[NetworkEdge, ...]:
    return tuple(NetworkEdge(p(*first), p(*second), directed=True) for first, second in zip(points, points[1:]))


def merge_graph() -> NetworkGraph:
    edges = (
        *directed_path([(1, 0), (1, 1), (1, 2)]),
        *directed_path([(0, 2), (1, 2), (2, 2)]),
    )
    return NetworkGraph(width=4, height=4, nodes=frozenset(), edges=edges)


def test_directed_edges_are_supported_by_the_same_generic_network_graph():
    graph = NetworkGraph(width=3, height=3, edges=directed_path([(0, 1), (1, 1)]))

    assert graph.edges[0].directed is True
    assert graph.outgoing_neighbors(p(0, 1)) == frozenset({p(1, 1)})
    assert graph.incoming_neighbors(p(1, 1)) == frozenset({p(0, 1)})
    assert graph.neighbors(p(1, 1)) == frozenset({p(0, 1)})


def test_network_type_rules_are_generic_but_river_rejects_cross():
    road = NetworkTypeRules.for_type(NetworkType.ROAD)
    river = NetworkTypeRules.for_type(NetworkType.RIVER)

    assert road.allows_cross is True
    assert river.allows_cross is False
    assert NetworkTopology.NESW in road.allowed_topologies
    assert NetworkTopology.NESW not in river.allowed_topologies

    cross = NetworkGraph(
        width=3,
        height=3,
        edges=(
            NetworkEdge(p(1, 1), p(1, 0), directed=True),
            NetworkEdge(p(1, 1), p(2, 1), directed=True),
            NetworkEdge(p(1, 1), p(1, 2), directed=True),
            NetworkEdge(p(0, 1), p(1, 1), directed=True),
        ),
    )
    with pytest.raises(RiverGraphValidationError, match="cross"):
        validate_river_graph(cross)


def test_river_flow_resolver_exposes_incoming_and_outgoing_sides():
    cell = RiverFlowResolver().resolve(merge_graph(), p(1, 2))

    assert cell.topology is NetworkTopology.NEW
    assert cell.incoming == ("N", "W")
    assert cell.outgoing == ("E",)
    assert cell.is_merge is True


def test_river_validation_rejects_directed_cycle_and_split_by_default():
    cycle = NetworkGraph(
        width=3,
        height=3,
        edges=(
            NetworkEdge(p(0, 1), p(1, 1), directed=True),
            NetworkEdge(p(1, 1), p(1, 2), directed=True),
            NetworkEdge(p(1, 2), p(0, 2), directed=True),
            NetworkEdge(p(0, 2), p(0, 1), directed=True),
        ),
    )
    with pytest.raises(RiverGraphValidationError, match="cycle"):
        validate_river_graph(cycle)

    split = NetworkGraph(
        width=3,
        height=3,
        edges=(
            NetworkEdge(p(1, 1), p(0, 1), directed=True),
            NetworkEdge(p(1, 1), p(1, 0), directed=True),
        ),
    )
    with pytest.raises(RiverGraphValidationError, match="split"):
        validate_river_graph(split)


def test_river_geometry_has_smooth_body_and_bank_outside_body():
    geometry = build_river_geometry(
        (128, 128),
        NetworkTopology.NE,
        incoming=("N",),
        outgoing=("E",),
        spec=RiverRenderSpec(base_width_ratio=0.26, width_variation=0.04, bank_width_ratio=0.04),
        seed=42,
    )

    assert geometry.body_mask.dtype == np.bool_
    assert geometry.centerline_mask.any()
    assert geometry.body_mask.any()
    assert geometry.bank_mask.any()
    assert not np.any(geometry.bank_mask & geometry.body_mask)
    assert geometry.body_mask[0, 64]
    assert geometry.body_mask[64, -1]


def test_river_renderer_emits_flow_contract_and_pixel_tile(tmp_path: Path):
    renderer = RiverNetworkRenderer(
        RiverRenderSpec(source_size=96, variants=1, palette_budget=24, pixelize=True, output_root=tmp_path / "river")
    )
    tile = renderer.render_tile(
        base_source=Image.new("RGBA", (96, 96), (70, 130, 65, 255)),
        water_source=Image.new("RGBA", (96, 96), (30, 130, 170, 255)),
        bank_source=Image.new("RGBA", (96, 96), (120, 85, 50, 255)),
        base_material="grass",
        topology=NetworkTopology.NEW,
        incoming=("N", "W"),
        outgoing=("E",),
        variant=0,
    )

    assert tile.pixel_image is not None
    assert tile.pixel_image.size == (64, 64)
    assert tile.spec.semantic_contract.north.flow == "in"
    assert tile.spec.semantic_contract.east.flow == "out"
    assert tile.spec.semantic_contract.west.flow == "in"
    assert tile.spec.metadata["bank_width_ratio"] == 0.04


def test_river_flow_contract_matches_outgoing_to_incoming_neighbor():
    out_profile = EdgeSemanticProfile(
        role="network_connector", material="water", feature_type="river", feature_center=0.5, feature_width=0.26, flow="out"
    )
    in_profile = EdgeSemanticProfile(
        role="network_connector", material="water", feature_type="river", feature_center=0.5, feature_width=0.26, flow="in"
    )
    wrong_profile = EdgeSemanticProfile(
        role="network_connector", material="water", feature_type="river", feature_center=0.5, feature_width=0.26, flow="out"
    )
    first = SemanticEdgeContract(out_profile, out_profile, out_profile, out_profile)
    second = SemanticEdgeContract(in_profile, in_profile, in_profile, in_profile)
    wrong = SemanticEdgeContract(wrong_profile, wrong_profile, wrong_profile, wrong_profile)

    assert contracts_compatible(first, second, "east")
    assert not contracts_compatible(first, wrong, "east")
