"""透過GIFの書き出しと、再読込による画素一致の検証。

GIFの制約:
- 時間は10ms刻み。24fps（41.67ms）は累積丸めで 40,40,40,50ms… と割り振り、総時間を保つ。
- 透過は二値。パレットの0番を透過に使うので、不透明色は最大255色。
- Pillowは連続する同一フレームを結合して時間を延ばす。フレーム数ではなく時刻で照合する。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
from PIL import Image, ImageSequence

MAX_GIF_FPS = 100.0
MAX_OPAQUE_COLORS = 255
_VISIBLE_ALPHA = 128


@dataclass(frozen=True)
class GifExportResult:
    path: Path
    sha256: str
    fps: float
    durations_ms: tuple[int, ...]
    loop: int | None
    frame_count: int
    color_count: int
    size: tuple[int, int]
    verified: bool

    @property
    def total_ms(self) -> int:
        return sum(self.durations_ms)

    def report_as_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "sha256": self.sha256,
            "fps": self.fps,
            "frame_count": self.frame_count,
            "durations_ms": list(self.durations_ms),
            "total_ms": self.total_ms,
            "loop": self.loop,
            "color_count": self.color_count,
            "size": list(self.size),
            "verified": self.verified,
            "transparency": "binary (palette index 0)",
        }


def gif_durations_ms(frame_count: int, fps: float) -> tuple[int, ...]:
    """fpsから各フレームの表示時間(ms)を作る。10ms単位の累積丸めで総時間の誤差を1単位未満に保つ。"""
    if frame_count < 1:
        raise ValueError("frame_count must be positive")
    if not 0 < fps <= MAX_GIF_FPS:
        raise ValueError(f"fps は 0 より大きく {MAX_GIF_FPS:g} 以下にしてください（GIFは10ms刻み）")
    durations: list[int] = []
    elapsed = 0
    for index in range(frame_count):
        boundary = max(elapsed + 1, int(np.floor((index + 1) * 100.0 / fps + 0.5)))
        durations.append((boundary - elapsed) * 10)
        elapsed = boundary
    return tuple(durations)


def _rgba_arrays(frames: Sequence[Image.Image]) -> list[np.ndarray]:
    if not frames:
        raise ValueError("at least one frame is required")
    arrays = [np.asarray(frame.convert("RGBA")) for frame in frames]
    if any(array.shape != arrays[0].shape for array in arrays):
        raise ValueError("all animation frames must have the same size")
    return arrays


def _pack(rgb: np.ndarray) -> np.ndarray:
    rgb = rgb.astype(np.uint32)
    return (rgb[..., 0] << 16) | (rgb[..., 1] << 8) | rgb[..., 2]


def save_animated_gif(
    frames: Sequence[Image.Image],
    path: Path | str,
    *,
    fps: float = 24.0,
    loop: int | None = 0,
    verify: bool = True,
) -> GifExportResult:
    """RGBAフレーム列を全フレーム共通パレットの透過GIFに書き出す。

    loop=0 は無限ループ、N は初回再生後にN回繰り返す、None はループ拡張なし（1回だけ再生）。
    アルファは128以上を不透明として二値化する。verify=True なら書き出し後に再読込して
    全フレームの可視RGB・アルファの一致を確認し、ずれがあれば例外にする。
    """
    if loop is not None and loop < 0:
        raise ValueError("loop must be non-negative or None")
    arrays = _rgba_arrays(frames)
    durations = gif_durations_ms(len(arrays), fps)
    visible = [array[:, :, 3] >= _VISIBLE_ALPHA for array in arrays]
    packed = [_pack(array[:, :, :3]) for array in arrays]
    colors = np.unique(np.concatenate([p[v] for p, v in zip(packed, visible)])) if any(
        v.any() for v in visible
    ) else np.zeros(0, np.uint32)
    if len(colors) > MAX_OPAQUE_COLORS:
        raise ValueError(
            f"GIFに使える不透明色は{MAX_OPAQUE_COLORS}色までです（{len(colors)}色ありました）"
        )
    palette = [0, 0, 0]  # 0番 = 透過
    for color in colors:
        palette += [int(color >> 16) & 255, int(color >> 8) & 255, int(color) & 255]
    palette += [0] * (768 - len(palette))

    images: list[Image.Image] = []
    for p, v in zip(packed, visible):
        index = np.zeros(p.shape, np.uint8)
        index[v] = np.searchsorted(colors, p[v]).astype(np.uint8) + 1
        image = Image.fromarray(index, "P")
        image.putpalette(palette)
        images.append(image)

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    options: dict[str, object] = dict(
        save_all=True,
        append_images=images[1:],
        duration=list(durations),
        disposal=2,
        transparency=0,
        optimize=False,
    )
    if loop is not None:
        options["loop"] = loop
    images[0].save(destination, "GIF", **options)

    if verify:
        verify_gif_matches_frames(destination, frames, durations)
    return GifExportResult(
        destination,
        hashlib.sha256(destination.read_bytes()).hexdigest(),
        float(fps),
        durations,
        loop,
        len(arrays),
        int(len(colors)),
        (arrays[0].shape[1], arrays[0].shape[0]),
        verify,
    )


def verify_gif_matches_frames(
    path: Path | str,
    frames: Sequence[Image.Image],
    durations_ms: Sequence[int],
) -> None:
    """GIFを再読込し、各元フレームの表示開始時刻に出ている画像の可視RGB・アルファが一致することを確認する。

    Pillowが同一フレームを結合しても、時刻で照合するので正しく検証できる。
    """
    arrays = _rgba_arrays(frames)
    if len(durations_ms) != len(arrays):
        raise ValueError("durations_ms must contain one value per frame")
    decoded: list[tuple[int, int, np.ndarray]] = []  # (開始ms, 終了ms, RGBA)
    elapsed = 0
    with Image.open(path) as opened:
        for frame in ImageSequence.Iterator(opened):
            duration = int(frame.info.get("duration", 0))
            decoded.append((elapsed, elapsed + duration, np.asarray(frame.convert("RGBA")).copy()))
            elapsed += duration
    if elapsed != sum(durations_ms):
        raise RuntimeError(f"GIFの総時間が一致しません: {elapsed}ms != {sum(durations_ms)}ms")
    start = 0
    for index, (array, duration) in enumerate(zip(arrays, durations_ms)):
        shown = next((image for begin, end, image in decoded if begin <= start < end), None)
        if shown is None or shown.shape != array.shape:
            raise RuntimeError(f"GIFのフレーム{index + 1}を読み戻せませんでした")
        expected_visible = array[:, :, 3] >= _VISIBLE_ALPHA
        actual_visible = shown[:, :, 3] >= _VISIBLE_ALPHA
        if not np.array_equal(expected_visible, actual_visible):
            raise RuntimeError(f"GIFのフレーム{index + 1}: 透過の範囲が元と一致しません")
        if not np.array_equal(shown[expected_visible][:, :3], array[expected_visible][:, :3]):
            raise RuntimeError(f"GIFのフレーム{index + 1}: 色が元と一致しません")
        start += duration


__all__ = [
    "GifExportResult",
    "gif_durations_ms",
    "save_animated_gif",
    "verify_gif_matches_frames",
]
