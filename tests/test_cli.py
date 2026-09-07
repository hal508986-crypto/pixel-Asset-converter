import json
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


def test_cli_compile_accepts_character_profile_options(tmp_path: Path):
    source = tmp_path / "character.png"
    Image.new("RGBA", (64, 64), (40, 120, 200, 255)).save(source)
    output = tmp_path / "character-output"

    result = CliRunner().invoke(
        app,
        [
            "compile",
            str(source),
            "--output",
            str(output),
            "--palette",
            "32",
            "--tile-mode",
            "object",
            "--pixelization",
            "nearest",
            "--outline",
            "black",
            "--no-repeat-opt",
        ],
    )

    assert result.exit_code == 0, result.stdout
    metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["config"]["pixelization_mode"] == "nearest"
    assert metadata["config"]["outline_color"] == "black"
    assert Image.open(output / "final.png").size == (64, 64)


def test_cli_compile_character_purpose_uses_shared_character_defaults(tmp_path: Path):
    source = tmp_path / "character-purpose.png"
    image = Image.new("RGBA", (128, 128), (255, 255, 255, 255))
    for y in range(24, 104):
        for x in range(44, 84):
            image.putpixel((x, y), (60, 120, 220, 255))
    image.save(source)
    output = tmp_path / "character-purpose-output"

    result = CliRunner().invoke(
        app,
        ["compile", str(source), "--output", str(output), "--purpose", "character", "--palette", "16"],
    )

    assert result.exit_code == 0, result.stdout
    metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["config"]["tile_mode"] == "object"
    assert metadata["config"]["pixelization_mode"] == "nearest"
    assert metadata["config"]["repeat_opt_enabled"] is False
    assert metadata["config"]["dither"] == "off"


def test_cli_compile_accepts_character_detail_level(tmp_path: Path):
    source = tmp_path / "character-detail.png"
    Image.new("RGBA", (64, 64), (40, 120, 200, 255)).save(source)
    output = tmp_path / "character-detail-output"

    result = CliRunner().invoke(
        app,
        [
            "compile",
            str(source),
            "--output",
            str(output),
            "--purpose",
            "character",
            "--character-detail",
            "balanced",
        ],
    )

    assert result.exit_code == 0, result.stdout
    metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["config"]["character_detail_level"] == "balanced"
    assert metadata["character_detail"]["applied"] is True


def test_cli_compile_accepts_native_character_canvas_and_b24(tmp_path: Path):
    source = tmp_path / "character-native.png"
    Image.new("RGBA", (128, 128), (0, 0, 0, 0)).save(source)
    output = tmp_path / "character-native-output"

    result = CliRunner().invoke(
        app,
        [
            "compile",
            str(source),
            "--output",
            str(output),
            "--purpose",
            "character",
            "--width",
            "128",
            "--height",
            "128",
            "--preset",
            "b24",
        ],
    )

    assert result.exit_code == 0, result.stdout
    metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    assert Image.open(output / "final.png").size == (128, 128)
    assert metadata["config"]["palette_budget"] == 24
    assert metadata["config"]["character_detail_level"] == "balanced"


def test_cli_explicit_character_options_override_b24_defaults(tmp_path: Path):
    source = tmp_path / "character-b24-override.png"
    Image.new("RGBA", (128, 128), (40, 120, 200, 255)).save(source)
    output = tmp_path / "character-b24-override-output"

    result = CliRunner().invoke(
        app,
        [
            "compile",
            str(source),
            "--output",
            str(output),
            "--purpose",
            "character",
            "--preset",
            "b24",
            "--palette",
            "32",
            "--character-detail",
            "sparse",
        ],
    )

    assert result.exit_code == 0, result.stdout
    metadata = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["config"]["palette_budget"] == 32
    assert metadata["config"]["character_detail_level"] == "sparse"


def test_cli_rejects_non_64_terrain_canvas(tmp_path: Path):
    source = tmp_path / "terrain-native.png"
    Image.new("RGBA", (128, 128), (60, 120, 70, 255)).save(source)

    result = CliRunner().invoke(
        app,
        ["compile", str(source), "--purpose", "terrain", "--width", "128", "--height", "128"],
    )

    assert result.exit_code != 0
    assert "object/nearest" in result.output


def test_cli_help_lists_character_palette_density_study():
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "study-character-palette-density" in result.stdout


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
