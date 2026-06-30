from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "benchmark_release_gate.py"


def load_benchmark_release_gate_module():
    spec = importlib.util.spec_from_file_location("benchmark_release_gate", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_benchmark_release_gate_runs_787_pattern_and_expanded_cases() -> None:
    module = load_benchmark_release_gate_module()

    report = module.run_benchmark_gate(quantity_levels=[1000])

    assert report["status"] == "passed"
    assert report["summary"]["case_count"] == 3
    assert report["summary"]["quantity_levels"] == [1000]
    assert set(report["summary"]["planning_modes"]) == {"pattern", "expanded"}
    assert report["coverage"]["or_dataset"] is True
    assert report["coverage"]["sheet_787x1092"] is True
    assert report["coverage"]["moq_1000"] is True
    assert "or_dataset" in report["coverage"]["case_sources"]
    assert report["summary"]["min_quantity_fulfillment_rate"] == 1
    assert report["summary"]["error_count"] == 0
    assert all(case["requested_units"] >= 1000 for case in report["cases"])
    assert all(case["shortage_units"] == 0 for case in report["cases"])
    assert all(case["hard_rule_pass"] is True for case in report["cases"])


def test_benchmark_release_gate_fails_when_quantity_threshold_is_not_met() -> None:
    module = load_benchmark_release_gate_module()

    report = module.run_benchmark_gate(
        thresholds=module.BenchmarkGateThresholds(min_quantity_fulfillment_rate=1.1),
        quantity_levels=[1000],
    )

    assert report["status"] == "failed"
    assert report["summary"]["error_count"] > 0
    assert any("quantity_fulfillment_rate" in error for error in report["errors"])
