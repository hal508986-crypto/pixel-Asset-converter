# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

- **PNG sequence → transparent GIF** (`pixel-tile compile-character-frames`):
  - Reads a numbered PNG folder in natural order, removes a flat opaque background (colour estimated once from all
    frames and shared; `auto`/`connected`/`global` modes; optional outline `--choke`), then compiles every frame with
    one shared layout (motion-preserving by default) and one shared palette.
  - Saves every stage as separate zero-padded files (`source_frames/`, `keyed_frames/`, `aligned_frames/`,
    `compiled/`, `final_frames/`) and writes `animation.gif` (shared palette, binary transparency, cumulative 10 ms
    rounding so the total time is preserved), which is re-decoded and verified pixel-for-pixel by time position.
  - 512-based by default and configurable from 64 up; the sheet/preview images are skipped instead of failing when the
    sheet would exceed the pixel limit.
  - Python API: `compile_character_frame_directory`, `compile_character_animation_frames`,
    `remove_sequence_background`, `save_animated_gif`.

### Fixed

- Motion-preserving fit no longer rejects a frame as "cut off by 1px" when the fitted scale lands exactly on the
  canvas boundary (floating-point noise such as -1e-13).

### Changed

- `CharacterAnimationResult.report_as_dict()` no longer composes the whole output sheet just to report its size
  (values unchanged).

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
