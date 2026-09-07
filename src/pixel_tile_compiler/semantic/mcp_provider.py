"""Optional MCP-compatible semantic adapter with strict fallback boundary."""

import json
from typing import Any, Callable

from PIL import Image

from pixel_tile_compiler.analysis.structural import StructuralAnalysis
from pixel_tile_compiler.config import CompilerConfig

from .base import SemanticAnalysis, SemanticProviderError, SemanticRegion
from .prompts import build_prompt


class McpSemanticProvider:
    """Adapt an injected callable; transport details stay outside the compiler core."""

    def __init__(self, callback: Callable[..., Any]) -> None:
        self.callback = callback

    def analyze(
        self,
        image: Image.Image,
        structural_data: StructuralAnalysis,
        config: CompilerConfig,
    ) -> SemanticAnalysis:
        try:
            payload = self.callback(
                image=image,
                structural_data=structural_data,
                prompt=build_prompt(config.tile_mode, config.palette_budget, config.canvas.size),
            )
            if isinstance(payload, str):
                payload = json.loads(payload)
            return self._validate(payload, config)
        except Exception as exc:
            raise SemanticProviderError(f"MCP semantic analysis failed: {exc}") from exc

    @staticmethod
    def _validate(payload: Any, config: CompilerConfig) -> SemanticAnalysis:
        if not isinstance(payload, dict) or not isinstance(payload.get("regions"), list):
            raise ValueError("semantic response must contain a regions array")
        regions: list[SemanticRegion] = []
        for item in payload["regions"]:
            if not isinstance(item, dict):
                raise ValueError("semantic region must be an object")
            regions.append(
                SemanticRegion(
                    id=int(item["region_id"]),
                    semantic=str(item.get("semantic", "unknown")),
                    importance=max(0.0, min(1.0, float(item.get("importance", 0.5)))),
                    preserve_edges=True,
                    preserve_texture=True,
                    texture_priority=0.5,
                    merge_candidates=tuple(int(value) for value in item.get("merge_with", [])),
                    discard_micro_detail=True,
                )
            )
        global_data = payload.get("global", {})
        return SemanticAnalysis(
            tile_type=str(payload.get("tile_type", "unknown")),
            tile_mode=str(payload.get("tile_mode", config.tile_mode)),
            texture_density=float(global_data.get("texture_density", 0.45)),
            contrast_strength=float(global_data.get("contrast_priority", 0.5)),
            edge_strength=float(global_data.get("edge_priority", 0.35)),
            regions=tuple(regions),
            provider_name="mcp",
        )
