"""Input-file rules shared by the GUI file dialog and drag-and-drop path."""

from __future__ import annotations

from pathlib import Path
from typing import Iterable


SUPPORTED_IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})


def first_supported_image_path(paths: Iterable[Path | str]) -> Path | None:
    """Return the first existing image file from a dropped path collection."""
    for candidate in paths:
        path = Path(candidate)
        if path.is_file() and path.suffix.casefold() in SUPPORTED_IMAGE_SUFFIXES:
            return path
    return None


__all__ = ["SUPPORTED_IMAGE_SUFFIXES", "first_supported_image_path"]
