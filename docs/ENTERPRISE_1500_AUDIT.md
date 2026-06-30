# 1500 File Enterprise Upgrade Audit

## Scope

This audit maps the current project against the enterprise target: 1500+ packaging artwork inputs, 787x1092 mm parent sheets with cut variants, MOQ 1000 per item, deterministic Top3 production plans, and replayable audit evidence. It was prepared from the current repository and the referenced enterprise implementation PDF.

## Completed Capabilities

- FastAPI backend, SQLAlchemy models, Alembic migrations, RBAC, operation logs, approval-gated exports, solver run persistence, and release evidence gates are already present.
- SVG/DXF direct artwork preflight and polygon parsing are implemented. PDF/AI/CDR and other non-direct formats are archived and routed to conversion/manual review.
- Single NestingJob workflow can create jobs, run the deterministic solver path, validate geometry, approve solutions, and export approved production output.
- Pattern/expanded batch quantity planning exists for benchmark use, including 787x1092 stress cases and quantity fulfillment metrics.
- External PackingSolver/Sparrow CLI contracts exist with structured missing-binary, timeout, invalid JSON, and certificate parse failures.

## MVP Capability

The previous system was a usable single-job enterprise MVP: upload one parseable artwork, create a sheet and nesting job, run a solver, validate, approve, export, and audit the operation. It was not yet a 1500-file batch production planning system.

## Blocking Gaps Before This Slice

- No persistent batch upload model for 1500+ artwork files.
- No batch artwork item lifecycle: uploaded, preflighted, parsed, conversion required, manual review, failed.
- No artwork feature extraction contract with bbox, area, area ratio, aspect ratio, holes, concavity, parse confidence, and manual review flag.
- No FULL_SHEET, ANCHOR, FILLER, OVERSIZE, MULTI_PAGE classification.
- No compatibility grouping by material, thickness, print method, spot color, due date, category, and customer rules.
- No 787x1092 parent sheet cut variant model.
- No batch layout job, production pattern, production plan, Top3 global plan selector, or batch benchmark run persistence.
- No `/api/batch-artworks/*`, `/api/batch-layout/*`, or `/api/benchmarks/*` enterprise endpoints.
- Frontend still lacks the batch artwork workbench, parse progress, feature table, grouping view, cut config, Top3 plan comparison, pattern detail, fulfillment report, oversize exception view, and 1500-file stress result pages.

## Implemented In This Slice

- Added enterprise schemas for batch uploads, batch artwork items, artwork features, sheet parent specs, cut variants, batch layout jobs/groups, production patterns/plans, and batch benchmark runs.
- Added ORM tables and Alembic migration `0015_batch_layout_enterprise_tables`.
- Added `BatchArtworkService`, `ArtworkFeatureExtractor`, and `ArtworkClassifier`.
- Added `CompatibilityGroupingService`, `SheetCutVariantGenerator`, `CandidateJobGenerator`, and `TopKGlobalPlanSelector`.
- Added `/api/batch-artworks/upload`, `/preflight`, `/parse`, and `/summary`.
- Added `/api/batch-layout/jobs`, `/run`, `/groups`, `/plans`, `/plans/{plan_id}/preview`, and guarded `/export`.
- Added `/api/benchmarks/import/or-datasets`, `/run/stress-787`, and `/run/batch-1500`.
- Added `batch:write` permission and seeded it into enterprise role templates.
- Added tests for feature extraction/classification, grouping, cut variants, Top3 selection, batch APIs, enterprise benchmark APIs, RBAC, route auth, and migrations.
- Added a frontend batch artwork workbench for multi-file upload, preflight, parse, feature table, grouping, Top3 plan comparison, plan preview, plan approval/export actions, oversize/manual-review visibility, and 1500-file stress entry.
- Added production plan approval and JSON manifest export persistence, with plan-scoped confirmation phrases and workflow-layer Validator/approval checks.

## Remaining Risks

- Top3 plans are deterministic production-planning candidates, not final physical placement coordinates. Production coordinates still require solver and Validator integration.
- The new batch layout path uses heuristic capacity estimates for pattern candidates. It must be deepened with real MultiSolverOrchestrator runs, multi-seed solver matrices, and exact validator certificates.
- Batch plan export now requires production-plan approval and writes a JSON export manifest. PDF/DXF production exports for batch plans still require a later precise-placement integration.
- PDF/JPG/PNG/AI real files are correctly classified as conversion/manual-review inputs; native geometry extraction for those formats is still out of scope until a conversion supplier or parser is integrated.
- The 1500-file endpoint currently exercises synthetic feature/classification stress. It should be extended to run repository fixtures and real customer-like datasets in release gates.
- True MultiSolverOrchestrator execution is still shallow: Top3 global plans are deterministic capacity/pattern candidates, not a full multi-solver, multi-seed coordinate candidate pool.

## Verification

- `python -m ruff check backend scripts tests`
- `pytest -q tests/backend`
- `npm.cmd run build` from `frontend/`
- `python scripts/benchmark_release_gate.py --output tmp/benchmark-release-gate-after-plan-approval.json`
- Result: 457 passed, 2 skipped; frontend build passed; benchmark gate passed with P95 29 ms.
