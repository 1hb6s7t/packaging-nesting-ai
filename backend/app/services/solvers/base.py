from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.schemas import NestingJob, NestingSolution, SolverConfig, SolverName, ValidationReport


class SolverAdapter(ABC):
    name: SolverName | str
    version: str = "0.1.0"

    @abstractmethod
    def supports(self, job: NestingJob) -> bool:
        raise NotImplementedError

    def validate_input(self, job: NestingJob) -> ValidationReport:
        issues = []
        if not job.candidate_items:
            issues.append({"code": "empty_candidates", "message": "candidate_items is empty", "severity": "error"})
        return ValidationReport(is_valid=not issues, issues=issues)

    @abstractmethod
    def solve(self, job: NestingJob, config: SolverConfig) -> NestingSolution:
        raise NotImplementedError

    def normalize_output(self, raw_output: dict) -> NestingSolution:
        return NestingSolution.model_validate(raw_output)

