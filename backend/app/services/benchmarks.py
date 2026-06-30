from __future__ import annotations

import ctypes
import os
import statistics
from collections.abc import Callable

from sqlalchemy.orm import Session

from app.domain.schemas import BenchmarkCaseRead, BenchmarkRunResult, NestingJob, SolverConfig, SolverName
from app.services import repository
from app.services.batch_planning import plan_batch


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
    rss_before = _current_rss_mb()
    batch_result = plan_batch(job, case.planning_mode, job.solver_config)
    rss_after = _current_rss_mb()
    if cancel_check:
        cancel_check()
    baseline_delta = (
        round(batch_result.utilization_rate - case.baseline_utilization_rate, 4)
        if case.baseline_utilization_rate is not None
        else None
    )
    solution_runtimes = [solution.runtime_ms for solution in batch_result.solutions]
    metrics = {
        **batch_result.metrics,
        "baseline_utilization_rate": case.baseline_utilization_rate,
        "baseline_delta_utilization_rate": baseline_delta,
        "solver_coordinates_source": "backend_solver",
        "rss_before_mb": rss_before,
        "rss_after_mb": rss_after,
    }
    return repository.create_benchmark_run_record(
        db,
        case_id=case.case_id,
        solver_name=solver_name.value,
        planning_mode=batch_result.planning_mode,
        utilization_rate=batch_result.utilization_rate,
        waste_rate=batch_result.waste_rate,
        runtime_ms=batch_result.runtime_ms,
        valid=batch_result.valid,
        hard_rule_pass=batch_result.hard_rule_pass,
        quantity_fulfillment_rate=batch_result.quantity_fulfillment_rate,
        requested_units=batch_result.requested_units,
        produced_units=batch_result.produced_units,
        shortage_units=batch_result.shortage_units,
        overproduction_units=batch_result.overproduction_units,
        units_per_sheet=batch_result.units_per_sheet,
        sheets_used=batch_result.sheets_used,
        peak_rss_mb=max(value for value in [rss_before, rss_after] if value is not None)
        if rss_before is not None or rss_after is not None
        else None,
        export_ok=batch_result.export_ok,
        case_score=batch_result.case_score,
        baseline_delta_utilization_rate=baseline_delta,
        p95_runtime_ms=_p95_int(solution_runtimes),
        metrics=metrics,
        failure_reason=batch_result.failure_reason,
    )


def _p95_int(values: list[int]) -> int | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    return int(statistics.quantiles(values, n=100, method="inclusive")[94])


def _current_rss_mb() -> float | None:
    if os.name == "nt":
        return _windows_rss_mb()
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF)
    except (ImportError, OSError):
        return None
    divisor = 1024 * 1024 if os.uname().sysname == "Darwin" else 1024
    return round(usage.ru_maxrss / divisor, 3)


def _windows_rss_mb() -> float | None:
    try:
        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(ProcessMemoryCounters)
        process = ctypes.windll.kernel32.GetCurrentProcess()
        ok = ctypes.windll.psapi.GetProcessMemoryInfo(process, ctypes.byref(counters), counters.cb)
    except (AttributeError, OSError):
        return None
    if not ok:
        return None
    return round(counters.WorkingSetSize / (1024 * 1024), 3)
