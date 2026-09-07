"""Pure canvas interaction state, independently testable without a display."""

from dataclasses import dataclass


ZOOMS = (1, 2, 4, 8, 16, 32)


@dataclass
class CanvasState:
    """Integer-zoom and coordinate calculations for a logical canvas."""

    zoom: int = 8
    canvas_size: tuple[int, int] = (64, 64)

    def __post_init__(self) -> None:
        if self.zoom not in ZOOMS:
            raise ValueError("zoom must be one of 1, 2, 4, 8, 16, 32")
        self.set_canvas_size(self.canvas_size)

    @property
    def grid_spacing(self) -> int:
        return self.zoom

    def pixel_at(self, view_x: int, view_y: int) -> tuple[int, int]:
        width, height = self.canvas_size
        return (
            min(width - 1, max(0, view_x // self.zoom)),
            min(height - 1, max(0, view_y // self.zoom)),
        )

    def set_canvas_size(self, canvas_size: tuple[int, int]) -> tuple[int, int]:
        width, height = int(canvas_size[0]), int(canvas_size[1])
        if width < 1 or height < 1:
            raise ValueError("canvas dimensions must be positive")
        self.canvas_size = (width, height)
        return self.canvas_size

    def zoom_in(self) -> int:
        index = min(len(ZOOMS) - 1, ZOOMS.index(self.zoom) + 1)
        self.zoom = ZOOMS[index]
        return self.zoom

    def zoom_out(self) -> int:
        index = max(0, ZOOMS.index(self.zoom) - 1)
        self.zoom = ZOOMS[index]
        return self.zoom
