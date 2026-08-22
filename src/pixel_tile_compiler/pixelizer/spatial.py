"""Region-aware reduction into a fixed 64x64 raster."""

from collections import Counter

import numpy as np
from PIL import Image

from pixel_tile_compiler.ir.schema import TileIR


def region_aware_pixelize(
    source: Image.Image,
    region_map: np.ndarray,
    ir: TileIR,
) -> Image.Image:
    """Choose one important region per output cell instead of averaging pixels."""
    rgba = np.asarray(source.convert("RGBA"))
    region_map = np.asarray(region_map, dtype=np.int32)
    height, width = region_map.shape
    output = np.zeros((ir.height, ir.width, 4), dtype=np.uint8)
    region_by_id = {region.id: region for region in ir.regions}
    for out_y in range(ir.height):
        y0 = (out_y * height) // ir.height
        y1 = max(y0 + 1, ((out_y + 1) * height) // ir.height)
        for out_x in range(ir.width):
            x0 = (out_x * width) // ir.width
            x1 = max(x0 + 1, ((out_x + 1) * width) // ir.width)
            cell_regions = region_map[y0:y1, x0:x1]
            candidates = Counter(int(value) for value in cell_regions.ravel())
            best_id = None
            best_score = -1.0
            for region_id, coverage in candidates.items():
                region = region_by_id.get(region_id)
                if region is None:
                    continue
                edge_weight = 1.25 if region.preserve_edges else 1.0
                score = (coverage / max(cell_regions.size, 1)) * region.importance * edge_weight
                if score > best_score:
                    best_id, best_score = region_id, score
            alpha_cell = rgba[y0:y1, x0:x1, 3]
            if alpha_cell.size and int((alpha_cell >= 128).sum()) < alpha_cell.size / 2:
                continue
            if best_id is None:
                continue
            region = region_by_id[best_id]
            base = np.asarray(region.dominant_color, dtype=np.int16)
            if region.preserve_texture:
                # Keep only a low-frequency luminance hint; palette quantization handles the rest.
                source_rgb = rgba[y0:y1, x0:x1, :3].astype(np.float32).mean(axis=(0, 1))
                source_luma = float(source_rgb.mean())
                base_luma = float(base.mean())
                delta = int(max(-12, min(12, round((source_luma - base_luma) * 0.25))))
                base = np.clip(base + delta, 0, 255)
            output[out_y, out_x, :3] = base.astype(np.uint8)
            output[out_y, out_x, 3] = 255
    return Image.fromarray(output, mode="RGBA")
