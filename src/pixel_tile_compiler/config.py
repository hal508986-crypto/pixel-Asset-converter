"""Configuration contracts for the compiler pipeline."""

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Optional

TileMode = Literal["repeatable", "directional", "object"]
SemanticMode = Literal["rule", "mcp"]
DitherMode = Literal["off", "minimal", "ordered"]
SeamMode = Literal["off", "inspect", "correct"]


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
    dither: DitherMode = "minimal"
    background_mode: Literal["auto", "alpha", "color"] = "alpha"
    background_color: Optional[str] = None
    background_tolerance: int = 12
    work_size: int = 256
    smoothing_enabled: bool = True
    seed: int = 42
    debug_enabled: bool = True
    semantic_callable: Optional[Callable[..., Any]] = None

    def __post_init__(self) -> None:
        if (self.width, self.height) != (64, 64):
            raise ValueError("MVP output size is fixed at 64x64")
        if not 4 <= self.palette_budget <= 32:
            raise ValueError("palette_budget must be between 4 and 32")
        if self.background_tolerance < 0:
            raise ValueError("background_tolerance must be non-negative")

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-safe representation, excluding runtime callbacks."""
        values = asdict(self)
        values.pop("semantic_callable", None)
        values["output_root"] = str(self.output_root)
        return values
