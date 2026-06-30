# Enterprise finalization notes

This addendum records the final local enterprise-hardening work completed after the external audit package review.

## Core production constraints

- AI tools cannot generate production coordinates. Solver coordinates must come from backend solver adapters.
- Production PDF/DXF export still requires Validator success, solution approval, export permission, and the confirmation phrase.
- Unconfigured external solver adapters return auditable `failed` solutions and cannot be promoted to `valid` by Validator.

## Batch planning and benchmark coverage

- `backend/app/services/batch_planning.py` provides `single_sheet`, `pattern`, and `expanded` planning modes.
- Pattern planning calculates units per sheet, required sheets, produced units, overproduction, shortage, and quantity fulfillment.
- Expanded planning solves remaining quantities sheet by sheet and can avoid overproduction when exact remaining quantities fit.
- `benchmark_run` now records enterprise metrics: `hard_rule_pass`, `quantity_fulfillment_rate`, requested/produced/shortage/overproduction units, units per sheet, sheets used, peak RSS, export gate status, case score, baseline delta, P95 runtime, and extensible `metrics_json`.
- `tests/backend/test_benchmark_stress_787.py` covers 787x1092 sheets for 1000/3000/5000/10000/15000 quantities.

## Public datasets and external solvers

- `scripts/convert_or_dataset_to_benchmark_case.py` converts public OR/rectangle JSON, CSV, or whitespace datasets into repository `BenchmarkCase` JSON.
- `backend/app/services/solvers/external_cli_adapters.py` provides CLI contracts for PackingSolver and Sparrow.
- Configure external binaries through `packing_solver_command`, `packing_solver_binary`, `PACKING_SOLVER_BINARY`, `sparrow_command`, `sparrow_binary`, or `SPARROW_SOLVER_BINARY`.
- CLI adapters pass JSON input on stdin and expect JSON/certificate output on stdout. Missing binaries, timeouts, nonzero exits, invalid JSON, and invalid certificates become failed solutions with unplaced reasons.

## Release gates

- `scripts/benchmark_release_gate.py` runs deterministic 787x1092 benchmark gates and writes `benchmark-release-gate.json`.
- `scripts/release_preflight.py` runs the benchmark gate by default and embeds its payload in the preflight report.
- `scripts/verify_release_preflight.py` verifies that benchmark gates cover Pattern and Expanded modes, 1000/3000/5000/10000/15000 quantities, quantity fulfillment thresholds, runtime thresholds, and RSS thresholds when configured.
- Targeted preflight backend tests now include AI safety, solution approval, external solver adapters, batch planning, benchmark importers, benchmark release gate, and 787 stress tests.

## AI tool governance

- `GET /api/ai/tools` now requires `ai:use`.
- AI tool definitions expose `schema_version`, `required_permissions`, `read_only`, `mutates`, `reversible`, `blocked_in_production`, and `requires_human_approval`.
- AI tool execution checks the declared permission list and continues to block `create_nesting_job`, `export_pdf`, `export_dxf`, and `write_back_crm` inside the AI boundary.
