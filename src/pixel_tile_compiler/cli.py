"""Typer command-line interface."""

import json
from pathlib import Path
from typing import Optional

import typer

from pixel_tile_compiler.config import CanvasSpec, CompilerConfig, MapCompilerConfig, compiler_config_for_purpose
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
from pixel_tile_compiler.material_library.real_qualification import (
    RealMaterialQualificationRunner,
    import_material_review,
    import_real_material_sources,
    load_real_qualification_config,
)
from pixel_tile_compiler.palette_study import (
    PaletteBudgetStudyRunner,
    import_palette_review,
    load_palette_budget_config,
)
from pixel_tile_compiler.mixed_palette_study import (
    MixedMaterialPaletteArchitectureStudyRunner,
    import_mixed_palette_review,
    load_mixed_palette_config,
)
from pixel_tile_compiler.character_study import (
    CharacterPaletteDensityStudyRunner,
    NativeResolutionStudyRunner,
    load_character_study_config,
    load_native_resolution_study_config,
)
from pixel_tile_compiler.pixelizer.character_animation import (
    CharacterAnimationConfig,
    CharacterAnimationTransform,
    compile_character_animation_sheet,
)
from pixel_tile_compiler.asset.pipeline import compile_generated_sheet, process_generated_sheet, validate_asset_package
from pixel_tile_compiler.generation.adapter import GenerationUnavailableError, UnconfiguredImageGenerationAdapter
from pixel_tile_compiler.generation.pipeline import GenerationFirstPipeline
from pixel_tile_compiler.generation.request_compiler import GenerationRequestCompiler
from pixel_tile_compiler.generation.spec import TilesetSpec

app = typer.Typer(help="SRPG用ピクセルアートコンパイラ（単体Canvas可変、MAPセルは64x64）")


@app.command()
def compile(
    source: Path = typer.Argument(..., exists=True, readable=True, help="入力画像PNG/JPEG/WebP"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="出力ディレクトリ"),
    purpose: str = typer.Option("terrain", "--purpose", help="用途: terrain/character"),
    palette: Optional[int] = typer.Option(None, "--palette", min=4, max=64, help="パレット上限"),
    preset: str = typer.Option("none", "--preset", help="none/b24"),
    width: int = typer.Option(64, "--width", min=1, help="単体出力Canvasの幅"),
    height: int = typer.Option(64, "--height", min=1, help="単体出力Canvasの高さ"),
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
    character_detail: Optional[str] = typer.Option(None, "--character-detail", help="sparse/balanced/detailed"),
) -> None:
    """入力画像を指定した単体Output Canvasへ変換します。"""
    output_dir = output or (Path("output") / source.stem)
    if preset not in {"none", "b24"}:
        raise typer.BadParameter("presetはnoneまたはb24です", param_hint="--preset")
    palette_budget = palette if palette is not None else (24 if preset == "b24" else 16)
    detail_level = character_detail or ("balanced" if preset == "b24" else "detailed")
    try:
        config = compiler_config_for_purpose(
            purpose,  # type: ignore[arg-type]
            output_root=output_dir,
            canvas=CanvasSpec(width, height),
            palette_budget=palette_budget,
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
            character_detail_level=detail_level,  # type: ignore[arg-type]
        )
        result = PixelTileCompiler().compile(source, config)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"完了: {result.final_path}")
    typer.echo(f"パレット: {result.metrics.actual_palette_count}色")
    if result.metrics.periodicity_risk_score is not None:
        typer.echo(f"周期リスク: {result.metrics.periodicity_risk_score:.3f}")


@app.command("compile-character-animation")
def compile_character_animation_command(
    source: Path = typer.Argument(..., exists=True, readable=True, help="キャラクターアニメーションSheet PNG"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="アニメーション出力ディレクトリ"),
    split_mode: str = typer.Option("fixed_grid", "--split-mode", help="fixed_grid/alpha_gap_auto/hybrid"),
    columns: int = typer.Option(4, "--cols", min=1, help="fixed_gridまたはhybridフォールバック時の列数"),
    rows: int = typer.Option(1, "--rows", min=1, help="fixed_gridまたはhybridフォールバック時の行数"),
    frames: Optional[int] = typer.Option(None, "--frames", min=1, help="後方互換: --cols N --rows 1 と同じ"),
    palette: int = typer.Option(24, "--palette", min=4, max=64, help="各フレームのパレット上限"),
    alpha_threshold: int = typer.Option(16, "--alpha-threshold", min=0, max=255, help="可視扱いするアルファ閾値"),
    min_component_area: int = typer.Option(3, "--min-component-area", min=1, help="残す孤立成分の最小面積"),
    remove_isolated: bool = typer.Option(True, "--remove-isolated/--keep-isolated", help="微小な孤立成分を除去"),
    padding: int = typer.Option(1, "--padding", min=0, help="共通bboxへ追加する入力側余白"),
    fit_width: int = typer.Option(54, "--fit-width", min=1, help="共通bboxの最大幅"),
    fit_height: int = typer.Option(54, "--fit-height", min=1, help="共通bboxの最大高さ"),
    bottom_margin: int = typer.Option(6, "--bottom-margin", min=0, help="64x64下端から足元までの余白"),
    empty_column_threshold: int = typer.Option(2, "--empty-column-threshold", min=0, help="空列とみなす可視画素数の上限"),
    empty_row_threshold: int = typer.Option(2, "--empty-row-threshold", min=0, help="空行とみなす可視画素数の上限"),
    min_gutter_width: int = typer.Option(2, "--min-gutter-width", min=1, help="境界とみなす透明ガター幅"),
    max_cell_variance: float = typer.Option(1.25, "--max-cell-variance", min=1.0, help="セル幅・高さの最大ばらつき比"),
    require_nonempty: bool = typer.Option(True, "--require-nonempty/--allow-empty", help="検出セルを空にしない"),
    remainder_policy: str = typer.Option("center_crop", "--remainder-policy", help="center_crop/error"),
    character_detail: str = typer.Option("balanced", "--character-detail", help="sparse/balanced/detailed"),
    outline: str = typer.Option("off", "--outline", help="off/black/white"),
    debug: bool = typer.Option(False, "--debug/--no-debug", help="各フレームのデバッグ画像を保存"),
    width: int = typer.Option(64, "--width", min=1, help="1frameの出力Canvas幅"),
    height: int = typer.Option(64, "--height", min=1, help="1frameの出力Canvas高さ"),
    placement_mode: str = typer.Option("legacy_foot", "--placement-mode", help="legacy_foot/preserve_motion"),
    source_origin: Optional[str] = typer.Option(None, "--source-origin", help="移動保存のソース原点 x,y"),
    output_origin: Optional[str] = typer.Option(None, "--output-origin", help="移動保存の出力原点 x,y"),
    scale: Optional[float] = typer.Option(None, "--scale", min=0.000001, help="移動保存の固定倍率"),
    transform_file: Optional[Path] = typer.Option(None, "--transform", exists=True, readable=True, help="保存済みtransform JSON"),
    shared_palette: Optional[bool] = typer.Option(None, "--shared-palette/--no-shared-palette", help="動作全体でpaletteを共有"),
    allow_empty: bool = typer.Option(False, "--allow-empty/--reject-empty", help="移動保存で透明frameを許可"),
) -> None:
    """キャラクターアニメーションSheetを分割し、共通配置で64x64化します。"""
    output_dir = output or (Path("output") / f"{source.stem}_animation")
    if split_mode not in {"fixed_grid", "alpha_gap_auto", "hybrid"}:
        raise typer.BadParameter("split_modeはfixed_grid、alpha_gap_auto、hybridのいずれかです", param_hint="--split-mode")
    if frames is not None:
        if columns != 4 or rows != 1:
            raise typer.BadParameter("--framesは--cols/--rowsと併用できません", param_hint="--frames")
        columns, rows = frames, 1
    if remainder_policy not in {"center_crop", "error"}:
        raise typer.BadParameter("remainder_policyはcenter_cropまたはerrorです", param_hint="--remainder-policy")
    if character_detail not in {"sparse", "balanced", "detailed"}:
        raise typer.BadParameter("character_detailはsparse、balanced、detailedのいずれかです", param_hint="--character-detail")
    if outline not in {"off", "black", "white"}:
        raise typer.BadParameter("outlineはoff、black、whiteのいずれかです", param_hint="--outline")
    if placement_mode not in {"legacy_foot", "preserve_motion"}:
        raise typer.BadParameter("placement_modeはlegacy_footまたはpreserve_motionです", param_hint="--placement-mode")

    def parse_point(value: str | None, option: str) -> tuple[float, float] | None:
        if value is None:
            return None
        try:
            parts = tuple(float(item.strip()) for item in value.split(","))
        except ValueError as exc:
            raise typer.BadParameter("原点はx,y形式で指定してください", param_hint=option) from exc
        if len(parts) != 2:
            raise typer.BadParameter("原点はx,y形式で指定してください", param_hint=option)
        return parts

    parsed_source_origin = parse_point(source_origin, "--source-origin")
    parsed_output_origin = parse_point(output_origin, "--output-origin")
    shared_palette_enabled = (
        placement_mode == "preserve_motion" if shared_palette is None else shared_palette
    )
    loaded_transform = None
    if transform_file is not None:
        try:
            payload = json.loads(transform_file.read_text(encoding="utf-8"))
            loaded_transform = CharacterAnimationTransform.from_dict(payload.get("transform", payload))
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise typer.BadParameter(f"transformを読み込めません: {exc}", param_hint="--transform") from exc
    if loaded_transform is not None and any(
        value is not None for value in (parsed_source_origin, parsed_output_origin, scale)
    ):
        raise typer.BadParameter("--transformは原点・--scaleと併用できません", param_hint="--transform")
    effective_placement_mode = "preserve_motion" if loaded_transform is not None else placement_mode
    shared_palette_enabled = (
        effective_placement_mode == "preserve_motion" if shared_palette is None else shared_palette
    )
    try:
        result = compile_character_animation_sheet(
            source,
            output_dir,
            config=CharacterAnimationConfig(
                frame_count=columns * rows,
                split_mode=split_mode,  # type: ignore[arg-type]
                grid_columns=columns,
                grid_rows=rows,
                canvas_size=(width, height),
                fit_within=(fit_width, fit_height),
                bottom_margin=bottom_margin,
                alpha_threshold=alpha_threshold,
                remove_isolated_components=remove_isolated,
                min_component_area_px=min_component_area,
                padding_px=padding,
                empty_column_threshold=empty_column_threshold,
                empty_row_threshold=empty_row_threshold,
                min_gutter_width_px=min_gutter_width,
                max_cell_size_variance_ratio=max_cell_variance,
                require_nonempty_each_cell=require_nonempty,
                remainder_policy=remainder_policy,  # type: ignore[arg-type]
                outline_width=1 if outline != "off" else 0,
                placement_mode=effective_placement_mode,  # type: ignore[arg-type]
                source_origin=parsed_source_origin,
                output_origin=parsed_output_origin,
                scale_override=scale,
                shared_palette_enabled=shared_palette_enabled,
                allow_empty_frames=allow_empty,
            ),
            palette_budget=palette,
            character_detail_level=character_detail,
            outline_color=outline,
            debug_enabled=debug,
            transform=loaded_transform,
        )
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"完了: {result.output_root}")
    typer.echo(f"整列Sheet: {result.aligned_sheet_path}")
    typer.echo(f"出力Sheet: {result.compiled_sheet_path}")
    typer.echo(f"{result.preview_scale}倍プレビュー（上限8倍）: {result.preview_8x_path}")
    typer.echo(f"bboxレポート: {result.report_path}")
    typer.echo(f"final集約: {result.final_frame_paths[0].parent}")
    if result.detection_overlay_path is not None:
        typer.echo(f"分割確認画像: {result.detection_overlay_path}")
    for frame_path in result.frame_paths:
        typer.echo(f"フレーム: {frame_path}")


@app.command("compile-map")
def compile_map(
    source: Path = typer.Argument(..., exists=True, readable=True, help="入力MAP画像PNG/JPEG/WebP"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="実験出力ディレクトリ"),
    cols: int = typer.Option(4, "--cols", min=1, help="MAP列数"),
    rows: int = typer.Option(5, "--rows", min=1, help="MAP行数"),
    palette: int = typer.Option(24, "--palette", min=16, max=32, help="共有palette上限（16/24/32）"),
    context: int = typer.Option(1, "--context", min=0, help="周辺contextのtile幅"),
    shared_palette: bool = typer.Option(True, "--shared-palette/--no-shared-palette", help="MAP全体paletteを共有"),
    tile_mode: str = typer.Option("directional", "--tile-mode", help="repeatable/directional/object"),
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


@app.command("study-character-palette-density")
def study_character_palette_density(
    config: Path = typer.Option(..., "--config", "-c", exists=True, readable=True, help="Character Palette Density Study YAML/JSON設定"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="実験出力ディレクトリで設定を上書き"),
) -> None:
    """キャラクターのPalette BudgetとDetail Densityを3x3で比較します。"""
    try:
        study_config = load_character_study_config(config)
        if output is not None:
            study_config.output_root = output
        result = CharacterPaletteDensityStudyRunner().run(study_config)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"完了: {result.output_root}")
    for manifest in result.case_manifests:
        typer.echo(f"manifest: {manifest}")


@app.command("study-character-native-resolution")
def study_character_native_resolution(
    config: Path = typer.Option(..., "--config", "-c", exists=True, readable=True, help="Character Native Resolution Study YAML/JSON設定"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="実験出力ディレクトリで設定を上書き"),
) -> None:
    """同一Source/B24でNative 64・64 nearest 2x・Native 128を比較します。"""
    try:
        study_config = load_native_resolution_study_config(config)
        if output is not None:
            study_config.output_root = output
        result = NativeResolutionStudyRunner().run(study_config)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"完了: {result.output_root}")
    typer.echo(f"manifest: {result.manifest_path}")
    typer.echo(f"metrics: {result.metrics_path}")
    typer.echo(f"比較: {result.comparison_path}")


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


@app.command("qualify-real-material-sources")
def qualify_real_material_sources(
    config: Path = typer.Option(..., "--config", "-c", exists=True, readable=True, help="Real t2i Material Qualification YAML/JSON設定"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="実験出力ディレクトリで設定を上書き"),
) -> None:
    """実t2i Sourceだけを対象にMaterial品質支配性を検証します。"""
    try:
        study_config = load_real_qualification_config(config)
        if output is not None:
            study_config.output_root = output
        result = RealMaterialQualificationRunner().run(study_config)
    except (OSError, ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"状態: {result.status}")
    typer.echo(f"出力: {result.output_root}")
    if result.missing_sources:
        typer.echo(f"不足Source: {', '.join(result.missing_sources)}")


@app.command("import-real-material-sources")
def import_real_material_sources_command(
    study: Path = typer.Option(..., "--study", exists=True, file_okay=False, help="Real t2i Qualification studyディレクトリ"),
    input_root: Path = typer.Option(..., "--input", exists=True, file_okay=False, help="生成済みPNGを含む入力ディレクトリ"),
    generator: str = typer.Option(..., "--generator", help="生成器名"),
    model: str = typer.Option("unknown", "--model", help="モデル名"),
) -> None:
    """生成済みPNGをraw immutable Sourceとして取り込みます。"""
    try:
        result = import_real_material_sources(study, input_root, generator, model)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"取り込み: {result['count']}件")


@app.command("import-material-review")
def import_material_review_command(
    study: Path = typer.Option(..., "--study", exists=True, file_okay=False, help="Real t2i Qualification studyディレクトリ"),
    csv_file: Path = typer.Option(..., "--csv", exists=True, readable=True, help="Blind Review記入済みCSV"),
) -> None:
    """Blind Review CSVをMaterial Source Cardへ取り込みます。"""
    try:
        result = import_material_review(study, csv_file)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"人間評価取り込み: {result['human_reviews_imported']}件")
    typer.echo(f"Gate calibration: {result['gate_calibration']}")


@app.command("study-palette-budget")
def study_palette_budget(
    config: Path = typer.Option(..., "--config", "-c", exists=True, readable=True, help="Palette Budget Study YAML/JSON設定"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="実験出力ディレクトリで設定を上書き"),
) -> None:
    """固定Real t2i SourceでPalette Budget / Rampを比較します。"""
    try:
        study_config = load_palette_budget_config(config)
        if output is not None:
            study_config.output_root = output
        result = PaletteBudgetStudyRunner().run(study_config)
    except (OSError, ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"状態: {result.status}")
    typer.echo(f"出力: {result.output_root}")
    typer.echo(f"比較board: {result.output_root / 'summary' / 'comparison_board.png'}")


@app.command("import-palette-review")
def import_palette_review_command(
    study: Path = typer.Option(..., "--study", exists=True, file_okay=False, help="Palette Budget Studyディレクトリ"),
    csv_file: Path = typer.Option(..., "--csv", exists=True, readable=True, help="記入済みPalette Review CSV"),
) -> None:
    """Blind Palette Review CSVを条件成果物へ取り込みます。"""
    try:
        result = import_palette_review(study, csv_file)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"人間評価取り込み: {result['human_reviews_imported']}件")
    typer.echo("Palette Profileの自動昇格: 実施していません")


@app.command("study-mixed-palette-architecture")
def study_mixed_palette_architecture(
    config: Path = typer.Option(..., "--config", "-c", exists=True, readable=True, help="Mixed Palette Architecture Study YAML/JSON設定"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="実験出力ディレクトリで設定を上書き"),
) -> None:
    """固定Mixed MAPでPalette Architectureを比較します。"""
    try:
        study_config = load_mixed_palette_config(config)
        if output is not None:
            study_config.output_root = output
        result = MixedMaterialPaletteArchitectureStudyRunner().run(study_config)
    except (OSError, ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"状態: {result.status}")
    typer.echo(f"出力: {result.output_root}")
    typer.echo(f"比較board: {result.output_root / 'summary' / 'comparison_board.png'}")


@app.command("import-mixed-palette-review")
def import_mixed_palette_review_command(
    study: Path = typer.Option(..., "--study", exists=True, file_okay=False, help="Mixed Palette Architecture Studyディレクトリ"),
    csv_file: Path = typer.Option(..., "--csv", exists=True, readable=True, help="記入済みMixed Palette Review CSV"),
) -> None:
    """Mixed MAPのBlind Review CSVを条件成果物へ取り込みます。"""
    try:
        result = import_mixed_palette_review(study, csv_file)
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"人間評価取り込み: {result['human_reviews_imported']}件")
    typer.echo("Palette Architectureの自動昇格: 実施していません")


@app.command("compile-generation-request")
def compile_generation_request(
    spec: Path = typer.Option(..., "--spec", exists=True, readable=True, help="TilesetSpec JSON"),
    output: Optional[Path] = typer.Option(None, "--output", "-o", help="GenerationRequest JSONの出力先"),
) -> None:
    """TilesetSpecを正本として画像生成Request JSONを生成します。"""
    try:
        tileset_spec = TilesetSpec.from_json_file(spec)
        request = GenerationRequestCompiler().compile(tileset_spec)
        if output is None:
            typer.echo(request.model_dump_json(indent=2))
        else:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(request.model_dump_json(indent=2) + "\n", encoding="utf-8")
            typer.echo(f"出力: {output}")
    except (OSError, ValueError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("process-generated-sheet")
def process_generated_sheet_command(
    spec: Path = typer.Option(..., "--spec", exists=True, readable=True, help="TilesetSpec JSON"),
    image: Path = typer.Option(..., "--image", exists=True, readable=True, help="生成済みSheet PNG"),
    output: Path = typer.Option(..., "--output", "-o", help="Asset Package出力ディレクトリ"),
) -> None:
    """生成済みSheetを等分割・64x64化してAsset Packageにします。"""
    try:
        result = process_generated_sheet(TilesetSpec.from_json_file(spec), image, output)
    except (OSError, ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"状態: {result.validation['status']}")
    typer.echo(f"manifest: {result.manifest_path}")
    typer.echo(f"validation: {result.validation_path}")


@app.command("compile-generated-sheet")
def compile_generated_sheet_command(
    spec: Path = typer.Option(..., "--spec", exists=True, readable=True, help="TilesetSpec JSON"),
    image: Path = typer.Option(..., "--image", exists=True, readable=True, help="高解像度の生成済みSheet PNG"),
    output: Path = typer.Option(..., "--output", "-o", help="コンパイル済みAsset Package出力ディレクトリ"),
    palette: int = typer.Option(16, "--palette", min=4, max=64, help="共有palette上限"),
    debug: bool = typer.Option(False, "--debug/--no-debug", help="各セルのデバッグ画像を保存"),
) -> None:
    """高解像度セルをPixelTileCompilerへ渡して64x64 packageにします。"""
    try:
        result = compile_generated_sheet(
            TilesetSpec.from_json_file(spec),
            image,
            output,
            palette_budget=palette,
            debug_enabled=debug,
        )
    except (OSError, ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"状態: {result.validation['status']}")
    typer.echo(f"map: {result.map_path}")
    typer.echo(f"manifest: {result.manifest_path}")
    typer.echo(f"validation: {result.validation_path}")
    if result.validation["status"] == "rejected":
        raise typer.Exit(code=1)


@app.command("validate-tileset")
def validate_tileset_command(
    package: Path = typer.Argument(..., exists=True, file_okay=False, help="Asset Packageディレクトリ"),
) -> None:
    """既存Asset Packageのmanifest・Tile寸法・ファイル存在を検証します。"""
    result = validate_asset_package(package)
    typer.echo(f"状態: {result['status']}")
    for issue in result.get("issues", []):
        typer.echo(f"問題: {issue}")
    if result["status"] == "rejected":
        raise typer.Exit(code=1)


@app.command("generate-tileset")
def generate_tileset_command(
    spec: Path = typer.Option(..., "--spec", exists=True, readable=True, help="TilesetSpec JSON"),
    output: Path = typer.Option(..., "--output", "-o", help="Asset Package出力ディレクトリ"),
) -> None:
    """ImageGenerationAdapter経由でSheet生成からAsset Package化まで行います。"""
    try:
        tileset_spec = TilesetSpec.from_json_file(spec)
        GenerationFirstPipeline().run(tileset_spec, output, UnconfiguredImageGenerationAdapter())
    except GenerationUnavailableError as exc:
        raise typer.BadParameter(str(exc)) from exc
    except (OSError, ValueError, RuntimeError) as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command()
def gui() -> None:
    """GUIを起動します。"""
    from pixel_tile_compiler.gui.app import run

    run()


if __name__ == "__main__":
    app()
