from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from pixel_tile_compiler.pixel_grammar import (
    DensityLevel,
    PixelGrammarProfile,
    PixelGrammarStudyConfig,
)
from pixel_tile_compiler.pixel_grammar.density import apply_density
from pixel_tile_compiler.pixel_grammar.metrics import compute_map_metrics, frequency_band_metrics
from pixel_tile_compiler.pixel_grammar.runner import PixelGrammarStudyRunner


def _synthetic_source(path: Path) -> Path:
    y, x = np.mgrid[0:96, 0:96]
    red = (40 + (x * 3 + y * 2) % 150).astype(np.uint8)
    green = (80 + (x * 2 + y * 5) % 130).astype(np.uint8)
    blue = (35 + (x * 7 + y) % 110).astype(np.uint8)
    image = Image.fromarray(np.dstack([red, green, blue]), mode="RGB")
    image.save(path)
    return path


def test_pixel_grammar_profile_round_trips_density_contract() -> None:
    profile = PixelGrammarProfile(
        name="balanced_surface",
        semantic_role="surface",
        material="grass",
        preferred_density=DensityLevel.BALANCED,
        preferred_frequency="low_to_medium",
        major_cluster_scale="medium",
        micro_detail_level=0.45,
        silhouette_priority=0.15,
        topology_priority=0.0,
        shading_strength=0.35,
        texture_strength=0.55,
        transition_band_emphasis=0.0,
    )

    payload = profile.to_dict()
    restored = PixelGrammarProfile.from_dict(payload)

    assert restored == profile
    assert payload["preferred_density"] == "balanced"


def test_density_variants_are_deterministic_and_distinct(tmp_path: Path) -> None:
    source = Image.open(_synthetic_source(tmp_path / "source.png"))

    sparse_a = apply_density(source, DensityLevel.SPARSE, seed=42)
    sparse_b = apply_density(source, DensityLevel.SPARSE, seed=42)
    detailed = apply_density(source, DensityLevel.DETAILED, seed=42)

    assert np.array_equal(np.asarray(sparse_a), np.asarray(sparse_b))
    assert not np.array_equal(np.asarray(sparse_a), np.asarray(detailed))


def test_frequency_and_map_metrics_expose_study_bands() -> None:
    source = Image.fromarray(np.tile(np.array([[32, 96, 160]], dtype=np.uint8), (64, 1)), mode="L").convert("RGB")
    bands = frequency_band_metrics(source)
    preview = Image.new("RGB", (128, 128), (80, 110, 70))

    assert set(bands) >= {"low_frequency_energy", "medium_frequency_energy", "high_frequency_energy"}
    assert abs(sum(bands[name] for name in ("low_frequency_energy", "medium_frequency_energy", "high_frequency_energy")) - 1.0) < 1e-5
    assert set(compute_map_metrics(preview)) >= {"grid_visibility_score", "map_periodicity_risk", "map_readability_score"}


def test_study_config_accepts_subset_and_serializes_paths(tmp_path: Path) -> None:
    source = _synthetic_source(tmp_path / "grass.png")
    config = PixelGrammarStudyConfig(
        output_root=tmp_path / "out",
        targets=("grass", "tree_object"),
        levels=(DensityLevel.SPARSE, DensityLevel.BALANCED, DensityLevel.DETAILED),
        sources={"grass": source},
        generate_demo_maps=False,
        pixelize=False,
    )

    payload = config.to_dict()

    study = payload["study"]
    assert study["targets"] == ["grass", "tree_object"]
    assert study["levels"] == ["sparse", "balanced", "detailed"]
    assert study["sources"]["grass"] == str(source)


def test_study_runner_writes_tiles_metrics_and_recommendation(tmp_path: Path) -> None:
    source = _synthetic_source(tmp_path / "grass.png")
    config = PixelGrammarStudyConfig(
        output_root=tmp_path / "out",
        targets=("grass", "tree_object"),
        levels=(DensityLevel.SPARSE, DensityLevel.BALANCED, DensityLevel.DETAILED),
        sources={"grass": source},
        generate_demo_maps=False,
        pixelize=False,
    )

    result = PixelGrammarStudyRunner().run(config)

    assert len(result.records) == 6
    assert result.recommended_profiles["grass"]["density_level"] in {"sparse", "balanced", "detailed"}
    assert (config.output_root / "tiles" / "grass" / "balanced" / "tile.png").exists()
    assert (config.output_root / "metrics" / "grammar_metrics.json").exists()
    assert result.comparison_board_path.exists()
