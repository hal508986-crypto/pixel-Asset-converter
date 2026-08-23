"""Semantic network and transition tileset experiment support."""

from .assembler import assemble_image, assemble_semantic_grid, validate_semantic_adjacency
from .contracts import EdgeSemanticProfile, SemanticEdgeContract, SemanticTile, SemanticTileSpec
from .graph import (
    GridPoint,
    NetworkEdge,
    NetworkGraph,
    NetworkSpec,
    NetworkTopology,
    NetworkTopologyResolver,
    NetworkType,
    NetworkTypeRules,
    connector_mask_for_sides,
    connector_mask_for_topology,
    topology_for_connector_mask,
)
from .masks import ROAD_TOPOLOGIES, build_road_mask, build_transition_mask
from .network import NetworkBuildResult, NetworkTileCompiler, NetworkTileConfig
from .road_graph import RoadGraphStudyConfig, RoadGraphStudyResult, RoadGraphStudyRunner, load_road_graph_config
from .renderer import NetworkRenderer
from .river import (
    RiverFlowCell,
    RiverFlowResolver,
    RiverGraphValidationError,
    RiverGraphValidationReport,
    RiverNetworkRenderer,
    RiverRenderSpec,
    build_river_geometry,
    validate_river_graph,
)
from .river_study import RiverGraphStudyConfig, RiverGraphStudyResult, RiverGraphStudyRunner, load_river_graph_config
from .transition import TransitionBuildResult, TransitionTileCompiler, TransitionTileConfig

__all__ = [
    "EdgeSemanticProfile",
    "GridPoint",
    "NetworkEdge",
    "NetworkGraph",
    "NetworkSpec",
    "NetworkTopology",
    "NetworkTopologyResolver",
    "NetworkType",
    "NetworkTypeRules",
    "NetworkBuildResult",
    "NetworkTileCompiler",
    "NetworkTileConfig",
    "ROAD_TOPOLOGIES",
    "SemanticEdgeContract",
    "SemanticTile",
    "SemanticTileSpec",
    "TransitionBuildResult",
    "TransitionTileCompiler",
    "TransitionTileConfig",
    "assemble_image",
    "assemble_semantic_grid",
    "build_road_mask",
    "build_transition_mask",
    "connector_mask_for_sides",
    "connector_mask_for_topology",
    "load_road_graph_config",
    "RoadGraphStudyConfig",
    "RoadGraphStudyResult",
    "RoadGraphStudyRunner",
    "NetworkRenderer",
    "RiverFlowCell",
    "RiverFlowResolver",
    "RiverGraphValidationError",
    "RiverGraphValidationReport",
    "RiverNetworkRenderer",
    "RiverRenderSpec",
    "RiverGraphStudyConfig",
    "RiverGraphStudyResult",
    "RiverGraphStudyRunner",
    "build_river_geometry",
    "load_river_graph_config",
    "topology_for_connector_mask",
    "validate_semantic_adjacency",
    "validate_river_graph",
]
