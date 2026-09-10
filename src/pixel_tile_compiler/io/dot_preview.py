"""コンパイル結果からドット確認画像を作る。

仕様: docs/spec/dot_preview_spec.md

出力は等倍で1画素＝1ドットのため、格子を見せるには整数倍へ拡大する必要がある。
`final.png` そのものは変更せず、別ファイルとして書き出す。
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, UnidentifiedImageError

# 長辺がこの画素数前後に収まる整数倍を選ぶ
DOT_PREVIEW_TARGET_LONG_SIDE = 768
DOT_PREVIEW_MIN_SCALE = 4
DOT_PREVIEW_MAX_SCALE = 16
# 何ドットごとに濃い線を引くか（数えやすさのため）
DOT_PREVIEW_MAJOR_INTERVAL = 8

_MINOR_LINE = (0, 0, 0, 70)
_MAJOR_LINE = (0, 0, 0, 150)
_CHECKER_LIGHT = (214, 217, 221)
_CHECKER_DARK = (185, 190, 197)
_CHECKER_TILE = 8
# 見せ物用で、ドットの右辺・下辺をどれだけ暗くするか
_SHOWCASE_EDGE_SCALE = 0.72
# 一度に処理する行数。大きいCanvasでも一時配列を一定に保つ（仕様4.10節）
_BAND_ROWS = 512


def resolve_dot_preview_scale(size: tuple[int, int]) -> int:
    """長辺が目安に収まる整数倍を返す。下限4倍、上限16倍。"""
    long_side = max(int(size[0]), int(size[1]))
    if long_side < 1:
        raise ValueError("画像サイズが不正です")
    scale = DOT_PREVIEW_TARGET_LONG_SIDE // long_side
    return max(DOT_PREVIEW_MIN_SCALE, min(DOT_PREVIEW_MAX_SCALE, scale))


def _scaled(image: Image.Image, scale: int) -> Image.Image:
    if scale < 1:
        raise ValueError("拡大率は1以上でなければなりません")
    rgba = image.convert("RGBA")
    return rgba.resize((rgba.width * scale, rgba.height * scale), Image.Resampling.NEAREST)


def _checkerboard(size: tuple[int, int]) -> Image.Image:
    """透明部分を見せるための市松模様。"""
    width, height = size
    board = np.empty((height, width, 4), dtype=np.uint8)
    ys = (np.arange(height) // _CHECKER_TILE)[:, None]
    xs = (np.arange(width) // _CHECKER_TILE)[None, :]
    light = ((ys + xs) % 2) == 0
    # np.whereで組むと画素数ぶんのint64が要るため、面ごとに塗り分ける
    for channel in range(3):
        plane = board[..., channel]
        plane[...] = _CHECKER_DARK[channel]
        plane[light] = _CHECKER_LIGHT[channel]
    board[..., 3] = 255
    return Image.fromarray(board, "RGBA")


def _blend_ramp(values: np.ndarray, line: tuple[int, int, int, int]) -> np.ndarray:
    """階調へ半透明の線色を重ねる。切り捨てはまだしない。"""
    alpha = line[3] / 255.0
    return values * (1 - alpha) + np.asarray(line[:3], dtype=np.float64) * alpha


def _line_tables() -> tuple[np.ndarray, np.ndarray]:
    """細い線だけの対応表と、その上に濃い線を重ねた対応表（256階調 × RGB）。

    線色とアルファは固定なので、画素ごとに掛け算をやり直す必要がない。
    濃い線の間隔は細い線の整数倍なので、**濃い線の位置は必ず細い線の位置でもある**。
    交点は2回重なるため、途中で切り捨てずに2段ぶんを畳んだ表を別に用意する。
    """
    ramp = np.arange(256, dtype=np.float64)[:, None]
    minor = _blend_ramp(ramp, _MINOR_LINE)
    both = _blend_ramp(minor, _MAJOR_LINE)
    return (
        minor.clip(0, 255).astype(np.uint8),
        both.clip(0, 255).astype(np.uint8),
    )


def _darken_table(ratio: float) -> np.ndarray:
    """ドットの縁を暗くした結果の対応表。式は `floor(v*ratio)`。"""
    ramp = (np.arange(256, dtype=np.float64) * ratio).clip(0, 255).astype(np.uint8)
    return np.repeat(ramp[:, None], 3, axis=1)


def _map_through_table(pixels: np.ndarray, mask: np.ndarray, table: np.ndarray) -> None:
    """maskの当たるRGBを対応表で置き換える。行の帯に切って一時配列を抑える。"""
    for start in range(0, pixels.shape[0], _BAND_ROWS):
        band_mask = mask[start : start + _BAND_ROWS]
        if not band_mask.any():
            continue
        band = pixels[start : start + _BAND_ROWS]
        for channel in range(3):
            plane = band[..., channel]
            plane[band_mask] = table[plane[band_mask], channel]


def _line_mask(shape: tuple[int, int], step: int) -> np.ndarray:
    """step間隔の行と列に立つマスク。行ベクトルと列ベクトルの論理和で1枚だけ作る。"""
    height, width = shape
    rows = np.zeros(height, dtype=bool)
    rows[::step] = True
    columns = np.zeros(width, dtype=bool)
    columns[::step] = True
    return rows[:, None] | columns[None, :]


def render_dot_inspect_preview(image: Image.Image, scale: int | None = None) -> Image.Image:
    """検査用。1ドット境界に格子線を引き、8ドットごとに濃い線を入れる。

    透明部分は市松模様の上に合成し、Canvasの範囲とアルファの抜けを同時に見せる。
    """
    rgba = image.convert("RGBA")
    factor = scale if scale is not None else resolve_dot_preview_scale(rgba.size)
    enlarged = _scaled(rgba, factor)
    canvas = _checkerboard(enlarged.size)
    canvas.alpha_composite(enlarged)

    pixels = np.array(canvas)
    shape = (pixels.shape[0], pixels.shape[1])

    minor_table, both_table = _line_tables()
    major_mask = _line_mask(shape, factor * DOT_PREVIEW_MAJOR_INTERVAL)
    minor_only = _line_mask(shape, factor)
    np.logical_and(minor_only, ~major_mask, out=minor_only)
    _map_through_table(pixels, minor_only, minor_table)
    _map_through_table(pixels, major_mask, both_table)

    return Image.fromarray(pixels, "RGBA")


def render_dot_showcase_preview(image: Image.Image, scale: int | None = None) -> Image.Image:
    """見せ物用。各ドットの右辺と下辺を暗くして、タイルが並ぶように見せる。

    透明なドットは透明のままにし、区切り線も入れない。
    """
    rgba = image.convert("RGBA")
    factor = scale if scale is not None else resolve_dot_preview_scale(rgba.size)
    pixels = np.array(_scaled(rgba, factor))
    height, width = pixels.shape[:2]

    edge = np.zeros((height, width), dtype=bool)
    if factor >= 2:
        edge[:, factor - 1 :: factor] = True
        edge[factor - 1 :: factor, :] = True
    # 透明なドットには何も足さない
    edge &= pixels[..., 3] > 0
    _map_through_table(pixels, edge, _darken_table(_SHOWCASE_EDGE_SCALE))

    return Image.fromarray(pixels, "RGBA")


def write_dot_previews(final_path: Path | str, output_dir: Path | str | None = None) -> tuple[Path, ...]:
    """検査用と見せ物用の2枚を書き出し、そのパスを返す。"""
    source = Path(final_path)
    try:
        with Image.open(source) as opened:
            rgba = opened.convert("RGBA")
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError(f"コンパイル結果を読み取れません: {source}") from exc

    target_dir = Path(output_dir) if output_dir is not None else source.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    factor = resolve_dot_preview_scale(rgba.size)

    grid_path = target_dir / f"final_grid_{factor}x.png"
    dot_path = target_dir / f"final_dot_{factor}x.png"
    render_dot_inspect_preview(rgba, scale=factor).save(grid_path)
    render_dot_showcase_preview(rgba, scale=factor).save(dot_path)
    return (grid_path, dot_path)


__all__ = [
    "DOT_PREVIEW_MAJOR_INTERVAL",
    "DOT_PREVIEW_MAX_SCALE",
    "DOT_PREVIEW_MIN_SCALE",
    "render_dot_inspect_preview",
    "render_dot_showcase_preview",
    "resolve_dot_preview_scale",
    "write_dot_previews",
]
