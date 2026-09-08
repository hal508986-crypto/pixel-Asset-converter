from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "generate_licenses.py"
SPEC = importlib.util.spec_from_file_location("generate_licenses", MODULE_PATH)
assert SPEC and SPEC.loader
license_audit = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(license_audit)


def test_classification_distinguishes_allowed_conditional_blocked_and_unknown() -> None:
    assert license_audit.classify_license("MIT") == "allowed"
    assert license_audit.classify_license("MIT-CMU", {"MIT-CMU"}) == "allowed"
    assert license_audit.classify_license("LGPL-3.0-only") == "conditional"
    assert license_audit.classify_license("GPL-3.0-only") == "blocked"
    assert license_audit.classify_license("Project-specific terms") == "needs_review"


def test_dependency_closure_ignores_unrelated_global_packages() -> None:
    audit = license_audit.build_audit(Path(__file__).resolve().parents[1])
    names = {item["normalized_name"] for item in audit["packages"]}

    assert "pixel-tile-compiler" not in names
    assert "requests" not in names
    assert {"pillow", "numpy", "opencv-python", "scikit-image", "pydantic", "typer"} <= names


def test_main_license_expression_wins_over_bundled_component_text() -> None:
    documents = [{"path": "LICENSE-3RD-PARTY.txt", "sha256": "", "text": "GNU GENERAL PUBLIC LICENSE"}]

    assert license_audit._infer_spdx("opencv-python", "Apache 2.0", documents, {}) == "Apache-2.0"


def test_license_audit_normalizes_new_gcc_exception_spdx_wording() -> None:
    assert license_audit._spdx_ids("GPL-3.0-or-later WITH GCC-exception-3.1") == [
        "GPL-3.0-with-GCC-exception"
    ]


def test_package_license_overrides_win_over_version_specific_metadata() -> None:
    policy = {"package_spdx_overrides": {"numpy": "BSD-3-Clause"}}

    assert (
        license_audit._infer_spdx(
            "numpy",
            "BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0",
            [],
            policy,
            metadata_expression="BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0",
        )
        == "BSD-3-Clause"
    )


def test_audit_keeps_bundled_component_licenses_visible() -> None:
    audit = license_audit.build_audit(Path(__file__).resolve().parents[1])
    numpy = next(item for item in audit["packages"] if item["normalized_name"] == "numpy")

    assert "GPL-3.0-with-GCC-exception" in numpy["bundled_license_ids"]

    text = license_audit._render_third_party_text(audit)
    assert "GPL-3.0-with-GCC-exception" in text


def test_bundled_notices_do_not_replace_distribution_license() -> None:
    audit = license_audit.build_audit(Path(__file__).resolve().parents[1])
    packages = {item["normalized_name"]: item for item in audit["packages"]}

    assert packages["numpy"]["spdx"] == "BSD-3-Clause"
    assert packages["scipy"]["spdx"] == "BSD-3-Clause"
    assert packages["scikit-image"]["spdx"] == "BSD-3-Clause"
    assert packages["opencv-python"]["spdx"] == "Apache-2.0"
    assert packages["typing-extensions"]["status"] == "allowed"


def test_asset_inventory_excludes_gitignored_generated_surfaces() -> None:
    inventory = license_audit.build_asset_inventory(Path(__file__).resolve().parents[1])

    assert inventory["tracked_files"] > 0
    assert "assets" not in inventory["by_root"]
    assert "e2e" not in inventory["by_root"]
    assert "experiment" not in inventory["by_root"]
    assert "material_library" not in inventory["by_root"]
    assert inventory["by_root"]["experiments"]["json"] > 0
    assert inventory["provenance_flags"]["image_2_mentions"] > 0


def test_rendered_outputs_include_a_generated_root_notice() -> None:
    audit = license_audit.build_audit(Path(__file__).resolve().parents[1])
    outputs = license_audit.render_outputs(Path(__file__).resolve().parents[1], audit)

    notice_path = str(Path(__file__).resolve().parents[1] / "NOTICE.txt")
    assert notice_path in outputs
    assert "LICENSE/third-party-licenses.txt" in outputs[notice_path]


@pytest.mark.license_gate
def test_committed_license_outputs_are_reproducible_in_the_pinned_environment() -> None:
    root = Path(__file__).resolve().parents[1]
    audit = license_audit.build_audit(root)

    assert license_audit._compare_outputs(license_audit.render_outputs(root, audit)) == []


def test_code_inventory_detects_unlisted_external_imports() -> None:
    audit = license_audit.build_audit(Path(__file__).resolve().parents[1])

    assert "yaml" not in audit["code_inventory"]["undeclared_external_imports"]
    assert audit["code_inventory"]["undeclared_external_imports"] == []
    assert "a" not in audit["code_inventory"]["import_roots"]
