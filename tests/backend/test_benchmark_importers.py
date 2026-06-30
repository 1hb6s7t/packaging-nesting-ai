from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from app.services.benchmark_importers import load_public_dataset_as_benchmark_case


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "convert_or_dataset_to_benchmark_case.py"


def load_converter_module():
    spec = importlib.util.spec_from_file_location("convert_or_dataset_to_benchmark_case", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_or_style_json_dataset_converts_to_benchmark_case(tmp_path: Path) -> None:
    dataset = tmp_path / "or_dataset.json"
    dataset.write_text(
        json.dumps(
            {
                "bin_width": 787,
                "bin_height": 1092,
                "rectangles": [
                    {"id": "box_a", "width": 80, "height": 60, "demand": 1000},
                    {"id": "box_b", "w": 120, "h": 90, "qty": 250},
                ],
            }
        ),
        encoding="utf-8",
    )

    case = load_public_dataset_as_benchmark_case(dataset, case_id="OR_CASE_001")

    assert case.case_id == "OR_CASE_001"
    assert case.planning_mode == "pattern"
    assert case.sheet.width == 787
    assert case.sheet.height == 1092
    assert len(case.items) == 2
    assert case.items[0].item_id == "box_a"
    assert case.items[0].quantity == 1000
    assert case.items[1].polygon.outer[2] == (120.0, 90.0)


def test_csv_dataset_uses_cli_sheet_dimensions(tmp_path: Path) -> None:
    dataset = tmp_path / "rectangles.csv"
    dataset.write_text("id,width,height,quantity\nbox_a,80,60,1000\n", encoding="utf-8")

    case = load_public_dataset_as_benchmark_case(
        dataset,
        case_id="CSV_CASE_001",
        sheet_width=787,
        sheet_height=1092,
        planning_mode="expanded",
    )

    assert case.planning_mode == "expanded"
    assert case.sheet.width == 787
    assert case.items[0].quantity == 1000


def test_converter_cli_writes_standard_benchmark_case_json(tmp_path: Path) -> None:
    module = load_converter_module()
    dataset = tmp_path / "rectangles.csv"
    output = tmp_path / "benchmark-case.json"
    dataset.write_text("id,width,height,quantity\nbox_a,80,60,1000\n", encoding="utf-8")

    exit_code = module.main(
        [
            "--input",
            str(dataset),
            "--output",
            str(output),
            "--case-id",
            "CLI_CASE_001",
            "--sheet-width",
            "787",
            "--sheet-height",
            "1092",
            "--planning-mode",
            "pattern",
        ]
    )

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["case_id"] == "CLI_CASE_001"
    assert payload["planning_mode"] == "pattern"
    assert payload["sheet"]["width"] == 787
    assert payload["items"][0]["quantity"] == 1000
