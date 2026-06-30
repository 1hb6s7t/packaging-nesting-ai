from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.benchmark_importers import load_public_dataset_as_benchmark_case  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert a public OR/rectangle dataset into a BenchmarkCase JSON.")
    parser.add_argument("--input", type=Path, required=True, help="Dataset path (.json, .csv, or whitespace text).")
    parser.add_argument("--output", type=Path, required=True, help="Output BenchmarkCase JSON path.")
    parser.add_argument("--case-id", required=True, help="Benchmark case id to write into the output.")
    parser.add_argument("--name", help="Benchmark display name. Defaults to input stem.")
    parser.add_argument("--sheet-width", type=float, help="Sheet/bin width when not present in the dataset.")
    parser.add_argument("--sheet-height", type=float, help="Sheet/bin height when not present in the dataset.")
    parser.add_argument("--material", default="dataset_material")
    parser.add_argument("--thickness", default="dataset_thickness")
    parser.add_argument(
        "--planning-mode",
        choices=["single_sheet", "pattern", "expanded"],
        default="pattern",
        help="Benchmark planning mode.",
    )
    return parser.parse_args(argv)


def write_json(path: Path, payload: dict) -> Path:
    output_path = path if path.is_absolute() else REPO_ROOT / path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    input_path = args.input if args.input.is_absolute() else REPO_ROOT / args.input
    case = load_public_dataset_as_benchmark_case(
        input_path,
        case_id=args.case_id,
        name=args.name,
        sheet_width=args.sheet_width,
        sheet_height=args.sheet_height,
        material=args.material,
        thickness=args.thickness,
        planning_mode=args.planning_mode,
    )
    output_path = write_json(args.output, case.model_dump(mode="json"))
    print(
        "converted dataset "
        f"input={input_path} "
        f"output={output_path} "
        f"case_id={case.case_id} "
        f"items={len(case.items)} "
        f"planning_mode={case.planning_mode}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
