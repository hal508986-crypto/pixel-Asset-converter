"""Additive semantic edge contracts for surface/network/transition tiles."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from PIL import Image

EdgeSide = Literal["north", "east", "south", "west"]


@dataclass(frozen=True)
class EdgeSemanticProfile:
    """Describe what a tile edge exposes to the neighboring tile."""

    role: str
    material: str
    materials: tuple[str, ...] = ()
    feature_type: str | None = None
    feature_center: float | None = None
    feature_width: float | None = None
    orientation: str | None = None
    connects_to: tuple[str, ...] = ()
    flow: str | None = None

    def __post_init__(self) -> None:
        if not self.role or not self.material:
            raise ValueError("semantic edge role and material must be non-empty")
        values = self.materials or (self.material,)
        if any(not value for value in values):
            raise ValueError("semantic edge materials must be non-empty")
        if self.feature_center is not None and not 0.0 <= self.feature_center <= 1.0:
            raise ValueError("feature_center must be normalized to 0..1")
        if self.feature_width is not None and not 0.0 < self.feature_width <= 1.0:
            raise ValueError("feature_width must be normalized to (0, 1]")
        if self.flow is not None and self.flow not in {"in", "out", "bidirectional", "none"}:
            raise ValueError("flow must be in, out, bidirectional, or none")
        object.__setattr__(self, "materials", tuple(values))

    def as_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "material": self.material,
            "materials": list(self.materials),
            "feature_type": self.feature_type,
            "feature_center": self.feature_center,
            "feature_width": self.feature_width,
            "orientation": self.orientation,
            "connects_to": list(self.connects_to),
            "flow": self.flow,
        }


@dataclass(frozen=True)
class SemanticEdgeContract:
    north: EdgeSemanticProfile
    east: EdgeSemanticProfile
    south: EdgeSemanticProfile
    west: EdgeSemanticProfile
    orientation: str | None = None

    def for_side(self, side: EdgeSide) -> EdgeSemanticProfile:
        return getattr(self, side)

    def as_dict(self) -> dict[str, object]:
        return {
            "north": self.north.as_dict(),
            "east": self.east.as_dict(),
            "south": self.south.as_dict(),
            "west": self.west.as_dict(),
            "orientation": self.orientation,
        }


@dataclass(frozen=True)
class SemanticTileSpec:
    tile_id: str
    family: str
    semantic_contract: SemanticEdgeContract
    topology: str | None = None
    material_a: str | None = None
    material_b: str | None = None
    variant: int = 0
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def orientation(self) -> str | None:
        """Compatibility alias for transition/network orientation assertions."""
        return self.topology

    def as_dict(self) -> dict[str, object]:
        return {
            "tile_id": self.tile_id,
            "family": self.family,
            "topology": self.topology,
            "material_a": self.material_a,
            "material_b": self.material_b,
            "variant": self.variant,
            "semantic_contract": self.semantic_contract.as_dict(),
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class SemanticTile:
    spec: SemanticTileSpec
    source_image: Image.Image
    pixel_image: Image.Image | None = None

    @property
    def image(self) -> Image.Image:
        return (self.pixel_image or self.source_image).convert("RGBA")


def profiles_compatible(first: EdgeSemanticProfile, second: EdgeSemanticProfile) -> bool:
    """Check whether two external edge descriptions can be joined."""
    if first.material != second.material and not set(first.materials).intersection(second.materials):
        return False
    if first.role == "transition_boundary" or second.role == "transition_boundary":
        if first.role == second.role == "transition_boundary":
            return first.materials == second.materials and first.orientation == second.orientation
        return True
    if first.role == "network_connector" and second.role == "network_connector":
        if first.feature_type != second.feature_type:
            return False
        if first.feature_center is not None and second.feature_center is not None:
            if abs(first.feature_center - second.feature_center) > 0.06:
                return False
        if first.feature_width is not None and second.feature_width is not None:
            if abs(first.feature_width - second.feature_width) > 0.08:
                return False
        if first.flow and second.flow:
            if first.flow == "out" and second.flow not in {"in", "bidirectional"}:
                return False
            if first.flow == "in" and second.flow not in {"out", "bidirectional"}:
                return False
            if first.flow == "none" or second.flow == "none":
                return False
    return True


def contracts_compatible(first: SemanticEdgeContract, second: SemanticEdgeContract, side: EdgeSide) -> bool:
    opposite: dict[EdgeSide, EdgeSide] = {
        "north": "south",
        "east": "west",
        "south": "north",
        "west": "east",
    }
    return profiles_compatible(first.for_side(side), second.for_side(opposite[side]))
