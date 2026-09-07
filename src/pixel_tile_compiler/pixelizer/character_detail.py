"""Character-only post-quantization detail density controls."""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from math import sqrt
from typing import Literal

from PIL import Image

CharacterDetailLevel = Literal["sparse", "balanced", "detailed"]


@dataclass(frozen=True)
class CharacterDetailProfile:
    """Deterministic thresholds for one character detail density level."""

    max_low_contrast_component_area: int
    contrast_keep_threshold: float


DETAIL_PROFILES: dict[str, CharacterDetailProfile] = {
    "detailed": CharacterDetailProfile(max_low_contrast_component_area=0, contrast_keep_threshold=48.0),
    "balanced": CharacterDetailProfile(max_low_contrast_component_area=2, contrast_keep_threshold=48.0),
    "sparse": CharacterDetailProfile(max_low_contrast_component_area=4, contrast_keep_threshold=48.0),
}


def character_detail_profile(
    level: CharacterDetailLevel | str,
    canvas_size: tuple[int, int] = (64, 64),
) -> CharacterDetailProfile:
    """Return a validated profile with area thresholds scaled from 64px."""
    try:
        base = DETAIL_PROFILES[str(level)]
    except KeyError as exc:
        raise ValueError("character detail level must be sparse, balanced, or detailed") from exc
    if base.max_low_contrast_component_area == 0:
        return base
    width, height = canvas_size
    if width < 1 or height < 1:
        raise ValueError("canvas dimensions must be positive")
    scale = min(width / 64.0, height / 64.0)
    scaled_area = max(1, int(base.max_low_contrast_component_area * scale * scale + 0.5))
    return CharacterDetailProfile(scaled_area, base.contrast_keep_threshold)


def simplify_character_detail(
    image: Image.Image,
    level: CharacterDetailLevel | str,
    canvas_size: tuple[int, int] = (64, 64),
) -> Image.Image:
    """Simplify only low-contrast micro-components while preserving alpha exactly.

    The source palette is the only color source. Components touching the alpha
    silhouette boundary and components with enough RGB contrast are protected.
    All decisions are made from the immutable source image so traversal order
    cannot change the result.
    """
    source = image.convert("RGBA")
    profile = character_detail_profile(level, canvas_size=canvas_size)
    result = source.copy()
    if profile.max_low_contrast_component_area == 0:
        return result

    width, height = source.size
    pixels = source.load()
    visited: set[tuple[int, int]] = set()
    components: list[tuple[tuple[tuple[int, int], ...], tuple[int, int, int], bool, Counter[tuple[int, int, int]]]] = []

    for y in range(height):
        for x in range(width):
            if (x, y) in visited or pixels[x, y][3] == 0:
                continue
            color = pixels[x, y][:3]
            queue = deque([(x, y)])
            visited.add((x, y))
            component: list[tuple[int, int]] = []
            neighbors: Counter[tuple[int, int, int]] = Counter()
            touches_boundary = False
            while queue:
                current_x, current_y = queue.popleft()
                component.append((current_x, current_y))
                for neighbor_x, neighbor_y in _neighbors(current_x, current_y, width, height):
                    if not (0 <= neighbor_x < width and 0 <= neighbor_y < height):
                        touches_boundary = True
                        continue
                    neighbor = pixels[neighbor_x, neighbor_y]
                    if neighbor[3] == 0:
                        touches_boundary = True
                    elif neighbor[:3] == color:
                        if (neighbor_x, neighbor_y) not in visited:
                            visited.add((neighbor_x, neighbor_y))
                            queue.append((neighbor_x, neighbor_y))
                    else:
                        neighbors[neighbor[:3]] += 1
            components.append((tuple(component), color, touches_boundary, neighbors))

    for component, color, touches_boundary, neighbors in components:
        if len(component) > profile.max_low_contrast_component_area or touches_boundary or not neighbors:
            continue
        replacement, count = min(neighbors.items(), key=lambda item: (-item[1], item[0]))
        if count <= 0 or _rgb_distance(color, replacement) >= profile.contrast_keep_threshold:
            continue
        for x, y in component:
            alpha = pixels[x, y][3]
            result.putpixel((x, y), (*replacement, alpha))
    return result


def _neighbors(x: int, y: int, width: int, height: int) -> tuple[tuple[int, int], ...]:
    """Return four-neighbor coordinates, including out-of-bounds sentinels."""
    return ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))


def _rgb_distance(first: tuple[int, int, int], second: tuple[int, int, int]) -> float:
    return sqrt(sum((left - right) ** 2 for left, right in zip(first, second)))


__all__ = [
    "CharacterDetailLevel",
    "CharacterDetailProfile",
    "DETAIL_PROFILES",
    "character_detail_profile",
    "simplify_character_detail",
]
