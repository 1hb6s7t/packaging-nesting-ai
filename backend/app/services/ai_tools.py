from __future__ import annotations

from typing import Any, Callable

from sqlalchemy.orm import Session

from app.domain.schemas import AiToolCallResult, AiToolDefinition, NestingSolution
from app.services import repository
from app.services.reports import generate_solution_report
from app.services.store import store
from app.services.validator import validate_solution as run_validation
from app.services.workflows import get_solution_and_job, run_nesting_job


AI_TOOL_DEFINITIONS = [
    AiToolDefinition(
        name="search_orders",
        description="Search production orders using deterministic backend filters.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "material": {"type": "string"},
                "thickness": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
            },
        },
    ),
    AiToolDefinition(
        name="get_order_detail",
        description="Fetch one production order by id.",
        parameters={"type": "object", "required": ["order_id"], "properties": {"order_id": {"type": "string"}}},
    ),
    AiToolDefinition(
        name="get_artwork_geometry",
        description="Return normalized Polygon JSON for an artwork file.",
        parameters={"type": "object", "required": ["artwork_file_id"], "properties": {"artwork_file_id": {"type": "string"}}},
    ),
    AiToolDefinition(
        name="get_sheet_specs",
        description="List configured sheet specifications.",
        parameters={"type": "object", "properties": {}},
    ),
    AiToolDefinition(
        name="create_nesting_job",
        description="Create a NestingJob JSON. The model must not invent coordinates.",
        parameters={
            "type": "object",
            "required": ["sheet_id", "candidate_order_ids"],
            "properties": {
                "sheet_id": {"type": "string"},
                "candidate_order_ids": {"type": "array", "items": {"type": "string"}},
            },
        },
    ),
    AiToolDefinition(
        name="run_solver",
        description="Run an approved SolverAdapter against a stored NestingJob.",
        parameters={"type": "object", "required": ["job_id"], "properties": {"job_id": {"type": "string"}, "solver_name": {"type": "string"}}},
    ),
    AiToolDefinition(
        name="validate_solution",
        description="Run Validator checks. Required before export.",
        parameters={"type": "object", "required": ["solution_id"], "properties": {"solution_id": {"type": "string"}}},
    ),
    AiToolDefinition(
        name="compare_solutions",
        description="Compare Top-K solutions using backend metrics only.",
        parameters={"type": "object", "required": ["job_id"], "properties": {"job_id": {"type": "string"}}},
    ),
    AiToolDefinition(
        name="explain_unplaced_items",
        description="Explain unplaced item reasons from Solver/Validator outputs.",
        parameters={"type": "object", "required": ["solution_id"], "properties": {"solution_id": {"type": "string"}}},
    ),
    AiToolDefinition(
        name="generate_report",
        description="Generate a utilization and cost report from stored solution data.",
        parameters={"type": "object", "required": ["solution_id"], "properties": {"solution_id": {"type": "string"}}},
    ),
    AiToolDefinition(
        name="export_pdf",
        description="Export an approved and validated solution to PDF.",
        parameters={"type": "object", "required": ["solution_id"], "properties": {"solution_id": {"type": "string"}}},
    ),
    AiToolDefinition(
        name="export_dxf",
        description="Export an approved and validated solution to DXF.",
        parameters={"type": "object", "required": ["solution_id"], "properties": {"solution_id": {"type": "string"}}},
    ),
    AiToolDefinition(
        name="write_back_crm",
        description="Write back solution status through the configured CRM Adapter and audit the confirmation.",
        parameters={"type": "object", "required": ["solution_id"], "properties": {"solution_id": {"type": "string"}}},
    ),
]

AI_TOOL_MAP = {tool.name: tool for tool in AI_TOOL_DEFINITIONS}

BASE_SAFETY = {
    "mode": "controlled_tool_execution",
    "ai_generated_coordinates": False,
    "production_export_allowed": False,
}


def execute_ai_tool(
    db: Session,
    *,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
    actor_id: str | None = None,
) -> AiToolCallResult:
    args = arguments or {}
    executor = AI_TOOL_EXECUTORS.get(tool_name)
    if executor is None:
        result = _failed(tool_name, f"unknown AI tool: {tool_name}")
    else:
        try:
            result = executor(db, args, actor_id)
        except Exception as exc:
            result = _failed(tool_name, str(exc))
    repository.log_operation(
        db,
        action="ai.tool.execute",
        target_type="ai_tool",
        target_id=tool_name,
        actor_id=actor_id,
        payload={
            "status": result.status,
            "message": result.message,
            "arguments": _sanitize_arguments(args),
            "safety": result.safety,
        },
    )
    return result


def plan_ai_tool_calls(message: str) -> list[dict[str, Any]]:
    text = message.lower()
    planned: list[dict[str, Any]] = []
    if any(token in text for token in ("order", "orders", "订单", "查询", "查找")):
        planned.append({"tool_name": "search_orders", "arguments": {"query": message}})
    if any(token in text for token in ("sheet", "纸张", "纸板", "规格")):
        planned.append({"tool_name": "get_sheet_specs", "arguments": {}})
    if any(token in text for token in ("solver", "solve", "run", "求解", "排版", "拼版")):
        planned.append({"tool_name": "run_solver", "arguments": {"job_id": "<nesting_job_id>"}})
    if any(token in text for token in ("compare", "对比", "比较")):
        planned.append({"tool_name": "compare_solutions", "arguments": {"job_id": "<nesting_job_id>"}})
    if any(token in text for token in ("unplaced", "未放", "未排", "原因")):
        planned.append({"tool_name": "explain_unplaced_items", "arguments": {"solution_id": "<solution_id>"}})
    if any(token in text for token in ("report", "报告", "成本", "利用率")):
        planned.append({"tool_name": "generate_report", "arguments": {"solution_id": "<solution_id>"}})
    if not planned:
        planned.append({"tool_name": "search_orders", "arguments": {"query": message}})
    return planned


def _execute_search_orders(db: Session, args: dict[str, Any], actor_id: str | None) -> AiToolCallResult:
    query = str(args.get("query") or "").strip().lower()
    material = str(args.get("material") or "").strip().lower()
    thickness = str(args.get("thickness") or "").strip().lower()
    limit = _bounded_limit(args.get("limit"), default=20)
    orders = repository.list_orders(db) or list(store.orders.values())
    matched = []
    for order in orders:
        if material and order.material.lower() != material:
            continue
        if thickness and order.thickness.lower() != thickness:
            continue
        if query and query not in _order_search_text(order):
            continue
        matched.append(order)
    return _completed(
        "search_orders",
        {
            "count": len(matched[:limit]),
            "total_matches": len(matched),
            "orders": [order.model_dump(mode="json") for order in matched[:limit]],
        },
        f"matched {len(matched)} production order(s)",
        {"read_only": True},
    )


def _execute_get_order_detail(db: Session, args: dict[str, Any], actor_id: str | None) -> AiToolCallResult:
    order_id = _require_string(args, "order_id")
    order = repository.get_order(db, order_id) or store.orders.get(order_id)
    if order is None:
        raise ValueError("order not found")
    return _completed("get_order_detail", {"order": order.model_dump(mode="json")}, "order detail loaded", {"read_only": True})


def _execute_get_artwork_geometry(db: Session, args: dict[str, Any], actor_id: str | None) -> AiToolCallResult:
    artwork_id = _require_string(args, "artwork_file_id")
    polygons = repository.get_polygons(db, artwork_id) or store.polygons.get(artwork_id, [])
    if not polygons and not (repository.get_artwork_meta(db, artwork_id) or store.artworks.get(artwork_id)):
        raise ValueError("artwork not found")
    return _completed(
        "get_artwork_geometry",
        {"artwork_file_id": artwork_id, "polygon_count": len(polygons), "polygons": [item.model_dump(mode="json") for item in polygons]},
        "artwork geometry loaded",
        {"read_only": True, "coordinates_source": "stored_polygon_json"},
    )


def _execute_get_sheet_specs(db: Session, args: dict[str, Any], actor_id: str | None) -> AiToolCallResult:
    sheets = repository.list_sheets(db) or list(store.sheets.values())
    return _completed(
        "get_sheet_specs",
        {"count": len(sheets), "sheets": [sheet.model_dump(mode="json") for sheet in sheets]},
        f"loaded {len(sheets)} sheet spec(s)",
        {"read_only": True},
    )


def _execute_run_solver(db: Session, args: dict[str, Any], actor_id: str | None) -> AiToolCallResult:
    job_id = _require_string(args, "job_id")
    requested_solver = str(args.get("solver_name") or "").strip()
    job = repository.get_job(db, job_id) or store.jobs.get(job_id)
    if job is None:
        raise ValueError("job not found")
    configured_solver = _solver_name(job.solver_config.solver_name)
    if requested_solver and requested_solver not in {configured_solver, str(job.solver_config.solver_name)}:
        raise ValueError(f"job is configured for {configured_solver}; update the job before requesting {requested_solver}")
    solution_list = run_nesting_job(db, job_id, actor_id=actor_id)
    return _completed(
        "run_solver",
        {
            "job_id": solution_list.job_id,
            "solution_count": len(solution_list.solutions),
            "solutions": [_solution_summary(solution) for solution in solution_list.solutions],
        },
        f"solver completed with {len(solution_list.solutions)} solution(s)",
        {"mutates": True, "coordinates_source": "backend_solver", "requires_validator_for_export": True},
    )


def _execute_validate_solution(db: Session, args: dict[str, Any], actor_id: str | None) -> AiToolCallResult:
    solution_id = _require_string(args, "solution_id")
    solution, job = get_solution_and_job(db, solution_id)
    report = run_validation(job, solution)
    solution.validation_report = report
    solution.status = "valid" if report.is_valid else "invalid"
    store.solutions[solution_id] = solution
    repository.update_solution(db, solution)
    repository.log_operation(
        db,
        action="solution.validate",
        target_type="nesting_solution",
        target_id=solution_id,
        actor_id=actor_id,
        payload={"is_valid": report.is_valid, "issue_count": len(report.issues), "source": "ai_tool"},
    )
    return _completed(
        "validate_solution",
        {"solution_id": solution_id, "validation_report": report.model_dump(mode="json"), "status": solution.status},
        "validator completed",
        {"mutates": True, "validator_source": "backend_validator"},
    )


def _execute_compare_solutions(db: Session, args: dict[str, Any], actor_id: str | None) -> AiToolCallResult:
    job_id = _require_string(args, "job_id")
    if repository.get_job(db, job_id) is None and job_id not in store.jobs:
        raise ValueError("job not found")
    solutions = repository.list_job_solutions(db, job_id) or [store.solutions[sid] for sid in store.job_solutions.get(job_id, [])]
    summaries = [_solution_summary(solution) for solution in sorted(solutions, key=lambda item: item.rank)]
    return _completed(
        "compare_solutions",
        {"job_id": job_id, "solution_count": len(summaries), "solutions": summaries},
        f"compared {len(summaries)} solution(s)",
        {"read_only": True, "metrics_source": "stored_solver_results"},
    )


def _execute_explain_unplaced_items(db: Session, args: dict[str, Any], actor_id: str | None) -> AiToolCallResult:
    solution_id = _require_string(args, "solution_id")
    solution = repository.get_solution(db, solution_id) or store.solutions.get(solution_id)
    if solution is None:
        raise ValueError("solution not found")
    explanations = [
        {
            "item_id": item.item_id,
            "order_id": item.order_id,
            "reason": item.reason,
            "category": _unplaced_category(item.reason),
        }
        for item in solution.unplaced_items
    ]
    message = "all candidate items were placed" if not explanations else f"found {len(explanations)} unplaced item(s)"
    return _completed(
        "explain_unplaced_items",
        {"solution_id": solution_id, "placed_count": len(solution.placed_items), "unplaced_items": explanations},
        message,
        {"read_only": True, "explanation_source": "solver_unplaced_reasons"},
    )


def _execute_generate_report(db: Session, args: dict[str, Any], actor_id: str | None) -> AiToolCallResult:
    solution_id = _require_string(args, "solution_id")
    solution, job = get_solution_and_job(db, solution_id)
    report = generate_solution_report(job, solution)
    return _completed(
        "generate_report",
        {"report": report},
        "solution report generated from stored solver and validator data",
        {"read_only": True, "report_source": "backend_report_service"},
    )


def _execute_blocked_production_action(db: Session, args: dict[str, Any], actor_id: str | None) -> AiToolCallResult:
    tool_name = str(args.get("_tool_name") or "unknown")
    messages = {
        "create_nesting_job": "AI-assisted job creation is blocked until verified PolygonAsset inputs are selected by a user.",
        "export_pdf": "Production PDF export requires the solution export workflow and confirmation phrase.",
        "export_dxf": "Production DXF export requires the solution export workflow and confirmation phrase.",
        "write_back_crm": "CRM write-back requires the configured adapter workflow and an auditable confirmation path.",
    }
    return _blocked(
        tool_name,
        messages.get(tool_name, "This AI tool is blocked by the controlled execution policy."),
        {"requires_human_workflow": True},
    )


def _completed(tool_name: str, result: dict[str, Any], message: str, safety: dict[str, Any] | None = None) -> AiToolCallResult:
    return AiToolCallResult(
        tool_name=tool_name,
        status="completed",
        result=result,
        message=message,
        safety={**BASE_SAFETY, **(safety or {})},
    )


def _blocked(tool_name: str, message: str, safety: dict[str, Any] | None = None) -> AiToolCallResult:
    return AiToolCallResult(
        tool_name=tool_name,
        status="blocked",
        result={},
        message=message,
        safety={**BASE_SAFETY, **(safety or {})},
    )


def _failed(tool_name: str, message: str) -> AiToolCallResult:
    return AiToolCallResult(
        tool_name=tool_name,
        status="failed",
        result={},
        message=message,
        safety={**BASE_SAFETY, "error": True},
    )


def _require_string(args: dict[str, Any], key: str) -> str:
    value = str(args.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value


def _bounded_limit(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, 100))


def _order_search_text(order: Any) -> str:
    fields = [
        order.order_id,
        order.external_order_id,
        order.customer_id,
        order.customer_name,
        order.product_id,
        order.product_name,
        order.category,
        order.material,
        order.thickness,
        order.priority_note,
    ]
    return " ".join(str(value).lower() for value in fields if value is not None)


def _solution_summary(solution: NestingSolution) -> dict[str, Any]:
    return {
        "solution_id": solution.solution_id,
        "job_id": solution.job_id,
        "solver": _solver_name(solution.solver),
        "status": solution.status,
        "rank": solution.rank,
        "runtime_ms": solution.runtime_ms,
        "utilization_rate": solution.utilization_rate,
        "waste_rate": solution.waste_rate,
        "placed_count": len(solution.placed_items),
        "unplaced_count": len(solution.unplaced_items),
        "score_total": solution.score.total if solution.score else None,
        "validation_valid": solution.validation_report.is_valid if solution.validation_report else None,
    }


def _solver_name(value: Any) -> str:
    return str(getattr(value, "value", value))


def _unplaced_category(reason: str) -> str:
    lower = reason.lower()
    if "fit" in lower or "space" in lower or "sheet" in lower:
        return "sheet_capacity"
    if "rotation" in lower:
        return "rotation_constraint"
    if "time" in lower or "limit" in lower:
        return "solver_limit"
    return "solver_reported"


def _sanitize_arguments(args: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in args.items():
        if key.startswith("_"):
            continue
        if isinstance(value, str):
            sanitized[key] = value[:300]
        elif isinstance(value, (int, float, bool)) or value is None:
            sanitized[key] = value
        elif isinstance(value, list):
            sanitized[key] = {"type": "list", "length": len(value)}
        elif isinstance(value, dict):
            sanitized[key] = {"type": "object", "keys": sorted(value.keys())[:20]}
        else:
            sanitized[key] = str(type(value).__name__)
    return sanitized


def _blocked_executor(tool_name: str) -> Callable[[Session, dict[str, Any], str | None], AiToolCallResult]:
    def executor(db: Session, args: dict[str, Any], actor_id: str | None) -> AiToolCallResult:
        return _execute_blocked_production_action(db, {**args, "_tool_name": tool_name}, actor_id)

    return executor


AI_TOOL_EXECUTORS: dict[str, Callable[[Session, dict[str, Any], str | None], AiToolCallResult]] = {
    "search_orders": _execute_search_orders,
    "get_order_detail": _execute_get_order_detail,
    "get_artwork_geometry": _execute_get_artwork_geometry,
    "get_sheet_specs": _execute_get_sheet_specs,
    "create_nesting_job": _blocked_executor("create_nesting_job"),
    "run_solver": _execute_run_solver,
    "validate_solution": _execute_validate_solution,
    "compare_solutions": _execute_compare_solutions,
    "explain_unplaced_items": _execute_explain_unplaced_items,
    "generate_report": _execute_generate_report,
    "export_pdf": _blocked_executor("export_pdf"),
    "export_dxf": _blocked_executor("export_dxf"),
    "write_back_crm": _blocked_executor("write_back_crm"),
}
