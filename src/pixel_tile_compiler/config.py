"""Configuration contracts for the compiler pipeline."""

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Optional

TileMode = Literal["repeatable", "directional", "object"]
SemanticMode = Literal["rule", "mcp"]
DitherMode = Literal["off", "minimal", "ordered"]
SeamMode = Literal["off", "inspect", "correct"]
PixelizationMode = Literal["region", "nearest"]
OutlineColor = Literal["off", "black", "white"]


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
    width: int = 64
    height: int = 64
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
    work_size: int = 256
    smoothing_enabled: bool = True
    seed: int = 42
    debug_enabled: bool = True
    semantic_callable: Optional[Callable[..., Any]] = None
    palette_colors: Optional[tuple[tuple[int, int, int], ...]] = None
    quantize_enabled: bool = True
    color_conditioning: ColorConditioningConfig = field(default_factory=ColorConditioningConfig)

    def __post_init__(self) -> None:
        if (self.width, self.height) != (64, 64):
            raise ValueError("MVP output size is fixed at 64x64")
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
        return values


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
