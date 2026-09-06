"""Image generation adapter boundary and immutable provenance contract."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import shutil
from typing import Callable, Protocol

from .spec import GenerationRequest


class GenerationUnavailableError(RuntimeError):
    """Raised when a generation-first command has no configured image generator."""


@dataclass(frozen=True)
class GeneratedImage:
    raw_path: Path
    generator: str
    model: str | None
    generated_at_utc: str
    raw_dimensions: tuple[int, int]
    raw_sha256: str
    metadata: dict[str, object] = field(default_factory=dict)

    @classmethod
    def from_path(
        cls,
        path: Path,
        generator: str,
        model: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> "GeneratedImage":
        from PIL import Image

        path = Path(path)
        with Image.open(path) as image:
            dimensions = tuple(int(value) for value in image.size)
        return cls(
            raw_path=path,
            generator=generator,
            model=model,
            generated_at_utc=datetime.now(timezone.utc).isoformat(),
            raw_dimensions=(dimensions[0], dimensions[1]),
            raw_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            metadata=dict(metadata or {}),
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "raw_path": str(self.raw_path),
            "generator": self.generator,
            "model": self.model,
            "generated_at_utc": self.generated_at_utc,
            "raw_dimensions": list(self.raw_dimensions),
            "raw_sha256": self.raw_sha256,
            "metadata": dict(self.metadata),
        }


class ImageGenerationAdapter(Protocol):
    def generate(self, request: GenerationRequest, output_root: Path) -> GeneratedImage:
        """Generate one raw sheet and return its immutable provenance."""


class McpImageGenerationAdapter:
    """Thin MCP boundary with an injected tool callback.

    The callback is intentionally the only integration point. A desktop/MCP
    host can provide the actual tool call without making the core compiler
    import a transport-specific client or fabricate a fallback image.
    """

    def __init__(
        self,
        generate_fn: Callable[[GenerationRequest, Path], str | Path | GeneratedImage],
        *,
        generator: str = "mcp",
        model: str | None = None,
    ) -> None:
        self._generate_fn = generate_fn
        self._generator = generator
        self._model = model

    def generate(self, request: GenerationRequest, output_root: Path) -> GeneratedImage:
        output_root = Path(output_root)
        output_root.mkdir(parents=True, exist_ok=True)
        result = self._generate_fn(request, output_root)
        if isinstance(result, GeneratedImage):
            return result
        source = Path(result)
        if not source.is_file():
            raise GenerationUnavailableError(f"MCP adapter returned a missing image: {source}")
        target = output_root / "sheet_raw.png"
        shutil.copy2(source, target)
        return GeneratedImage.from_path(target, generator=self._generator, model=self._model)


class UnconfiguredImageGenerationAdapter:
    """Explicit Phase-1 placeholder; it never fabricates a generated image."""

    def generate(self, request: GenerationRequest, output_root: Path) -> GeneratedImage:
        del request, output_root
        raise GenerationUnavailableError(
            "No MCP image generation adapter is configured. Use process-generated-sheet with an existing PNG."
        )
