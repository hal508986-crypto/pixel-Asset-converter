from pathlib import Path

from PIL import Image, ImageDraw

from pixel_tile_compiler.source_study.models import SourceCandidate, SourceStudyConfig
from pixel_tile_compiler.source_study.prompts import default_prompt_matrix, render_source_prompt, write_prompt_set
from pixel_tile_compiler.source_study.runner import SourceStudyRunner
from pixel_tile_compiler.source_study.scoring import rank_source_records
from pixel_tile_compiler.tileset.material import analyze_material


def make_source(path: Path, *, periodic: bool = False) -> None:
    image = Image.new("RGBA", (96, 96), (72, 132, 62, 255))
    draw = ImageDraw.Draw(image)
    if periodic:
        for y in range(0, 96, 24):
            draw.rectangle((0, y, 95, y + 8), fill=(45, 92, 42, 255))
    else:
        for index in range(10):
            x = (index * 17) % 90
            y = (index * 23) % 90
            draw.line((x, y, x + 3, y - 4), fill=(50, 110, 48, 255), width=1)
    image.save(path)


def test_prompt_matrix_has_eight_intentional_candidates_and_required_constraints():
    matrix = default_prompt_matrix()

    assert len(matrix) >= 8
    prompt = render_source_prompt(matrix[0])
    assert "top-down grass material" in prompt
    assert "no focal point" in prompt
    assert "texture synthesis" in prompt


def test_prompt_set_writes_matrix_and_one_prompt_per_candidate(tmp_path: Path):
    prompt_root = tmp_path / "prompts"

    matrix_path = write_prompt_set(prompt_root)

    assert matrix_path.exists()
    assert len(list(prompt_root.glob("*.txt"))) >= 8


def test_material_analysis_exposes_low_frequency_and_autocorrelation_risk(tmp_path: Path):
    uniform_path = tmp_path / "uniform.png"
    periodic_path = tmp_path / "periodic.png"
    make_source(uniform_path)
    make_source(periodic_path, periodic=True)

    uniform = analyze_material(Image.open(uniform_path))
    periodic = analyze_material(Image.open(periodic_path))

    assert periodic.metrics.low_frequency_pattern_strength > uniform.metrics.low_frequency_pattern_strength
    assert periodic.metrics.autocorrelation_peak_risk >= uniform.metrics.autocorrelation_peak_risk


def test_source_study_ranking_returns_top_and_worst_sources():
    records = [
        {"source_id": "good", "score": 0.9},
        {"source_id": "middle", "score": 0.5},
        {"source_id": "bad", "score": 0.1},
    ]

    ranking = rank_source_records(records)

    assert ranking[0]["source_id"] == "good"
    assert ranking[-1]["source_id"] == "bad"


def test_source_study_runner_compiles_each_source_and_writes_summary(tmp_path: Path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    first = source_root / "grass_a.png"
    second = source_root / "grass_b.png"
    make_source(first)
    make_source(second, periodic=True)
    candidates = (
        SourceCandidate("grass_a", "high", "low", "minimal", "none", "low", first),
        SourceCandidate("grass_b", "low", "high", "medium", "illustrative", "high", second),
    )
    config = SourceStudyConfig(
        output_root=tmp_path / "study",
        source_root=source_root,
        prompt_root=tmp_path / "prompts",
        candidates=candidates,
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
    assert (config.output_root / "summary" / "source_ranking.json").exists()
    assert (config.output_root / "summary" / "source_comparison_table.json").exists()
    assert (config.output_root / "summary" / "best_sources.md").exists()
    assert (config.output_root / "summary" / "best_prompt_template.txt").exists()
    assert (config.output_root / "summary" / "montage.png").exists()
    assert (config.output_root / "compiled" / "grass_a" / "source.png").exists()
    assert result.ranking[0]["source_id"] in {"grass_a", "grass_b"}
