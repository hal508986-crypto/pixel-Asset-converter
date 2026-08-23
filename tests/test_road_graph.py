import pytest

from pixel_tile_compiler.transition_network.graph import (
    GridPoint,
    NetworkEdge,
    NetworkGraph,
    NetworkSpec,
    NetworkTopology,
    NetworkTopologyResolver,
    connector_mask_for_sides,
    topology_for_connector_mask,
)
from pixel_tile_compiler.transition_network.masks import ROAD_TOPOLOGIES, build_road_mask
import numpy as np


def point(x: int, y: int) -> GridPoint:
    return GridPoint(x, y)


def test_connector_mask_and_topology_cover_empty_dead_end_curve_t_and_cross():
    assert connector_mask_for_sides() == 0
    assert connector_mask_for_sides("N") == 1
    assert connector_mask_for_sides("E") == 2
    assert connector_mask_for_sides("S") == 4
    assert connector_mask_for_sides("W") == 8
    assert connector_mask_for_sides("N", "S") == 5
    assert connector_mask_for_sides("E", "W") == 10
    assert connector_mask_for_sides("N", "E") == 3
    assert connector_mask_for_sides("N", "W") == 9
    assert connector_mask_for_sides("S", "E") == 6
    assert connector_mask_for_sides("S", "W") == 12
    assert connector_mask_for_sides("N", "E", "S") == 7
    assert connector_mask_for_sides("N", "E", "W") == 11
    assert connector_mask_for_sides("N", "S", "W") == 13
    assert connector_mask_for_sides("E", "S", "W") == 14
    assert connector_mask_for_sides("N", "E", "S", "W") == 15
    assert topology_for_connector_mask(0) is NetworkTopology.EMPTY
    assert topology_for_connector_mask(14) is NetworkTopology.ESW
    assert NetworkTopology.SEW is NetworkTopology.ESW
    assert connector_mask_for_sides("E", "S", "W") == 14
    assert topology_for_connector_mask(15) is NetworkTopology.NESW


def test_graph_resolver_finds_all_four_neighbors_and_empty_cells():
    center = point(4, 4)
    graph = NetworkGraph(
        width=10,
        height=10,
        nodes={center, point(4, 3), point(5, 4), point(4, 5), point(3, 4)},
        edges=(
            NetworkEdge(center, point(4, 3)),
            NetworkEdge(center, point(5, 4)),
            NetworkEdge(center, point(4, 5)),
            NetworkEdge(center, point(3, 4)),
        ),
    )
    resolver = NetworkTopologyResolver()
    resolved = resolver.resolve(graph, center)
    assert resolved.connector_mask == 15
    assert resolved.topology is NetworkTopology.NESW
    assert resolver.resolve(graph, point(0, 0)).topology is NetworkTopology.EMPTY


@pytest.mark.parametrize(
    ("neighbors", "expected"),
    [
        ((point(2, 1),), NetworkTopology.N),
        ((point(3, 2),), NetworkTopology.E),
        ((point(2, 3),), NetworkTopology.S),
        ((point(1, 2),), NetworkTopology.W),
        ((point(2, 1), point(2, 3)), NetworkTopology.NS),
        ((point(1, 2), point(3, 2)), NetworkTopology.EW),
        ((point(2, 1), point(3, 2)), NetworkTopology.NE),
        ((point(2, 1), point(1, 2)), NetworkTopology.NW),
        ((point(2, 3), point(3, 2)), NetworkTopology.SE),
        ((point(2, 3), point(1, 2)), NetworkTopology.SW),
        ((point(2, 1), point(3, 2), point(2, 3)), NetworkTopology.NES),
        ((point(2, 1), point(3, 2), point(1, 2)), NetworkTopology.NEW),
        ((point(2, 1), point(2, 3), point(1, 2)), NetworkTopology.NSW),
        ((point(3, 2), point(2, 3), point(1, 2)), NetworkTopology.ESW),
    ],
)
def test_graph_resolver_maps_local_degree_to_topology(neighbors, expected):
    center = point(2, 2)
    graph = NetworkGraph(
        width=5,
        height=5,
        nodes={center, *neighbors},
        edges=tuple(NetworkEdge(center, neighbor) for neighbor in neighbors),
    )
    assert NetworkTopologyResolver().resolve(graph, center).topology is expected


def test_paths_normalize_duplicate_edges_and_emit_warnings_for_isolated_node():
    graph = NetworkGraph.from_paths(
        width=10,
        height=10,
        paths=[[[1, 1], [1, 2], [1, 3]], [[1, 3], [1, 2], [1, 1]]],
        extra_nodes=[[8, 8]],
    )
    assert len(graph.edges) == 2
    report = graph.validate()
    assert report.is_valid
    assert report.isolated_nodes == (GridPoint(8, 8),)
    assert any("isolated" in warning for warning in report.warnings)


def test_grid_outside_node_is_rejected():
    with pytest.raises(ValueError, match="grid"):
        NetworkGraph(width=2, height=2, nodes={GridPoint(2, 0)}, edges=())


def test_from_mapping_rejects_diagonal_and_non_adjacent_edges():
    with pytest.raises(ValueError, match="diagonal"):
        NetworkGraph.from_mapping(
            {
                "width": 10,
                "height": 10,
                "edges": [[[0, 0], [1, 1]]],
            }
        )
    with pytest.raises(ValueError, match="adjacent"):
        NetworkGraph.from_mapping(
            {
                "width": 10,
                "height": 10,
                "edges": [[[0, 0], [0, 2]]],
            }
        )


def test_network_spec_is_generic_and_validates_geometry():
    spec = NetworkSpec(network_type="road", material="dirt_road")
    assert spec.width_ratio == 0.22
    assert spec.network_type.value == "road"
    with pytest.raises(ValueError, match="width_ratio"):
        NetworkSpec(network_type="river", material="water", width_ratio=1.2)


def test_road_masks_support_every_resolved_topology_and_shared_edge_width():
    assert set(ROAD_TOPOLOGIES) >= {
        "N", "E", "S", "W", "NS", "EW", "NE", "NW", "SE", "SW",
        "NES", "NEW", "NSW", "ESW", "NESW",
    }
    for topology in ROAD_TOPOLOGIES:
        mask = build_road_mask((128, 128), topology, width=0.22, center=0.5)
        assert mask.dtype == np.bool_
        assert mask.any()
        assert bool(mask[64, 64])
    for topology, side in (("N", 0), ("E", 1), ("S", 2), ("W", 3)):
        mask = build_road_mask((128, 128), topology, width=0.22, center=0.5)
        edge_profile = [mask[0, :].mean(), mask[:, -1].mean(), mask[-1, :].mean(), mask[:, 0].mean()]
        assert edge_profile[side] > 0.15
        assert sum(value > 0.15 for value in edge_profile) == 1
