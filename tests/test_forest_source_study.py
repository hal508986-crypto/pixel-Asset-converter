from pathlib import Path

from PIL import Image, ImageDraw

from pixel_tile_compiler.source_study.models import SourceCandidate, SourceStudyConfig
from pixel_tile_compiler.source_study.prompts import default_prompt_matrix, render_source_prompt
from pixel_tile_compiler.source_study.runner import SourceStudyRunner
from pixel_tile_compiler.tileset.material import analyze_material


def make_canopy_source(path: Path, *, large_mass: bool) -> None:
    image = Image.new("RGBA", (128, 128), (48, 86, 42, 255))
    draw = ImageDraw.Draw(image)
    if large_mass:
        draw.ellipse((10, 12, 116, 108), fill=(28, 56, 32, 255))
        draw.ellipse((45, 32, 126, 124), fill=(36, 70, 34, 255))
    else:
        for y in range(8, 128, 20):
            for x in range(8, 128, 20):
                draw.ellipse((x, y, min(127, x + 8), min(127, y + 8)), fill=(25, 52, 30, 255))
    image.save(path)


def test_forest_prompt_matrix_covers_canopy_axes_and_required_constraints():
    matrix = default_prompt_matrix(material="forest_canopy")

    assert len(matrix) >= 8
    prompt = render_source_prompt(matrix[0], material="forest_canopy")
    assert "top-down continuous forest canopy" in prompt
    assert "no hero tree" in prompt
    assert "no clearing" in prompt
    assert "texture synthesis" in prompt


def test_forest_config_accepts_forest_canopy_material():
    config = SourceStudyConfig.from_mapping(
        {
            "study": {
                "material": "forest_canopy",
                "source_candidates": [{"id": "forest_src_a"}],
            }
        }
    )

    assert config.material == "forest_canopy"
    assert config.candidates[0].canopy_density == "medium"


def test_canopy_metrics_distinguish_large_mass_from_fragmented_canopy(tmp_path: Path):
    large_path = tmp_path / "large.png"
    fragmented_path = tmp_path / "fragmented.png"
    make_canopy_source(large_path, large_mass=True)
    make_canopy_source(fragmented_path, large_mass=False)

    large = analyze_material(Image.open(large_path), material="forest_canopy")
    fragmented = analyze_material(Image.open(fragmented_path), material="forest_canopy")

    assert large.metrics.large_mass_dominance_score > fragmented.metrics.large_mass_dominance_score
    assert fragmented.metrics.canopy_fragmentation_score > large.metrics.canopy_fragmentation_score


def test_forest_runner_writes_forest_metrics_and_summary(tmp_path: Path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    first = source_root / "forest_a.png"
    second = source_root / "forest_b.png"
    make_canopy_source(first, large_mass=False)
    make_canopy_source(second, large_mass=True)
    candidates = (
        SourceCandidate(
            source_id="forest_a",
            homogeneity="slightly varied",
            brightness_variation="medium",
            tufts="",
            composition="no focal point",
            contrast="medium",
            source_path=first,
            canopy_density="medium",
            cluster_scale="small",
            illustrative=False,
        ),
        SourceCandidate(
            source_id="forest_b",
            homogeneity="strongly varied",
            brightness_variation="high",
            tufts="",
            composition="illustrative",
            contrast="high",
            source_path=second,
            canopy_density="dense",
            cluster_scale="large",
            illustrative=True,
        ),
    )
    config = SourceStudyConfig(
        output_root=tmp_path / "study",
        source_root=source_root,
        prompt_root=tmp_path / "prompts",
        candidates=candidates,
        material="forest_canopy",
        tileset_options={
            "variants": 3,
            "edge_types": 2,
            "palette": 8,
            "shared_palette": True,
            "preview_cols": 2,
            "preview_rows": 2,
            "source_tile_size": 64,
            "patch_size": 24,
            "patch_overlap": 6,
            "strip_width": 8,
        },
    )

    result = SourceStudyRunner().run(config)

    assert len(result.records) == 2
    assert result.skipped == ()
    metrics = result.records[0]["validation"]["metrics"]
    assert "canopy_cluster_scale_score" in metrics
    assert "canopy_fragmentation_score" in metrics
    assert "large_mass_dominance_score" in metrics
    assert "cluster_continuity_score" in result.records[0]["tileset"]["source_compiler_tileset"]
    assert "forest canopy" in (config.output_root / "summary" / "best_prompt_template.txt").read_text(encoding="utf-8")
    assert (config.output_root / "summary" / "montage.png").exists()
