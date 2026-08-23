"""Contract-aware MAP assembly and experiment metrics."""

from __future__ import annotations

import random
from collections import Counter
from typing import Any, Sequence

import numpy as np
from PIL import Image

from pixel_tile_compiler.map.metrics import measure_map_metrics

from .edge_contract import EdgeContract, EdgeProfile, compare_edge_profiles, edge_profile


def assemble_contract_map(
    tiles: Sequence[Any],
    columns: int,
    rows: int,
    seed: int = 42,
) -> list[list[Any]]:
    """Assemble a grid with exact north/west contract matching using seeded backtracking."""
    if columns < 1 or rows < 1:
        raise ValueError("columns and rows must be positive")
    if not tiles:
        raise ValueError("at least one source tile is required")
    rng = random.Random(seed)
    ordered = list(tiles)
    positions = [(x, y) for y in range(rows) for x in range(columns)]
    selected: list[Any] = [None] * len(positions)
    usage: Counter[str] = Counter()

    def candidates(index: int) -> list[Any]:
        x, y = positions[index]
        north = selected[index - columns].spec.edge_contract.south if y > 0 else None
        west = selected[index - 1].spec.edge_contract.east if x > 0 else None
        values = [
            tile
            for tile in ordered
            if (north is None or tile.spec.edge_contract.north == north)
            and (west is None or tile.spec.edge_contract.west == west)
        ]
        rng.shuffle(values)
        recent = [item.spec.tile_id for item in selected[max(0, index - 4):index] if item is not None]
        values.sort(key=lambda tile: (usage[tile.spec.tile_id], recent.count(tile.spec.tile_id), tile.spec.tile_id))
        return values

    def visit(index: int) -> bool:
        if index == len(positions):
            return True
        for tile in candidates(index):
            selected[index] = tile
            usage[tile.spec.tile_id] += 1
            if visit(index + 1):
                return True
            usage[tile.spec.tile_id] -= 1
        selected[index] = None
        return False

    if not visit(0):
        raise ValueError("source tile contracts cannot assemble the requested MAP")
    return [selected[row * columns : (row + 1) * columns] for row in range(rows)]


def validate_contract_adjacency(grid: Sequence[Sequence[Any]]) -> bool:
    """Return whether every logical neighbor has matching interface IDs."""
    if not grid or not grid[0]:
        return True
    columns = len(grid[0])
    for row, values in enumerate(grid):
        if len(values) != columns:
            return False
        for column, tile in enumerate(values):
            contract = tile.spec.edge_contract
            if column + 1 < columns and contract.east != values[column + 1].spec.edge_contract.west:
                return False
            if row + 1 < len(grid) and contract.south != grid[row + 1][column].spec.edge_contract.north:
                return False
    return True


def validate_edge_profiles(
    tiles: Sequence[Any],
    image_getter=lambda tile: tile.image,
) -> dict[str, Any]:
    """Measure source or pixel edge compatibility for equal IDs."""
    groups: dict[str, dict[str, list[EdgeProfile]]] = {}
    for tile in tiles:
        contract: EdgeContract = tile.spec.edge_contract
        image = image_getter(tile)
        for side in ("north", "east", "south", "west"):
            edge_id = getattr(contract, side)
            groups.setdefault(edge_id, {}).setdefault(side, []).append(edge_profile(image, side))
    by_id: dict[str, Any] = {}
    scores: list[float] = []
    for edge_id, sides in sorted(groups.items()):
        pairs: dict[str, Any] = {}
        for first_side, second_side, label in (("east", "west", "east_west"), ("north", "south", "north_south")):
            first_profiles = sides.get(first_side, [])
            second_profiles = sides.get(second_side, [])
            comparisons = [compare_edge_profiles(first, second) for first in first_profiles for second in second_profiles]
            if comparisons:
                aggregate = {
                    key: round(float(np.mean([comparison[key] for comparison in comparisons])), 6)
                    for key in comparisons[0]
                }
                pairs[label] = aggregate
                scores.append(aggregate["score"])
            else:
                pairs[label] = {"score": 0.0, "color_distance": 0.0, "brightness_distance": 0.0, "cluster_similarity": 1.0}
        by_id[edge_id] = pairs
    risk = float(np.mean(scores)) if scores else 0.0
    return {"by_edge_id": by_id, "risk_score": round(risk, 6)}


def tileset_metrics(
    image: Image.Image,
    columns: int,
    rows: int,
    tile_ids: Sequence[str],
    contract_validation: dict[str, Any],
) -> dict[str, Any]:
    map_metrics = measure_map_metrics(image, columns, rows, tile_size=64)
    counts = Counter(tile_ids)
    reuse = max(counts.values(), default=0) / max(1, len(tile_ids))
    adjacent_pairs = 0
    repeated_pairs = 0
    for y in range(rows):
        for x in range(columns):
            index = y * columns + x
            if x + 1 < columns:
                adjacent_pairs += 1
                repeated_pairs += int(tile_ids[index] == tile_ids[index + 1])
            if y + 1 < rows:
                adjacent_pairs += 1
                repeated_pairs += int(tile_ids[index] == tile_ids[index + columns])
    adjacent_repeat_rate = repeated_pairs / max(1, adjacent_pairs)
    periodicity = max(0.0, min(1.0, 0.65 * adjacent_repeat_rate + 0.35 * contract_validation.get("risk_score", 0.0)))
    cluster_continuity, canopy_mass_break = _cluster_continuity_metrics(image, columns, rows, tile_size=64)
    return {
        "edge_discontinuity_score": round(map_metrics.neighbor_color_discontinuity, 6),
        "brightness_discontinuity_score": round(map_metrics.neighbor_brightness_discontinuity, 6),
        "palette_discontinuity_score": round(map_metrics.neighbor_texture_discontinuity, 6),
        "periodicity_risk": round(periodicity, 6),
        "tile_reuse_frequency": round(reuse, 6),
        "adjacent_repeat_rate": round(adjacent_repeat_rate, 6),
        "grid_visibility_score": round(map_metrics.map_grid_visibility_score, 6),
        "cluster_continuity_score": round(cluster_continuity, 6),
        "canopy_mass_break_risk": round(canopy_mass_break, 6),
        "contract_validation": contract_validation,
    }


def _cluster_continuity_metrics(image: Image.Image, columns: int, rows: int, tile_size: int) -> tuple[float, float]:
    """Compare coarse dark/light canopy classes across tile boundaries."""
    luminance = np.asarray(image.convert("RGB"), dtype=np.float32).mean(axis=2)
    threshold = float(np.median(luminance))
    canopy_class = luminance <= threshold
    matches: list[float] = []
    for tile_x in range(1, columns):
        boundary = tile_x * tile_size
        if boundary < canopy_class.shape[1]:
            matches.append(float(np.mean(canopy_class[:, boundary - 1] == canopy_class[:, boundary])))
    for tile_y in range(1, rows):
        boundary = tile_y * tile_size
        if boundary < canopy_class.shape[0]:
            matches.append(float(np.mean(canopy_class[boundary - 1, :] == canopy_class[boundary, :])))
    continuity = float(np.mean(matches)) if matches else 1.0
    return max(0.0, min(1.0, continuity)), max(0.0, min(1.0, 1.0 - continuity))


def assemble_image(grid: Sequence[Sequence[Any]], image_getter=lambda tile: tile.image) -> Image.Image:
    if not grid or not grid[0]:
        raise ValueError("MAP grid must not be empty")
    tile_images = [[image_getter(tile).convert("RGBA") for tile in row] for row in grid]
    tile_width, tile_height = tile_images[0][0].size
    output = Image.new("RGBA", (tile_width * len(tile_images[0]), tile_height * len(tile_images)), (0, 0, 0, 0))
    for y, row in enumerate(tile_images):
        for x, image in enumerate(row):
            output.paste(image, (x * tile_width, y * tile_height))
    return output
