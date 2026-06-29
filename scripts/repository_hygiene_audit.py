from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GITIGNORE = REPO_ROOT / ".gitignore"

REQUIRED_IGNORE_PATTERNS = (
    "__pycache__/",
    ".pytest_cache/",
    ".venv/",
    "*.pyc",
    "*.pyo",
    "*.db",
    "storage/",
    "logs/",
    "*.log",
    ".codex-live-*.log",
    "artifacts/",
    "tmp/",
    "node_modules/",
    "dist/",
    ".env",
    ".env.*",
    "!.env.production.example",
)


def build_repository_hygiene_audit(*, gitignore_file: Path = DEFAULT_GITIGNORE) -> dict[str, Any]:
    resolved_gitignore = gitignore_file if gitignore_file.is_absolute() else REPO_ROOT / gitignore_file
    errors: list[str] = []
    warnings: list[str] = []
    patterns: list[str] = []

    if not resolved_gitignore.is_file():
        errors.append(f".gitignore does not exist: {resolved_gitignore}")
    else:
        patterns = parse_gitignore_patterns(resolved_gitignore)
        missing_patterns = [pattern for pattern in REQUIRED_IGNORE_PATTERNS if pattern not in patterns]
        errors.extend(f".gitignore is missing required pattern: {pattern}" for pattern in missing_patterns)
        if ".env.*" in patterns and "!.env.production.example" not in patterns:
            errors.append(".gitignore must unignore .env.production.example when .env.* is ignored")
        if "tmp/" not in patterns:
            warnings.append("tmp/ is not ignored; release preflight artifacts may be mixed into source review")
        if "artifacts/" not in patterns:
            warnings.append("artifacts/ is not ignored; go-live evidence may be mixed into source review")

    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "passed" if not errors else "failed",
        "gitignore_file": str(resolved_gitignore),
        "summary": {
            "required_pattern_count": len(REQUIRED_IGNORE_PATTERNS),
            "observed_pattern_count": len(patterns),
            "missing_pattern_count": sum(1 for pattern in REQUIRED_IGNORE_PATTERNS if pattern not in patterns),
            "error_count": len(errors),
            "warning_count": len(warnings),
        },
        "required_patterns": list(REQUIRED_IGNORE_PATTERNS),
        "observed_patterns": patterns,
        "errors": errors,
        "warnings": warnings,
    }


def parse_gitignore_patterns(path: Path) -> list[str]:
    patterns: list[str] = []
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        patterns.append(stripped)
    return patterns


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    output_path = path if path.is_absolute() else REPO_ROOT / path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit repository ignore rules for generated artifacts and local secrets.")
    parser.add_argument("--gitignore-file", type=Path, default=DEFAULT_GITIGNORE)
    parser.add_argument("--output", type=Path, help="Optional JSON audit report path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_repository_hygiene_audit(gitignore_file=args.gitignore_file)
    if args.output:
        write_json(args.output, report)
    summary = report["summary"]
    print(
        "repository hygiene audit "
        f"{report['status']} "
        f"required_patterns={summary['required_pattern_count']} "
        f"missing={summary['missing_pattern_count']} "
        f"errors={summary['error_count']}",
        flush=True,
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
