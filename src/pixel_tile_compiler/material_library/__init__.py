"""Material Source Library v0.1 contracts and deterministic study runner."""

from .config import MaterialLibraryStudyConfig, load_material_library_config
from .models import MaterialFamily, MaterialSourceCard, SourceGateReport
from .resolver import MaterialLibrary
from .real_qualification import (
    RealMaterialQualificationConfig,
    RealMaterialQualificationResult,
    RealMaterialQualificationRunner,
    RealT2IUnavailable,
    analyze_source_dominance,
    cross_material_analysis,
    create_blind_review,
    import_material_review,
    import_real_material_sources,
    load_real_qualification_config,
)
from .runner import MaterialLibraryRunner

__all__ = [
    "MaterialFamily",
    "MaterialLibrary",
    "MaterialLibraryRunner",
    "MaterialLibraryStudyConfig",
    "MaterialSourceCard",
    "RealMaterialQualificationConfig",
    "RealMaterialQualificationResult",
    "RealMaterialQualificationRunner",
    "RealT2IUnavailable",
    "SourceGateReport",
    "analyze_source_dominance",
    "cross_material_analysis",
    "create_blind_review",
    "import_material_review",
    "import_real_material_sources",
    "load_material_library_config",
    "load_real_qualification_config",
]
