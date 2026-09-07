"""User-facing GUI policies kept separate from Qt widgets."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CharacterGuiProfile:
    """The safe, square Character profile exposed by the first GUI slice."""

    canvas_size: tuple[int, int]
    palette_budget: int = 24
    detail_level: str = "balanced"
    tile_mode: str = "object"
    pixelization_mode: str = "nearest"


GUI_CHARACTER_CANVAS_SIZES = ((64, 64), (128, 128))


def resolve_character_gui_profile(canvas_size: tuple[int, int] = (128, 128)) -> CharacterGuiProfile:
    """Resolve the GUI's square Character preset without exposing unsupported geometry."""
    normalized = (int(canvas_size[0]), int(canvas_size[1]))
    if normalized not in GUI_CHARACTER_CANVAS_SIZES:
        raise ValueError("GUI currently supports only square 64x64 or 128x128 Character canvases")
    return CharacterGuiProfile(canvas_size=normalized)


__all__ = ["CharacterGuiProfile", "GUI_CHARACTER_CANVAS_SIZES", "resolve_character_gui_profile"]
