# Current Capability Report

## Audit Inputs

- Repository state audited: current worktree after `bc7559c` (`feat: add enterprise audit contract endpoints`) plus the A8 batch AI tool patch.
- Target documents:
  - `C:\Users\shenh\Downloads\packaging-nesting-ai_一次性企业级升级实施方案.pdf`
  - `C:\Users\shenh\Downloads\packaging-nesting-ai_企业级升级任务Backlog.csv`
  - `C:\Users\shenh\Downloads\packaging-nesting-ai_多Agent企业级升级执行提示词.txt`
- Repository areas inspected in this pass: `README.md`, `docker-compose.yml`, `docs/`, `backend/app`, `backend/app/services`, `backend/app/services/solvers`, `backend/app/api/routes`, `backend/app/db/models.py`, `backend/app/domain/schemas.py`, `tests/backend`, and `frontend/src`.

## Executive Summary

The project has moved beyond the original enterprise skeleton. It now contains persistent batch intake, batch artwork state tracking, feature extraction/classification, compatibility grouping, sheet cut variants, batch layout jobs, production patterns/plans, approval-gated production-plan export, MultiSolver candidate-pool evidence, external CLI solver contracts, benchmark gates, controlled batch AI tools, and a Vue batch workbench.

It is still not fully production-grade for unattended customer go-live. The largest remaining gaps are true production-coordinate batch planning, real configured PackingSolver/Sparrow binaries contributing legal candidates, deeper PatternPlanner support for mixed multi-item fulfillment, full real-sample 1500/20000 slow-release stress coverage in release gates, and stronger frontend coverage for dedicated enterprise views, virtualization, and benchmark history.

## Enterprise-Usable Capabilities

| Area | Status | Evidence |
| --- | --- | --- |
| Enterprise platform skeleton | Enterprise usable | `README.md`, `docker-compose.yml`, `backend/app/api/router.py`, `backend/app/services/security.py`, `backend/app/services/repository.py` |
| RBAC, audit, approval, export governance | Enterprise usable | `backend/app/api/routes/rbac.py`, `backend/app/api/routes/operation_logs.py`, `backend/app/api/routes/solutions.py`, `backend/app/services/workflows.py`, `tests/backend/test_rbac.py`, `tests/backend/test_solution_approval.py` |
| Production export guardrails | Enterprise usable for single-solution exports | `backend/app/services/workflows.py`, `backend/app/api/routes/solutions.py`, `tests/backend/test_solution_approval.py` |
| Batch data model | Enterprise foundation present | `backend/app/db/models.py` classes `BatchUpload`, `BatchArtworkItem`, `SheetParentSpec`, `SheetCutVariant`, `BatchLayoutJob`, `BatchLayoutGroup`, `ProductionPattern`, `ProductionPlan`, `ProductionPlanPattern`, `BatchBenchmarkRun`; migration `backend/alembic/versions/0015_batch_layout_enterprise_tables.py` |
| Batch artwork API | Enterprise foundation present | `backend/app/api/routes/batch_artworks.py` implements upload, preflight, parse, retry-failed, summary; `tests/backend/test_batch_artwork_api.py` |
| Feature extraction/classification | Enterprise foundation present | `backend/app/services/batch_artworks.py` classes `ArtworkFeatureExtractor`, `ArtworkClassifier`; `tests/backend/test_batch_artwork_features.py` |
| Compatibility grouping and cut variants | Enterprise foundation present | `backend/app/services/batch_layout.py` classes `CompatibilityGroupingService`, `SheetCutVariantGenerator`; `tests/backend/test_batch_layout_planning.py` |
| Batch layout job/plans | Enterprise foundation present | `backend/app/services/batch_layout.py`, `backend/app/api/routes/batch_layout.py` including job/plan detail endpoints, `tests/backend/test_batch_layout_api.py` |
| MultiSolver candidate pool | Enterprise foundation present | `backend/app/services/solvers/multi_orchestrator.py`, `backend/app/services/workflows.py`, `tests/backend/test_multi_solver_orchestrator.py`, `tests/backend/test_audit_and_runs.py` |
| External CLI solver contracts | Enterprise foundation present | `backend/app/services/solvers/external_cli_adapters.py`, `backend/app/services/solvers/cli_runner.py`, `tests/backend/test_external_solver_adapters.py` |
| Benchmark release gate | Enterprise foundation present | `scripts/benchmark_release_gate.py`, `scripts/release_preflight.py`, `scripts/verify_release_preflight.py`, `tests/backend/test_benchmark_release_gate.py`, `tests/backend/test_verify_release_preflight.py` |
| Frontend batch workbench | MVP to enterprise foundation | `frontend/src/views/BatchWorkbench.vue`, `frontend/src/services/api.ts`, `frontend/src/router/index.ts`; includes upload/preflight/parse/retry, Top3 workflow, 787/1500/20000 stress controls |
| AI safety boundary | Enterprise usable for current single-job and batch workflow tools | `backend/app/services/ai_tools.py`, `backend/app/api/routes/ai.py`, `tests/backend/test_ai_tools.py` |

## MVP Capabilities

| Area | MVP behavior | Enterprise gap |
| --- | --- | --- |
| Native artwork parsing | SVG/DXF direct parsing and PDF/manual/conversion fallback exist. | Parser is not proven at 99.5% native SVG/DXF success on real customer corpora; PDF/CDR/AI are not production-native parsers. |
| Batch layout patterns | Patterns are generated from bbox/feature capacity estimates and group/cut-variant heuristics. | Final batch production coordinates are not yet mapped from each selected solver candidate into exact persisted production pattern placements. |
| Top3 production plans | `TopKGlobalPlanSelector` emits highest-utilization, balanced-risk, fastest-production intents with `diversity_score`. | Fallback can still create lower-diversity variants when candidate signatures repeat; Top3 validity depends on candidate-pool evidence and heuristic pattern feasibility, not full physical placement solving for every pattern. |
| MultiSolver orchestration | Candidate-pool execution runs solver names x seeds x time limits x rotation policies and ranks validated solutions. | Public API method names from the target (`generate_candidate_solutions`, `run_solver_matrix`, `validate_all`, `rank_top_k`) are not first-class methods; current surface is `solve_candidate_pool` plus helper methods. |
| PackingSolver/Sparrow | Real CLI contracts exist, stdin/stdout JSON is handled, evidence is persisted. | Real binaries are not bundled or configured; without installed binaries these adapters correctly return failed auditable candidates. |
| OR-Tools role | OR-Datasets importer and release-gate OR coverage exist. | OR-Tools is still not a production combination/quantity planner in the solver matrix. |
| Frontend batch UI | One workbench covers upload, preflight, parse, failed retry, grouping, Top3, preview, approval/export, and 1500/20000 stress entry. | It is not split into all target product pages and still lacks table virtualization, benchmark history, and dedicated exception triage views. |
| Release gates | Backend targeted gates, benchmark gate, evidence pack, frontend build and smoke are available. | Slow 1500/20000 batch, real sample benchmark, formal production env evidence, external acceptance, release-image dependency audit, and final handoff/go-live evidence are not closed in the current local artifacts. |

## Blocking Go-Live Items

1. True production-coordinate batch layout is not complete.
   Evidence: `backend/app/services/batch_layout.py` computes production patterns from capacity estimates; preview in `backend/app/api/routes/batch_layout.py` is summary SVG, not exact production placement output.

2. Real external solvers are contract-ready but not operationally proven.
   Evidence: `backend/app/services/solvers/external_cli_adapters.py` supports CLI contracts; README and tests show missing binaries become failed solutions. Go-live requires configured PackingSolver/Sparrow binaries and real baseline runs.

3. `batch-20000` exists as an explicit generated-pipeline endpoint, but it is not yet a default blocking release gate with real 20000-file evidence.
   Evidence: `backend/app/api/routes/benchmarks.py` exposes `POST /run/batch-20000`; tests exercise reduced generated counts for speed.

4. Batch AI tools are present but remain a controlled orchestration surface, not a production export path.
   Evidence: `backend/app/services/ai_tools.py` exposes `get_batch_summary`, `get_batch_features`, `create_batch_layout_job`, `run_batch_layout_job`, `compare_batch_top3`, and `generate_batch_report`; write tools require `ai:use` plus `batch:write`; `tests/backend/test_ai_tools.py` verifies the permission gate and that AI cannot enable production export.

5. Frontend does not expose every target workflow as separate operational views.
   Evidence: `frontend/src/views/BatchWorkbench.vue` consolidates multiple workflows into one page; no dedicated pages for retry queue, cut-spec config, benchmark history, or detailed oversize exception triage.

6. Default release preflight does not prove full slow enterprise volume.
   Evidence: `scripts/release_preflight.py` runs `benchmark_release_gate.py`, but the gate is deterministic and compact; `tests/backend/test_enterprise_benchmarks_api.py` uses reduced file counts for unit speed.

7. Formal go-live evidence is not closed.
   Evidence: local remediation artifacts show production env and external acceptance as pending/skipped, release-image dependency evidence is not complete, and no final release handoff/go-live readiness package is present under `artifacts/`.

## Needs Refactor Or Hardening

- Promote high-value solver attempt evidence from JSON logs into indexed first-class fields or artifact keys.
  Current evidence is durable in `solver_run_log.payload`, but reporting/search will be stronger with columns for `candidate_id`, `input_sha256`, `stdout/stderr` object keys, `validator_report`, and certificate references.

- Separate `PatternPlanner`, `ProductionPlanBuilder`, and `Top3GlobalPlanSelector`.
  Current code keeps these responsibilities largely inside `backend/app/services/batch_layout.py`. This is workable now but too dense for enterprise ownership and independent testing at 20000-file scale.

- Add exact placement persistence for `ProductionPattern`.
  `ProductionPattern` currently stores scalar planning metrics and validator summaries. Enterprise production needs per-pattern placement JSON/SVG artifacts tied to deterministic solver output.

- Keep frontend retry and generated 20000 stress controls aligned with backend evidence wording.
  `frontend/src/services/api.ts` now exposes `retry-failed` and `batch-20000`; dedicated pages and history views are still pending.

- Continue hardening AI tool governance for batch-specific workflows.
  The batch tool surface now covers query, features, create/run, Top3 comparison, and report generation; go-live documentation and operator playbooks should continue to stress that AI cannot export or bypass approval.

- Keep AI authentication docs aligned with code.
  `README.md`, `docs/DEPLOYMENT.md`, and `docs/OPERATIONS.md` now state that AI tool schema access requires Bearer Token and `ai:use`.

## Performance And Scale Risks

- 1500-file endpoint exists and can run a generated pipeline, but full real-file parser throughput, memory profile, and storage pressure are not yet proven against the real sample directory or a 20000-file corpus.
- Current `batch-1500` tests use reduced generated fixtures for speed; they prove the code path, not a full real customer 1500-file acceptance run.
- Batch grouping and candidate generation may become combinatorial when many customer/material/date groups and custom cut variants are active.
- Current frontend table rendering will need pagination or virtualization for 1500-20000 rows.
- `BatchLayoutService._build_solver_candidate_evidence` intentionally limits solver evidence items by `solver_evidence_item_limit`; this keeps runs fast but does not prove full-batch physical optimization.
- PDF/CDR/AI conversion service is a contract path, not a production-proven supplier integration unless external acceptance evidence is supplied.

## Solver Risks

- Rectpack remains the only always-available deterministic solver.
- PackingSolver/Sparrow are real CLI adapters but require configured binaries, compatible output schemas, seed/time-limit controls, and baseline evidence.
- OR-Tools is not yet a production planner in the solver matrix.
- Top3 legal solution rate can be high in current deterministic benchmarks but is not proven over mixed real irregular shapes with real external solvers.

## File Parsing Risks

- SVG/DXF parsing supports common basic shapes and lines; complex customer exports may include transforms, nested groups, clipping, strokes-as-cuts, malformed units, or vendor-specific DXF entities.
- PDF bbox fallback is not proven as native production geometry in the batch parser. PDF/AI/CDR should remain conversion/manual-review until a tested parser/supplier path is accepted.
- The target classification examples from the PDF need a dedicated fixture set and assertions: coffee-machine FULL_SHEET, soy-milk-machine/big box ANCHOR, Gage/capsule box FILLER, cat litter box OVERSIZE.

## 1500+/20000 Batch Risks

- `BatchArtworkService` has lifecycle and retry support, but no resumable chunk upload protocol or frontend virtualization yet.
- `run_batch_pipeline(file_count=1500)` and generated `batch-20000` entry points exist, but full 20000-file slow tests and default blocking release gates are not present.
- Database indexes and artifact retention policy should be reviewed before real 20000-file use.

## MOQ 1000 And Pattern Risks

- `batch_planning.py` and batch layout pattern metrics calculate `units_per_sheet`, `required_sheets`, produced units, shortage/overproduction, and quantity fulfillment.
- Mixed multi-item OR-style pattern fulfillment remains limited; current release gate uses compact deterministic cases rather than proving all real mixed-item patterns.
- `ProductionPlanPattern.produced_units` is scalar in the current ORM model; enterprise reporting will likely need per-order produced-unit JSON for multi-order patterns.

## Backlog Status Map

| Backlog item | Status |
| --- | --- |
| A0 架构契约 | Partially complete: `docs/ENTERPRISE_FINALIZATION.md` exists, but this current report identifies missing endpoint contracts and go-live gaps. |
| A1 数据库迁移 | Mostly complete for listed batch/pattern/plan/benchmark objects; future migration needed for first-class solver attempt evidence and richer produced-units JSON. |
| A2 批量文件入口 | Mostly complete: upload/preflight/parse/summary/retry exist; native parser success and resumable 1500/20000 UX still need proof. |
| A3 版图特征和分类 | Foundation complete; target real-sample classification fixture tests still needed. |
| A3 兼容分组 | Foundation complete by material/thickness/print method/spot color/due date/category/customer; hard customer rules need expansion. |
| A3 裁切变体 | Foundation complete for parent/rotated/half/third/quarter/custom model input. |
| A4 MultiSolverOrchestrator | Foundation complete; public method contract and full cut-variant solver matrix need hardening. |
| A4 PackingSolverAdapter | Contract complete; real binary acceptance not proven. |
| A4 SparrowSolverAdapter | Contract complete; real binary acceptance not proven. |
| A5 PatternPlanner | Foundation present inside batch layout and batch planning; should be extracted and deepened for mixed multi-item quantities. |
| A5 Top3GlobalPlanSelector | Foundation complete; stronger diversity/validity proof needed. |
| A6 Benchmark gate | Foundation complete for OR/787/MOQ and generated batch-1500/20000 endpoint coverage; missing default slow real-sample gates. |
| A6 OR-Datasets importer | Present. |
| A7 前端批量页面 | Foundation present as one workbench with retry and batch-20000 controls; dedicated enterprise pages, virtualization, and history views still missing. |
| A8 AI工具扩展 | Batch workflow tools now cover query/features/create/run/Top3/report with RBAC gates; production export remains blocked and go-live docs/evidence still need final closure. |
| A8 上线文档 | Many docs/scripts exist; current go-live report should be extended after remaining blockers close. |

## Recommended Next Implementation Order

1. Extract PatternPlanner/ProductionPlanBuilder from `batch_layout.py` and add mixed multi-item quantity tests.
2. Add real sample fixture classification tests for the PDF examples.
3. Add slow-release scripts for real sample directory, full 1500 generated pipeline, and 20000 synthetic batch.
4. Convert generated 1500/20000 endpoint evidence into formal slow gate artifacts with clear synthetic/real dataset labels.
5. Add operator-facing batch AI playbooks that show controlled query/run/report flows and explicitly exclude export/approval bypass.

## Current Verification Snapshot

This report and the contract patches made with it were verified with:

- `pytest -q tests\backend`: 466 passed, 2 skipped.
- `python -m ruff check backend tests scripts`: passed.
- `npm.cmd run build` from `frontend/`: passed.
- `python scripts\benchmark_release_gate.py --output tmp\benchmark-release-gate-ai-batch.json`: passed, 7 cases, 0 errors, P95 22 ms.
- `git diff --check`: no whitespace errors; Windows line-ending warnings only.

This report is an audit artifact plus a contract-alignment record. Any implementation changes after this report must rerun the relevant backend/frontend/release gates before being considered complete.
