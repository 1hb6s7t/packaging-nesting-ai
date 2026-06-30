from __future__ import annotations

from typing import Any

from app.domain.schemas import NestingJob, NestingSolution, ValidationReport


def generate_solution_report(job: NestingJob, solution: NestingSolution) -> dict:
    validation = solution.validation_report
    validation_summary = summarize_validation(validation)
    return {
        "job_id": job.job_id,
        "solution_id": solution.solution_id,
        "solver": str(solution.solver),
        "status": solution.status,
        "rank": solution.rank,
        "utilization_rate": solution.utilization_rate,
        "waste_rate": solution.waste_rate,
        "fixed_count": len(job.fixed_items),
        "candidate_count": len(job.candidate_items),
        "placed_count": len(solution.placed_items),
        "unplaced_count": len(solution.unplaced_items),
        "estimated_sheet_cost": job.sheet.cost_per_sheet,
        "estimated_waste_cost": round(job.sheet.cost_per_sheet * solution.waste_rate, 4),
        "sheet": {
            "sheet_id": job.sheet.sheet_id,
            "width": job.sheet.width,
            "height": job.sheet.height,
            "material": job.sheet.material,
            "thickness": job.sheet.thickness,
        },
        "placed_items": [
            {
                "item_id": placement.item_id,
                "order_id": placement.order_id,
                "x": placement.x,
                "y": placement.y,
                "rotation": placement.rotation,
                "mirrored": placement.mirrored,
                "width": placement.width,
                "height": placement.height,
            }
            for placement in solution.placed_items
        ],
        "unplaced_items": [
            {
                "item_id": item.item_id,
                "order_id": item.order_id,
                "reason": item.reason,
            }
            for item in solution.unplaced_items
        ],
        "validation_summary": validation_summary,
        "validation": validation.model_dump() if validation else None,
        "score": solution.score.model_dump() if solution.score else None,
        "exports": solution.exports,
    }


def summarize_validation(validation: ValidationReport | None) -> dict[str, Any]:
    if validation is None:
        return {
            "is_valid": None,
            "issue_count": 0,
            "error_count": 0,
            "warning_count": 0,
            "issue_codes": [],
        }
    issue_codes = sorted({issue.code for issue in validation.issues})
    return {
        "is_valid": validation.is_valid,
        "issue_count": len(validation.issues),
        "error_count": sum(1 for issue in validation.issues if issue.severity == "error"),
        "warning_count": sum(1 for issue in validation.issues if issue.severity == "warning"),
        "issue_codes": issue_codes,
        "overlap": validation.overlap,
        "out_of_bounds": validation.out_of_bounds,
        "gripper_conflict": validation.gripper_conflict,
        "min_gap_violation": validation.min_gap_violation,
        "rotation_invalid": validation.rotation_invalid,
    }
