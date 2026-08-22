"""Connected-component cleanup for isolated and micro-cluster pixels."""

from collections import Counter, deque
from dataclasses import dataclass

import numpy as np
from PIL import Image


@dataclass(frozen=True)
class ClusterMetrics:
    isolated_pixel_count: int = 0
    micro_cluster_count: int = 0


def _neighbors(x: int, y: int, width: int, height: int):
    for dy in (-1, 0, 1):
        for dx in (-1, 0, 1):
            if dx == 0 and dy == 0:
                continue
            nx, ny = x + dx, y + dy
            if 0 <= nx < width and 0 <= ny < height:
                yield nx, ny


def cleanup_pixel_clusters(image: Image.Image, min_cluster_size: int = 2) -> tuple[Image.Image, ClusterMetrics]:
    """Replace same-color components smaller than the configured threshold."""
    rgba = np.asarray(image.convert("RGBA")).copy()
    height, width = rgba.shape[:2]
    visited = np.zeros((height, width), dtype=bool)
    isolated = 0
    micro = 0
    replacements: list[tuple[list[tuple[int, int]], tuple[int, int, int, int]]] = []
    for y in range(height):
        for x in range(width):
            if visited[y, x]:
                continue
            color = tuple(int(value) for value in rgba[y, x])
            queue = deque([(x, y)])
            visited[y, x] = True
            component: list[tuple[int, int]] = []
            while queue:
                cx, cy = queue.popleft()
                component.append((cx, cy))
                for nx, ny in _neighbors(cx, cy, width, height):
                    if not visited[ny, nx] and tuple(int(value) for value in rgba[ny, nx]) == color:
                        visited[ny, nx] = True
                        queue.append((nx, ny))
            if len(component) < min_cluster_size:
                if len(component) == 1:
                    isolated += 1
                micro += 1
                nearby = []
                for cx, cy in component:
                    nearby.extend(
                        tuple(int(value) for value in rgba[ny, nx])
                        for nx, ny in _neighbors(cx, cy, width, height)
                        if tuple(int(value) for value in rgba[ny, nx]) != color
                    )
                if nearby:
                    replacements.append((component, Counter(nearby).most_common(1)[0][0]))
    for component, replacement in replacements:
        for x, y in component:
            rgba[y, x] = replacement
    return Image.fromarray(rgba, mode="RGBA"), ClusterMetrics(isolated, micro)
