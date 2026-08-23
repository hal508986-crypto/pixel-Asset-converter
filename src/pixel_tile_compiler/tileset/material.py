"""Material-exemplar analysis used as a shared tileset source baseline."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
from PIL import Image
from skimage.color import rgb2lab
from skimage.filters import threshold_otsu
from skimage.measure import label

from pixel_tile_compiler.pixelizer.palette import quantize_palette


@dataclass(frozen=True)
class MaterialMetrics:
    """Low-cost spatial risk signals for a material exemplar."""

    brightness_spatial_variance: float
    color_spatial_variance: float
    texture_density_variance: float
    center_dominance: float
    large_landmark_risk: float
    low_frequency_pattern_strength: float = 0.0
    autocorrelation_peak_risk: float = 0.0
    canopy_cluster_scale_score: float = 0.0
    canopy_fragmentation_score: float = 0.0
    large_mass_dominance_score: float = 0.0

    def as_dict(self) -> dict[str, float]:
        values = {key: round(float(value), 6) for key, value in asdict(self).items()}
        values["center_dominance_score"] = values["center_dominance"]
        return values


@dataclass(frozen=True)
class MaterialAnalysis:
    """Material-wide statistics shared by patch selection and validation."""

    metrics: MaterialMetrics
    global_color_distribution: tuple[tuple[tuple[int, int, int], int], ...]
    lab_palette_candidates: tuple[tuple[float, float, float], ...]
    brightness_range: tuple[float, float]
    texture_scale: float
    edge_density: float
    local_contrast_distribution: tuple[float, ...]
    image_size: tuple[int, int]

    def as_dict(self) -> dict[str, object]:
        return {
            "metrics": self.metrics.as_dict(),
            "global_color_distribution": [
                {"color": list(color), "count": count}
                for color, count in self.global_color_distribution
            ],
            "lab_palette_candidates": [list(color) for color in self.lab_palette_candidates],
            "brightness_range": list(self.brightness_range),
            "texture_scale": round(float(self.texture_scale), 6),
            "edge_density": round(float(self.edge_density), 6),
            "local_contrast_distribution": [round(float(value), 6) for value in self.local_contrast_distribution],
            "image_size": list(self.image_size),
        }


def _working_rgb(image: Image.Image, max_size: int = 256) -> np.ndarray:
    converted = image.convert("RGB")
    scale = min(1.0, max_size / max(converted.size))
    if scale < 1.0:
        converted = converted.resize(
            (max(1, round(converted.width * scale)), max(1, round(converted.height * scale))),
            Image.Resampling.BILINEAR,
        )
    return np.asarray(converted, dtype=np.float32)


def _blocks(array: np.ndarray, grid: int = 4) -> list[np.ndarray]:
    rows = np.array_split(array, grid, axis=0)
    return [block for row in rows for block in np.array_split(row, grid, axis=1) if block.size]


def _edge_density_map(rgb: np.ndarray) -> np.ndarray:
    normalized = rgb / 255.0
    horizontal = np.abs(np.diff(normalized, axis=1, prepend=normalized[:, :1]))
    vertical = np.abs(np.diff(normalized, axis=0, prepend=normalized[:1, :]))
    gradient = (horizontal.mean(axis=2) + vertical.mean(axis=2)) * 0.5
    return gradient


def analyze_material(image: Image.Image, palette_budget: int = 24, material: str = "grass") -> MaterialAnalysis:
    """Analyze a source without changing pixels or introducing randomness."""
    if image.width < 1 or image.height < 1:
        raise ValueError("material source must not be empty")
    rgb = _working_rgb(image)
    brightness = rgb @ np.asarray([0.2126, 0.7152, 0.0722], dtype=np.float32)
    gradient = _edge_density_map(rgb)
    blocks = _blocks(rgb)
    brightness_means = np.asarray([float((block @ np.asarray([0.2126, 0.7152, 0.0722])).mean()) for block in blocks])
    color_means = np.asarray([block.mean(axis=(0, 1)) for block in blocks], dtype=np.float32)
    density_means = np.asarray([float((gradient[y0:y1, x0:x1] > 0.045).mean()) for y0, y1, x0, x1 in _block_boxes(gradient.shape)])
    brightness_spatial_variance = float(np.var(brightness_means) / (255.0**2))
    color_spatial_variance = float(np.mean(np.var(color_means, axis=0)) / (255.0**2))
    texture_density_variance = float(np.var(density_means))

    height, width = brightness.shape
    y0, y1 = height // 4, max(height // 4 + 1, height * 3 // 4)
    x0, x1 = width // 4, max(width // 4 + 1, width * 3 // 4)
    center = rgb[y0:y1, x0:x1]
    outer_mask = np.ones((height, width), dtype=bool)
    outer_mask[y0:y1, x0:x1] = False
    outer = rgb[outer_mask]
    global_mean = rgb.mean(axis=(0, 1))
    center_distance = float(np.linalg.norm(center.mean(axis=(0, 1)) - global_mean) / 441.6729559)
    center_contrast = float(min(1.0, center.std() / 96.0))
    center_dominance = max(0.0, min(1.0, 0.7 * center_distance + 0.3 * center_contrast))
    outer_distance = float(np.linalg.norm(center.mean(axis=(0, 1)) - outer.mean(axis=0)) / 441.6729559) if outer.size else 0.0
    large_landmark_risk = max(0.0, min(1.0, 0.65 * center_dominance + 0.35 * outer_distance))
    low_frequency_pattern_strength, autocorrelation_peak_risk = _macro_pattern_risks(brightness)

    quantized = quantize_palette(Image.fromarray(rgb.astype(np.uint8), mode="RGB"), budget=palette_budget)
    colors, counts = np.unique(np.asarray(quantized.convert("RGB"), dtype=np.uint8).reshape(-1, 3), axis=0, return_counts=True)
    order = np.argsort(-counts, kind="stable")
    distribution = tuple(
        ((int(colors[index, 0]), int(colors[index, 1]), int(colors[index, 2])), int(counts[index]))
        for index in order
    )
    candidate_rgb = np.asarray([color for color, _count in distribution], dtype=np.float32) / 255.0
    lab = rgb2lab(candidate_rgb.reshape(-1, 1, 3)).reshape(-1, 3) if len(candidate_rgb) else np.empty((0, 3))
    texture_scale = float(1.0 / max(1.0, gradient.mean() * max(rgb.shape[:2])))
    local_contrast = tuple(float(min(1.0, block.std() / 128.0)) for block in _blocks(brightness))
    canopy_cluster_scale_score = 0.0
    canopy_fragmentation_score = 0.0
    large_mass_dominance_score = 0.0
    if material == "forest_canopy":
        (
            canopy_cluster_scale_score,
            canopy_fragmentation_score,
            large_mass_dominance_score,
        ) = _canopy_structure_metrics(brightness)
    metrics = MaterialMetrics(
        brightness_spatial_variance=brightness_spatial_variance,
        color_spatial_variance=color_spatial_variance,
        texture_density_variance=texture_density_variance,
        center_dominance=center_dominance,
        large_landmark_risk=large_landmark_risk,
        low_frequency_pattern_strength=low_frequency_pattern_strength,
        autocorrelation_peak_risk=autocorrelation_peak_risk,
        canopy_cluster_scale_score=canopy_cluster_scale_score,
        canopy_fragmentation_score=canopy_fragmentation_score,
        large_mass_dominance_score=large_mass_dominance_score,
    )
    return MaterialAnalysis(
        metrics=metrics,
        global_color_distribution=distribution,
        lab_palette_candidates=tuple(tuple(float(value) for value in row) for row in lab),
        brightness_range=(float(brightness.min()), float(brightness.max())),
        texture_scale=texture_scale,
        edge_density=float((gradient > 0.045).mean()),
        local_contrast_distribution=local_contrast,
        image_size=image.size,
    )


def _block_boxes(shape: tuple[int, int], grid: int = 4) -> list[tuple[int, int, int, int]]:
    height, width = shape
    y_edges = [int(edge) for edge in np.linspace(0, height, grid + 1)]
    x_edges = [int(edge) for edge in np.linspace(0, width, grid + 1)]
    boxes: list[tuple[int, int, int, int]] = []
    for row in range(grid):
        for column in range(grid):
            y0, y1 = y_edges[row], max(y_edges[row] + 1, y_edges[row + 1])
            x0, x1 = x_edges[column], max(x_edges[column] + 1, x_edges[column + 1])
            boxes.append((y0, min(height, y1), x0, min(width, x1)))
    return boxes


def _macro_pattern_risks(brightness: np.ndarray) -> tuple[float, float]:
    """Estimate large-scale structure and repeated-lag risk without a model."""
    gray = Image.fromarray(np.clip(brightness, 0, 255).astype(np.uint8), mode="L")
    coarse = np.asarray(gray.resize((16, 16), Image.Resampling.BILINEAR), dtype=np.float32)
    low_frequency_strength = float(min(1.0, coarse.std() / 32.0))
    if brightness.std() < 1e-6:
        return low_frequency_strength, 0.0
    normalized = brightness - brightness.mean()
    height, width = normalized.shape
    correlations: list[float] = []
    min_lag = max(4, min(height, width) // 16)
    max_lag = max(min_lag, min(24, min(height, width) // 2))
    denominator = float(np.square(normalized).sum())
    for lag in range(min_lag, max_lag + 1):
        horizontal = float(np.abs((normalized[:, :-lag] * normalized[:, lag:]).sum()) / max(1.0, denominator))
        vertical = float(np.abs((normalized[:-lag, :] * normalized[lag:, :]).sum()) / max(1.0, denominator))
        correlations.extend((horizontal, vertical))
    autocorrelation_risk = max(0.0, min(1.0, max(correlations, default=0.0)))
    return low_frequency_strength, autocorrelation_risk


def _canopy_structure_metrics(brightness: np.ndarray) -> tuple[float, float, float]:
    """Approximate canopy blob scale, fragmentation, and dominant mass risk."""
    gray = np.asarray(
        Image.fromarray(np.clip(brightness, 0, 255).astype(np.uint8), mode="L").resize((64, 64), Image.Resampling.BILINEAR),
        dtype=np.float32,
    )
    threshold = float(threshold_otsu(gray))
    mask = gray <= threshold
    components = label(mask, connectivity=2)
    sizes = np.bincount(components.reshape(-1))[1:].astype(np.float32)
    sizes = sizes[sizes > 0]
    if not len(sizes):
        return 0.0, 0.0, 0.0
    area = float(mask.size)
    largest = float(sizes.max() / area)
    upper_cluster = float(np.percentile(sizes, 75) / area)
    cluster_scale = max(0.0, min(1.0, (upper_cluster**0.5) * 4.0))
    component_pressure = min(1.0, len(sizes) / 48.0)
    small_component_pressure = float(np.mean(sizes < area * 0.02))
    fragmentation = max(0.0, min(1.0, 0.7 * component_pressure + 0.3 * small_component_pressure))
    # Keep the observed dominant-region fraction instead of an early-saturating
    # threshold; forest sources often contain one connected shadow field.
    large_mass = max(0.0, min(1.0, largest))
    return cluster_scale, fragmentation, large_mass
