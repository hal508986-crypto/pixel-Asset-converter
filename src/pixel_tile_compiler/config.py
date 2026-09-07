"""Configuration contracts for the compiler pipeline."""

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from pixel_tile_compiler.pixelizer.character_detail import CharacterDetailLevel

TileMode = Literal["repeatable", "directional", "object"]
SemanticMode = Literal["rule", "mcp"]
DitherMode = Literal["off", "minimal", "ordered"]
SeamMode = Literal["off", "inspect", "correct"]
PixelizationMode = Literal["region", "nearest"]
OutlineColor = Literal["off", "black", "white"]
CompilerPurpose = Literal["terrain", "character"]


@dataclass(frozen=True)
class CanvasSpec:
    """Final raster dimensions for one standalone image compilation."""

    width: int = 64
    height: int = 64

    def __post_init__(self) -> None:
        if self.width < 1 or self.height < 1:
            raise ValueError("canvas dimensions must be positive")

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height


@dataclass(frozen=True)
class ColorConditioningConfig:
    """Deterministic RGB conditioning applied after spatial pixelization."""

    brightness_scale: float = 1.0
    saturation_scale: float = 1.0
    saturation_cap: Optional[float] = None

    def __post_init__(self) -> None:
        if self.brightness_scale <= 0.0:
            raise ValueError("brightness_scale must be greater than 0")
        if self.saturation_scale < 0.0:
            raise ValueError("saturation_scale must be non-negative")
        if self.saturation_cap is not None and not 0.0 <= self.saturation_cap <= 1.0:
            raise ValueError("saturation_cap must be between 0 and 1")


@dataclass
class CompilerConfig:
    """Runtime configuration shared by CLI, GUI, and core pipeline."""

    output_root: Path = field(default_factory=lambda: Path("output"))
    canvas: CanvasSpec = field(default_factory=CanvasSpec)
    palette_budget: int = 16
    tile_mode: TileMode = "repeatable"
    semantic_provider: SemanticMode = "rule"
    seam_mode: SeamMode = "inspect"
    repeat_opt_enabled: bool = True
    repeat_opt_strength: float = 0.5
    repeat_opt_edge_band: int = 6
    center_suppression_strength: float = 0.4
    dither: DitherMode = "minimal"
    background_mode: Literal["auto", "alpha", "color"] = "alpha"
    background_color: Optional[str] = None
    background_tolerance: int = 12
    pixelization_mode: PixelizationMode = "region"
    outline_color: OutlineColor = "off"
    character_frame_width: int | None = None
    character_frame_height: int | None = None
    character_bottom_margin: int | None = None
    character_detail_level: CharacterDetailLevel = "detailed"
    work_size: int = 256
    smoothing_enabled: bool = True
    seed: int = 42
    debug_enabled: bool = True
    semantic_callable: Optional[Callable[..., Any]] = None
    palette_colors: Optional[tuple[tuple[int, int, int], ...]] = None
    quantize_enabled: bool = True
    color_conditioning: ColorConditioningConfig = field(default_factory=ColorConditioningConfig)

    def __post_init__(self) -> None:
        if not isinstance(self.canvas, CanvasSpec):
            raise ValueError("canvas must be a CanvasSpec")
        if self.canvas.size != (64, 64) and not (self.tile_mode == "object" and self.pixelization_mode == "nearest"):
            raise ValueError("non-64 output canvas is currently supported only for object/nearest compilation")
        if not 4 <= self.palette_budget <= 64:
            raise ValueError("palette_budget must be between 4 and 64")
        if self.palette_colors is not None:
            if not self.palette_colors:
                raise ValueError("palette_colors must not be empty")
            if len(self.palette_colors) > self.palette_budget:
                raise ValueError("palette_colors cannot exceed palette_budget")
            if any(len(color) != 3 or any(not 0 <= channel <= 255 for channel in color) for color in self.palette_colors):
                raise ValueError("palette_colors must contain RGB triples")
        if self.background_tolerance < 0:
            raise ValueError("background_tolerance must be non-negative")
        if self.pixelization_mode not in {"region", "nearest"}:
            raise ValueError("pixelization_mode must be region or nearest")
        if self.outline_color not in {"off", "black", "white"}:
            raise ValueError("outline_color must be off, black, or white")
        if self.character_detail_level not in {"sparse", "balanced", "detailed"}:
            raise ValueError("character_detail_level must be sparse, balanced, or detailed")
        if self.character_frame_width is not None and self.character_frame_width < 1:
            raise ValueError("character_frame_width must be positive")
        if self.character_frame_height is not None and self.character_frame_height < 1:
            raise ValueError("character_frame_height must be positive")
        if self.character_bottom_margin is not None and self.character_bottom_margin < 0:
            raise ValueError("character_bottom_margin must be non-negative")
        layout = self.character_layout
        outline_width = 1 if self.outline_color != "off" else 0
        if layout.frame_width + outline_width * 2 > self.width:
            raise ValueError("character_frame_width does not fit the output canvas")
        if layout.frame_height + outline_width * 2 + layout.bottom_margin > self.height:
            raise ValueError("character frame and bottom margin do not fit the output canvas")
        if not 0.0 <= self.repeat_opt_strength <= 1.0:
            raise ValueError("repeat_opt_strength must be between 0 and 1")
        if self.repeat_opt_edge_band < 1:
            raise ValueError("repeat_opt_edge_band must be at least 1")
        if not 0.0 <= self.center_suppression_strength <= 1.0:
            raise ValueError("center_suppression_strength must be between 0 and 1")

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation, excluding runtime callbacks."""
        values = asdict(self)
        values.pop("semantic_callable", None)
        values["output_root"] = str(self.output_root)
        values["width"] = self.width
        values["height"] = self.height
        values["character_frame_width"] = self.character_layout.frame_width
        values["character_frame_height"] = self.character_layout.frame_height
        values["character_bottom_margin"] = self.character_layout.bottom_margin
        return values

    @property
    def width(self) -> int:
        return self.canvas.width

    @property
    def height(self) -> int:
        return self.canvas.height

    @property
    def character_layout(self):
        from pixel_tile_compiler.pixelizer.character import resolve_character_layout

        return resolve_character_layout(
            self.canvas,
            frame_width=self.character_frame_width,
            frame_height=self.character_frame_height,
            bottom_margin=self.character_bottom_margin,
        )


@dataclass
class MapCompilerConfig:
    """Configuration for MAP-first compilation while preserving single-tile defaults."""

    output_root: Path = field(default_factory=lambda: Path("map_output"))
    columns: int = 4
    rows: int = 5
    tile_size: int = 64
    context_margin_tiles: int = 1
    shared_palette_enabled: bool = True
    global_palette_budget: int = 24
    tile_mode: TileMode = "repeatable"
    semantic_provider: SemanticMode = "rule"
    seed: int = 42
    debug_enabled: bool = True
    smoothing_enabled: bool = True

    def __post_init__(self) -> None:
        if self.columns < 1 or self.rows < 1:
            raise ValueError("columns and rows must be positive")
        if self.tile_size != 64:
            raise ValueError("MVP tile_size is fixed at 64")
        if self.context_margin_tiles < 0:
            raise ValueError("context_margin_tiles must be non-negative")
        if self.global_palette_budget not in {16, 24, 32}:
            raise ValueError("global_palette_budget must be 16, 24, or 32")

    @property
    def output_size(self) -> tuple[int, int]:
        """Return the reconstructed MAP size in pixels."""
        return self.columns * self.tile_size, self.rows * self.tile_size


def compiler_config_for_purpose(purpose: CompilerPurpose, **overrides: Any) -> CompilerConfig:
    """Build the shared CLI/GUI configuration for terrain or character work."""
    if purpose not in {"terrain", "character"}:
        raise ValueError("purpose must be terrain or character")
    if purpose == "character":
        overrides.update(
            {
                "tile_mode": "object",
                "repeat_opt_enabled": False,
                "dither": "off",
                "background_mode": "auto",
                "pixelization_mode": "nearest",
                "outline_color": "off",
                "smoothing_enabled": False,
            }
        )
    return CompilerConfig(**overrides)
