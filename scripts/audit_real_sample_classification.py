from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.domain import schemas  # noqa: E402
from app.services.batch_artworks import ArtworkClassifier  # noqa: E402


DEFAULT_FIXTURE_PATH = REPO_ROOT / "samples" / "artworks" / "real-sample-classification-fixtures.json"


def load_fixture(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def build_report(
    fixture: dict[str, Any],
    *,
    sample_root: Path | None = None,
    require_files: bool = False,
    hash_files: bool = False,
) -> dict[str, Any]:
    parent = schemas.SheetParentSpec(
        width=float(fixture.get("parent_sheet_mm", {}).get("width", 787)),
        height=float(fixture.get("parent_sheet_mm", {}).get("height", 1092)),
    )
    root = sample_root or Path(str(fixture.get("source_root_hint", "")))
    root_exists = bool(root and root.exists())
    classifier = ArtworkClassifier()
    cases = []
    errors: list[str] = []
    for case in fixture.get("cases", []):
        case_report = evaluate_case(
            classifier,
            parent,
            root=root if root_exists else None,
            case=case,
            hash_files=hash_files,
        )
        cases.append(case_report)
        if case_report["classification_match"] is False:
            errors.append(
                f"{case_report['case_id']} expected {case_report['expected_classification']} "
                f"but got {case_report['actual_classification']}"
            )
        if require_files and not case_report["file_exists"]:
            errors.append(f"{case_report['case_id']} source file not found: {case_report['source_filename']}")
    missing_file_count = sum(1 for case in cases if not case["file_exists"])
    classification_match_count = sum(1 for case in cases if case["classification_match"])
    if errors:
        status = "failed"
    elif not root_exists:
        status = "skipped"
    else:
        status = "passed"
    return {
        "schema_version": 1,
        "report_status": status,
        "generated_at": datetime.now(UTC).isoformat(),
        "sample_root": str(root),
        "sample_root_exists": root_exists,
        "require_files": require_files,
        "hash_files": hash_files,
        "summary": {
            "case_count": len(cases),
            "classification_match_count": classification_match_count,
            "missing_file_count": missing_file_count,
            "error_count": len(errors),
        },
        "errors": errors,
        "cases": cases,
    }


def evaluate_case(
    classifier: ArtworkClassifier,
    parent: schemas.SheetParentSpec,
    *,
    root: Path | None,
    case: dict[str, Any],
    hash_files: bool,
) -> dict[str, Any]:
    bbox = case["bbox_mm"]
    feature = schemas.ArtworkFeature(
        bbox=schemas.BBox(
            width=float(bbox["width"]),
            height=float(bbox["height"]),
            min_x=0,
            min_y=0,
            max_x=float(bbox["width"]),
            max_y=float(bbox["height"]),
        ),
        area=round(float(bbox["width"]) * float(bbox["height"]), 4),
        area_ratio=0,
        aspect_ratio=round(float(bbox["width"]) / float(bbox["height"]), 4) if float(bbox["height"]) else 0,
        parse_confidence=0.35 if case.get("source_format") == "pdf" else 0.95,
        needs_manual_review=case.get("source_format") in {"pdf", "ai", "cdr"},
        metadata={
            "feature_source": "real_sample_classification_fixture",
            "area_ratio_basis": "bbox",
            "product_hint": case.get("product_hint"),
        },
    )
    actual = classifier.classify(feature, parent=parent, source_format=case.get("source_format"))
    source_path = find_source_file(root, str(case["source_filename"])) if root else None
    file_info = file_metadata(source_path, hash_file=hash_files) if source_path else {}
    expected = case["expected_classification"]
    return {
        "case_id": case["case_id"],
        "product_hint": case.get("product_hint"),
        "source_filename": case["source_filename"],
        "source_path": str(source_path) if source_path else None,
        "file_exists": source_path is not None,
        "source_format": case.get("source_format"),
        "bbox_mm": bbox,
        "expected_classification": expected,
        "actual_classification": actual,
        "classification_match": actual == expected,
        "file": file_info,
    }


def find_source_file(root: Path | None, filename: str) -> Path | None:
    if root is None or not root.exists():
        return None
    direct = root / filename
    if direct.exists():
        return direct
    matches = [path for path in root.rglob(filename) if path.is_file()]
    return matches[0] if matches else None


def file_metadata(path: Path, *, hash_file: bool) -> dict[str, Any]:
    stat = path.stat()
    metadata: dict[str, Any] = {
        "size_bytes": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, UTC).isoformat(),
    }
    if hash_file:
        metadata["sha256"] = sha256_file(path)
    return metadata


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit real packaging sample classification fixtures.")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE_PATH)
    parser.add_argument("--sample-root", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--require-files", action="store_true")
    parser.add_argument("--hash-files", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    fixture = load_fixture(args.fixture)
    report = build_report(
        fixture,
        sample_root=args.sample_root,
        require_files=args.require_files,
        hash_files=args.hash_files,
    )
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    else:
        print(payload)
    return 0 if report["report_status"] in {"passed", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
