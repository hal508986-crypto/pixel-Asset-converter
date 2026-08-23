"""Logical network graph to reusable 64x64 road MAP study pipeline."""

from __future__ import annotations

import json
import random
from collections import Counter
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

from .graph import (
    GridPoint,
    NetworkGraph,
    NetworkSpec,
    NetworkTopology,
    NetworkTopologyResolver,
    ResolvedNetworkCell,
    topology_sides,
)
from .masks import ROAD_TOPOLOGIES, build_road_mask
from .metrics import network_map_metrics
from .network import NetworkTileCompiler, NetworkTileConfig
from .transition import TransitionTileCompiler, TransitionTileConfig
from .contracts import EdgeSemanticProfile, SemanticEdgeContract, SemanticTile, SemanticTileSpec


@dataclass
class RoadGraphStudyConfig:
    output_root: Path = field(default_factory=lambda: Path("e2e/road_graph_study"))
    graph: NetworkGraph = field(default_factory=lambda: default_road_graph(10, 10))
    surface_map: tuple[tuple[str, ...], ...] = field(default_factory=lambda: default_surface_map(10, 10))
    sources: dict[str, Path] = field(default_factory=dict)
    network: NetworkSpec = field(default_factory=NetworkSpec)
    tile_size: int = 64
    source_size: int = 256
    palette_budget: int = 24
    shared_palette: bool = True
    surface_variants: int = 4
    surface_edge_types: int = 3
    transition_variants: int = 3
    pixelize: bool = True
    debug_enabled: bool = True
    seed: int = 42

    def __post_init__(self) -> None:
        self.output_root = Path(self.output_root)
        self.sources = {str(key): Path(value) for key, value in self.sources.items()}
        self.surface_map = tuple(tuple(str(value) for value in row) for row in self.surface_map)
        if self.tile_size != 64:
            raise ValueError("the MVP output tile_size must be 64")
        if self.source_size < 64:
            raise ValueError("source_size must be at least 64")
        if not 4 <= self.palette_budget <= 32:
            raise ValueError("palette_budget must be between 4 and 32")
        if self.surface_variants < 1 or self.surface_edge_types < 1 or self.transition_variants < 1:
            raise ValueError("surface, edge, and transition variant counts must be positive")
        if len(self.surface_map) != self.graph.height or any(len(row) != self.graph.width for row in self.surface_map):
            raise ValueError("surface_map dimensions must match the network graph grid")
        required = {"grass", "forest_canopy", "dirt_road"}
        missing = required.difference(self.sources)
        if missing:
            raise ValueError(f"road graph sources are missing: {', '.join(sorted(missing))}")
        allowed = {"grass", "forest_canopy"}
        found = {material for row in self.surface_map for material in row}
        if not found.issubset(allowed):
            raise ValueError("surface_map may contain only grass and forest_canopy")
        if not {"grass", "forest_canopy"}.issubset(found):
            raise ValueError("surface_map must contain both grass and forest_canopy")
        if self.seed < 0:
            raise ValueError("seed must be non-negative")

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any], base_dir: Path | None = None) -> "RoadGraphStudyConfig":
        root = Path(base_dir or ".")
        study = dict(mapping.get("study", {}))
        network_map = dict(mapping.get("network_map", study.get("network_map", {})))
        width = int(network_map.get("width", 10))
        height = int(network_map.get("height", 10))
        graph_file = study.get("graph_file", mapping.get("graph_file"))
        if graph_file:
            graph = NetworkGraph.from_mapping(_read_json(_resolve(root, graph_file)))
        else:
            graph = default_road_graph(width, height)
        surface_file = study.get("surface_file", mapping.get("surface_file"))
        if surface_file:
            surface_map = _load_surface_map(_read_json(_resolve(root, surface_file)))
        else:
            surface_map = _load_surface_map(study.get("surface_map", mapping.get("surface_map", default_surface_map(width, height))))
        raw_sources = study.get("sources", mapping.get("sources", {}))
        sources = {
            str(material): _resolve(root, value.get("source_file", value.get("source_path", value)) if isinstance(value, dict) else value)
            for material, value in raw_sources.items()
        }
        network_raw = dict(mapping.get("network", study.get("network", {})))
        pixel_raw = dict(mapping.get("pixel", study.get("pixel", {})))
        surface_raw = dict(mapping.get("surface", study.get("surface", {})))
        return cls(
            output_root=_resolve(root, study.get("output_root", "../e2e/road_graph_study")),
            graph=graph,
            surface_map=surface_map,
            sources=sources,
            network=NetworkSpec(
                network_type=network_raw.get("type", network_raw.get("network_type", "road")),
                material=network_raw.get("material", "dirt_road"),
                width_ratio=float(network_raw.get("width_ratio", 0.22)),
                center_offset=float(network_raw.get("center_offset", 0.0)),
                edge_softness=float(network_raw.get("edge_softness", 0.08)),
                variants_per_topology=int(network_raw.get("variants_per_topology", 3)),
            ),
            tile_size=int(network_map.get("tile_size", 64)),
            source_size=int(pixel_raw.get("source_size", network_raw.get("source_size", 256))),
            palette_budget=int(pixel_raw.get("palette_budget", pixel_raw.get("palette", 24))),
            shared_palette=bool(pixel_raw.get("shared_palette", True)),
            surface_variants=int(surface_raw.get("variants", 4)),
            surface_edge_types=int(surface_raw.get("edge_types", 3)),
            transition_variants=int(network_raw.get("variants_per_topology", 3)),
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
            "network_map": {"width": self.graph.width, "height": self.graph.height, "tile_size": self.tile_size},
            "pixel": {"palette_budget": self.palette_budget, "shared_palette": self.shared_palette, "enabled": self.pixelize},
            "surface": {"variants": self.surface_variants, "edge_types": self.surface_edge_types},
            "seed": self.seed,
        }


@dataclass(frozen=True)
class RoadGraphStudyResult:
    output_root: Path
    manifest_path: Path
    metrics_path: Path
    comparison_path: Path
    logical_graph_preview_path: Path
    topology_debug_path: Path
    manifest: dict[str, object]
    metrics: dict[str, object]


class RoadGraphStudyRunner:
    """Compile a logical graph into comparable manual and resolver maps."""

    def run(self, config: RoadGraphStudyConfig) -> RoadGraphStudyResult:
        root = config.output_root
        root.mkdir(parents=True, exist_ok=True)
        sources = {material: load_image(path) for material, path in config.sources.items()}
        validation = self._write_validation(config, sources, root)
        graph_report = config.graph.validate()
        save_json(config.graph.as_dict(), root / "logical_graph.json")
        save_json({"width": config.graph.width, "height": config.graph.height, "materials": [list(row) for row in config.surface_map]}, root / "surface_map.json")
        save_json(config.as_dict(), root / "config.json")

        surface_tiles = self._build_surfaces(config, sources, root)
        network_tiles = self._build_networks(config, sources, root)
        transition_tiles = self._build_transitions(config, sources, root)
        resolved = NetworkTopologyResolver().resolve_grid(config.graph)
        self._write_logical_graph_preview(config, resolved, root / "logical_graph_preview.png")

        methods = {
            "manual_baseline": (resolved, False),
            "graph_resolved": (resolved, False),
            "graph_resolved_variants": (resolved, True),
        }
        method_records: dict[str, dict[str, object]] = {}
        selected_grids: dict[str, list[list[SemanticTile]]] = {}
        selected_masks: dict[str, np.ndarray] = {}
        manifests: dict[str, list[dict[str, object]]] = {}
        for method, (cells, use_variants) in methods.items():
            grid, manifest_cells = self._assemble_method(
                config,
                sources,
                root,
                cells,
                surface_tiles,
                network_tiles,
                transition_tiles,
                use_variants=use_variants,
                manual=method == "manual_baseline",
            )
            image = _assemble_image(grid, config.tile_size)
            mask = _mask_grid(cells, config, config.tile_size)
            metrics = self._metrics(config, image, mask, cells)
            image_path = save_png(image, root / "maps" / f"{method}.png")
            record = {"method": method, "image": str(image_path), **metrics}
            save_json(record, root / "maps" / f"{method}.json")
            method_records[method] = record
            selected_grids[method] = grid
            selected_masks[method] = mask
            manifests[method] = manifest_cells

        graph_manifest = {
            "schema_version": 1,
            "network_type": config.network.network_type.value,
            "graph_validation": graph_report.as_dict(),
            "topology_counts": dict(sorted(Counter(cell.topology.value for cell in resolved.values()).items())),
            "config": config.as_dict(),
            "cells": manifests["graph_resolved"],
        }
        manifest_path = save_json(graph_manifest, root / "manifest.json")
        metrics_payload = {"methods": method_records, "ranking": _rank_methods(method_records)}
        metrics_path = save_json(metrics_payload, root / "metrics.json")
        comparison_path = save_png(_comparison_image(root, method_records), root / "maps" / "comparison.png")
        topology_debug_path = self._write_topology_debug(config, root / "maps" / "graph_resolved.png", resolved, root / "topology_debug.png")
        self._write_summary(config, root, method_records, validation, graph_report)
        return RoadGraphStudyResult(root, manifest_path, metrics_path, comparison_path, root / "logical_graph_preview.png", topology_debug_path, graph_manifest, metrics_payload)

    def _write_validation(self, config: RoadGraphStudyConfig, sources: dict[str, Image.Image], root: Path) -> dict[str, dict[str, object]]:
        records: dict[str, dict[str, object]] = {}
        for material, image in sources.items():
            analysis_material = "forest_canopy" if material == "forest_canopy" else "grass"
            report = validate_material_source(image, palette_budget=config.palette_budget, material=analysis_material)
            record = {"source_id": material, "source_path": str(config.sources[material]), "accepted": report.status != "rejected", **report.as_dict()}
            records[material] = record
            save_json(record, root / "validation" / f"{material}.json")
        save_json(records, root / "validation" / "summary.json")
        return records

    def _build_surfaces(self, config: RoadGraphStudyConfig, sources: dict[str, Image.Image], root: Path) -> dict[str, list[SemanticTile]]:
        result: dict[str, list[SemanticTile]] = {}
        for material in ("grass", "forest_canopy"):
            family_root = root / "families" / "surface" / material
            tileset = TilesetSourceCompiler().build_image(
                sources[material],
                TilesetConfig(
                    output_root=family_root,
                    material=material,
                    variants=config.surface_variants,
                    edge_types=config.surface_edge_types,
                    palette_budget=config.palette_budget,
                    shared_palette=config.shared_palette,
                    map_columns=config.graph.width,
                    map_rows=config.graph.height,
                    source_tile_size=config.source_size,
                    patch_size=max(32, config.source_size // 2),
                    patch_overlap=max(8, config.source_size // 8),
                    strip_width=max(8, config.source_size // 6),
                    seed=config.seed,
                    debug_enabled=config.debug_enabled,
                ),
                source_name=config.sources[material],
            )
            profile = EdgeSemanticProfile(role="surface", material=material)
            contract = SemanticEdgeContract(profile, profile, profile, profile, orientation="surface")
            result[material] = []
            for index, (source_tile, pixel_path) in enumerate(zip(tileset.source_tiles, tileset.pixel_tile_paths)):
                pixel_image = Image.open(pixel_path).convert("RGBA").copy()
                tile = SemanticTile(
                    SemanticTileSpec(f"{material}_surface_v{index:02d}", material, contract, topology="surface", variant=index),
                    source_tile.image,
                    pixel_image,
                )
                result[material].append(tile)
                save_png(tile.source_image, root / "source_tiles" / f"{tile.spec.tile_id}.png")
                save_png(tile.image, root / "pixel_tiles" / f"{tile.spec.tile_id}.png")
        return result

    def _build_networks(self, config: RoadGraphStudyConfig, sources: dict[str, Image.Image], root: Path) -> dict[str, dict[str, list[SemanticTile]]]:
        result: dict[str, dict[str, list[SemanticTile]]] = {}
        for base_material in ("grass", "forest_canopy"):
            family_id = f"dirt_road_{base_material}"
            build = NetworkTileCompiler(
                NetworkTileConfig(
                    output_root=root / "families" / "network" / base_material,
                    source_size=config.source_size,
                    variants=config.network.variants_per_topology,
                    palette_budget=config.palette_budget,
                    pixelize=config.pixelize,
                    debug_enabled=config.debug_enabled,
                    seed=config.seed,
                    topologies=ROAD_TOPOLOGIES,
                    width_ratio=config.network.width_ratio,
                    center=config.network.center,
                )
            ).build(sources[base_material], sources[config.network.material], base_material=base_material, family_id=family_id)
            grouped: dict[str, list[SemanticTile]] = {topology: [] for topology in ROAD_TOPOLOGIES}
            for tile in build.tiles:
                grouped[tile.spec.topology or "EMPTY"].append(tile)
                asset_id = f"{base_material}_{tile.spec.tile_id}"
                save_png(tile.source_image, root / "source_tiles" / f"{asset_id}.png")
                save_png(tile.image, root / "pixel_tiles" / f"{asset_id}.png")
            result[base_material] = grouped
        return result

    def _build_transitions(self, config: RoadGraphStudyConfig, sources: dict[str, Image.Image], root: Path) -> list[SemanticTile]:
        build = TransitionTileCompiler(
            TransitionTileConfig(
                output_root=root / "families" / "transition",
                source_size=config.source_size,
                variants=config.transition_variants,
                palette_budget=config.palette_budget,
                pixelize=config.pixelize,
                debug_enabled=config.debug_enabled,
                seed=config.seed,
            )
        ).build_pair(sources["grass"], sources["forest_canopy"], "grass", "forest_canopy", "grass_forest")
        tiles = list(build.tiles)
        for tile in tiles:
            save_png(tile.source_image, root / "source_tiles" / f"transition_{tile.spec.tile_id}.png")
            save_png(tile.image, root / "pixel_tiles" / f"transition_{tile.spec.tile_id}.png")
        return tiles

    def _assemble_method(
        self,
        config: RoadGraphStudyConfig,
        sources: dict[str, Image.Image],
        root: Path,
        cells: dict[GridPoint, ResolvedNetworkCell],
        surface_tiles: dict[str, list[SemanticTile]],
        network_tiles: dict[str, dict[str, list[SemanticTile]]],
        transition_tiles: list[SemanticTile],
        use_variants: bool,
        manual: bool,
    ) -> tuple[list[list[SemanticTile]], list[dict[str, object]]]:
        boundary_x = _find_boundary_column(config.surface_map)
        transition_lookup = {
            tile.spec.variant: tile
            for tile in transition_tiles
            if tile.spec.material_a == "grass" and tile.spec.material_b == "forest_canopy" and tile.spec.topology == "EW"
        }
        combined_cache: dict[tuple[str, int], SemanticTile] = {}
        grid: list[list[SemanticTile]] = []
        manifests: list[dict[str, object]] = []
        for y in range(config.graph.height):
            row: list[SemanticTile] = []
            for x in range(config.graph.width):
                point = GridPoint(x, y)
                cell = cells[point]
                material = config.surface_map[y][x]
                variant = _choose_variant(cell, point, config.seed, use_variants)
                is_boundary = x == boundary_x and x + 1 < config.graph.width and config.surface_map[y][x] == "grass" and config.surface_map[y][x + 1] == "forest_canopy"
                transition_name = "grass_to_forest_EW" if is_boundary else None
                if cell.is_network:
                    if is_boundary:
                        key = (cell.topology.value, variant)
                        if key not in combined_cache:
                            combined_cache[key] = self._compose_transition_network_tile(
                                config, sources[config.network.material], transition_lookup[variant % len(transition_lookup)],
                                network_tiles["grass"][cell.topology.value][variant % len(network_tiles["grass"][cell.topology.value])], root,
                            )
                        tile = combined_cache[key]
                    else:
                        options = network_tiles[material][cell.topology.value]
                        tile = options[variant % len(options)]
                elif is_boundary:
                    tile = transition_lookup[variant % len(transition_lookup)]
                else:
                    options = surface_tiles[material]
                    tile = options[variant % len(options)]
                row.append(tile)
                manifests.append({
                    "x": x,
                    "y": y,
                    "surface": material,
                    "network": None if not cell.is_network else {
                        "type": config.network.network_type.value,
                        "connector_mask": cell.connector_mask,
                        "topology": cell.topology.value,
                    },
                    "transition": transition_name,
                    "tile_asset": tile.spec.tile_id,
                })
            grid.append(row)
        return grid, manifests

    def _compose_transition_network_tile(
        self,
        config: RoadGraphStudyConfig,
        road_source: Image.Image,
        transition_tile: SemanticTile,
        network_tile: SemanticTile,
        root: Path,
    ) -> SemanticTile:
        source_size = config.source_size
        base = transition_tile.source_image.convert("RGBA").resize((source_size, source_size), Image.Resampling.BICUBIC)
        road = road_source.convert("RGBA").resize((source_size, source_size), Image.Resampling.BICUBIC)
        width = float(network_tile.spec.metadata.get("road_width", config.network.width_ratio))
        center = float(network_tile.spec.metadata.get("road_center", config.network.center))
        mask = build_road_mask((source_size, source_size), network_tile.spec.topology or "NS", width=width, center=center)
        source_image = Image.composite(road, base, Image.fromarray(mask.astype(np.uint8) * 255, mode="L"))
        tile_id = f"grass_forest_road_{network_tile.spec.tile_id}"
        contract = _transition_network_contract(network_tile.spec.semantic_contract, transition_tile.spec.semantic_contract)
        spec = SemanticTileSpec(
            tile_id=tile_id,
            family="road_transition",
            topology=network_tile.spec.topology,
            variant=network_tile.spec.variant,
            semantic_contract=contract,
            metadata={**network_tile.spec.metadata, "transition": "grass_to_forest_EW", "mask_type": "transition_then_network"},
        )
        pixel_image = self._pixelize(source_image, root / "pixel_artifacts" / tile_id, tile_id, config) if config.pixelize else None
        tile = SemanticTile(spec, source_image, pixel_image)
        save_png(source_image, root / "source_tiles" / f"{tile_id}.png")
        save_png(tile.image, root / "pixel_tiles" / f"{tile_id}.png")
        return tile

    @staticmethod
    def _pixelize(image: Image.Image, output_root: Path, source_name: str, config: RoadGraphStudyConfig) -> Image.Image:
        result = PixelTileCompiler().compile_image(
            image,
            CompilerConfig(
                output_root=output_root,
                palette_budget=config.palette_budget,
                tile_mode="repeatable",
                seed=config.seed,
                debug_enabled=config.debug_enabled,
            ),
            source_name=source_name,
        )
        return Image.open(result.final_path).convert("RGBA").copy()

    def _metrics(self, config: RoadGraphStudyConfig, image: Image.Image, mask: np.ndarray, cells: dict[GridPoint, ResolvedNetworkCell]) -> dict[str, object]:
        map_metrics = measure_map_metrics(image, config.graph.width, config.graph.height, tile_size=config.tile_size)
        network_metrics = network_map_metrics(image, mask, config.graph.width, config.graph.height, config.tile_size)
        fidelity, matched, total = _graph_fidelity(cells, _detect_road_mask(image), config.graph, config.tile_size)
        junction = _junction_clarity(cells, config, config.tile_size)
        width_stability = _road_width_stability(cells, mask, config.graph, config.tile_size)
        return {
            **network_metrics,
            "road_width_stability_score": round(width_stability, 6),
            "road_width_stability": round(width_stability, 6),
            "road_center_alignment": network_metrics["road_center_alignment_score"],
            "junction_clarity_score": round(junction, 6),
            "graph_fidelity_score": round(fidelity, 6),
            "graph_edge_matches": matched,
            "graph_edge_count": total,
            "edge_discontinuity_score": round(map_metrics.neighbor_color_discontinuity, 6),
            "brightness_discontinuity_score": round(map_metrics.neighbor_brightness_discontinuity, 6),
            "palette_discontinuity_score": round(map_metrics.neighbor_texture_discontinuity, 6),
            "grid_visibility_score": round(map_metrics.map_grid_visibility_score, 6),
        }

    @staticmethod
    def _write_logical_graph_preview(config: RoadGraphStudyConfig, cells: dict[GridPoint, ResolvedNetworkCell], path: Path) -> Path:
        image = Image.new("RGBA", (config.graph.width * config.tile_size, config.graph.height * config.tile_size), (44, 52, 44, 255))
        draw = ImageDraw.Draw(image)
        for y, row in enumerate(config.surface_map):
            for x, material in enumerate(row):
                color = (100, 145, 78, 255) if material == "grass" else (48, 92, 58, 255)
                draw.rectangle((x * config.tile_size, y * config.tile_size, (x + 1) * config.tile_size - 1, (y + 1) * config.tile_size - 1), fill=color)
        for edge in config.graph.edges:
            first = (edge.start.x * config.tile_size + config.tile_size // 2, edge.start.y * config.tile_size + config.tile_size // 2)
            second = (edge.end.x * config.tile_size + config.tile_size // 2, edge.end.y * config.tile_size + config.tile_size // 2)
            draw.line((first, second), fill=(152, 99, 49, 255), width=max(6, config.tile_size // 6))
        for point, cell in cells.items():
            cx = point.x * config.tile_size + config.tile_size // 2
            cy = point.y * config.tile_size + config.tile_size // 2
            radius = max(4, config.tile_size // 10)
            draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), fill=(242, 215, 120, 255), outline=(30, 30, 20, 255), width=2)
            if len(cell.neighbors) >= 3:
                draw.rectangle((cx - radius // 2, cy - radius // 2, cx + radius // 2, cy + radius // 2), outline=(220, 50, 40, 255), width=2)
        return save_png(image, path)

    @staticmethod
    def _write_topology_debug(config: RoadGraphStudyConfig, map_path: Path, cells: dict[GridPoint, ResolvedNetworkCell], path: Path) -> Path:
        with Image.open(map_path) as source:
            image = source.convert("RGBA").copy()
        draw = ImageDraw.Draw(image)
        for point, cell in cells.items():
            x = point.x * config.tile_size
            y = point.y * config.tile_size
            draw.rectangle((x, y, x + config.tile_size - 1, y + config.tile_size - 1), outline=(255, 240, 150, 255), width=1)
            label = cell.topology.value if cell.is_network else "."
            draw.text((x + 4, y + 4), label, fill=(255, 255, 255, 255), stroke_width=1, stroke_fill=(0, 0, 0, 255))
        return save_png(image, path)

    @staticmethod
    def _write_summary(config: RoadGraphStudyConfig, root: Path, methods: dict[str, dict[str, object]], validation: dict[str, dict[str, object]], graph_report) -> None:
        ranking = _rank_methods(methods)
        validation_summary = ", ".join(f"{key}={record['accepted']}" for key, record in validation.items())
        lines = [
            "# Logical Road Graph Study",
            "",
            f"固定seed={config.seed}、grid={config.graph.width}x{config.graph.height}、tile={config.tile_size}x{config.tile_size}。",
            "",
            "## Ranking",
            "",
        ]
        lines.extend(f"{index}. {item['method']} — {item['score']:.6f}" for index, item in enumerate(ranking, 1))
        lines.extend([
            "",
            "## 実装上の確認",
            "",
            "- Graphの隣接からN/E/S/W bitmaskを解決し、dead end、straight、curve、T、crossを同じresolverで扱った。",
            "- 道路geometryはNetwork mask、materialは既存のdirt source、最終raster化は既存Pixel Compilerに分離した。",
            "- grass/forest境界セルでは既存grass↔forest transitionを先に合成し、その上へroad maskを重ねた。",
            "- `graph_fidelity_score` は論理edgeの両端が最終tile edgeに存在するかを近似評価する。",
            "- graph/image fidelityは、road materialの色特徴による最終pixel maskと論理edgeを照合する。",
            "",
            "## Source / Graph validation",
            "",
            f"- source validation: {validation_summary}",
            f"- graph warnings: {', '.join(graph_report.warnings) if graph_report.warnings else 'none'}",
            "",
            "## 推奨条件",
            "",
            "- topologyは画像生成結果から推測せず、logical graphから決定する。",
            "- 全topologyでroad width / center / edge contractを共有する。",
            "- T / crossは各armのunionとjunction cleanupで作り、Pixel Compilerへ渡す。",
            "",
            "## 既知の限界",
            "",
            "- 現在のroad maskはgrid上の矩形stripeと円形junctionによる近似で、曲線道路や斜めedgeは対象外。",
            "- graph fidelityは最終画像の意味理解ではなく、edge近傍の二値mask検査である。",
            "- manual baselineは固定graphからmaterializedしたtopology列を使うため、人手入力の誤り比較ではなくresolverとの同一性比較である。",
        ])
        (root / "summary").mkdir(parents=True, exist_ok=True)
        (root / "summary" / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (root / "summary" / "limitations.md").write_text(
            "# Limitations\n\n" + "\n".join([
                "- diagonal grid edge、free-form spline、複数road widthは未対応。",
                "- River / Wall / CanalはNetworkTypeの拡張点のみで、専用material rendererは未実装。",
                "- Wang graph完全探索、graphcut seam最適化、gameplay semanticsは未実装。",
            ]) + "\n",
            encoding="utf-8",
        )


def load_road_graph_config(path: Path) -> RoadGraphStudyConfig:
    path = Path(path)
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        mapping = json.loads(raw)
    else:
        try:
            import yaml  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ValueError("YAML configにはPyYAMLが必要です。JSON configを使用してください") from exc
        mapping = yaml.safe_load(raw)
    if not isinstance(mapping, dict):
        raise ValueError("road graph study config must contain a mapping")
    return RoadGraphStudyConfig.from_mapping(mapping, base_dir=path.parent)


def default_road_graph(width: int = 10, height: int = 10) -> NetworkGraph:
    if width < 9 or height < 9:
        raise ValueError("default road graph needs a grid of at least 9x9")
    top = [(x, 2) for x in range(1, 9)] + [(8, y) for y in range(3, 9)] + [(x, 8) for x in range(7, 0, -1)] + [(1, y) for y in range(7, 1, -1)]
    horizontal = [(x, 5) for x in range(1, 9)]
    vertical = [(4, y) for y in range(2, 9)]
    endpoint = [(2, 2), (2, 1)]
    return NetworkGraph.from_paths(width, height, [top, horizontal, vertical, endpoint])


def default_surface_map(width: int = 10, height: int = 10) -> tuple[tuple[str, ...], ...]:
    boundary = max(1, width // 2)
    return tuple(tuple("grass" if x < boundary else "forest_canopy" for x in range(width)) for _y in range(height))


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_surface_map(value: Any) -> tuple[tuple[str, ...], ...]:
    if isinstance(value, dict):
        value = value.get("materials", value.get("surface", value))
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError("surface map must be a 2D sequence")
    return tuple(tuple(str(item) for item in row) for row in value)


def _resolve(base: Path, value: Any) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else base / path


def _assemble_image(grid: Sequence[Sequence[SemanticTile]], tile_size: int) -> Image.Image:
    image = Image.new("RGBA", (len(grid[0]) * tile_size, len(grid) * tile_size), (0, 0, 0, 255))
    for y, row in enumerate(grid):
        for x, tile in enumerate(row):
            tile_image = tile.image.resize((tile_size, tile_size), Image.Resampling.NEAREST)
            image.paste(tile_image, (x * tile_size, y * tile_size))
    return image


def _mask_grid(cells: dict[GridPoint, ResolvedNetworkCell], config: RoadGraphStudyConfig, tile_size: int) -> np.ndarray:
    return np.block([
        [build_road_mask((tile_size, tile_size), cells[GridPoint(x, y)].topology.value, width=config.network.width_ratio, center=config.network.center) if cells[GridPoint(x, y)].is_network else np.zeros((tile_size, tile_size), dtype=bool) for x in range(config.graph.width)]
        for y in range(config.graph.height)
    ]).astype(bool)


def _edge_presence(tile_mask: np.ndarray, side: str) -> bool:
    band = max(2, min(tile_mask.shape) // 16)
    center = tile_mask.shape[1] // 2
    half = max(2, int(tile_mask.shape[1] * 0.12))
    if side == "N":
        values = tile_mask[:band, center - half:center + half + 1]
    elif side == "E":
        values = tile_mask[center - half:center + half + 1, -band:]
    elif side == "S":
        values = tile_mask[-band:, center - half:center + half + 1]
    else:
        values = tile_mask[center - half:center + half + 1, :band]
    return float(values.mean()) > 0.08


def _graph_fidelity(cells: dict[GridPoint, ResolvedNetworkCell], mask: np.ndarray, graph: NetworkGraph, tile_size: int) -> tuple[float, int, int]:
    matched = 0
    for edge in graph.edges:
        dx = edge.end.x - edge.start.x
        dy = edge.end.y - edge.start.y
        if dx == 1:
            first_side, second_side = "E", "W"
        elif dx == -1:
            first_side, second_side = "W", "E"
        elif dy == 1:
            first_side, second_side = "S", "N"
        else:
            first_side, second_side = "N", "S"
        first_mask = mask[edge.start.y * tile_size:(edge.start.y + 1) * tile_size, edge.start.x * tile_size:(edge.start.x + 1) * tile_size]
        second_mask = mask[edge.end.y * tile_size:(edge.end.y + 1) * tile_size, edge.end.x * tile_size:(edge.end.x + 1) * tile_size]
        if _edge_presence(first_mask, first_side) and _edge_presence(second_mask, second_side):
            matched += 1
    total = len(graph.edges)
    return (matched / total if total else 1.0), matched, total


def _detect_road_mask(image: Image.Image) -> np.ndarray:
    """Approximate final-pixel road detection for graph/image fidelity checks."""
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    red, green, blue = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    return ((red > green * 1.08) & (red > blue * 1.25) & (green < 190)).astype(bool)


def _road_width_stability(cells: dict[GridPoint, ResolvedNetworkCell], mask: np.ndarray, graph: NetworkGraph, tile_size: int) -> float:
    widths: list[float] = []
    for point, cell in cells.items():
        if not cell.is_network:
            continue
        tile = mask[point.y * tile_size:(point.y + 1) * tile_size, point.x * tile_size:(point.x + 1) * tile_size]
        for side in topology_sides(cell.topology):
            if side == "N":
                widths.append(float(tile[0, :].mean()))
            elif side == "E":
                widths.append(float(tile[:, -1].mean()))
            elif side == "S":
                widths.append(float(tile[-1, :].mean()))
            else:
                widths.append(float(tile[:, 0].mean()))
    if not widths:
        return 1.0
    return max(0.0, min(1.0, 1.0 - float(np.std(widths)) / 0.08))


def _junction_clarity(cells: dict[GridPoint, ResolvedNetworkCell], config: RoadGraphStudyConfig, tile_size: int) -> float:
    values: list[float] = []
    for cell in cells.values():
        if len(cell.neighbors) < 3:
            continue
        mask = build_road_mask((tile_size, tile_size), cell.topology.value, width=config.network.width_ratio, center=config.network.center)
        expected = set(topology_sides(cell.topology))
        observed = {side for side in ("N", "E", "S", "W") if _edge_presence(mask, side)}
        values.append(len(expected.intersection(observed)) / max(1, len(expected)))
    return float(np.mean(values)) if values else 1.0


def _find_boundary_column(surface_map: Sequence[Sequence[str]]) -> int:
    for x in range(len(surface_map[0]) - 1):
        if any(row[x] != row[x + 1] and row[x] == "grass" and row[x + 1] == "forest_canopy" for row in surface_map):
            return x
    return -1


def _choose_variant(cell: ResolvedNetworkCell, point: GridPoint, seed: int, use_variants: bool) -> int:
    if not use_variants:
        return 0
    return random.Random(seed + point.x * 1009 + point.y * 9176 + cell.connector_mask * 37).randrange(3)


def _transition_network_contract(network: SemanticEdgeContract, transition: SemanticEdgeContract) -> SemanticEdgeContract:
    profiles = {}
    for side in ("north", "east", "south", "west"):
        network_profile = network.for_side(side)  # type: ignore[arg-type]
        profiles[side] = network_profile if network_profile.role == "network_connector" else transition.for_side(side)  # type: ignore[arg-type]
    return SemanticEdgeContract(profiles["north"], profiles["east"], profiles["south"], profiles["west"], orientation=network.orientation)


def _rank_methods(methods: dict[str, dict[str, object]]) -> list[dict[str, object]]:
    ranked: list[dict[str, object]] = []
    for method, metrics in methods.items():
        score = (
            0.35 * float(metrics["graph_fidelity_score"])
            + 0.15 * float(metrics["road_continuity_score"])
            + 0.12 * float(metrics["road_width_stability_score"])
            + 0.10 * float(metrics["road_center_alignment_score"])
            + 0.12 * float(metrics["junction_clarity_score"])
            + 0.08 * (1.0 - float(metrics["grid_visibility_score"]))
            + 0.08 * (1.0 - float(metrics["road_break_risk"]))
        )
        ranked.append({"method": method, "score": round(max(0.0, min(1.0, score)), 6)})
    return sorted(ranked, key=lambda item: (-float(item["score"]), str(item["method"])))


def _comparison_image(root: Path, methods: dict[str, dict[str, object]]) -> Image.Image:
    labels = ("manual_baseline", "graph_resolved", "graph_resolved_variants")
    images: list[Image.Image] = []
    for method in labels:
        with Image.open(Path(str(methods[method]["image"]))) as image:
            images.append(image.convert("RGBA").copy())
    margin = 24
    output = Image.new("RGBA", (sum(image.width for image in images), images[0].height + margin), (24, 24, 24, 255))
    draw = ImageDraw.Draw(output)
    offset = 0
    for label, image in zip(labels, images):
        draw.text((offset + 4, 4), label, fill=(255, 255, 255, 255))
        output.paste(image, (offset, margin))
        offset += image.width
    return output
