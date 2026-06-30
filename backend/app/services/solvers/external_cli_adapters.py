from __future__ import annotations

import json
import hashlib
from typing import Any
from uuid import uuid4

from app.domain.schemas import NestingJob, NestingSolution, Placement, SolverConfig, SolverName, UnplacedItem
from app.services.geometry import calculate_bbox, enrich_polygon
from app.services.solvers.base import SolverAdapter
from app.services.solvers.cli_runner import CliRunResult, resolve_cli_command, run_json_cli


class ExternalCliSolverAdapter(SolverAdapter):
    command_option: str
    binary_option: str
    env_var: str
    default_binary_names: list[str]

    def supports(self, job: NestingJob) -> bool:
        return bool(job.candidate_items)

    def solve(self, job: NestingJob, config: SolverConfig) -> NestingSolution:
        command = resolve_cli_command(
            options=config.options,
            command_option=self.command_option,
            binary_option=self.binary_option,
            env_var=self.env_var,
            default_binary_names=self.default_binary_names,
        )
        if command is None:
            return self.failed_solution(
                job,
                f"{self.name.value} external CLI is not configured; set {self.binary_option}, "
                f"{self.command_option}, external_solver_binary, or {self.env_var}",
                command=None,
            )
        input_payload = self.build_payload(job, config)
        input_payload_hash = sha256_text(json.dumps(input_payload, ensure_ascii=False, sort_keys=True))
        result = run_json_cli(command, input_payload, timeout_sec=config.time_limit_sec)
        if result.status != "passed":
            return self.failed_solution(
                job,
                self.failure_reason(result),
                runtime_ms=result.duration_ms,
                command=command,
                result=result,
                input_payload_hash=input_payload_hash,
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            return self.failed_solution(
                job,
                f"{self.name.value} returned invalid JSON: {exc}",
                runtime_ms=result.duration_ms,
                command=command,
                result=result,
                input_payload_hash=input_payload_hash,
            )
        try:
            return self.solution_from_payload(
                job,
                payload,
                runtime_ms=result.duration_ms,
                result=result,
                input_payload_hash=input_payload_hash,
            )
        except (TypeError, ValueError, KeyError) as exc:
            return self.failed_solution(
                job,
                f"{self.name.value} certificate could not be parsed: {exc}",
                runtime_ms=result.duration_ms,
                command=command,
                result=result,
                payload=payload,
                input_payload_hash=input_payload_hash,
            )

    def build_payload(self, job: NestingJob, config: SolverConfig) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "solver": self.name.value,
            "job": job.model_dump(mode="json"),
            "config": config.model_dump(mode="json"),
            "items": [self.item_payload(item) for item in job.candidate_items],
        }

    def item_payload(self, item) -> dict[str, Any]:
        polygon = enrich_polygon(item.polygon)
        bbox = polygon.bbox or calculate_bbox(polygon.outer)
        return {
            "item_id": item.item_id,
            "order_id": item.order_id,
            "width": bbox.width,
            "height": bbox.height,
            "quantity": item.quantity,
            "allowed_rotations": item.allowed_rotations,
            "min_gap_mm": item.min_gap_mm,
            "bleed_mm": item.bleed_mm,
        }

    def solution_from_payload(
        self,
        job: NestingJob,
        payload: dict[str, Any],
        *,
        runtime_ms: int,
        result: CliRunResult,
        input_payload_hash: str,
    ) -> NestingSolution:
        placements_payload = payload.get("placed_items", payload.get("placements", []))
        unplaced_payload = payload.get("unplaced_items", payload.get("unplaced", []))
        if not isinstance(placements_payload, list) or not isinstance(unplaced_payload, list):
            raise ValueError("placed_items and unplaced_items must be lists")
        placed_items = [placement_from_payload(item) for item in placements_payload]
        unplaced_items = [unplaced_from_payload(item) for item in unplaced_payload]
        solution_id = str(payload.get("solution_id") or f"sol_{uuid4().hex[:16]}")
        return NestingSolution(
            solution_id=solution_id,
            job_id=job.job_id,
            solver=self.name,
            status=str(payload.get("status") or "candidate"),
            rank=1,
            runtime_ms=int(payload.get("runtime_ms", runtime_ms)),
            utilization_rate=float(payload.get("utilization_rate", 0)),
            waste_rate=float(payload.get("waste_rate", 1)),
            placed_items=placed_items,
            unplaced_items=unplaced_items,
            exports=self.audit_exports(
                command=result.command,
                result=result,
                payload=payload,
                solution_id=solution_id,
                input_payload_hash=input_payload_hash,
            ),
        )

    def failed_solution(
        self,
        job: NestingJob,
        reason: str,
        *,
        runtime_ms: int = 0,
        command: list[str] | None = None,
        result: CliRunResult | None = None,
        payload: dict[str, Any] | None = None,
        input_payload_hash: str = "",
    ) -> NestingSolution:
        solution_id = f"sol_{uuid4().hex[:16]}"
        return NestingSolution(
            solution_id=solution_id,
            job_id=job.job_id,
            solver=self.name,
            status="failed",
            runtime_ms=runtime_ms,
            unplaced_items=[
                UnplacedItem(item_id=item.item_id, order_id=item.order_id, reason=reason) for item in job.candidate_items
            ],
            exports=self.audit_exports(
                command=command or (result.command if result else None),
                result=result,
                payload=payload,
                solution_id=solution_id,
                error_message=reason,
                input_payload_hash=input_payload_hash,
            ),
        )

    def failure_reason(self, result: CliRunResult) -> str:
        parts = [f"{self.name.value} external CLI failed"]
        if result.error:
            parts.append(result.error)
        if result.stderr.strip():
            parts.append(f"stderr={result.stderr.strip()[:500]}")
        return "; ".join(parts)

    def audit_exports(
        self,
        *,
        command: list[str] | None,
        result: CliRunResult | None,
        payload: dict[str, Any] | None,
        solution_id: str,
        input_payload_hash: str,
        error_message: str | None = None,
    ) -> dict[str, str]:
        stdout = result.stdout if result else ""
        stderr = result.stderr if result else ""
        certificate = external_certificate(
            solver_name=self.name.value,
            solver_version=self.version,
            solution_id=solution_id,
            payload=payload,
            result=result,
            error_message=error_message,
        )
        return {
            "solver_name": self.name.value,
            "solver_version": self.version,
            "command_json": json.dumps(command or [], ensure_ascii=False),
            "cli_status": result.status if result else "not_configured",
            "exit_code": "" if result is None or result.exit_code is None else str(result.exit_code),
            "duration_ms": "0" if result is None else str(result.duration_ms),
            "stdout": stdout,
            "stderr": stderr,
            "stdout_sha256": sha256_text(stdout),
            "stderr_sha256": sha256_text(stderr),
            "input_payload_sha256": input_payload_hash,
            "cli_result_json": json.dumps(cli_result_payload(result), ensure_ascii=False, sort_keys=True),
            "error_message": error_message or (result.error if result and result.error else ""),
            "external_certificate_json": json.dumps(certificate, ensure_ascii=False, sort_keys=True),
            "certificate_json": json.dumps(certificate, ensure_ascii=False, sort_keys=True),
        }


class PackingSolverAdapter(ExternalCliSolverAdapter):
    name = SolverName.packing_solver
    version = "external-cli-contract-1.0.0"
    command_option = "packing_solver_command"
    binary_option = "packing_solver_binary"
    env_var = "PACKING_SOLVER_BINARY"
    default_binary_names = ["packingsolver", "packing_solver"]


class SparrowSolverAdapter(ExternalCliSolverAdapter):
    name = SolverName.sparrow
    version = "external-cli-contract-1.0.0"
    command_option = "sparrow_command"
    binary_option = "sparrow_binary"
    env_var = "SPARROW_SOLVER_BINARY"
    default_binary_names = ["sparrow", "sparrow-solver"]


def placement_from_payload(payload: Any) -> Placement:
    if not isinstance(payload, dict):
        raise ValueError("placement must be an object")
    return Placement(
        item_id=str(payload["item_id"]),
        order_id=str(payload["order_id"]),
        x=float(payload.get("x", 0)),
        y=float(payload.get("y", 0)),
        rotation=int(payload.get("rotation", 0)),
        mirrored=bool(payload.get("mirrored", False)),
        width=float(payload["width"]) if payload.get("width") is not None else None,
        height=float(payload["height"]) if payload.get("height") is not None else None,
    )


def unplaced_from_payload(payload: Any) -> UnplacedItem:
    if isinstance(payload, str):
        return UnplacedItem(item_id=payload, reason="reported unplaced by external solver")
    if not isinstance(payload, dict):
        raise ValueError("unplaced item must be an object")
    return UnplacedItem(
        item_id=str(payload["item_id"]),
        order_id=str(payload["order_id"]) if payload.get("order_id") is not None else None,
        reason=str(payload.get("reason") or "reported unplaced by external solver"),
    )


def external_certificate(
    *,
    solver_name: str,
    solver_version: str,
    solution_id: str,
    payload: dict[str, Any] | None,
    result: CliRunResult | None,
    error_message: str | None,
) -> dict[str, Any]:
    run_status = result.status if result else "not_configured"
    exit_code = result.exit_code if result else None
    duration_ms = result.duration_ms if result else 0
    stdout_hash = sha256_text(result.stdout if result else "")
    stderr_hash = sha256_text(result.stderr if result else "")
    run_error = error_message or (result.error if result else "")
    if payload is not None and isinstance(payload.get("certificate"), dict):
        return {
            "schema_version": 1,
            "source": "external_solver_certificate",
            "solver_name": solver_name,
            "solver_version": solver_version,
            "solution_id": solution_id,
            "status": run_status,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "error_message": run_error,
            "stdout_sha256": stdout_hash,
            "stderr_sha256": stderr_hash,
            "certificate": payload["certificate"],
        }
    if payload is not None:
        return {
            "schema_version": 1,
            "source": "external_solver_payload",
            "solver_name": solver_name,
            "solver_version": solver_version,
            "solution_id": solution_id,
            "status": run_status,
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "error_message": run_error,
            "stdout_sha256": stdout_hash,
            "stderr_sha256": stderr_hash,
            "payload": payload,
        }
    return {
        "schema_version": 1,
        "source": "external_solver_failure",
        "solver_name": solver_name,
        "solver_version": solver_version,
        "solution_id": solution_id,
        "status": run_status,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "error_message": run_error,
        "stdout_sha256": stdout_hash,
        "stderr_sha256": stderr_hash,
    }


def cli_result_payload(result: CliRunResult | None) -> dict[str, Any]:
    if result is None:
        return {
            "command": [],
            "status": "not_configured",
            "duration_ms": 0,
            "exit_code": None,
            "error": "external CLI is not configured",
            "stdout_sha256": sha256_text(""),
            "stderr_sha256": sha256_text(""),
        }
    return {
        "command": result.command,
        "status": result.status,
        "duration_ms": result.duration_ms,
        "exit_code": result.exit_code,
        "error": result.error,
        "stdout_sha256": sha256_text(result.stdout),
        "stderr_sha256": sha256_text(result.stderr),
    }


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
