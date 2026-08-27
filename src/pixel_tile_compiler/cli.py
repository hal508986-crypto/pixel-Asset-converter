"""Typer command-line interface."""

from pathlib import Path
from typing import Optional

import typer

from pixel_tile_compiler.config import CompilerConfig, MapCompilerConfig
from pixel_tile_compiler.map.compiler import MapExperimentRunner
from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler
from pixel_tile_compiler.tileset.compiler import TilesetSourceCompiler
from pixel_tile_compiler.tileset.source_tile import TilesetConfig
from pixel_tile_compiler.source_study.runner import SourceStudyRunner, load_study_config
from pixel_tile_compiler.transition_network.study import (
    TransitionNetworkStudyRunner,
    load_transition_network_config,
)
from pixel_tile_compiler.transition_network.road_graph import (
    RoadGraphStudyRunner,
    load_road_graph_config,
)
from pixel_tile_compiler.transition_network.river_study import (
    RiverGraphStudyRunner,
    load_river_graph_config,
)
from pixel_tile_compiler.pixel_grammar import PixelGrammarStudyRunner, load_pixel_grammar_config
from pixel_tile_compiler.pixel_hierarchy import PixelHierarchyStudyRunner, load_pixel_hierarchy_config
from pixel_tile_compiler.material_library import MaterialLibraryRunner, load_material_library_config

app = typer.Typer(help="SRPG用64x64ピクセルアートMAPタイルコンパイラ")


@app.command()
def compile(
    source: Path = typer.Argument(..., exists=True, readable=True, help="入力画像PNG/JPEG/WebP"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="出力ディレクトリ"),
    palette: int = typer.Option(16, "--palette", min=4, max=64, help="パレット上限"),
    tile_mode: str = typer.Option("repeatable", "--tile-mode", help="repeatable/directional/object"),
    semantic: str = typer.Option("rule", "--semantic", help="rule/mcp"),
    seam: str = typer.Option("inspect", "--seam", help="off/inspect/correct"),
    repeat_opt: bool = typer.Option(True, "--repeat-opt/--no-repeat-opt", help="repeatable最適化"),
    repeat_opt_strength: float = typer.Option(0.5, "--repeat-opt-strength", min=0.0, max=1.0),
    repeat_opt_edge_band: int = typer.Option(6, "--repeat-opt-edge-band", min=1, max=32),
    center_suppression: float = typer.Option(0.4, "--center-suppression", min=0.0, max=1.0),
    background: str = typer.Option("alpha", "--background", help="alpha/auto/color"),
    background_color: Optional[str] = typer.Option(None, "--background-color"),
    pixelization: str = typer.Option("region", "--pixelization", help="region/nearest"),
    outline: str = typer.Option("off", "--outline", help="off/black/white"),
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
            pixelization_mode=pixelization,  # type: ignore[arg-type]
            outline_color=outline,  # type: ignore[arg-type]
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


@app.command("build-tileset")
def build_tileset(
    source: Path = typer.Argument(..., exists=True, readable=True, help="Material Exemplar画像PNG/JPEG/WebP"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="Tileset実験出力ディレクトリ"),
    variants: int = typer.Option(12, "--variants", min=1, help="生成するSource Tile数"),
    edge_types: int = typer.Option(3, "--edge-types", min=1, max=8, help="Edge Contract種類数"),
    palette: int = typer.Option(24, "--palette", min=4, max=32, help="共有palette上限"),
    map_cols: int = typer.Option(10, "--map-cols", min=1, help="比較MAP列数"),
    map_rows: int = typer.Option(10, "--map-rows", min=1, help="比較MAP行数"),
    patch_size: int = typer.Option(256, "--patch-size", min=2, help="quilt patchサイズ"),
    patch_overlap: int = typer.Option(64, "--patch-overlap", min=0, help="quilt overlapサイズ"),
    source_tile_size: int = typer.Option(512, "--source-tile-size", min=64, help="高解像度Source Tileサイズ"),
    strip_width: int = typer.Option(96, "--strip-width", min=1, help="Edge Strip幅"),
    shared_palette: bool = typer.Option(True, "--shared-palette/--no-shared-palette", help="Tileset全体でpaletteを共有"),
    seed: int = typer.Option(42, "--seed", help="再現性用seed"),
) -> None:
    """1枚のgrass Material Exemplarから契約付きTilesetを生成します。"""
    output_dir = output or (Path("experiment") / f"{source.stem}_tileset")
    try:
        config = TilesetConfig(
            output_root=output_dir,
            variants=variants,
            edge_types=edge_types,
            palette_budget=palette,
            map_columns=map_cols,
            map_rows=map_rows,
            patch_size=patch_size,
            patch_overlap=patch_overlap,
            source_tile_size=source_tile_size,
            strip_width=strip_width,
            shared_palette=shared_palette,
            seed=seed,
        )
        result = TilesetSourceCompiler().build(source, config)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"完了: {result.map_path}")
    typer.echo(f"比較: {result.comparison_path}")
    typer.echo(f"metrics: {result.metrics_path}")


@app.command("study-sources")
def study_sources(
    config: Path = typer.Option(..., "--config", "-c", exists=True, readable=True, help="source study JSON/YAML設定"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="実験出力ディレクトリで設定を上書き"),
) -> None:
    """複数のMaterial Sourceを同条件で検証・比較します。"""
    try:
        study_config = load_study_config(config)
        if output is not None:
            study_config.output_root = output
        result = SourceStudyRunner().run(study_config)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"完了: {result.output_root}")
    typer.echo(f"ランキング: {result.output_root / 'summary' / 'source_ranking.json'}")
    typer.echo(f"montage: {result.output_root / 'summary' / 'montage.png'}")


@app.command("study-forest-sources")
def study_forest_sources(
    config: Path = typer.Option(..., "--config", "-c", exists=True, readable=True, help="forest source study JSON/YAML設定"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="実験出力ディレクトリで設定を上書き"),
) -> None:
    """複数のcontinuous forest canopy Sourceを同条件で検証・比較します。"""
    study_sources(config=config, output=output)


@app.command("study-transition-network")
def study_transition_network(
    config: Path = typer.Option(..., "--config", "-c", exists=True, readable=True, help="Transition / Network study YAML/JSON設定"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="実験出力ディレクトリで設定を上書き"),
) -> None:
    """Surface / Network / Transitionを同一layoutで比較します。"""
    try:
        study_config = load_transition_network_config(config)
        if output is not None:
            study_config.output_root = output
        result = TransitionNetworkStudyRunner().run(study_config)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"完了: {result.output_root}")
    typer.echo(f"manifest: {result.manifest_path}")
    typer.echo(f"ランキング: {result.ranking_path}")
    typer.echo(f"montage: {result.montage_path}")


@app.command("study-road-graph")
def study_road_graph(
    config: Path = typer.Option(..., "--config", "-c", exists=True, readable=True, help="Logical Road Graph study YAML/JSON設定"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="実験出力ディレクトリで設定を上書き"),
) -> None:
    """Logical Road GraphからTopologyを解決して10x10 MAPを生成します。"""
    try:
        study_config = load_road_graph_config(config)
        if output is not None:
            study_config.output_root = output
        result = RoadGraphStudyRunner().run(study_config)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"完了: {result.output_root}")
    typer.echo(f"manifest: {result.manifest_path}")
    typer.echo(f"metrics: {result.metrics_path}")
    typer.echo(f"comparison: {result.comparison_path}")


@app.command("study-river-graph")
def study_river_graph(
    config: Path = typer.Option(..., "--config", "-c", exists=True, readable=True, help="Generic River Graph study YAML/JSON設定"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="実験出力ディレクトリで設定を上書き"),
) -> None:
    """Generic Network GraphからRiver topologyを解決して10x10 MAPを生成します。"""
    try:
        study_config = load_river_graph_config(config)
        if output is not None:
            study_config.output_root = output
        result = RiverGraphStudyRunner().run(study_config)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"完了: {result.output_root}")
    typer.echo(f"manifest: {result.manifest_path}")
    typer.echo(f"metrics: {result.metrics_path}")
    typer.echo(f"comparison: {result.comparison_path}")


@app.command("study-pixel-grammar")
def study_pixel_grammar(
    config: Path = typer.Option(..., "--config", "-c", exists=True, readable=True, help="64x64 Pixel Grammar Study YAML/JSON設定"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="実験出力ディレクトリで設定を上書き"),
) -> None:
    """Sparse / Balanced / Detailedの64x64 Pixel Grammarを比較します。"""
    try:
        study_config = load_pixel_grammar_config(config)
        if output is not None:
            study_config.output_root = output
        result = PixelGrammarStudyRunner().run(study_config)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"完了: {result.output_root}")
    typer.echo(f"metrics: {result.metrics_path}")
    typer.echo(f"comparison: {result.comparison_board_path}")


@app.command("study-pixel-hierarchy")
def study_pixel_hierarchy(
    config: Path = typer.Option(..., "--config", "-c", exists=True, readable=True, help="Flat / Structured / Volumetric Study YAML/JSON設定"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="実験出力ディレクトリで設定を上書き"),
) -> None:
    """大形状・cluster・立体表現の階層を64x64で比較します。"""
    try:
        study_config = load_pixel_hierarchy_config(config)
        if output is not None:
            study_config.output_root = output
        result = PixelHierarchyStudyRunner().run(study_config)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"完了: {result.output_root}")
    typer.echo(f"metrics: {result.metrics_path}")
    typer.echo(f"comparison: {result.comparison_board_path}")


@app.command("build-material-library")
def build_material_library(
    config: Path = typer.Option(..., "--config", "-c", exists=True, readable=True, help="Material Source Library YAML/JSON設定"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="実験出力ディレクトリで設定を上書き"),
) -> None:
    """4 material familiesをSource Card/Gate/Probe/Rankingまで一括生成します。"""
    try:
        study_config = load_material_library_config(config)
        if output is not None:
            study_config.output_root = output
        result = MaterialLibraryRunner().run(study_config)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"完了: {result.output_root}")
    typer.echo(f"library: {result.library_root}")
    typer.echo(f"ランキング: {result.output_root / 'summary' / 'source_ranking.json'}")


@app.command()
def gui() -> None:
    """GUIを起動します。"""
    from pixel_tile_compiler.gui.app import run

    run()


if __name__ == "__main__":
    app()
