"""End-to-end Transition / Network Edge Contract study runner."""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from pixel_tile_compiler.io.exporter import save_json, save_png
from pixel_tile_compiler.io.loader import load_image
from pixel_tile_compiler.map.metrics import measure_map_metrics
from pixel_tile_compiler.tileset.compiler import TilesetSourceCompiler
from pixel_tile_compiler.tileset.source_tile import TilesetConfig
from pixel_tile_compiler.tileset.validator import validate_material_source

from .assembler import assemble_image, assemble_independent_grid, assemble_semantic_grid, validate_semantic_adjacency
from .contracts import EdgeSemanticProfile, SemanticEdgeContract, SemanticTile, SemanticTileSpec
from .masks import build_road_mask, build_transition_mask
from .metrics import network_map_metrics, transition_map_metrics
from .network import NetworkTileCompiler, NetworkTileConfig
from .transition import TransitionTileCompiler, TransitionTileConfig


@dataclass
class TransitionNetworkStudyConfig:
    output_root: Path = field(default_factory=lambda: Path("e2e/transition_network_study"))
    sources: dict[str, Path] = field(default_factory=dict)
    seed: int = 42
    map_columns: int = 10
    map_rows: int = 10
    palette_budget: int = 24
    shared_palette: bool = True
    surface_variants: int = 12
    surface_edge_types: int = 3
    network_variants: int = 2
    transition_variants: int = 2
    source_size: int = 512
    pixelize: bool = True
    debug_enabled: bool = True
    generation_enabled: bool = False

    def __post_init__(self) -> None:
        self.output_root = Path(self.output_root)
        self.sources = {str(key): Path(value) for key, value in self.sources.items()}
        if self.map_columns < 1 or self.map_rows < 1:
            raise ValueError("map columns and rows must be positive")
        if self.seed < 0:
            raise ValueError("seed must be non-negative")
        if not 4 <= self.palette_budget <= 32:
            raise ValueError("palette_budget must be between 4 and 32")
        if self.surface_variants < 1 or self.network_variants < 1 or self.transition_variants < 1:
            raise ValueError("all variant counts must be positive")
        required = {"grass", "forest_canopy", "dirt_road"}
        missing = required.difference(self.sources)
        if missing:
            raise ValueError(f"study sources are missing: {', '.join(sorted(missing))}")

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any], base_dir: Path | None = None) -> "TransitionNetworkStudyConfig":
        root = Path(base_dir or ".")
        study = mapping.get("study", mapping)
        raw_sources = study.get("sources", study.get("source_materials", {}))
        sources = {
            str(material): _resolve(root, value.get("source_file", value.get("source_path", value)))
            for material, value in raw_sources.items()
        }
        tileset = dict(study.get("tileset", {}))
        return cls(
            output_root=_resolve(root, study.get("output_root", "../e2e/transition_network_study")),
            sources=sources,
            seed=int(study.get("seed", tileset.get("seed", 42))),
            map_columns=int(tileset.get("preview_cols", tileset.get("map_columns", 10))),
            map_rows=int(tileset.get("preview_rows", tileset.get("map_rows", 10))),
            palette_budget=int(tileset.get("palette", tileset.get("palette_budget", 24))),
            shared_palette=bool(tileset.get("shared_palette", True)),
            surface_variants=int(tileset.get("surface_variants", 4)),
            surface_edge_types=int(tileset.get("surface_edge_types", tileset.get("edge_types", 3))),
            network_variants=int(tileset.get("network_variants", 2)),
            transition_variants=int(tileset.get("transition_variants", 2)),
            source_size=int(tileset.get("source_size", 512)),
            pixelize=bool(tileset.get("pixelize", True)),
            debug_enabled=bool(tileset.get("debug_enabled", True)),
            generation_enabled=bool(study.get("generation", {}).get("enabled", False)),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "study": {
                "output_root": str(self.output_root),
                "sources": {key: str(value) for key, value in self.sources.items()},
                "generation": {"enabled": self.generation_enabled},
                "seed": self.seed,
                "tileset": {
                    "preview_cols": self.map_columns,
                    "preview_rows": self.map_rows,
                    "palette": self.palette_budget,
                    "shared_palette": self.shared_palette,
                    "surface_variants": self.surface_variants,
                    "surface_edge_types": self.surface_edge_types,
                    "network_variants": self.network_variants,
                    "transition_variants": self.transition_variants,
                    "source_size": self.source_size,
                    "pixelize": self.pixelize,
                    "debug_enabled": self.debug_enabled,
                },
            }
        }


@dataclass(frozen=True)
class TransitionNetworkStudyResult:
    output_root: Path
    manifest_path: Path
    ranking_path: Path
    montage_path: Path


class TransitionNetworkStudyRunner:
    """Run all families with pinned sources and shared map layouts."""

    def run(self, config: TransitionNetworkStudyConfig) -> TransitionNetworkStudyResult:
        root = config.output_root
        root.mkdir(parents=True, exist_ok=True)
        save_json(config.as_dict(), root / "study_config.json")
        sources = {material: load_image(path) for material, path in config.sources.items()}
        source_records = self._write_source_validation(config, sources, root)
        surface_tiles = self._build_surfaces(config, root, sources)
        network_tiles = self._build_networks(config, root, sources)
        transition_tiles = self._build_transitions(config, root, sources)
        candidates: dict[str, list[SemanticTile]] = {}
        candidates.update(surface_tiles)
        candidates.update(network_tiles)
        candidates.update(transition_tiles)
        candidates = _with_preview_tiles(candidates)
        map_records = self._build_maps(config, root, candidates)
        ranking = _rank_map_records(map_records)
        summary_root = root / "summary"
        summary_root.mkdir(parents=True, exist_ok=True)
        ranking_payload = {
            "top_3": ranking[:3],
            "worst_3": ranking[-3:],
            "ranking": ranking,
            "source_validation": source_records,
        }
        ranking_path = save_json(ranking_payload, summary_root / "method_ranking.json")
        save_json(ranking, root / "metrics" / "comparison_table.json")
        save_json(source_records, root / "metrics" / "source_validation.json")
        (summary_root / "best_sources.md").write_text(_summary_markdown(config, source_records, ranking), encoding="utf-8")
        (summary_root / "best_prompt_template.txt").write_text(_road_prompt_template(), encoding="utf-8")
        montage_path = save_png(_make_montage(root, ranking), summary_root / "montage.png")
        manifest = {
            "study": "transition_network_edge_contract",
            "config": config.as_dict(),
            "contract_conventions": {
                "road_NS": "road connects north/south; EW connects east/west",
                "transition_NS": "material_a is north; material_b is south",
                "transition_EW": "material_a is west; material_b is east",
                "baselines": {
                    "surface_only": "same base material repeated on the fixed layout",
                    "independent_handoff": "same family layout with contract matching ignored",
                    "contract_aware": "same family layout with north/west semantic matching",
                },
            },
            "sources": source_records,
            "families": {
                "surface": {
                    "family_ids": ["grass", "forest_canopy"],
                    "candidate_keys": sorted(surface_tiles),
                },
                "network": {
                    "family_ids": ["dirt_road_grass", "dirt_road_forest_canopy"],
                    "candidate_keys": sorted(network_tiles),
                },
                "transition": {
                    "family_ids": ["grass_forest", "grass_road", "forest_road"],
                    "candidate_keys": sorted(transition_tiles),
                },
            },
            "maps": ranking,
            "summary": {
                "ranking": str(ranking_path),
                "montage": str(montage_path),
                "best_prompt_template": str(summary_root / "best_prompt_template.txt"),
            },
        }
        manifest_path = save_json(manifest, root / "manifest.json")
        return TransitionNetworkStudyResult(root, manifest_path, ranking_path, montage_path)

    @staticmethod
    def _write_source_validation(
        config: TransitionNetworkStudyConfig,
        sources: dict[str, Image.Image],
        root: Path,
    ) -> dict[str, dict[str, object]]:
        records: dict[str, dict[str, object]] = {}
        for material, image in sources.items():
            analysis_material = material if material == "forest_canopy" else "grass"
            report = validate_material_source(image, palette_budget=config.palette_budget, material=analysis_material)
            record = {
                "source_id": material,
                "source_path": str(config.sources[material]),
                "accepted": report.status != "rejected",
                **report.as_dict(),
            }
            save_json(record, root / "validation" / f"{material}.json")
            save_png(image, root / "families" / "surface" / material / "source.png") if material != "dirt_road" else save_png(image, root / "families" / "network" / "dirt_road" / "source.png")
            records[material] = record
        return records

    @staticmethod
    def _build_surfaces(
        config: TransitionNetworkStudyConfig,
        root: Path,
        sources: dict[str, Image.Image],
    ) -> dict[str, list[SemanticTile]]:
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
                    map_columns=config.map_columns,
                    map_rows=config.map_rows,
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
            tiles: list[SemanticTile] = []
            for source_tile, pixel_path in zip(tileset.source_tiles, tileset.pixel_tile_paths):
                pixel_image = Image.open(pixel_path).convert("RGBA").copy()
                spec = SemanticTileSpec(source_tile.spec.tile_id, material, contract, topology="surface", variant=source_tile.spec.seed - config.seed)
                tiles.append(SemanticTile(spec, source_tile.image, pixel_image))
            result[material] = tiles
        return result

    @staticmethod
    def _build_networks(
        config: TransitionNetworkStudyConfig,
        root: Path,
        sources: dict[str, Image.Image],
    ) -> dict[str, list[SemanticTile]]:
        result: dict[str, list[SemanticTile]] = {}
        for base_material in ("grass", "forest_canopy"):
            family_id = f"dirt_road_{base_material}"
            build = NetworkTileCompiler(
                NetworkTileConfig(
                    output_root=root / "families" / "network" / "dirt_road" / f"{base_material}_base",
                    source_size=config.source_size,
                    variants=config.network_variants,
                    palette_budget=config.palette_budget,
                    pixelize=config.pixelize,
                    debug_enabled=config.debug_enabled,
                    seed=config.seed,
                )
            ).build(sources[base_material], sources["dirt_road"], base_material=base_material, family_id=family_id)
            result[family_id] = list(build.tiles)
            for topology in {tile.spec.topology for tile in build.tiles}:
                result[f"{family_id}_{topology}"] = [tile for tile in build.tiles if tile.spec.topology == topology]
        return result

    @staticmethod
    def _build_transitions(
        config: TransitionNetworkStudyConfig,
        root: Path,
        sources: dict[str, Image.Image],
    ) -> dict[str, list[SemanticTile]]:
        compiler = TransitionTileCompiler(
            TransitionTileConfig(
                output_root=root / "families" / "transition",
                source_size=config.source_size,
                variants=config.transition_variants,
                palette_budget=config.palette_budget,
                pixelize=config.pixelize,
                debug_enabled=config.debug_enabled,
                seed=config.seed,
            )
        )
        builds = (
            compiler.build_pair(sources["grass"], sources["forest_canopy"], "grass", "forest_canopy", "grass_forest"),
            compiler.build_network_handoff(sources["grass"], sources["dirt_road"], "grass"),
            compiler.build_network_handoff(sources["forest_canopy"], sources["dirt_road"], "forest_canopy"),
        )
        result: dict[str, list[SemanticTile]] = {}
        for build in builds:
            result[build.family_id] = list(build.tiles)
            for topology in {tile.spec.topology for tile in build.tiles}:
                result[f"{build.family_id}_{topology}"] = [tile for tile in build.tiles if tile.spec.topology == topology]
            for tile in build.tiles:
                key = f"{build.family_id}_{tile.spec.material_a}_to_{tile.spec.material_b}_{tile.spec.topology}"
                result.setdefault(key, []).append(tile)
        return result

    def _build_maps(
        self,
        config: TransitionNetworkStudyConfig,
        root: Path,
        candidates: dict[str, list[SemanticTile]],
    ) -> list[dict[str, object]]:
        records: list[dict[str, object]] = []
        demos = (
            ("transition_demo", _surface_boundary_layout("grass", "forest_canopy", config.map_columns, config.map_rows), "grass"),
            ("road_demo_grass", _road_layout("grass", "grass_road", "dirt_road_grass", config.map_columns, config.map_rows), "grass"),
            ("road_demo_forest", _road_layout("forest_canopy", "forest_road", "dirt_road_forest_canopy", config.map_columns, config.map_rows), "forest_canopy"),
        )
        for demo_id, layout, base_material in demos:
            demo_root = root / "maps" / demo_id
            independent = assemble_independent_grid(layout, candidates, seed=config.seed + 101)
            contract = assemble_semantic_grid(candidates, config.map_columns, config.map_rows, seed=config.seed, family_layout=layout)
            surface_only = [[candidates[base_material][0] for _ in range(config.map_columns)] for _ in range(config.map_rows)]
            methods = {"surface_only": surface_only, "independent_handoff": independent, "contract_aware": contract}
            for method, grid in methods.items():
                image = assemble_image(grid)
                image_path = save_png(image, demo_root / f"{method}.png")
                valid = validate_semantic_adjacency(grid)
                mask = _mask_for_grid(grid)
                map_metrics = measure_map_metrics(image, config.map_columns, config.map_rows, tile_size=64)
                network = network_map_metrics(image, mask, config.map_columns, config.map_rows, 64)
                transition = transition_map_metrics(image, mask, config.map_columns, config.map_rows, 64)
                record = {
                    "demo_id": demo_id,
                    "method": method,
                    "layout": layout,
                    "image": str(image_path),
                    "contract_compatible": valid,
                    "edge_discontinuity_score": round(map_metrics.neighbor_color_discontinuity, 6),
                    "brightness_discontinuity_score": round(map_metrics.neighbor_brightness_discontinuity, 6),
                    "palette_discontinuity_score": round(map_metrics.neighbor_texture_discontinuity, 6),
                    "grid_visibility_score": round(map_metrics.map_grid_visibility_score, 6),
                    "network_metrics": network,
                    "transition_metrics": transition,
                }
                records.append(record)
                save_json(record, demo_root / f"{method}.json")
        return records


def load_transition_network_config(path: Path) -> TransitionNetworkStudyConfig:
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
        raise ValueError("transition/network study config must contain a mapping")
    return TransitionNetworkStudyConfig.from_mapping(mapping, base_dir=path.parent)


def _resolve(base: Path, value: Any) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else base / path


def _surface_boundary_layout(first: str, second: str, columns: int, rows: int) -> list[list[str]]:
    boundary_row = max(1, rows // 2)
    return [
        [first if y < boundary_row else ("grass_forest_NS" if y == boundary_row else second) for _x in range(columns)]
        for y in range(rows)
    ]


def _road_layout(base: str, transition_family: str, network_family: str, columns: int, rows: int) -> list[list[str]]:
    road_row = max(1, rows // 2)
    left_transition = max(1, columns // 5)
    right_transition = min(columns - 2, max(left_transition + 1, columns - left_transition - 1)) if columns >= 4 else min(columns - 1, left_transition + 1)
    left_key = f"{transition_family}_{base}_to_dirt_road_EW"
    right_key = f"{transition_family}_dirt_road_to_{base}_EW"
    network_key = f"{network_family}_EW"
    return [
        [
            base
            if y != road_row or x < left_transition or x > right_transition
            else left_key
            if x == left_transition
            else right_key
            if x == right_transition
            else network_key
            for x in range(columns)
        ]
        for y in range(rows)
    ]


def _mask_for_grid(grid: list[list[SemanticTile]]) -> np.ndarray:
    blocks: list[list[np.ndarray]] = []
    for row in grid:
        row_masks: list[np.ndarray] = []
        for tile in row:
            size = tile.image.size
            if tile.spec.family.startswith("dirt_road_"):
                width = float(tile.spec.metadata.get("road_width", 0.32))
                center = float(tile.spec.metadata.get("road_center", 0.5))
                row_masks.append(build_road_mask(size, tile.spec.topology or "NS", width=width, center=center))
            elif tile.spec.material_a == "dirt_road":
                boundary = float(tile.spec.metadata.get("boundary", 0.5))
                row_masks.append(build_transition_mask(size, tile.spec.topology or "NS", boundary))
            elif tile.spec.material_b == "dirt_road":
                boundary = float(tile.spec.metadata.get("boundary", 0.5))
                row_masks.append(~build_transition_mask(size, tile.spec.topology or "NS", boundary))
            elif tile.spec.family == "grass_forest":
                boundary = float(tile.spec.metadata.get("boundary", 0.5))
                row_masks.append(build_transition_mask(size, tile.spec.topology or "NS", boundary))
            else:
                row_masks.append(np.zeros((size[1], size[0]), dtype=bool))
        blocks.append(row_masks)
    return np.block(blocks).astype(bool)


def _with_preview_tiles(candidates: dict[str, list[SemanticTile]]) -> dict[str, list[SemanticTile]]:
    """Keep map preview dimensions stable even when pixelization is disabled."""
    normalized: dict[str, list[SemanticTile]] = {}
    for family, tiles in candidates.items():
        values: list[SemanticTile] = []
        for tile in tiles:
            image = tile.pixel_image or tile.source_image
            preview = image if image.size == (64, 64) else image.resize((64, 64), Image.Resampling.BICUBIC)
            values.append(SemanticTile(tile.spec, tile.source_image, preview))
        normalized[family] = values
    return normalized


def _rank_map_records(records: list[dict[str, object]]) -> list[dict[str, object]]:
    ranked: list[dict[str, object]] = []
    for record in records:
        network = record["network_metrics"]
        transition = record["transition_metrics"]
        score = (
            0.15 * (1.0 - float(record["edge_discontinuity_score"]))
            + 0.08 * (1.0 - float(record["grid_visibility_score"]))
            + 0.15 * float(network["road_continuity_score"])
            + 0.12 * float(transition["boundary_continuity_score"])
            + 0.10 * (1.0 - float(transition["boundary_abruptness_score"]))
            + 0.12 * float(network["road_presence_score"])
            + 0.12 * float(transition["boundary_presence_score"])
            + 0.16 * float(bool(record["contract_compatible"]))
        )
        ranked.append({**record, "score": round(max(0.0, min(1.0, score)), 6)})
    return sorted(ranked, key=lambda item: (-float(item["score"]), str(item["demo_id"]), str(item["method"])))


def _summary_markdown(config: TransitionNetworkStudyConfig, sources: dict[str, dict[str, object]], ranking: list[dict[str, object]]) -> str:
    top = ", ".join(f"{item['demo_id']}/{item['method']} ({item['score']:.3f})" for item in ranking[:3])
    worst = ", ".join(f"{item['demo_id']}/{item['method']} ({item['score']:.3f})" for item in ranking[-3:])
    return f"""# Transition / Network Edge Contract Study

固定ソース、固定seed={config.seed}、同じfamily layoutで、surface-only / independent handoff / contract-awareを比較した。

## Ranking

- top 3: {top}
- worst 3: {worst}

## 良い接続の条件

- road NS/EW の意味を外側edge profileに持たせ、同じroad feature同士だけを接続する。
- transition NS/EW は material_a/material_b の順序を固定し、外側素材を明示する。
- forest-road は forest base に road mask と dirt material を合成した実物で評価する。
- independent handoff は見た目が近くても、境界中心・幅・素材がずれると break risk が増える。

## 既知の限界

- mask / edge profile は近似的な数値指標で、地形意味理解や完全なWang graphではない。
- cornerは構造先行の矩形stripe＋junctionであり、曲率最適化はまだ行っていない。
- transition boundaryのpixel-level最適化は、現在は既存PixelTileCompilerに委譲している。
"""


def _road_prompt_template() -> str:
    return """Use case: reusable SRPG dirt road material exemplar
Asset type: high-resolution raster source for a structure-first network tileset compiler
Primary request: a top-down orthographic continuous dirt road surface material field, usable as a reusable material source rather than a composed road scene
Texture: compact natural dirt grain, subtle small stones and color variation, no dominant macro bands
Composition: material-only, no road geometry, no vanishing point, no focal point, no center composition
Constraints: seamless-friendly, texture-synthesis-friendly, consistent scale, suitable for 64x64 pixel-art SRPG network tiles
Avoid: perspective, horizon, path composition, fork or intersection illustration, sign, bridge, rock landmark, building, vegetation clump, cast shadow, text, watermark
"""


def _make_montage(root: Path, records: list[dict[str, object]], thumbnail_size: int = 220) -> Image.Image:
    if not records:
        return Image.new("RGBA", (thumbnail_size, thumbnail_size), (24, 24, 24, 255))
    columns = 3
    row_height = thumbnail_size + 28
    montage = Image.new("RGBA", (columns * thumbnail_size, ((len(records) + columns - 1) // columns) * row_height), (24, 24, 24, 255))
    draw = ImageDraw.Draw(montage)
    for index, record in enumerate(records):
        path = Path(str(record["image"]))
        if path.exists():
            with Image.open(path) as image:
                preview = image.convert("RGBA")
                preview.thumbnail((thumbnail_size, thumbnail_size), Image.Resampling.NEAREST)
                x = (index % columns) * thumbnail_size
                y = (index // columns) * row_height
                montage.paste(preview, (x, y))
                draw.text((x + 4, y + thumbnail_size + 5), f"{record['demo_id']} / {record['method']}  {record.get('score', 0):.3f}", fill=(255, 255, 255, 255))
    return montage
