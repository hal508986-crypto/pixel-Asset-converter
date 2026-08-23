"""Profiles that describe semantic pixel-grammar preferences."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class DensityLevel(str, Enum):
    SPARSE = "sparse"
    BALANCED = "balanced"
    DETAILED = "detailed"


class HierarchyMode(str, Enum):
    FLAT = "flat"
    STRUCTURED = "structured"
    VOLUMETRIC = "volumetric"


@dataclass(frozen=True)
class PixelGrammarProfile:
    """A serializable, semantic-facing preference profile for 64x64 rendering."""

    name: str
    semantic_role: str
    material: str
    preferred_density: DensityLevel = DensityLevel.BALANCED
    preferred_frequency: str = "medium"
    major_cluster_scale: str = "medium"
    micro_detail_level: float = 0.45
    silhouette_priority: float = 0.25
    topology_priority: float = 0.0
    shading_strength: float = 0.35
    texture_strength: float = 0.55
    transition_band_emphasis: float = 0.0
    hierarchy_mode: HierarchyMode = HierarchyMode.STRUCTURED
    major_mass_strength: float = 0.80
    medium_cluster_strength: float = 0.50
    shading_layers: int = 2
    depth_cue_strength: float = 0.20
    contact_shadow_strength: float = 0.10
    highlight_strength: float = 0.10
    micro_detail_strength: float = 0.20

    def __post_init__(self) -> None:
        object.__setattr__(self, "preferred_density", DensityLevel(self.preferred_density))
        object.__setattr__(self, "hierarchy_mode", HierarchyMode(self.hierarchy_mode))
        if not self.name or not self.material:
            raise ValueError("profile name and material must be non-empty")
        if self.semantic_role not in {"surface", "network", "transition", "object"}:
            raise ValueError("semantic_role must be surface, network, transition, or object")
        for field_name in (
            "micro_detail_level",
            "silhouette_priority",
            "topology_priority",
            "shading_strength",
            "texture_strength",
            "transition_band_emphasis",
        ):
            value = float(getattr(self, field_name))
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{field_name} must be between 0 and 1")
        if not 1 <= int(self.shading_layers) <= 4:
            raise ValueError("shading_layers must be between 1 and 4")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "semantic_role": self.semantic_role,
            "material": self.material,
            "preferred_density": self.preferred_density.value,
            "preferred_frequency": self.preferred_frequency,
            "major_cluster_scale": self.major_cluster_scale,
            "micro_detail_level": self.micro_detail_level,
            "silhouette_priority": self.silhouette_priority,
            "topology_priority": self.topology_priority,
            "shading_strength": self.shading_strength,
            "texture_strength": self.texture_strength,
            "transition_band_emphasis": self.transition_band_emphasis,
            "hierarchy_mode": self.hierarchy_mode.value,
            "major_mass_strength": self.major_mass_strength,
            "medium_cluster_strength": self.medium_cluster_strength,
            "shading_layers": self.shading_layers,
            "depth_cue_strength": self.depth_cue_strength,
            "contact_shadow_strength": self.contact_shadow_strength,
            "highlight_strength": self.highlight_strength,
            "micro_detail_strength": self.micro_detail_strength,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "PixelGrammarProfile":
        return cls(**dict(value))


def default_profile(target: str, semantic_role: str, material: str) -> PixelGrammarProfile:
    """Return a conservative starting point for a target semantic."""
    if semantic_role == "surface":
        density = DensityLevel.BALANCED
        frequency = "low_to_medium"
        cluster = "medium"
        micro = 0.42
        silhouette = 0.12
        topology = 0.0
        shading = 0.30
        texture = 0.58
        transition = 0.0
    elif semantic_role == "network":
        density = DensityLevel.SPARSE if target == "dirt_road" else DensityLevel.BALANCED
        frequency = "medium"
        cluster = "structured_medium"
        micro = 0.35
        silhouette = 0.35
        topology = 0.90
        shading = 0.32
        texture = 0.45
        transition = 0.0
    elif semantic_role == "transition":
        density = DensityLevel.BALANCED
        frequency = "low_to_medium"
        cluster = "medium"
        micro = 0.30
        silhouette = 0.40
        topology = 0.15
        shading = 0.28
        texture = 0.42
        transition = 0.90
    else:
        density = DensityLevel.BALANCED
        frequency = "medium_to_high"
        cluster = "small"
        micro = 0.52
        silhouette = 0.85
        topology = 0.0
        shading = 0.55
        texture = 0.30
        transition = 0.0
    return PixelGrammarProfile(
        name=f"{target}_{density.value}",
        semantic_role=semantic_role,
        material=material,
        preferred_density=density,
        preferred_frequency=frequency,
        major_cluster_scale=cluster,
        micro_detail_level=micro,
        silhouette_priority=silhouette,
        topology_priority=topology,
        shading_strength=shading,
        texture_strength=texture,
        transition_band_emphasis=transition,
    )
