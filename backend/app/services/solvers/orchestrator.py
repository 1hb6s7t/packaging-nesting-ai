from __future__ import annotations

from app.domain.schemas import NestingJob, NestingSolution, SolverConfig, SolverName
from app.services.scoring import score_solution
from app.services.solvers.placeholders import UnsupportedExternalSolverAdapter
from app.services.solvers.rectpack_adapter import RectpackSolverAdapter
from app.services.validator import validate_solution


class SolverOrchestrator:
    def __init__(self) -> None:
        self.adapters = {
            SolverName.rectpack: RectpackSolverAdapter(),
            SolverName.ortools: UnsupportedExternalSolverAdapter(SolverName.ortools),
            SolverName.packing_solver: UnsupportedExternalSolverAdapter(SolverName.packing_solver),
            SolverName.sparrow: UnsupportedExternalSolverAdapter(SolverName.sparrow),
            SolverName.phoenix: UnsupportedExternalSolverAdapter(SolverName.phoenix),
        }

    def solve(self, job: NestingJob, config: SolverConfig | None = None) -> list[NestingSolution]:
        cfg = config or job.solver_config
        adapter = self.adapters.get(cfg.solver_name, self.adapters[SolverName.rectpack])
        solutions = [adapter.solve(job, cfg)]
        for solution in solutions:
            report = validate_solution(job, solution)
            solution.validation_report = report
            solution.status = "valid" if report.is_valid else "invalid"
            solution.score = score_solution(job, solution)
        ranked = sorted(solutions, key=lambda item: item.score.total if item.score else 0, reverse=True)
        for index, solution in enumerate(ranked, 1):
            solution.rank = index
        return ranked[: job.top_k]
