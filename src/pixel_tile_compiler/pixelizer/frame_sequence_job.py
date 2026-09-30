"""PNG連番→GIFのジョブ層（CLIとGUIの共通部分。Qtに依存しない）。

設定の検証・設定からコア設定への変換・事前確認・実行・結果の要約をここに集め、
CLIとGUIで同じ検証メッセージ・同じ既定値・同じ処理になるようにする。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Callable

from pixel_tile_compiler.io.frame_sequence import load_frame_directory
from pixel_tile_compiler.pixelizer.character_animation import CharacterAnimationConfig
from pixel_tile_compiler.pixelizer.character_frame_sequence import (
    DEFAULT_STABILIZE_MARGIN,
    compile_character_frame_directory,
    default_sequence_config,
)
from pixel_tile_compiler.preprocess.background import _parse_color
from pixel_tile_compiler.preprocess.sequence_background import (
    AUTO_GLOBAL_MIN_SPREAD,
    DEFAULT_BACKGROUND_TOLERANCE,
    estimate_sequence_background,
)

BACKGROUND_MODES = ("auto", "connected", "global")
OUTLINE_CHOICES = ("off", "black", "white")
DETAIL_CHOICES = ("sparse", "balanced", "detailed")
MAX_CANVAS_SIDE = 4096

ProgressCallback = Callable[[str, int, int], None]


class FrameSequenceCancelled(Exception):
    """ユーザー操作で処理を中止した。既存の出力は変更されない。"""


@dataclass(frozen=True)
class FrameSequenceRequest:
    input_dir: Path
    output_dir: Path
    canvas_size: tuple[int, int] = (512, 512)
    palette_budget: int = 24
    fps: float = 24.0
    write_gif: bool = True
    play_once: bool = False
    loop: int = 0
    key_background: bool = True
    background_color: str | None = None  # None = 全フレームの外周から推定
    background_mode: str = "auto"
    background_tolerance: int = DEFAULT_BACKGROUND_TOLERANCE
    choke_px: int = 0
    stabilize_margin: float = DEFAULT_STABILIZE_MARGIN  # 0 = しない
    alpha_threshold: int = 16
    min_component_area: int = 3
    remove_isolated: bool = True
    scale: float | None = None
    fit_percentile: float = 100.0  # 100 = 全フレームが収まる倍率。未満は極端なコマの見切れを許して本体を大きく
    outline: str = "off"
    detail: str = "balanced"
    debug: bool = False

    def validate(self) -> None:
        """不正な設定は、対処が分かる日本語メッセージのValueErrorにする。"""
        if not Path(self.input_dir).is_dir():
            raise ValueError(f"入力フォルダが見つかりません: {self.input_dir}")
        if Path(self.output_dir).resolve() == Path(self.input_dir).resolve():
            raise ValueError("出力フォルダは入力フォルダと別にしてください")
        width, height = self.canvas_size
        if not (1 <= width <= MAX_CANVAS_SIDE and 1 <= height <= MAX_CANVAS_SIDE):
            raise ValueError(f"出力サイズは1〜{MAX_CANVAS_SIDE}の範囲で指定してください")
        if not 4 <= self.palette_budget <= 64:
            raise ValueError("パレット上限は4〜64で指定してください")
        if self.write_gif and not 0 < self.fps <= 100:
            raise ValueError("fpsは0より大きく100以下で指定してください（GIFは10ms刻み）")
        if self.loop < 0:
            raise ValueError("ループ回数は0以上で指定してください")
        if self.background_mode not in BACKGROUND_MODES:
            raise ValueError("背景の消し方はauto、connected、globalのいずれかです")
        if not 0 <= self.background_tolerance <= 255:
            raise ValueError("背景色の許容差は0〜255で指定してください")
        if self.choke_px < 0:
            raise ValueError("輪郭を削る画素数は0以上で指定してください")
        if self.stabilize_margin < 0:
            raise ValueError("色の安定化のmarginは0以上で指定してください（0でしない）")
        if not 0 <= self.alpha_threshold <= 255:
            raise ValueError("アルファ閾値は0〜255で指定してください")
        if self.min_component_area < 1:
            raise ValueError("孤立成分の最小面積は1以上で指定してください")
        if self.scale is not None and self.scale <= 0:
            raise ValueError("固定倍率は0より大きい値で指定してください")
        if not 0 < self.fit_percentile <= 100:
            raise ValueError("フィットの基準（百分位）は0より大きく100以下で指定してください")
        if self.outline not in OUTLINE_CHOICES:
            raise ValueError("outlineはoff、black、whiteのいずれかです")
        if self.detail not in DETAIL_CHOICES:
            raise ValueError("character_detailはsparse、balanced、detailedのいずれかです")
        if self.background_color:
            _parse_color(self.background_color)  # #RRGGBB でなければValueError

    def to_config(self) -> CharacterAnimationConfig:
        return replace(
            default_sequence_config(self.canvas_size),
            alpha_threshold=self.alpha_threshold,
            min_component_area_px=self.min_component_area,
            remove_isolated_components=self.remove_isolated,
            outline_width=1 if self.outline != "off" else 0,
            scale_override=self.scale,
        )


@dataclass(frozen=True)
class FrameSequenceInspection:
    """実行前の確認結果（GUIで「この設定で何が起きるか」を先に見せる）。"""

    frame_count: int
    frame_size: tuple[int, int]
    first_frame: Path
    background_color: str | None = None
    border_coverage: float | None = None
    suggested_mode: str | None = None
    background_error: str | None = None


def inspect_frame_directory(
    input_dir: Path | str,
    *,
    tolerance: int = DEFAULT_BACKGROUND_TOLERANCE,
    estimate_background: bool = True,
) -> FrameSequenceInspection:
    """PNG連番フォルダを読み、枚数・サイズ・推定される背景色を返す。読めなければValueError。"""
    paths, frames = load_frame_directory(input_dir)
    color = coverage = mode = error = None
    if estimate_background:
        try:
            rgb, coverage = estimate_sequence_background(frames, tolerance=tolerance)
            color = "#{:02X}{:02X}{:02X}".format(*rgb)
            mode = "global" if max(rgb) - min(rgb) >= AUTO_GLOBAL_MIN_SPREAD else "connected"
        except ValueError as exc:
            error = str(exc)
    return FrameSequenceInspection(len(frames), frames[0].size, paths[0], color, coverage, mode, error)


@dataclass(frozen=True)
class FrameSequenceSummary:
    output_root: Path
    frame_count: int
    final_dir: Path
    final_frame_paths: tuple[Path, ...]
    gif_path: Path | None
    gif_bytes: int | None
    gif_total_ms: int | None
    report_path: Path
    palette_colors: int
    background_color: str | None
    background_mode: str | None
    swapped_px: int | None
    sheets_written: bool
    warnings: tuple[str, ...] = field(default_factory=tuple)


def run_frame_sequence(
    request: FrameSequenceRequest,
    progress: ProgressCallback | None = None,
) -> FrameSequenceSummary:
    """検証→コンパイル→要約。progressから例外（FrameSequenceCancelled等）を投げると中止できる。"""
    request.validate()
    result = compile_character_frame_directory(
        request.input_dir,
        request.output_dir,
        config=request.to_config(),
        key_background=request.key_background,
        background_color=request.background_color or None,
        background_tolerance=request.background_tolerance,
        background_mode=request.background_mode,  # type: ignore[arg-type]
        background_choke_px=request.choke_px,
        gif_fps=request.fps if request.write_gif else None,
        gif_loop=None if request.play_once else request.loop,
        stabilize_margin=request.stabilize_margin if request.stabilize_margin > 0 else None,
        fit_percentile=request.fit_percentile,
        progress=progress,
        palette_budget=request.palette_budget,
        character_detail_level=request.detail,
        outline_color=request.outline,
        debug_enabled=request.debug,
    )
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    key = report.get("background_key") or {}
    gif = (report.get("animation") or {}).get("gif") or {}
    stabilization = report.get("stabilization")
    return FrameSequenceSummary(
        output_root=result.output_root,
        frame_count=len(result.final_frame_paths),
        final_dir=result.final_frame_paths[0].parent,
        final_frame_paths=result.final_frame_paths,
        gif_path=result.gif_path,
        gif_bytes=result.gif_path.stat().st_size if result.gif_path is not None else None,
        gif_total_ms=gif.get("total_ms"),
        report_path=result.report_path,
        palette_colors=int(report["final_palette"]["color_count"]),
        background_color=key.get("color"),
        background_mode=key.get("mode"),
        swapped_px=stabilization["total_swapped_px"] if stabilization else None,
        sheets_written=result.compiled_sheet_path is not None,
        warnings=result.warnings,
    )


__all__ = [
    "BACKGROUND_MODES",
    "DETAIL_CHOICES",
    "FrameSequenceCancelled",
    "FrameSequenceInspection",
    "FrameSequenceRequest",
    "FrameSequenceSummary",
    "OUTLINE_CHOICES",
    "inspect_frame_directory",
    "run_frame_sequence",
]
