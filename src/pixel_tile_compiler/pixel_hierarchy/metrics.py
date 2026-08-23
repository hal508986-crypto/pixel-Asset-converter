"""Hierarchy, noise and semantic metrics for the volume study."""

from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image

from pixel_tile_compiler.pixel_grammar.metrics import compute_tile_metrics, frequency_band_metrics
from pixel_tile_compiler.pixel_grammar.profiles import HierarchyMode


def compute_hierarchy_metrics(
    image: Image.Image,
    target: str,
    semantic_role: str,
    mode: HierarchyMode | str,
    structured_baseline: Image.Image | None = None,
    semantic_mask: np.ndarray | None = None,
    boundary_mask: np.ndarray | None = None,
    silhouette_mask: np.ndarray | None = None,
    lighting_direction: tuple[float, float] = (-1.0, -1.0),
) -> dict[str, Any]:
    hierarchy_mode = HierarchyMode(mode)
    base = compute_tile_metrics(
        image,
        target=target,
        semantic_role=semantic_role,
        mask=semantic_mask,
        boundary_mask=boundary_mask,
        silhouette_mask=silhouette_mask,
    )
    clusters = _cluster_metrics(image)
    frequency = frequency_band_metrics(image)
    expected_layers = {HierarchyMode.FLAT: 2, HierarchyMode.STRUCTURED: 3, HierarchyMode.VOLUMETRIC: 4}[hierarchy_mode]
    shading_layer_score = _clamp(1.0 - abs(clusters["shading_layer_count"] - expected_layers) / 4.0)
    major_mass = _major_mass_preservation(image, structured_baseline)
    medium_structure = _medium_structure_score(clusters)
    depth = _depth_readability(image, semantic_mask, silhouette_mask)
    lighting = _lighting_consistency(image, lighting_direction)
    contact = _contact_shadow_readability(image, semantic_mask, boundary_mask, silhouette_mask)
    plane = _plane_separation(image)
    noise = _clamp(
        0.48 * frequency["high_frequency_energy"]
        + 0.30 * clusters["micro_cluster_ratio"]
        + 0.22 * clusters["isolated_pixel_ratio"]
    )
    topology = base.get("network_readability_score", base.get("feature_readability_score", base["semantic_fitness_score"]))
    boundary = base.get("boundary_clarity_score", base["semantic_fitness_score"])
    silhouette = base.get("silhouette_clarity_score", base["semantic_fitness_score"])
    visual_hierarchy = _clamp(
        0.26 * major_mass
        + 0.22 * medium_structure
        + 0.18 * shading_layer_score
        + 0.16 * depth
        + 0.10 * plane
        + 0.08 * base["semantic_fitness_score"]
        - 0.20 * noise
    )
    result: dict[str, Any] = {
        **base,
        **frequency,
        **clusters,
        "major_mass_preservation_score": round(major_mass, 6),
        "medium_cluster_structure_score": round(medium_structure, 6),
        "shading_layer_score": round(shading_layer_score, 6),
        "depth_readability_score": round(depth, 6),
        "high_frequency_noise_risk": round(noise, 6),
        "lighting_consistency_score": round(lighting, 6),
        "contact_shadow_readability": round(contact, 6),
        "plane_separation_score": round(plane, 6),
        "topology_clarity": round(float(topology), 6),
        "boundary_clarity": round(float(boundary), 6),
        "silhouette_clarity": round(float(silhouette), 6),
        "visual_hierarchy_score": round(visual_hierarchy, 6),
    }
    baseline_noise = _noise_value(structured_baseline) if structured_baseline is not None else noise
    result["noise_inflation_penalty"] = round(max(0.0, noise - baseline_noise), 6)
    result["shape_degradation_penalty"] = round(max(0.0, 1.0 - major_mass), 6)
    result["contrast_fragmentation_penalty"] = round(_clamp(clusters["micro_cluster_ratio"] * 1.5), 6)
    result["failure_penalty"] = round(
        0.40 * result["noise_inflation_penalty"]
        + 0.40 * result["shape_degradation_penalty"]
        + 0.20 * result["contrast_fragmentation_penalty"],
        6,
    )
    result["hierarchy_quality_score"] = round(_clamp(visual_hierarchy - result["failure_penalty"]), 6)
    return result


def hierarchy_score(tile: dict[str, Any], map_metrics: dict[str, Any] | None = None) -> dict[str, float]:
    maps = map_metrics or {}
    tile_quality = float(tile.get("single_tile_score", tile.get("readability_score", 0.0)))
    map_readability = float(maps.get("map_readability_score", 0.75))
    semantic = float(tile.get("semantic_fitness_score", 0.0))
    hierarchy = float(tile.get("hierarchy_quality_score", tile.get("visual_hierarchy_score", 0.0)))
    noise = float(tile.get("high_frequency_noise_risk", 0.0))
    return {
        "tile_quality": round(_clamp(tile_quality), 6),
        "map_readability": round(_clamp(map_readability), 6),
        "semantic_fitness": round(_clamp(semantic), 6),
        "hierarchy_quality": round(_clamp(hierarchy), 6),
        "noise_risk": round(_clamp(noise), 6),
        "overall": round(_clamp(0.24 * tile_quality + 0.18 * map_readability + 0.22 * semantic + 0.36 * hierarchy - 0.12 * noise), 6),
    }


def _cluster_metrics(image: Image.Image) -> dict[str, Any]:
    luma = np.asarray(image.convert("RGB"), dtype=np.float32) @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    bins = np.floor(luma / 32.0).astype(np.uint8)
    sizes: list[int] = []
    height, width = bins.shape
    visited = np.zeros_like(bins, dtype=bool)
    for y in range(height):
        for x in range(width):
            if visited[y, x]:
                continue
            value = bins[y, x]
            stack = [(y, x)]
            visited[y, x] = True
            count = 0
            while stack:
                cy, cx = stack.pop()
                count += 1
                for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                    if 0 <= ny < height and 0 <= nx < width and not visited[ny, nx] and bins[ny, nx] == value:
                        visited[ny, nx] = True
                        stack.append((ny, nx))
            sizes.append(count)
    total = float(sum(sizes)) or 1.0
    major = [size for size in sizes if size >= 32]
    medium = [size for size in sizes if 6 <= size < 32]
    micro = [size for size in sizes if size < 6]
    occupied_bins = int(sum(1 for count in np.bincount(bins.ravel(), minlength=8) if count >= len(bins.ravel()) * 0.01))
    return {
        "major_cluster_mean_size": round(float(np.mean(major)) if major else 0.0, 6),
        "medium_cluster_mean_size": round(float(np.mean(medium)) if medium else 0.0, 6),
        "micro_cluster_ratio": round(float(sum(micro) / total), 6),
        "isolated_pixel_ratio": round(float(sum(size == 1 for size in sizes) / total), 6),
        "cluster_count": len(sizes),
        "shading_layer_count": occupied_bins,
    }


def _medium_structure_score(clusters: dict[str, Any]) -> float:
    medium = float(clusters["medium_cluster_mean_size"])
    ratio = 1.0 - abs(float(clusters["micro_cluster_ratio"]) - 0.18)
    return _clamp(0.55 * min(1.0, medium / 24.0) + 0.45 * ratio)


def _major_mass_preservation(image: Image.Image, baseline: Image.Image | None) -> float:
    if baseline is None:
        return 0.75
    current = np.asarray(image.convert("L").resize((8, 8), Image.Resampling.BILINEAR), dtype=np.float32)
    reference = np.asarray(baseline.convert("L").resize((8, 8), Image.Resampling.BILINEAR), dtype=np.float32)
    return _clamp(1.0 - float(np.abs(current - reference).mean()) / 96.0)


def _depth_readability(image: Image.Image, semantic_mask: np.ndarray | None, silhouette_mask: np.ndarray | None) -> float:
    luma = np.asarray(image.convert("L"), dtype=np.float32)
    if semantic_mask is not None or silhouette_mask is not None:
        mask = semantic_mask if semantic_mask is not None else silhouette_mask
        fitted = _fit_mask(mask, luma.shape)
        if fitted is not None and fitted.any() and (~fitted).any():
            return _clamp(abs(float(luma[fitted].mean() - luma[~fitted].mean())) / 64.0)
    coarse = np.asarray(image.convert("L").resize((8, 8), Image.Resampling.BILINEAR), dtype=np.float32)
    return _clamp(float(coarse.std()) / 48.0)


def _lighting_consistency(image: Image.Image, direction: tuple[float, float]) -> float:
    luma = np.asarray(image.convert("L"), dtype=np.float32)
    threshold = np.percentile(luma, 75)
    ys, xs = np.where(luma >= threshold)
    if not len(xs):
        return 0.0
    center = np.array([luma.shape[1] / 2.0, luma.shape[0] / 2.0])
    bright = np.array([float(xs.mean()), float(ys.mean())])
    vector = bright - center
    light = np.array(direction, dtype=np.float32)
    norm = float(np.linalg.norm(vector) * np.linalg.norm(light)) or 1.0
    return _clamp(float(np.dot(vector, light) / norm) * 0.5 + 0.5)


def _contact_shadow_readability(
    image: Image.Image,
    semantic_mask: np.ndarray | None,
    boundary_mask: np.ndarray | None,
    silhouette_mask: np.ndarray | None,
) -> float:
    mask = semantic_mask if semantic_mask is not None else boundary_mask if boundary_mask is not None else silhouette_mask
    fitted = _fit_mask(mask, (image.height, image.width))
    if fitted is None:
        return 0.35
    edge = np.zeros_like(fitted)
    edge[:, 1:] |= fitted[:, 1:] != fitted[:, :-1]
    edge[1:, :] |= fitted[1:, :] != fitted[:-1, :]
    if not edge.any():
        return 0.0
    luma = np.asarray(image.convert("L"), dtype=np.float32)
    inside = luma[edge & fitted]
    outside = luma[edge & ~fitted]
    if not len(inside) or not len(outside):
        return 0.0
    return _clamp(abs(float(inside.mean() - outside.mean())) / 64.0)


def _plane_separation(image: Image.Image) -> float:
    luma = np.asarray(image.convert("L"), dtype=np.float32)
    blocks = luma.reshape(8, luma.shape[0] // 8, 8, luma.shape[1] // 8).mean(axis=(1, 3))
    return _clamp(float(blocks.std()) / 48.0)


def _noise_value(image: Image.Image | None) -> float:
    if image is None:
        return 0.0
    metrics = compute_hierarchy_metrics(image, "baseline", "surface", HierarchyMode.STRUCTURED, structured_baseline=None)
    return float(metrics["high_frequency_noise_risk"])


def _fit_mask(mask: np.ndarray | None, shape: tuple[int, int]) -> np.ndarray | None:
    if mask is None:
        return None
    if mask.shape == shape:
        return mask.astype(bool)
    fitted = Image.fromarray(mask.astype(np.uint8) * 255, mode="L").resize((shape[1], shape[0]), Image.Resampling.NEAREST)
    return np.asarray(fitted, dtype=np.uint8) > 0


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
