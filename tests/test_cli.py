from pathlib import Path

from PIL import Image
from typer.testing import CliRunner

from pixel_tile_compiler.cli import app


def test_cli_compile_creates_output(tmp_path: Path):
    source = tmp_path / "source.png"
    Image.new("RGBA", (128, 128), (60, 120, 70, 255)).save(source)
    output = tmp_path / "output"

    result = CliRunner().invoke(app, ["compile", str(source), "--output", str(output)])

    assert result.exit_code == 0, result.stdout
    assert (output / "final.png").exists()


def test_cli_compile_map_creates_three_way_experiment(tmp_path: Path):
    source = tmp_path / "map.png"
    image = Image.new("RGBA", (256, 320), (80, 140, 60, 255))
    for x in range(96, 160):
        for y in range(320):
            image.putpixel((x, y), (150, 105, 65, 255))
    image.save(source)
    output = tmp_path / "map-output"

    result = CliRunner().invoke(
        app,
        [
            "compile-map",
            str(source),
            "--output",
            str(output),
            "--cols",
            "4",
            "--rows",
            "5",
            "--palette",
            "24",
            "--context",
            "1",
        ],
    )

    assert result.exit_code == 0, result.stdout
    assert (output / "baseline_global.png").exists()
    assert (output / "independent_tiles.png").exists()
    assert (output / "context_compiled.png").exists()
    assert (output / "comparison.png").exists()
    assert (output / "metrics.json").exists()


def test_cli_help_lists_study_transition_network():
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "study-transition-network" in result.stdout


def test_cli_help_lists_study_road_graph():
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "study-road-graph" in result.stdout


def test_cli_help_lists_study_river_graph():
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "study-river-graph" in result.stdout
