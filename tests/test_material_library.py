from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from pixel_tile_compiler.material_library.fingerprint import fingerprint_image
from pixel_tile_compiler.material_library.gate import evaluate_source_gate
from pixel_tile_compiler.material_library.config import MaterialLibraryStudyConfig
from pixel_tile_compiler.material_library.models import MaterialSourceCard, TargetPixelSpec
from pixel_tile_compiler.material_library.probe import run_compile_probe
from pixel_tile_compiler.material_library.ranking import rank_candidates
from pixel_tile_compiler.material_library.resolver import MaterialLibrary
from pixel_tile_compiler.material_library.runner import MaterialLibraryRunner


def make_material_source(size: tuple[int, int] = (128, 128)) -> Image.Image:
    image = Image.new("RGB", size, (84, 132, 68))
    draw = ImageDraw.Draw(image)
    for y in range(0, size[1], 13):
        draw.line((0, y, size[0], y + 3), fill=(100, 151, 76), width=2)
    for x in range(7, size[0], 19):
        draw.ellipse((x, 22, x + 7, 29), fill=(54, 104, 55))
    return image


def test_material_source_card_round_trips_and_keeps_human_review_null():
    card = MaterialSourceCard(
        source_id="grass_v01",
        material_id="grass",
        material_class="surface",
        source_file="source.png",
        prompt_file="prompt.txt",
        target_pixel_spec=TargetPixelSpec(2, 4, 8),
    )

    restored = MaterialSourceCard.from_dict(card.as_dict())

    assert restored.source_id == "grass_v01"
    assert restored.human_review.rating is None
    assert restored.status == "candidate"


def test_fingerprint_is_deterministic_and_reports_stationarity_and_scale():
    source = make_material_source()

    first = fingerprint_image(source, patch_grid=4, target_feature_scale_px=4)
    second = fingerprint_image(source, patch_grid=4, target_feature_scale_px=4)

    assert first == second
    assert 0.0 <= first["stationarity_score"] <= 1.0
    assert first["estimated_feature_scale_px"] > 0
    assert len(first["patch_statistics"]) == 16


def test_source_gate_can_reject_a_strong_landmark_but_probe_is_allowed():
    source = Image.new("RGB", (128, 128), (64, 100, 54))
    draw = ImageDraw.Draw(source)
    draw.ellipse((18, 18, 112, 112), fill=(220, 190, 80))

    report = evaluate_source_gate(
        source,
        material_id="grass",
        target_pixel_spec=TargetPixelSpec(2, 4, 8),
        allow_rejected_probe=True,
    )

    assert report.status in {"warning", "rejected"}
    assert report.accepted_for_probe is True
    assert "center_dominance_score" in report.metrics


def test_compile_probe_exports_64_pixel_tiles_and_10x10_map(tmp_path: Path):
    source = make_material_source()
    result = run_compile_probe(
        source,
        output_root=tmp_path / "probe",
        material_id="grass",
        variants=2,
        palette_budget=8,
        seed=42,
        map_columns=10,
        map_rows=10,
    )

    assert len(result.pixel_tile_paths) == 2
    assert all(Image.open(path).size == (64, 64) for path in result.pixel_tile_paths)
    assert Image.open(result.map_path).size == (640, 640)
    assert result.metrics["palette_usage"] <= 8


def test_ranking_is_stable_and_promotion_writes_preferred_resolver(tmp_path: Path):
    records = [
        {"source_id": "grass_v01", "material_id": "grass", "source_quality_score": 0.8, "compile_fitness_score": 0.9, "map_fitness_score": 0.8, "downstream_fitness_score": 0.85},
        {"source_id": "grass_v02", "material_id": "grass", "source_quality_score": 0.7, "compile_fitness_score": 0.7, "map_fitness_score": 0.7, "downstream_fitness_score": 0.7},
    ]

    ranked = rank_candidates(records)

    assert ranked[0]["rank"] == 1
    assert ranked[0]["source_id"] == "grass_v01"

    library_root = tmp_path / "material_library"
    (library_root / "grass" / "accepted" / "grass_v01").mkdir(parents=True)
    (library_root / "grass" / "accepted" / "grass_v01" / "source.png").write_bytes(b"png")
    (library_root / "grass" / "family.json").write_text(
        '{"material_id":"grass","preferred_source":"grass_v01","accepted_sources":["grass_v01"]}',
        encoding="utf-8",
    )
    (library_root / "index.json").write_text(
        '{"version":"0.1","families":["grass"]}', encoding="utf-8"
    )

    resolver = MaterialLibrary(library_root)
    assert resolver.get_preferred_source("grass").source_id == "grass_v01"
    assert resolver.get_sources("grass")[0].source_id == "grass_v01"
    try:
        resolver.get_family("unknown")
    except KeyError:
        pass
    else:
        raise AssertionError("unknown material must fail explicitly")


def test_runner_writes_card_family_index_and_promoted_source(tmp_path: Path):
    config = MaterialLibraryStudyConfig(
        output_root=tmp_path / "e2e",
        library_root=tmp_path / "library",
        materials=("grass",),
        candidates_per_material=1,
        promote_top=1,
        compile_variants=1,
        palette_budget=8,
    )

    result = MaterialLibraryRunner().run(config)

    assert len(result.records) == 1
    assert (tmp_path / "e2e" / "candidates" / "grass" / "grass_v01_uniform_dark" / "source_card.json").exists()
    assert (tmp_path / "library" / "grass" / "family.json").exists()
    assert (tmp_path / "library" / "index.json").exists()
    assert result.rankings["grass"][0]["rank"] == 1
    preferred = MaterialLibrary(tmp_path / "library").get_preferred_source("grass")
    assert preferred.source_id == "grass_v01_uniform_dark"
    assert "accepted" in preferred.source_file
