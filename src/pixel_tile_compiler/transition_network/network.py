"""Structure-first dirt-road network tile compiler."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image

from pixel_tile_compiler.config import CompilerConfig
from pixel_tile_compiler.io.exporter import save_json, save_png
from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler

from .contracts import EdgeSemanticProfile, SemanticEdgeContract, SemanticTile, SemanticTileSpec
from .graph import topology_sides
from .masks import ROAD_TOPOLOGIES, build_road_mask


@dataclass
class NetworkTileConfig:
    output_root: Path = field(default_factory=lambda: Path("e2e/transition_network_study/families/network/dirt_road"))
    source_size: int = 512
    variants: int = 3
    palette_budget: int = 24
    pixelize: bool = True
    debug_enabled: bool = True
    seed: int = 42
    topologies: tuple[str, ...] = ROAD_TOPOLOGIES
    width_ratio: float | None = None
    center: float = 0.5

    def __post_init__(self) -> None:
        self.output_root = Path(self.output_root)
        if self.source_size < 64:
            raise ValueError("source_size must be at least 64")
        if self.variants < 1:
            raise ValueError("variants must be positive")
        if not 4 <= self.palette_budget <= 32:
            raise ValueError("palette_budget must be between 4 and 32")
        if not self.topologies or any(item not in ROAD_TOPOLOGIES for item in self.topologies):
            raise ValueError("topologies must be drawn from the supported road topology set")
        if self.width_ratio is not None and not 0.05 <= self.width_ratio <= 0.9:
            raise ValueError("width_ratio must be between 0.05 and 0.9")
        if not 0.2 <= self.center <= 0.8:
            raise ValueError("center must be between 0.2 and 0.8")


@dataclass(frozen=True)
class NetworkBuildResult:
    family_id: str
    output_root: Path
    tiles: tuple[SemanticTile, ...]


class NetworkTileCompiler:
    """Create road masks first, then composite dirt material and pixelize."""

    def __init__(self, config: NetworkTileConfig) -> None:
        self.config = config

    def build(
        self,
        base_source: Image.Image,
        road_source: Image.Image,
        base_material: str = "grass",
        family_id: str = "dirt_road",
    ) -> NetworkBuildResult:
        root = self.config.output_root
        root.mkdir(parents=True, exist_ok=True)
        base = _fit(base_source, self.config.source_size)
        road = _fit(road_source, self.config.source_size)
        tiles: list[SemanticTile] = []
        for topology in self.config.topologies:
            for variant in range(self.config.variants):
                width, center = (
                    (self.config.width_ratio, self.config.center)
                    if self.config.width_ratio is not None
                    else _variant_geometry(variant, self.config.variants)
                )
                mask = build_road_mask((self.config.source_size, self.config.source_size), topology, width=width, center=center)
                source_image = Image.composite(road, base, Image.fromarray(mask.astype(np.uint8) * 255, mode="L"))
                tile_id = f"{topology.lower()}_v{variant:02d}"
                spec = SemanticTileSpec(
                    tile_id=tile_id,
                    family=family_id,
                    topology=topology,
                    variant=variant,
                    semantic_contract=_network_contract(base_material, topology, center, width),
                    metadata={"road_width": width, "road_center": center, "mask_type": "structure_first"},
                )
                pixel_image = self._pixelize(source_image, root / "pixel_artifacts" / tile_id, tile_id) if self.config.pixelize else None
                tile = SemanticTile(spec, source_image, pixel_image)
                save_png(source_image, root / "source_tiles" / f"{tile_id}.png")
                save_json(spec.as_dict(), root / "source_tiles" / f"{tile_id}.json")
                if pixel_image is not None:
                    save_png(pixel_image, root / "pixel_tiles" / f"{tile_id}.png")
                save_png(Image.fromarray(mask.astype(np.uint8) * 255, mode="L"), root / "masks" / f"{tile_id}.png")
                tiles.append(tile)
        manifest = {
            "family_id": family_id,
            "kind": "network",
            "base_material": base_material,
            "config": _config_dict(self.config),
            "tiles": [tile.spec.as_dict() for tile in tiles],
        }
        save_json(manifest, root / "manifest.json")
        return NetworkBuildResult(family_id, root, tuple(tiles))

    def _pixelize(self, image: Image.Image, output_root: Path, source_name: str) -> Image.Image:
        result = PixelTileCompiler().compile_image(
            image,
            CompilerConfig(
                output_root=output_root,
                palette_budget=self.config.palette_budget,
                tile_mode="directional",
                seed=self.config.seed,
                debug_enabled=self.config.debug_enabled,
            ),
            source_name=source_name,
        )
        return Image.open(result.final_path).convert("RGBA").copy()


def _fit(image: Image.Image, size: int) -> Image.Image:
    return image.convert("RGBA").resize((size, size), Image.Resampling.BICUBIC)


def _variant_geometry(variant: int, count: int) -> tuple[float, float]:
    if count == 1:
        return 0.32, 0.5
    positions = np.linspace(0.26, 0.38, count)
    centers = np.linspace(0.46, 0.54, count)
    return float(positions[variant]), float(centers[variant])


def _network_contract(base_material: str, topology: str, center: float, width: float) -> SemanticEdgeContract:
    base = EdgeSemanticProfile(role="surface", material=base_material)
    road = lambda side: EdgeSemanticProfile(
        role="network_connector",
        material="dirt_road",
        feature_type="road",
        feature_center=center,
        feature_width=width,
        orientation=topology,
        connects_to=(side,),
    )
    profiles = {"north": base, "east": base, "south": base, "west": base}
    for side in _topology_sides(topology):
        profiles[side] = road(side)
    return SemanticEdgeContract(
        north=profiles["north"],
        east=profiles["east"],
        south=profiles["south"],
        west=profiles["west"],
        orientation=topology,
    )


def _topology_sides(topology: str) -> tuple[str, ...]:
    return tuple({"N": "north", "E": "east", "S": "south", "W": "west"}[side] for side in topology_sides(topology))


def _config_dict(config: NetworkTileConfig) -> dict[str, object]:
    values = asdict(config)
    values["output_root"] = str(config.output_root)
    return values
