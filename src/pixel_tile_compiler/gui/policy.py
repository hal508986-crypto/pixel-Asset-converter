"""User-facing GUI policies kept separate from Qt widgets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CharacterGuiProfile:
    """The safe, square Character profile exposed by the first GUI slice."""

    canvas_size: tuple[int, int]
    palette_budget: int = 24
    detail_level: str = "balanced"
    tile_mode: str = "object"
    pixelization_mode: str = "nearest"


@dataclass(frozen=True)
class CharacterAnimationGuiProfile:
    """The shared-layout animation profile exposed by the GUI."""

    canvas_size: tuple[int, int]
    frame_count: int = 4
    fit_within: tuple[int, int] = (54, 54)
    bottom_margin: int = 6
    palette_budget: int = 24
    detail_level: str = "balanced"


@dataclass(frozen=True)
class TerrainGuiProfile:
    """The terrain conversion choices exposed by the GUI."""

    pixelization_mode: str = "nearest"
    label: str = "元絵を保持"
    repeat_opt_enabled: bool = False


GUI_CHARACTER_CANVAS_SIZES = ((64, 64), (128, 128))
GUI_TERRAIN_PIXELIZATION_OPTIONS = (
    ("元絵を保持", "nearest"),
    ("領域を整理", "region"),
)


def resolve_character_gui_profile(canvas_size: tuple[int, int] = (128, 128)) -> CharacterGuiProfile:
    """Resolve the GUI's square Character preset without exposing unsupported geometry."""
    normalized = (int(canvas_size[0]), int(canvas_size[1]))
    if normalized not in GUI_CHARACTER_CANVAS_SIZES:
        raise ValueError("GUI currently supports only square 64x64 or 128x128 Character canvases")
    return CharacterGuiProfile(canvas_size=normalized)


def resolve_character_animation_gui_profile(
    canvas_size: tuple[int, int] = (128, 128),
) -> CharacterAnimationGuiProfile:
    """Resolve animation layout values proportionally to the selected square canvas."""
    normalized = resolve_character_gui_profile(canvas_size).canvas_size
    width, height = normalized
    return CharacterAnimationGuiProfile(
        canvas_size=normalized,
        fit_within=(_scale_half_up(54, width, 64), _scale_half_up(54, height, 64)),
        bottom_margin=_scale_half_up(6, height, 64),
    )


def _scale_half_up(value: int, target: int, reference: int) -> int:
    return (value * target + reference // 2) // reference


def resolve_terrain_gui_profile(pixelization_mode: str = "nearest") -> TerrainGuiProfile:
    """Resolve one of the terrain conversion modes without changing core defaults."""
    for label, mode in GUI_TERRAIN_PIXELIZATION_OPTIONS:
        if pixelization_mode == mode:
            return TerrainGuiProfile(pixelization_mode=mode, label=label)
    raise ValueError("terrain pixelization mode must be nearest or region")


def build_output_path(
    output_root: Path | str,
    source_path: Path | str,
    *,
    purpose: str,
    canvas_size: tuple[int, int],
    pixelization_mode: str | None = None,
    palette_budget: int | None = None,
    repeat_opt_enabled: bool | None = None,
) -> Path:
    """Build the stable GUI output layout below the user-selected root."""
    root = Path(output_root).expanduser()
    source = Path(source_path)
    width, height = int(canvas_size[0]), int(canvas_size[1])
    if purpose == "character":
        variant = f"character_{width}x{height}_b24"
    elif purpose == "character_animation":
        variant = f"character_animation_{width}x{height}_b24"
    elif purpose == "terrain":
        if pixelization_mode is None and palette_budget is None and repeat_opt_enabled is None:
            return root / source.stem / "terrain_64x64"
        if pixelization_mode is None or palette_budget is None or repeat_opt_enabled is None:
            raise ValueError("terrain output identity requires pixelization mode, palette budget, and repeat setting")
        resolve_terrain_gui_profile(pixelization_mode)
        if not 4 <= int(palette_budget) <= 64:
            raise ValueError("terrain palette budget must be between 4 and 64")
        repeat_label = "on" if repeat_opt_enabled else "off"
        variant = f"terrain_64x64_{pixelization_mode}_{int(palette_budget)}c_repeat-{repeat_label}"
    else:
        raise ValueError("purpose must be character or terrain")
    return root / source.stem / variant


__all__ = [
    "CharacterAnimationGuiProfile",
    "CharacterGuiProfile",
    "GUI_CHARACTER_CANVAS_SIZES",
    "GUI_TERRAIN_PIXELIZATION_OPTIONS",
    "TerrainGuiProfile",
    "build_output_path",
    "resolve_character_gui_profile",
    "resolve_character_animation_gui_profile",
    "resolve_terrain_gui_profile",
]
