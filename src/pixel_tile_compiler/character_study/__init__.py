"""Character palette budget and detail density studies."""

from .config import CharacterPaletteDensityStudyConfig, load_character_study_config
from .native_resolution import (
    NativeResolutionStudyConfig,
    NativeResolutionStudyResult,
    NativeResolutionStudyRunner,
    load_native_resolution_study_config,
)
from .runner import CharacterPaletteDensityStudyResult, CharacterPaletteDensityStudyRunner

__all__ = [
    "CharacterPaletteDensityStudyConfig",
    "CharacterPaletteDensityStudyResult",
    "CharacterPaletteDensityStudyRunner",
    "load_character_study_config",
    "NativeResolutionStudyConfig",
    "NativeResolutionStudyResult",
    "NativeResolutionStudyRunner",
    "load_native_resolution_study_config",
]
