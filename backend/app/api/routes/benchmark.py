from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.domain.schemas import BenchmarkCase, BenchmarkCaseRead, BenchmarkRunResult, CurrentUser, SolverName, WorkTaskRead
from app.services import repository
from app.services.benchmarks import run_and_record_benchmark
from app.services.security import require_permission
from app.services.task_dispatch import dispatch_work_task

router = APIRouter()


@router.get("/cases", response_model=list[BenchmarkCaseRead])
def list_benchmark_cases(
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("benchmark:write")),
) -> list[BenchmarkCaseRead]:
    return repository.list_benchmark_cases(db)


@router.post("/cases", response_model=BenchmarkCaseRead)
def upsert_benchmark_case(
    case: BenchmarkCase,
    source: str = "manual",
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("benchmark:write")),
) -> BenchmarkCaseRead:
    saved = repository.upsert_benchmark_case(db, case, source=source)
    repository.log_operation(
        db,
        action="benchmark.case.upsert",
        target_type="benchmark_case",
        target_id=saved.case_id,
        actor_id=current_user.user_id,
        payload={"name": saved.name, "source": saved.source, "item_count": len(saved.items)},
    )
    return saved


@router.get("/cases/{case_id}", response_model=BenchmarkCaseRead)
def get_benchmark_case(
    case_id: str,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("benchmark:write")),
) -> BenchmarkCaseRead:
    case = repository.get_benchmark_case(db, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="benchmark case not found")
    return case


@router.post("/cases/{case_id}/runs", response_model=BenchmarkRunResult)
def run_stored_benchmark_case(
    case_id: str,
    solver_name: SolverName = SolverName.rectpack,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("benchmark:write")),
) -> BenchmarkRunResult:
    case = repository.get_benchmark_case(db, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="benchmark case not found")
    try:
        result = run_and_record_benchmark(db, case, solver_name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    repository.log_operation(
        db,
        action="benchmark.run",
        target_type="benchmark_case",
        target_id=case.case_id,
        actor_id=current_user.user_id,
        payload=result.model_dump(mode="json"),
    )
    return result


@router.post("/cases/{case_id}/runs/async", response_model=WorkTaskRead)
def queue_stored_benchmark_case_run(
    case_id: str,
    background_tasks: BackgroundTasks,
    solver_name: SolverName = SolverName.rectpack,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("benchmark:write")),
) -> WorkTaskRead:
    case = repository.get_benchmark_case(db, case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="benchmark case not found")
    task = repository.create_work_task(
        db,
        task_type="benchmark.run",
        target_type="benchmark_case",
        target_id=case.case_id,
        actor_id=current_user.user_id,
        payload={"case_id": case.case_id, "solver_name": solver_name.value},
        timeout_sec=get_settings().benchmark_task_timeout_sec,
    )
    repository.log_operation(
        db,
        action="benchmark.run_queued",
        target_type="benchmark_case",
        target_id=case.case_id,
        actor_id=current_user.user_id,
        payload={"task_id": task.id, "solver_name": solver_name.value},
    )
    dispatch_work_task(task.id, background_tasks)
    return task


@router.get("/runs", response_model=list[BenchmarkRunResult])
def list_benchmark_runs(
    case_id: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("benchmark:write")),
) -> list[BenchmarkRunResult]:
    return repository.list_benchmark_runs(db, case_id=case_id, limit=limit)


@router.post("/run", response_model=BenchmarkRunResult)
def run_benchmark(
    case: BenchmarkCase,
    solver_name: SolverName = SolverName.rectpack,
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(require_permission("benchmark:write")),
) -> BenchmarkRunResult:
    saved = repository.upsert_benchmark_case(db, case, source="ad_hoc")
    try:
        result = run_and_record_benchmark(db, saved, solver_name)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    repository.log_operation(
        db,
        action="benchmark.run_ad_hoc",
        target_type="benchmark_case",
        target_id=saved.case_id,
        actor_id=current_user.user_id,
        payload=result.model_dump(mode="json"),
    )
    return result
