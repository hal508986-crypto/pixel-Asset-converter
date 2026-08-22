"""Typer command-line interface."""

from pathlib import Path
from typing import Optional

import typer

from pixel_tile_compiler.config import CompilerConfig
from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler

app = typer.Typer(help="SRPG用64x64ピクセルアートMAPタイルコンパイラ")


@app.command()
def compile(
    source: Path = typer.Argument(..., exists=True, readable=True, help="入力画像PNG/JPEG/WebP"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="出力ディレクトリ"),
    palette: int = typer.Option(16, "--palette", min=4, max=32, help="パレット上限"),
    tile_mode: str = typer.Option("repeatable", "--tile-mode", help="repeatable/directional/object"),
    semantic: str = typer.Option("rule", "--semantic", help="rule/mcp"),
    seam: str = typer.Option("inspect", "--seam", help="off/inspect/correct"),
    background: str = typer.Option("alpha", "--background", help="alpha/auto/color"),
    background_color: Optional[str] = typer.Option(None, "--background-color"),
) -> None:
    """入力画像を64x64のMAPタイルへ変換します。"""
    output_dir = output or (Path("output") / source.stem)
    try:
        config = CompilerConfig(
            output_root=output_dir,
            palette_budget=palette,
            tile_mode=tile_mode,  # type: ignore[arg-type]
            semantic_provider=semantic,  # type: ignore[arg-type]
            seam_mode=seam,  # type: ignore[arg-type]
            background_mode=background,  # type: ignore[arg-type]
            background_color=background_color,
        )
        result = PixelTileCompiler().compile(source, config)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"完了: {result.final_path}")
    typer.echo(f"パレット: {result.metrics.actual_palette_count}色")


@app.command()
def gui() -> None:
    """GUIを起動します。"""
    from pixel_tile_compiler.gui.app import run

    run()


if __name__ == "__main__":
    app()
