# Changelog

All notable changes to this project will be documented in this file.

## [0.1.0] - 2026-09-17

Initial public review release.

> **Notice:** This is a review version. Interfaces and behavior are experimental and may change in future releases.

### Added

- **Single-image Pixel-art Asset Conversion**:
  - Deterministic processing for converting high-resolution images into pixel-art assets.
  - Support for 64x64 and 128x128 canvas sizes, custom aspect ratio scaling, and canvas extension up to 1280.
- **MAP & Tile Compilation**:
  - Context-aware compilation for high-resolution MAP images into 64x64 tiles.
  - Multi-variant compilation (A/B/C comparisons) and layout reassembly.
- **Palette Control & Shared Palette**:
  - Shared palette constraints across multiple tiles and animation frames.
  - Configurable palette budgets, color ramp generation, and interactive palette editing.
- **Character Animation Processing**:
  - Character animation sheet splitting (uniform grid, hybrid, and component-based modes).
  - Frame normalization, origin alignment, scale mode selection, and motion preservation.
- **Tileset Processing from Material Exemplar**:
  - Generation of tilesets, edge contracts, and comparison MAPs from material exemplars.
- **Interfaces**:
  - Command Line Interface (`pixel-tile` / `python -m pixel_tile_compiler`) covering all compiler workflows.
  - Optional PySide6 desktop GUI (`pixel-tile gui` or `start_gui.bat`) with Japanese interface.
- **Outputs & Diagnostics**:
  - Structured outputs including final RGBA images, intermediate representation (`ir.json`), metadata (`metadata.json`), metrics, dot preview inspection images, and baseline comparisons.
- **Deterministic Pipeline & Audit Gates**:
  - Deterministic image processing ensuring identical outputs across runs.
  - Comprehensive unit and regression test suite.
  - Automated third-party license audit, asset inventory tracking, and CI reproducibility gate.
