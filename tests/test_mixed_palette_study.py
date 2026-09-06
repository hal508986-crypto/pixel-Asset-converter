from __future__ import annotations

import hashlib
import json
import csv
from pathlib import Path

import numpy as np
from PIL import Image

from pixel_tile_compiler.mixed_palette_study import (
    MixedMaterialPaletteArchitectureStudyRunner,
    MixedMaterialPaletteStudyConfig,
    PaletteArchitecture,
    build_fixed_mixed_map,
    compute_mixed_palette_metrics,
    import_mixed_palette_review,
    resolve_palette_architecture,
)


MATERIALS = ("grass", "dirt", "water", "stone")


def _source(material: str, size: int = 96) -> Image.Image:
    image = Image.new("RGB", (size, size))
    pixels = image.load()
    bases = {
        "grass": (62, 126, 48),
        "dirt": (142, 91, 52),
        "water": (42, 106, 174),
        "stone": (122, 126, 132),
    }
    base = bases[material]
    for y in range(size):
        for x in range(size):
            variation = (x * 7 + y * 11 + (x * y) % 13) % 21 - 10
            pixels[x, y] = tuple(max(0, min(255, channel + variation)) for channel in base)
    return image


def _real_study_root(root: Path) -> Path:
    for material in MATERIALS:
        candidate = root / material / "candidates" / f"{material}_real_01"
        candidate.mkdir(parents=True)
        source_path = candidate / "source_normalized.png"
        _source(material).save(source_path)
        source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
        (candidate / "record.json").write_text(
            json.dumps(
                {
                    "source_id": f"{material}_real_01",
                    "material_id": material,
                    "generation": {"origin": "real_t2i", "source_sha256": source_sha},
                }
            ),
            encoding="utf-8",
        )
    return root


def _conditions() -> tuple[PaletteArchitecture, ...]:
    return (
        PaletteArchitecture("global_24", "global", global_budget=24),
        PaletteArchitecture("per_material_6x4", "per_material", per_material_budget=6),
        PaletteArchitecture("semantic_ramp_6shade", "semantic_ramp", semantic_shades=6),
        PaletteArchitecture("shared_core4_material5", "shared_core", shared_core_size=4, material_shades=5),
    )


def test_palette_architecture_requires_exactly_one_strategy_and_serializes_roles() -> None:
    condition = PaletteArchitecture("shared_core4_material5", "shared_core", shared_core_size=4, material_shades=5)

    assert condition.as_dict()["kind"] == "shared_core"
    assert condition.as_dict()["shared_core_size"] == 4
    assert condition.as_dict()["material_shades"] == 5

    try:
        PaletteArchitecture("invalid", "global")
    except ValueError as exc:
        assert "requires" in str(exc)
    else:
        raise AssertionError("invalid architecture unexpectedly accepted")


def test_fixed_mixed_map_contains_materials_transitions_and_network_masks() -> None:
    sources = {material: _source(material) for material in MATERIALS}

    mixed = build_fixed_mixed_map(sources, columns=10, rows=10, tile_size=64, seed=42)

    assert mixed.image.size == (640, 640)
    assert set(np.unique(mixed.material_map)) == set(MATERIALS)
    assert mixed.road_mask.any()
    assert mixed.river_body_mask.any()
    assert mixed.river_bank_mask.any()
    assert mixed.stone_mask.any()
    assert mixed.layout["road_graph"]
    assert mixed.layout["river_graph"]
    assert mixed.layout["material_counts"]["grass"] > mixed.layout["material_counts"]["stone"]


def test_architectures_share_geometry_but_allocate_colors_differently() -> None:
    sources = {material: _source(material) for material in MATERIALS}
    mixed = build_fixed_mixed_map(sources, columns=10, rows=10, tile_size=64, seed=42)
    resolved = [resolve_palette_architecture(condition, mixed.image, mixed.material_map, sources) for condition in _conditions()]

    assert all(item.material_map_shape == mixed.material_map.shape for item in resolved)
    assert resolved[0].global_palette
    assert resolved[1].material_palettes.keys() == set(MATERIALS)
    assert resolved[2].material_ramps.keys() == set(MATERIALS)
    assert resolved[3].shared_core is not None
    assert resolved[3].shared_core.roles == ("deep_shadow", "dark", "light", "highlight")
    assert len({tuple(item.image.getdata()) for item in resolved}) >= 3
    assert len({json.dumps(item.as_dict(), sort_keys=True) for item in resolved}) == len(resolved)


def test_mixed_metrics_separate_harmony_identity_and_efficiency() -> None:
    sources = {material: _source(material) for material in MATERIALS}
    mixed = build_fixed_mixed_map(sources, columns=10, rows=10, tile_size=64, seed=42)
    resolved = resolve_palette_architecture(_conditions()[-1], mixed.image, mixed.material_map, sources)

    metrics = compute_mixed_palette_metrics(resolved, mixed)

    for key in (
        "global_palette_harmony_score",
        "material_separation_score",
        "material_identity_loss_score",
        "shared_color_utilization",
        "cross_material_redundant_color_ratio",
        "palette_efficiency_score",
        "road_background_separation_score",
        "river_background_separation_score",
        "object_background_separation_score",
        "map_readability_score",
    ):
        assert key in metrics
        assert 0.0 <= metrics[key] <= 1.0
    assert metrics["effective_global_palette_size"] >= 1


def test_runner_writes_minimal_architecture_study_and_blind_board(tmp_path: Path) -> None:
    real_root = _real_study_root(tmp_path / "real-study")
    config = MixedMaterialPaletteStudyConfig(
        output_root=tmp_path / "mixed-study",
        source_study_root=real_root,
        source_selection={material: f"{material}_real_01" for material in MATERIALS},
        materials=MATERIALS,
        conditions=_conditions(),
        variants=1,
        map_columns=10,
        map_rows=10,
        seed=42,
    )

    result = MixedMaterialPaletteArchitectureStudyRunner().run(config)

    assert result.status == "completed"
    assert len(result.conditions) == 4
    assert (config.output_root / "source_manifest.json").exists()
    assert (config.output_root / "layout.json").exists()
    assert (config.output_root / "blind_review" / "board.png").exists()
    assert (config.output_root / "blind_review" / "review.csv").exists()
    assert (config.output_root / "summary" / "architecture_ranking.json").exists()
    assert (config.output_root / "summary" / "recommended_architecture.json").exists()
    assert all((config.output_root / "conditions" / condition.condition_id / "full_map.png").exists() for condition in _conditions())


def test_mixed_review_import_attaches_ratings_without_promotion(tmp_path: Path) -> None:
    study_root = tmp_path / "study"
    review_root = study_root / "blind_review"
    condition_root = study_root / "conditions" / "global_24"
    review_root.mkdir(parents=True)
    condition_root.mkdir(parents=True)
    (review_root / "mapping.json").write_text(json.dumps({"M-01": {"condition_id": "global_24"}}), encoding="utf-8")
    (condition_root / "record.json").write_text(json.dumps({"condition": {"condition_id": "global_24"}}), encoding="utf-8")
    csv_path = tmp_path / "review.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["review_id", "pixel_art_likeness", "map_harmony", "material_readability", "overall_map_quality", "favorite", "notes"],
        )
        writer.writeheader()
        writer.writerow({"review_id": "M-01", "pixel_art_likeness": "5", "map_harmony": "4", "material_readability": "5", "overall_map_quality": "4", "favorite": "yes", "notes": "clear"})

    result = import_mixed_palette_review(study_root, csv_path)

    assert result["human_reviews_imported"] == 1
    record = json.loads((condition_root / "record.json").read_text(encoding="utf-8"))
    assert record["human_review"]["map_harmony"] == 4
    assert result["auto_promotion"] is False
