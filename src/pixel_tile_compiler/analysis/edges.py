"""Canny edge extraction."""

import cv2
import numpy as np
from PIL import Image


def detect_edges(image: Image.Image) -> Image.Image:
    """Return a grayscale Canny edge image."""
    rgb = np.asarray(image.convert("RGB"))
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(gray, 70, 150)
    return Image.fromarray(edges, mode="L")
