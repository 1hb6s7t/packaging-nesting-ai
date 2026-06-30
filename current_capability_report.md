# Current Capability Report

## Audit Inputs

- Repository state audited: current worktree after `60b14fc` (`feat: add batch AI playbook and faster slow gates`) plus the production pattern placement artifact patch.
- Target documents:
  - `C:\Users\shenh\Downloads\packaging-nesting-ai_一次性企业级升级实施方案.pdf`
  - `C:\Users\shenh\Downloads\packaging-nesting-ai_企业级升级任务Backlog.csv`
  - `C:\Users\shenh\Downloads\packaging-nesting-ai_多Agent企业级升级执行提示词.txt`
- Repository areas inspected in this pass: `README.md`, `docker-compose.yml`, `docs/`, `backend/app`, `backend/app/services`, `backend/app/services/solvers`, `backend/app/api/routes`, `backend/app/db/models.py`, `backend/app/domain/schemas.py`, `tests/backend`, and `frontend/src`.

## Executive Summary

The project has moved beyond the original enterprise skeleton. It now contains persistent batch intake, batch artwork state tracking, feature extraction/classification, compatibility grouping, sheet cut variants, batch layout jobs, production patterns/plans with deterministic placement artifacts, approval-gated production-plan export, MultiSolver candidate-pool evidence, external CLI solver contracts, benchmark gates, controlled batch AI tools, and a Vue batch workbench.

It is still not fully production-grade for unattended customer go-live. The largest remaining gaps are configured external-solver production-coordinate acceptance, real PackingSolver/Sparrow binaries contributing legal candidates, native real-PDF geometry acceptance beyond fixture classification, and stronger frontend coverage for dedicated enterprise views, virtualization, and benchmark history.

## Enterprise-Usable Capabilities

| Area | Status | Evidence |
| --- | --- | --- |
| Enterprise platform skeleton | Enterprise usable | `README.md`, `docker-compose.yml`, `backend/app/api/router.py`, `backend/app/services/security.py`, `backend/app/services/repository.py` |
| RBAC, audit, approval, export governance | Enterprise usable | `backend/app/api/routes/rbac.py`, `backend/app/api/routes/operation_logs.py`, `backend/app/api/routes/solutions.py`, `backend/app/services/workflows.py`, `tests/backend/test_rbac.py`, `tests/backend/test_solution_approval.py` |
| Production export guardrails | Enterprise usable for single-solution exports | `backend/app/services/workflows.py`, `backend/app/api/routes/solutions.py`, `tests/backend/test_solution_approval.py` |
| Batch data model | Enterprise foundation present | `backend/app/db/models.py` classes `BatchUpload`, `BatchArtworkItem`, `SheetParentSpec`, `SheetCutVariant`, `BatchLayoutJob`, `BatchLayoutGroup`, `ProductionPattern`, `ProductionPlan`, `ProductionPlanPattern`, `BatchBenchmarkRun`; migrations `backend/alembic/versions/0015_batch_layout_enterprise_tables.py` and `backend/alembic/versions/0016_production_pattern_placement_artifacts.py` |
| Batch artwork API | Enterprise foundation present | `backend/app/api/routes/batch_artworks.py` implements upload, preflight, parse, retry-failed, summary; `tests/backend/test_batch_artwork_api.py` |
| Feature extraction/classification | Enterprise foundation present with real-sample class fixtures | `backend/app/services/batch_artworks.py` classes `ArtworkFeatureExtractor`, `ArtworkClassifier`; `samples/artworks/real-sample-classification-fixtures.json`; `scripts/audit_real_sample_classification.py`; `tests/backend/test_batch_artwork_features.py`, `tests/backend/test_real_sample_classification_fixtures.py` |
| Compatibility grouping and cut variants | Enterprise foundation present | `backend/app/services/batch_layout.py` classes `CompatibilityGroupingService`, `SheetCutVariantGenerator`; `tests/backend/test_batch_layout_planning.py` |
| Batch layout job/plans | Enterprise foundation present | `backend/app/services/batch_layout.py`, `backend/app/api/routes/batch_layout.py` including job/plan detail endpoints, `tests/backend/test_batch_layout_api.py` |
| MultiSolver candidate pool | Enterprise foundation present | `backend/app/services/solvers/multi_orchestrator.py`, `backend/app/services/workflows.py`, `tests/backend/test_multi_solver_orchestrator.py`, `tests/backend/test_audit_and_runs.py` |
| External CLI solver contracts | Enterprise foundation present | `backend/app/services/solvers/external_cli_adapters.py`, `backend/app/services/solvers/cli_runner.py`, `tests/backend/test_external_solver_adapters.py` |
| Benchmark release gate | Enterprise foundation present | `scripts/benchmark_release_gate.py`, `scripts/enterprise_batch_slow_gates.py`, `scripts/release_preflight.py`, `scripts/verify_release_preflight.py`, `tests/backend/test_benchmark_release_gate.py`, `tests/backend/test_enterprise_batch_slow_gates.py`, `tests/backend/test_verify_release_preflight.py` |
| Frontend batch workbench | MVP to enterprise foundation | `frontend/src/views/BatchWorkbench.vue`, `frontend/src/services/api.ts`, `frontend/src/router/index.ts`; includes upload/preflight/parse/retry, Top3 workflow, 787/1500/20000 stress controls |
| AI safety boundary | Enterprise usable for current single-job and batch workflow tools | `backend/app/services/ai_tools.py`, `backend/app/api/routes/ai.py`, `docs/AI_BATCH_TOOLS.md`, `tests/backend/test_ai_tools.py` |

## MVP Capabilities

| Area | MVP behavior | Enterprise gap |
| --- | --- | --- |
| Native artwork parsing | SVG/DXF direct parsing and PDF/manual/conversion fallback exist. | Parser is not proven at 99.5% native SVG/DXF success on real customer corpora; PDF/CDR/AI are not production-native parsers. |
| Batch layout patterns | `PatternPlanner` generates bbox/feature capacity patterns with per-item mixed-order quantity summaries and persisted deterministic placement JSON/SVG artifacts. | Configured external PackingSolver/Sparrow binary placement acceptance and broader real mixed-irregular proof are still pending. |
| Top3 production plans | `TopKGlobalPlanSelector` emits highest-utilization, balanced-risk, fastest-production intents with `diversity_score`. | Fallback can still create lower-diversity variants when candidate signatures repeat; Top3 validity depends on candidate-pool evidence and heuristic pattern feasibility, not full physical placement solving for every pattern. |
| MultiSolver orchestration | Candidate-pool execution runs solver names x seeds x time limits x rotation policies and ranks validated solutions. | Public API method names from the target (`generate_candidate_solutions`, `run_solver_matrix`, `validate_all`, `rank_top_k`) are not first-class methods; current surface is `solve_candidate_pool` plus helper methods. |
| PackingSolver/Sparrow | Real CLI contracts exist, stdin/stdout JSON is handled, evidence is persisted. | Real binaries are not bundled or configured; without installed binaries these adapters correctly return failed auditable candidates. |
| OR-Tools role | OR-Datasets importer and release-gate OR coverage exist. | OR-Tools is still not a production combination/quantity planner in the solver matrix. |
| Frontend batch UI | One workbench covers upload, preflight, parse, failed retry, grouping, Top3, preview, approval/export, and 1500/20000 stress entry. | It is not split into all target product pages and still lacks table virtualization, benchmark history, and dedicated exception triage views. |
| Release gates | Backend targeted gates, benchmark gate, opt-in enterprise batch slow gate, evidence pack, frontend build and smoke are available. | Formal production env evidence, external acceptance, release-image dependency audit, native PDF production geometry, and final handoff/go-live evidence are not closed in the current local artifacts. |

## Blocking Go-Live Items

1. External-solver production-coordinate batch layout is not complete.
   Evidence: `backend/app/services/batch_patterns.py` computes production patterns from capacity estimates and now writes deterministic placement artifacts; go-live still needs configured PackingSolver/Sparrow binaries to provide accepted external placement evidence.

2. Real external solvers are contract-ready but not operationally proven.
   Evidence: `backend/app/services/solvers/external_cli_adapters.py` supports CLI contracts; README and tests show missing binaries become failed solutions. Go-live requires configured PackingSolver/Sparrow binaries and real baseline runs.

3. `batch-20000` exists as an explicit generated-pipeline endpoint and opt-in slow gate, but it is synthetic generated evidence, not real customer-file 20000 acceptance evidence.
   Evidence: `backend/app/api/routes/benchmarks.py` exposes `POST /run/batch-20000`; `scripts/enterprise_batch_slow_gates.py` defaults to 20000 generated files and labels the dataset as synthetic.

4. Batch AI tools are present but remain a controlled orchestration surface, not a production export path.
   Evidence: `backend/app/services/ai_tools.py` exposes `get_batch_summary`, `get_batch_features`, `create_batch_layout_job`, `run_batch_layout_job`, `compare_batch_top3`, and `generate_batch_report`; write tools require `ai:use` plus `batch:write`; `tests/backend/test_ai_tools.py` verifies the permission gate and that AI cannot enable production export.

5. Frontend does not expose every target workflow as separate operational views.
   Evidence: `frontend/src/views/BatchWorkbench.vue` consolidates multiple workflows into one page; no dedicated pages for retry queue, cut-spec config, benchmark history, or detailed oversize exception triage.

6. Default release preflight does not prove full slow enterprise volume unless explicitly requested.
   Evidence: `scripts/release_preflight.py` keeps the deterministic benchmark gate as default; `--include-slow-batch-gates` adds `scripts/enterprise_batch_slow_gates.py` with generated 1500/20000 and real-sample classification evidence.

7. Formal go-live evidence is not closed.
   Evidence: local remediation artifacts show production env and external acceptance as pending/skipped, release-image dependency evidence is not complete, and no final release handoff/go-live readiness package is present under `artifacts/`.

## Needs Refactor Or Hardening

- Promote high-value solver attempt evidence from JSON logs into indexed first-class fields or artifact keys.
  Current evidence is durable in `solver_run_log.payload`, but reporting/search will be stronger with columns for `candidate_id`, `input_sha256`, `stdout/stderr` object keys, `validator_report`, and certificate references.

- Continue hardening `PatternPlanner`, `ProductionPlanBuilder`, and `Top3GlobalPlanSelector`.
  These responsibilities are now separated into `backend/app/services/batch_patterns.py`; next hardening should focus on external-solver placement mapping and larger real-sample mixed-pattern coverage.

- Expand placement evidence from deterministic planner templates to configured external PackingSolver/Sparrow production placement acceptance.
  `ProductionPattern` now stores deterministic placement JSON/SVG/checksum/solver metadata. Final go-live still needs external binary evidence and larger real mixed-irregular coverage.

- Keep frontend retry and generated 20000 stress controls aligned with backend evidence wording.
  `frontend/src/services/api.ts` now exposes `retry-failed` and `batch-20000`; dedicated pages and history views are still pending.

- Continue hardening AI tool governance for batch-specific workflows.
  The batch tool surface now covers query, features, create/run, Top3 comparison, and report generation; `docs/AI_BATCH_TOOLS.md` now provides the operator playbook and stresses that AI cannot export or bypass approval.

- Keep AI authentication docs aligned with code.
  `README.md`, `docs/DEPLOYMENT.md`, and `docs/OPERATIONS.md` now state that AI tool schema access requires Bearer Token and `ai:use`.

## Performance And Scale Risks

- 1500-file and 20000-file slow-gate artifacts can now be generated through `scripts/enterprise_batch_slow_gates.py`; real-file parser throughput and native PDF geometry remain outside that generated synthetic proof.
- Current fast tests use reduced generated fixtures for speed; the full slow artifact must be produced separately with the 1500/20000 counts before release acceptance.
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
- The target classification examples now have a dedicated fixture set and assertions: coffee-machine FULL_SHEET, soy-milk-machine/big box ANCHOR, Gage/capsule box FILLER, cat litter box OVERSIZE. This remains classification evidence, not native PDF die-line extraction evidence.

## 1500+/20000 Batch Risks

- `BatchArtworkService` has lifecycle and retry support, but no resumable chunk upload protocol or frontend virtualization yet.
- `run_batch_pipeline(file_count=1500)`, generated `batch-20000`, and the opt-in slow gate exist; the default preflight intentionally stays compact unless `--include-slow-batch-gates` is passed.
- Database indexes and artifact retention policy should be reviewed before real 20000-file use.

## MOQ 1000 And Pattern Risks

- `batch_planning.py` and `batch_patterns.py` calculate `units_per_sheet`, `required_sheets`, produced units, shortage/overproduction, and quantity fulfillment.
- Mixed multi-item quantity reporting is now available in `validator_report.quantity_summary`; current release gates still use compact deterministic cases rather than proving all real mixed-item customer patterns.
- `ProductionPlanPattern.produced_units` is still scalar in the ORM link row, but per-item produced/shortage/overproduction JSON is now carried in pattern and plan validator reports.

## Backlog Status Map

| Backlog item | Status |
| --- | --- |
| A0 架构契约 | Partially complete: `docs/ENTERPRISE_FINALIZATION.md` exists, but this current report identifies missing endpoint contracts and go-live gaps. |
| A1 数据库迁移 | Mostly complete for listed batch/pattern/plan/benchmark objects; future migration needed for first-class solver attempt evidence and optional indexed produced-units fields beyond validator-report JSON. |
| A2 批量文件入口 | Mostly complete: upload/preflight/parse/summary/retry exist; native parser success and resumable 1500/20000 UX still need proof. |
| A3 版图特征和分类 | Foundation complete with real-sample classification fixtures and optional local sample-directory audit; native PDF geometry extraction remains outside this proof. |
| A3 兼容分组 | Foundation complete by material/thickness/print method/spot color/due date/category/customer; hard customer rules need expansion. |
| A3 裁切变体 | Foundation complete for parent/rotated/half/third/quarter/custom model input. |
| A4 MultiSolverOrchestrator | Foundation complete; public method contract and full cut-variant solver matrix need hardening. |
| A4 PackingSolverAdapter | Contract complete; real binary acceptance not proven. |
| A4 SparrowSolverAdapter | Contract complete; real binary acceptance not proven. |
| A5 PatternPlanner | Extracted to `batch_patterns.py` with mixed multi-item quantity summaries and deterministic placement artifacts; broader real-sample and external-solver proof still needed. |
| A5 Top3GlobalPlanSelector | Extracted with `ProductionPlanBuilder`; stronger diversity/validity proof over real mixed irregular shapes still needed. |
| A6 Benchmark gate | Foundation complete for OR/787/MOQ, generated batch-1500/20000 endpoint coverage, full local 1500+20000 slow-gate artifact, and explicit synthetic/real labels. |
| A6 OR-Datasets importer | Present. |
| A7 前端批量页面 | Foundation present as one workbench with retry and batch-20000 controls; dedicated enterprise pages, virtualization, and history views still missing. |
| A8 AI工具扩展 | Batch workflow tools now cover query/features/create/run/Top3/report with RBAC gates; production export remains blocked and go-live docs/evidence still need final closure. |
| A8 上线文档 | Many docs/scripts exist; current go-live report should be extended after remaining blockers close. |

## Recommended Next Implementation Order

1. Add configured PackingSolver/Sparrow binary acceptance evidence and map accepted external placements into `ProductionPattern` artifacts.
2. Add native PDF/conversion-supplier acceptance evidence before treating PDF die-lines as production geometry.
3. Broaden real mixed-irregular pattern proof beyond deterministic bbox template artifacts.
4. Split frontend batch workbench into dedicated enterprise views with virtualization/history when 1500-20000 rows are active.
5. Promote high-value solver attempt evidence from JSON logs into indexed first-class fields or artifact keys.

## Current Verification Snapshot

This report and the contract patches made with it were verified with:

- `$env:PYTHONPATH='backend'; python -m pytest -q tests\backend`: 480 passed, 2 skipped, 1 warning.
- `python -m ruff check backend tests scripts`: passed.
- `npm.cmd run build` from `frontend/`: passed.
- `python scripts\benchmark_release_gate.py --output tmp\benchmark-release-gate-ai-playbook.json`: passed, 7 cases, 0 errors, P95 23 ms.
- `python scripts\release_preflight.py --include-slow-batch-gates --real-sample-root "D:\大卖数智AI部\包装印刷\甘-包装样例" --hash-real-sample-files --report-path tmp\release-preflight-ai-playbook.json`: passed; included 400 backend release-gate tests, benchmark gate, full 1500/20000 slow gates, 6 hashed real sample cases, release evidence pack verification, frontend build, and API smoke.
- `pytest -q tests\backend\test_batch_layout_planning.py tests\backend\test_batch_layout_api.py tests\backend\test_migrations.py`: passed, 8 tests.
- `python scripts\benchmark_release_gate.py --output tmp\benchmark-release-gate-placement-artifacts.json`: passed, 7 cases, 0 errors, P95 43 ms.
- `python scripts\enterprise_batch_slow_gates.py --output artifacts\enterprise-batch-slow-gates-placement-artifacts-full.json --batch-1500-count 1500 --batch-20000-count 20000 --real-sample-root "D:\大卖数智AI部\包装印刷\甘-包装样例" --hash-real-sample-files`: passed, 3 gates, 21,500 generated files, 6 real sample cases, 0 errors, P95 50,927 ms, wall time 122,321 ms.
- `python scripts\release_preflight.py --include-slow-batch-gates --real-sample-root "D:\大卖数智AI部\包装印刷\甘-包装样例" --hash-real-sample-files --report-path tmp\release-preflight-placement-artifacts.json`: passed, 8 gates including 400 backend release tests, benchmark gate, full 1500/20000 slow gates, release evidence pack verification, frontend build, and API smoke.
- `python scripts\benchmark_release_gate.py --output tmp\benchmark-release-gate-slow-gates.json`: passed, 7 cases, 0 errors, P95 27 ms.
- `python scripts\audit_real_sample_classification.py --require-files --output tmp\real-sample-classification-audit.json`: passed, 6 cases, 6 classification matches, 0 missing files.
- `pytest -q tests\backend\test_enterprise_batch_slow_gates.py tests\backend\test_release_preflight.py tests\backend\test_verify_release_preflight.py`: passed, 51 tests.
- `python -m ruff check scripts\enterprise_batch_slow_gates.py scripts\release_preflight.py scripts\verify_release_preflight.py tests\backend\test_enterprise_batch_slow_gates.py tests\backend\test_release_preflight.py tests\backend\test_verify_release_preflight.py`: passed.
- `python scripts\enterprise_batch_slow_gates.py --output artifacts\enterprise-batch-slow-gates-full.json --batch-1500-count 1500 --batch-20000-count 20000 --real-sample-root "D:\大卖数智AI部\包装印刷\甘-包装样例" --hash-real-sample-files`: passed, 3 gates, 21,500 generated files, 6 real sample cases, 0 errors, wall time 155,089 ms.
- `$env:PYTHONPATH='backend'; python -m pytest -q tests\backend\test_ai_tools.py`: passed, 5 tests.
- `python -m ruff check backend\app\services\ai_tools.py tests\backend\test_ai_tools.py`: passed.
- `git diff --check`: no whitespace errors; Windows line-ending warnings only.

This report is an audit artifact plus a contract-alignment record. Any implementation changes after this report must rerun the relevant backend/frontend/release gates before being considered complete.
