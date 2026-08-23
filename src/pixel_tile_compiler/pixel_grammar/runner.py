"""Run the controlled 64x64 Pixel Grammar Study."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw

from pixel_tile_compiler.config import CompilerConfig
from pixel_tile_compiler.io.exporter import save_json, save_png
from pixel_tile_compiler.io.loader import load_image
from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler
from pixel_tile_compiler.transition_network.masks import build_road_mask, build_transition_mask
from pixel_tile_compiler.transition_network.river import RiverRenderSpec, build_river_geometry

from .config import PixelGrammarStudyConfig
from .density import apply_density, compiler_grammar_options
from .metrics import compute_map_metrics, compute_tile_metrics, grammar_score
from .profiles import DensityLevel, PixelGrammarProfile, default_profile


@dataclass(frozen=True)
class PixelGrammarStudyResult:
    output_root: Path
    records: tuple[dict[str, Any], ...]
    recommended_profiles: dict[str, dict[str, Any]]
    comparison_board_path: Path
    metrics_path: Path


class PixelGrammarStudyRunner:
    """Generate all semantic samples under the same density comparison."""

    def run(self, config: PixelGrammarStudyConfig) -> PixelGrammarStudyResult:
        root = Path(config.output_root)
        root.mkdir(parents=True, exist_ok=True)
        self._write_config(config, root)
        shared_palette = self._build_shared_palette(config) if config.shared_palette else None
        if shared_palette is not None:
            save_json({"palette_budget": config.palette_budget, "colors": [list(color) for color in shared_palette]}, root / "configs" / "shared_palette.json")
        records: list[dict[str, Any]] = []
        for target in config.targets:
            for level in config.levels:
                records.append(self._compile_target(config, root, target, level, shared_palette))
        self._write_metrics(root, records)
        recommendations = self._recommend(records)
        self._write_summary(config, root, records, recommendations)
        comparison_board = root / "summary" / "comparison_board.png"
        metrics_path = root / "metrics" / "grammar_metrics.json"
        return PixelGrammarStudyResult(root, tuple(records), recommendations, comparison_board, metrics_path)

    def _compile_target(
        self,
        config: PixelGrammarStudyConfig,
        root: Path,
        target: str,
        level: DensityLevel,
        shared_palette: tuple[tuple[int, int, int], ...] | None = None,
    ) -> dict[str, Any]:
        role, material = self._target_identity(config, target)
        profile = self._profile(config, target, role, material, level)
        output = root / "tiles" / target / level.value
        output.mkdir(parents=True, exist_ok=True)
        composite, semantic_mask, boundary_mask, silhouette_mask, render_metadata = self._render_source(
            config, target, level, profile, output
        )
        save_png(composite, output / "source_composite.png")
        if semantic_mask is not None:
            save_png(Image.fromarray(semantic_mask.astype(np.uint8) * 255, mode="L"), output / "semantic_mask.png")
        if boundary_mask is not None:
            save_png(Image.fromarray(boundary_mask.astype(np.uint8) * 255, mode="L"), output / "boundary_mask.png")
        if silhouette_mask is not None:
            save_png(Image.fromarray(silhouette_mask.astype(np.uint8) * 255, mode="L"), output / "silhouette_mask.png")

        final, compiler_metadata = self._pixelize(config, composite, output, target, level, shared_palette)
        tile_metrics = compute_tile_metrics(
            final,
            target=target,
            semantic_role=role,
            mask=semantic_mask,
            boundary_mask=boundary_mask,
            silhouette_mask=silhouette_mask,
        )
        map_metrics: dict[str, Any] = {}
        if config.generate_demo_maps and role in {"surface", "network"}:
            preview = _assemble_repeated_map(final, config.map_width, config.map_height)
            map_path = root / "maps" / f"{target}_{level.value}.png"
            save_png(preview, map_path)
            map_metrics = compute_map_metrics(preview, config.tile_size)
        score = grammar_score(tile_metrics, map_metrics)
        record = {
            "target": target,
            "semantic_role": role,
            "material": material,
            "density_level": level.value,
            "profile": profile.to_dict(),
            "metrics": {"tile": tile_metrics, "map": map_metrics, "grammar_score": score},
            "render": render_metadata,
            "compiler": compiler_metadata,
            "artifacts": {
                "root": str(output),
                "source": str(output / "source_composite.png"),
                "tile": str(output / "tile.png"),
                "map": str(root / "maps" / f"{target}_{level.value}.png") if map_metrics else None,
            },
        }
        save_json(profile.to_dict(), output / "profile.json")
        save_json(record, output / "metrics.json")
        return record

    def _render_source(
        self,
        config: PixelGrammarStudyConfig,
        target: str,
        level: DensityLevel,
        profile: PixelGrammarProfile,
        output: Path,
    ) -> tuple[Image.Image, np.ndarray | None, np.ndarray | None, np.ndarray | None, dict[str, Any]]:
        size = config.source_size
        role, _ = self._target_identity(config, target)
        if role == "surface":
            image = apply_density(self._load_target_source(config, target), level, profile, config.seed)
            return image, None, None, None, {"renderer": "material_exemplar", "source": str(self._source_for_target(config, target))}
        if role == "network":
            return self._render_network(config, target, level, profile)
        if role == "transition":
            first_key, second_key = self._transition_sources(target)
            first = apply_density(self._load_source(config, first_key), level, profile, config.seed)
            second = apply_density(self._load_source(config, second_key), level, profile, config.seed + 1)
            orientation = str(config.target_specs.get(target, {}).get("orientation", "EW"))
            boundary = float(config.target_specs.get(target, {}).get("boundary", 0.5))
            mask = build_transition_mask((size, size), orientation, boundary)
            image = Image.composite(first, second, Image.fromarray(mask.astype(np.uint8) * 255, mode="L"))
            return image, None, mask, None, {
                "renderer": "transition_boundary",
                "materials": [first_key, second_key],
                "orientation": orientation,
                "boundary": boundary,
            }
        image, silhouette = self._render_object(config, target, level, profile)
        return image, None, None, silhouette, {"renderer": "study_procedural_object"}

    def _render_network(
        self,
        config: PixelGrammarStudyConfig,
        target: str,
        level: DensityLevel,
        profile: PixelGrammarProfile,
    ) -> tuple[Image.Image, np.ndarray, None, None, dict[str, Any]]:
        spec = config.target_specs.get(target, {})
        topology = str(spec.get("topology", "EW"))
        width = float(spec.get("width_ratio", 0.26 if target == "river" else 0.28))
        center = float(spec.get("center", 0.5))
        base_key = str(spec.get("base", "forest_canopy" if target == "river" else "grass"))
        base = apply_density(self._load_source(config, base_key), level, profile, config.seed)
        if target == "dirt_road":
            material_key = str(spec.get("material_source", "road"))
            material = apply_density(self._load_source(config, material_key), level, profile, config.seed + 1)
            mask = build_road_mask((config.source_size, config.source_size), topology, width=width, center=center)
            image = Image.composite(material, base, Image.fromarray(mask.astype(np.uint8) * 255, mode="L"))
            renderer = "road_network_adapter"
            metadata = {"renderer": renderer, "topology": topology, "width_ratio": width, "base": base_key}
        else:
            water = apply_density(self._load_source(config, str(spec.get("water_source", "water"))), level, profile, config.seed + 1)
            bank = apply_density(self._load_source(config, str(spec.get("bank_source", "bank"))), level, profile, config.seed + 2)
            render_spec = RiverRenderSpec(
                source_size=config.source_size,
                pixelize=False,
                debug_enabled=False,
                seed=config.seed,
                base_width_ratio=width,
                width_variation=float(spec.get("width_variation", 0.04)),
                bank_width_ratio=float(spec.get("bank_width_ratio", 0.04)),
                center=center,
            )
            incoming = tuple(str(value) for value in spec.get("incoming", ()))
            outgoing = tuple(str(value) for value in spec.get("outgoing", ()))
            geometry = build_river_geometry((config.source_size, config.source_size), topology, incoming, outgoing, render_spec, config.seed)
            bank_mask = Image.fromarray(geometry.bank_mask.astype(np.uint8) * 255, mode="L")
            body_mask = Image.fromarray(geometry.body_mask.astype(np.uint8) * 255, mode="L")
            image = Image.composite(bank, base, bank_mask)
            image = Image.composite(water, image, body_mask)
            mask = geometry.body_mask
            save_png(Image.fromarray(geometry.centerline_mask.astype(np.uint8) * 255, mode="L"), self._debug_path(config, target, level, "centerline.png"))
            save_png(bank_mask, self._debug_path(config, target, level, "bank_mask.png"))
            save_png(body_mask, self._debug_path(config, target, level, "body_mask.png"))
            metadata = {
                "renderer": "river_network_adapter",
                "topology": topology,
                "width_ratio": width,
                "width_variation": render_spec.width_variation,
                "bank_width_ratio": render_spec.bank_width_ratio,
                "incoming": list(incoming),
                "outgoing": list(outgoing),
                "base": base_key,
            }
        return image, mask, None, None, metadata

    def _render_object(
        self,
        config: PixelGrammarStudyConfig,
        target: str,
        level: DensityLevel,
        profile: PixelGrammarProfile,
    ) -> tuple[Image.Image, np.ndarray]:
        size = config.source_size
        background = apply_density(self._load_source(config, "grass"), level, profile, config.seed)
        overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        if target == "tree_object":
            cx, cy = size * 0.5, size * 0.48
            radius = size * 0.22
            draw.rectangle((cx - size * 0.035, cy + radius * 0.35, cx + size * 0.035, cy + radius * 1.25), fill=(91, 57, 35, 255))
            draw.ellipse((cx - radius, cy - radius * 0.78, cx + radius, cy + radius * 0.82), fill=(40, 91, 42, 255), outline=(21, 57, 29, 255), width=max(2, size // 64))
            if level is not DensityLevel.SPARSE:
                for offset in (-0.28, 0.0, 0.28):
                    draw.ellipse((cx + offset * radius - radius * 0.45, cy - radius * 0.3, cx + offset * radius + radius * 0.45, cy + radius * 0.42), fill=(62, 121, 51, 255))
            if level is DensityLevel.DETAILED:
                for offset in (-0.32, -0.10, 0.14, 0.34):
                    draw.ellipse((cx + offset * radius - 5, cy - radius * 0.28, cx + offset * radius + 5, cy - radius * 0.14), fill=(130, 170, 65, 255))
        else:
            cx, cy = size * 0.5, size * 0.52
            points = [(cx - size * 0.22, cy + size * 0.04), (cx - size * 0.13, cy - size * 0.18), (cx + size * 0.12, cy - size * 0.21), (cx + size * 0.24, cy + size * 0.04), (cx + size * 0.10, cy + size * 0.20), (cx - size * 0.15, cy + size * 0.18)]
            draw.polygon(points, fill=(102, 105, 99, 255), outline=(57, 60, 58, 255))
            if level is not DensityLevel.SPARSE:
                draw.polygon([(cx - size * 0.08, cy - size * 0.13), (cx + size * 0.10, cy - size * 0.15), (cx + size * 0.04, cy - size * 0.02), (cx - size * 0.12, cy)], fill=(160, 163, 150, 255))
            if level is DensityLevel.DETAILED:
                draw.line((cx - size * 0.07, cy + size * 0.08, cx + size * 0.12, cy + size * 0.10), fill=(67, 69, 67, 255), width=max(2, size // 90))
        alpha = np.asarray(overlay.getchannel("A"), dtype=np.uint8) > 0
        composite = Image.alpha_composite(background, overlay)
        return apply_density(composite, level, profile, config.seed), alpha

    def _pixelize(
        self,
        config: PixelGrammarStudyConfig,
        image: Image.Image,
        output: Path,
        target: str,
        level: DensityLevel,
        shared_palette: tuple[tuple[int, int, int], ...] | None,
    ) -> tuple[Image.Image, dict[str, Any]]:
        options = compiler_grammar_options(level)
        if config.pixelize:
            result = PixelTileCompiler().compile_image(
                image,
                CompilerConfig(
                    output_root=output / "compiler",
                    palette_budget=config.palette_budget,
                    tile_mode="object" if target.endswith("_object") else "repeatable",
                    repeat_opt_enabled=True,
                    repeat_opt_strength=float(options["repeat_opt_strength"]),
                    center_suppression_strength=float(options["center_suppression_strength"]),
                    smoothing_enabled=bool(options["smoothing_enabled"]),
                    seed=config.seed,
                    debug_enabled=False,
                    palette_colors=shared_palette,
                ),
                source_name=f"{target}_{level.value}",
            )
            final = Image.open(result.final_path).convert("RGBA").copy()
            metadata = {
                "pixelized": True,
                "shared_palette": shared_palette is not None,
                "final_path": str(result.final_path),
                "compiler_metrics": result.metadata.get("metrics", {}),
            }
        else:
            final = image.convert("RGBA").resize((config.tile_size, config.tile_size), Image.Resampling.LANCZOS)
            metadata = {"pixelized": False, "shared_palette": shared_palette is not None}
        save_png(final, output / "tile.png")
        return final, metadata

    def _write_metrics(self, root: Path, records: list[dict[str, Any]]) -> None:
        save_json({"records": records}, root / "metrics" / "grammar_metrics.json")

    def _write_summary(
        self,
        config: PixelGrammarStudyConfig,
        root: Path,
        records: list[dict[str, Any]],
        recommendations: dict[str, dict[str, Any]],
    ) -> None:
        summary = root / "summary"
        summary.mkdir(parents=True, exist_ok=True)
        save_json(recommendations, summary / "recommended_profiles.json")
        save_png(_comparison_board(records), summary / "comparison_board.png")
        lines = [
            "# 64x64 Pixel Grammar Study",
            "",
            "This experiment compares sparse, balanced and detailed reduction profiles under the same source, palette budget and seed.",
            "The metrics are approximate instrumentation for hypothesis testing, not a replacement for pixel-artist review.",
            "",
            "## Recommended profiles",
            "",
        ]
        for target in config.targets:
            recommendation = recommendations.get(target, {})
            lines.append(f"- `{target}`: **{recommendation.get('density_level', 'n/a')}** (score {recommendation.get('score', 0.0):.3f}) — {recommendation.get('reason', '')}")
        lines.extend(
            [
                "",
                "## Study interpretation",
                "",
                "- Sparse should reduce clutter and protect large semantic masses, but can erase useful local variation.",
                "- Balanced is the reference condition for comparing semantic readability against texture richness.",
                "- Detailed preserves high-frequency information, but can make 64x64 surfaces noisy and weaken transitions or silhouettes.",
                "- Network and transition geometry are kept outside the density transform; only the material appearance is changed.",
                "",
                "## Known limitations",
                "",
                "- Frequency bands and clutter are image statistics, not human perception.",
                "- The map previews intentionally repeat the representative tile to expose grammar-level grid and periodicity behavior.",
                "- Tree and rock are deterministic sample objects only; they do not introduce a general object compiler.",
                "",
            ]
        )
        (summary / "grammar_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (summary / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (summary / "limitations.md").write_text(
            "# Limitations\n\n" + "\n".join(f"- {item}" for item in [
                "Metrics are approximate and should be checked against the comparison board.",
                "The study does not train or call a diffusion model.",
                "The object rows are procedural samples, not a full object-overlay system.",
            ]) + "\n",
            encoding="utf-8",
        )

    def _recommend(self, records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            grouped.setdefault(record["target"], []).append(record)
        output: dict[str, dict[str, Any]] = {}
        for target, values in grouped.items():
            ordered = sorted(
                values,
                key=lambda value: (
                    float(value["metrics"]["grammar_score"]),
                    value["density_level"] == DensityLevel.BALANCED.value,
                ),
                reverse=True,
            )
            best = ordered[0]
            tile = best["metrics"]["tile"]
            reason = (
                f"readability={tile.get('readability_score', 0.0):.3f}, "
                f"semantic_fitness={tile.get('semantic_fitness_score', 0.0):.3f}, "
                f"clutter={tile.get('clutter_risk_score', 0.0):.3f}"
            )
            output[target] = {
                "target": target,
                "density_level": best["density_level"],
                "score": best["metrics"]["grammar_score"],
                "profile": best["profile"],
                "reason": reason,
                "ranked_levels": [
                    {"density_level": item["density_level"], "score": item["metrics"]["grammar_score"]}
                    for item in ordered
                ],
            }
        return output

    def _profile(self, config: PixelGrammarStudyConfig, target: str, role: str, material: str, level: DensityLevel) -> PixelGrammarProfile:
        profile_mapping = config.target_specs.get(target, {}).get("profile")
        if isinstance(profile_mapping, dict):
            values = dict(profile_mapping)
            values.setdefault("name", f"{target}_{level.value}")
            values.setdefault("semantic_role", role)
            values.setdefault("material", material)
            values["preferred_density"] = level.value
            return PixelGrammarProfile.from_dict(values)
        base = default_profile(target, role, material)
        return PixelGrammarProfile(
            **{**base.to_dict(), "name": f"{target}_{level.value}", "preferred_density": level.value}
        )

    def _target_identity(self, config: PixelGrammarStudyConfig, target: str) -> tuple[str, str]:
        spec = config.target_specs.get(target, {})
        if spec:
            return str(spec.get("semantic_role", spec.get("role", "surface"))), str(spec.get("material", target))
        if target in {"grass", "forest_canopy"}:
            return "surface", target
        if target in {"dirt_road", "river"}:
            return "network", target
        if target in {"grass_forest", "grass_road", "grass_river", "forest_river"}:
            return "transition", target
        return "object", target.removesuffix("_object")

    def _load_target_source(self, config: PixelGrammarStudyConfig, target: str) -> Image.Image:
        return self._load_source(config, self._source_for_target(config, target))

    def _source_for_target(self, config: PixelGrammarStudyConfig, target: str) -> str:
        return str(config.target_specs.get(target, {}).get("source", target))

    def _transition_sources(self, target: str) -> tuple[str, str]:
        return {
            "grass_forest": ("grass", "forest_canopy"),
            "grass_road": ("grass", "road"),
            "grass_river": ("grass", "water"),
            "forest_river": ("forest_canopy", "water"),
        }[target]

    def _load_source(self, config: PixelGrammarStudyConfig, key: str) -> Image.Image:
        aliases = {
            "forest": ("forest", "forest_canopy"),
            "forest_canopy": ("forest_canopy", "forest"),
            "road": ("road", "dirt_road"),
            "dirt_road": ("dirt_road", "road"),
            "bank": ("bank", "road", "dirt_road"),
        }
        for candidate in aliases.get(key, (key,)):
            path = config.sources.get(candidate)
            if path is not None and path.exists():
                return load_image(path).convert("RGBA").resize((config.source_size, config.source_size), Image.Resampling.BICUBIC)
        path = config.sources.get(key, Path(key))
        raise FileNotFoundError(f"pixel grammar source is missing: {key} ({path})")

    def _debug_path(self, config: PixelGrammarStudyConfig, target: str, level: DensityLevel, name: str) -> Path:
        path = Path(config.output_root) / "debug" / target / level.value / name
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _write_config(self, config: PixelGrammarStudyConfig, root: Path) -> None:
        save_json(config.to_dict(), root / "configs" / "study_config.json")
        profiles = {
            target: {
                level.value: self._profile(config, target, *self._target_identity(config, target), level).to_dict()
                for level in config.levels
            }
            for target in config.targets
        }
        save_json(profiles, root / "configs" / "pixel_grammar_profiles.json")

    def _build_shared_palette(self, config: PixelGrammarStudyConfig) -> tuple[tuple[int, int, int], ...]:
        """Build one deterministic palette from all material exemplars."""
        images: list[Image.Image] = []
        for path in config.sources.values():
            if path.exists():
                with Image.open(path) as image:
                    images.append(image.convert("RGB").resize((64, 64), Image.Resampling.BILINEAR))
        if not images:
            return ((0, 0, 0),)
        strip = Image.new("RGB", (64, 64 * len(images)), (0, 0, 0))
        for index, image in enumerate(images):
            strip.paste(image, (0, index * 64))
        quantized = strip.quantize(colors=config.palette_budget, method=Image.Quantize.MEDIANCUT)
        palette = quantized.getpalette() or []
        counts = quantized.getcolors(maxcolors=config.palette_budget) or []
        colors: list[tuple[int, int, int]] = []
        for _, palette_index in sorted(counts, reverse=True):
            offset = palette_index * 3
            color = tuple(int(value) for value in palette[offset : offset + 3])
            if len(color) == 3 and color not in colors:
                colors.append(color)
        return tuple(colors[: config.palette_budget]) or ((0, 0, 0),)


def _assemble_repeated_map(tile: Image.Image, columns: int, rows: int) -> Image.Image:
    tile = tile.convert("RGBA")
    output = Image.new("RGBA", (tile.width * columns, tile.height * rows), (0, 0, 0, 0))
    for y in range(rows):
        for x in range(columns):
            output.paste(tile, (x * tile.width, y * tile.height))
    return output


def _comparison_board(records: list[dict[str, Any]], thumb: int = 144) -> Image.Image:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(record["target"], {})[record["density_level"]] = record
    levels = [DensityLevel.SPARSE.value, DensityLevel.BALANCED.value, DensityLevel.DETAILED.value]
    cell_width = thumb + 24
    cell_height = thumb + 42
    board = Image.new("RGBA", (cell_width * len(levels), cell_height * max(1, len(grouped))), (25, 25, 29, 255))
    draw = ImageDraw.Draw(board)
    for row, target in enumerate(sorted(grouped)):
        for column, level in enumerate(levels):
            record = grouped[target].get(level)
            if record is None:
                continue
            path = Path(record["artifacts"]["tile"])
            if path.exists():
                with Image.open(path) as image:
                    preview = image.convert("RGBA").resize((thumb, thumb), Image.Resampling.NEAREST)
                board.paste(preview, (column * cell_width, row * cell_height))
            score = float(record["metrics"]["grammar_score"])
            draw.text((column * cell_width + 3, row * cell_height + thumb + 3), f"{target} / {level}", fill=(245, 245, 245, 255))
            draw.text((column * cell_width + 3, row * cell_height + thumb + 19), f"score={score:.3f}", fill=(190, 205, 220, 255))
    return board
