"""Wang-style edge contract data and edge-profile utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
from PIL import Image

EdgeSide = Literal["north", "east", "south", "west"]


@dataclass(frozen=True)
class EdgeContract:
    north: str
    east: str
    south: str
    west: str

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value for value in (self.north, self.east, self.south, self.west)):
            raise ValueError("edge contract IDs must be non-empty strings")

    def as_dict(self) -> dict[str, str]:
        return {
            "north": self.north,
            "east": self.east,
            "south": self.south,
            "west": self.west,
        }


@dataclass(frozen=True)
class EdgeProfile:
    side: EdgeSide
    colors: tuple[tuple[int, int, int], ...]
    brightness: tuple[float, ...]
    cluster_run_lengths: tuple[int, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "side": self.side,
            "colors": [list(color) for color in self.colors],
            "brightness": [round(value, 6) for value in self.brightness],
            "cluster_run_lengths": list(self.cluster_run_lengths),
        }


def edge_line(image: Image.Image, side: EdgeSide) -> np.ndarray:
    """Return the RGB pixels on one outer boundary in traversal order."""
    rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    if side == "north":
        line = rgb[0, :, :]
    elif side == "east":
        line = rgb[:, -1, :]
    elif side == "south":
        line = rgb[-1, :, :]
    else:
        line = rgb[:, 0, :]
    # Corner pixels belong to two different contracts and may legitimately
    # be overwritten by the perpendicular strip.  Exclude them from the
    # profile so the interface comparison measures the actual edge body.
    return line[1:-1] if len(line) > 2 else line


def edge_profile(image: Image.Image, side: EdgeSide) -> EdgeProfile:
    line = edge_line(image, side)
    colors = tuple(tuple(int(value) for value in pixel) for pixel in line)
    brightness = tuple(float(pixel.mean() / 255.0) for pixel in line)
    runs: list[int] = []
    if colors:
        current = colors[0]
        length = 1
        for color in colors[1:]:
            if color == current:
                length += 1
            else:
                runs.append(length)
                current, length = color, 1
        runs.append(length)
    return EdgeProfile(side, colors, brightness, tuple(runs))


def compare_edge_profiles(first: EdgeProfile, second: EdgeProfile) -> dict[str, float]:
    """Compare two same-direction-length profiles with normalized RGB/Luma signals."""
    length = min(len(first.colors), len(second.colors))
    if length == 0:
        return {"color_distance": 0.0, "brightness_distance": 0.0, "cluster_similarity": 1.0, "score": 0.0}
    first_rgb = np.asarray(first.colors[:length], dtype=np.float32)
    second_rgb = np.asarray(second.colors[:length], dtype=np.float32)
    color_distance = float(np.linalg.norm(first_rgb - second_rgb, axis=1).mean() / 441.6729559)
    brightness_distance = float(np.abs(np.asarray(first.brightness[:length]) - np.asarray(second.brightness[:length])).mean())
    run_similarity = _run_similarity(first.cluster_run_lengths, second.cluster_run_lengths)
    return {
        "color_distance": round(color_distance, 6),
        "brightness_distance": round(brightness_distance, 6),
        "cluster_similarity": round(run_similarity, 6),
        "score": round(0.65 * color_distance + 0.25 * brightness_distance + 0.10 * (1.0 - run_similarity), 6),
    }


def _run_similarity(first: tuple[int, ...], second: tuple[int, ...]) -> float:
    if not first and not second:
        return 1.0
    length = max(len(first), len(second))
    first_array = np.pad(np.asarray(first, dtype=np.float32), (0, length - len(first)))
    second_array = np.pad(np.asarray(second, dtype=np.float32), (0, length - len(second)))
    difference = np.abs(first_array - second_array).sum() / max(1.0, first_array.sum() + second_array.sum())
    return max(0.0, min(1.0, 1.0 - float(difference)))
