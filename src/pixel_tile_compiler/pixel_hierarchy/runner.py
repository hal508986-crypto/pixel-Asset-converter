"""Batch runner for Flat / Structured / Volumetric comparison."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter

from pixel_tile_compiler.config import CompilerConfig
from pixel_tile_compiler.io.exporter import save_json, save_png
from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler
from pixel_tile_compiler.pixel_grammar.config import PixelGrammarStudyConfig
from pixel_tile_compiler.pixel_grammar.metrics import compute_map_metrics
from pixel_tile_compiler.pixel_grammar.profiles import (
    DensityLevel,
    HierarchyMode,
    PixelGrammarProfile,
    default_profile,
)
from pixel_tile_compiler.pixel_grammar.runner import PixelGrammarStudyRunner

from .config import PixelHierarchyStudyConfig
from .metrics import compute_hierarchy_metrics, hierarchy_score
from .volume import VolumePass


@dataclass(frozen=True)
class PixelHierarchyStudyResult:
    output_root: Path
    records: tuple[dict[str, Any], ...]
    recommended_profiles: dict[str, dict[str, Any]]
    comparison_board_path: Path
    metrics_path: Path


class PixelHierarchyStudyRunner:
    """Reuse existing material/network renderers and compare hierarchy passes."""

    def run(self, config: PixelHierarchyStudyConfig) -> PixelHierarchyStudyResult:
        root = Path(config.output_root)
        root.mkdir(parents=True, exist_ok=True)
        self._write_config(config, root)
        base_config = self._base_grammar_config(config, root)
        base_runner = PixelGrammarStudyRunner()
        shared_palette = base_runner._build_shared_palette(base_config) if config.shared_palette else None
        if shared_palette is not None:
            save_json(
                {"palette_budget": config.palette_budget, "colors": [list(color) for color in shared_palette]},
                root / "configs" / "shared_palette.json",
            )

        records: list[dict[str, Any]] = []
        images_by_target: dict[str, dict[HierarchyMode, Image.Image]] = {}
        for target in config.targets:
            target_records, target_images = self._compile_target(config, root, base_config, base_runner, target, shared_palette)
            records.extend(target_records)
            images_by_target[target] = target_images

        self._write_map_and_object_previews(config, root, records, images_by_target)
        self._write_metrics(root, records)
        recommendations = self._recommend(records)
        self._write_summary(config, root, records, recommendations)
        return PixelHierarchyStudyResult(
            root,
            tuple(records),
            recommendations,
            root / "summary" / "comparison_board.png",
            root / "metrics" / "hierarchy_metrics.json",
        )

    def _compile_target(
        self,
        config: PixelHierarchyStudyConfig,
        root: Path,
        base_config: PixelGrammarStudyConfig,
        base_runner: PixelGrammarStudyRunner,
        target: str,
        shared_palette: tuple[tuple[int, int, int], ...] | None,
    ) -> tuple[list[dict[str, Any]], dict[HierarchyMode, Image.Image]]:
        role, material = base_runner._target_identity(base_config, target)
        structured_profile = self._profile(target, role, material, HierarchyMode.STRUCTURED)
        source, semantic_mask, boundary_mask, silhouette_mask, render_metadata = base_runner._render_source(
            base_config,
            target,
            DensityLevel.BALANCED,
            structured_profile,
            root / "debug" / target,
        )
        images: dict[HierarchyMode, Image.Image] = {}
        records: list[dict[str, Any]] = []
        compiler_metrics: dict[HierarchyMode, dict[str, Any]] = {}
        for mode in config.modes:
            output = root / "tiles" / target / mode.value
            output.mkdir(parents=True, exist_ok=True)
            profile = self._profile(target, role, material, mode)
            structured = _structured_pass(source, profile)
            if mode is HierarchyMode.FLAT:
                processed = _flat_pass(source, profile)
            elif mode is HierarchyMode.STRUCTURED:
                processed = structured
            else:
                save_png(structured, output / "structured_base.png")
                processed = VolumePass().apply(
                    structured,
                    profile,
                    target=target,
                    semantic_mask=semantic_mask,
                    boundary_mask=boundary_mask,
                    silhouette_mask=silhouette_mask,
                    light_direction=config.lighting.direction,
                    light_strength=config.lighting.strength,
                    seed=config.seed,
                )
            save_png(processed, output / "source_composite.png")
            if semantic_mask is not None:
                save_png(Image.fromarray(semantic_mask.astype(np.uint8) * 255, mode="L"), output / "semantic_mask.png")
            if boundary_mask is not None:
                save_png(Image.fromarray(boundary_mask.astype(np.uint8) * 255, mode="L"), output / "boundary_mask.png")
            if silhouette_mask is not None:
                save_png(Image.fromarray(silhouette_mask.astype(np.uint8) * 255, mode="L"), output / "silhouette_mask.png")
            final, compiler_data = self._pixelize(config, processed, output, target, shared_palette)
            images[mode] = final
            compiler_metrics[mode] = compiler_data
            records.append(
                {
                    "target": target,
                    "semantic_role": role,
                    "material": material,
                    "hierarchy_mode": mode.value,
                    "profile": profile.to_dict(),
                    "render": render_metadata,
                    "compiler": compiler_data,
                    "artifacts": {"root": str(output), "source": str(output / "source_composite.png"), "tile": str(output / "tile.png")},
                }
            )

        structured_tile = images.get(HierarchyMode.STRUCTURED)
        for record in records:
            mode = HierarchyMode(record["hierarchy_mode"])
            output = Path(record["artifacts"]["root"])
            metrics = compute_hierarchy_metrics(
                images[mode],
                target=target,
                semantic_role=role,
                mode=mode,
                structured_baseline=structured_tile,
                semantic_mask=semantic_mask,
                boundary_mask=boundary_mask,
                silhouette_mask=silhouette_mask,
                lighting_direction=config.lighting.direction,
            )
            record["metrics"] = {"tile": metrics, "map": {}, "scores": {}}
            profile = self._profile(target, role, material, mode)
            save_json(profile.to_dict(), output / "profile.json")
            save_json(record, output / "metrics.json")
        return records, images

    def _pixelize(
        self,
        config: PixelHierarchyStudyConfig,
        image: Image.Image,
        output: Path,
        target: str,
        shared_palette: tuple[tuple[int, int, int], ...] | None,
    ) -> tuple[Image.Image, dict[str, Any]]:
        result = None
        if config.pixelize:
            result = PixelTileCompiler().compile_image(
                image,
                CompilerConfig(
                    output_root=output / "compiler",
                    palette_budget=config.palette_budget,
                    palette_colors=shared_palette,
                    tile_mode="object" if target.endswith("_object") else "repeatable",
                    repeat_opt_enabled=True,
                    repeat_opt_strength=0.50,
                    center_suppression_strength=0.40,
                    smoothing_enabled=True,
                    seed=config.seed,
                    debug_enabled=False,
                ),
                source_name=f"{target}_hierarchy",
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

    def _write_map_and_object_previews(
        self,
        config: PixelHierarchyStudyConfig,
        root: Path,
        records: list[dict[str, Any]],
        images_by_target: dict[str, dict[HierarchyMode, Image.Image]],
    ) -> None:
        for record in records:
            target = record["target"]
            mode = HierarchyMode(record["hierarchy_mode"])
            image = images_by_target[target][mode]
            role = record["semantic_role"]
            if role in {"surface", "network"} and config.generate_demo_maps:
                preview = _repeat_map(image, config.map_width, config.map_height)
                path = root / "maps" / f"{target}_{mode.value}.png"
                save_png(preview, path)
                map_metrics = compute_map_metrics(preview, config.tile_size)
                tile_metrics = record["metrics"]["tile"]
                map_metrics.update(_map_semantic_metrics(tile_metrics))
                record["metrics"]["map"] = map_metrics
                record["metrics"]["scores"] = hierarchy_score(tile_metrics, map_metrics)
                record["artifacts"]["map"] = str(path)
                if config.generate_unit_preview:
                    unit_path = root / "unit_overlays" / f"{target}_{mode.value}.png"
                    save_png(_draw_unit_overlay(preview), unit_path)
                    record["artifacts"]["unit_overlay"] = str(unit_path)
            elif role == "object":
                preview = _repeat_map(image, config.object_preview_size, config.object_preview_size)
                path = root / "objects" / f"{target}_{mode.value}.png"
                save_png(preview, path)
                record["metrics"]["map"] = compute_map_metrics(preview, config.tile_size)
                record["metrics"]["scores"] = hierarchy_score(record["metrics"]["tile"], record["metrics"]["map"])
                record["artifacts"]["object_preview"] = str(path)
            else:
                record["metrics"]["scores"] = hierarchy_score(record["metrics"]["tile"])
            save_json(record, Path(record["artifacts"]["root"]) / "metrics.json")

    def _recommend(self, records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = {}
        for record in records:
            grouped.setdefault(record["target"], []).append(record)
        output: dict[str, dict[str, Any]] = {}
        for target, values in grouped.items():
            dimension_winners: dict[str, str] = {}
            for dimension, reverse in (
                ("tile_quality", True),
                ("map_readability", True),
                ("semantic_fitness", True),
                ("hierarchy_quality", True),
                ("noise_risk", False),
            ):
                best = sorted(values, key=lambda item: float(item["metrics"]["scores"].get(dimension, 0.0)), reverse=reverse)[0]
                dimension_winners[dimension] = best["hierarchy_mode"]
            selected = max(values, key=lambda item: float(item["metrics"]["scores"].get("overall", 0.0)))
            output[target] = {
                "target": target,
                "recommended_mode": selected["hierarchy_mode"],
                "score": selected["metrics"]["scores"].get("overall", 0.0),
                "dimension_winners": dimension_winners,
                "ranked_modes": [
                    {"mode": item["hierarchy_mode"], "scores": item["metrics"]["scores"]}
                    for item in sorted(values, key=lambda item: float(item["metrics"]["scores"].get("overall", 0.0)), reverse=True)
                ],
            }
        return output

    def _profile(self, target: str, role: str, material: str, mode: HierarchyMode) -> PixelGrammarProfile:
        base = default_profile(target, role, material)
        if mode is HierarchyMode.FLAT:
            values = {"major_mass_strength": 0.95, "medium_cluster_strength": 0.18, "shading_layers": 1, "depth_cue_strength": 0.05, "contact_shadow_strength": 0.02, "highlight_strength": 0.02, "micro_detail_strength": 0.04}
        elif mode is HierarchyMode.STRUCTURED:
            values = {"major_mass_strength": 0.90, "medium_cluster_strength": 0.68, "shading_layers": 3, "depth_cue_strength": 0.24, "contact_shadow_strength": 0.12, "highlight_strength": 0.14, "micro_detail_strength": 0.16}
        else:
            values = {"major_mass_strength": 0.92, "medium_cluster_strength": 0.72, "shading_layers": 4, "depth_cue_strength": 0.62, "contact_shadow_strength": 0.48, "highlight_strength": 0.38, "micro_detail_strength": 0.18}
        if role == "network":
            values["topology_priority"] = 0.98
        if role == "object":
            values["silhouette_priority"] = 0.95
        return PixelGrammarProfile(
            **{
                **base.to_dict(),
                "name": f"{target}_{mode.value}",
                "hierarchy_mode": mode.value,
                **values,
            }
        )

    def _base_grammar_config(self, config: PixelHierarchyStudyConfig, root: Path) -> PixelGrammarStudyConfig:
        return PixelGrammarStudyConfig(
            output_root=root,
            targets=config.targets,
            levels=(DensityLevel.BALANCED,),
            sources=config.sources,
            target_specs=config.target_specs,
            tile_size=config.tile_size,
            source_size=config.source_size,
            palette_budget=config.palette_budget,
            shared_palette=config.shared_palette,
            pixelize=False,
            generate_demo_maps=False,
            map_width=config.map_width,
            map_height=config.map_height,
            seed=config.seed,
        )

    def _write_config(self, config: PixelHierarchyStudyConfig, root: Path) -> None:
        save_json(config.to_dict(), root / "configs" / "study_config.json")

    def _write_metrics(self, root: Path, records: list[dict[str, Any]]) -> None:
        save_json({"records": records}, root / "metrics" / "hierarchy_metrics.json")

    def _write_summary(
        self,
        config: PixelHierarchyStudyConfig,
        root: Path,
        records: list[dict[str, Any]],
        recommendations: dict[str, dict[str, Any]],
    ) -> None:
        summary = root / "summary"
        summary.mkdir(parents=True, exist_ok=True)
        save_json(recommendations, summary / "recommended_profiles.json")
        save_png(_comparison_board(records), summary / "comparison_board.png")
        lines = [
            "# 64x64 Flat / Structured / Volumetric Pixel Grammar Study",
            "",
            "This study tests hierarchy depth rather than high-frequency detail. Volumetric is implemented as Structured followed by a coherent semantic VolumePass.",
            "All modes reuse the same source, topology, renderer input, palette and seed.",
            "",
            "## Recommended modes",
            "",
        ]
        for target in config.targets:
            recommendation = recommendations.get(target, {})
            lines.append(f"- `{target}`: **{recommendation.get('recommended_mode', 'n/a')}** (overall {recommendation.get('score', 0.0):.3f})")
        lines.extend(
            [
                "",
                "## Answers to the study questions",
                "",
                "1. The previous Detailed failure is treated as a noise baseline; this study checks whether volume can rise while high-frequency noise stays controlled.",
                "2. High-frequency detail is represented by noise risk and isolated/micro cluster ratios, while high-fidelity hierarchy is represented by major mass, medium cluster, shading and depth metrics.",
                "3. The most useful volume cues are broad plane lighting, semantic edge/contact shadow and a restrained highlight; pixel speckle is intentionally excluded.",
                "4. Network and transition modes must preserve masks first; objects benefit most visibly from silhouette plus contact shadow, while surfaces benefit from coherent cluster lighting.",
                "5. A future default should keep Structured as the base grammar and expose VolumePass as a semantic renderer stage, not as generic sharpening.",
                "6. Future Source Generators should provide major masses, medium clusters, material planes and stable light/depth cues rather than more microtexture.",
                "",
                "## Limitations",
                "",
                "- Metrics are approximate and must be checked against the comparison board.",
                "- The 10x10 previews use representative tile repetition to make map clutter and grid visibility observable.",
                "- Tree and rock remain procedural study samples, not a general object compiler.",
                "",
            ]
        )
        (summary / "hierarchy_summary.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        (summary / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _flat_pass(image: Image.Image, profile: PixelGrammarProfile) -> Image.Image:
    reduced = image.convert("RGBA").filter(ImageFilter.GaussianBlur(radius=1.2))
    return ImageEnhance.Contrast(reduced).enhance(0.92 + profile.major_mass_strength * 0.04)


def _structured_pass(image: Image.Image, profile: PixelGrammarProfile) -> Image.Image:
    return ImageEnhance.Contrast(image.convert("RGBA")).enhance(0.98 + profile.medium_cluster_strength * 0.05)


def _repeat_map(tile: Image.Image, columns: int, rows: int) -> Image.Image:
    output = Image.new("RGBA", (tile.width * columns, tile.height * rows), (0, 0, 0, 0))
    for y in range(rows):
        for x in range(columns):
            output.paste(tile, (x * tile.width, y * tile.height))
    return output


def _map_semantic_metrics(tile_metrics: dict[str, Any]) -> dict[str, float]:
    clutter = float(tile_metrics.get("clutter_risk_score", 0.0))
    semantic = float(tile_metrics.get("semantic_fitness_score", 0.0))
    return {
        "map_clutter_score": round(min(1.0, clutter * 1.25), 6),
        "background_competition_score": round(min(1.0, clutter * 0.85), 6),
        "semantic_readability_score": round(max(0.0, semantic - clutter * 0.12), 6),
    }


def _draw_unit_overlay(image: Image.Image) -> Image.Image:
    output = image.convert("RGBA").copy()
    draw = ImageDraw.Draw(output)
    cx, cy = output.width // 2, output.height // 2
    body = (cx - 12, cy - 2, cx + 12, cy + 28)
    head = (cx - 8, cy - 26, cx + 8, cy - 10)
    draw.ellipse(head, fill=(238, 222, 180, 255), outline=(30, 35, 45, 255), width=2)
    draw.rectangle(body, fill=(52, 69, 112, 255), outline=(20, 25, 40, 255), width=2)
    draw.line((cx - 12, cy + 4, cx - 23, cy + 18), fill=(20, 25, 40, 255), width=4)
    draw.line((cx + 12, cy + 4, cx + 23, cy + 18), fill=(20, 25, 40, 255), width=4)
    return output


def _comparison_board(records: list[dict[str, Any]], thumb: int = 144) -> Image.Image:
    grouped: dict[str, dict[str, dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(record["target"], {})[record["hierarchy_mode"]] = record
    modes = [mode.value for mode in HierarchyMode]
    cell_width = thumb + 42
    cell_height = thumb + 48
    board = Image.new("RGBA", (cell_width * len(modes), cell_height * max(1, len(grouped))), (25, 25, 29, 255))
    draw = ImageDraw.Draw(board)
    for row, target in enumerate(sorted(grouped)):
        for column, mode in enumerate(modes):
            record = grouped[target].get(mode)
            if record is None:
                continue
            path = Path(record["artifacts"]["tile"])
            if path.exists():
                with Image.open(path) as image:
                    preview = image.convert("RGBA").resize((thumb, thumb), Image.Resampling.NEAREST)
                board.paste(preview, (column * cell_width, row * cell_height))
            scores = record.get("metrics", {}).get("scores", {})
            draw.text((column * cell_width + 3, row * cell_height + thumb + 3), f"{target} / {mode}", fill=(245, 245, 245, 255))
            draw.text(
                (column * cell_width + 3, row * cell_height + thumb + 20),
                f"h={float(scores.get('hierarchy_quality', 0.0)):.2f} n={float(scores.get('noise_risk', 0.0)):.2f}",
                fill=(190, 205, 220, 255),
            )
    return board
