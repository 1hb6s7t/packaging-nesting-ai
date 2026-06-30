from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from app.domain import schemas
from app.services.batch_artworks import ArtworkClassifier


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_PATH = REPO_ROOT / "samples" / "artworks" / "real-sample-classification-fixtures.json"
SCRIPT_PATH = REPO_ROOT / "scripts" / "audit_real_sample_classification.py"


def load_audit_module():
    spec = importlib.util.spec_from_file_location("audit_real_sample_classification", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_real_sample_classification_fixtures_match_target_classes() -> None:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    parent = schemas.SheetParentSpec(
        width=fixture["parent_sheet_mm"]["width"],
        height=fixture["parent_sheet_mm"]["height"],
    )
    classifier = ArtworkClassifier()

    actual_by_case = {}
    for case in fixture["cases"]:
        bbox = case["bbox_mm"]
        feature = schemas.ArtworkFeature(
            bbox=schemas.BBox(
                width=bbox["width"],
                height=bbox["height"],
                min_x=0,
                min_y=0,
                max_x=bbox["width"],
                max_y=bbox["height"],
            ),
            area=bbox["width"] * bbox["height"],
            aspect_ratio=round(bbox["width"] / bbox["height"], 4),
            parse_confidence=0.35 if case["source_format"] == "pdf" else 0.95,
            needs_manual_review=case["source_format"] in {"pdf", "ai", "cdr"},
            metadata={"feature_source": "real_sample_classification_fixture"},
        )
        actual_by_case[case["case_id"]] = classifier.classify(
            feature,
            parent=parent,
            source_format=case["source_format"],
        )

    assert actual_by_case["coffee_machine_full_sheet"] == "FULL_SHEET"
    assert actual_by_case["soy_milk_machine_anchor"] == "ANCHOR"
    assert actual_by_case["large_outer_box_anchor"] == "ANCHOR"
    assert actual_by_case["gage_box_filler"] == "FILLER"
    assert actual_by_case["capsule_box_filler"] == "FILLER"
    assert actual_by_case["cat_litter_box_oversize"] == "OVERSIZE"


def test_real_sample_classification_audit_script_reports_fixture_coverage(tmp_path: Path) -> None:
    module = load_audit_module()
    fixture = module.load_fixture(FIXTURE_PATH)
    sample_root = tmp_path / "real-samples"
    sample_root.mkdir()
    for case in fixture["cases"]:
        (sample_root / case["source_filename"]).write_bytes(b"fixture-placeholder")

    report = module.build_report(fixture, sample_root=sample_root, require_files=True)

    assert report["report_status"] == "passed"
    assert report["summary"]["case_count"] == len(fixture["cases"])
    assert report["summary"]["classification_match_count"] == len(fixture["cases"])
    assert report["summary"]["missing_file_count"] == 0
    assert report["errors"] == []
    assert all(item["file_exists"] for item in report["cases"])
