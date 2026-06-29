from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import sys
import tomllib
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_PYPROJECT = Path("backend/pyproject.toml")
FRONTEND_PACKAGE_LOCK = Path("frontend/package-lock.json")

COPyleft_LICENSE_MARKERS = (
    "AGPL",
    "GPL",
    "LGPL",
    "SSPL",
    "BUSL",
    "COMMONS CLAUSE",
    "LICENSE-REF",
    "LICENSEREF",
    "SEE LICENSE",
    "UNLICENSED",
)
NON_RELEASE_BLOCKING_SCOPES = {"dev", "optional:test"}


@dataclass(frozen=True)
class DependencyRecord:
    ecosystem: str
    name: str
    scope: str
    source: str
    declared_specifier: str | None = None
    version: str | None = None
    installed: bool = True
    license: str | None = None
    resolved: str | None = None
    review_required: bool = False
    review_reason: str | None = None


def parse_requirement(requirement: str) -> tuple[str, str | None]:
    cleaned = requirement.split(";", 1)[0].strip()
    match = re.match(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?(.*)$", cleaned)
    if not match:
        return cleaned, None
    name = normalize_package_name(match.group(1))
    specifier = match.group(2).strip() or None
    return name, specifier


def normalize_package_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def collect_backend_dependencies(repo_root: Path) -> list[DependencyRecord]:
    pyproject_path = repo_root / BACKEND_PYPROJECT
    if not pyproject_path.exists():
        return []
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    records: list[DependencyRecord] = []
    for requirement in data.get("project", {}).get("dependencies", []):
        records.append(build_python_dependency_record(requirement, "runtime", str(BACKEND_PYPROJECT)))
    optional_dependencies = data.get("project", {}).get("optional-dependencies", {})
    for group, requirements in optional_dependencies.items():
        for requirement in requirements:
            records.append(build_python_dependency_record(requirement, f"optional:{group}", str(BACKEND_PYPROJECT)))
    return sorted(records, key=lambda item: (item.scope, item.name))


def build_python_dependency_record(requirement: str, scope: str, source: str) -> DependencyRecord:
    name, specifier = parse_requirement(requirement)
    version = installed_version(name)
    installed = version is not None
    license_name = installed_license(name) if installed else None
    license_requires_review, reason = license_review_status(license_name)
    if not installed:
        if is_release_blocking_scope(scope):
            reason = "package is not installed in this environment; regenerate inventory in the release image"
        else:
            reason = "package is not installed in this environment; not required for the production release image"
    elif license_name is None:
        reason = "missing license metadata; confirm during release review"
    return DependencyRecord(
        ecosystem="python",
        name=name,
        scope=scope,
        source=source,
        declared_specifier=specifier,
        version=version,
        installed=installed,
        license=license_name,
        review_required=(installed and license_requires_review) or (not installed and is_release_blocking_scope(scope)),
        review_reason=reason,
    )


def is_release_blocking_scope(scope: str) -> bool:
    return scope not in NON_RELEASE_BLOCKING_SCOPES


def installed_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def installed_license(name: str) -> str | None:
    try:
        metadata = importlib.metadata.metadata(name)
    except importlib.metadata.PackageNotFoundError:
        return None
    license_expressions = metadata.get_all("License-Expression") or []
    normalized_expressions = [collapse_whitespace(value) for value in license_expressions if value and value.strip()]
    if normalized_expressions:
        return "; ".join(sorted(set(normalized_expressions)))
    license_value = (metadata.get("License") or "").strip()
    if license_value and license_value.upper() not in {"UNKNOWN", "UNKNOWN LICENSE"}:
        return collapse_whitespace(license_value)
    classifiers = metadata.get_all("Classifier") or []
    license_classifiers = [item.split("::")[-1].strip() for item in classifiers if item.startswith("License ::")]
    if license_classifiers:
        return "; ".join(sorted(set(license_classifiers)))
    return None


def collect_frontend_dependencies(repo_root: Path) -> list[DependencyRecord]:
    lock_path = repo_root / FRONTEND_PACKAGE_LOCK
    if not lock_path.exists():
        return []
    lock_data = json.loads(lock_path.read_text(encoding="utf-8"))
    packages = lock_data.get("packages", {})
    root = packages.get("", {})
    runtime_specs = root.get("dependencies", {})
    dev_specs = root.get("devDependencies", {})
    records: list[DependencyRecord] = []
    for package_path, package_data in packages.items():
        if package_path == "" or "node_modules/" not in package_path:
            continue
        name = npm_package_name_from_path(package_path)
        scope = frontend_scope(name, runtime_specs, dev_specs)
        license_name = package_data.get("license")
        review_required, reason = license_review_status(license_name)
        records.append(
            DependencyRecord(
                ecosystem="npm",
                name=name,
                scope=scope,
                source=str(FRONTEND_PACKAGE_LOCK),
                declared_specifier=runtime_specs.get(name) or dev_specs.get(name),
                version=package_data.get("version"),
                license=license_name,
                resolved=package_data.get("resolved"),
                review_required=review_required,
                review_reason=reason,
            )
        )
    return sorted(records, key=lambda item: (item.scope, item.name, item.version or ""))


def npm_package_name_from_path(package_path: str) -> str:
    parts = package_path.split("/")
    node_index = len(parts) - 1 - parts[::-1].index("node_modules")
    first = parts[node_index + 1]
    if first.startswith("@"):
        return f"{first}/{parts[node_index + 2]}"
    return first


def frontend_scope(name: str, runtime_specs: dict[str, str], dev_specs: dict[str, str]) -> str:
    if name in runtime_specs:
        return "runtime"
    if name in dev_specs:
        return "dev"
    return "transitive"


def license_review_status(license_name: str | None) -> tuple[bool, str | None]:
    if not license_name:
        return True, "missing license metadata"
    upper = license_name.upper()
    for marker in COPyleft_LICENSE_MARKERS:
        if marker in upper:
            return True, f"license marker requires review: {marker}"
    return False, None


def collapse_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def build_dependency_inventory(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    records = collect_backend_dependencies(repo_root) + collect_frontend_dependencies(repo_root)
    dependencies = [asdict(record) for record in records]
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "repo_root": str(repo_root),
        "sources": [str(BACKEND_PYPROJECT), str(FRONTEND_PACKAGE_LOCK)],
        "summary": summarize_dependencies(dependencies),
        "dependencies": dependencies,
    }


def summarize_dependencies(dependencies: list[dict[str, Any]]) -> dict[str, Any]:
    by_ecosystem: dict[str, int] = {}
    by_scope: dict[str, int] = {}
    by_license: dict[str, int] = {}
    installed_count = 0
    missing_install_count = 0
    release_blocking_missing_install = []
    review_required = []
    for item in dependencies:
        by_ecosystem[item["ecosystem"]] = by_ecosystem.get(item["ecosystem"], 0) + 1
        by_scope[item["scope"]] = by_scope.get(item["scope"], 0) + 1
        license_name = item.get("license") or "UNKNOWN"
        by_license[license_name] = by_license.get(license_name, 0) + 1
        if item.get("installed", True):
            installed_count += 1
        else:
            missing_install_count += 1
            if is_release_blocking_scope(str(item.get("scope") or "")):
                release_blocking_missing_install.append(
                    {
                        "ecosystem": item["ecosystem"],
                        "name": item["name"],
                        "scope": item["scope"],
                        "version": item.get("version"),
                        "license": item.get("license"),
                        "reason": item.get("review_reason"),
                    }
                )
        if item.get("review_required"):
            review_required.append(
                {
                    "ecosystem": item["ecosystem"],
                    "name": item["name"],
                    "scope": item["scope"],
                    "installed": bool(item.get("installed", True)),
                    "version": item.get("version"),
                    "license": item.get("license"),
                    "reason": item.get("review_reason"),
                }
            )
    return {
        "dependency_count": len(dependencies),
        "installed_count": installed_count,
        "missing_install_count": missing_install_count,
        "release_blocking_missing_install_count": len(release_blocking_missing_install),
        "release_blocking_missing_install": sorted(
            release_blocking_missing_install,
            key=lambda item: (item["ecosystem"], item["scope"], item["name"]),
        ),
        "by_ecosystem": dict(sorted(by_ecosystem.items())),
        "by_scope": dict(sorted(by_scope.items())),
        "by_license": dict(sorted(by_license.items())),
        "review_required_count": len(review_required),
        "review_required": sorted(review_required, key=lambda item: (item["ecosystem"], item["scope"], item["name"])),
    }


def write_inventory(output_path: Path, inventory: dict[str, Any], repo_root: Path = REPO_ROOT) -> Path:
    resolved = output_path if output_path.is_absolute() else repo_root / output_path
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return resolved


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate release dependency and license inventory JSON.")
    parser.add_argument("--output", type=Path, default=Path("artifacts/dependency-inventory.json"))
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    inventory = build_dependency_inventory(REPO_ROOT)
    output_path = write_inventory(args.output, inventory, REPO_ROOT)
    summary = inventory["summary"]
    print(
        "dependency inventory written: "
        f"{output_path} "
        f"dependencies={summary['dependency_count']} "
        f"review_required={summary['review_required_count']}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
