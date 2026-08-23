"""Renderer boundary shared by semantic network implementations."""

from __future__ import annotations

from typing import Protocol

from PIL import Image

from .contracts import SemanticTile
from .graph import NetworkTopology


class NetworkRenderer(Protocol):
    def render_tile(
        self,
        base_source: Image.Image,
        water_source: Image.Image,
        bank_source: Image.Image,
        base_material: str,
        topology: NetworkTopology,
        incoming: tuple[str, ...],
        outgoing: tuple[str, ...],
        variant: int = 0,
    ) -> SemanticTile:
        ...
