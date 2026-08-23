"""Fixed-layout and contract-aware assembly for semantic tiles."""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from typing import Any

import numpy as np
from PIL import Image

from .contracts import SemanticTile, contracts_compatible


def assemble_semantic_grid(
    candidates: Mapping[str, Sequence[SemanticTile]] | Sequence[Sequence[SemanticTile]] | Sequence[SemanticTile],
    columns: int,
    rows: int,
    seed: int = 42,
    family_layout: Sequence[Sequence[str]] | None = None,
) -> list[list[SemanticTile]]:
    """Assemble a grid by matching north/west semantic profiles.

    A nested grid is treated as an already selected fixed layout.  A mapping
    plus ``family_layout`` enables deterministic contract-aware selection.
    """
    if columns < 1 or rows < 1:
        raise ValueError("columns and rows must be positive")
    if family_layout is None and isinstance(candidates, Sequence) and candidates:
        first = candidates[0]  # type: ignore[index]
        if isinstance(first, Sequence) and first and isinstance(first[0], SemanticTile):
            grid = [list(row) for row in candidates]  # type: ignore[arg-type]
            if len(grid) != rows or any(len(row) != columns for row in grid):
                raise ValueError("fixed semantic grid dimensions do not match")
            return grid
        if isinstance(first, SemanticTile):
            options = list(candidates)  # type: ignore[arg-type]
            candidates = {"*": options}
            family_layout = [["*"] * columns for _ in range(rows)]
    if family_layout is None or not isinstance(candidates, Mapping):
        raise ValueError("family_layout and candidate mapping are required")
    if len(family_layout) != rows or any(len(row) != columns for row in family_layout):
        raise ValueError("family layout dimensions do not match")
    rng = random.Random(seed)
    selected: list[SemanticTile | None] = [None] * (columns * rows)

    def visit(index: int) -> bool:
        if index == len(selected):
            return True
        x, y = index % columns, index // columns
        family = family_layout[y][x]
        options = list(candidates.get(family, ()))
        rng.shuffle(options)
        options.sort(key=lambda tile: (tile.spec.variant, tile.spec.tile_id))
        for tile in options:
            if x and not contracts_compatible(
                selected[index - 1].spec.semantic_contract, tile.spec.semantic_contract, "east"  # type: ignore[union-attr]
            ):
                continue
            if y and not contracts_compatible(
                selected[index - columns].spec.semantic_contract, tile.spec.semantic_contract, "south"  # type: ignore[union-attr]
            ):
                continue
            selected[index] = tile
            if visit(index + 1):
                return True
        selected[index] = None
        return False

    if not visit(0):
        raise ValueError("semantic tile contracts cannot assemble the requested layout")
    return [selected[row * columns : (row + 1) * columns] for row in range(rows)]  # type: ignore[list-item]


def assemble_independent_grid(
    family_layout: Sequence[Sequence[str]],
    candidates: Mapping[str, Sequence[SemanticTile]],
    seed: int = 42,
) -> list[list[SemanticTile]]:
    """Select the same requested family layout while ignoring edge contracts."""
    result: list[list[SemanticTile]] = []
    index = seed
    for row in family_layout:
        values: list[SemanticTile] = []
        for family in row:
            options = list(candidates.get(family, ()))
            if not options:
                raise ValueError(f"no semantic tiles for family {family}")
            values.append(options[index % len(options)])
            index += 1
        result.append(values)
    return result


def validate_semantic_adjacency(grid: Sequence[Sequence[SemanticTile]]) -> bool:
    if not grid or not grid[0]:
        return True
    columns = len(grid[0])
    for row, values in enumerate(grid):
        if len(values) != columns:
            return False
        for column, tile in enumerate(values):
            if column + 1 < columns and not contracts_compatible(
                tile.spec.semantic_contract, values[column + 1].spec.semantic_contract, "east"
            ):
                return False
            if row + 1 < len(grid) and not contracts_compatible(
                tile.spec.semantic_contract, grid[row + 1][column].spec.semantic_contract, "south"
            ):
                return False
    return True


def assemble_image(grid: Sequence[Sequence[Any]]) -> Image.Image:
    if not grid or not grid[0]:
        raise ValueError("semantic grid must not be empty")
    images = [[tile.image for tile in row] for row in grid]
    width, height = images[0][0].size
    output = Image.new("RGBA", (width * len(images[0]), height * len(images)), (0, 0, 0, 0))
    for y, row in enumerate(images):
        for x, image in enumerate(row):
            output.paste(image.convert("RGBA"), (x * width, y * height))
    return output


def grid_mask(grid: Sequence[Sequence[SemanticTile]], masks: Mapping[str, np.ndarray]) -> np.ndarray:
    """Assemble a bool mask using tile IDs, for metric calculations."""
    if not grid or not grid[0]:
        raise ValueError("semantic grid must not be empty")
    values = [[masks[tile.spec.tile_id] for tile in row] for row in grid]
    return np.block(values).astype(bool)
