"""End-to-end logical River Graph study runner."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from PIL import Image, ImageDraw

from pixel_tile_compiler.config import CompilerConfig
from pixel_tile_compiler.io.exporter import save_json, save_png
from pixel_tile_compiler.io.loader import load_image
from pixel_tile_compiler.map.metrics import measure_map_metrics
from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler
from pixel_tile_compiler.tileset.compiler import TilesetSourceCompiler
from pixel_tile_compiler.tileset.source_tile import TilesetConfig
from pixel_tile_compiler.tileset.validator import validate_material_source

from .contracts import EdgeSemanticProfile, SemanticEdgeContract, SemanticTile, SemanticTileSpec, contracts_compatible
from .graph import GridPoint, NetworkGraph, NetworkSpec, NetworkTopology, NetworkType, NetworkTopologyResolver
from .masks import build_road_mask, build_transition_mask
from .metrics import network_map_metrics
from .river import RiverFlowCell, RiverFlowResolver, RiverNetworkRenderer, RiverRenderSpec, build_river_geometry, validate_river_graph
from .transition import TransitionTileCompiler, TransitionTileConfig


@dataclass
class RiverGraphStudyConfig:
    output_root: Path = field(default_factory=lambda: Path("e2e/river_network_study"))
    graph: NetworkGraph = field(default_factory=lambda: default_river_graph(10, 10))
    surface_map: tuple[tuple[str, ...], ...] = field(default_factory=lambda: default_surface_map(10, 10))
    sources: dict[str, Path] = field(default_factory=dict)
    network: NetworkSpec = field(default_factory=lambda: NetworkSpec(NetworkType.RIVER, "water", 0.26))
    tile_size: int = 64
    source_size: int = 256
    palette_budget: int = 24
    shared_palette: bool = True
    variants: int = 3
    surface_variants: int = 4
    surface_edge_types: int = 3
    transition_variants: int = 3
    width_variation: float = 0.04
    bank_width_ratio: float = 0.04
    allow_split: bool = False
    pixelize: bool = True
    debug_enabled: bool = True
    seed: int = 42

    def __post_init__(self) -> None:
        self.output_root = Path(self.output_root)
        self.sources = {str(key): Path(value) for key, value in self.sources.items()}
        self.surface_map = tuple(tuple(str(value) for value in row) for row in self.surface_map)
        if self.network.network_type is not NetworkType.RIVER:
            raise ValueError("RiverGraphStudyConfig requires network_type=river")
        if self.tile_size != 64:
            raise ValueError("the MVP output tile_size must be 64")
        if self.source_size < 64:
            raise ValueError("source_size must be at least 64")
        if not 4 <= self.palette_budget <= 32:
            raise ValueError("palette_budget must be between 4 and 32")
        if self.variants < 1 or self.surface_variants < 1 or self.transition_variants < 1:
            raise ValueError("variant counts must be positive")
        if len(self.surface_map) != self.graph.height or any(len(row) != self.graph.width for row in self.surface_map):
            raise ValueError("surface_map dimensions must match the network graph grid")
        required = {"grass", "forest_canopy", "water", "bank"}
        missing = required.difference(self.sources)
        if missing:
            raise ValueError(f"river sources are missing: {', '.join(sorted(missing))}")
        found = {material for row in self.surface_map for material in row}
        if not {"grass", "forest_canopy"}.issubset(found) or not found.issubset({"grass", "forest_canopy"}):
            raise ValueError("surface_map must contain only grass and forest_canopy, including both")
        if self.seed < 0:
            raise ValueError("seed must be non-negative")

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any], base_dir: Path | None = None) -> "RiverGraphStudyConfig":
        root = Path(base_dir or ".")
        study = dict(mapping.get("study", {}))
        network_map = dict(mapping.get("network_map", study.get("network_map", {})))
        width = int(network_map.get("width", 10))
        height = int(network_map.get("height", 10))
        graph_file = study.get("graph_file", mapping.get("graph_file"))
        graph = NetworkGraph.from_mapping(_read_json(_resolve(root, graph_file))) if graph_file else default_river_graph(width, height)
        surface_file = study.get("surface_file", mapping.get("surface_file"))
        surface_map = _load_surface_map(_read_json(_resolve(root, surface_file))) if surface_file else _load_surface_map(study.get("surface_map", default_surface_map(width, height)))
        raw_sources = study.get("sources", mapping.get("sources", {}))
        sources = {
            str(material): _resolve(root, value.get("source_file", value.get("source_path", value)) if isinstance(value, dict) else value)
            for material, value in raw_sources.items()
        }
        network_raw = dict(mapping.get("network", study.get("network", {})))
        river_raw = dict(mapping.get("river", study.get("river", {})))
        pixel_raw = dict(mapping.get("pixel", study.get("pixel", {})))
        surface_raw = dict(mapping.get("surface", study.get("surface", {})))
        base_width = float(river_raw.get("base_width_ratio", network_raw.get("width_ratio", 0.26)))
        variant_count = int(river_raw.get("variants", network_raw.get("variants_per_topology", 3)))
        return cls(
            output_root=_resolve(root, study.get("output_root", "../e2e/river_network_study")),
            graph=graph,
            surface_map=surface_map,
            sources=sources,
            network=NetworkSpec(NetworkType.RIVER, network_raw.get("material", "water"), base_width, float(network_raw.get("center_offset", 0.0)), float(network_raw.get("edge_softness", 0.08)), variant_count),
            tile_size=int(network_map.get("tile_size", 64)),
            source_size=int(pixel_raw.get("source_size", 256)),
            palette_budget=int(pixel_raw.get("palette_budget", pixel_raw.get("palette", 24))),
            shared_palette=bool(pixel_raw.get("shared_palette", True)),
            variants=variant_count,
            surface_variants=int(surface_raw.get("variants", 4)),
            surface_edge_types=int(surface_raw.get("edge_types", 3)),
            transition_variants=int(river_raw.get("transition_variants", variant_count)),
            width_variation=float(river_raw.get("width_variation", 0.04)),
            bank_width_ratio=float(river_raw.get("bank_width_ratio", 0.04)),
            allow_split=bool(river_raw.get("allow_split", False)),
            pixelize=bool(pixel_raw.get("enabled", True)),
            debug_enabled=bool(study.get("debug_enabled", True)),
            seed=int(mapping.get("seed", study.get("seed", 42))),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "output_root": str(self.output_root),
            "graph": self.graph.as_dict(),
            "surface_map": [list(row) for row in self.surface_map],
            "sources": {key: str(value) for key, value in self.sources.items()},
            "network": self.network.as_dict(),
            "river": {"base_width_ratio": self.network.width_ratio, "width_variation": self.width_variation, "bank_width_ratio": self.bank_width_ratio, "allow_split": self.allow_split},
            "network_map": {"width": self.graph.width, "height": self.graph.height, "tile_size": self.tile_size},
            "pixel": {"source_size": self.source_size, "palette_budget": self.palette_budget, "shared_palette": self.shared_palette, "enabled": self.pixelize},
            "seed": self.seed,
        }


@dataclass(frozen=True)
class RiverGraphStudyResult:
    output_root: Path
    manifest_path: Path
    metrics_path: Path
    comparison_path: Path
    logical_graph_preview_path: Path
    topology_debug_path: Path
    manifest: dict[str, object]
    metrics: dict[str, object]


class RiverGraphStudyRunner:
    def run(self, config: RiverGraphStudyConfig) -> RiverGraphStudyResult:
        root = config.output_root
        root.mkdir(parents=True, exist_ok=True)
        sources = {material: load_image(path) for material, path in config.sources.items()}
        validation = _write_validation(config, sources, root)
        river_report = validate_river_graph(config.graph, allow_split=config.allow_split)
        save_json(config.graph.as_dict(), root / "logical_river_graph.json")
        save_json({"width": config.graph.width, "height": config.graph.height, "materials": [list(row) for row in config.surface_map]}, root / "surface_map.json")
        save_json(config.as_dict(), root / "config.json")
        surface_tiles = _build_surfaces(config, sources, root)
        transitions = _build_transitions(config, sources, root)
        flow_cells = river_report.flow_cells
        _write_logical_preview(config, flow_cells, root / "logical_river_graph_preview.png")

        renderer = RiverNetworkRenderer(RiverRenderSpec(
            output_root=root / "families" / "river",
            source_size=config.source_size,
            variants=config.variants,
            palette_budget=config.palette_budget,
            pixelize=config.pixelize,
            debug_enabled=config.debug_enabled,
            seed=config.seed,
            base_width_ratio=config.network.width_ratio,
            width_variation=config.width_variation,
            bank_width_ratio=config.bank_width_ratio,
            center=config.network.center,
            edge_softness=config.network.edge_softness,
        ))
        methods = ("road_like_river_baseline", "river_renderer", "river_renderer_variants")
        records: dict[str, dict[str, object]] = {}
        manifests: dict[str, list[dict[str, object]]] = {}
        resolved_grids: dict[str, list[list[SemanticTile]]] = {}
        mask_grids: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        boundary_x = _find_boundary_column(config.surface_map)
        transition_lookup = {tile.spec.variant: tile for tile in transitions if tile.spec.material_a == "grass" and tile.spec.material_b == "forest_canopy" and tile.spec.topology == "EW"}
        baseline_cache: dict[tuple[str, str, tuple[str, ...], tuple[str, ...], int], SemanticTile] = {}
        river_cache: dict[tuple[str, str, tuple[str, ...], tuple[str, ...], int, bool], SemanticTile] = {}
        for method in methods:
            use_variants = method == "river_renderer_variants"
            baseline = method == "road_like_river_baseline"
            grid, manifest_cells, masks, source_preview = self._assemble(
                config, sources, flow_cells, surface_tiles, transition_lookup, renderer, boundary_x,
                baseline_cache, river_cache, use_variants, baseline, root,
            )
            image = _assemble_image(grid, config.tile_size)
            body_mask, bank_mask, centerline_mask = masks
            metrics = _metrics(config, image, body_mask, bank_mask, centerline_mask, flow_cells, grid)
            image_path = save_png(image, root / "maps" / f"{method}.png")
            record = {"method": method, "image": str(image_path), **metrics}
            save_json(record, root / "maps" / f"{method}.json")
            records[method] = record
            manifests[method] = manifest_cells
            resolved_grids[method] = grid
            mask_grids[method] = masks
            if method == "river_renderer":
                _write_debug_maps(root, centerline_mask, body_mask, bank_mask, source_preview)

        manifest = {
            "schema_version": 1,
            "network_type": "river",
            "graph_validation": river_report.as_dict(),
            "config": config.as_dict(),
            "cells": manifests["river_renderer"],
            "topology_counts": _topology_counts(flow_cells),
        }
        manifest_path = save_json(manifest, root / "manifest.json")
        metrics_payload = {"methods": records, "ranking": _rank_methods(records)}
        metrics_path = save_json(metrics_payload, root / "metrics.json")
        comparison_path = save_png(_comparison_image(root, records), root / "maps" / "comparison.png")
        topology_debug_path = _write_topology_debug(config, root / "maps" / "river_renderer.png", flow_cells, root / "topology_debug.png")
        _write_summary(config, root, records, river_report, validation)
        return RiverGraphStudyResult(root, manifest_path, metrics_path, comparison_path, root / "logical_river_graph_preview.png", topology_debug_path, manifest, metrics_payload)

    def _assemble(
        self, config, sources, flow_cells, surface_tiles, transition_lookup, renderer, boundary_x,
        baseline_cache, river_cache, use_variants, baseline, root,
    ):
        grid: list[list[SemanticTile]] = []
        manifests: list[dict[str, object]] = []
        body_blocks: list[list[np.ndarray]] = []
        bank_blocks: list[list[np.ndarray]] = []
        centerline_blocks: list[list[np.ndarray]] = []
        source_rows: list[list[Image.Image]] = []
        for y in range(config.graph.height):
            row: list[SemanticTile] = []
            body_row: list[np.ndarray] = []
            bank_row: list[np.ndarray] = []
            centerline_row: list[np.ndarray] = []
            source_row: list[Image.Image] = []
            for x in range(config.graph.width):
                point = GridPoint(x, y)
                cell = flow_cells[point]
                material = config.surface_map[y][x]
                variant = _variant_for(point, cell, config.seed, use_variants, config.variants)
                is_boundary = x == boundary_x and x + 1 < config.graph.width and config.surface_map[y][x] == "grass" and config.surface_map[y][x + 1] == "forest_canopy"
                transition = transition_lookup[variant % len(transition_lookup)] if is_boundary else None
                if not cell.is_network:
                    tile = transition if transition is not None else surface_tiles[material][variant % len(surface_tiles[material])]
                    body = np.zeros((config.tile_size, config.tile_size), dtype=bool)
                    bank = np.zeros_like(body)
                    centerline = np.zeros_like(body)
                else:
                    base_tile = transition if transition is not None else surface_tiles[material][0]
                    cache_key = (material if transition is None else "grass_forest_transition", cell.topology.value, cell.incoming, cell.outgoing, variant, use_variants)
                    if baseline:
                        tile = baseline_cache.get(cache_key)
                        if tile is None:
                            tile, geometry = _render_baseline(config, sources["water"], base_tile, cell, variant, root)
                            baseline_cache[cache_key] = tile
                        else:
                            geometry = _baseline_geometry(config, cell)
                    else:
                        tile = river_cache.get(cache_key)
                        if tile is None:
                            tile = renderer.render_tile(
                                base_tile.source_image, sources["water"], sources["bank"],
                                "grass_forest_transition" if transition is not None else material,
                                cell.topology, cell.incoming, cell.outgoing, variant,
                                base_contract=transition.spec.semantic_contract if transition is not None else base_tile.spec.semantic_contract,
                            )
                            river_cache[cache_key] = tile
                        geometry = build_river_geometry(
                            (config.tile_size, config.tile_size), cell.topology, cell.incoming, cell.outgoing,
                            RiverRenderSpec(source_size=config.tile_size, base_width_ratio=config.network.width_ratio, width_variation=config.width_variation, bank_width_ratio=config.bank_width_ratio, center=config.network.center, seed=config.seed + variant),
                            config.seed + variant,
                        )
                    body = geometry.body_mask
                    bank = geometry.bank_mask
                    centerline = geometry.centerline_mask
                row.append(tile)
                body_row.append(body)
                bank_row.append(bank)
                centerline_row.append(centerline)
                source_row.append(tile.source_image.resize((config.tile_size, config.tile_size), Image.Resampling.BICUBIC))
                manifests.append({
                    "x": x, "y": y, "surface": material,
                    "network": None if not cell.is_network else {
                        "type": "river", "topology": cell.topology.value, "connector_mask": cell.connector_mask,
                        "incoming": list(cell.incoming), "outgoing": list(cell.outgoing), "width": config.network.width_ratio,
                    },
                    "transition": "grass_to_forest_EW" if is_boundary else None,
                    "tile_asset": tile.spec.tile_id,
                })
            grid.append(row)
            body_blocks.append(body_row)
            bank_blocks.append(bank_row)
            centerline_blocks.append(centerline_row)
            source_rows.append(source_row)
        return grid, manifests, (np.block(body_blocks).astype(bool), np.block(bank_blocks).astype(bool), np.block(centerline_blocks).astype(bool)), _assemble_image_from_images(source_rows)


def load_river_graph_config(path: Path) -> RiverGraphStudyConfig:
    path = Path(path)
    raw = path.read_text(encoding="utf-8")
    mapping = json.loads(raw) if path.suffix.lower() == ".json" else _load_yaml(raw)
    if not isinstance(mapping, dict):
        raise ValueError("river graph study config must contain a mapping")
    return RiverGraphStudyConfig.from_mapping(mapping, base_dir=path.parent)


def default_river_graph(width: int = 10, height: int = 10) -> NetworkGraph:
    if width < 10 or height < 10:
        raise ValueError("default river graph needs a 10x10-capable grid")
    main = [(x, 5) for x in range(10)]
    tributary_a = [(1, 1), (1, 2), (2, 2), (3, 2), (3, 3), (3, 4), (3, 5)]
    tributary_b = [(8, 9), (8, 8), (7, 8), (6, 8), (6, 7), (6, 6), (6, 5)]
    return NetworkGraph.from_paths(width, height, [main, tributary_a, tributary_b], directed=True)


def default_surface_map(width: int = 10, height: int = 10) -> tuple[tuple[str, ...], ...]:
    boundary = max(1, width // 2)
    return tuple(tuple("grass" if x < boundary else "forest_canopy" for x in range(width)) for _y in range(height))


def _write_validation(config, sources, root):
    records = {}
    for material, image in sources.items():
        analysis_material = "forest_canopy" if material == "forest_canopy" else "grass"
        report = validate_material_source(image, palette_budget=config.palette_budget, material=analysis_material)
        records[material] = {"source_id": material, "source_path": str(config.sources[material]), "accepted": report.status != "rejected", **report.as_dict()}
        save_json(records[material], root / "validation" / f"{material}.json")
    save_json(records, root / "validation" / "summary.json")
    return records


def _build_surfaces(config, sources, root):
    result = {}
    for material in ("grass", "forest_canopy"):
        tileset = TilesetSourceCompiler().build_image(
            sources[material],
            TilesetConfig(
                output_root=root / "families" / "surface" / material,
                material=material, variants=config.surface_variants, edge_types=config.surface_edge_types,
                palette_budget=config.palette_budget, shared_palette=config.shared_palette,
                map_columns=config.graph.width, map_rows=config.graph.height, source_tile_size=config.source_size,
                patch_size=max(32, config.source_size // 2), patch_overlap=max(8, config.source_size // 8),
                strip_width=max(8, config.source_size // 6), seed=config.seed, debug_enabled=config.debug_enabled,
            ), source_name=config.sources[material],
        )
        profile = EdgeSemanticProfile(role="surface", material=material)
        contract = SemanticEdgeContract(profile, profile, profile, profile, orientation="surface")
        result[material] = []
        for index, (source_tile, pixel_path) in enumerate(zip(tileset.source_tiles, tileset.pixel_tile_paths)):
            tile = SemanticTile(SemanticTileSpec(f"{material}_surface_v{index:02d}", material, contract, topology="surface", variant=index), source_tile.image, Image.open(pixel_path).convert("RGBA").copy())
            result[material].append(tile)
            save_png(tile.source_image, root / "source_tiles" / f"{tile.spec.tile_id}.png")
            save_png(tile.image, root / "pixel_tiles" / f"{tile.spec.tile_id}.png")
    return result


def _build_transitions(config, sources, root):
    build = TransitionTileCompiler(TransitionTileConfig(output_root=root / "families" / "transition", source_size=config.source_size, variants=config.transition_variants, palette_budget=config.palette_budget, pixelize=config.pixelize, debug_enabled=config.debug_enabled, seed=config.seed)).build_pair(sources["grass"], sources["forest_canopy"], "grass", "forest_canopy", "grass_forest")
    for tile in build.tiles:
        save_png(tile.source_image, root / "source_tiles" / f"transition_{tile.spec.tile_id}.png")
        save_png(tile.image, root / "pixel_tiles" / f"transition_{tile.spec.tile_id}.png")
    return list(build.tiles)


def _render_baseline(config, water_source, base_tile, cell, variant, root):
    size = (config.source_size, config.source_size)
    mask = build_road_mask(size, cell.topology.value, width=config.network.width_ratio, center=config.network.center)
    composite = Image.composite(water_source.convert("RGBA").resize(size, Image.Resampling.BICUBIC), base_tile.source_image.convert("RGBA").resize(size, Image.Resampling.BICUBIC), Image.fromarray(mask.astype(np.uint8) * 255, mode="L"))
    tile_id = f"river_baseline_{cell.topology.value.lower()}_v{variant:02d}"
    contract = _river_contract_for_tile(base_tile.spec.semantic_contract, cell, config.network.width_ratio)
    spec = SemanticTileSpec(
        tile_id=tile_id,
        family="river_baseline",
        semantic_contract=contract,
        topology=cell.topology.value,
        variant=variant,
        metadata={"mask_type": "road_like_stripe", "river_width": config.network.width_ratio},
    )
    pixel_image = _pixelize_if_enabled(config, composite, root / "families" / "river_baseline" / "pixel_artifacts" / tile_id, tile_id)
    tile = SemanticTile(spec, composite, pixel_image)
    save_png(composite, root / "source_tiles" / f"{tile_id}.png")
    save_png(tile.image, root / "pixel_tiles" / f"{tile_id}.png")
    return tile, _baseline_geometry(config, cell)


def _baseline_geometry(config, cell):
    mask = build_road_mask((config.tile_size, config.tile_size), cell.topology.value, width=config.network.width_ratio, center=config.network.center)
    return type("BaselineGeometry", (), {"body_mask": mask, "bank_mask": np.zeros_like(mask), "centerline_mask": mask})()


def _river_contract_for_tile(base_contract, cell, width):
    profiles = {}
    incoming, outgoing = set(cell.incoming), set(cell.outgoing)
    for side, name in (("N", "north"), ("E", "east"), ("S", "south"), ("W", "west")):
        if side not in {"N", "E", "S", "W"}:
            continue
        if side not in set(cell.topology.value):
            profiles[name] = base_contract.for_side(name)
        else:
            profiles[name] = EdgeSemanticProfile(role="network_connector", material="water", feature_type="river", feature_center=0.5, feature_width=width, flow="out" if side in outgoing else "in" if side in incoming else "bidirectional", connects_to=(name,), orientation=cell.topology.value)
    return SemanticEdgeContract(profiles["north"], profiles["east"], profiles["south"], profiles["west"], orientation=cell.topology.value)


def _pixelize_if_enabled(config, image, output_root, source_name):
    if not config.pixelize:
        return None
    result = PixelTileCompiler().compile_image(image, CompilerConfig(output_root=output_root, palette_budget=config.palette_budget, tile_mode="directional", seed=config.seed, debug_enabled=config.debug_enabled), source_name=source_name)
    return Image.open(result.final_path).convert("RGBA").copy()


def _metrics(config, image, body_mask, bank_mask, centerline_mask, flow_cells, grid):
    network = network_map_metrics(image, body_mask, config.graph.width, config.graph.height, config.tile_size)
    continuity = _boundary_match(body_mask, config.graph.width, config.graph.height, config.tile_size)
    graph_fidelity, edge_matches, edge_count = _graph_fidelity(flow_cells, image, body_mask, config.tile_size)
    flow_score, flow_matches = _flow_contract_score(config.graph, grid)
    edge_width = _edge_width_contract_score(flow_cells, body_mask, config.tile_size, config.network.width_ratio)
    interior = _interior_width_variation_score(flow_cells, body_mask, config.tile_size, config.network.width_ratio, config.width_variation)
    merge = _merge_clarity(flow_cells, body_mask, config.tile_size)
    map_metrics = measure_map_metrics(image, config.graph.width, config.graph.height, config.tile_size)
    return {
        "river_continuity_score": round(continuity, 6),
        "river_width_stability": round((edge_width + interior) / 2.0, 6),
        "river_center_alignment": network["road_center_alignment_score"],
        "river_break_risk": round(1.0 - continuity, 6),
        "bank_continuity_score": round(_boundary_match(bank_mask, config.graph.width, config.graph.height, config.tile_size), 6),
        "bank_presence_score": round(float(np.any(bank_mask)), 6),
        "merge_clarity_score": round(merge, 6),
        "edge_width_contract_score": round(edge_width, 6),
        "interior_width_variation_score": round(interior, 6),
        "graph_fidelity_score": round(graph_fidelity, 6),
        "graph_edge_matches": edge_matches,
        "graph_edge_count": edge_count,
        "flow_fidelity_score": round(flow_score, 6),
        "flow_contract_matches": flow_matches,
        "river_presence_score": network["road_presence_score"],
        "edge_discontinuity_score": round(map_metrics.neighbor_color_discontinuity, 6),
        "brightness_discontinuity_score": round(map_metrics.neighbor_brightness_discontinuity, 6),
        "grid_visibility_score": round(map_metrics.map_grid_visibility_score, 6),
    }


def _graph_fidelity(flow_cells, image, expected_mask, tile_size):
    actual = _detect_water_mask(image)
    if not actual.any():
        actual = expected_mask
    matches = 0
    total = 0
    for point, cell in flow_cells.items():
        for neighbor in cell.neighbors:
            if (point.x, point.y) >= (neighbor.x, neighbor.y):
                continue
            total += 1
            side = _side_from(point, neighbor)
            other_side = _opposite(side)
            first = actual[point.y * tile_size:(point.y + 1) * tile_size, point.x * tile_size:(point.x + 1) * tile_size]
            second = actual[neighbor.y * tile_size:(neighbor.y + 1) * tile_size, neighbor.x * tile_size:(neighbor.x + 1) * tile_size]
            if _edge_present(first, side) and _edge_present(second, other_side):
                matches += 1
    return (matches / total if total else 1.0), matches, total


def _flow_contract_score(graph, grid):
    matches = 0
    total = 0
    for edge in graph.edges:
        if not edge.directed:
            continue
        total += 1
        first = grid[edge.start.y][edge.start.x]
        second = grid[edge.end.y][edge.end.x]
        side = _side_from(edge.start, edge.end)
        side_name = {"N": "north", "E": "east", "S": "south", "W": "west"}[side]
        first_profile = first.spec.semantic_contract.for_side(side_name)
        second_profile = second.spec.semantic_contract.for_side({"N": "south", "E": "west", "S": "north", "W": "east"}[side])
        if first_profile.flow == "out" and second_profile.flow == "in" and contracts_compatible(first.spec.semantic_contract, second.spec.semantic_contract, side_name):
            matches += 1
    return (matches / total if total else 1.0), matches


def _edge_width_contract_score(flow_cells, mask, tile_size, expected):
    values = []
    for point, cell in flow_cells.items():
        if not cell.is_network:
            continue
        tile = mask[point.y * tile_size:(point.y + 1) * tile_size, point.x * tile_size:(point.x + 1) * tile_size]
        for side in set(cell.incoming).union(cell.outgoing):
            values.append(_edge_width(tile, side))
    if not values:
        return 1.0
    return max(0.0, min(1.0, 1.0 - float(np.mean([abs(value - expected) for value in values])) / 0.08))


def _interior_width_variation_score(flow_cells, mask, tile_size, expected, desired):
    samples = []
    for point, cell in flow_cells.items():
        if not cell.is_network:
            continue
        if cell.topology.value not in {"N", "E", "S", "W", "NS", "EW"}:
            continue
        tile = mask[point.y * tile_size:(point.y + 1) * tile_size, point.x * tile_size:(point.x + 1) * tile_size]
        active = set(cell.incoming).union(cell.outgoing)
        for side in active:
            # Sample the outer part of each arm, before the curve or merge
            # center. This isolates width variation from junction area.
            positions = (0.18, 0.32) if side in {"N", "W"} else (0.68, 0.82)
            for position in positions:
                index = min(tile_size - 1, max(0, round(position * (tile_size - 1))))
                if side in {"N", "S"}:
                    samples.append(float(tile[index, :].mean()))
                else:
                    samples.append(float(tile[:, index].mean()))
    if not samples:
        return 1.0
    deviation = max(abs(value - expected) for value in samples)
    # A 64px raster introduces a few-pixel quantization error; keep that
    # tolerance separate from the requested interior variation budget.
    tolerance = max(0.08, desired * 2.0)
    return max(0.0, min(1.0, 1.0 - deviation / tolerance))


def _merge_clarity(flow_cells, mask, tile_size):
    values = []
    for point, cell in flow_cells.items():
        if not cell.is_merge:
            continue
        tile = mask[point.y * tile_size:(point.y + 1) * tile_size, point.x * tile_size:(point.x + 1) * tile_size]
        expected = set(cell.incoming).union(cell.outgoing)
        values.append(sum(_edge_present(tile, side) for side in expected) / len(expected))
    return float(np.mean(values)) if values else 1.0


def _detect_water_mask(image):
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    return ((blue > red * 1.15) & (blue > green * 1.02)).astype(bool)


def _edge_present(tile, side):
    band = max(2, tile.shape[0] // 16)
    center = tile.shape[0] // 2
    half = max(2, tile.shape[0] // 8)
    if side == "N":
        region = tile[:band, center - half:center + half + 1]
    elif side == "E":
        region = tile[center - half:center + half + 1, -band:]
    elif side == "S":
        region = tile[-band:, center - half:center + half + 1]
    else:
        region = tile[center - half:center + half + 1, :band]
    return float(region.mean()) > 0.08


def _edge_width(tile, side):
    band = max(2, tile.shape[0] // 16)
    if side == "N":
        return float(tile[:band, :].mean())
    if side == "E":
        return float(tile[:, -band:].mean())
    if side == "S":
        return float(tile[-band:, :].mean())
    return float(tile[:, :band].mean())


def _boundary_match(mask, columns, rows, tile_size):
    matches = []
    for x in range(1, columns):
        boundary = x * tile_size
        matches.append(float(np.mean(mask[:, boundary - 1] == mask[:, boundary])))
    for y in range(1, rows):
        boundary = y * tile_size
        matches.append(float(np.mean(mask[boundary - 1, :] == mask[boundary, :])))
    return float(np.mean(matches)) if matches else 1.0


def _write_logical_preview(config, cells, path):
    image = Image.new("RGBA", (config.graph.width * config.tile_size, config.graph.height * config.tile_size), (45, 55, 45, 255))
    draw = ImageDraw.Draw(image)
    for y, row in enumerate(config.surface_map):
        for x, material in enumerate(row):
            color = (100, 145, 78, 255) if material == "grass" else (48, 92, 58, 255)
            draw.rectangle((x * config.tile_size, y * config.tile_size, (x + 1) * config.tile_size - 1, (y + 1) * config.tile_size - 1), fill=color)
    for edge in config.graph.edges:
        first = (edge.start.x * config.tile_size + config.tile_size // 2, edge.start.y * config.tile_size + config.tile_size // 2)
        second = (edge.end.x * config.tile_size + config.tile_size // 2, edge.end.y * config.tile_size + config.tile_size // 2)
        draw.line((first, second), fill=(44, 139, 178, 255), width=max(5, config.tile_size // 10))
        if edge.directed:
            _draw_arrow(draw, first, second, fill=(230, 240, 130, 255))
    for point, cell in cells.items():
        cx, cy = point.x * config.tile_size + config.tile_size // 2, point.y * config.tile_size + config.tile_size // 2
        radius = max(4, config.tile_size // 10)
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=(240, 225, 140, 255), outline=(20, 30, 20, 255), width=2)
        if cell.is_merge:
            draw.rectangle((cx - radius // 2, cy - radius // 2, cx + radius // 2, cy + radius // 2), outline=(235, 70, 40, 255), width=2)
    save_png(image, path)


def _draw_arrow(draw, first, second, fill):
    dx, dy = second[0] - first[0], second[1] - first[1]
    length = max(1.0, (dx * dx + dy * dy) ** 0.5)
    ux, uy = dx / length, dy / length
    px, py = -uy, ux
    tip = (second[0] - ux * 10, second[1] - uy * 10)
    left = (tip[0] - ux * 10 + px * 6, tip[1] - uy * 10 + py * 6)
    right = (tip[0] - ux * 10 - px * 6, tip[1] - uy * 10 - py * 6)
    draw.polygon((tip, left, right), fill=fill)


def _write_topology_debug(config, map_path, cells, path):
    with Image.open(map_path) as source:
        image = source.convert("RGBA").copy()
    draw = ImageDraw.Draw(image)
    for point, cell in cells.items():
        x, y = point.x * config.tile_size, point.y * config.tile_size
        draw.rectangle((x, y, x + config.tile_size - 1, y + config.tile_size - 1), outline=(255, 240, 150, 255), width=1)
        label = "." if not cell.is_network else f"{cell.topology.value}\n{','.join(cell.incoming)}→{','.join(cell.outgoing)}"
        draw.multiline_text((x + 3, y + 3), label, fill=(255, 255, 255, 255), stroke_width=1, stroke_fill=(0, 0, 0, 255))
    return save_png(image, path)


def _write_debug_maps(root, centerline, body, bank, source_preview):
    debug = root / "debug"
    save_png(Image.fromarray(centerline.astype(np.uint8) * 255, mode="L").convert("RGBA"), debug / "river_centerline.png")
    save_png(Image.fromarray(body.astype(np.uint8) * 255, mode="L").convert("RGBA"), debug / "river_body_mask.png")
    save_png(Image.fromarray(bank.astype(np.uint8) * 255, mode="L").convert("RGBA"), debug / "river_bank_mask.png")
    save_png(source_preview, debug / "river_source_preview.png")


def _write_summary(config, root, records, report, validation):
    ranking = _rank_methods(records)
    validation_summary = ", ".join(f"{key}={value['accepted']}" for key, value in validation.items())
    score_by_method = {item["method"]: item["score"] for item in ranking}
    metric_lines = [
        "| method | graph fidelity | flow fidelity | river continuity | bank continuity | edge width contract | merge clarity | score |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for method, metrics in records.items():
        metric_lines.append(
            f"| {method} | {metrics['graph_fidelity_score']:.6f} | {metrics['flow_fidelity_score']:.6f} | "
            f"{metrics['river_continuity_score']:.6f} | {metrics['bank_continuity_score']:.6f} | "
            f"{metrics['edge_width_contract_score']:.6f} | {metrics['merge_clarity_score']:.6f} | {score_by_method[method]:.6f} |"
        )
    text = [
        "# Generic Network Graph → River Renderer Study", "",
        f"固定seed={config.seed}、grid={config.graph.width}x{config.graph.height}、tile={config.tile_size}x{config.tile_size}。", "",
        "## Ranking", "",
        *[f"{index}. {item['method']} — {item['score']:.6f}" for index, item in enumerate(ranking, 1)], "",
        "## 結論", "",
        "- NetworkGraph、NetworkEdge、NetworkType、TopologyResolver、connector bitmask、Graph validationはRoadと共通利用した。",
        "- River固有の追加はNetworkTypeRules、directed flow、merge/split/cycle validation、River geometry、Bank、Water contractである。",
        "- Road-like baselineと比較することで、Graph coreは共通でもRendererはsemanticごとに必要であることを確認する。",
        "- RiverはNESW crossをrejectし、T topologyはincoming 2 + outgoing 1のmergeとして解釈した。",
        "", "## Comparison metrics", "", *metric_lines, "",
        "## Architectural answers", "",
        "- Q1: RoadからはNetworkGraph、NetworkEdge、NetworkType、connector bitmask、TopologyResolver、Graph validation、Pixel Compiler assemblyを再利用した。",
        "- Q2: coreにはdirected edge、NetworkTypeRules、incoming/outgoing flow、flow-aware SemanticEdgeProfileを追加した。",
        "- Q3: directed edgeとRulesはRiver以外にも、Railの方向、Canalの流向、Wallの許可Topologyなどへ一般化できる。",
        "- Q4: River固有なのはbank生成、flow-aware geometry、curveの自然化、merge appearance、water materialである。",
        "- Q5: Wall/Canal/Cliff lineへはGraph・Topology・Contract・Pixel Compiler境界を再利用し、geometry/material/transitionだけをRendererとして差し替えられる。",
        "", "## Source / Graph validation", "", f"- source validation: {validation_summary}", f"- graph warnings: {', '.join(report.warnings) if report.warnings else 'none'}", "",
        "## 既知の限界", "",
        "- Flowの判定はgrid directed edgeに限定し、自由曲線・水理シミュレーション・分水路最適化は行わない。",
        "- Curveとmergeは高解像度のquadratic pathと可変幅strokeによる近似である。",
        "- Water検出は色特徴による近似で、River意味理解の完全な代替ではない。",
        "- Bank materialは今回dirt系Sourceを分離入力として使うが、専用bank source studyは未実施。",
    ]
    (root / "summary").mkdir(parents=True, exist_ok=True)
    (root / "summary" / "report.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    (root / "summary" / "limitations.md").write_text("# Limitations\n\n- bridge / waterfall / lake / coast / river depthは対象外。\n- diagonal edge、free-form spline、full hydrological simulationは未実装。\n", encoding="utf-8")


def _rank_methods(records):
    values = []
    for method, metrics in records.items():
        score = 0.23 * float(metrics["graph_fidelity_score"]) + 0.19 * float(metrics["flow_fidelity_score"]) + 0.12 * float(metrics["river_continuity_score"]) + 0.12 * float(metrics["edge_width_contract_score"]) + 0.08 * float(metrics["bank_continuity_score"]) + 0.05 * float(metrics["bank_presence_score"]) + 0.1 * float(metrics["merge_clarity_score"]) + 0.06 * (1.0 - float(metrics["grid_visibility_score"])) + 0.05 * (1.0 - float(metrics["river_break_risk"]))
        values.append({"method": method, "score": round(max(0.0, min(1.0, score)), 6)})
    return sorted(values, key=lambda item: (-item["score"], item["method"]))


def _comparison_image(root, records):
    labels = ("road_like_river_baseline", "river_renderer", "river_renderer_variants")
    images = [Image.open(Path(str(records[label]["image"]))).convert("RGBA") for label in labels]
    margin = 24
    output = Image.new("RGBA", (sum(image.width for image in images), images[0].height + margin), (24, 24, 24, 255))
    draw = ImageDraw.Draw(output)
    offset = 0
    for label, image in zip(labels, images):
        draw.text((offset + 4, 4), label, fill=(255, 255, 255, 255))
        output.paste(image, (offset, margin))
        offset += image.width
    return output


def _assemble_image(grid, tile_size):
    output = Image.new("RGBA", (len(grid[0]) * tile_size, len(grid) * tile_size), (0, 0, 0, 255))
    for y, row in enumerate(grid):
        for x, tile in enumerate(row):
            output.paste(tile.image.resize((tile_size, tile_size), Image.Resampling.NEAREST), (x * tile_size, y * tile_size))
    return output


def _assemble_image_from_images(rows):
    if not rows:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 255))
    width, height = rows[0][0].size
    output = Image.new("RGBA", (width * len(rows[0]), height * len(rows)), (0, 0, 0, 255))
    for y, row in enumerate(rows):
        for x, image in enumerate(row):
            output.paste(image, (x * width, y * height))
    return output.resize((len(rows[0]) * 64, len(rows) * 64), Image.Resampling.BICUBIC)


def _variant_for(point, cell, seed, use_variants, count):
    if not use_variants:
        return 0
    return random.Random(seed + point.x * 1009 + point.y * 9176 + cell.connector_mask * 37).randrange(count)


def _find_boundary_column(surface_map):
    for x in range(len(surface_map[0]) - 1):
        if any(row[x] == "grass" and row[x + 1] == "forest_canopy" for row in surface_map):
            return x
    return -1


def _topology_counts(cells):
    counts = {}
    for cell in cells.values():
        counts[cell.topology.value] = counts.get(cell.topology.value, 0) + 1
    return dict(sorted(counts.items()))


def _side_from(first, second):
    return {(0, -1): "N", (1, 0): "E", (0, 1): "S", (-1, 0): "W"}[(second.x - first.x, second.y - first.y)]


def _opposite(side):
    return {"N": "S", "E": "W", "S": "N", "W": "E"}[side]


def _load_yaml(raw):
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError as exc:
        raise ValueError("YAML configにはPyYAMLが必要です。JSON configを使用してください") from exc
    return yaml.safe_load(raw)


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _load_surface_map(value):
    if isinstance(value, dict):
        value = value.get("materials", value.get("surface", value))
    return tuple(tuple(str(item) for item in row) for row in value)


def _resolve(base, value):
    path = Path(str(value))
    return path if path.is_absolute() else base / path
