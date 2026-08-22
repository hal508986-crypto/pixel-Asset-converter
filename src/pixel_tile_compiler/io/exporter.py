"""PNG and JSON artifact writers."""

import json
from pathlib import Path
from typing import Any

from PIL import Image


def save_png(image: Image.Image, path: Path) -> Path:
    """Save an image as RGBA PNG, creating its parent directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGBA").save(path, format="PNG", optimize=False)
    return path


def save_json(value: Any, path: Path) -> Path:
    """Save UTF-8 JSON with stable key ordering."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return path
