"""Deterministic masks for network topology and material boundaries."""

from __future__ import annotations

import numpy as np

from .graph import NetworkTopology, connector_mask_for_topology, topology_sides

ROAD_TOPOLOGIES = tuple(item.value for item in NetworkTopology if item is not NetworkTopology.EMPTY)


def build_road_mask(
    size: tuple[int, int],
    topology: str,
    width: float = 0.28,
    center: float = 0.5,
) -> np.ndarray:
    """Return a boolean road mask using the declared edge connectivity."""
    normalized = str(topology).upper()
    if normalized == "EMPTY":
        width_px, height_px = size
        if width_px < 2 or height_px < 2:
            raise ValueError("mask size must be at least 2x2")
        return np.zeros((height_px, width_px), dtype=bool)
    try:
        connector_mask_for_topology(normalized)
        sides = topology_sides(normalized)
    except ValueError as exc:
        raise ValueError(f"unsupported road topology: {topology}") from exc
    if not 0.05 <= width <= 0.9:
        raise ValueError("road width must be between 0.05 and 0.9")
    if not 0.2 <= center <= 0.8:
        raise ValueError("road center must be between 0.2 and 0.8")
    width_px, height_px = size
    if width_px < 2 or height_px < 2:
        raise ValueError("mask size must be at least 2x2")
    y, x = np.mgrid[0:height_px, 0:width_px]
    xx = x / max(1, width_px - 1)
    yy = y / max(1, height_px - 1)
    half = width / 2.0
    vertical = np.abs(xx - center) <= half
    horizontal = np.abs(yy - center) <= half
    arms = {
        "N": vertical & (yy <= center),
        "E": horizontal & (xx >= center),
        "S": vertical & (yy >= center),
        "W": horizontal & (xx <= center),
    }
    mask = np.zeros_like(vertical, dtype=bool)
    for side in sides:
        mask |= arms[side]
    junction_radius = max(half * 1.35, 1.0 / max(width_px, height_px))
    junction = (xx - center) ** 2 + (yy - center) ** 2 <= junction_radius**2
    return (mask | junction).astype(bool)


def build_transition_mask(size: tuple[int, int], orientation: str, boundary: float = 0.5) -> np.ndarray:
    """Return the region occupied by the first material in NS or EW order."""
    if orientation not in {"NS", "EW"}:
        raise ValueError("transition orientation must be NS or EW")
    if not 0.1 <= boundary <= 0.9:
        raise ValueError("transition boundary must be between 0.1 and 0.9")
    width, height = size
    if width < 2 or height < 2:
        raise ValueError("mask size must be at least 2x2")
    y, x = np.mgrid[0:height, 0:width]
    if orientation == "NS":
        return (y / max(1, height - 1) < boundary).astype(bool)
    return (x / max(1, width - 1) < boundary).astype(bool)
