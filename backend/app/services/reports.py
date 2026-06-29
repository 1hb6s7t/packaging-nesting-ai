from __future__ import annotations

from app.domain.schemas import NestingJob, NestingSolution


def generate_solution_report(job: NestingJob, solution: NestingSolution) -> dict:
    validation = solution.validation_report
    return {
        "job_id": job.job_id,
        "solution_id": solution.solution_id,
        "solver": str(solution.solver),
        "status": solution.status,
        "rank": solution.rank,
        "utilization_rate": solution.utilization_rate,
        "waste_rate": solution.waste_rate,
        "placed_count": len(solution.placed_items),
        "unplaced_count": len(solution.unplaced_items),
        "estimated_sheet_cost": job.sheet.cost_per_sheet,
        "validation": validation.model_dump() if validation else None,
        "score": solution.score.model_dump() if solution.score else None,
        "exports": solution.exports,
    }

