"""Material and surface-to-network transition tile compiler."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path

from PIL import Image
from pixel_tile_compiler.config import CompilerConfig
from pixel_tile_compiler.io.exporter import save_json, save_png
from pixel_tile_compiler.pipeline.compiler import PixelTileCompiler

from .contracts import EdgeSemanticProfile, SemanticEdgeContract, SemanticTile, SemanticTileSpec
from .masks import build_transition_mask


@dataclass
class TransitionTileConfig:
    output_root: Path = field(default_factory=lambda: Path("e2e/transition_network_study/families/transition"))
    source_size: int = 512
    variants: int = 3
    palette_budget: int = 24
    pixelize: bool = True
    debug_enabled: bool = True
    seed: int = 42

    def __post_init__(self) -> None:
        self.output_root = Path(self.output_root)
        if self.source_size < 64:
            raise ValueError("source_size must be at least 64")
        if self.variants < 1:
            raise ValueError("variants must be positive")
        if not 4 <= self.palette_budget <= 32:
            raise ValueError("palette_budget must be between 4 and 32")


@dataclass(frozen=True)
class TransitionBuildResult:
    family_id: str
    output_root: Path
    tiles: tuple[SemanticTile, ...]


class TransitionTileCompiler:
    """Build ordered material boundaries with explicit outside edge profiles."""

    def __init__(self, config: TransitionTileConfig) -> None:
        self.config = config

    def build_pair(
        self,
        source_a: Image.Image,
        source_b: Image.Image,
        material_a: str,
        material_b: str,
        family_id: str,
    ) -> TransitionBuildResult:
        root = self.config.output_root / family_id
        root.mkdir(parents=True, exist_ok=True)
        first_image = _fit(source_a, self.config.source_size)
        second_image = _fit(source_b, self.config.source_size)
        tiles: list[SemanticTile] = []
        ordered = ((material_a, first_image, material_b, second_image), (material_b, second_image, material_a, first_image))
        validation_reports: list[dict[str, object]] = []
        for first, first_material_image, second, second_material_image in ordered:
            for orientation in ("NS", "EW"):
                for variant in range(self.config.variants):
                    boundary = _variant_boundary(variant, self.config.variants)
                    mask = build_transition_mask((self.config.source_size, self.config.source_size), orientation, boundary)
                    source_image = Image.composite(
                        first_material_image,
                        second_material_image,
                        Image.fromarray(mask.astype("uint8") * 255, mode="L"),
                    )
                    tile_id = f"{first}_to_{second}_{orientation.lower()}_v{variant:02d}"
                    spec = SemanticTileSpec(
                        tile_id=tile_id,
                        family=family_id,
                        topology=orientation,
                        material_a=first,
                        material_b=second,
                        variant=variant,
                        semantic_contract=_transition_contract(first, second, orientation),
                        metadata={"boundary": boundary, "mask_type": "material_boundary"},
                    )
                    pixel_image = self._pixelize(source_image, root / "pixel_artifacts" / tile_id, tile_id) if self.config.pixelize else None
                    tile = SemanticTile(spec, source_image, pixel_image)
                    save_png(source_image, root / "source_tiles" / f"{tile_id}.png")
                    save_json(spec.as_dict(), root / "source_tiles" / f"{tile_id}.json")
                    save_png(Image.fromarray(mask.astype("uint8") * 255, mode="L"), root / "masks" / f"{tile_id}.png")
                    if pixel_image is not None:
                        save_png(pixel_image, root / "pixel_tiles" / f"{tile_id}.png")
                        from pixel_tile_compiler.asset.acceptance import validate_masked_transition_tile

                        validation = validate_masked_transition_tile(
                            source_image,
                            pixel_image,
                            mask,
                            orientation,
                            palette_budget=self.config.palette_budget,
                        )
                        validation_reports.append({"tile_id": tile_id, **validation})
                        save_json(validation, root / "validation" / f"{tile_id}.json")
                    tiles.append(tile)
        save_json(
            {
                "family_id": family_id,
                "kind": "transition",
                "materials": [material_a, material_b],
                "config": _config_dict(self.config),
                "tiles": [tile.spec.as_dict() for tile in tiles],
            },
            root / "manifest.json",
        )
        if validation_reports:
            save_json(
                {
                    "status": "accepted" if all(report["status"] == "accepted" for report in validation_reports) else "rejected",
                    "adoption_status": "provisional_not_approved",
                    "tiles": validation_reports,
                },
                root / "validation" / "report.json",
            )
        return TransitionBuildResult(family_id, root, tuple(tiles))

    def build_network_handoff(
        self,
        base_source: Image.Image,
        road_source: Image.Image,
        base_material: str,
    ) -> TransitionBuildResult:
        family_id = "forest_road" if base_material == "forest_canopy" else f"{base_material}_road"
        return self.build_pair(base_source, road_source, base_material, "dirt_road", family_id)

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


def _variant_boundary(variant: int, count: int) -> float:
    if count == 1:
        return 0.5
    return 0.44 + 0.12 * variant / max(1, count - 1)


def _transition_contract(first: str, second: str, orientation: str) -> SemanticEdgeContract:
    first_edge = EdgeSemanticProfile(role="surface", material=first)
    second_edge = EdgeSemanticProfile(role="surface", material=second)
    boundary = EdgeSemanticProfile(
        role="transition_boundary",
        material=first,
        materials=(first, second),
        feature_type="material_boundary",
        orientation=orientation,
    )
    if orientation == "NS":
        return SemanticEdgeContract(first_edge, boundary, second_edge, boundary, orientation=orientation)
    return SemanticEdgeContract(boundary, second_edge, boundary, first_edge, orientation=orientation)


def _config_dict(config: TransitionTileConfig) -> dict[str, object]:
    values = asdict(config)
    values["output_root"] = str(config.output_root)
    return values
