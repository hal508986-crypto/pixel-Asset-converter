"""Generation-first orchestration around the adapter boundary."""

from __future__ import annotations

from pathlib import Path

from pixel_tile_compiler.asset.pipeline import AssetPackageResult, process_generated_sheet

from .adapter import GeneratedImage, ImageGenerationAdapter
from .request_compiler import GenerationRequestCompiler
from .spec import TilesetSpec


class GenerationFirstPipeline:
    """Generate one raw sheet through an adapter, then package it deterministically."""

    def run(
        self,
        spec: TilesetSpec,
        output_root: Path,
        adapter: ImageGenerationAdapter,
    ) -> AssetPackageResult:
        request = GenerationRequestCompiler().compile(spec)
        generated: GeneratedImage = adapter.generate(request, Path(output_root) / "generation")
        return process_generated_sheet(spec, generated.raw_path, output_root, generated=generated)
