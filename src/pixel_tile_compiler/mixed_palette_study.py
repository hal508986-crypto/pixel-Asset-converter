"""Mixed-material palette architecture qualification study."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import csv
import hashlib
import json
from pathlib import Path
import random
import shutil
from typing import Any, Literal

import numpy as np
from PIL import Image, ImageDraw
from skimage.color import rgb2lab

from pixel_tile_compiler.config import CompilerConfig
from pixel_tile_compiler.io.exporter import save_json, save_png
from pixel_tile_compiler.palette_study import (
    ColorRamp,
    PaletteBudgetStudyConfig,
    PaletteBudgetStudyRunner,
    _clamp,
    _colors_to_lab,
    _effective_palette_size,
    build_semantic_ramp,
)
from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler
from pixel_tile_compiler.pixelizer.palette import quantize_palette
from pixel_tile_compiler.transition_network.graph import NetworkTopologyResolver
from pixel_tile_compiler.transition_network.masks import build_road_mask
from pixel_tile_compiler.transition_network.river import (
    RiverRenderSpec,
    build_river_geometry,
    validate_river_graph,
)
from pixel_tile_compiler.transition_network.river_study import default_river_graph
from pixel_tile_compiler.transition_network.road_graph import default_road_graph


MaterialId = str
ArchitectureKind = Literal["global", "per_material", "semantic_ramp", "shared_core"]
BudgetAxis = Literal["fixed_global", "natural"]

_MATERIALS = ("grass", "dirt", "water", "stone")
_CORE_ROLES = ("deep_shadow", "dark", "light", "highlight", "accent", "neutral")


@dataclass(frozen=True)
class PaletteArchitecture:
    """One palette sharing/allocation strategy under test."""

    condition_id: str
    kind: ArchitectureKind
    global_budget: int | None = None
    per_material_budget: int | None = None
    semantic_shades: int | None = None
    shared_core_size: int | None = None
    material_shades: int | None = None
    budget_axis: BudgetAxis = "fixed_global"

    def __post_init__(self) -> None:
        if not self.condition_id.strip():
            raise ValueError("architecture condition_id must not be empty")
        if self.kind not in {"global", "per_material", "semantic_ramp", "shared_core"}:
            raise ValueError("architecture kind requires one of global/per_material/semantic_ramp/shared_core")
        provided = {
            "global": self.global_budget,
            "per_material": self.per_material_budget,
            "semantic_ramp": self.semantic_shades,
            "shared_core": self.shared_core_size,
        }
        if self.kind != "shared_core" and provided[self.kind] is None:
            raise ValueError(f"{self.kind} architecture requires its budget")
        if self.kind == "shared_core" and (self.shared_core_size is None or self.material_shades is None):
            raise ValueError("shared_core architecture requires core and material shades")
        if self.kind == "global" and self.global_budget is not None and not 4 <= self.global_budget <= 64:
            raise ValueError("global palette budget must be between 4 and 64")
        if self.kind == "per_material" and self.per_material_budget is not None and not 4 <= self.per_material_budget <= 32:
            raise ValueError("per-material palette budget must be between 4 and 32")
        if self.kind == "semantic_ramp" and self.semantic_shades is not None and not 3 <= self.semantic_shades <= 16:
            raise ValueError("semantic ramp shades must be between 3 and 16")
        if self.kind == "shared_core":
            if not 2 <= int(self.shared_core_size or 0) <= 8:
                raise ValueError("shared core size must be between 2 and 8")
            if not 3 <= int(self.material_shades or 0) <= 8:
                raise ValueError("material ramp shades must be between 3 and 8")
        if self.budget_axis not in {"fixed_global", "natural"}:
            raise ValueError("budget_axis must be fixed_global or natural")

    def as_dict(self) -> dict[str, Any]:
        return {
            "condition_id": self.condition_id,
            "kind": self.kind,
            "global_budget": self.global_budget,
            "per_material_budget": self.per_material_budget,
            "semantic_shades": self.semantic_shades,
            "shared_core_size": self.shared_core_size,
            "material_shades": self.material_shades,
            "budget_axis": self.budget_axis,
        }


@dataclass(frozen=True)
class SharedCorePalette:
    roles: tuple[str, ...]
    colors: tuple[tuple[int, int, int], ...]

    def __post_init__(self) -> None:
        if len(self.roles) != len(self.colors) or not self.colors:
            raise ValueError("shared core requires one role for every color")

    def as_dict(self) -> dict[str, Any]:
        return {"roles": list(self.roles), "colors": [list(color) for color in self.colors]}


@dataclass(frozen=True)
class MixedMapArtifact:
    image: Image.Image
    material_map: np.ndarray
    road_mask: np.ndarray
    river_body_mask: np.ndarray
    river_bank_mask: np.ndarray
    stone_mask: np.ndarray
    layout: dict[str, Any]


@dataclass(frozen=True)
class ResolvedPaletteArchitecture:
    spec: PaletteArchitecture
    image: Image.Image
    global_palette: tuple[tuple[int, int, int], ...] | None
    material_palettes: dict[str, tuple[tuple[int, int, int], ...]]
    material_ramps: dict[str, ColorRamp]
    shared_core: SharedCorePalette | None
    material_map_shape: tuple[int, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "condition": self.spec.as_dict(),
            "global_palette": [list(color) for color in self.global_palette] if self.global_palette else None,
            "material_palettes": {
                material: [list(color) for color in colors]
                for material, colors in self.material_palettes.items()
            },
            "material_ramps": {material: ramp.as_dict() for material, ramp in self.material_ramps.items()},
            "shared_core": self.shared_core.as_dict() if self.shared_core else None,
            "material_map_shape": list(self.material_map_shape),
        }


@dataclass
class MixedMaterialPaletteStudyConfig:
    output_root: Path = field(default_factory=lambda: Path("e2e/mixed_palette_architecture_study"))
    source_study_root: Path = field(default_factory=lambda: Path("e2e/real_t2i_material_qualification"))
    materials: tuple[str, ...] = _MATERIALS
    source_selection: dict[str, str] = field(
        default_factory=lambda: {
            "grass": "grass_real_05",
            "dirt": "dirt_real_02",
            "water": "water_real_01",
            "stone": "stone_real_05",
        }
    )
    conditions: tuple[PaletteArchitecture, ...] = field(default_factory=lambda: default_architectures())
    tile_size: int = 64
    map_columns: int = 10
    map_rows: int = 10
    variants: int = 1
    freeze_source: bool = True
    dithering: Literal["off"] = "off"
    seed: int = 42

    def __post_init__(self) -> None:
        self.output_root = Path(self.output_root)
        self.source_study_root = Path(self.source_study_root)
        if not self.freeze_source:
            raise ValueError("mixed palette study requires freeze_source=true")
        if tuple(self.materials) != _MATERIALS:
            raise ValueError("the MVP mixed map requires grass, dirt, water, and stone")
        if self.tile_size != 64:
            raise ValueError("the MVP mixed map tile_size must be 64")
        if self.map_columns != 10 or self.map_rows != 10:
            raise ValueError("the MVP mixed map is fixed at 10x10")
        if self.variants < 1:
            raise ValueError("variants must be positive")
        if set(self.source_selection) != set(self.materials):
            raise ValueError("source_selection must cover every material")
        if not self.conditions:
            raise ValueError("at least one palette architecture is required")
        if len({condition.condition_id for condition in self.conditions}) != len(self.conditions):
            raise ValueError("architecture condition_id values must be unique")

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any], base_dir: Path | None = None) -> "MixedMaterialPaletteStudyConfig":
        root = Path(base_dir or ".")
        study = dict(mapping.get("study") or {})
        source = dict(mapping.get("source") or {})
        map_config = dict(mapping.get("map") or {})
        raw_conditions = list(study.get("conditions") or [])
        conditions = tuple(_architecture_from_mapping(value) for value in raw_conditions) if raw_conditions else default_architectures()
        selections = {str(key): str(value) for key, value in dict(source.get("selections") or {}).items()}
        return cls(
            output_root=_resolve_path(root, mapping.get("output", "../e2e/mixed_palette_architecture_study")),
            source_study_root=_resolve_path(root, source.get("real_study_root", "../e2e/real_t2i_material_qualification")),
            materials=tuple(str(value) for value in study.get("materials", _MATERIALS)),
            source_selection=selections or dict(cls().source_selection),
            conditions=conditions,
            tile_size=int(study.get("tile_size", 64)),
            map_columns=int(map_config.get("width", 10)),
            map_rows=int(map_config.get("height", 10)),
            variants=int(study.get("variants", 1)),
            freeze_source=bool(source.get("freeze_source", True)),
            seed=int(mapping.get("seed", 42)),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "study": {
                "materials": list(self.materials),
                "tile_size": self.tile_size,
                "variants": self.variants,
                "conditions": [condition.as_dict() for condition in self.conditions],
            },
            "source": {
                "real_study_root": str(self.source_study_root),
                "selections": dict(self.source_selection),
                "freeze_source": self.freeze_source,
            },
            "map": {"width": self.map_columns, "height": self.map_rows},
            "dithering": self.dithering,
            "seed": self.seed,
            "output": str(self.output_root),
        }


@dataclass(frozen=True)
class MixedMaterialPaletteStudyResult:
    output_root: Path
    status: str
    conditions: tuple[dict[str, Any], ...]


def default_architectures() -> tuple[PaletteArchitecture, ...]:
    """The minimum fair-budget round: four strategies, 24 effective slots."""
    return (
        PaletteArchitecture("global_24", "global", global_budget=24),
        PaletteArchitecture("per_material_6x4", "per_material", per_material_budget=6),
        PaletteArchitecture("semantic_ramp_6shade", "semantic_ramp", semantic_shades=6),
        PaletteArchitecture("shared_core4_material5", "shared_core", shared_core_size=4, material_shades=5),
    )


def load_mixed_palette_config(path: Path) -> MixedMaterialPaletteStudyConfig:
    path = Path(path)
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        mapping = json.loads(raw)
    else:
        import yaml  # type: ignore[import-not-found]

        mapping = yaml.safe_load(raw)
    if not isinstance(mapping, dict):
        raise ValueError("mixed palette config must contain a mapping")
    return MixedMaterialPaletteStudyConfig.from_mapping(mapping, path.parent)


def build_fixed_mixed_map(
    sources: dict[str, Image.Image],
    columns: int = 10,
    rows: int = 10,
    tile_size: int = 64,
    seed: int = 42,
) -> MixedMapArtifact:
    """Render one deterministic mixed MAP from existing graph/geometry rules."""
    del seed  # graph paths and renderer geometry are fixed for the first round
    missing = set(_MATERIALS).difference(sources)
    if missing:
        raise ValueError(f"mixed map sources are missing: {', '.join(sorted(missing))}")
    width, height = columns * tile_size, rows * tile_size
    textures: dict[str, Image.Image] = {}
    for material in _MATERIALS:
        textures[material] = sources[material].convert("RGB").resize((tile_size, tile_size), Image.Resampling.BICUBIC)

    material_map = np.full((height, width), "grass", dtype=object)
    road_mask = np.zeros((height, width), dtype=bool)
    river_body_mask = np.zeros((height, width), dtype=bool)
    river_bank_mask = np.zeros((height, width), dtype=bool)
    stone_mask = np.zeros((height, width), dtype=bool)

    road_graph = default_road_graph(columns, rows)
    road_cells = NetworkTopologyResolver().resolve_grid(road_graph)
    river_graph = default_river_graph(columns, rows)
    river_cells = validate_river_graph(river_graph, allow_split=True).flow_cells
    river_spec = RiverRenderSpec(
        source_size=tile_size,
        base_width_ratio=0.26,
        width_variation=0.04,
        bank_width_ratio=0.04,
        center=0.5,
        seed=42,
    )

    for point, cell in road_cells.items():
        if not cell.is_network:
            continue
        local = build_road_mask((tile_size, tile_size), cell.topology.value, width=0.22, center=0.5)
        box = _tile_box(point.x, point.y, tile_size)
        road_mask[box[1]:box[3], box[0]:box[2]] |= local

    for point, cell in river_cells.items():
        if not cell.is_network:
            continue
        geometry = build_river_geometry(
            (tile_size, tile_size),
            cell.topology,
            cell.incoming,
            cell.outgoing,
            river_spec,
            seed=42 + point.x * 1009 + point.y * 9176,
        )
        box = _tile_box(point.x, point.y, tile_size)
        river_body_mask[box[1]:box[3], box[0]:box[2]] |= geometry.body_mask
        river_bank_mask[box[1]:box[3], box[0]:box[2]] |= geometry.bank_mask

    stone_x, stone_y = columns - 2, 1
    stone_image = Image.new("L", (tile_size, tile_size), 0)
    draw = ImageDraw.Draw(stone_image)
    draw.ellipse((10, 8, tile_size - 8, tile_size - 10), fill=255)
    stone_local = np.asarray(stone_image, dtype=np.uint8) > 0
    stone_box = _tile_box(stone_x, stone_y, tile_size)
    stone_mask[stone_box[1]:stone_box[3], stone_box[0]:stone_box[2]] = stone_local

    material_map[road_mask] = "dirt"
    material_map[river_bank_mask] = "dirt"
    material_map[river_body_mask] = "water"
    material_map[stone_mask] = "stone"

    texture_canvas = {
        material: Image.new("RGB", (width, height), (0, 0, 0)) for material in _MATERIALS
    }
    for y in range(rows):
        for x in range(columns):
            box = _tile_box(x, y, tile_size)
            for material in _MATERIALS:
                texture_canvas[material].paste(textures[material], (box[0], box[1]))
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    for material in _MATERIALS:
        texture = np.asarray(texture_canvas[material], dtype=np.uint8)
        canvas[material_map == material] = texture[material_map == material]

    counts = Counter(str(value) for value in material_map.reshape(-1))
    layout = {
        "schema_version": 1,
        "columns": columns,
        "rows": rows,
        "tile_size": tile_size,
        "road_graph": road_graph.as_dict(),
        "river_graph": river_graph.as_dict(),
        "road_network_cells": [cell.as_dict() for cell in road_cells.values() if cell.is_network],
        "river_network_cells": [cell.as_dict() for cell in river_cells.values() if cell.is_network],
        "stone_object": {"x": stone_x, "y": stone_y, "shape": "ellipse"},
        "material_counts": dict(sorted(counts.items())),
        "mask_counts": {
            "road": int(road_mask.sum()),
            "river_body": int(river_body_mask.sum()),
            "river_bank": int(river_bank_mask.sum()),
            "stone": int(stone_mask.sum()),
        },
    }
    return MixedMapArtifact(
        Image.fromarray(canvas, mode="RGB").convert("RGBA"),
        material_map,
        road_mask,
        river_body_mask,
        river_bank_mask,
        stone_mask,
        layout,
    )


def resolve_palette_architecture(
    condition: PaletteArchitecture,
    image: Image.Image,
    material_map: np.ndarray,
    sources: dict[str, Image.Image],
) -> ResolvedPaletteArchitecture:
    """Apply only palette allocation to a fixed mixed source image."""
    if material_map.shape != (image.height, image.width):
        raise ValueError("material_map must match image dimensions")
    if set(sources) != set(_MATERIALS):
        raise ValueError("sources must cover the four mixed-map materials")
    material_palettes: dict[str, tuple[tuple[int, int, int], ...]] = {}
    material_ramps: dict[str, ColorRamp] = {}
    shared_core: SharedCorePalette | None = None
    global_palette: tuple[tuple[int, int, int], ...] | None = None

    if condition.kind == "global":
        quantized = quantize_palette(image.convert("RGB"), budget=int(condition.global_budget or 24))
        global_palette = _unique_colors(quantized)
        output = quantized.convert("RGBA")
    elif condition.kind == "per_material":
        for material in _MATERIALS:
            palette = _source_palette(sources[material], int(condition.per_material_budget or 6))
            material_palettes[material] = palette
            material_ramps[material] = _ramp_from_colors(material, palette)
        output = _apply_masked_palettes(image, material_map, material_palettes)
    elif condition.kind == "semantic_ramp":
        for material in _MATERIALS:
            ramp = build_semantic_ramp(sources[material], int(condition.semantic_shades or 6), material)
            material_ramps[material] = ramp
            material_palettes[material] = ramp.colors
        output = _apply_masked_palettes(image, material_map, material_palettes)
    else:
        shared_core = _build_shared_core(image, int(condition.shared_core_size or 4))
        for material in _MATERIALS:
            ramp = build_semantic_ramp(sources[material], int(condition.material_shades or 5), material)
            material_ramps[material] = ramp
            material_palettes[material] = _dedupe_colors(shared_core.colors + ramp.colors)
        output = _apply_masked_palettes(image, material_map, material_palettes)
    return ResolvedPaletteArchitecture(
        condition,
        output,
        global_palette,
        material_palettes,
        material_ramps,
        shared_core,
        material_map.shape,
    )


def compute_mixed_palette_metrics(resolved: ResolvedPaletteArchitecture, mixed: MixedMapArtifact) -> dict[str, Any]:
    """Return separated architecture metrics; no single score is treated as truth."""
    final = np.asarray(resolved.image.convert("RGB"), dtype=np.uint8)
    baseline = np.asarray(mixed.image.convert("RGB"), dtype=np.uint8)
    final_lab = rgb2lab(final.astype(np.float32) / 255.0)
    baseline_lab = rgb2lab(baseline.astype(np.float32) / 255.0)
    material_means: dict[str, np.ndarray] = {}
    baseline_means: dict[str, np.ndarray] = {}
    for material in _MATERIALS:
        mask = mixed.material_map == material
        material_means[material] = final_lab[mask].mean(axis=0) if mask.any() else np.zeros(3)
        baseline_means[material] = baseline_lab[mask].mean(axis=0) if mask.any() else np.zeros(3)
    distances = [
        float(np.linalg.norm(material_means[first] - material_means[second]))
        for index, first in enumerate(_MATERIALS)
        for second in _MATERIALS[index + 1:]
    ]
    material_separation = _clamp(float(np.mean(distances)) / 80.0) if distances else 0.0
    identity_loss = float(np.mean([
        np.linalg.norm(material_means[material] - baseline_means[material]) / 100.0
        for material in _MATERIALS
    ]))
    boundary_score = _boundary_contrast(final_lab, mixed.material_map)
    road_separation = _feature_background_contrast(final_lab, mixed.road_mask, mixed.material_map != "dirt")
    river_separation = _feature_background_contrast(final_lab, mixed.river_body_mask, mixed.material_map != "water")
    object_separation = _feature_background_contrast(final_lab, mixed.stone_mask, mixed.material_map != "stone")
    colors = _unique_colors(resolved.image)
    labs = _colors_to_lab(list(colors))
    redundant_ratio = _redundant_cross_material_ratio(final, mixed.material_map)
    shared_utilization = _shared_color_utilization(final, resolved.shared_core)
    harmony = _global_harmony(final_lab, redundant_ratio)
    cluster_coherence, isolated_ratio = _cluster_metrics(final)
    semantic = float(np.mean([road_separation, river_separation, object_separation]))
    readability = float(np.mean([boundary_score, semantic, 1.0 - isolated_ratio]))
    effective_count = _effective_palette_size(labs)
    efficiency = _clamp((0.28 * harmony + 0.28 * material_separation + 0.24 * readability + 0.20 * (1.0 - identity_loss)) / max(1.0, effective_count / 24.0))
    return {
        "global_palette_harmony_score": round(harmony, 6),
        "material_separation_score": round(material_separation, 6),
        "material_identity_loss_score": round(_clamp(identity_loss), 6),
        "material_identity_score": round(_clamp(1.0 - identity_loss), 6),
        "shared_color_utilization": round(shared_utilization, 6),
        "cross_material_redundant_color_ratio": round(redundant_ratio, 6),
        "cluster_coherence_score": round(cluster_coherence, 6),
        "isolated_pixel_ratio": round(isolated_ratio, 6),
        "effective_global_palette_size": effective_count,
        "global_palette_size": len(colors),
        "palette_efficiency_score": round(efficiency, 6),
        "road_background_separation_score": round(road_separation, 6),
        "river_background_separation_score": round(river_separation, 6),
        "object_background_separation_score": round(object_separation, 6),
        "terrain_boundary_readability": round(boundary_score, 6),
        "semantic_readability_score": round(semantic, 6),
        "map_readability_score": round(readability, 6),
        "road_continuity_score": round(_mask_boundary_continuity(mixed.road_mask, mixed.layout), 6),
        "road_break_risk": round(1.0 - _mask_boundary_continuity(mixed.road_mask, mixed.layout), 6),
        "river_continuity_score": round(_mask_boundary_continuity(mixed.river_body_mask, mixed.layout), 6),
        "river_break_risk": round(1.0 - _mask_boundary_continuity(mixed.river_body_mask, mixed.layout), 6),
        "material_pixel_counts": {material: int((mixed.material_map == material).sum()) for material in _MATERIALS},
    }


class MixedMaterialPaletteArchitectureStudyRunner:
    """Run the first small architecture round against one fixed Mixed MAP."""

    def run(self, config: MixedMaterialPaletteStudyConfig) -> MixedMaterialPaletteStudyResult:
        root = Path(config.output_root)
        root.mkdir(parents=True, exist_ok=True)
        for child in ("controls", "conditions", "maps", "blind_review", "summary"):
            target = root / child
            if target.exists():
                shutil.rmtree(target)
        save_json(config.as_dict(), root / "study_config.json")
        source_manifest = self._freeze_sources(config, root)
        sources = self._compile_control_textures(config, root, source_manifest)
        mixed = build_fixed_mixed_map(sources, config.map_columns, config.map_rows, config.tile_size, config.seed)
        save_png(mixed.image, root / "maps" / "fixed_source_map.png")
        save_json(mixed.layout, root / "layout.json")
        records: list[dict[str, Any]] = []
        for condition in config.conditions:
            resolved = resolve_palette_architecture(condition, mixed.image, mixed.material_map, sources)
            condition_root = root / "conditions" / condition.condition_id
            condition_root.mkdir(parents=True, exist_ok=True)
            full_map = save_png(resolved.image, condition_root / "full_map.png")
            crops = self._write_crops(resolved.image, condition_root, config.tile_size)
            palette_path = save_png(_palette_visualization(resolved), condition_root / "palette.png")
            metrics = compute_mixed_palette_metrics(resolved, mixed)
            score = _overall_score(metrics)
            record = {
                "condition": condition.as_dict(),
                "source_ids": dict(config.source_selection),
                "source_sha256": {material: source_manifest[material]["source_sha256"] for material in _MATERIALS},
                "fixed_layout_sha256": _sha256_bytes(json.dumps(mixed.layout, sort_keys=True).encode("utf-8")),
                "variant_count": config.variants,
                "full_map_path": str(full_map),
                "crops": crops,
                "palette_path": str(palette_path),
                "metrics": {**metrics, "overall_architecture_score": round(score, 6)},
                "palette_manifest": resolved.as_dict(),
            }
            save_json(record, condition_root / "record.json")
            records.append(record)
        self._write_summary(root, records)
        self._write_blind_review(root, records, config.seed)
        save_json(records, root / "manifest.json")
        return MixedMaterialPaletteStudyResult(root, "completed", tuple(records))

    def _freeze_sources(self, config: MixedMaterialPaletteStudyConfig, root: Path) -> dict[str, dict[str, Any]]:
        budget_config = PaletteBudgetStudyConfig(
            output_root=root,
            source_study_root=config.source_study_root,
            materials=config.materials,
            source_selection=config.source_selection,
            palette_budgets=(24,),
            palette_modes=("global_free",),
            mode_budgets={"global_free": (24,)},
            variants=1,
            map_conditions=(),
            semantic_conditions=(),
            semantic_materials=(),
            seed=config.seed,
        )
        manifest = PaletteBudgetStudyRunner()._freeze_sources(budget_config, root)
        save_json(manifest, root / "source_manifest.json")
        return manifest

    def _compile_control_textures(
        self,
        config: MixedMaterialPaletteStudyConfig,
        root: Path,
        source_manifest: dict[str, dict[str, Any]],
    ) -> dict[str, Image.Image]:
        textures: dict[str, Image.Image] = {}
        for index, material in enumerate(_MATERIALS):
            source_path = Path(source_manifest[material]["frozen_source_path"])
            with Image.open(source_path) as opened:
                result = PixelTileCompiler().compile_image(
                    opened.convert("RGB"),
                    CompilerConfig(
                        output_root=root / "controls" / material,
                        palette_budget=64,
                        quantize_enabled=False,
                        tile_mode="repeatable",
                        semantic_provider="rule",
                        seam_mode="off",
                        repeat_opt_enabled=False,
                        dither="off",
                        background_mode="color",
                        background_color="#000000",
                        work_size=128,
                        smoothing_enabled=False,
                        seed=config.seed + index,
                        debug_enabled=False,
                    ),
                    source_name=f"mixed-palette-control:{material}",
                )
            with Image.open(result.final_path) as texture:
                textures[material] = texture.convert("RGB").copy()
            source_manifest[material]["compiled_control_path"] = str(result.final_path)
        save_json(source_manifest, root / "source_manifest.json")
        return textures

    @staticmethod
    def _write_crops(image: Image.Image, root: Path, tile_size: int) -> dict[str, str]:
        boxes = {
            "road": (0, tile_size, tile_size * 10, tile_size * 4),
            "river": (0, tile_size * 4, tile_size * 10, tile_size * 7),
            "stone_object": (tile_size * 7, 0, tile_size * 10, tile_size * 3),
        }
        paths: dict[str, str] = {}
        for name, box in boxes.items():
            paths[name] = str(save_png(image.crop(box), root / f"{name}_crop.png"))
        return paths

    def _write_summary(self, root: Path, records: list[dict[str, Any]]) -> None:
        ranked = sorted(
            (
                {
                    "condition_id": record["condition"]["condition_id"],
                    "kind": record["condition"]["kind"],
                    "overall_architecture_score": record["metrics"]["overall_architecture_score"],
                    "global_harmony": record["metrics"]["global_palette_harmony_score"],
                    "material_identity": record["metrics"]["material_identity_score"],
                    "map_readability": record["metrics"]["map_readability_score"],
                    "palette_efficiency": record["metrics"]["palette_efficiency_score"],
                }
                for record in records
            ),
            key=lambda item: (-float(item["overall_architecture_score"]), str(item["condition_id"])),
        )
        summary_root = root / "summary"
        summary_root.mkdir(parents=True, exist_ok=True)
        save_json(ranked, summary_root / "architecture_ranking.json")
        save_json(
            [
                {
                    "condition_id": item["condition_id"],
                    "global_harmony": item["global_harmony"],
                    "material_identity": item["material_identity"],
                }
                for item in ranked
            ],
            summary_root / "harmony_identity_tradeoff.json",
        )
        save_json(
            [
                {
                    "condition_id": item["condition_id"],
                    "effective_global_palette_size": next(
                        record["metrics"]["effective_global_palette_size"]
                        for record in records
                        if record["condition"]["condition_id"] == item["condition_id"]
                    ),
                    "palette_efficiency_score": item["palette_efficiency"],
                }
                for item in ranked
            ],
            summary_root / "palette_efficiency.json",
        )
        save_json(
            {
                "status": "provisional_auto_recommendation",
                "condition_id": ranked[0]["condition_id"] if ranked else None,
                "reason": "automatic metrics only; blind human review is still pending",
            },
            summary_root / "recommended_architecture.json",
        )
        save_png(_comparison_board(root, records), summary_root / "comparison_board.png")
        report = [
            "# Mixed Material Palette Architecture Study",
            "",
            "This is the minimum fixed-budget round: one 10x10 MAP, four architectures, and one deterministic control texture per material.",
            "Palette Architecture is changed after the fixed source/graph/geometry stage. Dithering is off.",
            "",
            "## Automatic result",
            "",
        ]
        for item in ranked:
            report.append(
                f"- `{item['condition_id']}`: overall={item['overall_architecture_score']}, harmony={item['global_harmony']}, identity={item['material_identity']}, readability={item['map_readability']}"
            )
        report.extend(
            [
                "",
                "## Interpretation boundary",
                "",
                "The automatic ranking is provisional. It does not prove that Shared Core is superior, and it must be read with the separated harmony, identity, semantic, and efficiency metrics plus blind human review.",
                "Natural-budget variants, Core 0/2/6, 4/5-shade branches, multiple fixed MAPs, and Unit overlay are deferred until the first board is inspected.",
            ]
        )
        (summary_root / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")

    @staticmethod
    def _write_blind_review(root: Path, records: list[dict[str, Any]], seed: int) -> None:
        review_root = root / "blind_review"
        review_root.mkdir(parents=True, exist_ok=True)
        shuffled = list(records)
        random.Random(seed).shuffle(shuffled)
        mapping: dict[str, dict[str, Any]] = {}
        board_width = 640
        board = Image.new("RGBA", (board_width * 2, (360 + 28) * ((len(shuffled) + 1) // 2)), (28, 28, 28, 255))
        draw = ImageDraw.Draw(board)
        for index, record in enumerate(shuffled):
            review_id = f"M-{index + 1:02d}"
            image = Image.open(Path(record["full_map_path"])).convert("RGBA")
            image.thumbnail((board_width, 360), Image.Resampling.LANCZOS)
            x = (index % 2) * board_width
            y = (index // 2) * (360 + 28)
            draw.text((x + 8, y + 6), review_id, fill=(255, 255, 255, 255))
            board.paste(image, (x, y + 28))
            mapping[review_id] = {"condition_id": record["condition"]["condition_id"]}
        save_png(board, review_root / "board.png")
        save_json(mapping, review_root / "mapping.json")
        with (review_root / "review.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=["review_id", "pixel_art_likeness", "map_harmony", "material_readability", "overall_map_quality", "favorite", "notes"],
            )
            writer.writeheader()
            for review_id in mapping:
                writer.writerow({"review_id": review_id})


def import_mixed_palette_review(study_root: Path, csv_path: Path) -> dict[str, Any]:
    """Attach blind ratings to condition records without auto-promoting a profile."""
    study_root = Path(study_root)
    mapping = json.loads((study_root / "blind_review" / "mapping.json").read_text(encoding="utf-8"))
    imported = 0
    with Path(csv_path).open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            review_id = str(row.get("review_id", "")).strip()
            if not review_id or review_id not in mapping:
                raise ValueError(f"unknown mixed palette review_id: {review_id}")
            review: dict[str, Any] = {}
            for field_name in ("pixel_art_likeness", "map_harmony", "material_readability", "overall_map_quality"):
                raw = str(row.get(field_name, "")).strip()
                if raw:
                    value = int(raw)
                    if not 1 <= value <= 5:
                        raise ValueError(f"{field_name} must be between 1 and 5")
                    review[field_name] = value
            preferred = str(row.get("favorite", "")).strip().lower()
            if preferred:
                if preferred not in {"yes", "no"}:
                    raise ValueError("favorite must be yes or no")
                review["favorite"] = preferred == "yes"
            notes = str(row.get("notes", "")).strip()
            if notes:
                review["notes"] = notes
            condition_id = str(mapping[review_id]["condition_id"])
            record_path = study_root / "conditions" / condition_id / "record.json"
            record = json.loads(record_path.read_text(encoding="utf-8"))
            record["human_review"] = {"review_id": review_id, **review}
            save_json(record, record_path)
            imported += 1
    result = {
        "human_reviews_imported": imported,
        "auto_promotion": False,
        "note": "human review is attached to conditions only; recommended architecture remains provisional",
    }
    save_json(result, study_root / "summary" / "human_review_import.json")
    return result


def _architecture_from_mapping(value: Any) -> PaletteArchitecture:
    if not isinstance(value, dict):
        raise ValueError("mixed palette conditions must be mappings")
    return PaletteArchitecture(
        condition_id=str(value.get("condition_id", value.get("id", ""))),
        kind=str(value.get("kind", "global")),  # type: ignore[arg-type]
        global_budget=_optional_int(value.get("global_budget")),
        per_material_budget=_optional_int(value.get("per_material_budget")),
        semantic_shades=_optional_int(value.get("semantic_shades")),
        shared_core_size=_optional_int(value.get("shared_core_size")),
        material_shades=_optional_int(value.get("material_shades")),
        budget_axis=str(value.get("budget_axis", "fixed_global")),  # type: ignore[arg-type]
    )


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _source_palette(source: Image.Image, budget: int) -> tuple[tuple[int, int, int], ...]:
    return _unique_colors(quantize_palette(source.convert("RGB"), budget=budget))


def _build_shared_core(image: Image.Image, size: int) -> SharedCorePalette:
    colors = _source_palette(image, size)
    colors = tuple(sorted(colors, key=lambda color: float(_colors_to_lab([color])[0, 0])))
    roles = _CORE_ROLES[:len(colors)]
    return SharedCorePalette(roles, colors)


def _ramp_from_colors(material: str, colors: tuple[tuple[int, int, int], ...]) -> ColorRamp:
    ordered = tuple(sorted(colors, key=lambda color: float(_colors_to_lab([color])[0, 0])))
    roles = tuple(f"shade_{index:02d}" for index in range(len(ordered)))
    return ColorRamp(material, roles, ordered, monotonic_value=True)


def _apply_masked_palettes(image: Image.Image, material_map: np.ndarray, palettes: dict[str, tuple[tuple[int, int, int], ...]]) -> Image.Image:
    source = image.convert("RGB")
    output = np.zeros((source.height, source.width, 4), dtype=np.uint8)
    for material in _MATERIALS:
        mask = material_map == material
        if not mask.any():
            continue
        palette = palettes[material]
        quantized = quantize_palette(source, budget=max(4, len(palette)), palette_colors=palette)
        output[mask, :3] = np.asarray(quantized.convert("RGB"), dtype=np.uint8)[mask]
        output[mask, 3] = 255
    return Image.fromarray(output, mode="RGBA")


def _unique_colors(image: Image.Image) -> tuple[tuple[int, int, int], ...]:
    return tuple(sorted({tuple(int(channel) for channel in pixel[:3]) for pixel in image.convert("RGB").getdata()}))


def _dedupe_colors(colors: tuple[tuple[int, int, int], ...]) -> tuple[tuple[int, int, int], ...]:
    return tuple(dict.fromkeys(colors))


def _palette_visualization(resolved: ResolvedPaletteArchitecture) -> Image.Image:
    sections: list[tuple[str, tuple[tuple[int, int, int], ...]]] = []
    if resolved.shared_core:
        sections.append(("Shared Core", resolved.shared_core.colors))
    if resolved.global_palette:
        sections.append(("Global", resolved.global_palette))
    for material in _MATERIALS:
        colors = resolved.material_palettes.get(material, ())
        if colors:
            sections.append((material, colors))
    width = max(240, max((len(colors) for _, colors in sections), default=1) * 24 + 12)
    height = max(32, len(sections) * 38)
    output = Image.new("RGBA", (width, height), (30, 30, 30, 255))
    draw = ImageDraw.Draw(output)
    for row, (label, colors) in enumerate(sections):
        y = row * 38
        draw.text((4, y + 4), label, fill=(255, 255, 255, 255))
        x = 96
        for color in colors:
            draw.rectangle((x, y + 2, x + 20, y + 22), fill=(*color, 255))
            x += 24
    return output


def _boundary_contrast(lab: np.ndarray, material_map: np.ndarray) -> float:
    deltas: list[float] = []
    for axis in (0, 1):
        first = material_map.take(indices=range(material_map.shape[axis] - 1), axis=axis)
        second = material_map.take(indices=range(1, material_map.shape[axis]), axis=axis)
        different = first != second
        if axis == 0:
            delta = np.linalg.norm(lab[:-1] - lab[1:], axis=2)
        else:
            delta = np.linalg.norm(lab[:, :-1] - lab[:, 1:], axis=2)
        if different.any():
            deltas.extend(delta[different].tolist())
    return _clamp(float(np.mean(deltas)) / 48.0) if deltas else 0.0


def _feature_background_contrast(lab: np.ndarray, feature_mask: np.ndarray, background_mask: np.ndarray) -> float:
    if not feature_mask.any():
        return 0.0
    dilated = np.zeros_like(feature_mask)
    dilated[1:] |= feature_mask[:-1]
    dilated[:-1] |= feature_mask[1:]
    dilated[:, 1:] |= feature_mask[:, :-1]
    dilated[:, :-1] |= feature_mask[:, 1:]
    neighbor = dilated & background_mask & ~feature_mask
    if not neighbor.any():
        return 0.0
    feature_mean = lab[feature_mask].mean(axis=0)
    neighbor_mean = lab[neighbor].mean(axis=0)
    return _clamp(float(np.linalg.norm(feature_mean - neighbor_mean)) / 60.0)


def _redundant_cross_material_ratio(image: np.ndarray, material_map: np.ndarray) -> float:
    palettes = {
        material: _colors_to_lab([tuple(int(channel) for channel in color) for color in np.unique(image[material_map == material], axis=0)])
        for material in _MATERIALS
        if (material_map == material).any()
    }
    total = 0
    redundant = 0
    for index, first in enumerate(_MATERIALS):
        for second in _MATERIALS[index + 1:]:
            first_lab = palettes.get(first, np.empty((0, 3)))
            second_lab = palettes.get(second, np.empty((0, 3)))
            for color in first_lab:
                total += 1
                if len(second_lab) and np.min(np.linalg.norm(second_lab - color, axis=1)) <= 8.0:
                    redundant += 1
    return redundant / total if total else 0.0


def _shared_color_utilization(image: np.ndarray, core: SharedCorePalette | None) -> float:
    if core is None:
        return 0.0
    used = {tuple(int(channel) for channel in color) for color in image.reshape(-1, 3)}
    return sum(color in used for color in core.colors) / len(core.colors)


def _global_harmony(lab: np.ndarray, redundant_ratio: float) -> float:
    luminance = lab[:, :, 0]
    return _clamp(0.65 * (1.0 - redundant_ratio) + 0.35 * (1.0 - min(1.0, float(luminance.std()) / 80.0)))


def _cluster_metrics(rgb: np.ndarray) -> tuple[float, float]:
    total = rgb.shape[0] * rgb.shape[1]
    if not total:
        return 0.0, 0.0
    largest_sum = 0
    isolated = 0
    for color in np.unique(rgb.reshape(-1, 3), axis=0):
        mask = np.all(rgb == color, axis=2)
        visited = np.zeros(mask.shape, dtype=bool)
        for y, x in zip(*np.where(mask)):
            if visited[y, x]:
                continue
            stack = [(int(y), int(x))]
            visited[y, x] = True
            size = 0
            while stack:
                cy, cx = stack.pop()
                size += 1
                for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                    if 0 <= ny < mask.shape[0] and 0 <= nx < mask.shape[1] and mask[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = True
                        stack.append((ny, nx))
            largest_sum += size
            if size == 1:
                isolated += 1
    return _clamp(largest_sum / total), isolated / total


def _mask_boundary_continuity(mask: np.ndarray, layout: dict[str, Any]) -> float:
    columns = int(layout["columns"])
    rows = int(layout["rows"])
    tile_size = int(layout["tile_size"])
    if not mask.any():
        return 1.0
    matches = 0
    total = 0
    for y in range(rows):
        for x in range(columns - 1):
            left = mask[y * tile_size:(y + 1) * tile_size, (x + 1) * tile_size - 1]
            right = mask[y * tile_size:(y + 1) * tile_size, (x + 1) * tile_size]
            if left.any() or right.any():
                total += 1
                matches += int(left.any() == right.any())
    for y in range(rows - 1):
        for x in range(columns):
            top = mask[(y + 1) * tile_size - 1, x * tile_size:(x + 1) * tile_size]
            bottom = mask[(y + 1) * tile_size, x * tile_size:(x + 1) * tile_size]
            if top.any() or bottom.any():
                total += 1
                matches += int(top.any() == bottom.any())
    return matches / total if total else 1.0


def _overall_score(metrics: dict[str, Any]) -> float:
    return _clamp(
        0.22 * float(metrics["global_palette_harmony_score"])
        + 0.22 * float(metrics["material_identity_score"])
        + 0.22 * float(metrics["map_readability_score"])
        + 0.18 * float(metrics["palette_efficiency_score"])
        + 0.16 * float(metrics["semantic_readability_score"])
    )


def _comparison_board(root: Path, records: list[dict[str, Any]]) -> Image.Image:
    width, height = 640, 640
    columns = 2
    rows = (len(records) + columns - 1) // columns
    board = Image.new("RGBA", (width * columns, (height + 28) * rows), (28, 28, 28, 255))
    draw = ImageDraw.Draw(board)
    for index, record in enumerate(records):
        image = Image.open(Path(record["full_map_path"])).convert("RGBA")
        x = (index % columns) * width
        y = (index // columns) * (height + 28)
        draw.text((x + 8, y + 5), record["condition"]["condition_id"], fill=(255, 255, 255, 255))
        board.paste(image, (x, y + 28))
    return board


def _tile_box(x: int, y: int, tile_size: int) -> tuple[int, int, int, int]:
    return x * tile_size, y * tile_size, (x + 1) * tile_size, (y + 1) * tile_size


def _resolve_path(base: Path, value: Any) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else base / path


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()
