"""アルファからキャラ本体の大きさ（身長）の基準を測る。

槍・剣・残像のような細長い部分は、動きによって伸び縮みして全体のbboxを大きくブレさせる。
形態学的な開き処理（細い部分を取り除く）をしてから最大の連結成分を取ることで、
本体（頭〜足）の範囲だけを決定論的に取り出す。
"""

from __future__ import annotations

from typing import Sequence

import cv2
import numpy as np

# 開き処理の直径は、画像の短辺のこの割合（512pxで8px）。細い柄（数px）は消え、腕・胴・脚は残る。
BODY_KERNEL_RATIO = 1 / 64
MIN_BODY_KERNEL = 3


def body_kernel_size(image_size: tuple[int, int]) -> int:
    """画像サイズに応じた開き処理の直径（奇数）。"""
    size = max(MIN_BODY_KERNEL, round(min(image_size) * BODY_KERNEL_RATIO))
    return size if size % 2 == 1 else size + 1


def measure_body(alpha: np.ndarray) -> tuple[int, int, int, int] | None:
    """可視画素のマスク (H, W) から、本体の範囲 (left, top, right, bottom)（right/bottomは含まない）を返す。

    細い部分を開き処理で取り除き、残った中で面積が最大の連結成分（8近傍）の範囲を本体とする。
    本体が小さすぎて開き処理で消える場合は、可視画素全体の範囲にする。可視画素が無ければNone。
    """
    mask = (np.asarray(alpha) > 0).astype(np.uint8)
    if not mask.any():
        return None
    height, width = mask.shape
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (body_kernel_size((width, height)),) * 2)
    opened = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    if not opened.any():
        opened = mask
    count, _, stats, _ = cv2.connectedComponentsWithStats(opened, connectivity=8)
    best = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    left, top, w, h = (int(stats[best, cv2.CC_STAT_LEFT]), int(stats[best, cv2.CC_STAT_TOP]),
                       int(stats[best, cv2.CC_STAT_WIDTH]), int(stats[best, cv2.CC_STAT_HEIGHT]))
    return left, top, left + w, top + h


def reference_body_height(heights: Sequence[float], reference: str | int = "median") -> float:
    """各フレームの本体の高さから、身長の基準値を決める。

    "median": 中央値（既定。踏み込み・跳躍などが一部のコマだけなら影響を受けにくい）
    "first" : 最初のフレーム（待機の立ちポーズから始まる素材向け）
    整数N  : Nフレーム目（1始まり。基準にしたいポーズのコマを直接指定）
    """
    if not heights:
        raise ValueError("身長を測れるフレームがありません")
    if reference == "median":
        return float(np.median(np.asarray(heights, dtype=np.float64)))
    if reference == "first":
        return float(heights[0])
    if isinstance(reference, int) and not isinstance(reference, bool):
        if not 1 <= reference <= len(heights):
            raise ValueError(f"基準フレーム番号は1〜{len(heights)}で指定してください")
        return float(heights[reference - 1])
    raise ValueError("height_reference は median / first / フレーム番号（整数）のいずれかです")


__all__ = ["body_kernel_size", "measure_body", "reference_body_height"]
