from __future__ import annotations

from uuid import uuid4

from app.domain.schemas import NestingJob, NestingSolution, SolverConfig, SolverName, UnplacedItem
from app.services.solvers.base import SolverAdapter


class UnsupportedExternalSolverAdapter(SolverAdapter):
    def __init__(self, name: SolverName, version: str = "external-adapter-stub-0.1.0") -> None:
        self.name = name
        self.version = version

    def supports(self, job: NestingJob) -> bool:
        return False

    def solve(self, job: NestingJob, config: SolverConfig) -> NestingSolution:
        return NestingSolution(
            solution_id=f"sol_{uuid4().hex[:16]}",
            job_id=job.job_id,
            solver=self.name,
            status="failed",
            unplaced_items=[
                UnplacedItem(
                    item_id=item.item_id,
                    order_id=item.order_id,
                    reason=f"{self.name.value} adapter is registered but external binary/service is not configured",
                )
                for item in job.candidate_items
            ],
        )

