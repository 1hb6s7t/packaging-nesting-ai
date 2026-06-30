from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Iterable

from app.domain.schemas import NestingItem, NestingJob, NestingSolution, SolverConfig, SolverName
from app.services.scoring import score_solution
from app.services.solvers.orchestrator import SolverOrchestrator
from app.services.validator import validate_solution


@dataclass(frozen=True)
class SolverRunSpec:
    candidate_id: str
    solver_name: SolverName
    solver_version: str
    seed: int | None
    time_limit_sec: int
    rotation_policy: str
    config: SolverConfig


class MultiSolverOrchestrator:
    DEFAULT_SOLVERS: tuple[SolverName, ...] = (
        SolverName.rectpack,
        SolverName.ortools,
        SolverName.packing_solver,
        SolverName.sparrow,
    )
    DEFAULT_ROTATION_POLICIES: tuple[str, ...] = ("as_declared", "prefer_90", "zero_only")

    def __init__(self, base_orchestrator: SolverOrchestrator | None = None) -> None:
        self.base_orchestrator = base_orchestrator or SolverOrchestrator()
        self.adapters = self.base_orchestrator.adapters

    def solve_candidate_pool(
        self,
        job: NestingJob,
        *,
        solver_names: Iterable[SolverName] | None = None,
        seeds: Iterable[int | None] | None = None,
        time_limits_sec: Iterable[int] | None = None,
        rotation_policies: Iterable[str] | None = None,
        max_runs: int | None = None,
    ) -> list[NestingSolution]:
        specs = self.build_run_specs(
            job,
            solver_names=solver_names,
            seeds=seeds,
            time_limits_sec=time_limits_sec,
            rotation_policies=rotation_policies,
            max_runs=max_runs,
        )
        solutions: list[NestingSolution] = []
        for spec in specs:
            run_job = self._job_for_spec(job, spec)
            adapter = self.adapters[spec.solver_name]
            solution = adapter.solve(run_job, spec.config)
            solution.solution_id = spec.candidate_id
            solution.job_id = job.job_id
            report = validate_solution(run_job, solution)
            solution.validation_report = report
            if solution.status != "failed":
                solution.status = "valid" if report.is_valid else "invalid"
            solution.score = score_solution(run_job, solution)
            solution.exports.update(self._solution_audit(spec, solution))
            solutions.append(solution)

        ranked = sorted(solutions, key=_solution_sort_key, reverse=True)
        for rank, solution in enumerate(ranked, 1):
            solution.rank = rank
        return ranked

    def build_run_specs(
        self,
        job: NestingJob,
        *,
        solver_names: Iterable[SolverName] | None = None,
        seeds: Iterable[int | None] | None = None,
        time_limits_sec: Iterable[int] | None = None,
        rotation_policies: Iterable[str] | None = None,
        max_runs: int | None = None,
    ) -> list[SolverRunSpec]:
        names = list(solver_names or self.DEFAULT_SOLVERS)
        seed_values = list(seeds if seeds is not None else _default_seeds(job.solver_config.seed))
        limits = list(time_limits_sec if time_limits_sec is not None else [job.solver_config.time_limit_sec])
        policies = list(rotation_policies or self.DEFAULT_ROTATION_POLICIES)
        specs: list[SolverRunSpec] = []
        for solver_name in names:
            adapter = self.adapters.get(solver_name)
            if adapter is None:
                continue
            for seed in seed_values:
                for time_limit in limits:
                    for policy in policies:
                        config = job.solver_config.model_copy(
                            update={
                                "solver_name": solver_name,
                                "seed": seed,
                                "time_limit_sec": max(1, int(time_limit)),
                                "options": {
                                    **job.solver_config.options,
                                    "rotation_policy": policy,
                                    "candidate_pool_id": _stable_id(
                                        job.job_id,
                                        solver_name.value,
                                        str(seed),
                                        str(time_limit),
                                        policy,
                                    ),
                                },
                            }
                        )
                        specs.append(
                            SolverRunSpec(
                                candidate_id=f"msol_{config.options['candidate_pool_id']}",
                                solver_name=solver_name,
                                solver_version=getattr(adapter, "version", "unknown"),
                                seed=seed,
                                time_limit_sec=config.time_limit_sec,
                                rotation_policy=policy,
                                config=config,
                            )
                        )
                        if max_runs is not None and len(specs) >= max_runs:
                            return specs
        return specs

    def candidate_pool_report(self, solutions: list[NestingSolution]) -> dict[str, object]:
        legal = [
            solution
            for solution in solutions
            if solution.status == "valid" and solution.validation_report is not None and solution.validation_report.is_valid
        ]
        failed = [solution for solution in solutions if solution.status == "failed"]
        manifests = [_loads(solution.exports.get("audit_manifest_json")) for solution in solutions]
        return {
            "orchestrator": "MultiSolverOrchestrator",
            "candidate_count": len(solutions),
            "legal_candidate_count": len(legal),
            "failed_candidate_count": len(failed),
            "solver_names": sorted({str(manifest.get("solver_name")) for manifest in manifests if manifest.get("solver_name")}),
            "solver_versions": {
                str(manifest.get("solver_name")): str(manifest.get("solver_version"))
                for manifest in manifests
                if manifest.get("solver_name")
            },
            "seeds": sorted({manifest.get("seed") for manifest in manifests}, key=lambda value: str(value)),
            "rotation_policies": sorted(
                {str(manifest.get("rotation_policy")) for manifest in manifests if manifest.get("rotation_policy")}
            ),
            "time_limits_sec": sorted({int(manifest.get("time_limit_sec", 0)) for manifest in manifests}),
            "top_candidate_ids": [solution.solution_id for solution in solutions[:3]],
            "top_legal_candidate_ids": [solution.solution_id for solution in legal[:3]],
            "deterministic": True,
            "coordinates_source": "deterministic_solver_and_validator",
        }

    def _job_for_spec(self, job: NestingJob, spec: SolverRunSpec) -> NestingJob:
        items = [_item_for_rotation_policy(item, spec.rotation_policy) for item in job.candidate_items]
        if spec.seed is not None:
            items = sorted(items, key=lambda item: (-item.priority_score, _stable_id(str(spec.seed), item.item_id)))
        return job.model_copy(update={"candidate_items": items, "solver_config": spec.config})

    def _solution_audit(self, spec: SolverRunSpec, solution: NestingSolution) -> dict[str, str]:
        report = solution.validation_report
        manifest = {
            "candidate_id": spec.candidate_id,
            "solver_name": spec.solver_name.value,
            "solver_version": spec.solver_version,
            "seed": spec.seed,
            "time_limit_sec": spec.time_limit_sec,
            "rotation_policy": spec.rotation_policy,
            "status": solution.status,
            "validator_is_valid": bool(report and report.is_valid),
            "validator_issue_codes": [issue.code for issue in report.issues] if report else [],
            "runtime_ms": solution.runtime_ms,
            "score_total": solution.score.total if solution.score else 0,
        }
        return {
            "candidate_id": spec.candidate_id,
            "solver_name": spec.solver_name.value,
            "solver_version": spec.solver_version,
            "seed": "" if spec.seed is None else str(spec.seed),
            "time_limit_sec": str(spec.time_limit_sec),
            "rotation_policy": spec.rotation_policy,
            "audit_manifest_json": json.dumps(manifest, sort_keys=True),
        }


def _item_for_rotation_policy(item: NestingItem, rotation_policy: str) -> NestingItem:
    rotations = list(dict.fromkeys(item.allowed_rotations))
    if rotation_policy == "prefer_90":
        preferred = [90, 270, 0, 180]
        ordered = [rotation for rotation in preferred if rotation in rotations]
    elif rotation_policy == "zero_only":
        ordered = [0] if 0 in rotations else rotations[:1]
    else:
        ordered = rotations
    return item.model_copy(update={"allowed_rotations": ordered or rotations})


def _solution_sort_key(solution: NestingSolution) -> tuple[bool, bool, float, float, int, str]:
    valid = solution.status == "valid" and solution.validation_report is not None and solution.validation_report.is_valid
    not_failed = solution.status != "failed"
    total_score = solution.score.total if solution.score else 0
    return (valid, not_failed, total_score, solution.utilization_rate, -solution.runtime_ms, solution.solution_id)


def _default_seeds(seed: int | None) -> tuple[int | None, int | None]:
    if seed is None:
        return (0, 17)
    return (seed, seed + 17)


def _stable_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _loads(value: str | None) -> dict[str, object]:
    if not value:
        return {}
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}
