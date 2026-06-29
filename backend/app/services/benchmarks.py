from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from app.domain.schemas import BenchmarkCaseRead, BenchmarkRunResult, NestingJob, SolverConfig, SolverName
from app.services import repository
from app.services.solvers import SolverOrchestrator


def run_and_record_benchmark(
    db: Session,
    case: BenchmarkCaseRead,
    solver_name: SolverName = SolverName.rectpack,
    cancel_check: Callable[[], None] | None = None,
) -> BenchmarkRunResult:
    if cancel_check:
        cancel_check()
    repository.ensure_solver_enabled(db, solver_name.value)
    job = NestingJob(
        job_id=f"bench_{case.case_id}",
        sheet=case.sheet,
        candidate_items=case.items,
        top_k=1,
        solver_config=SolverConfig(solver_name=solver_name),
    )
    solution = SolverOrchestrator().solve(job)[0]
    if cancel_check:
        cancel_check()
    valid = bool(solution.validation_report and solution.validation_report.is_valid)
    failure_reason = None if valid else "validator_failed"
    return repository.create_benchmark_run_record(
        db,
        case_id=case.case_id,
        solver_name=solver_name.value,
        utilization_rate=solution.utilization_rate,
        waste_rate=solution.waste_rate,
        runtime_ms=solution.runtime_ms,
        valid=valid,
        failure_reason=failure_reason,
    )
