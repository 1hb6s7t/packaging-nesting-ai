# Pattern Planning

This document records the batch pattern planning boundary after the A5 service split.

## Modules

- `backend/app/services/batch_patterns.py`
  - `PatternPlanner` generates candidate production patterns for each compatibility group and cut variant.
  - `DeterministicPatternPlacementSolver` logic inside the planner writes compact placement JSON/SVG artifacts for every persisted `ProductionPattern`.
  - `ProductionPlanBuilder` builds persisted plan contracts from selected patterns and candidate-pool evidence.
  - `TopKGlobalPlanSelector` selects highest-utilization, balanced-risk, and fastest-production plans.
- `backend/app/services/batch_layout.py`
  - Owns batch layout job orchestration, persistence, group creation, solver evidence, approval, export gating, and row-to-schema mapping.

## Quantity Fulfillment

`PatternPlanner` records mixed-item quantity metrics in `ProductionPatternRead.validator_report["quantity_summary"]`:

- `requested_units_by_item`
- `units_per_sheet_by_item`
- `required_sheets_by_item`
- `produced_units_by_item`
- `shortage_units_by_item`
- `overproduction_units_by_item`
- aggregate requested, produced, fulfilled, shortage, and overproduction units

`ProductionPlanBuilder` aggregates the same fields into `ProductionPlanRead.validator_report["quantity_summary"]`.

For mixed-item groups, `required_sheets` is the sum of per-item template sheet counts from `required_sheets_by_item`, not the maximum of those counts. This prevents mixed patterns from claiming that independent item templates can all occupy the same physical sheet at once.

## Placement Artifacts

Every `ProductionPatternRead` now carries:

- `placement_json`: schema-versioned placement artifact with sheet template coordinates, deterministic solver metadata, coverage flags, and a pointer to `validator_report.quantity_summary`.
- `placement_svg`: operator-facing SVG preview of rendered pattern templates.
- `placement_checksum`: SHA-256 over the placement JSON and SVG payloads.
- `placement_solver`: deterministic solver identity, version, input hash, and coordinate source.

The API exposes these artifacts through:

- `GET /api/batch-layout/patterns/{pattern_id}`
- `GET /api/batch-layout/patterns/{pattern_id}/placement`
- `GET /api/batch-layout/patterns/{pattern_id}/placement.svg`

For very large compatibility groups, the JSON/SVG artifact renders a bounded number of item templates and records `omitted_item_count` / `complete_item_coverage` so release gates do not hide truncation. Full quantity coverage remains in `validator_report.quantity_summary`.

## Current Boundary

- Pattern quantities are calculated from parsed feature bounding boxes and cut-variant capacity estimates.
- The planner can prove MOQ fulfillment and per-item overproduction/shortage metrics for mixed groups.
- The planner persists deterministic per-pattern placement artifacts for rendered sheet templates; configured external PackingSolver/Sparrow binaries are still required before claiming external-solver production coordinates.
- Production export remains blocked until Validator, approval, permission, and confirmation workflows pass.

## Verification

The mixed-item contract is covered by:

```powershell
$env:PYTHONPATH='backend'
pytest -q tests\backend\test_batch_layout_planning.py
```
