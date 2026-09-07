"""Machine-observable metrics for the character density Study."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from PIL import Image


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def image_metrics(image: Image.Image, output_path: Path, palette_budget: int) -> dict[str, Any]:
    rgba = image.convert("RGBA")
    alpha = list(rgba.getchannel("A").getdata())
    visible = [pixel[:3] for pixel in rgba.getdata() if pixel[3] != 0]
    bbox = rgba.getchannel("A").getbbox()
    return {
        "actual_palette_count": len(set(visible)),
        "palette_utilization": round(len(set(visible)) / palette_budget, 6) if palette_budget else 0.0,
        "visible_bbox": list(bbox) if bbox else None,
        "visible_pixel_count": len(visible),
        "alpha_unique_values": sorted(set(alpha)),
        "alpha_binary": set(alpha).issubset({0, 255}),
        "palette_budget": palette_budget,
        "output_sha256": sha256_file(output_path),
    }


def compare_visible_pixels(reference: Image.Image, candidate: Image.Image) -> tuple[int, float]:
    reference_rgba = reference.convert("RGBA")
    candidate_rgba = candidate.convert("RGBA")
    if reference_rgba.size != candidate_rgba.size:
        raise ValueError("character Study outputs must have the same dimensions")
    reference_pixels = list(reference_rgba.getdata())
    candidate_pixels = list(candidate_rgba.getdata())
    visible_count = sum(1 for pixel in reference_pixels if pixel[3] != 0)
    changed = sum(
        1
        for reference_pixel, candidate_pixel in zip(reference_pixels, candidate_pixels)
        if reference_pixel[3] != 0 and reference_pixel[:3] != candidate_pixel[:3]
    )
    return changed, round(changed / visible_count, 6) if visible_count else 0.0
