"""Real-t2i Material Source Qualification Study.

This module deliberately keeps the real-source experiment separate from the
v0.1 deterministic library builder.  A candidate is accepted only when its
raw image and explicit real-t2i provenance are present.  Missing generation
results produce a queue/blocked study; they never fall back to a synthetic
candidate.
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from PIL import Image, ImageDraw, ImageOps

from pixel_tile_compiler.io.exporter import save_json, save_png

from .fingerprint import fingerprint_image
from .gate import evaluate_source_gate
from .models import GenerationMetadata, HumanReview, MaterialSourceCard, SourceConstraints, TargetPixelSpec
from .probe import run_compile_probe
from .prompts import MATERIALS, render_prompt
from .ranking import rank_candidates, source_downstream_correlations


class RealT2IUnavailable(RuntimeError):
    """Raised when a raw candidate exists without verifiable real-t2i metadata."""


@dataclass
class RealMaterialQualificationConfig:
    output_root: Path = field(default_factory=lambda: Path("e2e/real_t2i_material_qualification"))
    library_root: Path = field(default_factory=lambda: Path("material_library"))
    materials: tuple[str, ...] = ("grass", "dirt", "water", "stone")
    real_candidates_per_material: int = 8
    compiled_variants_per_source: int = 8
    real_t2i_required: bool = True
    fallback_adapter: bool = False
    normalization_working_size: int = 1024
    palette_budget: int = 28
    shared_palette: bool = True
    map_columns: int = 10
    map_rows: int = 10
    human_review_blind: bool = True
    auto_promote_without_review: bool = False
    variance_decomposition: bool = True
    source_effect_ratio: bool = True
    correlations: bool = True
    compiler_sensitivity: bool = True
    seed: int = 42
    config_path: Path | None = None

    def __post_init__(self) -> None:
        self.output_root = Path(self.output_root)
        self.library_root = Path(self.library_root)
        if not self.materials:
            raise ValueError("at least one material family is required")
        unknown = set(self.materials) - set(MATERIALS)
        if unknown:
            raise ValueError(f"unknown material families: {sorted(unknown)}")
        if self.real_candidates_per_material < 1:
            raise ValueError("real_candidates_per_material must be positive")
        if not 1 <= self.compiled_variants_per_source <= 16:
            raise ValueError("compiled_variants_per_source must be between 1 and 16")
        if self.normalization_working_size < 32:
            raise ValueError("normalization_working_size must be at least 32")
        if not 4 <= self.palette_budget <= 32:
            raise ValueError("palette_budget must be between 4 and 32")
        if self.map_columns < 1 or self.map_rows < 1:
            raise ValueError("map dimensions must be positive")
        if self.seed < 0:
            raise ValueError("seed must be non-negative")
        if not self.real_t2i_required and self.fallback_adapter:
            raise ValueError("fallback_adapter is forbidden for this study")

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any], base_dir: Path | None = None) -> "RealMaterialQualificationConfig":
        root = Path(base_dir or ".")
        study = dict(mapping.get("study") or {})
        generation = dict(mapping.get("generation") or {})
        normalization = dict(mapping.get("normalization") or {})
        compile_probe = dict(mapping.get("compile_probe") or {})
        map_probe = dict(mapping.get("map_probe") or {})
        human = dict(mapping.get("human_review") or {})
        analysis = dict(mapping.get("analysis") or {})
        materials = tuple(str(value) for value in study.get("materials", cls.materials))
        return cls(
            output_root=_resolve(root, mapping.get("output", "e2e/real_t2i_material_qualification")),
            library_root=_resolve(root, mapping.get("library_root", "material_library")),
            materials=materials,
            real_candidates_per_material=int(study.get("real_candidates_per_material", 8)),
            compiled_variants_per_source=int(study.get("compiled_variants_per_source", 8)),
            real_t2i_required=bool(generation.get("real_t2i_required", True)),
            fallback_adapter=bool(generation.get("fallback_adapter", False)),
            normalization_working_size=int(normalization.get("working_size", 1024)),
            palette_budget=int(compile_probe.get("palette_budget", compile_probe.get("palette", 28))),
            shared_palette=bool(compile_probe.get("shared_palette", True)),
            map_columns=int(map_probe.get("width", map_probe.get("columns", 10))),
            map_rows=int(map_probe.get("height", map_probe.get("rows", 10))),
            human_review_blind=bool(human.get("blind", True)),
            auto_promote_without_review=bool(human.get("auto_promote_without_review", False)),
            variance_decomposition=bool(analysis.get("variance_decomposition", True)),
            source_effect_ratio=bool(analysis.get("source_effect_ratio", True)),
            correlations=bool(analysis.get("correlations", True)),
            compiler_sensitivity=bool(analysis.get("compiler_sensitivity", True)),
            seed=int(mapping.get("seed", 42)),
            config_path=_resolve(root, mapping["config_path"]) if mapping.get("config_path") else None,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "study": {
                "materials": list(self.materials),
                "real_candidates_per_material": self.real_candidates_per_material,
                "compiled_variants_per_source": self.compiled_variants_per_source,
            },
            "generation": {
                "real_t2i_required": self.real_t2i_required,
                "fallback_adapter": self.fallback_adapter,
            },
            "normalization": {"working_size": self.normalization_working_size},
            "compile_probe": {
                "palette_budget": self.palette_budget,
                "shared_palette": self.shared_palette,
                "variants": self.compiled_variants_per_source,
            },
            "map_probe": {"width": self.map_columns, "height": self.map_rows},
            "human_review": {
                "blind": self.human_review_blind,
                "auto_promote_without_review": self.auto_promote_without_review,
            },
            "analysis": {
                "variance_decomposition": self.variance_decomposition,
                "source_effect_ratio": self.source_effect_ratio,
                "correlations": self.correlations,
                "compiler_sensitivity": self.compiler_sensitivity,
            },
            "seed": self.seed,
            "output": str(self.output_root),
            "library_root": str(self.library_root),
        }


@dataclass(frozen=True)
class RealMaterialQualificationResult:
    output_root: Path
    status: str
    records: tuple[dict[str, Any], ...] = ()
    rankings: dict[str, tuple[dict[str, Any], ...]] = field(default_factory=dict)
    missing_sources: tuple[str, ...] = ()


def load_real_qualification_config(path: Path) -> RealMaterialQualificationConfig:
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
        raise ValueError("real qualification config must contain a mapping")
    mapping["config_path"] = str(path)
    return RealMaterialQualificationConfig.from_mapping(mapping, path.parent)


class RealMaterialQualificationRunner:
    """Run deterministic downstream probes over imported, real-t2i sources."""

    def run(self, config: RealMaterialQualificationConfig) -> RealMaterialQualificationResult:
        root = Path(config.output_root)
        root.mkdir(parents=True, exist_ok=True)
        save_json(config.as_dict(), root / "study_config.json")
        matrix = real_prompt_matrix(config.materials, config.real_candidates_per_material)
        manifest = self._write_generation_queue(root, matrix)
        missing = tuple(item["source_id"] for item in manifest if not Path(item["raw_source_file"]).exists())
        if missing:
            self._write_blocked_outputs(root, manifest, missing)
            return RealMaterialQualificationResult(root, "blocked_real_t2i_unavailable", missing_sources=missing)
        records: list[dict[str, Any]] = []
        for item in manifest:
            records.append(self._run_candidate(item, config, root))
        duplicate_pairs = detect_near_duplicates(
            [Path(record["artifacts"]["source_normalized"]) for record in records]
        )
        self._attach_duplicate_results(records, duplicate_pairs, root)
        diversity = self._attach_diversity_scores(records, root)

        rankings: dict[str, tuple[dict[str, Any], ...]] = {}
        for material in config.materials:
            family = [record for record in records if record["material_id"] == material]
            ranked = rank_candidates(family)
            rankings[material] = tuple(ranked)
            save_json(ranked, root / material / "ranking.json")
            self._write_provisional(material, ranked, root)
            save_json(analyze_source_dominance(family).get(material, {}), root / material / "source_effect.json")

        save_json(records, root / "summary" / "real_source_ranking_input.json")
        save_json(diversity, root / "summary" / "candidate_diversity.json")
        save_json({material: list(values) for material, values in rankings.items()}, root / "summary" / "real_source_ranking.json")
        dominance = analyze_source_dominance(records)
        save_json(dominance, root / "summary" / "variance_decomposition.json")
        save_json(cross_material_analysis(records), root / "summary" / "cross_material_analysis.json")
        if config.correlations:
            save_json(
                {material: source_downstream_correlations(list(values)) for material, values in rankings.items()},
                root / "summary" / "source_downstream_correlations.json",
            )
        save_json(self._current_library_baseline(config), root / "summary" / "current_library_baseline.json")
        self._write_current_vs_real_comparison(config, rankings, root)
        if config.compiler_sensitivity:
            self._write_compiler_sensitivity_controls(config, rankings, root)
        if config.human_review_blind:
            create_blind_review(root, records, seed=config.seed)
        (root / "summary" / "limitations.md").write_text(
            "# Real t2i Material Qualification: limitations\n\n"
            "- Human review ratings are still pending; automatic `auto_top2` promotion remains provisional.\n"
            "- The candidates were imported with `origin=real_t2i`; no deterministic fallback was used.\n"
            "- Correlations and source-effect ratios are exploratory evidence from this fixed probe set, "
            "not causal or statistically significant conclusions.\n",
            encoding="utf-8",
        )
        self._write_report(root, config, records, dominance)
        save_json(
            {"status": "completed", "origin": "real_t2i", "candidates": len(manifest)},
            root / "generation" / "status.json",
        )
        return RealMaterialQualificationResult(root, "completed", tuple(records), rankings)

    def _write_generation_queue(self, root: Path, matrix: list[dict[str, Any]]) -> list[dict[str, Any]]:
        generation_root = root / "generation"
        prompts_root = generation_root / "prompts"
        manifest: list[dict[str, Any]] = []
        for row in matrix:
            source_id = str(row["source_id"])
            candidate_root = generation_root / row["material"] / "real_sources" / source_id
            candidate_root.mkdir(parents=True, exist_ok=True)
            prompt_path = prompts_root / f"{source_id}.txt"
            prompt_path.parent.mkdir(parents=True, exist_ok=True)
            prompt_path.write_text(str(row["prompt"]) + "\n", encoding="utf-8")
            manifest.append({
                **row,
                "raw_source_file": str(candidate_root / "source_raw.png"),
                "generation_metadata_file": str(candidate_root / "generation.json"),
                "prompt_file": str(prompt_path),
                "status": "ready_for_real_t2i_import",
            })
        save_json(matrix, generation_root / "prompt_matrix.json")
        save_json(manifest, generation_root / "generation_manifest.json")
        save_json({"status": "generation_required", "origin": "real_t2i", "candidates": len(manifest)}, generation_root / "generation_queue.json")
        return manifest

    def _run_candidate(self, item: dict[str, Any], config: RealMaterialQualificationConfig, root: Path) -> dict[str, Any]:
        source_id = str(item["source_id"])
        material = str(item["material"])
        raw_path = Path(item["raw_source_file"])
        metadata_path = Path(item["generation_metadata_file"])
        metadata = _read_json(metadata_path) if metadata_path.exists() else {}
        _require_real_provenance(metadata, source_id)
        source_root = root / material / "candidates" / source_id
        source_root.mkdir(parents=True, exist_ok=True)
        normalized_path = source_root / "source_normalized.png"
        normalization = normalize_source(raw_path, normalized_path, config.normalization_working_size)
        raw_dimensions = normalization["raw_dimensions"]
        raw_hash = sha256_file(raw_path)
        if metadata.get("source_sha256") and metadata["source_sha256"] != raw_hash:
            raise RealT2IUnavailable(f"provenance hash mismatch for {source_id}")
        with Image.open(normalized_path) as image:
            source = image.convert("RGB")
        target = TargetPixelSpec(**MATERIALS[material]["target"])
        fingerprint = fingerprint_image(source, target_feature_scale_px=target.preferred_px)
        gate = evaluate_source_gate(
            source,
            material_id=material,
            target_pixel_spec=target,
            constraints=SourceConstraints(),
            source_id=source_id,
            allow_rejected_probe=True,
        )
        probe = run_compile_probe(
            source,
            source_root / "compile_probe",
            material_id=material,
            variants=config.compiled_variants_per_source,
            palette_budget=config.palette_budget,
            seed=config.seed,
            map_columns=config.map_columns,
            map_rows=config.map_rows,
        )
        semantic_probe = _run_semantic_probe(material, source, source_root / "semantic_probe", config, normalized_path)
        generation = {
            **metadata,
            "origin": "real_t2i",
            "source_sha256": raw_hash,
            "raw_dimensions": raw_dimensions,
            "actual_dimensions": raw_dimensions,
            "normalization": normalization,
        }
        card = MaterialSourceCard(
            source_id=source_id,
            material_id=material,
            material_class=str(MATERIALS[material]["material_class"]),
            source_file=str(normalized_path),
            prompt_file=str(item["prompt_file"]),
            generation=GenerationMetadata(
                generator=str(metadata.get("generator", "unknown_real_t2i")),
                generation_date=str(metadata.get("generated_at", "")) or None,
                prompt_version="real-t2i-material-qualification-v1",
                source_dimensions=tuple(raw_dimensions),
                seed=config.seed,
                status="real_t2i_imported",
            ),
            target_pixel_spec=target,
            constraints=SourceConstraints(),
            fingerprint=fingerprint,
            validation=gate.as_dict(),
            compiler={**probe.metrics, "semantic_probe": semantic_probe},
            status=gate.status,
            human_review=HumanReview(),
        )
        save_json(card.as_dict(), source_root / "source_card.json")
        record = {
            "source_id": source_id,
            "material_id": material,
            "material_class": MATERIALS[material]["material_class"],
            "status": gate.status,
            "fingerprint": fingerprint,
            "gate": gate.as_dict(),
            "generation": generation,
            "compile_probe": probe.metrics,
            "semantic_probe": semantic_probe,
            "source_quality_score": None,
            "artifacts": {
                "candidate_root": str(source_root),
                "source_raw": str(raw_path),
                "source_normalized": str(normalized_path),
                "source_card": str(source_root / "source_card.json"),
                "compile_probe": str(probe.output_root),
                "pixel_tiles": [str(path) for path in probe.pixel_tile_paths],
                "map_probe": str(probe.map_path),
            },
        }
        save_json(record, source_root / "record.json")
        return record

    @staticmethod
    def _attach_duplicate_results(records: list[dict[str, Any]], pairs: list[dict[str, Any]], root: Path) -> None:
        by_id = {record["source_id"]: record for record in records}
        for record in records:
            matches = [pair for pair in pairs if record["source_id"] in {pair["left"], pair["right"]}]
            record["duplicate_gate"] = {
                "status": "generation_retry_required" if matches else "clear",
                "near_duplicate_pairs": matches,
            }
            save_json(record, Path(record["artifacts"]["candidate_root"]) / "record.json")
        save_json(pairs, root / "summary" / "near_duplicate_pairs.json")
        save_json(
            {record_id: by_id[record_id].get("duplicate_gate", {}) for record_id in by_id},
            root / "summary" / "duplicate_gate.json",
        )

    @staticmethod
    def _attach_diversity_scores(records: list[dict[str, Any]], root: Path) -> dict[str, float]:
        scores: dict[str, float] = {}
        for material in sorted({str(record["material_id"]) for record in records}):
            family = [record for record in records if record["material_id"] == material]
            score = candidate_diversity_score([Path(record["artifacts"]["source_normalized"]) for record in family])
            scores[material] = score
            for record in family:
                record["candidate_diversity_score"] = score
                save_json(record, Path(record["artifacts"]["candidate_root"]) / "record.json")
        save_json(scores, root / "summary" / "candidate_diversity.json")
        return scores

    @staticmethod
    def _write_provisional(material: str, ranked: list[dict[str, Any]], root: Path) -> None:
        for item in ranked[:2]:
            source_id = str(item["source_id"])
            destination = root / "provisional" / material / source_id
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(Path(item["artifacts"]["candidate_root"]), destination)

    @staticmethod
    def _current_library_baseline(config: RealMaterialQualificationConfig) -> dict[str, Any]:
        index = config.library_root / "index.json"
        if not index.exists():
            return {"status": "missing", "library_root": str(config.library_root)}
        data = _read_json(index)
        return {
            "status": "available",
            "library_root": str(config.library_root),
            "preferred_sources": data.get("preferred_sources", {}),
        }

    @staticmethod
    def _write_current_vs_real_comparison(
        config: RealMaterialQualificationConfig,
        rankings: dict[str, tuple[dict[str, Any], ...]],
        root: Path,
    ) -> None:
        width, height = 220, 250
        board = Image.new("RGBA", (width * 2, height * max(1, len(config.materials))), (24, 24, 28, 255))
        draw = ImageDraw.Draw(board)
        comparisons: dict[str, Any] = {}
        for row, material in enumerate(config.materials):
            ranked = rankings.get(material, ())
            if not ranked:
                continue
            real_record = ranked[0]
            real_path = Path(real_record["artifacts"]["source_normalized"])
            current_source = _preferred_library_source(config.library_root, material)
            comparisons[material] = {
                "real_source_id": real_record["source_id"],
                "real_source": str(real_path),
                "current_library_source": str(current_source) if current_source else None,
            }
            for column, path in enumerate((real_path, current_source)):
                x = column * width
                if path and Path(path).exists():
                    with Image.open(path) as image:
                        preview = image.convert("RGBA")
                        preview.thumbnail((width, height - 34), Image.Resampling.NEAREST)
                        board.paste(preview, (x, row * height))
                draw.text((x + 4, row * height + height - 28), "real_top" if column == 0 else "current_library", fill=(255, 255, 255, 255))
            draw.text((4, row * height + 4), material, fill=(255, 255, 255, 255))
        save_png(board, root / "summary" / "current_vs_real_comparison.png")
        save_json(comparisons, root / "summary" / "current_vs_real_comparison.json")

    @staticmethod
    def _write_compiler_sensitivity_controls(
        config: RealMaterialQualificationConfig,
        rankings: dict[str, tuple[dict[str, Any], ...]],
        root: Path,
    ) -> None:
        palettes = tuple(dict.fromkeys((max(4, config.palette_budget - 4), config.palette_budget, min(32, config.palette_budget + 4))))
        summary: dict[str, Any] = {}
        for material, ranked in rankings.items():
            if not ranked:
                continue
            selections = {"best_source_default": ranked[0], "worst_source_control": ranked[-1]}
            material_summary: dict[str, Any] = {}
            for label, item in selections.items():
                source_path = Path(item["artifacts"]["source_normalized"])
                controls: dict[str, Any] = {}
                with Image.open(source_path) as image:
                    source = image.convert("RGB")
                for palette in palettes:
                    output_root = root / "controls" / material / str(item["source_id"]) / f"palette_{palette}"
                    probe = run_compile_probe(
                        source,
                        output_root,
                        material_id=material,
                        variants=min(2, config.compiled_variants_per_source),
                        palette_budget=palette,
                        seed=config.seed,
                        map_columns=config.map_columns,
                        map_rows=config.map_rows,
                    )
                    controls[str(palette)] = {
                        "compile_fitness_score": probe.metrics["compile_fitness_score"],
                        "map_fitness_score": probe.metrics["map_fitness_score"],
                        "downstream_fitness_score": 0.58 * float(probe.metrics["compile_fitness_score"]) + 0.42 * float(probe.metrics["map_fitness_score"]),
                        "output_root": str(output_root),
                    }
                material_summary[label] = {"source_id": item["source_id"], "controls": controls}
            best_control = max(
                material_summary["worst_source_control"]["controls"].values(),
                key=lambda value: float(value["downstream_fitness_score"]),
            )
            material_summary["comparison"] = {
                "best_source_default_library_fitness": ranked[0].get("library_fitness_score"),
                "worst_source_best_control_downstream": best_control["downstream_fitness_score"],
                "best_source_wins": float(ranked[0].get("library_fitness_score", 0.0)) > float(best_control["downstream_fitness_score"]),
            }
            summary[material] = material_summary
        save_json(summary, root / "summary" / "compiler_sensitivity.json")

    @staticmethod
    def _write_blocked_outputs(root: Path, manifest: list[dict[str, Any]], missing: tuple[str, ...]) -> None:
        save_json({"status": "blocked_real_t2i_unavailable", "missing_sources": list(missing)}, root / "generation" / "status.json")
        save_json(manifest, root / "generation" / "generation_manifest.json")
        (root / "summary").mkdir(parents=True, exist_ok=True)
        (root / "summary" / "limitations.md").write_text(
            "# Real t2i Material Qualification: blocked\n\n"
            "Real t2i source images are required. No deterministic adapter or pseudo-candidate was generated.\n\n"
            f"Missing candidates: {', '.join(missing)}\n",
            encoding="utf-8",
        )

    @staticmethod
    def _write_report(root: Path, config: RealMaterialQualificationConfig, records: list[dict[str, Any]], dominance: dict[str, Any]) -> None:
        lines = [
            "# Real t2i Material Source Qualification Study",
            "",
            "All processed candidates carry `origin=real_t2i`; no deterministic fallback is used.",
            "",
            f"- Candidates: {len(records)}",
            f"- Materials: {', '.join(config.materials)}",
            "- Human review: pending unless imported with `import-material-review`",
            "",
            "## Source dominance",
            "",
            "```json",
            json.dumps(dominance, ensure_ascii=False, indent=2),
            "```",
            "",
        ]
        (root / "summary" / "report.md").write_text("\n".join(lines), encoding="utf-8")
        (root / "summary" / "source_dominance_report.md").write_text(
            "# Source Dominance Report\n\n"
            + "\n".join(
                f"- {material}: {value.get('classification')} / source_effect_ratio={value.get('source_effect_ratio')}"
                for material, value in dominance.items()
                if material != "overall"
            )
            + "\n",
            encoding="utf-8",
        )


def real_prompt_matrix(materials: Iterable[str], candidates_per_material: int = 8) -> list[dict[str, Any]]:
    """Return the explicit real-source generation matrix, including control 08."""
    rows: list[dict[str, Any]] = []
    axes = {
        "grass": ["stationary fine", "stationary medium", "sparse medium clusters", "subtle local variation", "coarse grass groups", "slight dry variation", "moderate contrast", "illustrative negative control"],
        "dirt": ["fine compact soil", "medium warm soil", "subtle granular aggregate", "dry dusty soil", "darker moist earth", "lightly worn earth variation", "stronger aggregate contrast", "illustrative negative control"],
        "water": ["calm stationary blue", "calm medium blue", "subtle broad directional flow", "darker deep water", "shallow restrained variation", "broad soft highlight bands", "stronger but controlled flow texture", "reflective illustrative negative control"],
        "stone": ["fine natural stone grain", "medium stone grain", "broad subtle stone planes", "dark natural rock", "light granite-like material", "restrained layered stone", "stronger fractured variation", "illustrative negative control"],
    }
    for material in materials:
        for number, axis in enumerate(axes[material][:candidates_per_material], start=1):
            source_id = f"{material}_real_{number:02d}"
            row = {
                "source_id": source_id,
                "material": material,
                "candidate_number": number,
                "experiment_axis": axis,
                "illustrative_control": number == 8,
                "prompt": _real_prompt(material, axis, number == 8),
                "target_pixel_spec": dict(MATERIALS[material]["target"]),
            }
            rows.append(row)
    return rows


def semantic_probe_kind(material: str) -> str:
    return {
        "grass": "surface",
        "dirt": "road_renderer",
        "water": "river_renderer",
        "stone": "object_material",
    }[material]


def import_real_material_sources(study_root: Path, input_root: Path, generator: str, model: str = "unknown") -> dict[str, Any]:
    """Import independently generated PNGs without overwriting existing raw files."""
    study_root = Path(study_root)
    input_root = Path(input_root)
    manifest_path = study_root / "generation" / "generation_manifest.json"
    if not manifest_path.exists():
        raise ValueError(f"generation manifest is missing: {manifest_path}")
    manifest_value = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest_value, list):
        raise ValueError(f"expected generation manifest array: {manifest_path}")
    manifest = manifest_value
    imported: list[str] = []
    skipped_existing: list[str] = []
    for item in manifest:
        source_id = str(item["source_id"])
        matches = list(input_root.rglob(f"{source_id}.png")) + list(input_root.rglob(f"{source_id}_*.png"))
        if not matches:
            continue
        source = matches[0]
        destination = Path(item["raw_source_file"])
        if destination.exists():
            if sha256_file(source) == sha256_file(destination):
                skipped_existing.append(source_id)
                continue
            raise FileExistsError(f"refusing to overwrite immutable raw source: {destination}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        raw_hash = sha256_file(destination)
        save_json(
            {
                "origin": "real_t2i",
                "generator": generator,
                "model": model,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "source_sha256": raw_hash,
                "imported_from": str(source),
            },
            destination.parent / "generation.json",
        )
        imported.append(source_id)
    return {
        "imported": imported,
        "skipped_existing": skipped_existing,
        "count": len(imported),
        "study_root": str(study_root),
    }


def normalize_source(raw_path: Path, normalized_path: Path, working_size: int = 1024) -> dict[str, Any]:
    raw_path = Path(raw_path)
    with Image.open(raw_path) as image:
        raw_dimensions = [int(image.width), int(image.height)]
        normalized = ImageOps.fit(image.convert("RGB"), (working_size, working_size), method=Image.Resampling.LANCZOS, centering=(0.5, 0.5))
    save_png(normalized, Path(normalized_path))
    return {
        "raw_dimensions": raw_dimensions,
        "working_dimensions": [working_size, working_size],
        "normalization_method": "fit_rgb_lanczos",
        "source_sha256": sha256_file(raw_path),
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def detect_near_duplicates(paths: list[Path], phash_hamming_threshold: int = 4) -> list[dict[str, Any]]:
    signatures = [(Path(path), _image_signatures(Path(path))) for path in paths]
    pairs: list[dict[str, Any]] = []
    for index, (left_path, left) in enumerate(signatures):
        for right_path, right in signatures[index + 1 :]:
            phash_distance = int(np.count_nonzero(left["phash"] != right["phash"]))
            color_distance = float(np.linalg.norm(left["color"] - right["color"]))
            frequency_distance = float(np.linalg.norm(left["frequency"] - right["frequency"]))
            if phash_distance <= phash_hamming_threshold or (color_distance < 0.02 and frequency_distance < 0.08):
                pairs.append({
                    "left": _candidate_label(left_path),
                    "right": _candidate_label(right_path),
                    "phash_hamming_distance": phash_distance,
                    "color_distance": round(color_distance, 8),
                    "frequency_distance": round(frequency_distance, 8),
                    "status": "generation_retry_required",
                })
    return pairs


def candidate_diversity_score(paths: list[Path]) -> float:
    signatures = [_image_signatures(Path(path)) for path in paths]
    if len(signatures) < 2:
        return 0.0
    distances: list[float] = []
    for index, left in enumerate(signatures):
        for right in signatures[index + 1 :]:
            phash = float(np.count_nonzero(left["phash"] != right["phash"])) / 256.0
            color = min(1.0, float(np.linalg.norm(left["color"] - right["color"])) * 12.0)
            frequency = min(1.0, float(np.linalg.norm(left["frequency"] - right["frequency"])) * 4.0)
            distances.append((phash + color + frequency) / 3.0)
    return round(float(np.mean(distances)), 6)


def analyze_source_dominance(records: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for material in sorted({str(record["material_id"]) for record in records}):
        family = [record for record in records if record["material_id"] == material]
        between_values = np.asarray([float(record.get("downstream_fitness_score", _downstream_score(record))) for record in family], dtype=float)
        within_values = [
            np.asarray(record.get("compile_probe", {}).get("variant_compile_fitness_scores", []), dtype=float)
            for record in family
        ]
        within_variances = [float(np.var(values)) for values in within_values if len(values) > 0]
        between = float(np.var(between_values)) if len(between_values) else 0.0
        within = float(np.mean(within_variances)) if within_variances else 0.0
        total = between + within
        ratio = between / total if total > 1e-12 else 0.0
        result[material] = {
            "material": material,
            "source_count": len(family),
            "variants_per_source": max((len(values) for values in within_values), default=0),
            "between_source_variance": round(between, 9),
            "within_source_variant_variance": round(within, 9),
            "source_effect_ratio": round(ratio, 6),
            "classification": _classify_effect(ratio),
        }
    if result:
        result["overall"] = {
            "source_effect_ratio": round(float(np.mean([value["source_effect_ratio"] for key, value in result.items() if key != "overall"])), 6),
            "classification": _classify_effect(float(np.mean([value["source_effect_ratio"] for key, value in result.items() if key != "overall"]))),
        }
    return result


def cross_material_analysis(records: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = (
        "stationarity_score",
        "feature_scale_error",
        "lighting_bias_score",
        "low_frequency_pattern_strength",
        "autocorrelation_peak_risk",
        "edge_density",
        "map_fitness_score",
    )
    by_material: dict[str, Any] = {}
    for material in sorted({str(record["material_id"]) for record in records}):
        family = [record for record in records if record["material_id"] == material]
        by_material[material] = {
            metric: round(float(np.mean([_metric_value(record, metric) for record in family])), 6)
            if family else None
            for metric in metrics
        }
    downstream = np.asarray([_downstream_score(record) for record in records], dtype=float)
    correlations: dict[str, float | None] = {}
    for metric in metrics[:-1]:
        values = np.asarray([_metric_value(record, metric) for record in records], dtype=float)
        correlations[metric] = None if len(values) < 2 or np.std(values) < 1e-12 or np.std(downstream) < 1e-12 else round(float(np.corrcoef(values, downstream)[0, 1]), 6)
    return {
        "interpretation": "exploratory; material-specific and cross-material trends are not causal evidence",
        "by_material": by_material,
        "cross_material_correlations": correlations,
    }


def create_blind_review(study_root: Path, records: list[dict[str, Any]], seed: int = 42) -> dict[str, Any]:
    study_root = Path(study_root)
    review_root = study_root / "blind_review"
    assets_root = review_root / "assets"
    assets_root.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    mapping: dict[str, dict[str, str]] = {}
    manifest: list[dict[str, Any]] = []
    rows: list[dict[str, str]] = []
    alphabet = list("ABCDEFGHJKLMNPQRSTUVWXYZ")
    for material in sorted({str(record["material_id"]) for record in records}):
        family = [record for record in records if record["material_id"] == material]
        rng.shuffle(family)
        for index, record in enumerate(family):
            review_id = f"{material[:1].upper()}-{alphabet[index]}"
            candidate_root = Path(record["artifacts"]["candidate_root"])
            source = candidate_root / "source_normalized.png"
            tile = Path(record["artifacts"].get("pixel_tiles", [candidate_root / "compile_probe" / "pixel_tiles" / "pixel_v01.png"])[0])
            map_path = Path(record["artifacts"].get("map_probe", candidate_root / "compile_probe" / "map_probe.png"))
            blind_source = assets_root / f"{review_id}_source.png"
            blind_tile = assets_root / f"{review_id}_tile.png"
            blind_map = assets_root / f"{review_id}_map.png"
            shutil.copy2(source, blind_source)
            shutil.copy2(tile, blind_tile)
            shutil.copy2(map_path, blind_map)
            mapping[review_id] = {"source_id": str(record["source_id"]), "material_id": material}
            manifest.append({
                "review_id": review_id,
                "material": material,
                "source_asset": str(blind_source.relative_to(review_root)),
                "tile_asset": str(blind_tile.relative_to(review_root)),
                "map_asset": str(blind_map.relative_to(review_root)),
            })
            rows.append({
                "review_id": review_id,
                "source_visual_rating": "",
                "compiled_tile_rating": "",
                "map_rating": "",
                "notes": "",
            })
    save_json(manifest, review_root / "blind_review_manifest.json")
    save_json(mapping, review_root / "review_id_mapping.json")
    with (review_root / "blind_review_sheet.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["review_id", "source_visual_rating", "compiled_tile_rating", "map_rating", "notes"])
        writer.writeheader()
        writer.writerows(rows)
    _write_blind_board(review_root, manifest)
    return {"review_root": str(review_root), "count": len(manifest)}


def import_material_review(study_root: Path, csv_path: Path) -> dict[str, Any]:
    study_root = Path(study_root)
    review_root = study_root / "blind_review"
    mapping = _read_json(review_root / "review_id_mapping.json")
    imported = 0
    records: list[dict[str, Any]] = []
    with Path(csv_path).open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            review_id = str(row.get("review_id", ""))
            target = mapping.get(review_id)
            if not target:
                raise ValueError(f"unknown blind review id: {review_id}")
            review = {
                "rating": _optional_rating(row.get("source_visual_rating")),
                "accepted": None,
                "notes": row.get("notes") or None,
                "tile_rating": _optional_rating(row.get("compiled_tile_rating")),
                "map_rating": _optional_rating(row.get("map_rating")),
            }
            candidate_root = _find_candidate_root(study_root, target["material_id"], target["source_id"])
            if candidate_root is None:
                raise ValueError(f"candidate not found for blind review id: {review_id}")
            card_path = candidate_root / "source_card.json"
            record_path = candidate_root / "record.json"
            card = _read_json(card_path)
            card["human_review"] = {"rating": review["rating"], "accepted": review["accepted"], "notes": review["notes"]}
            save_json(card, card_path)
            record = _read_json(record_path)
            record["human_review"] = review
            save_json(record, record_path)
            records.append(record)
            if any(value is not None for value in (review["rating"], review["tile_rating"], review["map_rating"])):
                imported += 1
    calibration = _calibrate_gate_weights(records) if imported else "pending_human_review"
    save_json({"human_reviews_imported": imported, "gate_calibration": calibration}, review_root / "review_import_result.json")
    if isinstance(calibration, dict):
        save_json(calibration, study_root / "summary" / "recommended_gate_weights.json")
    return {"human_reviews_imported": imported, "gate_calibration": calibration}


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _resolve(base: Path, value: Any) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else base / path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _require_real_provenance(metadata: dict[str, Any], source_id: str) -> None:
    if metadata.get("origin") != "real_t2i":
        raise RealT2IUnavailable(f"real-t2i provenance is missing for {source_id}")


def _real_prompt(material: str, axis: str, illustrative: bool) -> str:
    negative = {
        "grass": "no path, no rocks, no tree, no flower focal point, no landscape composition, no horizon, no perspective, no strong cast shadow",
        "dirt": "no completed road, no wheel tracks as subject, no grass field, no rock object, no footprints, no landscape composition",
        "water": "no shoreline, no riverbank, no rocks, no waterfall, no bridge, no foam landmark, no sun reflection focal point, no landscape composition",
        "stone": "no completed boulder object, no stone wall, no brick, no paving layout, no cliff panorama, no landscape composition",
    }[material]
    return (
        f"High-resolution strict top-down orthographic {material} material exemplar. "
        f"Experiment axis: {axis}. Continuous texture-synthesis-friendly field, neutral diffuse lighting, "
        f"no focal point, no center composition, no embedded topology, {negative}. "
        + ("Illustrative negative control: visually attractive and higher contrast while retaining the material semantic. " if illustrative else "")
        + "No text, watermark, border, or perspective."
    )


def _run_semantic_probe(
    material: str,
    source: Image.Image,
    output_root: Path,
    config: RealMaterialQualificationConfig,
    source_path: Path,
) -> dict[str, Any]:
    kind = semantic_probe_kind(material)
    output_root.mkdir(parents=True, exist_ok=True)
    if kind == "surface":
        return {"kind": kind, "status": "covered_by_compile_map_probe"}
    if kind == "road_renderer":
        from pixel_tile_compiler.transition_network.road_graph import RoadGraphStudyRunner, load_road_graph_config

        road_config = load_road_graph_config(_repo_root() / "experiments" / "road_graph_study.yaml")
        road_config.output_root = output_root
        road_config.sources["dirt_road"] = source_path
        result = RoadGraphStudyRunner().run(road_config)
        return {
            "kind": kind,
            "status": "completed",
            "comparison_path": str(result.comparison_path),
            "metrics_path": str(result.metrics_path),
        }
    if kind == "river_renderer":
        from pixel_tile_compiler.transition_network.river_study import RiverGraphStudyRunner, load_river_graph_config

        river_config = load_river_graph_config(_repo_root() / "experiments" / "river_network_study.yaml")
        river_config.output_root = output_root
        river_config.sources["water"] = source_path
        result = RiverGraphStudyRunner().run(river_config)
        return {
            "kind": kind,
            "status": "completed",
            "comparison_path": str(result.comparison_path),
            "metrics_path": str(result.metrics_path),
        }
    from pixel_tile_compiler.config import CompilerConfig
    from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler

    result = PixelTileCompiler().compile_image(
        source,
        CompilerConfig(
            output_root=output_root,
            palette_budget=config.palette_budget,
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
        source_name=f"{material}:object_material",
    )
    return {"kind": kind, "status": "completed", "final_path": str(result.final_path)}


def _image_signatures(path: Path) -> dict[str, np.ndarray]:
    with Image.open(path) as image:
        gray = np.asarray(image.convert("L").resize((16, 16), Image.Resampling.BILINEAR), dtype=np.float32)
        rgb = np.asarray(image.convert("RGB").resize((32, 32), Image.Resampling.BILINEAR), dtype=np.float32) / 255.0
    phash = gray >= float(gray.mean())
    histogram = np.concatenate([np.histogram(rgb[:, :, channel], bins=16, range=(0.0, 1.0), density=True)[0] for channel in range(3)])
    histogram = histogram / max(1e-9, float(np.linalg.norm(histogram)))
    spectrum = np.abs(np.fft.fftshift(np.fft.fft2(gray - gray.mean())))
    spectrum = spectrum / max(1e-9, float(np.linalg.norm(spectrum)))
    return {"phash": phash.reshape(-1), "color": histogram, "frequency": spectrum.reshape(-1)[::8]}


def _candidate_label(path: Path) -> str:
    return path.parent.name if path.stem == "source_normalized" else path.stem


def _downstream_score(record: dict[str, Any]) -> float:
    metrics = record.get("compile_probe", {})
    return 0.58 * float(metrics.get("compile_fitness_score", 0.0)) + 0.42 * float(metrics.get("map_fitness_score", 0.0))


def _metric_value(record: dict[str, Any], metric: str) -> float:
    if metric == "map_fitness_score":
        return float(record.get("compile_probe", {}).get(metric, 0.0))
    return float(record.get("fingerprint", {}).get(metric, 0.0))


def _classify_effect(ratio: float) -> str:
    if ratio >= 0.66:
        return "Source-dominant"
    if ratio <= 0.33:
        return "Downstream-dominant"
    return "Mixed"


def _optional_rating(value: str | None) -> int | None:
    if value is None or not value.strip():
        return None
    rating = int(value)
    if not 1 <= rating <= 5:
        raise ValueError("human rating must be between 1 and 5")
    return rating


def _find_candidate_root(study_root: Path, material: str, source_id: str) -> Path | None:
    for candidate_root in (
        study_root / material / "candidates" / source_id,
        study_root / "provisional" / material / source_id,
    ):
        if (candidate_root / "record.json").exists():
            return candidate_root
    return None


def _preferred_library_source(library_root: Path, material: str) -> Path | None:
    family_path = Path(library_root) / material / "family.json"
    if not family_path.exists():
        return None
    family = _read_json(family_path)
    preferred = family.get("preferred_source")
    if not preferred:
        return None
    source = Path(library_root) / material / "accepted" / str(preferred) / "source.png"
    return source if source.exists() else None


def _calibrate_gate_weights(records: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [record for record in records if record.get("human_review", {}).get("map_rating") is not None]
    if len(usable) < 3:
        return {"status": "exploratory_insufficient_samples", "n": len(usable), "weights": {}}
    target = np.asarray([float(record["human_review"]["map_rating"]) for record in usable], dtype=float)
    metrics = ("stationarity_score", "estimated_feature_scale_px", "lighting_bias_score", "autocorrelation_peak_risk", "center_dominance_score")
    correlations: dict[str, float | None] = {}
    for metric in metrics:
        values = np.asarray([float(record.get("fingerprint", {}).get(metric, 0.0)) for record in usable], dtype=float)
        correlations[metric] = _spearman(values, target)
    return {"status": "exploratory_recommendation", "n": len(usable), "weights": correlations, "does_not_overwrite_gate": True}


def _spearman(left: np.ndarray, right: np.ndarray) -> float | None:
    if len(left) < 2 or np.std(left) < 1e-12 or np.std(right) < 1e-12:
        return None
    left_rank = np.argsort(np.argsort(left)).astype(float)
    right_rank = np.argsort(np.argsort(right)).astype(float)
    return round(float(np.corrcoef(left_rank, right_rank)[0, 1]), 6)


def _write_blind_board(review_root: Path, manifest: list[dict[str, Any]]) -> None:
    width, height = 180, 140
    board = Image.new("RGBA", (width * 3, height * len(manifest)), (24, 24, 28, 255))
    draw = ImageDraw.Draw(board)
    for row, item in enumerate(manifest):
        for column, key in enumerate(("source_asset", "tile_asset", "map_asset")):
            path = review_root / item[key]
            with Image.open(path) as image:
                preview = image.convert("RGBA")
                preview.thumbnail((width, height - 24), Image.Resampling.NEAREST)
                board.paste(preview, (column * width, row * height))
        draw.text((4, row * height + height - 20), item["review_id"], fill=(255, 255, 255, 255))
    save_png(board, review_root / "blind_review_board.png")
