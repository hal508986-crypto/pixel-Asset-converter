"""3x3 nearest-neighbor tile preview."""

from PIL import Image


def make_tiled_preview(tile: Image.Image, count: int = 3) -> Image.Image:
    """Place the tile in a count-by-count preview without interpolation."""
    tile = tile.convert("RGBA")
    preview = Image.new("RGBA", (tile.width * count, tile.height * count), (0, 0, 0, 0))
    for y in range(count):
        for x in range(count):
            preview.paste(tile, (x * tile.width, y * tile.height))
    return preview
