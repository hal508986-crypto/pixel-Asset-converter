"""連番PNGフォルダ → 背景除去 → 共通座標・共有パレットでコンパイル、を1本で通す。

途中成果物は全段階を個別ファイルで残す（source_frames / keyed_frames / aligned_frames /
compiled / final_frames）。正本は final_frames のPNG連番で、GIFなどはその派生物として扱う。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from pixel_tile_compiler.io.frame_sequence import load_frame_directory
from pixel_tile_compiler.pixelizer.character_animation import (
    CharacterAnimationCompileResult,
    CharacterAnimationConfig,
    analyze_frame_alpha,
    compile_character_animation_frames,
)
from pixel_tile_compiler.preprocess.sequence_background import (
    DEFAULT_BACKGROUND_TOLERANCE,
    KeyMode,
    remove_sequence_background,
)

DEFAULT_SEQUENCE_CANVAS = (512, 512)
DEFAULT_GIF_FPS = 24.0


def default_sequence_config(canvas_size: tuple[int, int] = DEFAULT_SEQUENCE_CANVAS) -> CharacterAnimationConfig:
    """動画向けの既定: 移動保存（跳躍・突進を足元固定で消さない）、出力Canvasの縁に少し余白。"""
    margin = max(1, min(canvas_size) // 32)
    return CharacterAnimationConfig(
        canvas_size=canvas_size,
        fit_within=(canvas_size[0] - 2 * margin, canvas_size[1] - 2 * margin),
        bottom_margin=margin,
        placement_mode="preserve_motion",
    )


def _with_derived_origins(
    config: CharacterAnimationConfig,
    frames: tuple[Any, ...],
) -> CharacterAnimationConfig:
    """移動保存で原点が未指定なら、全フレームの可視範囲の足元中央を原点にする。

    ソース原点は全フレームのbbox和の（左右中央, 下端）、出力原点は（Canvas中央, 下端から余白）。
    倍率は未指定なら既存のフィット計算が全フレームを収める値に決める。
    """
    if config.placement_mode != "preserve_motion" or (
        config.source_origin is not None and config.output_origin is not None
    ):
        return config
    union = None
    for frame in frames:
        _, bbox = analyze_frame_alpha(
            frame,
            alpha_threshold=config.alpha_threshold,
            remove_isolated_components=config.remove_isolated_components,
            min_component_area_px=config.min_component_area_px,
        )
        if bbox is not None:
            union = bbox if union is None else union.union(bbox)
    if union is None:
        raise ValueError("可視画素のあるフレームがありません（背景除去の設定を確認してください）")
    width, height = config.canvas_size
    return replace(
        config,
        source_origin=config.source_origin or ((union.left + union.right) / 2, float(union.bottom)),
        output_origin=config.output_origin or (width / 2, float(height - config.bottom_margin)),
    )


def compile_character_frame_directory(
    input_dir: Path | str,
    output_root: Path | str,
    *,
    config: CharacterAnimationConfig | None = None,
    canvas_size: tuple[int, int] = DEFAULT_SEQUENCE_CANVAS,
    key_background: bool = True,
    background_color: str | tuple[int, int, int] | None = None,
    background_tolerance: int = DEFAULT_BACKGROUND_TOLERANCE,
    background_mode: KeyMode = "auto",
    background_choke_px: int = 0,
    gif_fps: float | None = DEFAULT_GIF_FPS,
    gif_loop: int | None = 0,
    **compile_kwargs: Any,
) -> CharacterAnimationCompileResult:
    """PNG連番フォルダを読み、背景除去（任意）→コンパイルして、全段階を個別ファイルで保存する。

    key_background=False は、すでに透過済みの連番を受けるとき。
    background_color 未指定なら、全フレームの外周から1回だけ推定して全フレームで共有する。
    background_mode は auto（既定: 鮮やかな背景色は global、白・グレーは connected）/connected/global。
    background_choke_px でキャラの輪郭を内側へ削り、背景との混色（縁のにじみ）を落とせる。
    config を渡した場合は canvas_size より config を優先する。
    gif_fps（既定24）で final_frames から animation.gif も出力する（None で出力しない）。
    gif_loop: 0=無限ループ、N=初回後にN回、None=1回だけ再生。
    """
    input_dir = Path(input_dir)
    paths, source_frames = load_frame_directory(input_dir)
    archive: dict[str, Any] = {"source_frames": list(paths)}
    extras: dict[str, object] = {
        "source_directory": {"path": str(input_dir), "files": [path.name for path in paths]},
    }
    if key_background:
        keyed = remove_sequence_background(
            source_frames,
            color=background_color,
            tolerance=background_tolerance,
            mode=background_mode,
            choke_px=background_choke_px,
        )
        frames = keyed.frames
        archive["keyed_frames"] = list(frames)
        extras["background_key"] = keyed.report_as_dict()
    else:
        frames = source_frames
        extras["background_key"] = None
    resolved_config = _with_derived_origins(config or default_sequence_config(canvas_size), frames)
    result = compile_character_animation_frames(
        frames,
        output_root,
        config=resolved_config,
        source_label=str(input_dir),
        report_extras=extras,
        archive_frames=archive,
        gif_fps=gif_fps,
        gif_loop=gif_loop,
        **compile_kwargs,
    )
    if key_background and keyed.warnings:
        result = replace(result, warnings=(*result.warnings, *keyed.warnings))
    return result


__all__ = ["compile_character_frame_directory", "default_sequence_config"]
