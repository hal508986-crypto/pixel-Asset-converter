"""IR validation helpers."""

from .schema import TileIR


def validate_tile_ir(ir: TileIR) -> TileIR:
    """Revalidate a Tile IR instance at an explicit pipeline boundary."""
    return TileIR.model_validate(ir.model_dump())
