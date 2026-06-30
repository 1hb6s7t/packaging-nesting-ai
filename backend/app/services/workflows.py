from __future__ import annotations

import json
import hashlib
import time
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.db.session import SessionLocal, init_db
from app.domain.schemas import (
    NestingSolution,
    ProductionPlanExportRead,
    ProductionPlanRead,
    SolutionExportRead,
    SolutionList,
    SolverName,
    WorkTaskRead,
)
from app.services import repository
from app.services.benchmarks import run_and_record_benchmark
from app.services.batch_layout import BatchLayoutService
from app.services.exports import create_solution_export
from app.services.solvers import MultiSolverOrchestrator, SolverOrchestrator
from app.services.storage import write_text
from app.services.store import store


orchestrator = SolverOrchestrator()
multi_solver_orchestrator = MultiSolverOrchestrator(orchestrator)
batch_layout_service = BatchLayoutService()


class WorkTaskCancelled(Exception):
    pass


def run_nesting_job(
    db: Session,
    job_id: str,
    actor_id: str | None = None,
    task_id: str | None = None,
) -> SolutionList:
    _raise_if_cancelled(db, task_id)
    job = repository.get_job(db, job_id) or store.jobs.get(job_id)
    if not job:
        raise ValueError("job not found")
    if _candidate_pool_enabled(job.solver_config.options):
        return _run_nesting_job_candidate_pool(db, job, actor_id=actor_id, task_id=task_id)
    adapter = orchestrator.adapters.get(job.solver_config.solver_name)
    solver_name = job.solver_config.solver_name.value
    repository.ensure_solver_enabled(db, solver_name)
    solver_version = adapter.version if adapter else "unknown"
    run_id = repository.create_solver_run(
        db,
        job_id=job_id,
        solver_name=solver_name,
        solver_version=solver_version,
        config=job.solver_config.model_dump(mode="json"),
    )
    started_at = time.perf_counter()
    try:
        _raise_if_cancelled(db, task_id)
        solutions = orchestrator.solve(job)
        _raise_if_cancelled(db, task_id)
    except Exception as exc:
        repository.fail_solver_run(db, run_id, str(exc), {"job_id": job_id})
        repository.log_operation(
            db,
            action="nesting_job.run_failed",
            target_type="nesting_job",
            target_id=job_id,
            actor_id=actor_id,
            payload={"solver_run_id": run_id, "error": str(exc)},
        )
        raise

    ids: list[str] = []
    for solution in solutions:
        solution.exports["solver_run_id"] = run_id
        store.solutions[solution.solution_id] = solution
        ids.append(solution.solution_id)
    store.job_solutions[job_id] = ids
    repository.save_solutions(db, job_id, solutions, solver_run_id=run_id)
    runtime_ms = int((time.perf_counter() - started_at) * 1000)
    evidence_payloads = []
    for solution in solutions:
        evidence = _solution_attempt_evidence(
            job,
            solution,
            attempt_config={
                "solver_name": solver_name,
                "solver_version": solver_version,
                "seed": job.solver_config.seed,
                "time_limit_sec": job.solver_config.time_limit_sec,
                "candidate_pool_enabled": False,
                "base_solver_config": job.solver_config.model_dump(mode="json"),
            },
        )
        repository.add_solver_run_log(db, run_id, "info", "solver attempt evidence", evidence)
        evidence_payloads.append(evidence)
    repository.complete_solver_run(
        db,
        run_id,
        runtime_ms,
        {
            "job_id": job_id,
            "solution_ids": ids,
            "solution_count": len(solutions),
            "attempt_evidence": evidence_payloads,
        },
    )
    repository.log_operation(
        db,
        action="nesting_job.run",
        target_type="nesting_job",
        target_id=job_id,
        actor_id=actor_id,
        payload={"solver_run_id": run_id, "solution_ids": ids},
    )
    return SolutionList(job_id=job_id, solutions=solutions)


def _run_nesting_job_candidate_pool(
    db: Session,
    job,
    *,
    actor_id: str | None = None,
    task_id: str | None = None,
) -> SolutionList:
    job_id = job.job_id
    repository.ensure_solver_enabled(db, job.solver_config.solver_name.value)
    options = job.solver_config.options
    started_at = time.perf_counter()
    all_solutions: list[NestingSolution] = []
    try:
        _raise_if_cancelled(db, task_id)
        all_solutions = multi_solver_orchestrator.solve_candidate_pool(
            job,
            solver_names=_solver_names_from_options(options),
            seeds=_int_or_none_list(options.get("candidate_pool_seeds")),
            time_limits_sec=_int_list(options.get("candidate_pool_time_limits_sec")),
            rotation_policies=_str_list(options.get("candidate_pool_rotation_policies")),
            max_runs=_optional_int(options.get("candidate_pool_max_runs")),
        )
        _raise_if_cancelled(db, task_id)
    except Exception as exc:
        repository.log_operation(
            db,
            action="nesting_job.run_failed",
            target_type="nesting_job",
            target_id=job_id,
            actor_id=actor_id,
            payload={"candidate_pool_enabled": True, "error": str(exc)},
        )
        raise

    for solution in all_solutions:
        _persist_solver_attempt_run(db, job, solution)

    solutions = all_solutions[: job.top_k]
    ids: list[str] = []
    for solution in solutions:
        store.solutions[solution.solution_id] = solution
        ids.append(solution.solution_id)
    store.job_solutions[job_id] = ids
    repository.save_solutions(db, job_id, solutions)
    runtime_ms = int((time.perf_counter() - started_at) * 1000)
    pool_report = multi_solver_orchestrator.candidate_pool_report(all_solutions)
    repository.log_operation(
        db,
        action="nesting_job.run",
        target_type="nesting_job",
        target_id=job_id,
        actor_id=actor_id,
        payload={
            "candidate_pool_enabled": True,
            "attempt_count": len(all_solutions),
            "solution_ids": ids,
            "runtime_ms": runtime_ms,
            "candidate_pool": pool_report,
        },
    )
    return SolutionList(job_id=job_id, solutions=solutions)


def _persist_solver_attempt_run(db: Session, job, solution: NestingSolution) -> str:
    manifest = _json_dict(solution.exports.get("audit_manifest_json"))
    solver_name = str(manifest.get("solver_name") or solution.solver)
    solver_version = str(manifest.get("solver_version") or solution.exports.get("solver_version") or "unknown")
    attempt_config = {
        "candidate_id": solution.exports.get("candidate_id") or solution.solution_id,
        "solver_name": solver_name,
        "solver_version": solver_version,
        "seed": manifest.get("seed"),
        "time_limit_sec": manifest.get("time_limit_sec"),
        "rotation_policy": manifest.get("rotation_policy"),
        "candidate_pool_enabled": True,
        "base_solver_config": job.solver_config.model_dump(mode="json"),
    }
    evidence = _solution_attempt_evidence(job, solution, attempt_config=attempt_config, audit_manifest=manifest)
    run_id = repository.create_solver_run(
        db,
        job_id=job.job_id,
        solver_name=solver_name,
        solver_version=solver_version,
        config={
            "seed": _optional_int(attempt_config["seed"]),
            "candidate_pool_attempt": attempt_config,
            "input_hash": evidence["input_hash"],
        },
    )
    repository.add_solver_run_log(db, run_id, "info", "solver attempt evidence", evidence)
    if solution.status == "failed":
        repository.fail_solver_run(db, run_id, _failure_reason(solution), evidence)
    else:
        repository.complete_solver_run(db, run_id, solution.runtime_ms, evidence)
    solution.exports["solver_run_id"] = run_id
    return run_id


def _solution_attempt_evidence(
    job,
    solution: NestingSolution,
    *,
    attempt_config: dict[str, Any],
    audit_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    input_snapshot = job.model_dump(mode="json")
    input_payload = json.dumps(input_snapshot, sort_keys=True, ensure_ascii=False)
    return {
        "solution_id": solution.solution_id,
        "candidate_id": attempt_config.get("candidate_id", solution.solution_id),
        "rank": solution.rank,
        "status": solution.status,
        "input_hash": hashlib.sha256(input_payload.encode("utf-8")).hexdigest(),
        "input_payload_sha256": solution.exports.get("input_payload_sha256", ""),
        "input_snapshot": input_snapshot,
        "attempt_config": attempt_config,
        "command": _json_list(solution.exports.get("command_json")),
        "cli_result": _json_dict(solution.exports.get("cli_result_json")),
        "cli_status": solution.exports.get("cli_status", ""),
        "exit_code": solution.exports.get("exit_code", ""),
        "error_message": solution.exports.get("error_message", ""),
        "stdout": solution.exports.get("stdout", ""),
        "stderr": solution.exports.get("stderr", ""),
        "stdout_sha256": solution.exports.get("stdout_sha256", ""),
        "stderr_sha256": solution.exports.get("stderr_sha256", ""),
        "certificate": _json_dict(solution.exports.get("certificate_json")),
        "external_certificate": _json_dict(solution.exports.get("external_certificate_json")),
        "validator_report": solution.validation_report.model_dump(mode="json") if solution.validation_report else None,
        "score": solution.score.model_dump(mode="json") if solution.score else None,
        "audit_manifest": audit_manifest or _json_dict(solution.exports.get("audit_manifest_json")),
    }


def _candidate_pool_enabled(options: dict[str, Any]) -> bool:
    value = options.get("candidate_pool_enabled", False)
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _solver_names_from_options(options: dict[str, Any]) -> list[SolverName] | None:
    value = options.get("candidate_pool_solvers")
    if value is None:
        return None
    if not isinstance(value, list):
        return None
    names: list[SolverName] = []
    for item in value:
        try:
            names.append(SolverName(str(item)))
        except ValueError:
            continue
    return names or None


def _int_or_none_list(value: Any) -> list[int | None] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        return None
    result: list[int | None] = []
    for item in value:
        if item is None or item == "":
            result.append(None)
            continue
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result or None


def _int_list(value: Any) -> list[int] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        return None
    result: list[int] = []
    for item in value:
        try:
            result.append(max(1, int(item)))
        except (TypeError, ValueError):
            continue
    return result or None


def _str_list(value: Any) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list):
        return None
    result = [str(item) for item in value if str(item).strip()]
    return result or None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _json_dict(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _json_list(value: str | None) -> list[Any]:
    if not value:
        return []
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return []
    return payload if isinstance(payload, list) else []


def _failure_reason(solution: NestingSolution) -> str:
    if solution.unplaced_items:
        return solution.unplaced_items[0].reason
    return f"solver attempt failed: {solution.status}"


def export_solution(
    db: Session,
    solution_id: str,
    export_type: str,
    actor_id: str | None = None,
    task_id: str | None = None,
) -> SolutionExportRead:
    _raise_if_cancelled(db, task_id)
    solution, job = get_solution_and_job(db, solution_id)
    ensure_approved_solution(solution)
    _raise_if_cancelled(db, task_id)
    artifact = create_solution_export(job, solution, export_type)
    _raise_if_cancelled(db, task_id)
    export = repository.create_solution_export_record(
        db,
        export_id=artifact.export_id,
        solution_id=solution_id,
        export_type=artifact.export_type,
        storage_key=artifact.storage_key,
        checksum=artifact.checksum,
        storage_backend=artifact.storage_backend,
        storage_object_key=artifact.storage_object_key,
        storage_version_id=artifact.storage_version_id,
        storage_etag=artifact.storage_etag,
        storage_size_bytes=artifact.storage_size_bytes,
    )
    solution.exports[export_type] = export.id
    solution.exports[f"{export_type}_download_path"] = export.download_path
    store.solutions[solution_id] = solution
    repository.update_solution(db, solution)
    repository.log_operation(
        db,
        action=f"solution.export_{export_type}",
        target_type="nesting_solution",
        target_id=solution_id,
        actor_id=actor_id,
        payload={
            "export_id": export.id,
            "export_type": export.export_type,
            "version": export.version,
            "lifecycle_status": export.lifecycle_status,
            "storage_key": export.storage_key,
            "checksum": export.checksum,
            "storage_backend": export.storage_backend,
            "storage_object_key": export.storage_object_key,
            "storage_version_id": export.storage_version_id,
            "storage_etag": export.storage_etag,
            "storage_size_bytes": export.storage_size_bytes,
        },
    )
    return export


def export_production_plan(
    db: Session,
    plan_id: str,
    actor_id: str | None = None,
    task_id: str | None = None,
) -> ProductionPlanExportRead:
    _raise_if_cancelled(db, task_id)
    plan = batch_layout_service.get_plan(db, plan_id)
    if plan is None:
        raise ValueError("production plan not found")
    ensure_approved_production_plan(plan)
    _raise_if_cancelled(db, task_id)
    approvals = [approval.model_dump(mode="json") for approval in repository.list_production_plan_approvals(db, plan_id)]
    export_id = f"pexp_{uuid4().hex[:16]}"
    manifest = {
        "schema_version": 1,
        "export_id": export_id,
        "plan": plan.model_dump(mode="json"),
        "approvals": approvals,
        "validator_report": plan.validator_report,
        "audit_manifest": plan.audit_manifest,
        "coordinates_source": "deterministic_system_plan_not_ai_generated",
    }
    text = json.dumps(manifest, ensure_ascii=False, indent=2)
    checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
    stored = write_text(
        f"exports/production-plans/{plan_id}/{export_id}.json",
        text,
        content_type="application/json; charset=utf-8",
    )
    export = repository.create_production_plan_export_record(
        db,
        export_id=export_id,
        plan_id=plan_id,
        export_type="json",
        storage_key=stored.storage_key,
        checksum=checksum,
        storage_backend=stored.backend,
        storage_object_key=stored.object_key,
        storage_version_id=stored.version_id,
        storage_etag=stored.etag,
        storage_size_bytes=stored.size,
    )
    repository.log_operation(
        db,
        action="production_plan.export_json",
        target_type="production_plan",
        target_id=plan_id,
        actor_id=actor_id,
        payload={
            "export_id": export.id,
            "version": export.version,
            "storage_key": export.storage_key,
            "checksum": export.checksum,
        },
    )
    return export


def archive_expired_solution_exports(
    db: Session,
    *,
    solution_id: str | None = None,
    dry_run: bool = False,
    actor_id: str | None = None,
    task_id: str | None = None,
) -> dict[str, Any]:
    _raise_if_cancelled(db, task_id)
    result = repository.archive_expired_solution_exports(db, solution_id=solution_id, dry_run=dry_run)
    repository.log_operation(
        db,
        action="solution.exports.archive_expired",
        target_type="solution_export",
        target_id=solution_id,
        actor_id=actor_id,
        payload=result.model_dump(mode="json"),
    )
    return result.model_dump(mode="json")


def get_solution_and_job(db: Session, solution_id: str):
    solution = repository.get_solution(db, solution_id) or store.solutions.get(solution_id)
    if not solution:
        raise ValueError("solution not found")
    job = repository.get_job(db, solution.job_id) or store.jobs.get(solution.job_id)
    if not job:
        raise ValueError("job not found")
    return solution, job


def ensure_valid_solution(solution: NestingSolution) -> None:
    if not solution.validation_report or not solution.validation_report.is_valid:
        raise ValueError("solution must pass Validator first")


def ensure_approved_solution(solution: NestingSolution) -> None:
    ensure_valid_solution(solution)
    if solution.status != "approved":
        raise ValueError("solution must be approved before production export")


def ensure_valid_production_plan(plan: ProductionPlanRead) -> None:
    if not plan.hard_rule_pass or not plan.export_ok:
        raise ValueError("production plan must pass Validator and export gates first")
    veto = plan.validator_report.get("veto") if isinstance(plan.validator_report, dict) else None
    if isinstance(veto, dict):
        required = [
            "no_overlap",
            "inside_printable_area",
            "gripper_clear",
            "min_gap_ok",
            "rotation_ok",
            "material_rule_ok",
            "export_ok",
        ]
        failed = [name for name in required if veto.get(name) is not True]
        quantity_rate = float(veto.get("quantity_fulfillment_rate", plan.quantity_fulfillment_rate) or 0)
        if failed or quantity_rate < 1:
            raise ValueError("production plan hard constraints are not fully satisfied")
    if any(not pattern.hard_rule_pass for pattern in plan.patterns):
        raise ValueError("all production patterns must pass Validator before approval")


def ensure_approved_production_plan(plan: ProductionPlanRead) -> None:
    ensure_valid_production_plan(plan)
    if plan.status != "approved":
        raise ValueError("production plan must be approved before production export")


def execute_work_task(task_id: str) -> WorkTaskRead:
    init_db()
    with SessionLocal() as db:
        task = repository.start_work_task(db, task_id)
        if task is None:
            raise ValueError("task not found")
        if task.status != "running":
            return task
        started_at = time.perf_counter()
        repository.heartbeat_work_task(db, task_id, 10)
        try:
            _raise_if_cancelled(db, task_id)
            result = _execute_task_payload(db, task)
        except WorkTaskCancelled as exc:
            cancelled = repository.cancel_work_task(db, task_id, str(exc))
            if cancelled is None:
                raise
            _notify_task_issue(db, cancelled, "后台任务已取消", cancelled.error or "task cancelled")
            return cancelled
        except Exception as exc:
            failed = repository.fail_work_task(db, task_id, str(exc))
            if failed is None:
                raise
            _notify_task_issue(db, failed, "后台任务失败", str(exc))
            return failed
        repository.heartbeat_work_task(db, task_id, 90)
        elapsed_sec = time.perf_counter() - started_at
        if task.timeout_sec is not None and elapsed_sec > task.timeout_sec:
            timed_out = repository.timeout_work_task(db, task_id, elapsed_sec, result)
            if timed_out is None:
                raise ValueError("task not found")
            _notify_task_issue(db, timed_out, "后台任务超时", timed_out.error or "task timed out")
            return timed_out
        completed = repository.complete_work_task(db, task_id, result)
        if completed is None:
            raise ValueError("task not found")
        return completed


def _execute_task_payload(db: Session, task: WorkTaskRead) -> dict[str, Any]:
    if task.task_type == "nesting.solve":
        solution_list = run_nesting_job(db, task.target_id, actor_id=task.actor_id, task_id=task.id)
        return {
            "job_id": solution_list.job_id,
            "solution_ids": [solution.solution_id for solution in solution_list.solutions],
            "solution_count": len(solution_list.solutions),
        }
    if task.task_type == "solution.export":
        export_type = str(task.payload.get("export_type") or "")
        export = export_solution(db, task.target_id, export_type, actor_id=task.actor_id, task_id=task.id)
        return export.model_dump(mode="json")
    if task.task_type == "solution.export_archive_expired":
        solution_id = task.payload.get("solution_id")
        dry_run = bool(task.payload.get("dry_run", False))
        return archive_expired_solution_exports(
            db,
            solution_id=str(solution_id) if solution_id else None,
            dry_run=dry_run,
            actor_id=task.actor_id,
            task_id=task.id,
        )
    if task.task_type == "maintenance.run":
        from app.domain import schemas
        from app.services.maintenance import run_scheduled_maintenance

        request = schemas.ScheduledMaintenanceRunRequest.model_validate(task.payload or {})
        result = run_scheduled_maintenance(db, request=request, actor_id=task.actor_id, task_id=task.id)
        return result.model_dump(mode="json")
    if task.task_type == "benchmark.run":
        _raise_if_cancelled(db, task.id)
        case_id = str(task.payload.get("case_id") or task.target_id)
        solver_name = SolverName(str(task.payload.get("solver_name") or SolverName.rectpack.value))
        case = repository.get_benchmark_case(db, case_id)
        if case is None:
            raise ValueError("benchmark case not found")
        _raise_if_cancelled(db, task.id)
        result = run_and_record_benchmark(db, case, solver_name, cancel_check=lambda: _raise_if_cancelled(db, task.id))
        _raise_if_cancelled(db, task.id)
        repository.log_operation(
            db,
            action="benchmark.run",
            target_type="benchmark_case",
            target_id=case.case_id,
            actor_id=task.actor_id,
            payload={**result.model_dump(mode="json"), "task_id": task.id},
        )
        return result.model_dump(mode="json")
    raise ValueError(f"unsupported task type: {task.task_type}")


def _raise_if_cancelled(db: Session, task_id: str | None) -> None:
    if task_id and repository.is_work_task_cancel_requested(db, task_id):
        raise WorkTaskCancelled("task cancellation requested")


def _notify_task_issue(db: Session, task: WorkTaskRead, title: str, message: str) -> None:
    if not task.actor_id:
        return
    repository.create_notification(
        db,
        user_id=task.actor_id,
        event_type=f"work_task.{task.status}",
        title=title,
        message=message,
        target_type="work_task",
        target_id=task.id,
        payload={
            "task_type": task.task_type,
            "target_type": task.target_type,
            "target_id": task.target_id,
            "attempt": task.attempt,
            "max_attempts": task.max_attempts,
        },
    )
