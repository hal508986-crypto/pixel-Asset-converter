"""Region statistics used by semantic analysis and IR construction."""

from dataclasses import dataclass

import numpy as np
from PIL import Image


@dataclass(frozen=True)
class StructuralRegion:
    id: int
    bbox: tuple[int, int, int, int]
    area: int
    centroid: tuple[float, float]
    mean_rgb: tuple[int, int, int]
    contrast: float
    edge_density: float
    saliency: float
    touches_border: bool
    neighbor_regions: tuple[int, ...]


@dataclass(frozen=True)
class StructuralAnalysis:
    region_map: np.ndarray
    regions: tuple[StructuralRegion, ...]


def analyze_regions(image: Image.Image, region_map: np.ndarray, edges: Image.Image) -> StructuralAnalysis:
    """Compute the minimum structural contract for every generated region."""
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    edge_array = np.asarray(edges.convert("L"), dtype=np.float32) / 255.0
    height, width = region_map.shape
    all_regions: list[StructuralRegion] = []
    for region_id in sorted(int(value) for value in np.unique(region_map)):
        mask = region_map == region_id
        ys, xs = np.where(mask)
        if not len(xs):
            continue
        pixels = rgb[mask]
        mean = pixels.mean(axis=0)
        contrast = float(pixels.std(axis=0).mean() / 128.0)
        edge_density = float(edge_array[mask].mean())
        area = int(mask.sum())
        touches_border = bool(
            (xs == 0).any() or (ys == 0).any() or (xs == width - 1).any() or (ys == height - 1).any()
        )
        neighbors: set[int] = set()
        for shifted in (
            np.roll(region_map, 1, axis=0),
            np.roll(region_map, -1, axis=0),
            np.roll(region_map, 1, axis=1),
            np.roll(region_map, -1, axis=1),
        ):
            neighbors.update(int(value) for value in shifted[mask] if int(value) != region_id)
        all_regions.append(
            StructuralRegion(
                id=region_id,
                bbox=(int(xs.min()), int(ys.min()), int(xs.max() + 1), int(ys.max() + 1)),
                area=area,
                centroid=(float(xs.mean()), float(ys.mean())),
                mean_rgb=tuple(int(round(value)) for value in mean),
                contrast=max(0.0, min(1.0, contrast)),
                edge_density=max(0.0, min(1.0, edge_density)),
                saliency=max(0.0, min(1.0, (area / (width * height)) * 2.0 + edge_density)),
                touches_border=touches_border,
                neighbor_regions=tuple(sorted(neighbors)),
            )
        )
    return StructuralAnalysis(region_map=region_map, regions=tuple(all_regions))
