from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "release_inventory.py"


def load_release_inventory_module():
    spec = importlib.util.spec_from_file_location("release_inventory", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_parse_requirement_handles_extras_and_specifiers() -> None:
    module = load_release_inventory_module()

    assert module.parse_requirement("uvicorn[standard]>=0.30") == ("uvicorn", ">=0.30")
    assert module.parse_requirement("pydantic-settings>=2.4 ; python_version >= '3.12'") == (
        "pydantic-settings",
        ">=2.4",
    )


class FakeMetadata:
    def __init__(self, values: dict[str, list[str] | str]) -> None:
        self.values = values

    def get(self, key: str, default: Any = None) -> Any:
        value = self.values.get(key, default)
        if isinstance(value, list):
            return value[0] if value else default
        return value

    def get_all(self, key: str) -> list[str] | None:
        value = self.values.get(key)
        if value is None:
            return None
        return value if isinstance(value, list) else [value]


def test_python_license_expression_metadata_is_used(monkeypatch) -> None:
    module = load_release_inventory_module()

    monkeypatch.setattr(
        module.importlib.metadata,
        "metadata",
        lambda name: FakeMetadata({"License-Expression": ["BSD-3-Clause"], "License": "UNKNOWN"}),
    )

    assert module.installed_license("uvicorn") == "BSD-3-Clause"


def test_python_missing_install_is_reported_explicitly(monkeypatch) -> None:
    module = load_release_inventory_module()

    monkeypatch.setattr(module, "installed_version", lambda name: None)

    record = module.build_python_dependency_record("celery>=5.4", "runtime", "backend/pyproject.toml")

    assert record.installed is False
    assert record.version is None
    assert record.license is None
    assert record.review_required is True
    assert "not installed" in record.review_reason


def test_missing_optional_test_dependency_is_not_release_blocking(monkeypatch) -> None:
    module = load_release_inventory_module()

    monkeypatch.setattr(module, "installed_version", lambda name: None)

    record = module.build_python_dependency_record("pytest>=8.2", "optional:test", "backend/pyproject.toml")
    summary = module.summarize_dependencies([module.asdict(record)])

    assert record.installed is False
    assert record.review_required is False
    assert "not required for the production release image" in record.review_reason
    assert summary["missing_install_count"] == 1
    assert summary["release_blocking_missing_install_count"] == 0
    assert summary["review_required_count"] == 0


def test_dependency_inventory_collects_python_and_npm_manifests(tmp_path) -> None:
    module = load_release_inventory_module()
    backend = tmp_path / "backend"
    frontend = tmp_path / "frontend"
    backend.mkdir()
    frontend.mkdir()
    (backend / "pyproject.toml").write_text(
        """
[project]
dependencies = ["fastapi>=0.115", "uvicorn[standard]>=0.30"]

[project.optional-dependencies]
optimization = ["rectpack>=0.2"]
""".strip(),
        encoding="utf-8",
    )
    (frontend / "package-lock.json").write_text(
        json.dumps(
            {
                "packages": {
                    "": {
                        "dependencies": {"vue": "^3.5.0"},
                        "devDependencies": {"vite": "^7.0.0"},
                    },
                    "node_modules/vue": {"version": "3.5.0", "license": "MIT", "resolved": "https://example/vue.tgz"},
                    "node_modules/vite": {"version": "7.0.0", "license": "MIT"},
                    "node_modules/copyleft-lib": {"version": "1.0.0", "license": "GPL-3.0"},
                    "node_modules/@scope/pkg": {"version": "2.0.0", "license": "Apache-2.0"},
                }
            }
        ),
        encoding="utf-8",
    )

    inventory = module.build_dependency_inventory(tmp_path)
    dependencies = {
        (item["ecosystem"], item["name"], item["scope"]): item
        for item in inventory["dependencies"]
    }

    assert dependencies[("python", "fastapi", "runtime")]["declared_specifier"] == ">=0.115"
    assert dependencies[("python", "uvicorn", "runtime")]["declared_specifier"] == ">=0.30"
    assert dependencies[("python", "rectpack", "optional:optimization")]["declared_specifier"] == ">=0.2"
    assert dependencies[("npm", "vue", "runtime")]["version"] == "3.5.0"
    assert dependencies[("npm", "vite", "dev")]["declared_specifier"] == "^7.0.0"
    assert dependencies[("npm", "copyleft-lib", "transitive")]["review_required"] is True
    assert dependencies[("npm", "@scope/pkg", "transitive")]["license"] == "Apache-2.0"
    assert inventory["summary"]["dependency_count"] == 7
    assert inventory["summary"]["installed_count"] <= 7
    assert inventory["summary"]["missing_install_count"] >= 0
    assert inventory["summary"]["by_ecosystem"] == {"npm": 4, "python": 3}
    assert inventory["summary"]["review_required_count"] >= 1


def test_dependency_inventory_writer_resolves_relative_paths(tmp_path) -> None:
    module = load_release_inventory_module()
    inventory = {"schema_version": 1, "summary": {"dependency_count": 0}, "dependencies": []}

    output_path = module.write_inventory(Path("artifacts/deps.json"), inventory, tmp_path)

    assert output_path == tmp_path / "artifacts" / "deps.json"
    assert json.loads(output_path.read_text(encoding="utf-8")) == inventory
