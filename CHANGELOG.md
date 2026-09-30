# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

- `compile-character-frames --character-height PX` (with `--height-reference median|first|N`): measures the character body from the
  alpha channel (thin parts such as spears are removed by a morphological opening before taking the largest component) and
  sets the scale so the body is PX tall. Scale and feet are fixed for every frame, so pose changes never alter the character's
  size, and one value keeps different actions of a character at the same size.
- `--foot-lock`: vertical-only per-frame translation that puts each frame's body bottom on the reference ground line (for sources
  whose ground line drifts during a lunge); scale and pose height are untouched; off by default because it also cancels real jumps.
- `--canvas-auto`: the tightest canvas (2 px margin) that holds every frame at that scale — no clipping, minimal margins.
- `trimmed_frames/` + `trim_manifest.json` (`--no-trim` disables): each final frame cropped to its visible box with offsets from the
  pivot (frame top-left = pivot + `offset_from_pivot`); reproduces the canvas frames pixel-for-pixel.
- GUI: a "キャラの大きさ（フレーミング）" group (whole-frame fit / top-N% fit / character height with automatic canvas) and the
  trimmed-output option.

- `compile-character-frames --fit-percentile P` (default 100): size the character so that P% of frames fit the canvas and allow the
  remaining extreme frames (e.g. a long spear thrust) to be clipped, which enlarges the character when the source has large
  margins. Clipped frames are listed as warnings and as `clipped` in `bbox_report.json`. The horizontal origin becomes the median
  of the per-frame centres so that a long weapon does not drag the character off-centre. `100` keeps the previous behaviour.

- **PNG sequence → transparent GIF** (`pixel-tile compile-character-frames`):
  - Reads a numbered PNG folder in natural order, removes a flat opaque background (colour estimated once from all
    frames and shared; `auto`/`connected`/`global` modes; optional outline `--choke`), then compiles every frame with
    one shared layout (motion-preserving by default) and one shared palette.
  - Saves every stage as separate zero-padded files (`source_frames/`, `keyed_frames/`, `aligned_frames/`,
    `compiled/`, `final_frames/`) and writes `animation.gif` (shared palette, binary transparency, cumulative 10 ms
    rounding so the total time is preserved), which is re-decoded and verified pixel-for-pixel by time position.
  - 512-based by default and configurable from 64 up; the sheet/preview images are skipped instead of failing when the
    sheet would exceed the pixel limit.
  - Temporal colour stabilisation (`--stabilize-margin`, default 12, `0` disables): palette hysteresis that suppresses
    colour flicker at palette boundaries without changing silhouettes or the palette. `compiled/` keeps the raw output.
  - `scripts/evaluate_frame_sequence.py` measures temporal stability per stage (silhouette change, A→B→A toggle rate,
    near-static pixel flip rate).
  - GUI: a **連番PNG→GIF** window (button shown for the Character / Character Animation purposes) with background
    estimation preview, all settings, progress, cancel (existing output is never corrupted) and a checkerboard playback
    preview. CLI and GUI share `pixelizer/frame_sequence_job.py` (validation, defaults, run, summary).
  - Python API: `compile_character_frame_directory`, `compile_character_animation_frames`,
    `remove_sequence_background`, `save_animated_gif`, `stabilize_palette_flicker`.

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
