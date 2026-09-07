"""River-specific rules, flow resolution, geometry, bank and water rendering."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from pixel_tile_compiler.config import CompilerConfig
from pixel_tile_compiler.io.exporter import save_json, save_png
from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler

from .contracts import EdgeSemanticProfile, SemanticEdgeContract, SemanticTile, SemanticTileSpec
from .graph import (
    GridPoint,
    NetworkGraph,
    NetworkTopology,
    NetworkTopologyResolver,
    NetworkType,
    NetworkTypeRules,
    ResolvedNetworkCell,
    topology_sides,
)


class RiverGraphValidationError(ValueError):
    """Raised when a logical graph cannot describe a valid river system."""


@dataclass(frozen=True)
class RiverFlowCell:
    point: GridPoint
    topology: NetworkTopology
    connector_mask: int
    incoming: tuple[str, ...]
    outgoing: tuple[str, ...]
    neighbors: frozenset[GridPoint]

    @property
    def is_merge(self) -> bool:
        return len(self.incoming) >= 2 and len(self.outgoing) == 1

    @property
    def is_split(self) -> bool:
        return len(self.incoming) == 1 and len(self.outgoing) >= 2

    @property
    def is_network(self) -> bool:
        return self.connector_mask != 0

    def as_dict(self) -> dict[str, object]:
        return {
            "x": self.point.x,
            "y": self.point.y,
            "topology": self.topology.value,
            "connector_mask": self.connector_mask,
            "incoming": list(self.incoming),
            "outgoing": list(self.outgoing),
            "neighbors": [point.as_list() for point in sorted(self.neighbors)],
        }


class RiverFlowResolver:
    """Reuse generic adjacency topology and add directed flow sides."""

    def __init__(self) -> None:
        self._topology_resolver = NetworkTopologyResolver()

    def resolve(self, graph: NetworkGraph, point: GridPoint) -> RiverFlowCell:
        resolved = self._topology_resolver.resolve(graph, point)
        incoming = tuple(sorted(_side_from(point, neighbor) for neighbor in graph.incoming_neighbors(point)))
        outgoing = tuple(sorted(_side_from(point, neighbor) for neighbor in graph.outgoing_neighbors(point)))
        return RiverFlowCell(point, resolved.topology, resolved.connector_mask, incoming, outgoing, resolved.neighbors)

    def resolve_grid(self, graph: NetworkGraph) -> dict[GridPoint, RiverFlowCell]:
        return {
            GridPoint(x, y): self.resolve(graph, GridPoint(x, y))
            for y in range(graph.height)
            for x in range(graph.width)
        }


@dataclass(frozen=True)
class RiverGraphValidationReport:
    warnings: tuple[str, ...] = ()
    flow_cells: dict[GridPoint, RiverFlowCell] = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        return True

    def as_dict(self) -> dict[str, object]:
        return {
            "is_valid": self.is_valid,
            "warnings": list(self.warnings),
            "flow_cells": [cell.as_dict() for cell in self.flow_cells.values() if cell.is_network],
        }


def validate_river_graph(graph: NetworkGraph, allow_split: bool = False) -> RiverGraphValidationReport:
    rules = NetworkTypeRules.for_type(NetworkType.RIVER)
    flow_cells = RiverFlowResolver().resolve_grid(graph)
    errors: list[str] = []
    warnings = list(graph.validate().warnings)
    if _has_directed_cycle(graph):
        errors.append("directed cycle is not allowed for river flow")
    for cell in flow_cells.values():
        if not cell.is_network:
            continue
        if cell.topology not in rules.allowed_topologies or (cell.topology is NetworkTopology.NESW and not rules.allows_cross):
            errors.append(f"river cross topology is not allowed at {cell.point.as_list()}")
        if len(cell.incoming) >= 2 and len(cell.outgoing) == 0:
            errors.append(f"impossible river merge without downstream at {cell.point.as_list()}")
        if len(cell.incoming) >= 2 and not rules.allows_merge:
            errors.append(f"river merge is not allowed at {cell.point.as_list()}")
        if len(cell.outgoing) >= 2:
            if allow_split or rules.allows_split:
                warnings.append(f"river split at {cell.point.as_list()}")
            else:
                errors.append(f"river split is not allowed at {cell.point.as_list()}")
        if graph.has_directed_edges and not cell.incoming and not cell.outgoing:
            warnings.append(f"river node has no directed flow at {cell.point.as_list()}")
    if errors:
        raise RiverGraphValidationError("; ".join(errors))
    if not graph.has_directed_edges and any(cell.is_network for cell in flow_cells.values()):
        warnings.append("river graph has no directed edges; flow is treated as bidirectional")
    return RiverGraphValidationReport(tuple(dict.fromkeys(warnings)), flow_cells)


@dataclass(frozen=True)
class RiverRenderSpec:
    output_root: Path = field(default_factory=lambda: Path("e2e/river_network_study/families/river"))
    source_size: int = 256
    variants: int = 3
    palette_budget: int = 24
    pixelize: bool = True
    debug_enabled: bool = True
    seed: int = 42
    base_width_ratio: float = 0.26
    width_variation: float = 0.04
    bank_width_ratio: float = 0.04
    center: float = 0.5
    edge_softness: float = 0.08

    def __post_init__(self) -> None:
        object.__setattr__(self, "output_root", Path(self.output_root))
        if self.source_size < 64:
            raise ValueError("source_size must be at least 64")
        if self.variants < 1:
            raise ValueError("variants must be positive")
        if not 4 <= self.palette_budget <= 32:
            raise ValueError("palette_budget must be between 4 and 32")
        if not 0.05 <= self.base_width_ratio <= 0.9:
            raise ValueError("base_width_ratio must be between 0.05 and 0.9")
        if not 0.0 <= self.width_variation <= 0.2:
            raise ValueError("width_variation must be between 0 and 0.2")
        if not 0.0 < self.bank_width_ratio <= 0.2:
            raise ValueError("bank_width_ratio must be between 0 and 0.2")
        if not 0.2 <= self.center <= 0.8:
            raise ValueError("center must be between 0.2 and 0.8")

    def as_dict(self) -> dict[str, object]:
        return {
            "source_size": self.source_size,
            "variants": self.variants,
            "palette_budget": self.palette_budget,
            "pixelize": self.pixelize,
            "seed": self.seed,
            "base_width_ratio": self.base_width_ratio,
            "width_variation": self.width_variation,
            "bank_width_ratio": self.bank_width_ratio,
            "center": self.center,
            "edge_softness": self.edge_softness,
        }


@dataclass(frozen=True)
class RiverGeometry:
    centerline_mask: np.ndarray
    body_mask: np.ndarray
    bank_mask: np.ndarray
    paths: tuple[tuple[tuple[float, float], ...], ...]


def build_river_geometry(
    size: tuple[int, int],
    topology: NetworkTopology | str,
    incoming: Iterable[str] = (),
    outgoing: Iterable[str] = (),
    spec: RiverRenderSpec | None = None,
    seed: int = 42,
) -> RiverGeometry:
    render_spec = spec or RiverRenderSpec(source_size=size[0], seed=seed)
    width, height = size
    if width < 8 or height < 8:
        raise ValueError("river geometry size must be at least 8x8")
    topology_value = topology.value if isinstance(topology, NetworkTopology) else str(topology).upper()
    if topology_value == NetworkTopology.EMPTY.value:
        empty = np.zeros((height, width), dtype=bool)
        return RiverGeometry(empty, empty, empty, ())
    sides = tuple(topology_sides(topology_value))
    incoming_values = tuple(sorted(set(str(side).upper() for side in incoming)))
    outgoing_values = tuple(sorted(set(str(side).upper() for side in outgoing)))
    if not incoming_values and not outgoing_values:
        incoming_values = sides
        outgoing_values = sides
    center = (render_spec.center * (width - 1), render_spec.center * (height - 1))
    paths: list[tuple[tuple[float, float], ...]] = []
    edge_extension = width * render_spec.base_width_ratio * 0.5 + 1.0
    for side in sides:
        endpoint = _edge_point(side, width, height, render_spec.center, edge_extension)
        role = "out" if side in outgoing_values else "in" if side in incoming_values else "bidirectional"
        paths.append(tuple(_river_arm(endpoint, center, side, sides, role, width, height)))
    body = Image.new("L", (width, height), 0)
    centerline = Image.new("L", (width, height), 0)
    body_draw = ImageDraw.Draw(body)
    center_draw = ImageDraw.Draw(centerline)
    rng = np.random.default_rng(seed)
    for path in paths:
        _draw_variable_path(body_draw, path, width * render_spec.base_width_ratio, width * render_spec.width_variation, rng)
        center_draw.line(path, fill=255, width=max(1, round(width * 0.018)), joint="curve")
    body_array = np.asarray(body, dtype=np.uint8) > 0
    center_array = np.asarray(centerline, dtype=np.uint8) > 0
    bank_radius = max(1, round(width * render_spec.bank_width_ratio))
    filter_size = bank_radius * 2 + 1
    dilated = body.filter(ImageFilter.MaxFilter(filter_size))
    bank_array = (np.asarray(dilated, dtype=np.uint8) > 0) & ~body_array
    return RiverGeometry(center_array, body_array, bank_array, tuple(paths))


class RiverNetworkRenderer:
    """Render water + bank over a land material, then delegate pixelization."""

    def __init__(self, spec: RiverRenderSpec) -> None:
        self.spec = spec

    def render_tile(
        self,
        base_source: Image.Image,
        water_source: Image.Image,
        bank_source: Image.Image,
        base_material: str,
        topology: NetworkTopology,
        incoming: tuple[str, ...],
        outgoing: tuple[str, ...],
        variant: int = 0,
        base_contract: SemanticEdgeContract | None = None,
    ) -> SemanticTile:
        size = (self.spec.source_size, self.spec.source_size)
        geometry = build_river_geometry(size, topology, incoming, outgoing, self.spec, self.spec.seed + variant)
        base = _fit(base_source, size)
        water = _fit(water_source, size)
        bank = _fit(bank_source, size)
        composite = Image.composite(bank, base, Image.fromarray(geometry.bank_mask.astype(np.uint8) * 255, mode="L"))
        composite = Image.composite(water, composite, Image.fromarray(geometry.body_mask.astype(np.uint8) * 255, mode="L"))
        tile_id = f"river_{base_material}_{topology.value.lower()}_v{variant:02d}"
        contract = _river_contract(base_material, topology, incoming, outgoing, self.spec.base_width_ratio, base_contract)
        semantic_spec = SemanticTileSpec(
            tile_id=tile_id,
            family="river",
            topology=topology.value,
            variant=variant,
            semantic_contract=contract,
            metadata={
                "river_width": self.spec.base_width_ratio,
                "width_variation": self.spec.width_variation,
                "bank_width_ratio": self.spec.bank_width_ratio,
                "incoming": list(incoming),
                "outgoing": list(outgoing),
                "mask_type": "centerline_variable_width_with_bank",
            },
        )
        pixel_image = self._pixelize(composite, self.spec.output_root / "pixel_artifacts" / tile_id, tile_id) if self.spec.pixelize else None
        tile = SemanticTile(semantic_spec, composite, pixel_image)
        root = self.spec.output_root
        save_png(composite, root / "source_tiles" / f"{tile_id}.png")
        save_json(semantic_spec.as_dict(), root / "source_tiles" / f"{tile_id}.json")
        save_png(Image.fromarray(geometry.body_mask.astype(np.uint8) * 255, mode="L"), root / "masks" / f"{tile_id}_body.png")
        save_png(Image.fromarray(geometry.bank_mask.astype(np.uint8) * 255, mode="L"), root / "masks" / f"{tile_id}_bank.png")
        if pixel_image is not None:
            save_png(pixel_image, root / "pixel_tiles" / f"{tile_id}.png")
        return tile

    def _pixelize(self, image: Image.Image, output_root: Path, source_name: str) -> Image.Image:
        result = PixelTileCompiler().compile_image(
            image,
            CompilerConfig(
                output_root=output_root,
                palette_budget=self.spec.palette_budget,
                tile_mode="directional",
                seed=self.spec.seed,
                debug_enabled=self.spec.debug_enabled,
            ),
            source_name=source_name,
        )
        return Image.open(result.final_path).convert("RGBA").copy()


def _river_contract(
    base_material: str,
    topology: NetworkTopology,
    incoming: Iterable[str],
    outgoing: Iterable[str],
    width: float,
    base_contract: SemanticEdgeContract | None = None,
) -> SemanticEdgeContract:
    incoming_values = set(incoming)
    outgoing_values = set(outgoing)
    base = EdgeSemanticProfile(role="surface", material=base_material)
    profiles: dict[str, EdgeSemanticProfile] = {}
    for side in ("N", "E", "S", "W"):
        side_name = {"N": "north", "E": "east", "S": "south", "W": "west"}[side]
        if side not in topology_sides(topology):
            profiles[side_name] = base_contract.for_side(side_name) if base_contract else base
            continue
        flow = "out" if side in outgoing_values else "in" if side in incoming_values else "bidirectional"
        profiles[side_name] = EdgeSemanticProfile(
            role="network_connector",
            material="water",
            feature_type="river",
            feature_center=0.5,
            feature_width=width,
            orientation=topology.value,
            connects_to=(side_name,),
            flow=flow,
        )
    return SemanticEdgeContract(profiles["north"], profiles["east"], profiles["south"], profiles["west"], orientation=topology.value)


def _fit(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    return image.convert("RGBA").resize(size, Image.Resampling.BICUBIC)


def _edge_point(side: str, width: int, height: int, center: float, extension: float = 0.0) -> tuple[float, float]:
    cx, cy = center * (width - 1), center * (height - 1)
    return {
        "N": (cx, -extension),
        "E": (width - 1.0 + extension, cy),
        "S": (cx, height - 1.0 + extension),
        "W": (-extension, cy),
    }[side]


def _river_arm(
    endpoint: tuple[float, float],
    center: tuple[float, float],
    side: str,
    sides: tuple[str, ...],
    role: str,
    width: int,
    height: int,
) -> list[tuple[float, float]]:
    if len(sides) == 2 and set(sides) in ({"N", "S"}, {"E", "W"}):
        return [endpoint, center]
    bend = min(width, height) * 0.13
    cx, cy = center
    if side == "N":
        control = (cx + bend, cy * 0.48)
    elif side == "E":
        control = (cx + (width - cx) * 0.48, cy + bend)
    elif side == "S":
        control = (cx - bend, cy + (height - cy) * 0.48)
    else:
        control = (cx - cx * 0.48, cy - bend)
    if role == "out" and len(sides) >= 3:
        control = ((endpoint[0] + cx) * 0.5, (endpoint[1] + cy) * 0.5)
    return _quadratic(endpoint, control, center, steps=max(8, min(width, height) // 8))


def _quadratic(start: tuple[float, float], control: tuple[float, float], end: tuple[float, float], steps: int) -> list[tuple[float, float]]:
    values: list[tuple[float, float]] = []
    for index in range(steps + 1):
        t = index / steps
        inv = 1.0 - t
        values.append((inv * inv * start[0] + 2 * inv * t * control[0] + t * t * end[0], inv * inv * start[1] + 2 * inv * t * control[1] + t * t * end[1]))
    return values


def _draw_variable_path(draw: ImageDraw.ImageDraw, path: tuple[tuple[float, float], ...], base_width: float, variation: float, rng: np.random.Generator) -> None:
    if len(path) < 2:
        return
    for index, (first, second) in enumerate(zip(path, path[1:])):
        t = (index + 0.5) / max(1, len(path) - 1)
        jitter = float(rng.uniform(0.85, 1.15))
        width = max(1, round(base_width * (1.0 + variation / max(0.05, base_width) * np.sin(np.pi * t) * jitter)))
        draw.line((first, second), fill=255, width=width, joint="curve")
    radius = max(1, round(base_width * 0.56))
    center = path[-1]
    draw.ellipse((center[0] - radius, center[1] - radius, center[0] + radius, center[1] + radius), fill=255)


def _side_from(point: GridPoint, neighbor: GridPoint) -> str:
    delta = neighbor.x - point.x, neighbor.y - point.y
    mapping = {(0, -1): "N", (1, 0): "E", (0, 1): "S", (-1, 0): "W"}
    return mapping[delta]


def _has_directed_cycle(graph: NetworkGraph) -> bool:
    adjacency = {node: graph.outgoing_neighbors(node) for node in graph.nodes}
    visiting: set[GridPoint] = set()
    visited: set[GridPoint] = set()

    def visit(node: GridPoint) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(neighbor) for neighbor in adjacency[node]):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in graph.nodes)
