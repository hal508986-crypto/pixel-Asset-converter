"""Generate and check the repository's license inventory.

The scanner deliberately starts at ``pyproject.toml`` instead of inspecting the
whole Python installation.  This keeps unrelated global packages out of the
report and makes a future dependency addition visible in the generated files.
It uses only the Python standard library and the installed distribution
metadata, so adding a license tool does not add another dependency to the
project.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import hashlib
import importlib.metadata as metadata
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Iterable


DEFAULT_ALLOWED_SPDX = {
    "0BSD",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "CC0-1.0",
    "HPND",
    "ISC",
    "MIT",
    "MIT-CMU",
    "MPL-2.0",
    "PSF-2.0",
    "Unicode-3.0",
    "Zlib",
}
CONDITIONAL_SPDX = {
    "LGPL-2.1-only",
    "LGPL-2.1-or-later",
    "LGPL-3.0-only",
    "LGPL-3.0-or-later",
}
BLOCKED_SPDX_PREFIXES = ("GPL-", "AGPL-")
LICENSE_FILE_PARTS = ("license", "licence", "copying", "notice", "copyright")
TEXT_EXTENSIONS = {"", ".md", ".rst", ".txt", ".html", ".htm", ".xml", ".yml", ".yaml"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".ico"}
CODE_EXTENSIONS = {".py", ".js", ".ts", ".rs", ".c", ".cpp", ".h", ".hpp"}
TEXT_SCAN_EXTENSIONS = TEXT_EXTENSIONS | {".json", ".toml", ".py", ".bat"}
SPDX_TOKEN_RE = re.compile(r"\b(?:[A-Za-z][A-Za-z0-9.]+)(?:-[A-Za-z0-9.]+)+\b")
REQUIREMENT_NAME_RE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9_.-]*)")
IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_.]*)")
KNOWN_SPDX = DEFAULT_ALLOWED_SPDX | CONDITIONAL_SPDX | {
    "MIT-CMU",
    "GPL-2.0-only",
    "GPL-3.0-only",
    "GPL-3.0-with-GCC-exception",
    "AGPL-3.0-only",
}


def normalize_name(value: str) -> str:
    """Apply the normalization used by Python package indexes."""

    return re.sub(r"[-_.]+", "-", value).lower()


def parse_requirement_name(value: str) -> str:
    match = REQUIREMENT_NAME_RE.match(value)
    return normalize_name(match.group(1)) if match else normalize_name(value)


def _extract_array(text: str, label: str, start: int = 0) -> list[str]:
    """Read a simple TOML string array for Python 3.10 fallback support."""

    match = re.search(rf"(?m)^\s*{re.escape(label)}\s*=\s*\[", text[start:])
    if not match:
        return []
    begin = start + match.end() - 1
    depth = 0
    quote: str | None = None
    escaped = False
    for index in range(begin, len(text)):
        char = text[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
            if depth == 0:
                value = ast.literal_eval(text[begin : index + 1])
                return [str(item) for item in value]
    return []


def _section(text: str, header: str) -> str:
    match = re.search(rf"(?m)^\[{re.escape(header)}\]\s*$", text)
    if not match:
        return ""
    rest = text[match.end() :]
    next_section = re.search(r"(?m)^\[", rest)
    return rest[: next_section.start()] if next_section else rest


def load_project_metadata(pyproject_path: Path) -> dict[str, Any]:
    """Load the small project metadata subset needed by the scanner."""

    raw = pyproject_path.read_text(encoding="utf-8")
    try:
        import tomllib  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        tomllib = None
    if tomllib is not None:
        with pyproject_path.open("rb") as handle:
            return tomllib.load(handle)

    project_section = _section(raw, "project")
    optional_section = _section(raw, "project.optional-dependencies")
    dependencies = _extract_array(project_section, "dependencies")
    optional: dict[str, list[str]] = {}
    for match in re.finditer(r"(?m)^\s*([A-Za-z0-9_.-]+)\s*=\s*\[", optional_section):
        optional[match.group(1)] = _extract_array(optional_section, match.group(1), match.start())
    name_match = re.search(r"(?m)^\s*name\s*=\s*[\"']([^\"']+)[\"']", project_section)
    version_match = re.search(r"(?m)^\s*version\s*=\s*[\"']([^\"']+)[\"']", project_section)
    return {
        "project": {
            "name": name_match.group(1) if name_match else pyproject_path.parent.name,
            "version": version_match.group(1) if version_match else "unknown",
            "dependencies": dependencies,
            "optional-dependencies": optional,
        }
    }


def load_policy(root: Path) -> dict[str, Any]:
    path = root / "LICENSE" / "audit-policy.json"
    if not path.exists():
        return {
            "allowed_spdx": sorted(DEFAULT_ALLOWED_SPDX),
            "package_spdx_overrides": {"pillow": "HPND"},
            "asset_policy": [],
        }
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"License policy must be an object: {path}")
    return value


def _root_requirements(project: dict[str, Any], include_optional: bool, include_dev: bool) -> list[dict[str, str]]:
    requirements: list[dict[str, str]] = [
        {"surface": "runtime", "requested": value}
        for value in project.get("dependencies", [])
    ]
    optional = project.get("optional-dependencies", {})
    if include_optional:
        for extra, values in optional.items():
            for value in values:
                requirements.append({"surface": f"optional:{extra}", "requested": value})
    if include_dev:
        for extra, values in optional.items():
            if extra.lower() in {"dev", "test", "testing"}:
                for value in values:
                    requirements.append({"surface": f"dev:{extra}", "requested": value})
    return requirements


def _has_extra_marker(requirement: str) -> bool:
    marker = requirement.split(";", 1)[1] if ";" in requirement else ""
    return bool(re.search(r"\bextra\s*==", marker, re.IGNORECASE))


def _dependency_names(requirements: Iterable[str]) -> Iterable[str]:
    for requirement in requirements:
        if _has_extra_marker(requirement):
            continue
        yield parse_requirement_name(requirement.split(";", 1)[0])


def _project_url(dist: metadata.Distribution) -> str | None:
    urls = dist.metadata.get_all("Project-URL", [])
    if urls:
        return urls[0].split(",", 1)[-1].strip()
    return dist.metadata.get("Home-page") or None


def _read_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) > 4 * 1024 * 1024 or b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("cp1252")
        except UnicodeDecodeError:
            return data.decode("latin-1")


def _license_file_paths(dist: metadata.Distribution) -> list[str]:
    paths: list[str] = []
    for file in dist.files or []:
        text = str(file).replace("\\", "/")
        lower = text.lower()
        basename = Path(text).name.lower()
        if "/licenses/" in f"/{lower}" or any(basename.startswith(part) for part in LICENSE_FILE_PARTS):
            if Path(text).suffix.lower() in TEXT_EXTENSIONS:
                paths.append(text)
    return sorted(set(paths), key=str.lower)


def _license_documents(dist: metadata.Distribution) -> list[dict[str, str]]:
    documents: list[dict[str, str]] = []
    for relative in _license_file_paths(dist):
        path = Path(dist.locate_file(relative))
        text = _read_text(path)
        if not text:
            continue
        documents.append(
            {
                "path": relative,
                "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "text": text.replace("\r\n", "\n").replace("\r", "\n").rstrip() + "\n",
            }
        )
    return documents


def _spdx_ids(value: str) -> list[str]:
    return sorted(set(SPDX_TOKEN_RE.findall(value)))


def _bundled_license_ids(metadata_license: str, documents: list[dict[str, str]], main_spdx: str) -> list[str]:
    """Extract explicitly named component licenses without changing main status."""

    combined = "\n".join([metadata_license] + [document["text"] for document in documents])
    main_ids = set(_spdx_ids(main_spdx))
    return sorted(
        {
            identifier
            for identifier in _spdx_ids(combined)
            if identifier in KNOWN_SPDX
        }
        - main_ids
    )


def _infer_spdx(
    name: str,
    raw_license: str,
    documents: list[dict[str, str]],
    policy: dict[str, Any],
    metadata_expression: str | None = None,
    classifiers: Iterable[str] = (),
) -> str:
    normalized = normalize_name(name)
    overrides = {normalize_name(k): str(v) for k, v in policy.get("package_spdx_overrides", {}).items()}
    if normalized in overrides:
        return overrides[normalized]

    # PEP 639's License-Expression describes the distribution itself.  It
    # must win over a package's bundled third-party notices, which may contain
    # GPL/LGPL text for components that are not the package's own license.
    expression_metadata = (metadata_expression or "").strip()
    if expression_metadata:
        return expression_metadata

    expression = raw_license.strip()
    ids = _spdx_ids(expression)
    known = DEFAULT_ALLOWED_SPDX | CONDITIONAL_SPDX | {"MIT-CMU"}
    if len(expression) < 200 and ids and all(item in known or item.startswith(BLOCKED_SPDX_PREFIXES) for item in ids):
        return expression

    classifier_text = "\n".join(classifiers).lower()
    raw_lower = raw_license.lower()
    if "apache software license" in classifier_text:
        return "Apache-2.0"
    if "bsd license" in classifier_text:
        return "BSD-3-Clause"
    if "mit license" in classifier_text or "mit-cmu" in raw_lower:
        return "MIT-CMU" if "mit-cmu" in raw_lower else "MIT"
    if "isc license" in classifier_text:
        return "ISC"

    # Use the package's metadata field before looking at every license file.
    # A bundled notice can legitimately mention a different, stronger license.
    combined = "\n".join([raw_license] + [doc["text"] for doc in documents]).lower()
    if "apache license" in raw_lower or "apache software license" in raw_lower:
        return "Apache-2.0"
    if "apache 2" in raw_lower:
        return "Apache-2.0"
    if "bsd 3-clause" in raw_lower or "three-clause bsd" in raw_lower or "bsd-3-clause" in raw_lower:
        return "BSD-3-Clause"
    if "bsd 2-clause" in raw_lower or "two-clause bsd" in raw_lower or "bsd-2-clause" in raw_lower:
        return "BSD-2-Clause"
    if "mit license" in raw_lower or "mit-cmu" in raw_lower:
        return "MIT-CMU" if "mit-cmu" in raw_lower else "MIT"
    if "isc license" in raw_lower:
        return "ISC"
    if "lgpl" in combined:
        version = "3.0" if "version 3" in combined or "lgpl-3" in combined else "2.1"
        return f"LGPL-{version}-only"
    if "agpl" in combined:
        return "AGPL-3.0-only"
    if "gnu general public license" in combined or re.search(r"\bgpl[- ]?[23]", combined):
        version = "3.0" if "version 3" in combined or "gpl-3" in combined else "2.0"
        return f"GPL-{version}-only"
    if "apache license" in combined or "apache software license" in combined:
        return "Apache-2.0"
    if "mit license" in combined or "permission is hereby granted" in combined and "substantial portions" in combined:
        return "MIT"
    if "bsd 3-clause" in combined or "three-clause bsd" in combined:
        return "BSD-3-Clause"
    if "bsd 2-clause" in combined or "two-clause bsd" in combined:
        return "BSD-2-Clause"
    if "isc license" in combined:
        return "ISC"
    if "zlib license" in combined:
        return "Zlib"
    if "historical permission notice and disclaimer" in combined:
        return "HPND"
    return expression or "Unknown"


def classify_license(expression: str, allowed_spdx: Iterable[str] | None = None) -> str:
    """Return the policy status for an SPDX expression or an observed label."""

    allowed = set(allowed_spdx or DEFAULT_ALLOWED_SPDX)
    value = expression.strip()
    if not value or value.lower() in {"unknown", "none", "n/a"}:
        return "needs_review"
    ids = _spdx_ids(value)
    upper = value.upper()
    # Qt for Python exposes a deliberate LGPL/GPL choice.  It is conditional,
    # not an accidental GPL dependency, but binary distribution still needs a
    # separate legal review.
    if "LGPL" in upper and "GPL" in upper and " OR " in upper:
        return "conditional"
    if any(identifier.startswith(BLOCKED_SPDX_PREFIXES) for identifier in ids) or "GNU GENERAL PUBLIC" in upper:
        return "blocked"
    if any(identifier in CONDITIONAL_SPDX for identifier in ids) or "LGPL" in upper:
        return "conditional"
    if ids and all(identifier in allowed for identifier in ids):
        return "allowed"
    if upper in {item.upper() for item in allowed}:
        return "allowed"
    return "needs_review"


def _package_record(
    item: dict[str, Any],
    policy: dict[str, Any],
) -> dict[str, Any]:
    if item.get("missing"):
        return {
            "name": item["name"],
            "normalized_name": item["normalized_name"],
            "version": None,
            "surfaces": sorted(item["surfaces"]),
            "requested": sorted(item["requested"]),
            "direct": item["direct"],
            "status": "needs_review",
            "spdx": "Unknown",
            "metadata_license": None,
            "project_url": None,
            "license_documents": [],
            "bundled_license_ids": [],
            "missing_from_environment": True,
        }
    dist: metadata.Distribution = item["distribution"]
    metadata_license = (dist.metadata.get("License") or "").strip()
    documents = _license_documents(dist)
    spdx = _infer_spdx(
        dist.metadata.get("Name") or item["name"],
        metadata_license,
        documents,
        policy,
        metadata_expression=dist.metadata.get("License-Expression"),
        classifiers=dist.metadata.get_all("Classifier", []),
    )
    bundled_license_ids = _bundled_license_ids(metadata_license, documents, spdx)
    allowed = set(policy.get("allowed_spdx", DEFAULT_ALLOWED_SPDX))
    status = classify_license(spdx, allowed)
    if status == "allowed" and any(
        identifier in CONDITIONAL_SPDX or identifier.startswith(("GPL-", "AGPL-"))
        for identifier in bundled_license_ids
    ):
        status = "conditional"
    if not documents and len(metadata_license) < 120:
        status = "needs_review"
    record: dict[str, Any] = {
        "name": dist.metadata.get("Name") or item["name"],
        "normalized_name": item["normalized_name"],
        "version": dist.version,
        "surfaces": sorted(item["surfaces"]),
        "requested": sorted(item["requested"]),
        "direct": item["direct"],
        "status": status,
        "spdx": spdx,
        "metadata_license": metadata_license or None,
        "project_url": _project_url(dist),
        "license_documents": documents,
        "bundled_license_ids": bundled_license_ids,
        "missing_from_environment": False,
    }
    if status == "conditional":
        record["conditions"] = (
            "LGPL/GPL or dual-license terms require the applicable license text and notice. "
            "If distributed as a bundled application, verify the chosen Qt/PySide6 build "
            "and preserve the required relink/source obligations."
        )
        if bundled_license_ids:
            record["conditions"] += f" Bundled component identifiers observed: {', '.join(bundled_license_ids)}."
    return record


def _scan_dependency_closure(root: Path, include_optional: bool, include_dev: bool, policy: dict[str, Any]) -> list[dict[str, Any]]:
    project = load_project_metadata(root / "pyproject.toml").get("project", {})
    queue: list[tuple[str, str, str, bool]] = []
    for entry in _root_requirements(project, include_optional, include_dev):
        queue.append((parse_requirement_name(entry["requested"]), entry["surface"], entry["requested"], True))
    seen: dict[str, dict[str, Any]] = {}
    while queue:
        name, surface, requested, direct = queue.pop(0)
        normalized = normalize_name(name)
        item = seen.setdefault(
            normalized,
            {
                "name": name,
                "normalized_name": normalized,
                "surfaces": set(),
                "requested": set(),
                "direct": False,
                "expanded": False,
            },
        )
        item["surfaces"].add(surface)
        item["requested"].add(requested)
        item["direct"] = item["direct"] or direct
        if item["expanded"]:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            item["missing"] = True
            item["expanded"] = True
            continue
        item["distribution"] = dist
        item["expanded"] = True
        for dependency in _dependency_names(dist.requires or []):
            queue.append((dependency, "transitive", dependency, False))
    return [_package_record(seen[key], policy) for key in sorted(seen)]


def _git_files(root: Path) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z"],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return sorted(
            str(path.relative_to(root)).replace("\\", "/")
            for path in root.rglob("*")
            if path.is_file() and ".git" not in path.parts
        )
    return sorted(item.decode("utf-8") for item in result.stdout.split(b"\0") if item)


def _root_name(path: str) -> str:
    return path.split("/", 1)[0] if "/" in path else path


def _asset_policy_for(path: str, policy: dict[str, Any]) -> dict[str, Any] | None:
    for entry in policy.get("asset_policy", []):
        pattern = str(entry.get("pattern", ""))
        if pattern and fnmatch.fnmatch(path, pattern):
            return entry
    return None


def build_asset_inventory(root: Path, policy: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = policy or load_policy(root)
    files = _git_files(root)
    by_root: dict[str, dict[str, int]] = {}
    for path in files:
        root_name = _root_name(path)
        stats = by_root.setdefault(root_name, {"files": 0, "images": 0, "json": 0, "text": 0, "code": 0})
        stats["files"] += 1
        suffix = Path(path).suffix.lower()
        if suffix in IMAGE_EXTENSIONS:
            stats["images"] += 1
        if suffix == ".json":
            stats["json"] += 1
        if suffix in TEXT_SCAN_EXTENSIONS:
            stats["text"] += 1
        if suffix in CODE_EXTENSIONS:
            stats["code"] += 1

    image_2_mentions = 0
    adapter_mentions = 0
    external_path_mentions = 0
    for relative in files:
        suffix = Path(relative).suffix.lower()
        if suffix not in TEXT_SCAN_EXTENSIONS:
            continue
        text = _read_text(root / relative) or ""
        lowered = text.lower()
        if "image 2.0" in lowered or "openai imagegen" in lowered or "built-in image_gen" in lowered:
            image_2_mentions += 1
        if "deterministic_existing_asset_adapter" in lowered:
            adapter_mentions += 1
        if "g:\\マイドライブ" in lowered or "g:/マイドライブ" in lowered:
            external_path_mentions += 1

    asset_records: list[dict[str, Any]] = []
    for entry in policy.get("asset_policy", []):
        pattern = str(entry.get("pattern", ""))
        matched = [path for path in files if fnmatch.fnmatch(path, pattern)]
        if not matched:
            continue
        asset_records.append(
            {
                "pattern": pattern,
                "matched_files": len(matched),
                "status": str(entry.get("status", "needs_review")),
                "license": str(entry.get("license", "unknown")),
                "surface": str(entry.get("surface", "unknown")),
                "evidence": str(entry.get("evidence", "")),
                "notes": str(entry.get("notes", "")),
            }
        )
    if not asset_records:
        asset_records.append(
            {
                "pattern": "assets/**",
                "matched_files": sum(1 for path in files if path.startswith("assets/")),
                "status": "needs_review",
                "license": "unknown",
                "surface": "bundled source assets",
                "evidence": "No asset policy entry was found.",
                "notes": "Record the source, license, and redistribution permission before publication.",
            }
        )
    asset_needs_review = sum(1 for item in asset_records if item["status"] in {"needs_review", "blocked"})
    return {
        "tracked_files": len(files),
        "by_root": dict(sorted(by_root.items())),
        "asset_records": asset_records,
        "asset_needs_review": asset_needs_review,
        "provenance_flags": {
            "image_2_mentions": image_2_mentions,
            "deterministic_adapter_mentions": adapter_mentions,
            "external_path_mentions": external_path_mentions,
        },
    }


def _code_inventory(root: Path, files: list[str], packages: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any]:
    code_files = [path for path in files if Path(path).suffix.lower() in CODE_EXTENSIONS]
    python_files = [path for path in files if Path(path).suffix.lower() == ".py"]
    imports: set[str] = set()
    process_or_network: list[str] = []
    syntax_errors: list[str] = []
    for relative in python_files:
        text = _read_text(root / relative) or ""
        try:
            tree = ast.parse(text, filename=relative)
        except SyntaxError:
            syntax_errors.append(relative)
            tree = None
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name.split(".", 1)[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    imports.add(node.module.split(".", 1)[0])
        if re.search(r"subprocess|urllib|requests|https?://|socket|Popen|os\.system|ctypes|cffi", text):
            process_or_network.append(relative)
    stdlib = set(getattr(sys, "stdlib_module_names", set()))
    project_roots = {"pixel_tile_compiler", "scripts"}
    package_names = {item["normalized_name"] for item in packages}
    import_distributions = metadata.packages_distributions()
    import_overrides = {
        key: [normalize_name(value)] if isinstance(value, str) else [normalize_name(item) for item in value]
        for key, value in policy.get("import_distribution_overrides", {}).items()
    }
    external_imports = sorted(imports - stdlib - project_roots)
    distribution_map: dict[str, list[str]] = {}
    undeclared: list[str] = []
    for import_root in external_imports:
        candidates = import_overrides.get(import_root) or sorted({normalize_name(name) for name in import_distributions.get(import_root, [])})
        distribution_map[import_root] = candidates
        if not any(candidate in package_names for candidate in candidates):
            undeclared.append(import_root)
    return {
        "files": len(code_files),
        "python_files": len(python_files),
        "source_python_files": sum(1 for path in python_files if path.startswith("src/")),
        "test_python_files": sum(1 for path in python_files if path.startswith("tests/")),
        "script_python_files": sum(1 for path in python_files if path.startswith("scripts/")),
        "import_roots": sorted(imports),
        "external_imports": external_imports,
        "external_import_distributions": distribution_map,
        "undeclared_external_imports": undeclared,
        "syntax_errors": sorted(syntax_errors),
        "process_or_network_references": sorted(process_or_network),
    }


def build_audit(root: Path, include_optional: bool = True, include_dev: bool = True) -> dict[str, Any]:
    root = root.resolve()
    policy = load_policy(root)
    project = load_project_metadata(root / "pyproject.toml").get("project", {})
    files = _git_files(root)
    packages = _scan_dependency_closure(root, include_optional, include_dev, policy)
    assets = build_asset_inventory(root, policy)
    code = _code_inventory(root, files, packages, policy)
    return {
        "schema_version": 1,
        "project": {
            "name": project.get("name", root.name),
            "version": project.get("version", "unknown"),
            "license": "MIT",
        },
        "environment": {
            "python": f"{sys.version_info.major}.{sys.version_info.minor}",
            "implementation": sys.implementation.name,
        },
        "scope": {
            "include_optional": include_optional,
            "include_dev": include_dev,
            "source_of_truth": "pyproject.toml dependency closure and installed distribution metadata",
        },
        "packages": packages,
        "summary": _summary(packages, assets, code),
        "code_inventory": code,
        "asset_inventory": assets,
    }


def _summary(packages: list[dict[str, Any]], assets: dict[str, Any], code: dict[str, Any] | None = None) -> dict[str, int]:
    summary = {"allowed": 0, "conditional": 0, "blocked": 0, "needs_review": 0, "missing": 0, "asset_needs_review": assets["asset_needs_review"], "undeclared_external_imports": 0, "syntax_errors": 0}
    for package in packages:
        status = package["status"]
        summary[status] = summary.get(status, 0) + 1
        if package.get("missing_from_environment"):
            summary["missing"] += 1
    if code is not None:
        summary["undeclared_external_imports"] = len(code.get("undeclared_external_imports", []))
        summary["syntax_errors"] = len(code.get("syntax_errors", []))
    return summary


def _package_blockers(audit: dict[str, Any]) -> list[dict[str, Any]]:
    blockers: list[dict[str, Any]] = []
    for package in audit["packages"]:
        if package["status"] in {"blocked", "needs_review"}:
            blockers.append(
                {
                    "kind": "package",
                    "id": package["normalized_name"],
                    "status": package["status"],
                    "subject": f"{package['name']} {package.get('version') or '(not installed)'}",
                    "reason": f"SPDX={package['spdx']}; distribution license text or policy classification requires review.",
                }
            )
    for import_root in audit["code_inventory"].get("undeclared_external_imports", []):
        blockers.append(
            {
                "kind": "code",
                "id": import_root,
                "status": "needs_review",
                "subject": f"undeclared import: {import_root}",
                "reason": "The import root is not mapped to a declared dependency closure entry.",
            }
        )
    for path in audit["code_inventory"].get("syntax_errors", []):
        blockers.append(
            {
                "kind": "code",
                "id": path,
                "status": "needs_review",
                "subject": f"syntax error: {path}",
                "reason": "The AST import scan could not parse this Python file.",
            }
        )
    for asset in audit["asset_inventory"]["asset_records"]:
        if asset["status"] in {"blocked", "needs_review"}:
            blockers.append(
                {
                    "kind": "asset",
                    "id": asset["pattern"],
                    "status": asset["status"],
                    "subject": f"{asset['pattern']} ({asset['matched_files']} files)",
                    "reason": asset["notes"] or asset["evidence"],
                }
            )
    return blockers


def _render_license_entry(package: dict[str, Any]) -> str:
    lines = [
        f"Package: {package['name']}",
        f"Version: {package.get('version') or 'not installed'}",
        f"SPDX: {package['spdx']}",
        f"Status: {package['status']}",
        f"Surface: {', '.join(package['surfaces'])}",
        f"Requested: {', '.join(package['requested'])}",
        f"Project URL: {package.get('project_url') or 'not declared'}",
    ]
    if package.get("conditions"):
        lines.extend(["Conditions:", package["conditions"]])
    if package.get("bundled_license_ids"):
        lines.append(f"Bundled component SPDX identifiers: {', '.join(package['bundled_license_ids'])}")
    documents = package.get("license_documents", [])
    if not documents and package.get("metadata_license"):
        lines.extend(["Metadata License field:", package["metadata_license"]])
    for document in documents:
        lines.extend([f"--- {document['path']} (sha256={document['sha256']}) ---", document["text"].rstrip()])
    return "\n".join(lines).rstrip() + "\n"


def _render_third_party_text(audit: dict[str, Any], packages: Iterable[dict[str, Any]] | None = None) -> str:
    selected = list(packages if packages is not None else audit["packages"])
    lines = [
        "Third-party software license inventory",
        "Generated by scripts/generate_licenses.py from pyproject.toml and installed distribution metadata.",
        "The installed environment is not itself the dependency scope; unrelated distributions are excluded.",
        "",
    ]
    for package in selected:
        lines.append(_render_license_entry(package).rstrip())
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_blocked(audit: dict[str, Any]) -> str:
    blockers = _package_blockers(audit)
    lines = [
        "License review queue",
        "",
        "The release/publication gate fails when a blocked or needs_review entry exists.",
        "Conditional entries are listed for human review but do not fail the default source audit.",
        "Resolve an item in LICENSE/audit-policy.json, then regenerate and inspect the diff.",
        "",
    ]
    for item in blockers:
        lines.extend([f"[{item['status']}] {item['kind']}:{item['id']}", f"Subject: {item['subject']}", f"Reason: {item['reason']}", ""])
    conditional = [package for package in audit["packages"] if package["status"] == "conditional"]
    if conditional:
        lines.extend(["Conditional dependencies:", ""])
        for package in conditional:
            lines.extend([f"[conditional] {package['name']} {package.get('version') or 'not installed'}: {package['spdx']}", f"Condition: {package.get('conditions', '')}", ""])
    if not blockers:
        lines.append("No blocked or needs_review entries.")
    return "\n".join(lines).rstrip() + "\n"


def _render_policy_notes(audit: dict[str, Any], policy: dict[str, Any]) -> str:
    lines = [
        "# License policy notes",
        "",
        "This file is generated. The editable policy is `LICENSE/audit-policy.json`.",
        "",
        "## Project license",
        "",
        "The project code is published under MIT, as recorded in the root `LICENSE.txt`.",
        "This was selected for the public GitHub source repository because no prior project license was present.",
        "",
        "## Classification policy",
        "",
        "- `allowed`: the SPDX identifier is explicitly allowed and the distribution supplied license evidence.",
        "- `conditional`: LGPL or an explicit dual-license choice; keep the license text and review binary distribution conditions.",
        "- `blocked`: GPL/AGPL terms or an equivalent prohibited expression.",
        "- `needs_review`: missing, unknown, custom, or otherwise unclassified evidence. The default gate blocks it.",
        "",
        "Allowed SPDX identifiers:",
        "",
    ]
    for identifier in sorted(policy.get("allowed_spdx", DEFAULT_ALLOWED_SPDX)):
        lines.append(f"- `{identifier}`")
    lines.extend(["", "## Current summary", "", "```json", json.dumps(audit["summary"], ensure_ascii=False, indent=2, sort_keys=True), "```", ""])
    lines.extend(
        [
            "## Important boundaries",
            "",
            "- The dependency report includes runtime, selected optional, and development dependency closures separately.",
            "- A clean global installation is not a project lockfile. Pin versions in a lock/constraints file before binary distribution if reproducible deployment is required.",
            "- Source PNGs and derived image fixtures are not covered by the code MIT license automatically. Their provenance entries remain a separate review surface.",
            "- This repository does not contain an npm manifest, Cargo manifest, sidecar executable, font, audio, or video file at audit time.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _render_model_notice() -> str:
    return (
        "# Model and user-supplied asset notice\n\n"
        "This repository does not ship model weights, LoRA files, or a model runtime.\n"
        "If a future feature downloads or accepts one, record the exact source URL,\n"
        "license text, version/hash, and commercial-use conditions before adding it\n"
        "to a distribution or automated download path. User-supplied models remain\n"
        "the user's responsibility unless this repository explicitly adds them to\n"
        "the asset policy.\n"
    )


def _render_notice() -> str:
    return (
        "Pixel Tile Compiler third-party notices\n"
        "========================================\n\n"
        "Project source code is licensed under the MIT License in LICENSE.txt.\n"
        "Third-party package license texts and the current audit are in:\n"
        "  LICENSE/third-party-licenses.txt\n"
        "  LICENSE/third-party-licenses.json\n"
        "\n"
        "Optional PySide6/Qt components have conditional LGPL/GPL terms; see\n"
        "LICENSE/blocked-and-review.txt and LICENSE/license-policy-notes.md.\n"
        "Repository image assets and generated/derived fixtures have a separate\n"
        "provenance boundary; see LICENSE/asset-provenance.md.\n"
    )


def _render_asset_provenance(audit: dict[str, Any]) -> str:
    inventory = audit["asset_inventory"]
    lines = [
        "# Asset provenance audit",
        "",
        "Generated by `scripts/generate_licenses.py`. This inventory records repository surfaces; it does not grant rights to an image.",
        "",
        "## Findings",
        "",
        f"- Tracked files: {inventory['tracked_files']}",
        f"- Files mentioning Image 2.0/OpenAI ImageGen: {inventory['provenance_flags']['image_2_mentions']}",
        f"- Files mentioning the deterministic existing-asset adapter: {inventory['provenance_flags']['deterministic_adapter_mentions']}",
        f"- Files containing an external `G:\\マイドライブ` reference: {inventory['provenance_flags']['external_path_mentions']}",
        "",
        "## Policy records",
        "",
    ]
    for item in inventory["asset_records"]:
        lines.extend(
            [
                f"### `{item['pattern']}`",
                "",
                f"- Matched files: {item['matched_files']}",
                f"- Surface: {item['surface']}",
                f"- Status: **{item['status']}**",
                f"- License/provenance: {item['license']}",
                f"- Evidence: {item['evidence']}",
                f"- Notes: {item['notes']}",
                "",
            ]
        )
    lines.extend(
        [
            "## By repository root",
            "",
            "| Root | Files | Images | JSON | Text | Code |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for name, stats in inventory["by_root"].items():
        lines.append(f"| `{name}` | {stats['files']} | {stats['images']} | {stats['json']} | {stats['text']} | {stats['code']} |")
    return "\n".join(lines).rstrip() + "\n"


def _render_audit_report(audit: dict[str, Any]) -> str:
    code = audit["code_inventory"]
    summary = audit["summary"]
    lines = [
        "# Repository license audit report",
        "",
        "Generated by `scripts/generate_licenses.py`; inspect the generated JSON for package-level evidence.",
        "",
        "## Dependency summary",
        "",
        f"- Packages in the declared dependency closure: {len(audit['packages'])}",
        f"- Allowed: {summary.get('allowed', 0)}",
        f"- Conditional: {summary.get('conditional', 0)}",
        f"- Blocked: {summary.get('blocked', 0)}",
        f"- Needs review: {summary.get('needs_review', 0)}",
        f"- Missing from the audit interpreter: {summary.get('missing', 0)}",
        f"- Undeclared external import roots: {summary.get('undeclared_external_imports', 0)}",
        f"- Python files with AST syntax errors: {summary.get('syntax_errors', 0)}",
        "",
        "## Code surface inspected",
        "",
        f"- Python/code files: {code['files']} (src={code['source_python_files']}, tests={code['test_python_files']}, scripts={code['script_python_files']})",
        f"- Import roots observed: `{', '.join(code['import_roots'])}`",
        f"- Source files containing process/network API markers: {len(code['process_or_network_references'])}",
        "",
        "The source uses Pillow, NumPy, OpenCV, scikit-image, Pydantic, Typer, PyYAML, and optional PySide6. No npm manifest, Cargo manifest, external process launcher, HTTP client, sidecar executable, font, audio, or video was found in the audited tracked source surface.",
        "",
        "## Asset boundary",
        "",
        "PNG/JSON experiment outputs are inventoried separately. A generated or derived PNG inherits the unresolved provenance of its source; it is not automatically covered by the code license. See `asset-provenance.md` and `audit-policy.json`.",
        "",
        "## Gate interpretation",
        "",
        "The default check fails on blocked, needs_review, missing dependency evidence, undeclared external imports, syntax errors, or unresolved asset policy entries. Conditional LGPL/dual-license dependencies remain visible for human review and become a release blocker when building a bundled application under `--strict`.",
        "",
    ]
    if code["process_or_network_references"]:
        lines.extend(["Process/network marker files:", "", *[f"- `{path}`" for path in code["process_or_network_references"]], ""])
    return "\n".join(lines).rstrip() + "\n"


def _render_empty_surface(name: str) -> str:
    return f"No {name} manifest was found in this repository at audit time.\n"


def render_outputs(root: Path, audit: dict[str, Any]) -> dict[str, str]:
    license_root = root / "LICENSE"
    outputs = {
        "NOTICE.txt": _render_notice(),
        "third-party-licenses.txt": _render_third_party_text(audit),
        "third-party-licenses.json": json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        "python-licenses.txt": _render_third_party_text(audit),
        "npm-licenses.txt": _render_empty_surface("npm"),
        "cargo-licenses.txt": _render_empty_surface("Cargo"),
        "blocked-and-review.txt": _render_blocked(audit),
        "license-policy-notes.md": _render_policy_notes(audit, load_policy(root)),
        "model-license-notice.md": _render_model_notice(),
        "asset-provenance.md": _render_asset_provenance(audit),
        "audit-report.md": _render_audit_report(audit),
    }
    return {
        str(root / name if name == "NOTICE.txt" else license_root / name): content
        for name, content in outputs.items()
    }


def _write_outputs(outputs: dict[str, str]) -> None:
    for raw_path, content in outputs.items():
        path = Path(raw_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")


def _compare_outputs(outputs: dict[str, str]) -> list[str]:
    differences: list[str] = []
    for raw_path, expected in outputs.items():
        path = Path(raw_path)
        if not path.exists():
            differences.append(f"missing: {path}")
            continue
        actual = path.read_text(encoding="utf-8")
        if actual != expected:
            differences.append(f"changed: {path}")
    return differences


def audit_exit_code(audit: dict[str, Any], strict: bool = False) -> int:
    summary = audit["summary"]
    if (
        summary.get("blocked", 0)
        or summary.get("needs_review", 0)
        or summary.get("missing", 0)
        or summary.get("asset_needs_review", 0)
        or summary.get("undeclared_external_imports", 0)
        or summary.get("syntax_errors", 0)
    ):
        return 1
    if strict and summary.get("conditional", 0):
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--check", action="store_true", help="regenerate in memory and fail if committed files drift")
    parser.add_argument("--strict", action="store_true", help="also fail on conditional LGPL/dual-license dependencies")
    parser.add_argument("--no-optional", action="store_true", help="exclude optional dependency groups")
    parser.add_argument("--no-dev", action="store_true", help="exclude dev/test dependency groups")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    audit = build_audit(root, include_optional=not args.no_optional, include_dev=not args.no_dev)
    outputs = render_outputs(root, audit)
    if args.check:
        differences = _compare_outputs(outputs)
        if differences:
            for difference in differences:
                print(difference, file=sys.stderr)
            return 1
    else:
        _write_outputs(outputs)
    print(json.dumps(audit["summary"], ensure_ascii=False, sort_keys=True))
    return audit_exit_code(audit, strict=args.strict)


if __name__ == "__main__":
    raise SystemExit(main())
