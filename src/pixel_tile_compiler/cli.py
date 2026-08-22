"""Typer command-line interface."""

from pathlib import Path
from typing import Optional

import typer

from pixel_tile_compiler.config import CompilerConfig, MapCompilerConfig
from pixel_tile_compiler.map.compiler import MapExperimentRunner
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
    repeat_opt: bool = typer.Option(True, "--repeat-opt/--no-repeat-opt", help="repeatable最適化"),
    repeat_opt_strength: float = typer.Option(0.5, "--repeat-opt-strength", min=0.0, max=1.0),
    repeat_opt_edge_band: int = typer.Option(6, "--repeat-opt-edge-band", min=1, max=32),
    center_suppression: float = typer.Option(0.4, "--center-suppression", min=0.0, max=1.0),
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
            repeat_opt_enabled=repeat_opt,
            repeat_opt_strength=repeat_opt_strength,
            repeat_opt_edge_band=repeat_opt_edge_band,
            center_suppression_strength=center_suppression,
            background_mode=background,  # type: ignore[arg-type]
            background_color=background_color,
        )
        result = PixelTileCompiler().compile(source, config)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"完了: {result.final_path}")
    typer.echo(f"パレット: {result.metrics.actual_palette_count}色")
    if result.metrics.periodicity_risk_score is not None:
        typer.echo(f"周期リスク: {result.metrics.periodicity_risk_score:.3f}")


@app.command("compile-map")
def compile_map(
    source: Path = typer.Argument(..., exists=True, readable=True, help="入力MAP画像PNG/JPEG/WebP"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="実験出力ディレクトリ"),
    cols: int = typer.Option(4, "--cols", min=1, help="MAP列数"),
    rows: int = typer.Option(5, "--rows", min=1, help="MAP行数"),
    palette: int = typer.Option(24, "--palette", min=16, max=32, help="共有palette上限（16/24/32）"),
    context: int = typer.Option(1, "--context", min=0, help="周辺contextのtile幅"),
    shared_palette: bool = typer.Option(True, "--shared-palette/--no-shared-palette", help="MAP全体paletteを共有"),
    tile_mode: str = typer.Option("repeatable", "--tile-mode", help="repeatable/directional/object"),
) -> None:
    """高解像度MAPをA/B/C方式で64x64 tileへコンパイルします。"""
    output_dir = output or (Path("e2e") / "map_context_test")
    if palette not in {16, 24, 32}:
        raise typer.BadParameter("MAP paletteは16、24、32のいずれかです", param_hint="--palette")
    try:
        config = MapCompilerConfig(
            output_root=output_dir,
            columns=cols,
            rows=rows,
            context_margin_tiles=context,
            global_palette_budget=palette,
            shared_palette_enabled=shared_palette,
            tile_mode=tile_mode,  # type: ignore[arg-type]
        )
        result = MapExperimentRunner().run(source, config)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"完了: {result.context_path}")
    typer.echo(f"比較: {result.comparison_path}")


@app.command()
def gui() -> None:
    """GUIを起動します。"""
    from pixel_tile_compiler.gui.app import run

    run()


if __name__ == "__main__":
    app()
