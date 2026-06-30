# Batch AI Tools

This document records the controlled AI tool surface for batch artwork and batch layout workflows.

## Tool Surface

| Tool | Permissions | Mutates | Purpose |
| --- | --- | --- | --- |
| `get_batch_summary` | `ai:use` | No | Read deterministic batch status, format, and classification counts. |
| `get_batch_features` | `ai:use` | No | Read feature summaries without raw geometry or production coordinates. |
| `create_batch_layout_job` | `ai:use`, `batch:write` | Yes | Create a backend batch layout job from a stored parsed batch. |
| `run_batch_layout_job` | `ai:use`, `batch:write` | Yes | Run backend grouping, cut variants, solver evidence, Validator, and Top3 planning. |
| `compare_batch_top3` | `ai:use` | No | Compare stored Top3 production plans using backend metrics only. |
| `generate_batch_report` | `ai:use` | No | Generate a stored-data report with plan metrics, blocker reasons, and safety flags. |

## Safety Boundary

- AI tools do not generate production coordinates.
- AI tools do not approve production plans.
- AI tools do not export production PDF, DXF, JSON, or other manufacturing artifacts.
- AI write tools require both `ai:use` and the underlying workflow permission, currently `batch:write`.
- Batch reports mark `production_export_allowed=false` and `requires_approval_before_export=true`.
- Export remains available only through the normal Validator, approval, permission, and confirmation workflow.

## Verification

The contract is covered by `tests/backend/test_ai_tools.py::test_ai_batch_tools_run_top3_pipeline_and_gate_write_permissions`.
