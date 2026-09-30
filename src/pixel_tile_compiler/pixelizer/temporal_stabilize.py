"""連番の色を時間方向に安定させる（パレット境界での色の行き来＝ちらつきを抑える）。

共有パレットへの量子化は、元の色がほぼ同じでもフレームごとに最近色が入れ替わることがある。
ここでは各画素で「前フレームで使った色が、今フレームの元の色から見て、今回の色より
margin（RGB距離）以内にしか劣らないなら、前フレームの色を維持する」（ヒステリシス）。

- 輪郭（アルファ）は変えない。色は前フレームに実在した色（＝共有パレット内）にだけ入れ替えるので、
  パレット上限と「共有パレット外へ出ない」保証は保たれる。
- 前から順に処理する決定論的な処理。元の色が大きく変わった画素（本物の動き）は入れ替えない。
- アウトライン色は保護する（入れ替え元にも先にもしない）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
from PIL import Image

DEFAULT_STABILIZE_MARGIN = 12.0


@dataclass(frozen=True)
class StabilizationResult:
    frames: tuple[Image.Image, ...]
    margin: float
    swapped_px: tuple[int, ...]  # フレームごとに前フレームの色へ入れ替えた画素数（先頭は0）

    def report_as_dict(self) -> dict[str, object]:
        return {
            "enabled": True,
            "method": "temporal_palette_hysteresis",
            "margin": self.margin,
            "swapped_px": list(self.swapped_px),
            "total_swapped_px": int(sum(self.swapped_px)),
        }


def stabilize_palette_flicker(
    source_frames: Sequence[Image.Image],
    final_frames: Sequence[Image.Image],
    *,
    margin: float = DEFAULT_STABILIZE_MARGIN,
    protect_colors: Sequence[tuple[int, int, int]] = (),
) -> StabilizationResult:
    """final_frames の色を、source_frames（整列後の元の色）を基準にヒステリシスで安定させる。"""
    if margin <= 0:
        raise ValueError("margin must be positive")
    if len(source_frames) != len(final_frames) or not final_frames:
        raise ValueError("source_frames and final_frames must have the same non-zero length")
    protected = np.array(list(protect_colors), dtype=np.int16).reshape(-1, 3)

    def is_protected(rgb: np.ndarray) -> np.ndarray:
        if not len(protected):
            return np.zeros(rgb.shape[:2], dtype=bool)
        return (rgb[..., None, :].astype(np.int16) == protected).all(axis=-1).any(axis=-1)

    outputs: list[np.ndarray] = []
    swapped: list[int] = []
    for index, (source, final) in enumerate(zip(source_frames, final_frames)):
        src = np.asarray(source.convert("RGBA")).astype(np.float32)
        cur = np.asarray(final.convert("RGBA")).copy()
        if src.shape != cur.shape:
            raise ValueError("source_frames and final_frames must have the same size")
        count = 0
        if index > 0:
            prev = outputs[-1]
            both = (cur[..., 3] > 0) & (prev[..., 3] > 0) & (src[..., 3] > 0)
            differs = (cur[..., :3] != prev[..., :3]).any(axis=2)
            candidate = both & differs & ~is_protected(cur[..., :3]) & ~is_protected(prev[..., :3])
            if candidate.any():
                d_prev = np.linalg.norm(src[..., :3] - prev[..., :3].astype(np.float32), axis=2)
                d_cur = np.linalg.norm(src[..., :3] - cur[..., :3].astype(np.float32), axis=2)
                swap = candidate & (d_prev <= d_cur + margin)
                cur[swap, :3] = prev[swap, :3]
                count = int(swap.sum())
        outputs.append(cur)
        swapped.append(count)
    return StabilizationResult(
        tuple(Image.fromarray(array, "RGBA") for array in outputs), float(margin), tuple(swapped)
    )


__all__ = ["DEFAULT_STABILIZE_MARGIN", "StabilizationResult", "stabilize_palette_flicker"]
