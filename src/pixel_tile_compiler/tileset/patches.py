"""Deterministic patch extraction and patch metadata."""

from __future__ import annotations

import random
from dataclasses import dataclass

import numpy as np
from PIL import Image
from skimage.color import rgb2lab


@dataclass(frozen=True)
class PatchMetadata:
    mean_lab: tuple[float, float, float]
    brightness: float
    edge_density: float
    texture_density: float
    position: tuple[int, int]


@dataclass(frozen=True)
class MaterialPatch:
    image: Image.Image
    mean_lab: tuple[float, float, float]
    brightness: float
    edge_density: float
    texture_density: float
    position: tuple[int, int]

    @property
    def metadata(self) -> PatchMetadata:
        return PatchMetadata(
            self.mean_lab,
            self.brightness,
            self.edge_density,
            self.texture_density,
            self.position,
        )


@dataclass(frozen=True)
class PatchDatabase:
    patches: tuple[MaterialPatch, ...]
    patch_size: int
    overlap: int
    seed: int

    @classmethod
    def from_image(
        cls,
        image: Image.Image,
        patch_size: int = 256,
        overlap: int = 64,
        seed: int = 42,
        max_patches: int = 128,
    ) -> "PatchDatabase":
        if patch_size < 2:
            raise ValueError("patch_size must be at least 2")
        if not 0 <= overlap < patch_size:
            raise ValueError("patch_overlap must be between 0 and patch_size - 1")
        source = image.convert("RGBA")
        if source.width < patch_size or source.height < patch_size:
            scale = max(patch_size / source.width, patch_size / source.height)
            source = source.resize(
                (max(patch_size, round(source.width * scale)), max(patch_size, round(source.height * scale))),
                Image.Resampling.BILINEAR,
            )
        step = max(1, patch_size - overlap)
        x_positions = _positions(source.width, patch_size, step)
        y_positions = _positions(source.height, patch_size, step)
        positions = [(x, y) for y in y_positions for x in x_positions]
        rng = random.Random(seed)
        rng.shuffle(positions)
        positions = positions[: max(1, min(max_patches, len(positions)))]
        patches = tuple(_make_patch(source.crop((x, y, x + patch_size, y + patch_size)), (x, y)) for x, y in positions)
        return cls(patches, patch_size, overlap, seed)

    def sample(self, rng: random.Random) -> MaterialPatch:
        if not self.patches:
            raise ValueError("patch database is empty")
        return self.patches[rng.randrange(len(self.patches))]


def _positions(length: int, patch_size: int, step: int) -> list[int]:
    positions = list(range(0, max(1, length - patch_size + 1), step))
    final = max(0, length - patch_size)
    if not positions or positions[-1] != final:
        positions.append(final)
    return sorted(set(positions))


def _make_patch(image: Image.Image, position: tuple[int, int]) -> MaterialPatch:
    rgb = np.asarray(image.convert("RGB"), dtype=np.float32)
    mean_rgb = rgb.mean(axis=(0, 1)) / 255.0
    lab = rgb2lab(mean_rgb.reshape(1, 1, 3))[0, 0]
    brightness = float((rgb @ np.asarray([0.2126, 0.7152, 0.0722])).mean())
    normalized = rgb / 255.0
    gradient = (
        np.abs(np.diff(normalized, axis=1, prepend=normalized[:, :1])).mean(axis=2)
        + np.abs(np.diff(normalized, axis=0, prepend=normalized[:1, :])).mean(axis=2)
    ) * 0.5
    edge_density = float((gradient > 0.045).mean())
    texture_density = float(min(1.0, rgb.std() / 96.0))
    return MaterialPatch(
        image=image.convert("RGBA"),
        mean_lab=tuple(float(value) for value in lab),
        brightness=brightness,
        edge_density=edge_density,
        texture_density=texture_density,
        position=position,
    )
