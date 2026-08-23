"""Deterministic, model-free material source fingerprinting."""

from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image

from pixel_tile_compiler.pixel_grammar.metrics import frequency_band_metrics


def fingerprint_image(
    image: Image.Image,
    patch_grid: int = 4,
    target_feature_scale_px: float | None = None,
) -> dict[str, Any]:
    """Return reproducible spatial statistics for a source exemplar.

    These are screening signals, not semantic recognition.  Keeping the
    fingerprint independent of random sampling makes candidate comparisons
    and later library refreshes auditable.
    """
    if patch_grid < 1:
        raise ValueError("patch_grid must be positive")
    rgb = np.asarray(image.convert("RGB").resize((256, 256), Image.Resampling.BILINEAR), dtype=np.float32)
    gray = rgb @ np.asarray([0.2126, 0.7152, 0.0722], dtype=np.float32)
    gradient = _gradient(gray)
    patches = _patch_statistics(rgb, gray, gradient, patch_grid)
    brightness_values = np.asarray([item["brightness_mean"] for item in patches], dtype=np.float32)
    color_values = np.asarray([item["color_mean"] for item in patches], dtype=np.float32)
    contrast_values = np.asarray([item["local_contrast"] for item in patches], dtype=np.float32)
    edge_values = np.asarray([item["edge_density"] for item in patches], dtype=np.float32)
    bands = frequency_band_metrics(image)
    feature_scale = estimate_feature_scale_px(gray)
    scale_error = 0.0
    if target_feature_scale_px is not None:
        scale_error = min(1.0, abs(feature_scale - target_feature_scale_px) / max(1.0, target_feature_scale_px))
    values: dict[str, Any] = {
        "brightness_spatial_variance": _clamp(float(np.var(brightness_values) / (255.0**2)) * 8.0),
        "color_spatial_variance": _clamp(float(np.var(color_values) / (255.0**2)) * 8.0),
        "local_contrast_mean": _clamp(float(contrast_values.mean())),
        "local_contrast_variance": _clamp(float(np.var(contrast_values)) * 4.0),
        "texture_density_variance": _clamp(float(np.var(edge_values)) * 12.0),
        "orientation_bias": _orientation_bias(gray),
        "edge_density": _clamp(float((gradient > 7.0).mean()) * 2.0),
        "center_dominance_score": _center_dominance(rgb),
        "large_landmark_risk": _large_landmark_risk(gray),
        "lighting_bias_score": _lighting_bias(gray),
        "autocorrelation_peak_risk": _autocorrelation_peak_risk(gray),
        "estimated_feature_scale_px": round(float(feature_scale), 6),
        "target_feature_scale_px": target_feature_scale_px,
        "feature_scale_error": round(float(scale_error), 6),
        "stationarity_score": _stationarity(patches),
        "patch_grid": patch_grid,
        "patch_statistics": patches,
        **bands,
    }
    values["low_frequency_pattern_strength"] = values["low_frequency_energy"]
    return _round_nested(values)


def estimate_feature_scale_px(gray: np.ndarray | Image.Image) -> float:
    """Estimate a visible feature's characteristic size in 64px space."""
    if isinstance(gray, Image.Image):
        values = np.asarray(gray.convert("L").resize((256, 256), Image.Resampling.BILINEAR), dtype=np.float32)
    else:
        values = np.asarray(gray, dtype=np.float32)
    if values.size == 0 or float(values.std()) < 1e-4:
        return 64.0
    edges = _gradient(values) > max(4.0, float(_gradient(values).mean() * 1.8))
    row_runs = _run_lengths(edges.mean(axis=0) > 0.06)
    column_runs = _run_lengths(edges.mean(axis=1) > 0.06)
    runs = row_runs + column_runs
    scale = float(np.median(runs)) if runs else 16.0
    return max(1.0, min(64.0, scale * 64.0 / max(values.shape)))


def _patch_statistics(rgb: np.ndarray, gray: np.ndarray, gradient: np.ndarray, grid: int) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    y_edges = np.linspace(0, gray.shape[0], grid + 1, dtype=int)
    x_edges = np.linspace(0, gray.shape[1], grid + 1, dtype=int)
    for row in range(grid):
        for column in range(grid):
            y0, y1 = y_edges[row], max(y_edges[row] + 1, y_edges[row + 1])
            x0, x1 = x_edges[column], max(x_edges[column] + 1, x_edges[column + 1])
            patch = rgb[y0:y1, x0:x1]
            patch_gray = gray[y0:y1, x0:x1]
            patch_gradient = gradient[y0:y1, x0:x1]
            output.append(
                {
                    "row": row,
                    "column": column,
                    "brightness_mean": float(patch_gray.mean()),
                    "color_mean": [float(value) for value in patch.mean(axis=(0, 1))],
                    "local_contrast": float(min(1.0, patch_gray.std() / 64.0)),
                    "edge_density": float((patch_gradient > 7.0).mean()),
                }
            )
    return output


def _gradient(gray: np.ndarray) -> np.ndarray:
    horizontal = np.abs(np.diff(gray, axis=1, prepend=gray[:, :1]))
    vertical = np.abs(np.diff(gray, axis=0, prepend=gray[:1, :]))
    return (horizontal + vertical) * 0.5


def _center_dominance(rgb: np.ndarray) -> float:
    height, width = rgb.shape[:2]
    y0, y1 = height // 4, height * 3 // 4
    x0, x1 = width // 4, width * 3 // 4
    center = rgb[y0:y1, x0:x1].mean(axis=(0, 1))
    global_mean = rgb.mean(axis=(0, 1))
    distance = float(np.linalg.norm(center - global_mean) / 441.6729559)
    center_contrast = min(1.0, float(rgb[y0:y1, x0:x1].std()) / 96.0)
    return _clamp(0.72 * distance + 0.28 * center_contrast)


def _large_landmark_risk(gray: np.ndarray) -> float:
    coarse = np.asarray(Image.fromarray(np.uint8(np.clip(gray, 0, 255))).resize((16, 16), Image.Resampling.BILINEAR), dtype=np.float32)
    if float(coarse.std()) < 1e-4:
        return 0.0
    maximum = float(np.max(np.abs(coarse - coarse.mean())))
    center = coarse[4:12, 4:12]
    center_delta = abs(float(center.mean() - coarse.mean())) / max(1.0, maximum)
    return _clamp(0.42 * float(coarse.std() / 64.0) + 0.58 * center_delta)


def _lighting_bias(gray: np.ndarray) -> float:
    height, width = gray.shape
    x = np.linspace(-1.0, 1.0, width, dtype=np.float32)[None, :]
    y = np.linspace(-1.0, 1.0, height, dtype=np.float32)[:, None]
    centered = gray - gray.mean()
    denominator = float(np.linalg.norm(centered)) * float(np.linalg.norm(x + y)) or 1.0
    correlation = abs(float(np.sum(centered * (x + y)) / denominator))
    return _clamp(correlation * 2.8)


def _orientation_bias(gray: np.ndarray) -> float:
    horizontal = float(np.abs(np.diff(gray, axis=1)).mean())
    vertical = float(np.abs(np.diff(gray, axis=0)).mean())
    total = horizontal + vertical or 1.0
    return _clamp(abs(horizontal - vertical) / total * 2.0)


def _autocorrelation_peak_risk(gray: np.ndarray) -> float:
    centered = gray - gray.mean()
    denominator = float(np.square(centered).sum()) or 1.0
    height, width = centered.shape
    values: list[float] = []
    for lag in (8, 16, 32, 48):
        if lag >= width or lag >= height:
            continue
        values.append(abs(float(np.multiply(centered[:, :-lag], centered[:, lag:]).sum() / denominator)))
        values.append(abs(float(np.multiply(centered[:-lag, :], centered[lag:, :]).sum() / denominator)))
    return _clamp(max(values, default=0.0) * 1.25)


def _stationarity(patches: list[dict[str, Any]]) -> float:
    if not patches:
        return 0.0
    brightness = np.asarray([item["brightness_mean"] for item in patches], dtype=np.float32)
    contrast = np.asarray([item["local_contrast"] for item in patches], dtype=np.float32)
    density = np.asarray([item["edge_density"] for item in patches], dtype=np.float32)
    variance_penalty = (
        min(1.0, float(brightness.std()) / 32.0)
        + min(1.0, float(contrast.std()) / 0.2)
        + min(1.0, float(density.std()) / 0.12)
    ) / 3.0
    uniform_penalty = 0.10 if float(brightness.std() + contrast.std() + density.std()) < 1e-5 else 0.0
    return _clamp(1.0 - variance_penalty - uniform_penalty)


def _run_lengths(values: np.ndarray) -> list[int]:
    runs: list[int] = []
    count = 0
    for value in values:
        if bool(value):
            count += 1
        elif count:
            runs.append(count)
            count = 0
    if count:
        runs.append(count)
    return runs


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _round_nested(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dict):
        return {key: _round_nested(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_round_nested(item) for item in value]
    return value
