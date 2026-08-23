"""Small, dependency-free Efros/Freeman-style image quilting implementation."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Sequence

import numpy as np
from PIL import Image

from .patches import MaterialPatch, PatchDatabase


def minimum_error_vertical_seam(error: Sequence[Sequence[float]]) -> list[int]:
    """Return one minimum-cost column for every row of a vertical seam."""
    costs = np.asarray(error, dtype=np.float64)
    if costs.ndim != 2 or costs.shape[0] == 0 or costs.shape[1] == 0:
        raise ValueError("seam error must be a non-empty 2D matrix")
    accumulated = costs.copy()
    parent = np.zeros_like(accumulated, dtype=np.int32)
    for row in range(1, costs.shape[0]):
        for column in range(costs.shape[1]):
            left = max(0, column - 1)
            right = min(costs.shape[1], column + 2)
            previous = accumulated[row - 1, left:right]
            offset = int(np.argmin(previous))
            parent[row, column] = left + offset
            accumulated[row, column] += previous[offset]
    seam = [0] * costs.shape[0]
    seam[-1] = int(np.argmin(accumulated[-1]))
    for row in range(costs.shape[0] - 1, 0, -1):
        seam[row - 1] = int(parent[row, seam[row]])
    return seam


def minimum_error_horizontal_seam(error: Sequence[Sequence[float]]) -> list[int]:
    """Return one minimum-cost row for every column of a horizontal seam."""
    costs = np.asarray(error, dtype=np.float64)
    if costs.ndim != 2 or costs.shape[0] == 0 or costs.shape[1] == 0:
        raise ValueError("seam error must be a non-empty 2D matrix")
    return minimum_error_vertical_seam(costs.T)


@dataclass(frozen=True)
class MinimumErrorSeamSolver:
    """SeamSolver boundary kept replaceable by a future GraphCut implementation."""

    def vertical(self, error: Sequence[Sequence[float]]) -> list[int]:
        return minimum_error_vertical_seam(error)

    def horizontal(self, error: Sequence[Sequence[float]]) -> list[int]:
        return minimum_error_horizontal_seam(error)


class ImageQuilter:
    """Compose overlapping patches with minimum-error boundary cuts."""

    def __init__(self, database: PatchDatabase, seed: int = 42, seam_solver: MinimumErrorSeamSolver | None = None) -> None:
        self.database = database
        self.rng = random.Random(seed)
        self.seam_solver = seam_solver or MinimumErrorSeamSolver()

    def synthesize(self, size: tuple[int, int]) -> Image.Image:
        width, height = size
        if width < 1 or height < 1:
            raise ValueError("quilt size must be positive")
        patch_size = min(self.database.patch_size, width, height)
        overlap = min(self.database.overlap, patch_size - 1)
        step = max(1, patch_size - overlap)
        x_positions = _positions(width, patch_size, step)
        y_positions = _positions(height, patch_size, step)
        canvas = np.zeros((height, width, 4), dtype=np.uint8)
        occupied = np.zeros((height, width), dtype=bool)
        for y in y_positions:
            for x in x_positions:
                patch = self._select_patch(canvas, occupied, x, y, patch_size, overlap)
                candidate = np.asarray(patch.image.resize((patch_size, patch_size), Image.Resampling.BILINEAR), dtype=np.uint8)
                self._place(canvas, occupied, candidate, x, y, overlap)
        return Image.fromarray(canvas, mode="RGBA")

    def _select_patch(
        self,
        canvas: np.ndarray,
        occupied: np.ndarray,
        x: int,
        y: int,
        patch_size: int,
        overlap: int,
    ) -> MaterialPatch:
        if not occupied.any() or overlap == 0:
            return self.database.sample(self.rng)
        scores: list[tuple[float, MaterialPatch]] = []
        for patch in self.database.patches:
            candidate = np.asarray(patch.image.resize((patch_size, patch_size), Image.Resampling.BILINEAR), dtype=np.float32)
            error = 0.0
            count = 0
            if x > 0:
                existing = canvas[y : y + patch_size, x : x + overlap, :3].astype(np.float32)
                target = candidate[:, :overlap, :3]
                error += float(np.square(existing - target).mean())
                count += 1
            if y > 0:
                existing = canvas[y : y + overlap, x : x + patch_size, :3].astype(np.float32)
                target = candidate[:overlap, :, :3]
                error += float(np.square(existing - target).mean())
                count += 1
            scores.append((error / max(1, count), patch))
        scores.sort(key=lambda item: (item[0], item[1].position))
        top = scores[: max(1, min(3, len(scores)))]
        return top[self.rng.randrange(len(top))][1]

    def _place(self, canvas: np.ndarray, occupied: np.ndarray, candidate: np.ndarray, x: int, y: int, overlap: int) -> None:
        patch_height, patch_width = candidate.shape[:2]
        if not occupied[y : y + patch_height, x : x + patch_width].any():
            canvas[y : y + patch_height, x : x + patch_width] = candidate
            occupied[y : y + patch_height, x : x + patch_width] = True
            return
        if x > 0 and overlap > 0:
            existing = canvas[y : y + patch_height, x : x + overlap, :3].astype(np.float32)
            target = candidate[:, :overlap, :3].astype(np.float32)
            error = np.square(existing - target).mean(axis=2)
            seam = self.seam_solver.vertical(error)
            for row, cut in enumerate(seam):
                canvas[y + row, x + cut : x + overlap] = candidate[row, cut:overlap]
        start_x = x + overlap if x > 0 else x
        if start_x < canvas.shape[1]:
            canvas[y : y + patch_height, start_x : min(canvas.shape[1], x + patch_width)] = candidate[:, start_x - x : min(patch_width, canvas.shape[1] - x)]
        if y > 0 and overlap > 0:
            existing = canvas[y : y + overlap, x : x + patch_width, :3].astype(np.float32)
            target = candidate[:overlap, :, :3].astype(np.float32)
            error = np.square(existing - target).mean(axis=2)
            seam = self.seam_solver.horizontal(error)
            for column, cut in enumerate(seam):
                canvas[y + cut : y + overlap, x + column] = candidate[cut:overlap, column]
        start_y = y + overlap if y > 0 else y
        if start_y < canvas.shape[0]:
            canvas[start_y : min(canvas.shape[0], y + patch_height), x : x + patch_width] = candidate[start_y - y : min(patch_height, canvas.shape[0] - y)]
        occupied[y : min(canvas.shape[0], y + patch_height), x : min(canvas.shape[1], x + patch_width)] = True


def _positions(length: int, patch_size: int, step: int) -> list[int]:
    positions = list(range(0, max(1, length - patch_size + 1), step))
    final = max(0, length - patch_size)
    if not positions or positions[-1] != final:
        positions.append(final)
    return sorted(set(positions))
