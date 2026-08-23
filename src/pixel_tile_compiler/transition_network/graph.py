"""Generic logical network graph and connector topology resolution."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Iterable, Sequence


class NetworkType(str, Enum):
    ROAD = "road"
    RIVER = "river"
    WALL = "wall"
    CANAL = "canal"
    RAIL = "rail"


class NetworkTopology(str, Enum):
    EMPTY = "EMPTY"
    N = "N"
    E = "E"
    S = "S"
    W = "W"
    NS = "NS"
    EW = "EW"
    NE = "NE"
    NW = "NW"
    SE = "SE"
    SW = "SW"
    NES = "NES"
    NEW = "NEW"
    NSW = "NSW"
    ESW = "ESW"
    SEW = "ESW"
    NESW = "NESW"


@dataclass(frozen=True)
class NetworkTypeRules:
    """Generic constraints that vary by network semantic, not by graph shape."""

    network_type: NetworkType | str
    allowed_topologies: frozenset[NetworkTopology | str]
    supports_direction: bool = False
    allows_cross: bool = True
    allows_merge: bool = True
    allows_split: bool = True
    width_mode: str = "fixed"

    def __post_init__(self) -> None:
        object.__setattr__(self, "network_type", NetworkType(self.network_type))
        normalized = frozenset(NetworkTopology(value) for value in self.allowed_topologies)
        object.__setattr__(self, "allowed_topologies", normalized)
        if self.width_mode not in {"fixed", "variable", "structured"}:
            raise ValueError("width_mode must be fixed, variable, or structured")

    @classmethod
    def for_type(cls, network_type: NetworkType | str) -> "NetworkTypeRules":
        kind = NetworkType(network_type)
        topologies = frozenset(item for item in NetworkTopology if item is not NetworkTopology.EMPTY)
        if kind is NetworkType.RIVER:
            return cls(
                network_type=kind,
                allowed_topologies=topologies.difference({NetworkTopology.NESW}),
                supports_direction=True,
                allows_cross=False,
                allows_merge=True,
                allows_split=False,
                width_mode="variable",
            )
        return cls(
            network_type=kind,
            allowed_topologies=topologies,
            supports_direction=False,
            allows_cross=True,
            allows_merge=True,
            allows_split=True,
            width_mode="fixed",
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "network_type": self.network_type.value,
            "allowed_topologies": sorted(topology.value for topology in self.allowed_topologies),
            "supports_direction": self.supports_direction,
            "allows_cross": self.allows_cross,
            "allows_merge": self.allows_merge,
            "allows_split": self.allows_split,
            "width_mode": self.width_mode,
        }


_DIRECTION_BITS = {"N": 1, "E": 2, "S": 4, "W": 8}
_DIRECTION_OFFSETS = {
    "N": (0, -1),
    "E": (1, 0),
    "S": (0, 1),
    "W": (-1, 0),
}
_MASK_TO_TOPOLOGY = {
    0: NetworkTopology.EMPTY,
    1: NetworkTopology.N,
    2: NetworkTopology.E,
    3: NetworkTopology.NE,
    4: NetworkTopology.S,
    5: NetworkTopology.NS,
    6: NetworkTopology.SE,
    7: NetworkTopology.NES,
    8: NetworkTopology.W,
    9: NetworkTopology.NW,
    10: NetworkTopology.EW,
    11: NetworkTopology.NEW,
    12: NetworkTopology.SW,
    13: NetworkTopology.NSW,
    14: NetworkTopology.ESW,
    15: NetworkTopology.NESW,
}
_TOPOLOGY_ALIASES = {"SEW": "ESW"}


def connector_mask_for_sides(*sides: str) -> int:
    """Return the N/E/S/W four-bit connector mask."""
    mask = 0
    for side in sides:
        normalized = str(side).upper()
        if normalized not in _DIRECTION_BITS:
            raise ValueError(f"unsupported connector side: {side}")
        mask |= _DIRECTION_BITS[normalized]
    return mask


def topology_for_connector_mask(mask: int) -> NetworkTopology:
    if not isinstance(mask, int) or mask < 0 or mask > 15:
        raise ValueError("connector mask must be an integer between 0 and 15")
    return _MASK_TO_TOPOLOGY[mask]


def connector_mask_for_topology(topology: str | NetworkTopology) -> int:
    normalized = topology.value if isinstance(topology, NetworkTopology) else str(topology).upper()
    normalized = _TOPOLOGY_ALIASES.get(normalized, normalized)
    if normalized not in {item.value for item in NetworkTopology}:
        raise ValueError(f"unsupported network topology: {topology}")
    return connector_mask_for_sides(*normalized) if normalized != "EMPTY" else 0


def topology_sides(topology: str | NetworkTopology) -> tuple[str, ...]:
    mask = connector_mask_for_topology(topology)
    return tuple(side for side, bit in _DIRECTION_BITS.items() if mask & bit)


@dataclass(frozen=True, order=True)
class GridPoint:
    x: int
    y: int

    def as_list(self) -> list[int]:
        return [self.x, self.y]


@dataclass(frozen=True)
class NetworkEdge:
    start: GridPoint
    end: GridPoint
    directed: bool = False

    def __post_init__(self) -> None:
        if self.start == self.end:
            raise ValueError("network edge cannot connect a node to itself")
        dx = abs(self.start.x - self.end.x)
        dy = abs(self.start.y - self.end.y)
        if dx and dy:
            raise ValueError("diagonal network edges are not supported")
        if dx + dy != 1:
            raise ValueError("network edge endpoints must be adjacent grid cells")
        if not self.directed and self.end < self.start:
            start, end = self.end, self.start
            object.__setattr__(self, "start", start)
            object.__setattr__(self, "end", end)

    def as_dict(self) -> dict[str, list[int]]:
        return {"start": self.start.as_list(), "end": self.end.as_list(), "directed": self.directed}

    @property
    def source(self) -> GridPoint:
        return self.start

    @property
    def target(self) -> GridPoint:
        return self.end


@dataclass(frozen=True)
class NetworkSpec:
    network_type: NetworkType | str = NetworkType.ROAD
    material: str = "dirt_road"
    width_ratio: float = 0.22
    center_offset: float = 0.0
    edge_softness: float = 0.08
    variants_per_topology: int = 3

    def __post_init__(self) -> None:
        try:
            object.__setattr__(self, "network_type", NetworkType(self.network_type))
        except ValueError as exc:
            raise ValueError(f"unsupported network_type: {self.network_type}") from exc
        if not self.material:
            raise ValueError("network material must be non-empty")
        if not 0.05 <= self.width_ratio <= 0.9:
            raise ValueError("width_ratio must be between 0.05 and 0.9")
        if not -0.3 <= self.center_offset <= 0.3:
            raise ValueError("center_offset must be between -0.3 and 0.3")
        if not 0.0 <= self.edge_softness <= 1.0:
            raise ValueError("edge_softness must be between 0 and 1")
        if self.variants_per_topology < 1:
            raise ValueError("variants_per_topology must be positive")

    @property
    def center(self) -> float:
        return 0.5 + self.center_offset

    def as_dict(self) -> dict[str, object]:
        return {
            "network_type": self.network_type.value,
            "material": self.material,
            "width_ratio": self.width_ratio,
            "center_offset": self.center_offset,
            "edge_softness": self.edge_softness,
            "variants_per_topology": self.variants_per_topology,
        }


@dataclass(frozen=True)
class GraphValidationReport:
    warnings: tuple[str, ...] = ()
    isolated_nodes: tuple[GridPoint, ...] = ()
    disconnected_components: int = 0

    @property
    def is_valid(self) -> bool:
        return True

    def as_dict(self) -> dict[str, object]:
        return {
            "is_valid": self.is_valid,
            "warnings": list(self.warnings),
            "isolated_nodes": [point.as_list() for point in self.isolated_nodes],
            "disconnected_components": self.disconnected_components,
        }


@dataclass(frozen=True)
class NetworkGraph:
    width: int
    height: int
    nodes: frozenset[GridPoint] = field(default_factory=frozenset)
    edges: tuple[NetworkEdge, ...] = ()

    def __post_init__(self) -> None:
        if self.width < 1 or self.height < 1:
            raise ValueError("network graph grid dimensions must be positive")
        normalized_nodes = frozenset(_coerce_point(node) for node in self.nodes)
        for node in normalized_nodes:
            if not self.contains(node):
                raise ValueError(f"network node {node.as_list()} is outside the graph grid")
        normalized_edges = tuple(
            sorted(
                {NetworkEdge(_coerce_point(edge.start), _coerce_point(edge.end), directed=edge.directed) for edge in self.edges},
                key=lambda edge: (edge.start, edge.end, edge.directed),
            )
        )
        edge_nodes = {endpoint for edge in normalized_edges for endpoint in (edge.start, edge.end)}
        missing = edge_nodes.difference(normalized_nodes)
        if missing:
            normalized_nodes = frozenset(set(normalized_nodes).union(missing))
            for node in missing:
                if not self.contains(node):
                    raise ValueError(f"network edge node {node.as_list()} is outside the graph grid")
        object.__setattr__(self, "nodes", normalized_nodes)
        object.__setattr__(self, "edges", normalized_edges)

    @classmethod
    def from_paths(
        cls,
        width: int,
        height: int,
        paths: Sequence[Sequence[Sequence[int] | GridPoint]],
        extra_nodes: Sequence[Sequence[int] | GridPoint] = (),
        directed: bool = False,
    ) -> "NetworkGraph":
        nodes = {_coerce_point(point) for point in extra_nodes}
        edges: list[NetworkEdge] = []
        for path in paths:
            values = [_coerce_point(point) for point in path]
            nodes.update(values)
            edges.extend(NetworkEdge(first, second, directed=directed) for first, second in zip(values, values[1:]))
        return cls(width=width, height=height, nodes=frozenset(nodes), edges=tuple(edges))

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any]) -> "NetworkGraph":
        width = int(mapping.get("width", 10))
        height = int(mapping.get("height", 10))
        nodes = [_coerce_point(value) for value in mapping.get("nodes", ())]
        edges: list[NetworkEdge] = []
        for raw_edge in mapping.get("edges", ()):
            if isinstance(raw_edge, dict):
                first = raw_edge.get("start", raw_edge.get("source"))
                second = raw_edge.get("end", raw_edge.get("target"))
                directed = bool(raw_edge.get("directed", "source" in raw_edge or "target" in raw_edge))
            else:
                first, second = raw_edge
                directed = False
            edges.append(NetworkEdge(_coerce_point(first), _coerce_point(second), directed=directed))
        paths = mapping.get("paths", ())
        graph = cls.from_paths(width, height, paths, extra_nodes=nodes)
        directed_graph = cls.from_paths(width, height, mapping.get("directed_paths", ()), directed=True)
        return cls(width=graph.width, height=graph.height, nodes=graph.nodes.union(directed_graph.nodes), edges=tuple((*graph.edges, *directed_graph.edges, *edges)))

    def contains(self, point: GridPoint) -> bool:
        return 0 <= point.x < self.width and 0 <= point.y < self.height

    def neighbors(self, point: GridPoint) -> frozenset[GridPoint]:
        values: set[GridPoint] = set()
        for edge in self.edges:
            if edge.start == point:
                values.add(edge.end)
            elif edge.end == point:
                values.add(edge.start)
        return frozenset(values)

    def outgoing_neighbors(self, point: GridPoint) -> frozenset[GridPoint]:
        values: set[GridPoint] = set()
        for edge in self.edges:
            if edge.directed:
                if edge.start == point:
                    values.add(edge.end)
            elif edge.start == point:
                values.add(edge.end)
            elif edge.end == point:
                values.add(edge.start)
        return frozenset(values)

    def incoming_neighbors(self, point: GridPoint) -> frozenset[GridPoint]:
        values: set[GridPoint] = set()
        for edge in self.edges:
            if edge.directed:
                if edge.end == point:
                    values.add(edge.start)
            elif edge.start == point:
                values.add(edge.end)
            elif edge.end == point:
                values.add(edge.start)
        return frozenset(values)

    @property
    def has_directed_edges(self) -> bool:
        return any(edge.directed for edge in self.edges)

    def validate(self) -> GraphValidationReport:
        degrees = {node: len(self.neighbors(node)) for node in self.nodes}
        isolated = tuple(sorted(node for node, degree in degrees.items() if degree == 0))
        components = _component_count(self.nodes, self.neighbors)
        warnings: list[str] = []
        if isolated:
            warnings.append(f"isolated network nodes: {len(isolated)}")
        if components > 1:
            warnings.append(f"disconnected network components: {components}")
        return GraphValidationReport(tuple(warnings), isolated, components)

    def as_dict(self) -> dict[str, object]:
        return {
            "width": self.width,
            "height": self.height,
            "nodes": [point.as_list() for point in sorted(self.nodes)],
            "edges": [edge.as_dict() for edge in self.edges],
        }


@dataclass(frozen=True)
class ResolvedNetworkCell:
    point: GridPoint
    connector_mask: int
    topology: NetworkTopology
    neighbors: frozenset[GridPoint]

    @property
    def is_network(self) -> bool:
        return self.connector_mask != 0

    def as_dict(self) -> dict[str, object]:
        return {
            "x": self.point.x,
            "y": self.point.y,
            "connector_mask": self.connector_mask,
            "topology": self.topology.value,
            "neighbors": [point.as_list() for point in sorted(self.neighbors)],
        }


class NetworkTopologyResolver:
    """Resolve graph adjacency into a renderer-independent connector topology."""

    def resolve(self, graph: NetworkGraph, point: GridPoint) -> ResolvedNetworkCell:
        if not graph.contains(point):
            raise ValueError(f"point {point.as_list()} is outside the graph grid")
        neighbors = graph.neighbors(point)
        sides: list[str] = []
        for side, (dx, dy) in _DIRECTION_OFFSETS.items():
            if GridPoint(point.x + dx, point.y + dy) in neighbors:
                sides.append(side)
        mask = connector_mask_for_sides(*sides)
        return ResolvedNetworkCell(point, mask, topology_for_connector_mask(mask), neighbors)

    def resolve_grid(self, graph: NetworkGraph) -> dict[GridPoint, ResolvedNetworkCell]:
        return {
            GridPoint(x, y): self.resolve(graph, GridPoint(x, y))
            for y in range(graph.height)
            for x in range(graph.width)
        }


def _coerce_point(value: Sequence[int] | GridPoint) -> GridPoint:
    if isinstance(value, GridPoint):
        return value
    if len(value) != 2:
        raise ValueError("grid point must contain exactly [x, y]")
    return GridPoint(int(value[0]), int(value[1]))


def _component_count(nodes: Iterable[GridPoint], neighbors) -> int:
    remaining = set(nodes)
    count = 0
    while remaining:
        count += 1
        pending = [remaining.pop()]
        while pending:
            current = pending.pop()
            for neighbor in neighbors(current):
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    pending.append(neighbor)
    return count
