"""Batch runner for comparing multiple grass material sources."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from pixel_tile_compiler.io.exporter import save_json, save_png
from pixel_tile_compiler.io.loader import load_image
from pixel_tile_compiler.tileset.compiler import TilesetSourceCompiler
from pixel_tile_compiler.tileset.source_tile import TilesetConfig
from pixel_tile_compiler.tileset.validator import validate_material_source

from .models import SourceCandidate, SourceStudyConfig, SourceStudyResult
from .prompts import render_source_prompt, write_prompt_set
from .scoring import rank_source_records, score_source_record, study_summary_markdown


class SourceStudyRunner:
    """Run every available source through the same tileset compiler settings."""

    def run(self, config: SourceStudyConfig) -> SourceStudyResult:
        root = Path(config.output_root)
        root.mkdir(parents=True, exist_ok=True)
        self._write_config_and_prompts(config, root)
        records: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        for candidate in config.candidates:
            source_path = Path(candidate.source_path or config.source_root / f"{candidate.source_id}.png")
            prompt = self._prompt_for(candidate, config.material)
            compiled_root = root / "compiled" / candidate.source_id
            compiled_root.mkdir(parents=True, exist_ok=True)
            (compiled_root / "prompt.txt").write_text(prompt + "\n", encoding="utf-8")
            save_json(
                {"source_id": candidate.source_id, "generation_enabled": config.generation_enabled, **candidate.generation},
                compiled_root / "generation.json",
            )
            if not source_path.exists():
                skipped_record = {
                    "source_id": candidate.source_id,
                    "status": "missing_source",
                    "source_path": str(source_path),
                    "message": "source image is missing; place the generated image at this path and rerun",
                }
                skipped.append(skipped_record)
                save_json(skipped_record, compiled_root / "record.json")
                continue
            image = load_image(source_path)
            save_png(image, compiled_root / "source.png")
            validation = validate_material_source(
                image,
                palette_budget=self._palette_budget(config),
                material=config.material,
            )
            validation_dict = {
                "source_id": candidate.source_id,
                "accepted": validation.status != "rejected",
                **validation.as_dict(),
            }
            save_json(validation_dict, root / "validation" / f"{candidate.source_id}.json")
            save_json(validation_dict, compiled_root / "validation.json")
            tileset_result = TilesetSourceCompiler().build(
                source_path,
                self._tileset_config(config, compiled_root),
            )
            tileset_metrics = tileset_result.metrics["source_compiler_tileset"]
            score, reasons = score_source_record(validation_dict, tileset_metrics, material=config.material)
            record = {
                "source_id": candidate.source_id,
                "status": "completed",
                "source_path": str(source_path),
                "prompt_file": str(candidate.prompt_file) if candidate.prompt_file else None,
                "validation": validation_dict,
                "score": round(score, 6),
                "reasons": reasons,
                "tileset": {
                    "single_repeat": tileset_result.metrics["single_repeat"],
                    "independent_variants": tileset_result.metrics["independent_variants"],
                    "source_compiler_tileset": tileset_metrics,
                    "contract_validation": tileset_result.metrics["contract_validation"],
                },
                "artifacts": {
                    "root": str(compiled_root),
                    "comparison": str(tileset_result.comparison_path),
                    "metrics": str(tileset_result.metrics_path),
                },
            }
            records.append(record)
            save_json(record, compiled_root / "record.json")
        ranking = rank_source_records(records)
        self._write_summary(config, root, records, ranking, skipped)
        return SourceStudyResult(root, tuple(records), tuple(ranking), tuple(skipped))

    @staticmethod
    def _palette_budget(config: SourceStudyConfig) -> int:
        return int(config.tileset_options.get("palette", config.tileset_options.get("palette_budget", 24)))

    @staticmethod
    def _tileset_config(config: SourceStudyConfig, output_root: Path) -> TilesetConfig:
        options = config.tileset_options
        return TilesetConfig(
            output_root=output_root,
            material=config.material,
            variants=int(options.get("variants", 12)),
            edge_types=int(options.get("edge_types", 3)),
            palette_budget=int(options.get("palette", options.get("palette_budget", 24))),
            shared_palette=bool(options.get("shared_palette", True)),
            map_columns=int(options.get("preview_cols", options.get("map_columns", 10))),
            map_rows=int(options.get("preview_rows", options.get("map_rows", 10))),
            source_tile_size=int(options.get("source_tile_size", 512)),
            patch_size=int(options.get("patch_size", 256)),
            patch_overlap=int(options.get("patch_overlap", 64)),
            strip_width=int(options.get("strip_width", 96)),
            seed=int(options.get("seed", config.seed)),
        )

    @staticmethod
    def _prompt_for(candidate: SourceCandidate, material: str = "grass") -> str:
        if candidate.prompt_file and candidate.prompt_file.exists():
            return candidate.prompt_file.read_text(encoding="utf-8").rstrip()
        return render_source_prompt(
            {
                "id": candidate.source_id,
                "homogeneity": candidate.homogeneity,
                "brightness_variation": candidate.brightness_variation,
                "tufts": candidate.tufts,
                "composition": candidate.composition,
                "contrast": candidate.contrast,
                "canopy_density": candidate.canopy_density,
                "cluster_scale": candidate.cluster_scale,
                "illustrative": candidate.illustrative,
            },
            material=material,
        )

    def _write_config_and_prompts(self, config: SourceStudyConfig, root: Path) -> None:
        save_json(config.as_dict(), root / "study_config.json")
        matrix_name = "forest_source_prompt_matrix.json" if config.material == "forest_canopy" else f"{config.material}_source_prompt_matrix.json"
        write_prompt_set(
            config.prompt_root,
            matrix_path=config.prompt_root.parent / matrix_name,
            material=config.material,
        )
        for candidate in config.candidates:
            prompt_path = candidate.prompt_file or config.prompt_root / f"{candidate.source_id}.txt"
            prompt_path.parent.mkdir(parents=True, exist_ok=True)
            if not prompt_path.exists():
                prompt_path.write_text(self._prompt_for(candidate, config.material) + "\n", encoding="utf-8")
            (root / "prompts").mkdir(parents=True, exist_ok=True)
            shutil.copyfile(prompt_path, root / "prompts" / prompt_path.name)

    @staticmethod
    def _write_summary(
        config: SourceStudyConfig,
        root: Path,
        records: list[dict[str, Any]],
        ranking: list[dict[str, Any]],
        skipped: list[dict[str, Any]],
    ) -> None:
        summary_root = root / "summary"
        summary_root.mkdir(parents=True, exist_ok=True)
        save_json(
            {
                "top_sources": [item["source_id"] for item in ranking[:3]],
                "worst_sources": [item["source_id"] for item in ranking[-3:]],
                "ranking": ranking,
                "skipped": skipped,
            },
            summary_root / "source_ranking.json",
        )
        save_json(records, summary_root / "source_comparison_table.json")
        (summary_root / "best_sources.md").write_text(study_summary_markdown(ranking, material=config.material), encoding="utf-8")
        (summary_root / "best_prompt_template.txt").write_text(_best_prompt_template(config.material), encoding="utf-8")
        save_png(_make_montage(root, ranking), summary_root / "montage.png")


def load_study_config(path: Path) -> SourceStudyConfig:
    """Load JSON or YAML without making YAML a mandatory project dependency."""
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
        raise ValueError("source study config must contain a mapping")
    mapping["config_path"] = str(path)
    return SourceStudyConfig.from_mapping(mapping, base_dir=path.parent)


def _make_montage(root: Path, ranking: list[dict[str, Any]], thumbnail_size: int = 256) -> Image.Image:
    if not ranking:
        return Image.new("RGBA", (thumbnail_size * 3, 64), (24, 24, 24, 255))
    row_height = thumbnail_size + 28
    montage = Image.new("RGBA", (thumbnail_size * 3, row_height * len(ranking)), (24, 24, 24, 255))
    draw = ImageDraw.Draw(montage)
    for row, record in enumerate(ranking):
        artifact_root = Path(record["artifacts"]["root"])
        images = (
            artifact_root / "source.png",
            artifact_root / "single_repeat.png",
            artifact_root / "source_compiler_tileset.png",
        )
        for column, path in enumerate(images):
            if path.exists():
                with Image.open(path) as image:
                    preview = image.convert("RGBA")
                    preview.thumbnail((thumbnail_size, thumbnail_size), Image.Resampling.NEAREST)
                    montage.paste(preview, (column * thumbnail_size, row * row_height))
        draw.text((row * 0 + 4, row * row_height + thumbnail_size + 5), f"{record['source_id']}  score={record.get('score', 0):.3f}", fill=(255, 255, 255, 255))
    return montage


def _best_prompt_template(material: str = "grass") -> str:
    if material == "forest_canopy":
        return """Use case: reusable SRPG forest canopy tileset material exemplar
Asset type: high-resolution raster source for texture synthesis
Primary request: a top-down continuous forest canopy material image with connected tree-crown masses
Density: moderate canopy density, neither empty nor an impenetrable uniform block
Cluster scale: small to medium canopy clusters with natural overlap and continuous local connection
Variation: subtle local brightness and color variation, moderate canopy mass variation, no dominant macro bands
Composition: no focal point, no hero tree, no composition center, reusable material field
Constraints: orthographic, no perspective, no path, no clearing, no forest floor, no isolated object subject, no large landmark
Avoid: scenic illustration, visible single trees, rocks, buildings, structures, external cast shadows, strong central mass, repeated macro pattern
"""
    return """Use case: reusable SRPG grass tileset material exemplar
Asset type: high-resolution raster source for texture synthesis
Primary request: a top-down orthographic grass material image with uniform texture scale
Composition: no focal point, no center composition, no illustrative scene structure
Variation: subtle local brightness and color variation, moderate natural texture variation
Density: uniform grass density with small sparse detail, no dominant tuft clusters
Constraints: seamless-friendly, texture-synthesis-friendly, no perspective, no subject, no landmark
Avoid: paths, roads, rocks, trees, flowers, buildings, objects, cast shadows, large patches, repeated macro bands
"""
