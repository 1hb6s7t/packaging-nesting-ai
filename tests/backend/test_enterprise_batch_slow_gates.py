from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "enterprise_batch_slow_gates.py"
FIXTURE_PATH = REPO_ROOT / "samples" / "artworks" / "real-sample-classification-fixtures.json"


def load_slow_gate_module():
    spec = importlib.util.spec_from_file_location("enterprise_batch_slow_gates", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def create_real_sample_placeholders(tmp_path: Path) -> Path:
    fixture = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    sample_root = tmp_path / "real-samples"
    sample_root.mkdir()
    for case in fixture["cases"]:
        (sample_root / case["source_filename"]).write_bytes(b"fixture-placeholder")
    return sample_root


def test_enterprise_batch_slow_gates_report_labels_generated_and_real_evidence(tmp_path: Path) -> None:
    module = load_slow_gate_module()
    sample_root = create_real_sample_placeholders(tmp_path)

    report = module.run_slow_gates(
        batch_1500_count=4,
        batch_20000_count=5,
        real_sample_root=sample_root,
        require_real_sample_files=True,
    )

    assert report["report_status"] == "passed"
    assert report["dataset_labels"]["batch_1500"] == "generated_synthetic_svg_dxf_pdf_placeholders"
    assert report["dataset_labels"]["batch_20000"] == "generated_synthetic_svg_dxf_pdf_placeholders"
    assert report["dataset_labels"]["real_sample_classification"] == "real_customer_sample_fixture_bbox"
    assert report["coverage"]["batch_1500"] is True
    assert report["coverage"]["batch_20000"] is True
    assert report["coverage"]["real_sample_classification"] is True
    assert report["coverage"]["real_sample_directory"] is True
    assert report["coverage"]["sheet_787x1092"] is True
    assert report["coverage"]["moq_1000"] is True
    assert report["coverage"]["top3"] is True
    assert report["coverage"]["synthetic_labels"] is True
    assert report["summary"]["synthetic_file_count"] == 9
    assert report["summary"]["real_sample_case_count"] == 6
    assert report["summary"]["real_sample_missing_file_count"] == 0
    assert report["summary"]["failed_gate_count"] == 0
    assert report["summary"]["error_count"] == 0

    batch_gates = [gate for gate in report["gates"] if gate["type"] == "generated_batch_pipeline"]
    assert {gate["benchmark_type"] for gate in batch_gates} == {"batch_1500", "batch_20000"}
    for gate in batch_gates:
        assert gate["status"] == "passed"
        assert gate["synthetic"] is True
        assert gate["fixture_source"] == "generated_svg_dxf_pdf_placeholders"
        assert gate["sheet_parent"] == {"width": 787, "height": 1092, "material": "white_card", "thickness": "350gsm"}
        assert gate["moq_per_item"] == 1000
        assert gate["top_k"] == 3
        assert gate["hard_rule_pass_rate"] == 1
        assert gate["quantity_fulfillment_rate"] == 1
        assert gate["topk_legal_rate"] == 1
        assert gate["avg_case_score"] >= 90
        assert gate["plan_count"] == 3
        assert gate["legal_plan_count"] == 3


def test_enterprise_batch_slow_gates_fail_when_real_sample_files_are_required() -> None:
    module = load_slow_gate_module()

    report = module.run_slow_gates(
        batch_1500_count=1,
        batch_20000_count=1,
        real_sample_root=Path("missing-real-samples"),
        require_real_sample_files=True,
    )

    assert report["report_status"] == "failed"
    assert report["coverage"]["real_sample_directory"] is False
    assert report["summary"]["failed_gate_count"] == 1
    assert any("real sample directory is required" in error for error in report["errors"])


def test_enterprise_batch_slow_gates_allow_missing_real_samples_for_dev_runs() -> None:
    module = load_slow_gate_module()

    report = module.run_slow_gates(
        batch_1500_count=1,
        batch_20000_count=1,
        real_sample_root=Path("missing-real-samples"),
        require_real_sample_files=False,
    )

    assert report["report_status"] == "passed"
    assert report["summary"]["skipped_gate_count"] == 1
    real_gate = next(gate for gate in report["gates"] if gate["type"] == "real_sample_classification")
    assert real_gate["status"] == "skipped"


def test_enterprise_batch_slow_gates_validate_thresholds(tmp_path: Path) -> None:
    module = load_slow_gate_module()
    sample_root = create_real_sample_placeholders(tmp_path)

    report = module.run_slow_gates(
        batch_1500_count=1,
        batch_20000_count=1,
        real_sample_root=sample_root,
        thresholds=module.SlowGateThresholds(min_avg_case_score=101),
    )

    assert report["report_status"] == "failed"
    assert any("avg_case_score is below threshold" in error for error in report["errors"])


def test_enterprise_batch_slow_gates_write_json(tmp_path: Path) -> None:
    module = load_slow_gate_module()
    output_path = tmp_path / "slow-gates.json"

    written = module.write_json(output_path, {"report_status": "passed"})

    assert written == output_path
    assert json.loads(output_path.read_text(encoding="utf-8")) == {"report_status": "passed"}
