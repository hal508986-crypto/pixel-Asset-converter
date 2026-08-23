from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from pixel_tile_compiler.pixel_grammar.profiles import HierarchyMode, PixelGrammarProfile
from pixel_tile_compiler.pixel_hierarchy import PixelHierarchyStudyConfig
from pixel_tile_compiler.pixel_hierarchy.runner import PixelHierarchyStudyRunner
from pixel_tile_compiler.pixel_hierarchy.volume import VolumePass


def _source(path: Path) -> Path:
    y, x = np.mgrid[0:96, 0:96]
    image = Image.fromarray(
        np.dstack(
            [
                (50 + x * 2).astype(np.uint8),
                (90 + y).astype(np.uint8),
                (45 + (x + y) // 3).astype(np.uint8),
            ]
        ),
        mode="RGB",
    )
    image.save(path)
    return path


def test_hierarchy_profile_round_trips_volume_contract() -> None:
    profile = PixelGrammarProfile(
        name="river_volumetric",
        semantic_role="network",
        material="river",
        hierarchy_mode=HierarchyMode.VOLUMETRIC,
        major_mass_strength=0.95,
        medium_cluster_strength=0.70,
        shading_layers=4,
        depth_cue_strength=0.65,
        contact_shadow_strength=0.45,
        highlight_strength=0.40,
        micro_detail_strength=0.18,
    )

    restored = PixelGrammarProfile.from_dict(profile.to_dict())

    assert restored == profile
    assert restored.hierarchy_mode is HierarchyMode.VOLUMETRIC
    assert restored.micro_detail_strength < restored.depth_cue_strength


def test_volume_pass_is_deterministic_and_not_high_frequency_noise() -> None:
    image = Image.new("RGBA", (96, 96), (80, 120, 70, 255))
    mask = np.zeros((96, 96), dtype=bool)
    mask[28:68, 12:84] = True
    profile = PixelGrammarProfile(
        name="road_volumetric",
        semantic_role="network",
        material="dirt_road",
        hierarchy_mode=HierarchyMode.VOLUMETRIC,
        major_mass_strength=0.9,
        medium_cluster_strength=0.6,
        shading_layers=4,
        depth_cue_strength=0.6,
        contact_shadow_strength=0.5,
        highlight_strength=0.35,
        micro_detail_strength=0.15,
    )

    first = VolumePass().apply(image, profile, target="dirt_road", semantic_mask=mask, seed=42)
    second = VolumePass().apply(image, profile, target="dirt_road", semantic_mask=mask, seed=42)

    assert np.array_equal(np.asarray(first), np.asarray(second))
    assert not np.array_equal(np.asarray(first), np.asarray(image))


def test_hierarchy_config_reads_modes_and_lighting(tmp_path: Path) -> None:
    grass = _source(tmp_path / "grass.png")
    config = PixelHierarchyStudyConfig.from_mapping(
        {
            "study": {
                "output_root": "out",
                "targets": ["grass"],
                "hierarchy_modes": ["flat", "structured", "volumetric"],
                "sources": {"grass": str(grass)},
                "lighting": {"direction": [-1, -1], "strength": 0.5},
                "pixel": {"palette_budget": 24, "enabled": False},
            }
        },
        base_dir=tmp_path,
    )

    assert config.modes == tuple(HierarchyMode)
    assert config.lighting.direction == (-1.0, -1.0)
    assert config.output_root == tmp_path / "out"


def test_hierarchy_runner_writes_three_modes_and_metrics(tmp_path: Path) -> None:
    grass = _source(tmp_path / "grass.png")
    config = PixelHierarchyStudyConfig(
        output_root=tmp_path / "out",
        targets=("grass",),
        sources={"grass": grass},
        pixelize=False,
        generate_demo_maps=True,
        generate_unit_preview=True,
    )

    result = PixelHierarchyStudyRunner().run(config)

    assert len(result.records) == 3
    assert {record["hierarchy_mode"] for record in result.records} == {"flat", "structured", "volumetric"}
    assert all("visual_hierarchy_score" in record["metrics"]["tile"] for record in result.records)
    assert (config.output_root / "maps" / "grass_volumetric.png").exists()
    assert result.comparison_board_path.exists()
