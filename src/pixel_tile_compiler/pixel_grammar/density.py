"""Deterministic density transforms applied before the existing Pixel Compiler."""

from __future__ import annotations

from PIL import Image, ImageEnhance, ImageFilter

from .profiles import DensityLevel, PixelGrammarProfile


def apply_density(
    image: Image.Image,
    level: DensityLevel | str,
    profile: PixelGrammarProfile | None = None,
    seed: int = 42,
) -> Image.Image:
    """Adjust visible detail without changing the semantic geometry layer.

    ``seed`` is part of the API for future material perturbation. The current
    transforms are intentionally seed-independent so the study remains easy to
    reproduce and attributes differences to grammar level rather than noise.
    """
    del seed
    density = DensityLevel(level)
    output = image.convert("RGBA")
    if density is DensityLevel.SPARSE:
        output = output.filter(ImageFilter.GaussianBlur(radius=0.75))
        output = ImageEnhance.Contrast(output).enhance(0.94)
        output = ImageEnhance.Color(output).enhance(0.96)
    elif density is DensityLevel.DETAILED:
        output = ImageEnhance.Contrast(output).enhance(1.06)
        output = ImageEnhance.Sharpness(output).enhance(1.8)
        output = output.filter(ImageFilter.UnsharpMask(radius=1.2, percent=110, threshold=3))
    else:
        output = ImageEnhance.Sharpness(output).enhance(1.05)
    if profile is not None:
        texture = max(0.0, min(1.0, float(profile.texture_strength)))
        if density is DensityLevel.SPARSE:
            output = ImageEnhance.Contrast(output).enhance(0.98 + 0.03 * texture)
        elif density is DensityLevel.DETAILED:
            output = ImageEnhance.Sharpness(output).enhance(1.0 + 0.35 * texture)
    return output


def compiler_grammar_options(level: DensityLevel | str) -> dict[str, object]:
    """Map the study levels to existing repeatability controls."""
    density = DensityLevel(level)
    if density is DensityLevel.SPARSE:
        return {
            "repeat_opt_strength": 0.72,
            "center_suppression_strength": 0.55,
            "smoothing_enabled": True,
        }
    if density is DensityLevel.DETAILED:
        return {
            "repeat_opt_strength": 0.28,
            "center_suppression_strength": 0.24,
            "smoothing_enabled": False,
        }
    return {
        "repeat_opt_strength": 0.50,
        "center_suppression_strength": 0.40,
        "smoothing_enabled": True,
    }
