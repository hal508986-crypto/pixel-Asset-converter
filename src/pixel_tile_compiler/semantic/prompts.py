"""Prompt text for optional semantic adapters."""

SYSTEM_PROMPT = """You are a semantic analysis component for a pixel-art map tile compiler.

Do not generate an image or individual pixel coordinates. Analyze the source image and
return only JSON describing terrain, important structures, retained features, discarded
detail, merge candidates, texture density, contrast, and edge priority for a 64x64 SRPG tile.
Prefer map readability over photorealistic fidelity.
"""


def build_prompt(tile_mode: str, palette_budget: int) -> str:
    """Build the stable user-facing MCP analysis request."""
    return (
        f"{SYSTEM_PROMPT}\nTarget tile_mode: {tile_mode}\n"
        f"Target palette budget: {palette_budget}\nReturn valid JSON matching the semantic schema."
    )
