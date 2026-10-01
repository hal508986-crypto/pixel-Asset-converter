"""連番PNGフォルダ → 背景除去 → 共通座標・共有パレットでコンパイル、を1本で通す。

途中成果物は全段階を個別ファイルで残す（source_frames / keyed_frames / aligned_frames /
compiled / final_frames）。正本は final_frames のPNG連番で、GIFなどはその派生物として扱う。
"""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

import numpy as np

from pixel_tile_compiler.io.frame_sequence import load_frame_directory
from pixel_tile_compiler.pixelizer.character_body import measure_body, reference_body_height
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
AUTO_CANVAS_MARGIN = 2  # Canvas自動のときの四方の余白(px)
MAX_AUTO_CANVAS_SIDE = 4096
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


def _parse_height_reference(reference: str | int) -> str | int:
    if isinstance(reference, str) and reference.strip().isdigit():
        return int(reference.strip())
    return reference


def _with_character_height(
    config: CharacterAnimationConfig,
    frames: tuple[Any, ...],
    character_height: float,
    height_reference: str | int,
    canvas_auto: bool,
    foot_lock: bool = False,
) -> tuple[CharacterAnimationConfig, dict[str, object]]:
    """キャラ本体の高さ（アルファから測定）を基準に、倍率・足元・Canvasを決める。

    倍率 = 指定の身長(出力px) ÷ 基準の本体高さ(元絵px)。倍率と足元は全フレームで固定なので、踏み込みや跳躍で
    縦幅が変わっても、キャラのサイズはブレない。足元＝各フレームの本体下端の中央値、横位置＝本体中心の中央値。
    canvas_auto: 全フレームの張り出しが収まる最小のCanvas（四方の余白AUTO_CANVAS_MARGIN）にする（見切れなし）。
    それ以外は config のCanvas固定で、収まらない部分は見切れを許す（見切れたコマは警告とレポートに記録）。
    foot_lock: 各フレームの本体の下端が基準の足元にそろうよう、フレームごとに**縦方向の平行移動だけ**を補正する。
    元動画側で踏み込み中などにキャラ全体が上下へずれる（接地線が動く）素材向け。倍率は変えない。
    跳躍のような本物の浮きも打ち消すので、既定はオフ。
    """
    if config.placement_mode != "preserve_motion":
        raise ValueError("身長指定は移動保存モード（preserve_motion）でのみ使えます")
    if not character_height > 0:
        raise ValueError("character_height must be positive")
    bodies: list[tuple[int, int, int, int]] = []
    boxes = []
    for frame in frames:
        cleaned, bbox = analyze_frame_alpha(
            frame,
            alpha_threshold=config.alpha_threshold,
            remove_isolated_components=config.remove_isolated_components,
            min_component_area_px=config.min_component_area_px,
        )
        body = measure_body(np.asarray(cleaned.getchannel("A"))) if bbox is not None else None
        if body is not None and bbox is not None:
            bodies.append(body)
            boxes.append(bbox)
    if not bodies:
        raise ValueError("可視画素のあるフレームがありません（背景除去の設定を確認してください）")
    heights = [float(bottom - top) for _, top, _, bottom in bodies]
    if isinstance(height_reference, int) and len(bodies) != len(frames):
        raise ValueError("空のフレームがあるため、フレーム番号での基準指定はできません（median か first を使ってください）")
    reference = reference_body_height(heights, height_reference)
    scale = float(character_height) / reference
    origin_x = float(np.median([(left + right) / 2 for left, _, right, _ in bodies]))
    origin_y = float(np.median([bottom for _, _, _, bottom in bodies]))
    frame_offsets: tuple[tuple[int, int], ...] | None = None
    if foot_lock:
        if len(bodies) != len(frames):
            raise ValueError("空のフレームがあるため、足元ロックは使えません")
        # 出力での縦移動量 = 倍率 × (基準の足元 - そのフレームの本体の下端)（整数pxに丸める）
        frame_offsets = tuple((0, int(round(scale * (origin_y - bottom)))) for _, _, _, bottom in bodies)
    framing: dict[str, object] = {
        "mode": "character_height",
        "character_height_px": float(character_height),
        "height_reference": height_reference,
        "reference_body_height_src_px": reference,
        "body_height_src_px": {"min": min(heights), "max": max(heights), "median": float(np.median(heights))},
        "scale": scale,
        "source_origin": [origin_x, origin_y],
        "canvas_auto": canvas_auto,
        "foot_lock": foot_lock,
    }
    if frame_offsets is not None:
        framing["foot_lock_offsets_y_px"] = {"min": min(o[1] for o in frame_offsets), "max": max(o[1] for o in frame_offsets)}
    if canvas_auto:
        margin = AUTO_CANVAS_MARGIN
        shifts = [offset[1] for offset in frame_offsets] if frame_offsets is not None else [0] * len(boxes)
        left = max(origin_x - box.left for box in boxes) * scale
        right = max(box.right - origin_x for box in boxes) * scale
        up = max((origin_y - box.top) * scale - shift for box, shift in zip(boxes, shifts))
        down = max(0.0, max((box.bottom - origin_y) * scale + shift for box, shift in zip(boxes, shifts)))
        pivot_x, pivot_y = margin + left, margin + up
        width = math.ceil(pivot_x + right + margin - 1e-9)
        height = math.ceil(pivot_y + down + margin - 1e-9)
        if max(width, height) > MAX_AUTO_CANVAS_SIDE:
            raise ValueError(
                f"Canvas自動が{width}×{height}になり上限（{MAX_AUTO_CANVAS_SIDE}）を超えます。身長を小さくするか、Canvasを固定してください"
            )
        outline = config.outline_width
        config = replace(
            config,
            canvas_size=(width, height),
            fit_within=(width - 2 * margin - 2 * outline, height - 2 * margin - 2 * outline),
            bottom_margin=margin,
            source_origin=(origin_x, origin_y),
            output_origin=(pivot_x, pivot_y),
            scale_override=scale,
            frame_offsets=frame_offsets,
            allow_clipping=False,
        )
        framing["canvas_size"] = [width, height]
        framing["pivot"] = [pivot_x, pivot_y]
    else:
        width, height = config.canvas_size
        output_origin = config.output_origin or (width / 2, float(height - config.bottom_margin))
        available = output_origin[1] - config.bottom_margin
        if character_height > available:
            raise ValueError(
                f"キャラの身長{character_height:g}pxがCanvas（高さ{height}px）に収まりません。"
                f"{available:g}px以下にするか、Canvasを大きくするか、Canvas自動にしてください"
            )
        config = replace(
            config,
            source_origin=(origin_x, origin_y),
            output_origin=output_origin,
            scale_override=scale,
            frame_offsets=frame_offsets,
            allow_clipping=True,
        )
        framing["canvas_size"] = list(config.canvas_size)
        framing["pivot"] = list(output_origin)
    return config, framing


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
    character_height: float | None = None,
    height_reference: str | int = "median",
    canvas_auto: bool = False,
    foot_lock: bool = False,
    write_trimmed: bool = True,
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
    character_height: キャラ本体の高さ（出力px）。アルファから測った本体の高さを基準に倍率を決める（倍率と足元は全フレーム固定）。
    height_reference: 身長の基準（median=中央値〈既定〉/ first=最初のフレーム / フレーム番号〈1始まり〉）。
    canvas_auto: character_height と併用。全フレームの張り出しが収まる最小のCanvasにする（見切れなし）。
    foot_lock: character_height と併用。各フレームの本体の下端を基準の足元にそろえる（縦方向の平行移動のみ・倍率は不変）。
    元動画で踏み込み中などに接地線がずれる素材向け。跳躍のような本物の浮きも打ち消すので既定はオフ。
    write_trimmed: 各フレームを切り詰めた画像とオフセット（trimmed_frames/, trim_manifest.json）も出力する。
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
    height_reference = _parse_height_reference(height_reference)
    if character_height is None:
        if canvas_auto:
            raise ValueError("Canvas自動は、キャラの身長（character_height）を指定したときだけ使えます")
        if foot_lock:
            raise ValueError("足元ロックは、キャラの身長（character_height）を指定したときだけ使えます")
        resolved_config = _with_derived_origins(config or default_sequence_config(canvas_size), frames, fit_percentile)
        extras["framing"] = {"fit_percentile": fit_percentile, "clipping_allowed": resolved_config.allow_clipping}
    else:
        if fit_percentile < 100:
            raise ValueError("キャラの身長の指定と fit_percentile（100未満）は同時に使えません")
        base_config = config or default_sequence_config(canvas_size)
        if base_config.scale_override is not None:
            raise ValueError("キャラの身長の指定と固定倍率（scale）は同時に使えません")
        resolved_config, framing = _with_character_height(
            base_config, frames, character_height, height_reference, canvas_auto, foot_lock
        )
        extras["framing"] = {**framing, "clipping_allowed": resolved_config.allow_clipping}
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
        write_trimmed=write_trimmed,
        progress=progress,
        **compile_kwargs,
    )
    if key_background and keyed.warnings:
        result = replace(result, warnings=(*result.warnings, *keyed.warnings))
    return result


__all__ = ["compile_character_frame_directory", "default_sequence_config"]
