"""Approximate network and transition continuity metrics."""

from __future__ import annotations

import numpy as np
from PIL import Image


def network_map_metrics(
    image: Image.Image,
    road_mask: np.ndarray,
    columns: int,
    rows: int,
    tile_size: int,
) -> dict[str, float]:
    mask = _normalize_mask(road_mask, image.size)
    continuity = _boundary_match(mask, columns, rows, tile_size)
    centers = _centers(mask, columns, rows, tile_size)
    widths = _widths(mask, columns, rows, tile_size)
    center_score = _stability(centers, scale=0.5)
    width_score = _stability(widths, scale=0.5)
    return {
        "road_continuity_score": round(continuity, 6),
        "road_center_alignment_score": round(center_score, 6),
        "road_width_stability_score": round(width_score, 6),
        "road_break_risk": round(1.0 - continuity, 6),
        "road_presence_score": round(1.0 if bool(mask.any()) else 0.0, 6),
    }


def transition_map_metrics(
    image: Image.Image,
    boundary_mask: np.ndarray,
    columns: int,
    rows: int,
    tile_size: int,
) -> dict[str, float]:
    mask = _normalize_mask(boundary_mask, image.size)
    continuity = _boundary_match(mask, columns, rows, tile_size)
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32) / 255.0
    first = rgb[mask]
    second = rgb[~mask]
    if len(first) and len(second):
        separation = float(np.linalg.norm(first.mean(axis=0) - second.mean(axis=0)) / np.sqrt(3.0))
    else:
        separation = 0.0
    abruptness = _material_boundary_jump(rgb, mask)
    return {
        "boundary_continuity_score": round(continuity, 6),
        "semantic_clarity_score": round(max(0.0, min(1.0, separation)), 6),
        "boundary_abruptness_score": round(abruptness, 6),
        "boundary_presence_score": round(1.0 if bool(mask.any()) else 0.0, 6),
    }


def _normalize_mask(mask: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    values = np.asarray(mask, dtype=bool)
    width, height = size
    if values.shape != (height, width):
        raise ValueError(f"mask shape {values.shape} does not match image size {(height, width)}")
    return values


def _boundary_match(mask: np.ndarray, columns: int, rows: int, tile_size: int) -> float:
    matches: list[float] = []
    for x in range(1, columns):
        boundary = x * tile_size
        if boundary < mask.shape[1]:
            matches.append(float(np.mean(mask[:, boundary - 1] == mask[:, boundary])))
    for y in range(1, rows):
        boundary = y * tile_size
        if boundary < mask.shape[0]:
            matches.append(float(np.mean(mask[boundary - 1, :] == mask[boundary, :])))
    return max(0.0, min(1.0, float(np.mean(matches)) if matches else 1.0))


def _centers(mask: np.ndarray, columns: int, rows: int, tile_size: int) -> list[float]:
    values: list[float] = []
    for y in range(rows):
        for x in range(columns):
            tile = mask[y * tile_size : (y + 1) * tile_size, x * tile_size : (x + 1) * tile_size]
            ys, xs = np.where(tile)
            values.append(float(xs.mean() / max(1, tile_size - 1)) if len(xs) else 0.5)
    return values


def _widths(mask: np.ndarray, columns: int, rows: int, tile_size: int) -> list[float]:
    values: list[float] = []
    for y in range(rows):
        for x in range(columns):
            tile = mask[y * tile_size : (y + 1) * tile_size, x * tile_size : (x + 1) * tile_size]
            values.append(float(tile.mean()))
    return values


def _stability(values: list[float], scale: float) -> float:
    if not values:
        return 1.0
    return max(0.0, min(1.0, 1.0 - float(np.std(values)) / max(1e-6, scale)))


def _material_boundary_jump(rgb: np.ndarray, mask: np.ndarray) -> float:
    jumps: list[float] = []
    horizontal = np.linalg.norm(np.diff(rgb, axis=1), axis=2) / np.sqrt(3.0)
    vertical = np.linalg.norm(np.diff(rgb, axis=0), axis=2) / np.sqrt(3.0)
    jumps.extend(horizontal[(mask[:, :-1] != mask[:, 1:])].tolist())
    jumps.extend(vertical[(mask[:-1, :] != mask[1:, :])].tolist())
    return max(0.0, min(1.0, float(np.mean(jumps)) if jumps else 0.0))
