"""連番PNGフォルダ → 背景除去 → 共通座標・共有パレットでコンパイル、を1本で通す。

途中成果物は全段階を個別ファイルで残す（source_frames / keyed_frames / aligned_frames /
compiled / final_frames）。正本は final_frames のPNG連番で、GIFなどはその派生物として扱う。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

import numpy as np

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
DEFAULT_STABILIZE_MARGIN = 12.0  # 実素材で輪郭・パレットを変えずにちらつきを大きく減らせた値


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
    fit_percentile: float = 100.0,
) -> CharacterAnimationConfig:
    """移動保存で原点・倍率が未指定なら、全フレームの可視範囲から足元中央の原点と倍率を決める。

    fit_percentile=100（既定）: ソース原点は全フレームのbbox和の（左右中央, 下端）、出力原点は
    （Canvas中央, 下端から余白）。倍率は既存のフィット計算が全フレームを収める値に決める。

    fit_percentile<100: 「その割合のフレームが収まる」倍率にして、はみ出す極端なコマ（槍を大きく突き出す
    など）は見切れを許す。原点の横位置は各フレームのbbox中心の中央値（槍に引っ張られずキャラ本体の位置になる）。
    左右・上への必要量を、各フレームの原点からの距離の百分位で測り、Canvasに収まる最大の倍率にする。
    """
    if not 0 < fit_percentile <= 100:
        raise ValueError("fit_percentile must be in (0, 100]")
    if config.placement_mode != "preserve_motion" or (
        config.source_origin is not None and config.output_origin is not None
    ):
        return config
    boxes = []
    for frame in frames:
        _, bbox = analyze_frame_alpha(
            frame,
            alpha_threshold=config.alpha_threshold,
            remove_isolated_components=config.remove_isolated_components,
            min_component_area_px=config.min_component_area_px,
        )
        if bbox is not None:
            boxes.append(bbox)
    if not boxes:
        raise ValueError("可視画素のあるフレームがありません（背景除去の設定を確認してください）")
    union = boxes[0]
    for bbox in boxes[1:]:
        union = union.union(bbox)
    width, height = config.canvas_size
    output_origin = config.output_origin or (width / 2, float(height - config.bottom_margin))
    if fit_percentile >= 100:
        return replace(
            config,
            source_origin=config.source_origin or ((union.left + union.right) / 2, float(union.bottom)),
            output_origin=output_origin,
        )
    centers = [(box.left + box.right) / 2 for box in boxes]
    origin_x, origin_y = config.source_origin or (float(np.median(centers)), float(union.bottom))

    def percentile(values: list[float]) -> float:
        return float(np.percentile(np.asarray(values, dtype=np.float64), fit_percentile, method="higher"))

    half_width = max(percentile([origin_x - box.left for box in boxes]), percentile([box.right - origin_x for box in boxes]), 1.0)
    upward = max(percentile([origin_y - box.top for box in boxes]), 1.0)
    margin = config.bottom_margin
    scale = min(
        (width / 2 - margin) / half_width,
        (output_origin[1] - margin) / upward,
    )
    return replace(
        config,
        source_origin=(origin_x, origin_y),
        output_origin=output_origin,
        scale_override=config.scale_override if config.scale_override is not None else scale,
        allow_clipping=True,
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
    stabilize_margin: float | None = DEFAULT_STABILIZE_MARGIN,
    fit_percentile: float = 100.0,
    progress: Callable[[str, int, int], None] | None = None,
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
    stabilize_margin: final_frames の色を時間方向に安定させる（既定12。パレット境界でのちらつき抑制。None でしない）。
    fit_percentile: 100（既定）は全フレームがCanvasに収まる倍率。100未満は、その割合のフレームが収まる倍率にして、
    はみ出す極端なコマ（槍の突き出しなど）は見切れを許す（見切れたコマは警告とレポートに記録）。
    progress(stage, done, total): 進捗通知（load/key/compile/finalize）。ここから例外を投げると中断でき、既存の出力は壊れない。
    """
    input_dir = Path(input_dir)
    if progress is not None:
        progress("load", 0, 1)
    paths, source_frames = load_frame_directory(input_dir)
    archive: dict[str, Any] = {"source_frames": list(paths)}
    extras: dict[str, object] = {
        "source_directory": {"path": str(input_dir), "files": [path.name for path in paths]},
    }
    if key_background:
        if progress is not None:
            progress("key", 0, 1)
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
    resolved_config = _with_derived_origins(config or default_sequence_config(canvas_size), frames, fit_percentile)
    extras["framing"] = {"fit_percentile": fit_percentile, "clipping_allowed": resolved_config.allow_clipping}
    result = compile_character_animation_frames(
        frames,
        output_root,
        config=resolved_config,
        source_label=str(input_dir),
        report_extras=extras,
        archive_frames=archive,
        gif_fps=gif_fps,
        gif_loop=gif_loop,
        stabilize_margin=stabilize_margin,
        progress=progress,
        **compile_kwargs,
    )
    if key_background and keyed.warnings:
        result = replace(result, warnings=(*result.warnings, *keyed.warnings))
    return result


__all__ = ["compile_character_frame_directory", "default_sequence_config"]
