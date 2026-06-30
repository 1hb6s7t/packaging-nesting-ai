# Pattern Planning

This document records the batch pattern planning boundary after the A5 service split.

## Modules

- `backend/app/services/batch_patterns.py`
  - `PatternPlanner` generates candidate production patterns for each compatibility group and cut variant.
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

## Current Boundary

- Pattern quantities are calculated from parsed feature bounding boxes and cut-variant capacity estimates.
- The planner can prove MOQ fulfillment and per-item overproduction/shortage metrics for mixed groups.
- The planner still does not persist exact production placement coordinates for every repeated pattern sheet.
- Production export remains blocked until Validator, approval, permission, and confirmation workflows pass.

## Verification

The mixed-item contract is covered by:

```powershell
$env:PYTHONPATH='backend'
pytest -q tests\backend\test_batch_layout_planning.py
```
