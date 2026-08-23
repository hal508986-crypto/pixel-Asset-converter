"""Approximate frequency, clutter, readability and semantic study metrics."""

from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image


def frequency_band_metrics(image: Image.Image) -> dict[str, float]:
    gray = _gray(image)
    centered = gray - float(gray.mean())
    spectrum = np.fft.fftshift(np.fft.fft2(centered))
    energy = np.abs(spectrum) ** 2
    height, width = gray.shape
    yy, xx = np.mgrid[0:height, 0:width]
    radius = np.sqrt(((xx - width / 2) / max(1.0, width / 2)) ** 2 + ((yy - height / 2) / max(1.0, height / 2)) ** 2)
    energy[radius < 0.02] = 0.0
    total = float(energy.sum()) or 1.0
    low = float(energy[radius <= 0.18].sum() / total)
    medium = float(energy[(radius > 0.18) & (radius <= 0.48)].sum() / total)
    high = float(energy[radius > 0.48].sum() / total)
    return {
        "low_frequency_energy": round(low, 6),
        "medium_frequency_energy": round(medium, 6),
        "high_frequency_energy": round(high, 6),
        "frequency_entropy": round(_entropy(np.array([low, medium, high])), 6),
    }


def compute_tile_metrics(
    image: Image.Image,
    target: str,
    semantic_role: str,
    mask: np.ndarray | None = None,
    boundary_mask: np.ndarray | None = None,
    silhouette_mask: np.ndarray | None = None,
) -> dict[str, Any]:
    values = frequency_band_metrics(image)
    array = np.asarray(image.convert("RGB"), dtype=np.float32)
    gradients = np.abs(np.diff(array, axis=0)).mean() + np.abs(np.diff(array, axis=1)).mean()
    contrast = float(array.std() / 128.0)
    clutter = _clamp(0.58 * values["high_frequency_energy"] + 0.22 * min(1.0, gradients / 32.0) + 0.12 * contrast)
    palette = len({tuple(pixel) for pixel in array.astype(np.uint8).reshape(-1, 3)})
    edge = _edge_continuity(array)
    readability = _clamp(0.55 * values["low_frequency_energy"] + 0.35 * values["medium_frequency_energy"] + 0.12 * min(1.0, contrast) - 0.50 * clutter + 0.35)
    semantic = _semantic_fitness(array, semantic_role, mask, boundary_mask, silhouette_mask, clutter)
    metrics: dict[str, Any] = {
        "target": target,
        "semantic_role": semantic_role,
        "palette_usage": palette,
        "edge_continuity_score": round(edge, 6),
        "clutter_risk_score": round(clutter, 6),
        "readability_score": round(readability, 6),
        "semantic_fitness_score": round(semantic, 6),
        **values,
    }
    if mask is not None:
        metrics.update(_mask_metrics(array, mask, semantic_role))
    if boundary_mask is not None:
        metrics.update(_boundary_metrics(array, boundary_mask))
    if silhouette_mask is not None:
        metrics.update(_silhouette_metrics(array, silhouette_mask))
    metrics["single_tile_score"] = round(_clamp(0.45 * readability + 0.35 * semantic + 0.20 * edge), 6)
    return metrics


def compute_map_metrics(image: Image.Image, tile_size: int = 64) -> dict[str, float]:
    array = np.asarray(image.convert("RGB"), dtype=np.float32)
    height, width, _ = array.shape
    vertical_diffs = []
    horizontal_diffs = []
    grid_lines = []
    for x in range(tile_size, width, tile_size):
        left = array[:, x - 1]
        right = array[:, x]
        vertical_diffs.append(float(np.abs(left - right).mean() / 255.0))
        grid_lines.append(float(np.abs(array[:, x - 1] - array[:, max(0, x - 2)]).mean() / 255.0))
    for y in range(tile_size, height, tile_size):
        top = array[y - 1]
        bottom = array[y]
        horizontal_diffs.append(float(np.abs(top - bottom).mean() / 255.0))
        grid_lines.append(float(np.abs(array[y - 1] - array[max(0, y - 2)]).mean() / 255.0))
    discontinuity = float(np.mean(vertical_diffs + horizontal_diffs)) if vertical_diffs or horizontal_diffs else 0.0
    grid_visibility = _clamp(discontinuity * 1.75)
    periodicity = _clamp(float(np.mean(np.abs(array[:, :tile_size] - array[:, -tile_size:])) / 255.0) if width >= tile_size * 2 else 0.55)
    return {
        "map_edge_discontinuity_score": round(discontinuity, 6),
        "grid_visibility_score": round(grid_visibility, 6),
        "map_periodicity_risk": round(1.0 - periodicity, 6),
        "map_readability_score": round(_clamp(1.0 - 0.65 * grid_visibility - 0.25 * (1.0 - periodicity)), 6),
    }


def grammar_score(tile_metrics: dict[str, Any], map_metrics: dict[str, Any] | None = None) -> float:
    map_values = map_metrics or {}
    score = (
        0.42 * float(tile_metrics.get("readability_score", 0.0))
        + 0.34 * float(tile_metrics.get("semantic_fitness_score", 0.0))
        + 0.14 * float(tile_metrics.get("edge_continuity_score", 0.0))
        + 0.10 * float(map_values.get("map_readability_score", 0.75))
        - 0.10 * float(tile_metrics.get("clutter_risk_score", 0.0))
        - 0.08 * float(map_values.get("grid_visibility_score", 0.0))
        - 0.06 * float(map_values.get("map_periodicity_risk", 0.0))
    )
    return round(_clamp(score), 6)


def _semantic_fitness(
    array: np.ndarray,
    role: str,
    mask: np.ndarray | None,
    boundary_mask: np.ndarray | None,
    silhouette_mask: np.ndarray | None,
    clutter: float,
) -> float:
    if mask is not None:
        values = _mask_metrics(array, mask, role)
        return _clamp(0.48 + 0.36 * values.get("feature_contrast_score", 0.0) + 0.16 * values.get("mask_continuity_score", 0.0) - 0.12 * clutter)
    if boundary_mask is not None:
        return _clamp(0.55 + 0.36 * _boundary_metrics(array, boundary_mask)["boundary_clarity_score"] - 0.12 * clutter)
    if silhouette_mask is not None:
        return _clamp(0.50 + 0.45 * _silhouette_metrics(array, silhouette_mask)["silhouette_clarity_score"] - 0.10 * clutter)
    if role == "surface":
        return _clamp(0.78 - 0.15 * clutter)
    return _clamp(0.70 - 0.18 * clutter)


def _mask_metrics(array: np.ndarray, mask: np.ndarray, role: str) -> dict[str, float]:
    resized = _fit_mask(mask, array.shape[:2])
    inside = array[resized]
    outside = array[~resized]
    if not len(inside) or not len(outside):
        return {"feature_contrast_score": 0.0, "mask_continuity_score": 0.0}
    contrast = float(np.abs(inside.mean(axis=0) - outside.mean(axis=0)).mean() / 255.0)
    continuity = _largest_component_ratio(resized)
    return {
        "feature_contrast_score": round(_clamp(contrast * 2.0), 6),
        "mask_continuity_score": round(continuity, 6),
        "network_readability_score" if role == "network" else "feature_readability_score": round(_clamp(0.65 * contrast * 2.0 + 0.35 * continuity), 6),
    }


def _boundary_metrics(array: np.ndarray, boundary_mask: np.ndarray) -> dict[str, float]:
    mask = _fit_mask(boundary_mask, array.shape[:2])
    if not mask.any():
        return {"boundary_clarity_score": 0.0, "transition_band_score": 0.0}
    edge = np.zeros_like(mask)
    edge[:, 1:] |= mask[:, 1:] != mask[:, :-1]
    edge[1:, :] |= mask[1:, :] != mask[:-1, :]
    values = array[edge]
    clarity = float(values.std() / 128.0) if len(values) else 0.0
    return {"boundary_clarity_score": round(_clamp(clarity), 6), "transition_band_score": round(_clamp(float(mask.mean()) * 2.0), 6)}


def _silhouette_metrics(array: np.ndarray, silhouette_mask: np.ndarray) -> dict[str, float]:
    mask = _fit_mask(silhouette_mask, array.shape[:2])
    inside = array[mask]
    outside = array[~mask]
    contrast = float(np.abs(inside.mean(axis=0) - outside.mean(axis=0)).mean() / 255.0) if len(inside) and len(outside) else 0.0
    return {"silhouette_clarity_score": round(_clamp(contrast * 2.0), 6), "object_fill_ratio": round(float(mask.mean()), 6)}


def _gray(image: Image.Image) -> np.ndarray:
    array = np.asarray(image.convert("RGB"), dtype=np.float32)
    return array @ np.array([0.299, 0.587, 0.114], dtype=np.float32)


def _edge_continuity(array: np.ndarray) -> float:
    if min(array.shape[:2]) < 2:
        return 1.0
    horizontal = np.abs(array[:, 0] - array[:, -1]).mean() / 255.0
    vertical = np.abs(array[0] - array[-1]).mean() / 255.0
    return _clamp(1.0 - 0.5 * (horizontal + vertical))


def _fit_mask(mask: np.ndarray, shape: tuple[int, int]) -> np.ndarray:
    if mask.shape == shape:
        return mask.astype(bool)
    image = Image.fromarray(mask.astype(np.uint8) * 255, mode="L").resize((shape[1], shape[0]), Image.Resampling.NEAREST)
    return np.asarray(image, dtype=np.uint8) > 0


def _largest_component_ratio(mask: np.ndarray) -> float:
    values = mask.astype(bool).copy()
    total = int(values.sum())
    if total == 0:
        return 0.0
    largest = 0
    height, width = values.shape
    for y, x in zip(*np.where(values)):
        if not values[y, x]:
            continue
        stack = [(int(y), int(x))]
        values[y, x] = False
        count = 0
        while stack:
            cy, cx = stack.pop()
            count += 1
            for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                if 0 <= ny < height and 0 <= nx < width and values[ny, nx]:
                    values[ny, nx] = False
                    stack.append((ny, nx))
        largest = max(largest, count)
    return _clamp(largest / total)


def _entropy(values: np.ndarray) -> float:
    values = values[values > 0]
    if not len(values):
        return 0.0
    return float(-(values * np.log2(values)).sum())


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
