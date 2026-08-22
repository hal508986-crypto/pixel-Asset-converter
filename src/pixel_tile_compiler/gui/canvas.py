"""Pure canvas interaction state, independently testable without a display."""

from dataclasses import dataclass


ZOOMS = (1, 2, 4, 8, 16, 32)


@dataclass
class CanvasState:
    """Integer-zoom and coordinate calculations for a 64x64 canvas."""

    zoom: int = 8

    def __post_init__(self) -> None:
        if self.zoom not in ZOOMS:
            raise ValueError("zoom must be one of 1, 2, 4, 8, 16, 32")

    @property
    def grid_spacing(self) -> int:
        return self.zoom

    def pixel_at(self, view_x: int, view_y: int) -> tuple[int, int]:
        return max(0, view_x // self.zoom), max(0, view_y // self.zoom)

    def zoom_in(self) -> int:
        index = min(len(ZOOMS) - 1, ZOOMS.index(self.zoom) + 1)
        self.zoom = ZOOMS[index]
        return self.zoom

    def zoom_out(self) -> int:
        index = max(0, ZOOMS.index(self.zoom) - 1)
        self.zoom = ZOOMS[index]
        return self.zoom
