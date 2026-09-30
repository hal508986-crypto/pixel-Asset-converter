"""連番PNGフォルダの読み込み。"""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image

_NUMBER = re.compile(r"(\d+)")


def frame_label(index: int, count: int) -> str:
    """連番経路のフレームID。ソート順が崩れないよう最低3桁でゼロ埋めする（F001…）。"""
    return f"F{index + 1:0{max(3, len(str(count)))}d}"


def _natural_key(path: Path) -> tuple[tuple[tuple[int, int, str], ...], str]:
    """f2 < f10 になる自然順。同順位は名前で確定させて決定論を保つ。"""
    parts = tuple(
        (0, int(part), "") if part.isdigit() else (1, 0, part.lower())
        for part in _NUMBER.split(path.stem)
        if part != ""
    )
    return parts, path.name


def list_frame_files(directory: Path | str) -> tuple[Path, ...]:
    """フォルダ直下のPNGを自然順で返す。"""
    root = Path(directory)
    if not root.is_dir():
        raise ValueError(f"フォルダが見つかりません: {root}")
    files = sorted(
        (path for path in root.iterdir() if path.is_file() and path.suffix.lower() == ".png"),
        key=_natural_key,
    )
    if not files:
        raise ValueError(f"PNG連番が見つかりません: {root}")
    return tuple(files)


def load_frame_directory(directory: Path | str) -> tuple[tuple[Path, ...], tuple[Image.Image, ...]]:
    """PNG連番を自然順で読み込み、(パス, RGBA画像) を返す。全フレーム同サイズが必須。"""
    files = list_frame_files(directory)
    frames: list[Image.Image] = []
    for path in files:
        with Image.open(path) as opened:
            frames.append(opened.convert("RGBA"))
    if any(frame.size != frames[0].size for frame in frames):
        sizes = sorted({frame.size for frame in frames})
        raise ValueError(f"連番フレームのサイズが揃っていません: {sizes}")
    return files, tuple(frames)


__all__ = ["frame_label", "list_frame_files", "load_frame_directory"]
