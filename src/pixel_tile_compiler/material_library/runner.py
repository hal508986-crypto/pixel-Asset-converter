"""End-to-end Material Source Library v0.1 experiment runner."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageOps

from pixel_tile_compiler.io.exporter import save_json, save_png

from .config import MaterialLibraryStudyConfig
from .fingerprint import fingerprint_image
from .gate import evaluate_source_gate
from .models import GenerationMetadata, HumanReview, MaterialFamily, MaterialSourceCard, SourceConstraints, TargetPixelSpec
from .probe import run_compile_probe
from .prompts import MATERIALS, best_prompt_template, prompt_matrix, render_prompt, write_prompt_set
from .ranking import rank_candidates, source_downstream_correlations


@dataclass(frozen=True)
class MaterialLibraryStudyResult:
    output_root: Path
    library_root: Path
    records: tuple[dict[str, Any], ...]
    rankings: dict[str, tuple[dict[str, Any], ...]]


class MaterialLibraryRunner:
    """Run all material candidates under identical probe conditions."""

    def run(self, config: MaterialLibraryStudyConfig) -> MaterialLibraryStudyResult:
        root = Path(config.output_root)
        library_root = Path(config.library_root)
        root.mkdir(parents=True, exist_ok=True)
        library_root.mkdir(parents=True, exist_ok=True)
        matrix = [row for row in prompt_matrix(config.materials) if _candidate_number(row["id"]) <= config.candidates_per_material]
        write_prompt_set(root / "prompts", config.materials)
        write_prompt_set(library_root / "prompts", config.materials)
        save_json(config.as_dict(), root / "study_config.json")
        records: list[dict[str, Any]] = []
        for row in matrix:
            records.append(self._run_candidate(row, config, root, library_root))
        rankings: dict[str, tuple[dict[str, Any], ...]] = {}
        for material in config.materials:
            family_records = [record for record in records if record["material_id"] == material]
            ranked = rank_candidates(family_records)
            rankings[material] = tuple(ranked)
            self._write_family_outputs(material, ranked, config, root, library_root)
        save_json(records, root / "summary" / "source_comparison_table.json")
        save_json({material: list(values) for material, values in rankings.items()}, root / "summary" / "source_ranking.json")
        correlations = {material: source_downstream_correlations(list(values)) for material, values in rankings.items()}
        save_json(correlations, root / "summary" / "source_downstream_correlations.json")
        self._write_summary(root, rankings, correlations)
        self._write_global_index(library_root, config, rankings)
        return MaterialLibraryStudyResult(root, library_root, tuple(records), rankings)

    def _run_candidate(self, row: dict[str, Any], config: MaterialLibraryStudyConfig, root: Path, library_root: Path) -> dict[str, Any]:
        material = str(row["material_id"])
        source_id = str(row["id"])
        candidate_root = root / "candidates" / material / source_id
        candidate_root.mkdir(parents=True, exist_ok=True)
        prompt = render_prompt(material, row)
        prompt_path = candidate_root / "prompt.txt"
        prompt_path.write_text(prompt + "\n", encoding="utf-8")
        save_json(row, candidate_root / "candidate_spec.json")
        source, generation = self._source_for(material, source_id, row, candidate_root, config)
        source_path = save_png(source, candidate_root / "source.png")
        target = TargetPixelSpec(**MATERIALS[material]["target"])
        fingerprint = fingerprint_image(source, target_feature_scale_px=target.preferred_px)
        gate = evaluate_source_gate(
            source,
            material_id=material,
            target_pixel_spec=target,
            constraints=SourceConstraints(),
            source_id=source_id,
            allow_rejected_probe=config.allow_rejected_probe,
        ) if config.source_gate_enabled else None
        gate_dict: dict[str, Any] = gate.as_dict() if gate else {"status": "accepted", "accepted_for_probe": True, "warnings": [], "errors": [], "metrics": fingerprint}
        save_json(fingerprint, candidate_root / "fingerprint.json")
        save_json(gate_dict, candidate_root / "validation.json")
        probe = run_compile_probe(
            source,
            candidate_root / "compile_probe",
            material_id=material,
            variants=config.compile_variants,
            palette_budget=config.palette_budget,
            seed=config.seed,
            map_columns=config.map_columns,
            map_rows=config.map_rows,
        )
        card = MaterialSourceCard(
            source_id=source_id,
            material_id=material,
            material_class=str(MATERIALS[material]["material_class"]),
            source_file=str(source_path),
            prompt_file=str(prompt_path),
            generation=GenerationMetadata(generator=generation["generator"], source_dimensions=source.size, seed=config.seed, status=generation["status"]),
            target_pixel_spec=target,
            constraints=SourceConstraints(),
            fingerprint=fingerprint,
            validation=gate_dict,
            compiler=probe.metrics,
            status=gate_dict["status"],
            human_review=HumanReview(),
        )
        save_json(card.as_dict(), candidate_root / "source_card.json")
        record = {
            "source_id": source_id,
            "material_id": material,
            "material_class": MATERIALS[material]["material_class"],
            "source_file": str(source_path),
            "prompt_file": str(prompt_path),
            "status": gate_dict["status"],
            "fingerprint": fingerprint,
            "gate": gate_dict,
            "compile_probe": probe.metrics,
            "artifacts": {"candidate_root": str(candidate_root), "source": str(source_path), "compile_probe": str(probe.output_root), "pixel_tiles": [str(path) for path in probe.pixel_tile_paths], "map_probe": str(probe.map_path)},
        }
        save_json(record, candidate_root / "record.json")
        shutil.copytree(candidate_root, library_root / material / "candidates" / source_id, dirs_exist_ok=True)
        return record

    def _source_for(self, material: str, source_id: str, row: dict[str, Any], candidate_root: Path, config: MaterialLibraryStudyConfig) -> tuple[Image.Image, dict[str, str]]:
        override = config.source_overrides.get(material, {}).get(source_id)
        base_path = Path(override) if override else self._default_base(material, source_id)
        if not base_path.is_absolute():
            base_path = _repo_root() / base_path
        if base_path.exists():
            with Image.open(base_path) as image:
                base = image.convert("RGB")
            generator = "deterministic_existing_asset_adapter"
            status = "generated_from_existing_asset"
        else:
            base = _procedural_material(material, size=256, seed=_candidate_number(source_id))
            generator = "deterministic_procedural_adapter"
            status = "generated_without_external_t2i"
        index = max(0, _candidate_number(source_id) - 1)
        image = _material_variant(base, index, config.seed, material)
        return image, {"generator": generator, "status": status if not config.generation_enabled else "generation_backend_not_wired"}

    @staticmethod
    def _default_base(material: str, source_id: str) -> Path:
        number = _candidate_number(source_id)
        grass_sources = [
            "assets/source_experiments/source/grass_src_01_uniform_dark.png",
            "assets/source_experiments/source/grass_src_02_uniform_mid.png",
            "assets/source_experiments/source/grass_src_03_uniform_light.png",
            "assets/source_experiments/source/grass_src_04_sparse_tufts.png",
            "assets/source_experiments/source/grass_src_05_patchy_mid.png",
            "assets/source_experiments/source/grass_src_06_bright_variation.png",
            "assets/source_experiments/source/grass_src_07_dense_tufts.png",
            "assets/source_experiments/source/grass_src_08_illustrative.png",
        ]
        if material == "grass":
            return Path(grass_sources[min(number - 1, len(grass_sources) - 1)])
        if material == "dirt":
            return Path("assets/road_source_experiments/source/dirt_road_master.png")
        if material == "water":
            return Path("assets/river_source_experiments/source/water_master.png")
        return Path("assets/source/road_stone_1024.png")

    def _write_family_outputs(
        self,
        material: str,
        ranked: list[dict[str, Any]],
        config: MaterialLibraryStudyConfig,
        root: Path,
        library_root: Path,
    ) -> None:
        summary_root = root / "summary" / material
        summary_root.mkdir(parents=True, exist_ok=True)
        save_json(ranked, summary_root / "ranking.json")
        promote_ids = [item["source_id"] for item in ranked if item.get("status") != "rejected"][:config.promote_top]
        material_root = library_root / material
        material_root.mkdir(parents=True, exist_ok=True)
        for item in ranked:
            source_id = str(item["source_id"])
            candidate_root = Path(item["artifacts"]["candidate_root"])
            if item.get("status") == "rejected":
                shutil.copytree(candidate_root, material_root / "rejected" / source_id, dirs_exist_ok=True)
            elif source_id in promote_ids:
                accepted_root = material_root / "accepted" / source_id
                shutil.copytree(candidate_root, accepted_root, dirs_exist_ok=True)
                card_path = accepted_root / "source_card.json"
                if card_path.exists():
                    card = MaterialSourceCard.from_dict(_read_json(card_path))
                    card.source_file = str(accepted_root / "source.png")
                    card.prompt_file = str(accepted_root / "prompt.txt")
                    card.status = "accepted"
                    save_json(card.as_dict(), card_path)
        preferred = promote_ids[0] if promote_ids else None
        feature_values = [
            float(item.get("fingerprint", {}).get("estimated_feature_scale_px", 4.0))
            for item in ranked
            if item.get("source_id") in promote_ids
        ]
        family = MaterialFamily(
            material_id=material,
            material_class=str(MATERIALS[material]["material_class"]),
            accepted_sources=tuple(promote_ids),
            preferred_source=preferred,
            recommended_feature_scale_px=round(float(np.mean(feature_values)) if feature_values else 4.0, 6),
            recommended_renderer=str(MATERIALS[material]["renderer"]),
            recommended_palette_budget=config.palette_budget,
        )
        save_json(family.as_dict(), material_root / "family.json")
        save_json(family.as_dict(), summary_root / "family.json")
        (summary_root / "best_prompt_template.txt").write_text(best_prompt_template(material), encoding="utf-8")
        (material_root / "best_prompt_template.txt").write_text(best_prompt_template(material), encoding="utf-8")
        save_png(_comparison_board(ranked), summary_root / "comparison_board.png")

    @staticmethod
    def _write_summary(root: Path, rankings: dict[str, tuple[dict[str, Any], ...]], correlations: dict[str, Any]) -> None:
        lines = ["# Material Source Library v0.1 Summary", "", "Source gate and downstream probes are kept as separate signals.", ""]
        for material, ranked in rankings.items():
            lines.append(f"## {material}")
            if ranked:
                lines.append(f"- Top: `{ranked[0]['source_id']}` ({ranked[0]['library_fitness_score']:.4f})")
                lines.append(f"- Worst: `{ranked[-1]['source_id']}` ({ranked[-1]['library_fitness_score']:.4f})")
                lines.append(f"- Promoted: `{', '.join(item['source_id'] for item in ranked if item.get('status') != 'rejected' and item['rank'] <= 2)}`")
            lines.append("")
        lines.extend([
            "## Interpretation",
            "- Stationary, moderate-scale material fields tend to be safer library candidates than focal or macro-illustrative sources.",
            "- Compile and map fitness are downstream probes, not substitutes for human review.",
            "- Correlations are exploratory with n=8 per material and should not be treated as causal evidence.",
            "",
            "## Correlations",
            "```json",
        ])
        import json
        lines.append(json.dumps(correlations, ensure_ascii=False, indent=2))
        lines.extend(["```", ""])
        (root / "summary" / "report.md").write_text("\n".join(lines), encoding="utf-8")
        (root / "summary" / "limitations.md").write_text(
            "# Limitations\n\n"
            "- External image generation is not wired into the CLI; existing assets and deterministic procedural adapters fill candidate slots.\n"
            "- Source fingerprints are model-free approximations and do not identify actual objects.\n"
            "- Compile Probe uses the existing Pixel Compiler with lightweight variant adapters; it is not a full 12-variant Tileset Source Compiler run.\n"
            "- HumanReview remains null and needs artist assessment before production use.\n",
            encoding="utf-8",
        )

    @staticmethod
    def _write_global_index(library_root: Path, config: MaterialLibraryStudyConfig, rankings: dict[str, tuple[dict[str, Any], ...]]) -> None:
        save_json(
            {
                "version": config.version,
                "families": list(config.materials),
                "preferred_sources": {
                    material: next((item["source_id"] for item in ranked if item.get("status") != "rejected" and item["rank"] <= config.promote_top), None)
                    for material, ranked in rankings.items()
                },
                "resolver_api": ["get_family", "get_preferred_source", "get_sources"],
            },
            library_root / "index.json",
        )


def _candidate_number(source_id: str) -> int:
    try:
        return int(source_id.split("_v", 1)[1].split("_", 1)[0])
    except (IndexError, ValueError):
        return 1


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _read_json(path: Path) -> dict[str, Any]:
    import json
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _material_variant(base: Image.Image, index: int, seed: int, material: str) -> Image.Image:
    image = ImageOps.exif_transpose(base.convert("RGB"))
    image = ImageChops.offset(image, (index * 17 + seed) % max(1, image.width), (index * 23 + seed) % max(1, image.height))
    image = ImageEnhance.Brightness(image).enhance(0.94 + ((index * 7 + seed) % 10) / 100.0)
    image = ImageEnhance.Contrast(image).enhance(0.96 + ((index * 5 + seed) % 9) / 100.0)
    image = image.resize((256, 256), Image.Resampling.BICUBIC)
    if material == "water":
        image = ImageEnhance.Color(image).enhance(0.82)
    if material == "stone":
        image = ImageEnhance.Color(image).enhance(0.55)
    return image


def _procedural_material(material: str, size: int, seed: int) -> Image.Image:
    rng = np.random.default_rng(seed)
    height = width = size
    coarse = rng.normal(0, 1, (max(4, size // 16), max(4, size // 16)))
    coarse_image = Image.fromarray(np.uint8(np.clip((coarse - coarse.min()) / max(1e-6, float(np.ptp(coarse))) * 255, 0, 255)), mode="L")
    field = np.asarray(coarse_image.resize((width, height), Image.Resampling.BICUBIC), dtype=np.float32) / 255.0
    fine = rng.normal(0, 1, (height, width)).astype(np.float32)
    fine = (fine - fine.min()) / max(1e-6, float(np.ptp(fine)))
    if material == "grass":
        base = np.stack((55 + 38 * field, 105 + 48 * field, 48 + 30 * field), axis=2)
    elif material == "dirt":
        base = np.stack((92 + 55 * field, 60 + 37 * field, 35 + 25 * field), axis=2)
    elif material == "water":
        base = np.stack((26 + 25 * field, 78 + 65 * field, 112 + 72 * field), axis=2)
    else:
        base = np.stack((90 + 65 * field, 92 + 62 * field, 96 + 58 * field), axis=2)
    base += (fine[..., None] - 0.5) * 16.0
    return Image.fromarray(np.uint8(np.clip(base, 0, 255)), mode="RGB")


def _comparison_board(ranked: list[dict[str, Any]], thumbnail: tuple[int, int] = (180, 120)) -> Image.Image:
    width = thumbnail[0] * 3
    row_height = thumbnail[1] + 34
    board = Image.new("RGBA", (width, max(1, row_height * len(ranked))), (24, 24, 28, 255))
    draw = ImageDraw.Draw(board)
    for row, item in enumerate(ranked):
        root = Path(item["artifacts"]["candidate_root"])
        paths = (root / "source.png", root / "compile_probe" / "pixel_tiles" / "pixel_v01.png", root / "compile_probe" / "map_probe.png")
        for column, path in enumerate(paths):
            if path.exists():
                with Image.open(path) as image:
                    preview = image.convert("RGBA")
                    preview.thumbnail(thumbnail, Image.Resampling.NEAREST)
                    board.paste(preview, (column * thumbnail[0], row * row_height))
        label = f"#{item.get('rank', row + 1)} {item['source_id']}  lib={item.get('library_fitness_score', 0):.3f}  gate={item.get('status', '')}"
        draw.text((4, row * row_height + thumbnail[1] + 5), label, fill=(255, 255, 255, 255))
    return board
