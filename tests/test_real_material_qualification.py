import csv
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from pixel_tile_compiler.material_library.real_qualification import (
    RealMaterialQualificationConfig,
    RealMaterialQualificationRunner,
    RealT2IUnavailable,
    analyze_source_dominance,
    candidate_diversity_score,
    create_blind_review,
    detect_near_duplicates,
    import_material_review,
    normalize_source,
    semantic_probe_kind,
    sha256_file,
)


def _write_real_source(root: Path, source_id: str, color: tuple[int, int, int]) -> Path:
    candidate_root = root / "generation" / "grass" / "real_sources" / source_id
    candidate_root.mkdir(parents=True)
    raw = candidate_root / "source_raw.png"
    Image.new("RGB", (32, 24), color).save(raw)
    raw_hash = sha256_file(raw)
    (candidate_root / "generation.json").write_text(
        json.dumps(
            {
                "origin": "real_t2i",
                "generator": "test-imagegen",
                "model": "test-model",
                "source_sha256": raw_hash,
            }
        ),
        encoding="utf-8",
    )
    return raw


def test_real_config_requires_real_generation_and_disables_fallback():
    config = RealMaterialQualificationConfig()

    assert config.real_t2i_required is True
    assert config.fallback_adapter is False
    assert config.human_review_blind is True
    assert config.auto_promote_without_review is False


def test_semantic_probe_kind_matches_material_usage():
    assert semantic_probe_kind("grass") == "surface"
    assert semantic_probe_kind("dirt") == "road_renderer"
    assert semantic_probe_kind("water") == "river_renderer"
    assert semantic_probe_kind("stone") == "object_material"


def test_runner_stops_with_queue_when_raw_real_sources_are_missing(tmp_path: Path):
    config = RealMaterialQualificationConfig(
        output_root=tmp_path / "study",
        materials=("grass",),
        real_candidates_per_material=1,
        compiled_variants_per_source=1,
    )

    result = RealMaterialQualificationRunner().run(config)

    assert result.status == "blocked_real_t2i_unavailable"
    assert result.records == ()
    assert (tmp_path / "study" / "generation" / "generation_manifest.json").exists()
    assert (tmp_path / "study" / "summary" / "limitations.md").exists()
    assert not list((tmp_path / "study").glob("**/compile_probe"))


def test_normalization_preserves_raw_and_is_deterministic(tmp_path: Path):
    raw = tmp_path / "source_raw.png"
    Image.new("RGB", (24, 16), (10, 20, 30)).save(raw)
    before = raw.read_bytes()

    normalized = normalize_source(raw, tmp_path / "source_normalized.png", working_size=32)

    assert normalized["raw_dimensions"] == [24, 16]
    assert normalized["normalization_method"] == "fit_rgb_lanczos"
    assert Image.open(tmp_path / "source_normalized.png").size == (32, 32)
    assert raw.read_bytes() == before


def test_duplicate_gate_and_diversity_measure_real_candidates(tmp_path: Path):
    first = tmp_path / "grass_real_01.png"
    second = tmp_path / "grass_real_02.png"
    third = tmp_path / "grass_real_03.png"
    Image.new("RGB", (32, 32), (80, 120, 70)).save(first)
    Image.new("RGB", (32, 32), (80, 120, 70)).save(second)
    Image.new("RGB", (32, 32), (190, 60, 40)).save(third)

    pairs = detect_near_duplicates([first, second, third])

    assert pairs
    assert {pairs[0]["left"], pairs[0]["right"]} == {first.stem, second.stem}
    assert candidate_diversity_score([first, second, third]) > 0


def test_source_dominance_compares_between_and_within_variance():
    records = [
        {
            "material_id": "grass",
            "source_id": "grass_real_01",
            "downstream_fitness_score": 0.2,
            "compile_probe": {"variant_compile_fitness_scores": [0.19, 0.21]},
        },
        {
            "material_id": "grass",
            "source_id": "grass_real_02",
            "downstream_fitness_score": 0.8,
            "compile_probe": {"variant_compile_fitness_scores": [0.79, 0.81]},
        },
    ]

    result = analyze_source_dominance(records)["grass"]

    assert result["between_source_variance"] > result["within_source_variant_variance"]
    assert result["source_effect_ratio"] > 0.9
    assert result["classification"] == "Source-dominant"


def test_blind_review_and_import_keep_blank_ratings_null(tmp_path: Path):
    study_root = tmp_path / "study"
    candidate_root = study_root / "grass" / "candidates" / "grass_real_01"
    (candidate_root / "compile_probe" / "pixel_tiles").mkdir(parents=True)
    Image.new("RGB", (32, 32), (80, 120, 70)).save(candidate_root / "source_normalized.png")
    Image.new("RGB", (64, 64), (80, 120, 70)).save(candidate_root / "compile_probe" / "pixel_tiles" / "pixel_v01.png")
    Image.new("RGB", (64, 64), (80, 120, 70)).save(candidate_root / "compile_probe" / "map_probe.png")
    (candidate_root / "source_card.json").write_text(
        json.dumps({"source_id": "grass_real_01", "human_review": {"rating": None, "accepted": None, "notes": None}}),
        encoding="utf-8",
    )
    (candidate_root / "record.json").write_text(
        json.dumps({"source_id": "grass_real_01", "material_id": "grass", "artifacts": {"candidate_root": str(candidate_root)}}),
        encoding="utf-8",
    )
    records = [{"source_id": "grass_real_01", "material_id": "grass", "artifacts": {"candidate_root": str(candidate_root)}}]

    create_blind_review(study_root, records, seed=42)
    sheet = study_root / "blind_review" / "blind_review_sheet.csv"
    rows = list(csv.DictReader(sheet.open(encoding="utf-8", newline="")))

    assert rows[0]["source_visual_rating"] == ""
    assert rows[0]["compiled_tile_rating"] == ""
    assert rows[0]["map_rating"] == ""

    result = import_material_review(study_root, sheet)

    assert result["human_reviews_imported"] == 0
    card = json.loads((candidate_root / "source_card.json").read_text(encoding="utf-8"))
    assert card["human_review"]["rating"] is None
    assert result["gate_calibration"] == "pending_human_review"


def test_runner_promotes_only_to_provisional_and_requires_provenance(tmp_path: Path):
    study_root = tmp_path / "study"
    raw = _write_real_source(study_root, "grass_real_01", (80, 120, 70))
    config = RealMaterialQualificationConfig(
        output_root=study_root,
        materials=("grass",),
        real_candidates_per_material=1,
        compiled_variants_per_source=1,
        normalization_working_size=32,
        palette_budget=8,
    )

    result = RealMaterialQualificationRunner().run(config)

    assert result.status == "completed"
    assert result.records[0]["generation"]["origin"] == "real_t2i"
    assert result.records[0]["generation"]["source_sha256"] == sha256_file(raw)
    assert (study_root / "provisional" / "grass" / "grass_real_01").exists()
    assert not (tmp_path / "material_library" / "grass" / "accepted").exists()


def test_missing_provenance_is_not_accepted_as_real_t2i(tmp_path: Path):
    candidate_root = tmp_path / "study" / "generation" / "grass" / "real_sources" / "grass_real_01"
    candidate_root.mkdir(parents=True)
    Image.new("RGB", (16, 16), (80, 120, 70)).save(candidate_root / "source_raw.png")
    config = RealMaterialQualificationConfig(
        output_root=tmp_path / "study",
        materials=("grass",),
        real_candidates_per_material=1,
    )

    with pytest.raises(RealT2IUnavailable, match="provenance"):
        RealMaterialQualificationRunner().run(config)
