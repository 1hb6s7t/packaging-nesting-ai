# Batch AI Tools Operator Playbook

This playbook describes the controlled AI tool surface for batch artwork and batch layout workflows. It is an operator guide, not a production-export shortcut.

## Entry Points

- Tool catalog: `GET /api/ai/tools`
- Tool planner: `POST /api/ai/chat`
- Tool execution: `POST /api/ai/tools/execute`

All endpoints require `Authorization: Bearer <token>` and `ai:use`. Batch write tools also require `batch:write`.

## Tool Surface

| Tool | Permissions | Mutates | Purpose |
| --- | --- | --- | --- |
| `get_batch_summary` | `ai:use` | No | Read deterministic batch status, format, and classification counts. |
| `get_batch_features` | `ai:use` | No | Read feature summaries without raw geometry or production coordinates. |
| `create_batch_layout_job` | `ai:use`, `batch:write` | Yes | Create a backend batch layout job from a stored parsed batch. |
| `run_batch_layout_job` | `ai:use`, `batch:write` | Yes | Run backend grouping, cut variants, solver evidence, Validator, and Top3 planning. |
| `compare_batch_top3` | `ai:use` | No | Compare stored Top3 production plans using backend metrics only. |
| `generate_batch_report` | `ai:use` | No | Generate a stored-data report with plan metrics, blocker reasons, and safety flags. |

## Role Matrix

| Role | Expected permissions | Allowed AI workflow |
| --- | --- | --- |
| Viewer or auditor | `ai:use` | Read batch summaries, features, Top3 metrics, and reports. |
| Print planner | `ai:use`, `batch:write` | Create and run batch layout jobs, then hand off plans for human approval. |
| Approver | Normal approval/export permissions outside AI | Review and approve plans through the batch layout approval workflow, not through AI tools. |
| Production operator | Normal export permissions outside AI | Export only after Validator success, approval, export permission, and confirmation phrase. |

## Operator Flow

1. Upload, preflight, and parse artwork through `/api/batch-artworks/*` or the batch workbench.
2. Use `get_batch_summary` to confirm parsed, failed, conversion-required, and manual-review counts.
3. Use `get_batch_features` to inspect classification and feature summaries. This returns bbox summaries only, not production placement coordinates.
4. Use `create_batch_layout_job` only when the batch is ready to plan and the operator has `batch:write`.
5. Use `run_batch_layout_job` to invoke backend grouping, pattern planning, Validator checks, and Top3 selection.
6. Use `compare_batch_top3` to compare the three stored plans by utilization, risk, runtime, diversity, quantity fulfillment, and hard-rule status.
7. Use `generate_batch_report` to produce a read-only report with blocker reasons and safety flags.
8. Hand off the selected plan to the normal approval and export workflow. AI tools do not approve or export.

## Example Calls

Read summary:

```json
{
  "tool_name": "get_batch_summary",
  "arguments": {
    "batch_id": "BATCH_ID"
  }
}
```

Filter parsed feature rows:

```json
{
  "tool_name": "get_batch_features",
  "arguments": {
    "batch_id": "BATCH_ID",
    "status": "parsed",
    "classification": "FILLER",
    "limit": 500
  }
}
```

Create a job:

```json
{
  "tool_name": "create_batch_layout_job",
  "arguments": {
    "batch_id": "BATCH_ID",
    "moq_per_item": 1000,
    "top_k": 3,
    "sheet_parent": {
      "parent_id": "PARENT_787_1092",
      "width": 787,
      "height": 1092
    }
  }
}
```

Run and compare:

```json
{
  "tool_name": "run_batch_layout_job",
  "arguments": {
    "job_id": "BATCH_LAYOUT_JOB_ID"
  }
}
```

```json
{
  "tool_name": "compare_batch_top3",
  "arguments": {
    "job_id": "BATCH_LAYOUT_JOB_ID"
  }
}
```

Generate the report:

```json
{
  "tool_name": "generate_batch_report",
  "arguments": {
    "job_id": "BATCH_LAYOUT_JOB_ID"
  }
}
```

## Safety Boundary

- AI tools do not generate production coordinates.
- AI tools do not approve production plans.
- AI tools do not export production PDF, DXF, JSON, or other manufacturing artifacts.
- AI tools do not bypass Validator, plan approval, export permission, confirmation phrase, CRM/MES/ERP adapters, or operation logs.
- AI write tools require both `ai:use` and the underlying workflow permission, currently `batch:write`.
- Batch reports mark `production_export_allowed=false` and `requires_approval_before_export=true`.
- Export remains available only through the normal Validator, approval, permission, and confirmation workflow.

The blocked AI tools remain blocked inside the AI boundary:

| Blocked action | Required workflow instead |
| --- | --- |
| `create_nesting_job` | Human-selected verified PolygonAsset inputs and normal nesting-job API. |
| `export_pdf` | Approved solution export workflow with confirmation phrase. |
| `export_dxf` | Approved solution export workflow with confirmation phrase. |
| `write_back_crm` | Configured adapter workflow and auditable confirmation path. |

## Failure Handling

- Missing `ai:use` returns an authentication or authorization failure at the route layer.
- Missing `batch:write` returns an AI tool result with `status=failed` and a message containing `batch:write`.
- Failed parsing, conversion-required files, manual-review items, no plans, no hard-rule-passing plans, or quantity shortfalls appear in `generate_batch_report.report.blocked_reasons`.
- A failed or blocked AI tool result must be resolved in the underlying workflow; do not retry by asking AI to invent coordinates or bypass export gates.

## Audit Checks

Every tool execution writes `operation_log` with:

- `action=ai.tool.execute`
- `target_id=<tool_name>`
- execution status and message
- sanitized arguments
- safety fields including `ai_generated_coordinates=false`

Operators should verify that any batch run used for release has operation-log evidence and that exported manufacturing files were created by the normal approval/export workflow, not by AI tools.

## Verification

The contract is covered by:

```powershell
$env:PYTHONPATH='backend'
pytest -q tests\backend\test_ai_tools.py
python -m ruff check backend\app\services\ai_tools.py tests\backend\test_ai_tools.py
```
