"""Palette budget and semantic ramp qualification study."""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
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
from pixel_tile_compiler.material_library.probe import _variant
from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler
from pixel_tile_compiler.pixel_grammar.metrics import compute_map_metrics, compute_tile_metrics
from pixel_tile_compiler.pixelizer.palette import quantize_palette

PaletteBudget = int | Literal["true_color"]
PaletteMode = Literal["global_free", "per_material", "semantic_ramp"]

_MATERIAL_ROLES: dict[str, tuple[str, ...]] = {
    "grass": ("deep_shadow", "shadow", "base", "light", "highlight"),
    "dirt": ("deep_soil", "dark_soil", "base_earth", "dry_light", "highlight"),
    "water": ("deep", "shadow", "base", "surface_light", "specular_hint"),
    "stone": ("deep_shadow", "side_plane", "base_plane", "light_plane", "highlight"),
}


@dataclass(frozen=True)
class ColorRamp:
    material_id: str
    roles: tuple[str, ...]
    colors: tuple[tuple[int, int, int], ...]
    monotonic_value: bool = True

    def __post_init__(self) -> None:
        if not self.colors or len(self.colors) != len(self.roles):
            raise ValueError("ColorRamp requires one role for every color")
        if any(len(color) != 3 or any(not 0 <= channel <= 255 for channel in color) for color in self.colors):
            raise ValueError("ColorRamp colors must contain RGB triples")

    def as_dict(self) -> dict[str, Any]:
        return {
            "material_id": self.material_id,
            "roles": list(self.roles),
            "colors": [list(color) for color in self.colors],
            "monotonic_value": self.monotonic_value,
        }


@dataclass
class PaletteBudgetStudyConfig:
    output_root: Path = field(default_factory=lambda: Path("e2e/palette_budget_study"))
    source_study_root: Path = field(default_factory=lambda: Path("e2e/real_t2i_material_qualification"))
    materials: tuple[str, ...] = ("grass", "dirt", "water", "stone")
    source_selection: dict[str, str] = field(
        default_factory=lambda: {
            "grass": "grass_real_05",
            "dirt": "dirt_real_02",
            "water": "water_real_01",
            "stone": "stone_real_05",
        }
    )
    palette_budgets: tuple[PaletteBudget, ...] = ("true_color", 64, 32, 24, 16, 12, 8)
    palette_modes: tuple[PaletteMode, ...] = ("global_free", "per_material", "semantic_ramp")
    mode_budgets: dict[str, tuple[PaletteBudget, ...]] = field(default_factory=dict)
    variants: int = 8
    map_columns: int = 10
    map_rows: int = 10
    map_conditions: tuple[tuple[str, PaletteBudget], ...] = (("global_free", 24), ("semantic_ramp", 16))
    semantic_conditions: tuple[tuple[str, PaletteBudget], ...] = (("global_free", 24),)
    semantic_materials: tuple[str, ...] = ("dirt", "water", "stone")
    freeze_source: bool = True
    dithering: Literal["off"] = "off"
    seed: int = 42
    config_path: Path | None = None

    def __post_init__(self) -> None:
        self.output_root = Path(self.output_root)
        self.source_study_root = Path(self.source_study_root)
        if not self.freeze_source:
            raise ValueError("palette study requires freeze_source=true")
        if not self.materials:
            raise ValueError("at least one material is required")
        if set(self.source_selection) != set(self.materials):
            raise ValueError("source_selection must cover every material")
        allowed_modes = {"global_free", "per_material", "semantic_ramp"}
        if not set(self.palette_modes) <= allowed_modes:
            raise ValueError("unknown palette mode")
        if not self.palette_budgets:
            raise ValueError("at least one palette budget is required")
        for budget in self.palette_budgets:
            if budget != "true_color" and not 4 <= int(budget) <= 64:
                raise ValueError("palette budgets must be true_color or between 4 and 64")
        if not self.mode_budgets:
            self.mode_budgets = {
                "global_free": self.palette_budgets,
                "per_material": tuple(value for value in self.palette_budgets if value != "true_color" and int(value) in {24, 16, 12}),
                "semantic_ramp": tuple(value for value in self.palette_budgets if value != "true_color" and int(value) in {32, 24, 16, 12, 8}),
            }
        for mode in self.palette_modes:
            if mode not in self.mode_budgets:
                raise ValueError(f"mode_budgets is missing {mode}")
            if not self.mode_budgets[mode]:
                raise ValueError(f"mode_budgets[{mode}] must not be empty")
        if self.variants < 1:
            raise ValueError("variants must be positive")
        if self.map_columns < 1 or self.map_rows < 1:
            raise ValueError("map dimensions must be positive")
        if self.seed < 0:
            raise ValueError("seed must be non-negative")

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any], base_dir: Path | None = None) -> "PaletteBudgetStudyConfig":
        root = Path(base_dir or ".")
        study = dict(mapping.get("study") or {})
        source = dict(mapping.get("source") or {})
        compile_config = dict(mapping.get("compile") or {})
        map_probe = dict(mapping.get("map_probe") or {})
        semantic_probe = dict(mapping.get("semantic_probe") or {})
        raw_budgets = study.get("palette_budgets", ["true_color", 64, 32, 24, 16, 12, 8])
        budgets: tuple[PaletteBudget, ...] = tuple(
            value if value == "true_color" else int(value) for value in raw_budgets
        )
        raw_modes = tuple(str(value) for value in study.get("palette_modes", cls.palette_modes))
        raw_mode_budgets = dict(study.get("mode_budgets") or {})
        mode_budgets = {
            str(mode): tuple(value if value == "true_color" else int(value) for value in values)
            for mode, values in raw_mode_budgets.items()
        }
        source_selection = {str(key): str(value) for key, value in dict(source.get("selections") or {}).items()}
        map_conditions = _parse_conditions(map_probe.get("conditions", [{"mode": "global_free", "budget": 24}]))
        semantic_conditions = _parse_conditions(
            semantic_probe.get("conditions", [{"mode": "global_free", "budget": 24}])
        )
        return cls(
            output_root=_resolve_path(root, mapping.get("output", "e2e/palette_budget_study")),
            source_study_root=_resolve_path(root, source.get("real_study_root", "e2e/real_t2i_material_qualification")),
            materials=tuple(str(value) for value in study.get("materials", cls.materials)),
            source_selection=source_selection or dict(cls().source_selection),
            palette_budgets=budgets,
            palette_modes=raw_modes,  # type: ignore[arg-type]
            mode_budgets=mode_budgets,
            variants=int(compile_config.get("variants", 8)),
            map_columns=int(map_probe.get("width", 10)),
            map_rows=int(map_probe.get("height", 10)),
            map_conditions=map_conditions,
            semantic_conditions=semantic_conditions,
            semantic_materials=tuple(str(value) for value in semantic_probe.get("materials", cls.semantic_materials)),
            freeze_source=bool(source.get("freeze_source", True)),
            seed=int(mapping.get("seed", 42)),
            config_path=_resolve_path(root, mapping["config_path"]) if mapping.get("config_path") else None,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "study": {
                "materials": list(self.materials),
                "palette_budgets": list(self.palette_budgets),
                "palette_modes": list(self.palette_modes),
                "mode_budgets": {mode: list(values) for mode, values in self.mode_budgets.items()},
            },
            "source": {
                "real_study_root": str(self.source_study_root),
                "selections": dict(self.source_selection),
                "freeze_source": self.freeze_source,
            },
            "compile": {"variants": self.variants, "dithering": self.dithering},
            "map_probe": {
                "width": self.map_columns,
                "height": self.map_rows,
                "conditions": [{"mode": mode, "budget": budget} for mode, budget in self.map_conditions],
            },
            "semantic_probe": {
                "materials": list(self.semantic_materials),
                "conditions": [{"mode": mode, "budget": budget} for mode, budget in self.semantic_conditions],
            },
            "seed": self.seed,
            "output": str(self.output_root),
        }


def load_palette_budget_config(path: Path) -> PaletteBudgetStudyConfig:
    path = Path(path)
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        import json

        mapping = json.loads(raw)
    else:
        import yaml  # type: ignore[import-not-found]

        mapping = yaml.safe_load(raw)
    if not isinstance(mapping, dict):
        raise ValueError("palette budget config must contain a mapping")
    mapping["config_path"] = str(path)
    return PaletteBudgetStudyConfig.from_mapping(mapping, path.parent)


def build_semantic_ramp(image: Image.Image, budget: int, material_id: str) -> ColorRamp:
    if not 4 <= budget <= 64:
        raise ValueError("semantic ramp budget must be between 4 and 64")
    quantized = quantize_palette(image.convert("RGB"), budget=budget)
    colors = list({tuple(int(channel) for channel in pixel) for pixel in quantized.convert("RGB").getdata()})
    lab = _colors_to_lab(colors)
    colors = [colors[index] for index in np.argsort(lab[:, 0])]
    roles = _ramp_roles(material_id, len(colors))
    return ColorRamp(material_id, roles, tuple(colors), monotonic_value=True)


def compute_palette_study_metrics(
    image: Image.Image,
    requested_budget: PaletteBudget,
    mode: PaletteMode,
    source_image: Image.Image | None = None,
    ramp: ColorRamp | None = None,
) -> dict[str, Any]:
    rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    colors = sorted({tuple(int(channel) for channel in pixel) for pixel in rgb.reshape(-1, 3)})
    labs = _colors_to_lab(colors)
    actual_count = len(colors)
    effective_count = _effective_palette_size(labs)
    requested_count = None if requested_budget == "true_color" else int(requested_budget)
    pair_count = len(colors) * (len(colors) - 1) // 2
    redundant_pairs = sum(1 for index in range(len(labs)) for other in range(index + 1, len(labs)) if np.linalg.norm(labs[index] - labs[other]) <= 8.0)
    sorted_l = np.sort(labs[:, 0]) if len(labs) else np.empty(0)
    separation = float(np.median(np.diff(sorted_l)) / 18.0) if len(sorted_l) > 1 else 0.0
    coherence, isolated_ratio = _cluster_metrics(rgb)
    fragmentation = 1.0 - coherence
    source_identity = _material_identity_score(rgb, source_image)
    ramp_score = _ramp_coherence(ramp)
    semantic_readability = _clamp(0.52 * coherence + 0.28 * min(1.0, float(rgb.std()) / 64.0) + 0.20 * source_identity)
    fitness = _clamp(
        0.24 * coherence
        + 0.16 * _clamp(separation)
        + 0.18 * semantic_readability
        + 0.16 * source_identity
        + 0.12 * (ramp_score if ramp_score is not None else coherence)
        - 0.08 * fragmentation
        - 0.04 * (redundant_pairs / pair_count if pair_count else 0.0)
        - 0.02 * isolated_ratio,
    )
    return {
        "requested_palette_budget": requested_budget,
        "palette_mode": mode,
        "palette_size": actual_count,
        "effective_palette_size": effective_count,
        "palette_utilization_ratio": None if requested_count is None else round(min(1.0, actual_count / requested_count), 6),
        "value_separation_score": round(_clamp(separation), 6),
        "redundant_color_ratio": round(redundant_pairs / pair_count if pair_count else 0.0, 6),
        "ramp_coherence_score": None if ramp_score is None else round(ramp_score, 6),
        "cluster_coherence_score": round(coherence, 6),
        "cluster_fragmentation_score": round(fragmentation, 6),
        "isolated_pixel_ratio": round(isolated_ratio, 6),
        "semantic_readability_score": round(semantic_readability, 6),
        "material_identity_score": round(source_identity, 6),
        "pixel_art_fitness_score": round(fitness, 6),
    }


def _ramp_roles(material_id: str, count: int) -> tuple[str, ...]:
    base = _MATERIAL_ROLES.get(material_id, ("deep_shadow", "shadow", "base", "light", "highlight"))
    if count <= len(base):
        return tuple(base[:count])
    return tuple(base) + tuple(f"shade_{index:02d}" for index in range(count - len(base)))


def _colors_to_lab(colors: list[tuple[int, int, int]]) -> np.ndarray:
    if not colors:
        return np.empty((0, 3), dtype=np.float32)
    values = np.asarray(colors, dtype=np.float32).reshape(-1, 1, 3) / 255.0
    return rgb2lab(values).reshape(-1, 3)


def _effective_palette_size(labs: np.ndarray, threshold: float = 10.0) -> int:
    if not len(labs):
        return 0
    selected: list[np.ndarray] = []
    for color in labs[np.argsort(labs[:, 0])]:
        if not selected or min(float(np.linalg.norm(color - other)) for other in selected) > threshold:
            selected.append(color)
    return len(selected)


def _cluster_metrics(rgb: np.ndarray) -> tuple[float, float]:
    height, width = rgb.shape[:2]
    total = height * width
    largest_sum = 0
    isolated = 0
    for color in np.unique(rgb.reshape(-1, 3), axis=0):
        mask = np.all(rgb == color, axis=2)
        visited = np.zeros((height, width), dtype=bool)
        largest = 0
        for y, x in zip(*np.where(mask)):
            if visited[y, x]:
                continue
            queue = deque([(int(x), int(y))])
            visited[y, x] = True
            size = 0
            while queue:
                cx, cy = queue.popleft()
                size += 1
                for nx, ny in _neighbors(cx, cy, width, height):
                    if not visited[ny, nx] and mask[ny, nx]:
                        visited[ny, nx] = True
                        queue.append((nx, ny))
            largest = max(largest, size)
            if size == 1:
                isolated += 1
        largest_sum += largest
    coherence = largest_sum / total if total else 0.0
    return _clamp(coherence), isolated / total if total else 0.0


def _material_identity_score(rgb: np.ndarray, source_image: Image.Image | None) -> float:
    if source_image is None:
        return 0.5
    source = np.asarray(source_image.convert("RGB").resize((rgb.shape[1], rgb.shape[0])), dtype=np.float32) / 255.0
    output = rgb.astype(np.float32) / 255.0
    source_lab = rgb2lab(source).mean(axis=(0, 1))
    output_lab = rgb2lab(output).mean(axis=(0, 1))
    return _clamp(1.0 - float(np.linalg.norm(source_lab - output_lab)) / 100.0)


def _ramp_coherence(ramp: ColorRamp | None) -> float | None:
    if ramp is None or len(ramp.colors) < 2:
        return None
    lab = _colors_to_lab(list(ramp.colors))
    values = lab[:, 0]
    monotonic = float(np.all(np.diff(values) >= -1e-6))
    adjacent = np.diff(values)
    separation = float(np.mean(np.clip(adjacent / 18.0, 0.0, 1.0))) if len(adjacent) else 0.0
    return _clamp(0.65 * monotonic + 0.35 * separation)


def _neighbors(x: int, y: int, width: int, height: int):
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            nx, ny = x + dx, y + dy
            if 0 <= nx < width and 0 <= ny < height:
                yield nx, ny


def _luminance(color: tuple[int, int, int]) -> float:
    return 0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2]


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _parse_conditions(values: Any) -> tuple[tuple[str, PaletteBudget], ...]:
    conditions: list[tuple[str, PaletteBudget]] = []
    for value in values:
        if not isinstance(value, dict):
            raise ValueError("palette probe conditions must be mappings")
        budget: PaletteBudget = value["budget"] if value["budget"] == "true_color" else int(value["budget"])
        conditions.append((str(value["mode"]), budget))
    return tuple(conditions)


def _resolve_path(base: Path, value: Any) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else base / path


@dataclass(frozen=True)
class PaletteBudgetStudyResult:
    output_root: Path
    status: str
    conditions: tuple[dict[str, Any], ...] = ()


class PaletteBudgetStudyRunner:
    """Run a source-frozen, single-tile-first palette study."""

    def run(self, config: PaletteBudgetStudyConfig) -> PaletteBudgetStudyResult:
        root = Path(config.output_root)
        root.mkdir(parents=True, exist_ok=True)
        save_json(config.as_dict(), root / "study_config.json")
        source_manifest = self._freeze_sources(config, root)
        save_json(source_manifest, root / "source_manifest.json")
        conditions: list[dict[str, Any]] = []
        for material in config.materials:
            source_path = Path(source_manifest[material]["frozen_source_path"])
            with Image.open(source_path) as opened:
                source = opened.convert("RGB")
            for mode in config.palette_modes:
                for budget in config.mode_budgets[mode]:
                    if mode == "semantic_ramp" and budget == "true_color":
                        continue
                    conditions.append(
                        self._run_condition(
                            config,
                            root,
                            material,
                            source,
                            source_manifest[material],
                            mode,
                            budget,
                        )
                    )
        semantic_results = self._run_semantic_probes(config, root, source_manifest)
        save_json(semantic_results, root / "semantic_manifest.json")
        self._write_summary(config, root, conditions)
        save_json(conditions, root / "manifest.json")
        return PaletteBudgetStudyResult(root, "completed", tuple(conditions))

    def _run_semantic_probes(
        self,
        config: PaletteBudgetStudyConfig,
        root: Path,
        source_manifest: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for mode, budget in config.semantic_conditions:
            for material in config.semantic_materials:
                if material not in source_manifest:
                    continue
                record: dict[str, Any] = {
                    "material_id": material,
                    "mode": mode,
                    "requested_budget": budget,
                    "source_id": source_manifest[material]["source_id"],
                }
                if mode == "semantic_ramp" or budget == "true_color":
                    record["status"] = "deferred_unsupported_semantic_palette"
                    results.append(record)
                    continue
                output_root = root / material / mode / f"budget_{budget}" / "semantic_probe"
                if output_root.exists():
                    shutil.rmtree(output_root)
                output_root.mkdir(parents=True, exist_ok=True)
                source_path = Path(source_manifest[material]["frozen_source_path"])
                if material == "dirt":
                    from pixel_tile_compiler.transition_network.road_graph import RoadGraphStudyRunner, load_road_graph_config

                    study_config = load_road_graph_config(_repo_root() / "experiments" / "road_graph_study.yaml")
                    study_config.output_root = output_root
                    study_config.palette_budget = int(budget)
                    study_config.sources["dirt_road"] = source_path
                    result = RoadGraphStudyRunner().run(study_config)
                    record.update(
                        {
                            "status": "completed",
                            "comparison_path": str(result.comparison_path),
                            "metrics_path": str(result.metrics_path),
                        }
                    )
                elif material == "water":
                    from pixel_tile_compiler.transition_network.river_study import RiverGraphStudyRunner, load_river_graph_config

                    study_config = load_river_graph_config(_repo_root() / "experiments" / "river_network_study.yaml")
                    study_config.output_root = output_root
                    study_config.palette_budget = int(budget)
                    study_config.sources["water"] = source_path
                    result = RiverGraphStudyRunner().run(study_config)
                    record.update(
                        {
                            "status": "completed",
                            "comparison_path": str(result.comparison_path),
                            "metrics_path": str(result.metrics_path),
                        }
                    )
                elif material == "stone":
                    with Image.open(source_path) as opened:
                        source = opened.convert("RGB")
                    result = PixelTileCompiler().compile_image(
                        source,
                        CompilerConfig(
                            output_root=output_root,
                            palette_budget=int(budget),
                            tile_mode="object",
                            semantic_provider="rule",
                            seam_mode="off",
                            repeat_opt_enabled=False,
                            dither="off",
                            background_mode="color",
                            background_color="#000000",
                            work_size=128,
                            smoothing_enabled=False,
                            seed=config.seed,
                            debug_enabled=False,
                        ),
                        source_name=f"palette-study:{material}:{mode}:{budget}:object",
                    )
                    record.update({"status": "completed", "final_path": str(result.final_path)})
                else:
                    record["status"] = "deferred_surface_covered_by_single_tile"
                results.append(record)
        return results

    def _freeze_sources(self, config: PaletteBudgetStudyConfig, root: Path) -> dict[str, dict[str, Any]]:
        frozen_root = root / "frozen_sources"
        frozen_root.mkdir(parents=True, exist_ok=True)
        manifest: dict[str, dict[str, Any]] = {}
        for material in config.materials:
            source_id = config.source_selection[material]
            candidate_root = config.source_study_root / material / "candidates" / source_id
            source_path = candidate_root / "source_normalized.png"
            record_path = candidate_root / "record.json"
            if not source_path.exists() or not record_path.exists():
                raise FileNotFoundError(f"frozen real-t2i source is missing: {material}/{source_id}")
            record = _read_json_object(record_path)
            generation = dict(record.get("generation") or {})
            if generation.get("origin") != "real_t2i":
                raise ValueError(f"source is not real_t2i: {material}/{source_id}")
            normalized_sha = _sha256_file(source_path)
            expected_raw_sha = str(generation.get("source_sha256", ""))
            raw_path = config.source_study_root / "generation" / material / "real_sources" / source_id / "source_raw.png"
            raw_sha = _sha256_file(raw_path) if raw_path.exists() else None
            if expected_raw_sha and raw_sha is not None and expected_raw_sha != raw_sha:
                raise ValueError(f"frozen raw source hash mismatch: {material}/{source_id}")
            if expected_raw_sha and raw_sha is None and expected_raw_sha != normalized_sha:
                raise ValueError(f"frozen source hash mismatch: {material}/{source_id}")
            frozen_path = frozen_root / material / "source_normalized.png"
            frozen_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, frozen_path)
            manifest[material] = {
                "material_id": material,
                "source_id": source_id,
                "origin": "real_t2i",
                "source_sha256": normalized_sha,
                "normalized_source_sha256": normalized_sha,
                "raw_source_sha256": raw_sha or expected_raw_sha or None,
                "source_path": str(source_path),
                "frozen_source_path": str(frozen_path),
            }
        return manifest

    def _run_condition(
        self,
        config: PaletteBudgetStudyConfig,
        root: Path,
        material: str,
        source: Image.Image,
        source_record: dict[str, Any],
        mode: PaletteMode,
        budget: PaletteBudget,
    ) -> dict[str, Any]:
        budget_label = str(budget)
        condition_root = root / material / mode / f"budget_{budget_label}"
        if condition_root.exists():
            shutil.rmtree(condition_root)
        condition_root.mkdir(parents=True, exist_ok=True)
        ramp = build_semantic_ramp(source, int(budget), material) if mode == "semantic_ramp" else None
        palette_colors = ramp.colors if ramp is not None else None
        pixel_root = condition_root / "pixel_tiles"
        compiler_root = condition_root / "_compiler"
        pixel_root.mkdir(parents=True, exist_ok=True)
        compiler = PixelTileCompiler()
        variant_records: list[dict[str, Any]] = []
        pixel_paths: list[Path] = []
        for variant in range(config.variants):
            variant_image = _variant(source, variant, config.seed)
            variant_root = compiler_root / f"variant_{variant + 1:02d}"
            compiler_config = {
                "output_root": variant_root,
                "palette_budget": 64 if budget == "true_color" else int(budget),
                "palette_colors": palette_colors,
                "quantize_enabled": budget != "true_color",
                "tile_mode": "repeatable",
                "semantic_provider": "rule",
                "seam_mode": "off",
                "repeat_opt_enabled": False,
                "dither": "off",
                "background_mode": "color",
                "background_color": "#000000",
                "work_size": 128,
                "smoothing_enabled": False,
                "seed": config.seed + variant,
                "debug_enabled": False,
            }
            result = compiler.compile_image(
                variant_image,
                CompilerConfig(**compiler_config),
                source_name=f"palette-study:{material}:{mode}:{budget_label}:v{variant + 1:02d}",
            )
            pixel_path = pixel_root / f"pixel_v{variant + 1:02d}.png"
            with Image.open(result.final_path) as final_opened:
                final = final_opened.convert("RGBA")
                save_png(final, pixel_path)
                tile_metrics = compute_tile_metrics(final, target="64x64", semantic_role="surface")
                palette_metrics = compute_palette_study_metrics(
                    final,
                    requested_budget=budget,
                    mode=mode,
                    source_image=source,
                    ramp=ramp,
                )
            variant_records.append(
                {
                    "variant": variant + 1,
                    "compiler_metrics": asdict(result.metrics),
                    "tile_metrics": tile_metrics,
                    "palette_metrics": palette_metrics,
                    "pixel_path": str(pixel_path),
                }
            )
            pixel_paths.append(pixel_path)
        map_enabled = (mode, budget) in config.map_conditions
        if map_enabled:
            map_path, map_metrics = self._write_map_probe(pixel_paths, condition_root, config)
        else:
            map_path, map_metrics = None, {}
        summary = self._summarize_condition(
            material,
            mode,
            budget,
            source_record,
            ramp,
            variant_records,
            map_path,
            map_metrics,
        )
        save_json(summary, condition_root / "metrics.json")
        save_json(
            {
                "material_id": material,
                "mode": mode,
                "requested_budget": budget,
                "source": source_record,
                "ramp": ramp.as_dict() if ramp else None,
                "metrics_path": str(condition_root / "metrics.json"),
            },
            condition_root / "record.json",
        )
        return {
            "material_id": material,
            "mode": mode,
            "requested_budget": budget,
            "condition_id": f"{material}:{mode}:{budget_label}",
            "source_id": source_record["source_id"],
            "source_sha256": source_record["source_sha256"],
            "ramp": ramp.as_dict() if ramp else None,
            "metrics": summary,
            "metrics_path": str(condition_root / "metrics.json"),
            "map_path": str(map_path) if map_path else None,
        }

    def _write_map_probe(
        self,
        pixel_paths: list[Path],
        condition_root: Path,
        config: PaletteBudgetStudyConfig,
    ) -> tuple[Path, dict[str, Any]]:
        if not pixel_paths:
            raise ValueError("at least one pixel tile is required for map probe")
        map_image = Image.new("RGBA", (config.map_columns * 64, config.map_rows * 64), (0, 0, 0, 255))
        for y in range(config.map_rows):
            for x in range(config.map_columns):
                index = (x * 7 + y * 11 + config.seed) % len(pixel_paths)
                with Image.open(pixel_paths[index]) as tile:
                    map_image.paste(tile.convert("RGBA"), (x * 64, y * 64))
        map_path = save_png(map_image, condition_root / "map_probe.png")
        return map_path, compute_map_metrics(map_image, tile_size=64)

    def _summarize_condition(
        self,
        material: str,
        mode: PaletteMode,
        budget: PaletteBudget,
        source_record: dict[str, Any],
        ramp: ColorRamp | None,
        variants: list[dict[str, Any]],
        map_path: Path | None,
        map_metrics: dict[str, Any],
    ) -> dict[str, Any]:
        palette_metrics = [item["palette_metrics"] for item in variants]
        tile_metrics = [item["tile_metrics"] for item in variants]
        numeric_keys = (
            "palette_size",
            "effective_palette_size",
            "value_separation_score",
            "redundant_color_ratio",
            "cluster_coherence_score",
            "cluster_fragmentation_score",
            "isolated_pixel_ratio",
            "semantic_readability_score",
            "material_identity_score",
            "pixel_art_fitness_score",
        )
        averages = {
            key: round(float(np.mean([float(item[key]) for item in palette_metrics])), 6)
            for key in numeric_keys
        }
        averages["ramp_coherence_score"] = (
            None
            if not ramp
            else round(float(np.mean([float(item["ramp_coherence_score"]) for item in palette_metrics])), 6)
        )
        averages.update(
            {
                "compile_readability_score": round(float(np.mean([float(item["readability_score"]) for item in tile_metrics])), 6),
                "semantic_fitness_score": round(float(np.mean([float(item["semantic_fitness_score"]) for item in tile_metrics])), 6),
                "map_readability_score": None if not map_metrics else float(map_metrics["map_readability_score"]),
                "map_clutter_score": None if not map_metrics else float(map_metrics["grid_visibility_score"]),
                "map_edge_discontinuity_score": None if not map_metrics else float(map_metrics["map_edge_discontinuity_score"]),
            }
        )
        return {
            "material_id": material,
            "palette_mode": mode,
            "requested_palette_budget": budget,
            "source_id": source_record["source_id"],
            "source_sha256": source_record["source_sha256"],
            "ramp": ramp.as_dict() if ramp else None,
            "variants": len(variants),
            "variant_metrics": variants,
            "map_metrics": map_metrics,
            "map_path": str(map_path) if map_path else None,
            **averages,
        }

    def _write_summary(self, config: PaletteBudgetStudyConfig, root: Path, conditions: list[dict[str, Any]]) -> None:
        summary_root = root / "summary"
        summary_root.mkdir(parents=True, exist_ok=True)
        rankings: dict[str, list[dict[str, Any]]] = {}
        sweet_spots: dict[str, Any] = {}
        palette_effect: dict[str, Any] = {}
        profiles: dict[str, Any] = {}
        for material in config.materials:
            family = [item for item in conditions if item["material_id"] == material]
            ranked = sorted(family, key=lambda item: float(item["metrics"]["pixel_art_fitness_score"]), reverse=True)
            rankings[material] = ranked
            if ranked:
                best = ranked[0]
                best_score = float(best["metrics"]["pixel_art_fitness_score"])
                near = [
                    item
                    for item in ranked
                    if item["mode"] == best["mode"]
                    and best_score - float(item["metrics"]["pixel_art_fitness_score"]) <= 0.02
                ]
                numeric_budgets = [int(item["requested_budget"]) for item in near if item["requested_budget"] != "true_color"]
                sweet_spots[material] = {
                    "best_condition": best["condition_id"],
                    "best_score": best_score,
                    "near_best_conditions": [item["condition_id"] for item in near],
                    "recommended_range": [min(numeric_budgets), max(numeric_budgets)] if numeric_budgets else None,
                }
                profiles[material] = {
                    "material_id": material,
                    "mode": best["mode"],
                    "budget": best["requested_budget"],
                    "recommended_range": sweet_spots[material]["recommended_range"],
                    "source_id": best["source_id"],
                    "provisional": True,
                }
            palette_effect[material] = self._effect_ratio(family)
        save_json(rankings, summary_root / "condition_ranking.json")
        save_json(sweet_spots, summary_root / "material_sweet_spots.json")
        save_json(palette_effect, summary_root / "palette_effect.json")
        save_json(profiles, summary_root / "recommended_palette_profiles.json")
        self._write_comparison_board(root, conditions)
        self._write_blind_review(root, conditions, config.seed)
        best_lines = [
            f"- {material}: `{sweet_spots[material]['best_condition']}` (fitness {sweet_spots[material]['best_score']:.6f})"
            for material in config.materials
            if material in sweet_spots
        ]
        (summary_root / "report.md").write_text(
            "# Palette Budget & Ramp Structure Study\n\n"
            f"- Materials: {', '.join(config.materials)}\n"
            f"- Conditions: {len(conditions)}\n"
            f"- Variants per condition: {config.variants}\n"
            f"- Representative Map Probe conditions: {len(config.map_conditions)}\n"
            f"- Semantic renderer probe conditions: {len(config.semantic_conditions)}\n"
            "- Source is frozen to Real t2i candidates by source ID and SHA-256.\n"
            "- This is the first single-tile comparison pass with representative semantic probes; Semantic Ramp renderer injection remains deferred.\n"
            "- Automatic profiles are provisional and are not written to Material Library.\n",
            encoding="utf-8",
        )
        with (summary_root / "report.md").open("a", encoding="utf-8") as handle:
            handle.write("\n## Provisional best conditions\n\n" + "\n".join(best_lines) + "\n")

    def _effect_ratio(self, family: list[dict[str, Any]]) -> dict[str, Any]:
        by_mode: dict[str, Any] = {}
        for mode in sorted({str(item["mode"]) for item in family}):
            rows = [item for item in family if item["mode"] == mode]
            means = np.asarray([float(item["metrics"]["pixel_art_fitness_score"]) for item in rows], dtype=float)
            within = np.asarray(
                [
                    np.var([float(variant["palette_metrics"]["pixel_art_fitness_score"]) for variant in item["metrics"]["variant_metrics"]])
                    for item in rows
                ],
                dtype=float,
            )
            between = float(np.var(means)) if len(means) else 0.0
            within_mean = float(np.mean(within)) if len(within) else 0.0
            by_mode[mode] = {
                "condition_count": len(rows),
                "between_palette_condition_variance": round(between, 9),
                "within_condition_variant_variance": round(within_mean, 9),
                "palette_effect_ratio": round(between / (between + within_mean), 6) if between + within_mean else 0.0,
                "interpretation": "exploratory; source is fixed and this ratio is not directly comparable to Source Effect Ratio",
            }
        return by_mode

    def _write_comparison_board(self, root: Path, conditions: list[dict[str, Any]]) -> None:
        materials = sorted({str(item["material_id"]) for item in conditions})
        columns = list(dict.fromkeys(item["mode"] + ":" + str(item["requested_budget"]) for item in conditions))
        lookup = {(item["material_id"], item["mode"] + ":" + str(item["requested_budget"])): item for item in conditions}
        cell_width, cell_height = 120, 110
        board = Image.new("RGB", (max(1, len(columns)) * cell_width, len(materials) * cell_height), (24, 24, 28))
        draw = ImageDraw.Draw(board)
        for row, material in enumerate(materials):
            for col, column in enumerate(columns):
                item = lookup.get((material, column))
                if not item:
                    continue
                variant = item["metrics"]["variant_metrics"][0]["pixel_path"]
                with Image.open(variant) as image:
                    preview = image.convert("RGB").resize((cell_width, cell_height - 20), Image.Resampling.NEAREST)
                board.paste(preview, (col * cell_width, row * cell_height))
                draw.text((col * cell_width + 3, row * cell_height + cell_height - 18), f"{material} {column}", fill=(255, 255, 255))
        save_png(board, root / "summary" / "comparison_board.png")

    def _write_blind_review(self, root: Path, conditions: list[dict[str, Any]], seed: int) -> None:
        review_root = root / "blind_review"
        if review_root.exists():
            shutil.rmtree(review_root)
        assets_root = review_root / "assets"
        assets_root.mkdir(parents=True, exist_ok=True)
        rng = random.Random(seed)
        shuffled = [item for item in conditions if item.get("map_path")]
        rng.shuffle(shuffled)
        mapping: dict[str, dict[str, Any]] = {}
        manifest: list[dict[str, Any]] = []
        rows: list[dict[str, str]] = []
        for index, item in enumerate(shuffled, start=1):
            review_id = f"P-{index:03d}"
            source = Path(root / "frozen_sources" / item["material_id"] / "source_normalized.png")
            tile = Path(item["metrics"]["variant_metrics"][0]["pixel_path"])
            map_path = Path(item["map_path"])
            assets = {}
            for key, source_path in (("source", source), ("tile", tile), ("map", map_path)):
                target = assets_root / f"{review_id}_{key}.png"
                shutil.copy2(source_path, target)
                assets[key] = str(target.relative_to(review_root))
            mapping[review_id] = {
                "material_id": item["material_id"],
                "condition_id": item["condition_id"],
            }
            manifest.append({"review_id": review_id, **assets})
            rows.append(
                {
                    "review_id": review_id,
                    "pixel_art_likeness": "",
                    "material_readability": "",
                    "map_quality": "",
                    "preferred": "",
                    "notes": "",
                }
            )
        save_json(manifest, review_root / "manifest.json")
        save_json(mapping, review_root / "mapping.json")
        import csv

        with (review_root / "review.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["review_id"])
            writer.writeheader()
            writer.writerows(rows)
        self._write_review_board(review_root, manifest)

    def _write_review_board(self, review_root: Path, manifest: list[dict[str, Any]]) -> None:
        width, height = 160, 110
        board = Image.new("RGB", (width * 3, max(1, height * len(manifest))), (24, 24, 28))
        draw = ImageDraw.Draw(board)
        for row, item in enumerate(manifest):
            for col, key in enumerate(("source", "tile", "map")):
                with Image.open(review_root / item[key]) as image:
                    preview = image.convert("RGB")
                    preview.thumbnail((width, height - 18), Image.Resampling.NEAREST)
                    board.paste(preview, (col * width, row * height))
            draw.text((3, row * height + height - 15), item["review_id"], fill=(255, 255, 255))
        save_png(board, review_root / "board.png")


def _read_json_object(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def import_palette_review(study_root: Path, csv_path: Path) -> dict[str, Any]:
    """Import blind palette ratings without promoting a palette profile."""
    import csv

    study_root = Path(study_root)
    review_root = study_root / "blind_review"
    mapping = _read_json_object(review_root / "mapping.json")
    reviews: list[dict[str, Any]] = []
    imported = 0
    with Path(csv_path).open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            review_id = str(row.get("review_id", ""))
            target = mapping.get(review_id)
            if not target:
                raise ValueError(f"unknown palette review id: {review_id}")
            review = {
                "pixel_art_likeness": _review_rating(row.get("pixel_art_likeness"), "pixel_art_likeness"),
                "material_readability": _review_rating(row.get("material_readability"), "material_readability"),
                "map_quality": _review_rating(row.get("map_quality"), "map_quality"),
                "preferred": _review_preferred(row.get("preferred")),
                "notes": row.get("notes") or None,
            }
            condition_id = str(target["condition_id"])
            material, mode, budget = condition_id.split(":", 2)
            condition_root = study_root / material / mode / f"budget_{budget}"
            record_path = condition_root / "record.json"
            if not record_path.exists():
                raise ValueError(f"palette condition not found: {condition_id}")
            record = _read_json_object(record_path)
            record["human_review"] = {"review_id": review_id, **review}
            save_json(record, record_path)
            reviews.append({"review_id": review_id, "condition_id": condition_id, **review})
            if any(review[key] is not None for key in ("pixel_art_likeness", "material_readability", "map_quality")):
                imported += 1
    result = {
        "human_reviews_imported": imported,
        "reviews": reviews,
        "profile_promotion": "not_performed",
    }
    save_json(result, review_root / "review_import.json")
    return result


def _review_rating(value: str | None, field_name: str) -> int | None:
    if value is None or not value.strip():
        return None
    rating = int(value)
    if not 1 <= rating <= 5:
        raise ValueError(f"{field_name} must be between 1 and 5")
    return rating


def _review_preferred(value: str | None) -> bool | None:
    if value is None or not value.strip():
        return None
    normalized = value.strip().lower()
    if normalized in {"yes", "y", "true", "1"}:
        return True
    if normalized in {"no", "n", "false", "0"}:
        return False
    raise ValueError("preferred must be yes or no")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]
