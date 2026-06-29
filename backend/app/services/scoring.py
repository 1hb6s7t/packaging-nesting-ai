from app.domain.schemas import NestingJob, NestingSolution, SolutionScore


def score_solution(job: NestingJob, solution: NestingSolution) -> SolutionScore:
    total_priority = sum(item.priority_score for item in job.candidate_items) or 1
    placed_priority = 0.0
    placed_ids = {placement.item_id for placement in solution.placed_items}
    for item in job.candidate_items:
        if item.item_id in placed_ids:
            placed_priority += item.priority_score
    priority_score = min(1.0, placed_priority / total_priority)
    penalty = 0.0 if solution.validation_report and solution.validation_report.is_valid else 1.0
    total = (
        0.40 * solution.utilization_rate
        + 0.20 * priority_score
        + 0.15 * priority_score
        + 0.10 * 0.7
        + 0.10 * 1.0
        + 0.05 * 1.0
        - penalty
    )
    return SolutionScore(
        utilization_score=round(solution.utilization_rate, 4),
        total_priority_score=round(priority_score, 4),
        quote_coverage_score=round(priority_score, 4),
        due_date_score=0.7,
        manufacturability_score=1.0,
        stability_score=1.0,
        penalty=penalty,
        total=round(total, 4),
    )
