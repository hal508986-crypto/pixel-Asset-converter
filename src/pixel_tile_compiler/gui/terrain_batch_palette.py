"""Palette contracts for terrain batch comparison and fixed-palette runs."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

from PIL import Image

from pixel_tile_compiler.config import CompilerConfig


def normalize_palette(colors: Iterable[Iterable[int]]) -> tuple[tuple[int, int, int], ...]:
    """Normalize RGB triples into a unique, ascending palette tuple."""
    normalized: set[tuple[int, int, int]] = set()
    for color in colors:
        values = tuple(int(channel) for channel in color)
        if len(values) != 3 or any(channel < 0 or channel > 255 for channel in values):
            raise ValueError("palette colors must contain RGB triples between 0 and 255")
        normalized.add(values)
    return tuple(sorted(normalized))


def extract_final_palette(image_or_path: Image.Image | Path | str) -> tuple[tuple[int, int, int], ...]:
    """Read the actual RGB set from final PNG pixels; never infer colors from debug art."""
    if isinstance(image_or_path, Image.Image):
        image = image_or_path.convert("RGBA")
        return normalize_palette(
            pixel[:3]
            for pixel in image.getdata()
            if pixel[3] != 0
        )
    else:
        with Image.open(Path(image_or_path)) as opened:
            image = opened.convert("RGBA")
            return normalize_palette(
                pixel[:3]
                for pixel in image.getdata()
                if pixel[3] != 0
            )


def validate_reference_palette(colors: Iterable[Iterable[int]]) -> tuple[tuple[int, int, int], ...]:
    """Validate the palette selection contract without padding the actual color set."""
    normalized = normalize_palette(colors)
    if not normalized:
        raise ValueError("reference palette must contain at least one visible color")
    if len(normalized) > 64:
        raise ValueError("reference palette must contain at most 64 colors")
    return normalized


def palette_id(colors: Iterable[Iterable[int]]) -> str:
    """Return the contract-stable ID for a normalized RGB palette."""
    normalized = validate_reference_palette(colors)
    payload = b"palette-rgb-v1\n" + bytes(channel for color in normalized for channel in color)
    return hashlib.sha256(payload).hexdigest()


def fixed_palette_config(
    config: CompilerConfig,
    colors: Iterable[Iterable[int]],
    output_root: Path | str,
) -> CompilerConfig:
    """Clone all effective conditions while changing only the fixed palette."""
    normalized = validate_reference_palette(colors)
    return replace(
        config,
        output_root=Path(output_root),
        palette_budget=max(4, len(normalized)),
        palette_colors=normalized,
    )


def metadata_palette_matches_final(
    final_path: Path | str,
    metadata: dict[str, Any] | None,
) -> tuple[tuple[tuple[int, int, int], ...], str | None]:
    """Use metadata only when its palette exactly matches the final image."""
    measured = extract_final_palette(final_path)
    if metadata is None:
        return measured, None
    if "transformation" not in metadata:
        return measured, None
    transformation = metadata.get("transformation")
    if not isinstance(transformation, dict):
        return measured, "metadata palette is invalid; colors were measured from final.png"
    raw = transformation.get("palette_colors")
    if raw is None:
        return measured, None
    try:
        from_metadata = normalize_palette(raw)
    except (TypeError, ValueError):
        return measured, "metadata palette is invalid; colors were measured from final.png"
    if from_metadata != measured:
        return measured, "metadata palette differs from final.png; colors were measured from final.png"
    return measured, None


__all__ = [
    "extract_final_palette",
    "fixed_palette_config",
    "metadata_palette_matches_final",
    "normalize_palette",
    "palette_id",
    "validate_reference_palette",
]
