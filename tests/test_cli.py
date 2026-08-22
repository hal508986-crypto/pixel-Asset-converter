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
