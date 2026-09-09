"""元絵そのものの性質を測る。

出力Canvasサイズとpalette上限をどう選ぶかは元絵で決まるため
（docs/canvas_scale_study.md）、その判断材料をここで用意する。
画像の加工は一切しない。読み取るだけ。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import gcd
from pathlib import Path

import numpy as np
from PIL import Image, UnidentifiedImageError

# よく使う比だけ名前で出す。それ以外は約分した比をそのまま見せる。
_NAMED_ASPECTS: tuple[tuple[int, int], ...] = (
    (1, 1), (4, 3), (3, 4), (3, 2), (2, 3), (16, 9), (9, 16), (2, 1), (1, 2), (5, 4), (16, 10)
)
_MAX_DOT_PITCH = 64
_GRID_ALIGNMENT_THRESHOLD = 0.98


@dataclass(frozen=True)
class SourceInfo:
    """元絵から読み取った、変換の判断に使う値。"""

    path: Path
    size: tuple[int, int]
    pixel_count: int
    aspect_ratio: float
    aspect_label: str
    visible_colors: int
    dot_pitch: int
    apparent_grid: int
    has_block_grid: bool
    semi_alpha_ratio: float

    @property
    def is_square(self) -> bool:
        return self.size[0] == self.size[1]


def _aspect_label(width: int, height: int) -> str:
    """縦横比を読める形にする。よくある比は名前どおりに出す。"""
    divisor = gcd(width, height) or 1
    reduced = (width // divisor, height // divisor)
    if reduced in _NAMED_ASPECTS:
        return f"{reduced[0]}:{reduced[1]}"
    ratio = width / height
    for named in _NAMED_ASPECTS:
        if abs(ratio - named[0] / named[1]) < 0.01:
            return f"{named[0]}:{named[1]}"
    if max(reduced) > 40:
        return f"{ratio:.2f}:1"
    return f"{reduced[0]}:{reduced[1]}"


def _change_positions(rgb: np.ndarray, axis: int) -> np.ndarray:
    """色が変わる座標。拡大されたドット絵ならピッチの倍数へ揃う。"""
    values = rgb if axis == 0 else np.swapaxes(rgb, 0, 1)
    changed = np.any(values[:, 1:, :] != values[:, :-1, :], axis=2)
    if changed.shape[1] >= 2:
        # 端の1画素は切り抜き境界で必ず変わるので数えない
        changed[:, 0] = False
        changed[:, -1] = False
    return np.nonzero(changed)[1] + 1


def _detect_dot_pitch(rgb: np.ndarray) -> int:
    """1ドットが何画素で描かれているかを、色変化位置の周期性から求める。

    1 は「格子なし」（ネイティブ解像度、または連続階調）。
    """
    positions = np.concatenate([_change_positions(rgb, 0), _change_positions(rgb, 1)])
    if positions.size < 32:
        return 1
    best = 1
    for candidate in range(2, _MAX_DOT_PITCH + 1):
        if float((positions % candidate == 0).mean()) >= _GRID_ALIGNMENT_THRESHOLD:
            best = candidate
    return best


def describe_source(path: Path | str) -> SourceInfo:
    """元絵を読み取り、変換の判断に使う値をまとめて返す。"""
    source = Path(path)
    try:
        with Image.open(source) as opened:
            rgba = opened.convert("RGBA")
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError(f"元絵を読み取れません: {source}") from exc

    array = np.asarray(rgba)
    alpha = array[..., 3]
    rgb = array[..., :3]
    visible = alpha > 0
    width, height = rgba.size

    if visible.any():
        colors = np.unique(rgb[visible].reshape(-1, 3), axis=0)
        visible_colors = int(len(colors))
        semi = float(((alpha > 0) & (alpha < 255)).sum() / visible.sum())
    else:
        visible_colors = 0
        semi = 0.0

    pitch = _detect_dot_pitch(rgb)
    return SourceInfo(
        path=source,
        size=(width, height),
        pixel_count=width * height,
        aspect_ratio=width / height,
        aspect_label=_aspect_label(width, height),
        visible_colors=visible_colors,
        dot_pitch=pitch,
        apparent_grid=int(round(width / pitch)),
        has_block_grid=pitch > 1,
        semi_alpha_ratio=semi,
    )


__all__ = ["SourceInfo", "describe_source"]
