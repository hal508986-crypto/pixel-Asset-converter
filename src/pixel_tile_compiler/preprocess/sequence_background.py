"""連番フレーム全体で背景色を1回だけ決め、単色背景（緑・白など）を透過にする。

フレームごとに背景色を自動判定すると判定が揺れて、フレーム間でキャラの縁がちらつく。
ここでは全フレームの外周から背景色を1つ決め、全フレームに同じ色・同じ許容差を適用する。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Sequence

import cv2
import numpy as np
from PIL import Image

from pixel_tile_compiler.io.frame_sequence import frame_label
from pixel_tile_compiler.preprocess.background import _parse_color

KeyMode = Literal["connected", "global", "auto"]

DEFAULT_BACKGROUND_TOLERANCE = 30
# auto: 背景色のチャンネル差（最大-最小）がこれ以上の鮮やかな色（緑・青緑など）は global、
# 白・グレー系は connected。鮮やかな背景色はキャラ内部に現れにくいが、白はハイライトに現れやすい。
AUTO_GLOBAL_MIN_SPREAD = 48
DEFAULT_MIN_BORDER_COVERAGE = 0.5
# 外周と繋がらず背景色に近いまま残った画素がこれ以上あるフレームに警告する
ENCLOSED_WARNING_PIXELS = 8
_VISIBLE_ALPHA = 128


@dataclass(frozen=True)
class SequenceBackgroundResult:
    frames: tuple[Image.Image, ...]
    color: tuple[int, int, int]
    tolerance: int
    mode: Literal["connected", "global"]
    color_source: Literal["specified", "estimated"]
    border_coverage: float | None
    frame_reports: tuple[dict[str, object], ...]
    warnings: tuple[str, ...] = ()
    mode_requested: str = ""
    choke_px: int = 0

    def report_as_dict(self) -> dict[str, object]:
        return {
            "color": "#{:02X}{:02X}{:02X}".format(*self.color),
            "rgb": list(self.color),
            "tolerance": self.tolerance,
            "mode": self.mode,
            "mode_requested": self.mode_requested or self.mode,
            "choke_px": self.choke_px,
            "color_source": self.color_source,
            "border_coverage": self.border_coverage,
            "frames": [dict(report) for report in self.frame_reports],
            "warnings": list(self.warnings),
        }


def _as_arrays(frames: Sequence[Image.Image]) -> list[np.ndarray]:
    if not frames:
        raise ValueError("at least one frame is required")
    arrays = [np.asarray(frame.convert("RGBA")) for frame in frames]
    if any(array.shape != arrays[0].shape for array in arrays):
        raise ValueError("all animation frames must have the same size")
    return arrays


def _border_ring(array: np.ndarray) -> np.ndarray:
    """外周1画素の可視画素を (N, 3) で返す。"""
    top, bottom = array[0, :], array[-1, :]
    left, right = array[1:-1, 0], array[1:-1, -1]
    ring = np.concatenate([top, bottom, left, right], axis=0)
    return ring[ring[:, 3] >= _VISIBLE_ALPHA][:, :3]


def _distance(rgb: np.ndarray, color: tuple[int, int, int]) -> np.ndarray:
    return np.linalg.norm(rgb.astype(np.int16) - np.asarray(color, dtype=np.int16), axis=-1)


def estimate_sequence_background(
    frames: Sequence[Image.Image],
    *,
    tolerance: int = DEFAULT_BACKGROUND_TOLERANCE,
    min_border_coverage: float = DEFAULT_MIN_BORDER_COVERAGE,
) -> tuple[tuple[int, int, int], float]:
    """全フレームの外周から背景色を推定し、(色, 外周が背景色に収まる割合) を返す。

    外周の最頻色帯を選び、その近傍画素の各チャンネル中央値（偶数個は小さい側）を採る。
    同数のときは色値の小さい帯を選ぶので、同じ入力なら常に同じ結果になる。
    """
    if tolerance < 0:
        raise ValueError("tolerance must be non-negative")
    ring = np.concatenate([_border_ring(array) for array in _as_arrays(frames)], axis=0)
    if len(ring) == 0:
        raise ValueError("外周に可視画素がないため背景色を推定できません（すでに透過済みの可能性があります）")
    quantized = (ring >> 3).astype(np.int32)
    keys = (quantized[:, 0] << 10) | (quantized[:, 1] << 5) | quantized[:, 2]
    unique, counts = np.unique(keys, return_counts=True)
    winner = int(unique[int(np.argmax(counts))])
    center = tuple(int(((winner >> shift) & 31) * 8 + 4) for shift in (10, 5, 0))
    near = ring[_distance(ring, center) <= tolerance]  # type: ignore[arg-type]
    if len(near) == 0:
        near = ring[keys == winner]
    color = tuple(int(np.sort(near[:, channel])[(len(near) - 1) // 2]) for channel in range(3))
    coverage = float(np.count_nonzero(_distance(ring, color) <= tolerance)) / len(ring)  # type: ignore[arg-type]
    if coverage < min_border_coverage:
        raise ValueError(
            "外周が単色背景として推定できません"
            f"（最有力の背景色 #{color[0]:02X}{color[1]:02X}{color[2]:02X} に収まる外周は{coverage:.0%}）。"
            "背景色を明示指定してください"
        )
    return color, coverage  # type: ignore[return-value]


def _border_connected(candidate: np.ndarray) -> np.ndarray:
    """外周に触れている候補領域（8近傍）だけを返す。"""
    count, labels = cv2.connectedComponents(candidate.astype(np.uint8), connectivity=8)
    if count <= 1:
        return np.zeros_like(candidate)
    edge = np.concatenate([labels[0, :], labels[-1, :], labels[:, 0], labels[:, -1]])
    touching = np.unique(edge)
    touching = touching[touching != 0]
    return np.isin(labels, touching)


def remove_sequence_background(
    frames: Sequence[Image.Image],
    *,
    color: str | tuple[int, int, int] | None = None,
    tolerance: int = DEFAULT_BACKGROUND_TOLERANCE,
    mode: KeyMode = "connected",
    min_border_coverage: float = DEFAULT_MIN_BORDER_COVERAGE,
    choke_px: int = 0,
) -> SequenceBackgroundResult:
    """連番全体に同じ背景色・許容差を適用して、単色背景を透過にする。

    color未指定なら全フレームの外周から1回だけ推定する。
    mode="connected": 外周と繋がる背景色近傍だけ消す（白背景でキャラ内部の白を守る）。
    mode="global": フレーム内の背景色近傍を全部消す（緑背景で腕の輪の中なども消す）。
    mode="auto": 背景色が鮮やか（チャンネル差48以上）なら global、白・グレー系なら connected。
    choke_px: 除去後の輪郭を8近傍でN画素内側へ削り、背景との混色（縁のにじみ）を落とす。
    画像の端に接する輪郭は削らない（フレームの外へ続くキャラを欠けさせない）。
    出力alphaは0/255の二値で、透過画素のRGBは0にそろえる。
    """
    if tolerance < 0:
        raise ValueError("tolerance must be non-negative")
    if mode not in {"connected", "global", "auto"}:
        raise ValueError("mode must be connected, global, or auto")
    if choke_px < 0:
        raise ValueError("choke_px must be non-negative")
    requested_mode = mode
    arrays = _as_arrays(frames)
    coverage: float | None
    if color is None:
        resolved, coverage = estimate_sequence_background(
            frames, tolerance=tolerance, min_border_coverage=min_border_coverage
        )
        source: Literal["specified", "estimated"] = "estimated"
    else:
        resolved = _parse_color(color) if isinstance(color, str) else tuple(int(c) for c in color)  # type: ignore[assignment]
        if len(resolved) != 3 or any(not 0 <= c <= 255 for c in resolved):
            raise ValueError("背景色はRGB各0〜255の3値で指定してください")
        coverage = None
        source = "specified"

    if mode == "auto":
        mode = "global" if max(resolved) - min(resolved) >= AUTO_GLOBAL_MIN_SPREAD else "connected"
    keyed: list[Image.Image] = []
    reports: list[dict[str, object]] = []
    warnings: list[str] = []
    for index, array in enumerate(arrays):
        name = frame_label(index, len(arrays))
        visible = array[:, :, 3] >= _VISIBLE_ALPHA
        candidate = visible & (_distance(array[:, :, :3], resolved) <= tolerance)  # type: ignore[arg-type]
        removed = candidate if mode == "global" else _border_connected(candidate)
        keep = visible & ~removed
        choked = 0
        if choke_px:
            eroded = cv2.erode(keep.astype(np.uint8), np.ones((3, 3), np.uint8), iterations=choke_px) > 0
            choked = int(np.count_nonzero(keep & ~eroded))
            keep = eroded
        out = array.copy()
        out[:, :, 3] = np.where(keep, 255, 0)
        out[~keep, :3] = 0
        keyed.append(Image.fromarray(out, mode="RGBA"))
        enclosed = int(np.count_nonzero(candidate & ~removed))
        kept = int(np.count_nonzero(keep))
        report = {
            "frame_id": name,
            "removed_px": int(np.count_nonzero(visible & removed)),
            "kept_px": kept,
            "enclosed_bg_like_px": enclosed,
        }
        if choke_px:
            report["choked_px"] = choked
        reports.append(report)
        if kept == 0:
            warnings.append(f"{name}: 背景除去後に何も残りませんでした")
        elif report["removed_px"] == 0:
            warnings.append(f"{name}: 背景色に近い画素が外周にありません（背景が除去されていません）")
        if enclosed >= ENCLOSED_WARNING_PIXELS:
            warnings.append(
                f"{name}: 背景色に近い領域が内側に{enclosed}画素残っています"
                "（穴の中の背景なら mode=global を検討）"
            )
    return SequenceBackgroundResult(
        tuple(keyed), resolved, tolerance, mode, source, coverage, tuple(reports), tuple(warnings),  # type: ignore[arg-type]
        requested_mode, choke_px,
    )


__all__ = [
    "DEFAULT_BACKGROUND_TOLERANCE",
    "SequenceBackgroundResult",
    "estimate_sequence_background",
    "remove_sequence_background",
]
