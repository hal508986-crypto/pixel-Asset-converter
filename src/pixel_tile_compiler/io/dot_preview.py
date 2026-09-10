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
    board = np.zeros((height, width, 4), dtype=np.uint8)
    ys = (np.arange(height) // _CHECKER_TILE)[:, None]
    xs = (np.arange(width) // _CHECKER_TILE)[None, :]
    light = ((ys + xs) % 2) == 0
    board[..., :3] = np.where(light[..., None], np.array(_CHECKER_LIGHT), np.array(_CHECKER_DARK))
    board[..., 3] = 255
    return Image.fromarray(board, "RGBA")


def render_dot_inspect_preview(image: Image.Image, scale: int | None = None) -> Image.Image:
    """検査用。1ドット境界に格子線を引き、8ドットごとに濃い線を入れる。

    透明部分は市松模様の上に合成し、Canvasの範囲とアルファの抜けを同時に見せる。
    """
    rgba = image.convert("RGBA")
    factor = scale if scale is not None else resolve_dot_preview_scale(rgba.size)
    enlarged = _scaled(rgba, factor)
    canvas = _checkerboard(enlarged.size)
    canvas.alpha_composite(enlarged)

    pixels = np.array(canvas).astype(float)
    height, width = pixels.shape[:2]

    def blend(mask: np.ndarray, line: tuple[int, int, int, int]) -> None:
        alpha = line[3] / 255.0
        pixels[mask, :3] = pixels[mask, :3] * (1 - alpha) + np.array(line[:3]) * alpha

    columns = np.zeros(width, dtype=bool)
    rows = np.zeros(height, dtype=bool)
    columns[::factor] = True
    rows[::factor] = True
    minor_x = np.zeros((height, width), dtype=bool)
    minor_x[:, columns] = True
    minor_y = np.zeros((height, width), dtype=bool)
    minor_y[rows, :] = True
    blend(minor_x | minor_y, _MINOR_LINE)

    major_step = factor * DOT_PREVIEW_MAJOR_INTERVAL
    major_columns = np.zeros(width, dtype=bool)
    major_rows = np.zeros(height, dtype=bool)
    major_columns[::major_step] = True
    major_rows[::major_step] = True
    major_x = np.zeros((height, width), dtype=bool)
    major_x[:, major_columns] = True
    major_y = np.zeros((height, width), dtype=bool)
    major_y[major_rows, :] = True
    blend(major_x | major_y, _MAJOR_LINE)

    return Image.fromarray(pixels.clip(0, 255).astype(np.uint8), "RGBA")


def render_dot_showcase_preview(image: Image.Image, scale: int | None = None) -> Image.Image:
    """見せ物用。各ドットの右辺と下辺を暗くして、タイルが並ぶように見せる。

    透明なドットは透明のままにし、区切り線も入れない。
    """
    rgba = image.convert("RGBA")
    factor = scale if scale is not None else resolve_dot_preview_scale(rgba.size)
    enlarged = np.array(_scaled(rgba, factor)).astype(float)
    height, width = enlarged.shape[:2]

    edge = np.zeros((height, width), dtype=bool)
    if factor >= 2:
        edge[:, factor - 1 :: factor] = True
        edge[factor - 1 :: factor, :] = True
    # 透明なドットには何も足さない
    edge &= enlarged[..., 3] > 0
    enlarged[edge, :3] *= _SHOWCASE_EDGE_SCALE

    return Image.fromarray(enlarged.clip(0, 255).astype(np.uint8), "RGBA")


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
