"""Safe image loading and RGBA normalization."""

from pathlib import Path

from PIL import Image, UnidentifiedImageError


def load_image(source: Path) -> Image.Image:
    """Load a supported image without modifying the source file."""
    if not source.exists():
        raise FileNotFoundError(f"入力画像が見つかりません: {source}")
    try:
        with Image.open(source) as image:
            return image.convert("RGBA")
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError(f"画像を読み込めません: {source}") from exc
