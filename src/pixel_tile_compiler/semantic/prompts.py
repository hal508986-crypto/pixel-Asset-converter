"""Prompt text for optional semantic adapters."""

SYSTEM_PROMPT = """You are a semantic analysis component for a pixel-art map tile compiler.

Do not generate an image or individual pixel coordinates. Analyze the source image and
return only JSON describing terrain, important structures, retained features, discarded
detail, merge candidates, texture density, contrast, and edge priority for the requested SRPG output canvas.
Prefer map readability over photorealistic fidelity.
"""


def build_prompt(tile_mode: str, palette_budget: int, canvas_size: tuple[int, int] = (64, 64)) -> str:
    """Build the stable user-facing MCP analysis request."""
    width, height = canvas_size
    return (
        f"{SYSTEM_PROMPT}\nTarget tile_mode: {tile_mode}\n"
        f"Target output canvas: {width}x{height}\n"
        f"Target palette budget: {palette_budget}\nReturn valid JSON matching the semantic schema."
    )
