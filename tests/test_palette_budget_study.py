from pathlib import Path

import numpy as np
from PIL import Image

from pixel_tile_compiler.config import CompilerConfig
from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler
from pixel_tile_compiler.palette_study import (
    ColorRamp,
    PaletteBudgetStudyConfig,
    PaletteBudgetStudyRunner,
    build_semantic_ramp,
    compute_palette_study_metrics,
    import_palette_review,
)


def test_palette_study_config_accepts_true_color_and_64_budget(tmp_path: Path):
    config = PaletteBudgetStudyConfig(
        output_root=tmp_path / "study",
        source_study_root=tmp_path / "real-study",
        palette_budgets=("true_color", 64, 32, 24, 16, 12, 8),
        palette_modes=("global_free", "per_material", "semantic_ramp"),
    )

    assert config.palette_budgets[0] == "true_color"
    assert 64 in config.palette_budgets
    assert config.freeze_source is True


def test_semantic_ramp_has_material_roles_and_monotonic_luminance():
    image = Image.new("RGB", (8, 8))
    pixels = image.load()
    for y in range(8):
        for x in range(8):
            value = 32 + ((x + y) * 12)
            pixels[x, y] = (value // 2, value, 32)

    ramp = build_semantic_ramp(image, budget=8, material_id="grass")

    assert isinstance(ramp, ColorRamp)
    assert ramp.material_id == "grass"
    assert len(ramp.colors) == len(ramp.roles) <= 8
    assert ramp.roles[0] == "deep_shadow"
    luminance = [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in ramp.colors]
    assert luminance == sorted(luminance)
    ramp_metrics = compute_palette_study_metrics(image, 8, "semantic_ramp", source_image=image, ramp=ramp)
    assert ramp_metrics["ramp_coherence_score"] is not None
    assert ramp_metrics["ramp_coherence_score"] >= 0.65


def test_palette_study_metrics_measure_redundancy_and_clusters():
    image = Image.new("RGB", (8, 8), (32, 32, 32))
    array = np.asarray(image).copy()
    array[2:6, 2:6] = (160, 160, 160)
    array[0, 0] = (161, 161, 161)
    image = Image.fromarray(array, mode="RGB")

    metrics = compute_palette_study_metrics(
        image,
        requested_budget=8,
        mode="global_free",
        source_image=image,
    )

    assert metrics["palette_size"] == 3
    assert metrics["effective_palette_size"] >= 2
    assert 0.0 <= metrics["cluster_coherence_score"] <= 1.0
    assert 0.0 <= metrics["cluster_fragmentation_score"] <= 1.0
    assert 0.0 <= metrics["isolated_pixel_ratio"] <= 1.0
    assert "material_identity_score" in metrics


def test_compiler_config_supports_64_color_control_and_true_color_switch():
    config = CompilerConfig(palette_budget=64, quantize_enabled=False)

    assert config.palette_budget == 64
    assert config.quantize_enabled is False


def test_true_color_skips_final_palette_quantizer(tmp_path: Path, monkeypatch):
    import pixel_tile_compiler.pipeline.compiler as compiler_module

    calls = 0
    original = compiler_module.quantize_palette

    def spy(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(compiler_module, "quantize_palette", spy)
    image = Image.new("RGB", (64, 64))
    pixels = image.load()
    for y in range(64):
        for x in range(64):
            pixels[x, y] = (x * 4, y * 4, (x + y) * 2)

    PixelTileCompiler().compile_image(
        image,
        CompilerConfig(
            output_root=tmp_path / "true-color",
            palette_budget=64,
            quantize_enabled=False,
            tile_mode="repeatable",
            seam_mode="off",
            repeat_opt_enabled=False,
            dither="off",
            background_mode="color",
            background_color="#000000",
            work_size=64,
            smoothing_enabled=False,
            debug_enabled=False,
        ),
    )

    assert calls == 1  # only the diagnostic bicubic baseline is quantized


def test_palette_review_import_updates_condition_without_promoting_profile(tmp_path: Path):
    import csv
    import json

    review_root = tmp_path / "study" / "blind_review"
    review_root.mkdir(parents=True)
    (review_root / "mapping.json").write_text(
        json.dumps({"P-001": {"material_id": "grass", "condition_id": "grass:global_free:24"}}),
        encoding="utf-8",
    )
    condition_root = tmp_path / "study" / "grass" / "global_free" / "budget_24"
    condition_root.mkdir(parents=True)
    (condition_root / "record.json").write_text(json.dumps({"condition_id": "grass:global_free:24"}), encoding="utf-8")
    csv_path = tmp_path / "review.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["review_id", "pixel_art_likeness", "material_readability", "map_quality", "preferred", "notes"],
        )
        writer.writeheader()
        writer.writerow(
            {
                "review_id": "P-001",
                "pixel_art_likeness": "4",
                "material_readability": "5",
                "map_quality": "3",
                "preferred": "yes",
                "notes": "clear ramp",
            }
        )

    result = import_palette_review(tmp_path / "study", csv_path)

    assert result["human_reviews_imported"] == 1
    record = json.loads((condition_root / "record.json").read_text(encoding="utf-8"))
    assert record["human_review"]["pixel_art_likeness"] == 4
    assert not (tmp_path / "study" / "summary" / "recommended_palette_profiles.json").exists()


def test_runner_freezes_real_source_and_writes_single_tile_manifest(tmp_path: Path):
    real_root = tmp_path / "real-study"
    candidate_root = real_root / "grass" / "candidates" / "grass_real_05"
    candidate_root.mkdir(parents=True)
    source = Image.new("RGB", (128, 128), (64, 128, 32))
    source_path = candidate_root / "source_normalized.png"
    source.save(source_path)
    import hashlib
    import json

    source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    (candidate_root / "record.json").write_text(
        json.dumps(
            {
                "source_id": "grass_real_05",
                "material_id": "grass",
                "generation": {"origin": "real_t2i", "source_sha256": source_sha},
            }
        ),
        encoding="utf-8",
    )
    config = PaletteBudgetStudyConfig(
        output_root=tmp_path / "palette-study",
        source_study_root=real_root,
        materials=("grass",),
        source_selection={"grass": "grass_real_05"},
        palette_budgets=(8,),
        palette_modes=("global_free",),
        variants=1,
        map_conditions=(),
        semantic_conditions=(),
        semantic_materials=(),
    )

    result = PaletteBudgetStudyRunner().run(config)

    assert result.status == "completed"
    manifest = json.loads((config.output_root / "source_manifest.json").read_text(encoding="utf-8"))
    assert manifest["grass"]["source_id"] == "grass_real_05"
    assert manifest["grass"]["source_sha256"] == source_sha
    assert (config.output_root / "grass" / "global_free" / "budget_8" / "metrics.json").exists()
    assert json.loads((config.output_root / "semantic_manifest.json").read_text(encoding="utf-8")) == []
