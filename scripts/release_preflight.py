from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
FRONTEND_DIR = REPO_ROOT / "frontend"
SCRIPTS_DIR = REPO_ROOT / "scripts"
INVENTORY_SCRIPT = REPO_ROOT / "scripts" / "release_inventory.py"

TARGETED_BACKEND_TESTS = [
    "tests/backend/test_api.py",
    "tests/backend/test_migrations.py",
    "tests/backend/test_health.py",
    "tests/backend/test_config.py",
    "tests/backend/test_operation_logs.py",
    "tests/backend/test_adapters.py",
    "tests/backend/test_ai_tools.py",
    "tests/backend/test_solution_approval.py",
    "tests/backend/test_solver_placeholders.py",
    "tests/backend/test_external_solver_adapters.py",
    "tests/backend/test_batch_planning.py",
    "tests/backend/test_benchmark.py",
    "tests/backend/test_benchmark_importers.py",
    "tests/backend/test_benchmark_release_gate.py",
    "tests/backend/test_benchmark_stress_787.py",
    "tests/backend/test_customer_sandbox_pack.py",
    "tests/backend/test_customer_sandbox_audit.py",
    "tests/backend/test_conversion_supplier_audit.py",
    "tests/backend/test_solver_governance_audit.py",
    "tests/backend/test_notifications.py",
    "tests/backend/test_notification_channel_audit.py",
    "tests/backend/test_storage_export_audit.py",
    "tests/backend/test_external_acceptance_audit.py",
    "tests/backend/test_verify_external_acceptance_audit.py",
    "tests/backend/test_deployment_compose.py",
    "tests/backend/test_deployment_compose_audit.py",
    "tests/backend/test_repository_hygiene_audit.py",
    "tests/backend/test_route_auth_surface.py",
    "tests/backend/test_production_env_audit.py",
    "tests/backend/test_verify_production_env_audit.py",
    "tests/backend/test_dependency_review_audit.py",
    "tests/backend/test_dependency_review_template.py",
    "tests/backend/test_verify_dependency_review_audit.py",
    "tests/backend/test_release_image_dependency_audit.py",
    "tests/backend/test_verify_release_image_dependency_audit.py",
    "tests/backend/test_release_handoff_bundle.py",
    "tests/backend/test_verify_release_handoff_bundle.py",
    "tests/backend/test_go_live_readiness_audit.py",
    "tests/backend/test_verify_go_live_readiness_report.py",
    "tests/backend/test_go_live_remediation_packet.py",
    "tests/backend/test_release_evidence_pack.py",
    "tests/backend/test_verify_release_evidence_pack.py",
    "tests/backend/test_ci_evidence_manifest.py",
    "tests/backend/test_release_inventory.py",
    "tests/backend/test_verify_release_preflight.py",
    "tests/backend/test_release_preflight.py",
]


@dataclass(frozen=True)
class CommandStep:
    name: str
    command: list[str]
    cwd: Path
    env: dict[str, str] | None = None
    timeout_sec: int = 180


@dataclass(frozen=True)
class GateResult:
    name: str
    kind: str
    status: str
    duration_sec: float
    command: list[str] | None = None
    cwd: str | None = None
    timeout_sec: int | None = None
    exit_code: int | None = None
    error: str | None = None
    payload: dict[str, Any] | None = None


def backend_env() -> dict[str, str]:
    env = os.environ.copy()
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = str(BACKEND_DIR) if not existing else os.pathsep.join([str(BACKEND_DIR), existing])
    return env


def npm_command() -> str:
    return "npm.cmd" if os.name == "nt" else "npm"


def build_subprocess_steps(
    *,
    full_backend: bool,
    skip_frontend: bool,
    skip_benchmark_gate: bool = False,
    skip_evidence_pack: bool = False,
    evidence_output_dir: Path = Path("tmp/release-preflight-evidence"),
    env_file: Path | None = None,
    require_production_env: bool = False,
    dependency_review_file: Path | None = None,
    require_dependency_review: bool = False,
    external_acceptance_file: Path | None = None,
    require_external_acceptance: bool = False,
) -> list[CommandStep]:
    backend_tests = ["tests/backend"] if full_backend else TARGETED_BACKEND_TESTS
    steps = [
        CommandStep(
            name="backend full test suite" if full_backend else "backend release gate tests",
            command=[sys.executable, "-m", "pytest", "-q", *backend_tests],
            cwd=REPO_ROOT,
            env=backend_env(),
            timeout_sec=420 if full_backend else 240,
        )
    ]
    if not skip_benchmark_gate:
        steps.append(
            CommandStep(
                name="benchmark release gate",
                command=[
                    sys.executable,
                    "scripts/benchmark_release_gate.py",
                    "--output",
                    str(evidence_output_dir / "benchmark-release-gate.json"),
                ],
                cwd=REPO_ROOT,
                env=backend_env(),
                timeout_sec=60,
            )
        )
    if not skip_evidence_pack:
        evidence_manifest_path = evidence_output_dir / "release-evidence-pack.json"
        evidence_generation_command = [
            sys.executable,
            "scripts/release_evidence_pack.py",
            "--output-dir",
            str(evidence_output_dir),
        ]
        if env_file is not None:
            evidence_generation_command.extend(["--env-file", str(env_file)])
        if require_production_env:
            evidence_generation_command.append("--require-production-env")
        if dependency_review_file is not None:
            evidence_generation_command.extend(["--dependency-review-file", str(dependency_review_file)])
        if require_dependency_review:
            evidence_generation_command.append("--require-dependency-review")
        if external_acceptance_file is not None:
            evidence_generation_command.extend(["--external-acceptance-file", str(external_acceptance_file)])
        if require_external_acceptance:
            evidence_generation_command.append("--require-external-acceptance")
        steps.extend(
            [
                CommandStep(
                    name="release evidence pack generation",
                    command=evidence_generation_command,
                    cwd=REPO_ROOT,
                    timeout_sec=120,
                ),
                CommandStep(
                    name="release evidence pack verification",
                    command=[
                        sys.executable,
                        "scripts/verify_release_evidence_pack.py",
                        "--manifest",
                        str(evidence_manifest_path),
                        "--output",
                        str(evidence_output_dir / "release-evidence-verification.json"),
                    ],
                    cwd=REPO_ROOT,
                    timeout_sec=60,
                ),
            ]
        )
        if env_file is not None:
            steps.append(
                CommandStep(
                    name="release evidence production env verification",
                    command=[
                        sys.executable,
                        "scripts/verify_production_env_audit.py",
                        "--report",
                        str(evidence_output_dir / "production-env-audit.json"),
                        "--env-file",
                        str(env_file),
                        "--output",
                        str(evidence_output_dir / "production-env-verification.json"),
                    ],
                    cwd=REPO_ROOT,
                    timeout_sec=60,
                )
            )
        dependency_review_verification_command = [
            sys.executable,
            "scripts/verify_dependency_review_audit.py",
            "--report",
            str(evidence_output_dir / "dependency-review-audit.json"),
            "--output",
            str(evidence_output_dir / "dependency-review-verification.json"),
        ]
        if dependency_review_file is None and not require_dependency_review:
            dependency_review_verification_command.append("--allow-non-passed-report")
        steps.append(
            CommandStep(
                name="release evidence dependency review verification",
                command=dependency_review_verification_command,
                cwd=REPO_ROOT,
                timeout_sec=60,
            )
        )
        if external_acceptance_file is not None:
            steps.append(
                CommandStep(
                    name="release evidence external acceptance verification",
                    command=[
                        sys.executable,
                        "scripts/verify_external_acceptance_audit.py",
                        "--report",
                        str(evidence_output_dir / "external-acceptance-audit.json"),
                        "--output",
                        str(evidence_output_dir / "external-acceptance-verification.json"),
                    ],
                    cwd=REPO_ROOT,
                    timeout_sec=60,
                )
            )
    if not skip_frontend:
        steps.append(
            CommandStep(
                name="frontend production build",
                command=[npm_command(), "run", "build"],
                cwd=FRONTEND_DIR,
                timeout_sec=180,
            )
        )
    return steps


def run_command_step(step: CommandStep) -> GateResult:
    print(f"\n==> {step.name}", flush=True)
    print(f"cwd: {step.cwd}", flush=True)
    print("cmd: " + " ".join(step.command), flush=True)
    started = time.perf_counter()
    try:
        result = subprocess.run(step.command, cwd=step.cwd, env=step.env, timeout=step.timeout_sec, check=False)
    except subprocess.TimeoutExpired:
        duration_sec = round(time.perf_counter() - started, 2)
        return GateResult(
            name=step.name,
            kind="command",
            status="failed",
            duration_sec=duration_sec,
            command=step.command,
            cwd=str(step.cwd),
            timeout_sec=step.timeout_sec,
            error=f"timed out after {step.timeout_sec} seconds",
        )
    duration_sec = round(time.perf_counter() - started, 2)
    return GateResult(
        name=step.name,
        kind="command",
        status="passed" if result.returncode == 0 else "failed",
        duration_sec=duration_sec,
        command=step.command,
        cwd=str(step.cwd),
        timeout_sec=step.timeout_sec,
        exit_code=result.returncode,
        error=None if result.returncode == 0 else f"exit code {result.returncode}",
    )


def enrich_preflight_gate_result(result: GateResult, *, evidence_output_dir: Path) -> GateResult:
    if result.name == "benchmark release gate":
        return replace(result, payload=build_benchmark_gate_payload(evidence_output_dir))
    if result.name == "release evidence pack generation":
        return replace(result, payload=build_evidence_pack_manifest_payload(evidence_output_dir))
    if result.name == "release evidence pack verification":
        return replace(result, payload=build_evidence_pack_verification_payload(evidence_output_dir))
    if result.name == "release evidence production env verification":
        return replace(
            result,
            payload=build_evidence_verification_file_payload(
                evidence_output_dir / "production-env-verification.json"
            ),
        )
    if result.name == "release evidence dependency review verification":
        return replace(
            result,
            payload=build_evidence_verification_file_payload(
                evidence_output_dir / "dependency-review-verification.json"
            ),
        )
    if result.name == "release evidence external acceptance verification":
        return replace(
            result,
            payload=build_evidence_verification_file_payload(
                evidence_output_dir / "external-acceptance-verification.json"
            ),
        )
    return result


def build_evidence_pack_manifest_payload(evidence_output_dir: Path) -> dict[str, Any]:
    output_dir, manifest_path, verification_path = evidence_pack_paths(evidence_output_dir)
    payload: dict[str, Any] = {
        "output_dir": str(output_dir),
        "manifest_path": str(manifest_path),
        "verification_path": str(verification_path),
        "manifest_exists": manifest_path.is_file(),
    }
    manifest = read_json_if_present(manifest_path)
    if manifest.get("error"):
        payload["manifest_error"] = manifest["error"]
        return payload
    if manifest.get("exists"):
        data = manifest["data"]
        payload.update(
            {
                "pack_status": data.get("status"),
                "pack_summary": data.get("summary"),
                "artifacts": compact_evidence_artifacts(data.get("artifacts")),
            }
        )
    return payload


def build_benchmark_gate_payload(evidence_output_dir: Path) -> dict[str, Any]:
    output_dir = evidence_output_dir if evidence_output_dir.is_absolute() else REPO_ROOT / evidence_output_dir
    report_path = output_dir / "benchmark-release-gate.json"
    payload: dict[str, Any] = {"report_path": str(report_path), "exists": report_path.is_file()}
    report = read_json_if_present(report_path)
    if report.get("error"):
        payload["error"] = report["error"]
        return payload
    if report.get("exists"):
        data = report["data"]
        payload.update(
            {
                "status": data.get("status"),
                "thresholds": data.get("thresholds"),
                "summary": data.get("summary"),
                "case_count": len(data.get("cases") or []),
            }
        )
    return payload


def build_evidence_pack_verification_payload(evidence_output_dir: Path) -> dict[str, Any]:
    output_dir, manifest_path, verification_path = evidence_pack_paths(evidence_output_dir)
    payload: dict[str, Any] = build_evidence_pack_manifest_payload(evidence_output_dir)
    payload.update(
        {
            "output_dir": str(output_dir),
            "manifest_path": str(manifest_path),
            "verification_path": str(verification_path),
            "verification_report_exists": verification_path.is_file(),
        }
    )
    verification = read_json_if_present(verification_path)
    if verification.get("error"):
        payload["verification_error"] = verification["error"]
        return payload
    if verification.get("exists"):
        data = verification["data"]
        payload.update(
            {
                "verification_status": data.get("status"),
                "verification_summary": data.get("summary"),
            }
        )
    return payload


def build_evidence_verification_file_payload(path: Path) -> dict[str, Any]:
    resolved_path = path if path.is_absolute() else REPO_ROOT / path
    payload: dict[str, Any] = {
        "path": str(resolved_path),
        "exists": resolved_path.is_file(),
    }
    verification = read_json_if_present(resolved_path)
    if verification.get("error"):
        payload["error"] = verification["error"]
        return payload
    if verification.get("exists"):
        data = verification["data"]
        payload.update(
            {
                "status": data.get("status"),
                "report_status": data.get("report_status"),
                "report_path": data.get("report_path"),
                "summary": data.get("summary"),
            }
        )
    return payload


def evidence_pack_paths(evidence_output_dir: Path) -> tuple[Path, Path, Path]:
    output_dir = evidence_output_dir if evidence_output_dir.is_absolute() else REPO_ROOT / evidence_output_dir
    return (
        output_dir,
        output_dir / "release-evidence-pack.json",
        output_dir / "release-evidence-verification.json",
    )


def read_json_if_present(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"exists": False}
    try:
        return {"exists": True, "data": json.loads(path.read_text(encoding="utf-8"))}
    except Exception as exc:
        return {"exists": True, "error": str(exc)}


def compact_evidence_artifacts(artifacts: Any) -> list[dict[str, Any]]:
    if not isinstance(artifacts, list):
        return []
    compacted: list[dict[str, Any]] = []
    for item in artifacts:
        if not isinstance(item, dict):
            continue
        compacted.append(
            {
                "name": item.get("name"),
                "required": item.get("required"),
                "status": item.get("status"),
                "relative_path": item.get("relative_path"),
                "size_bytes": item.get("size_bytes"),
                "sha256": item.get("sha256"),
                "summary": compact_evidence_artifact_summary(item.get("summary")),
            }
        )
    return compacted


def compact_evidence_artifact_summary(summary: Any) -> dict[str, Any]:
    if not isinstance(summary, dict):
        return {}
    compacted: dict[str, Any] = {}
    for key, value in summary.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            compacted[str(key)] = value
    return compacted


def parse_smoke_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("smoke port must be an integer from 0 to 65535") from exc
    if port < 0 or port > 65535:
        raise argparse.ArgumentTypeError("smoke port must be an integer from 0 to 65535")
    return port


def choose_smoke_port(requested_port: int) -> int:
    if requested_port != 0:
        return requested_port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run_api_smoke(port: int, timeout_sec: int) -> GateResult:
    print("\n==> API health smoke", flush=True)
    started = time.perf_counter()
    env = backend_env()
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            command,
            cwd=BACKEND_DIR,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + timeout_sec
        health_payload = ""
        ready_payload = ""
        while time.monotonic() < deadline:
            if process.poll() is not None:
                stdout, stderr = process.communicate(timeout=5)
                raise RuntimeError(
                    "API smoke server exited early\n"
                    f"stdout:\n{stdout[-4000:]}\n"
                    f"stderr:\n{stderr[-4000:]}"
                )
            try:
                health_payload = _http_get(f"http://127.0.0.1:{port}/api/health")
                ready_payload = _http_get(f"http://127.0.0.1:{port}/api/health/ready")
                break
            except URLError:
                time.sleep(0.5)
        else:
            raise RuntimeError(f"API smoke did not become healthy within {timeout_sec} seconds")
        print(f"health: {health_payload}", flush=True)
        print(f"ready: {ready_payload}", flush=True)
        return GateResult(
            name="API health smoke",
            kind="smoke",
            status="passed",
            duration_sec=round(time.perf_counter() - started, 2),
            command=command,
            cwd=str(BACKEND_DIR),
            timeout_sec=timeout_sec,
            payload={"port": port, "health": health_payload, "ready": ready_payload},
        )
    except Exception as exc:
        return GateResult(
            name="API health smoke",
            kind="smoke",
            status="failed",
            duration_sec=round(time.perf_counter() - started, 2),
            command=command,
            cwd=str(BACKEND_DIR),
            timeout_sec=timeout_sec,
            payload={"port": port},
            error=str(exc),
        )
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


def _http_get(url: str) -> str:
    with urlopen(url, timeout=2) as response:
        body = response.read().decode("utf-8")
        if response.status >= 400:
            raise URLError(f"{url} returned {response.status}: {body}")
        return body


def clean_pycache() -> int:
    removed = 0
    for root in (BACKEND_DIR, REPO_ROOT / "tests", SCRIPTS_DIR):
        if not root.exists():
            continue
        for path in root.rglob("__pycache__"):
            if path.is_dir():
                shutil.rmtree(path)
                removed += 1
    return removed


def cleanup_pycache_result(*, keep_pycache: bool) -> GateResult:
    started = time.perf_counter()
    if keep_pycache:
        return GateResult(
            name="cleanup pycache",
            kind="cleanup",
            status="skipped",
            duration_sec=round(time.perf_counter() - started, 2),
            payload={"removed_count": 0},
        )
    removed = clean_pycache()
    print(f"\nremoved __pycache__ directories: {removed}", flush=True)
    return GateResult(
        name="cleanup pycache",
        kind="cleanup",
        status="passed",
        duration_sec=round(time.perf_counter() - started, 2),
        payload={"removed_count": removed},
    )


def skipped_gate_result(name: str, kind: str) -> GateResult:
    return GateResult(name=name, kind=kind, status="skipped", duration_sec=0)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run non-customer-dependent release preflight gates.")
    parser.add_argument("--full-backend", action="store_true", help="Run all backend tests instead of the targeted gate.")
    parser.add_argument("--skip-frontend", action="store_true", help="Skip npm production build.")
    parser.add_argument("--skip-benchmark-gate", action="store_true", help="Skip deterministic benchmark release gate.")
    parser.add_argument("--skip-evidence-pack", action="store_true", help="Skip local release evidence pack generation and verification.")
    parser.add_argument(
        "--evidence-output-dir",
        type=Path,
        default=Path("tmp/release-preflight-evidence"),
        help="Directory for the temporary release evidence pack generated by preflight.",
    )
    parser.add_argument("--env-file", type=Path, help="Optional production env file to audit in the evidence pack.")
    parser.add_argument(
        "--require-production-env",
        action="store_true",
        help="Fail evidence pack generation when --env-file is omitted or the production env audit fails.",
    )
    parser.add_argument("--dependency-review-file", type=Path, help="Optional dependency review acknowledgement JSON for the evidence pack.")
    parser.add_argument(
        "--require-dependency-review",
        action="store_true",
        help="Fail evidence pack generation unless review-required dependencies have approved acknowledgements.",
    )
    parser.add_argument("--external-acceptance-file", type=Path, help="Optional real external release acceptance manifest JSON.")
    parser.add_argument(
        "--require-external-acceptance",
        action="store_true",
        help="Fail evidence pack generation unless real external release acceptance evidence passes.",
    )
    parser.add_argument("--skip-smoke", action="store_true", help="Skip temporary API health smoke.")
    parser.add_argument(
        "--smoke-port",
        type=parse_smoke_port,
        default=0,
        help="Port for the temporary API smoke server; 0 chooses an available local port.",
    )
    parser.add_argument("--smoke-timeout", type=int, default=30, help="Seconds to wait for API smoke readiness.")
    parser.add_argument("--keep-pycache", action="store_true", help="Do not remove generated __pycache__ directories.")
    parser.add_argument("--report-path", type=Path, help="Write a JSON release preflight report to this path.")
    parser.add_argument("--inventory-path", type=Path, help="Write a full dependency and license inventory JSON.")
    parser.add_argument("--skip-inventory", action="store_true", help="Do not include dependency inventory summary.")
    parser.add_argument(
        "--fail-on-dependency-review",
        action="store_true",
        help="Fail when dependency inventory has review-required items.",
    )
    return parser.parse_args(argv)


def load_inventory_module():
    spec = importlib.util.spec_from_file_location("release_inventory", INVENTORY_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load inventory script: {INVENTORY_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def build_dependency_inventory(args: argparse.Namespace) -> dict | None:
    if args.skip_inventory:
        return None
    module = load_inventory_module()
    inventory = module.build_dependency_inventory(REPO_ROOT)
    if args.inventory_path:
        output_path = module.write_inventory(args.inventory_path, inventory, REPO_ROOT)
        print(f"\ndependency inventory: {output_path}", flush=True)
    return inventory


def build_release_report(
    *,
    args: argparse.Namespace,
    gate_results: list[GateResult],
    cleanup_result: GateResult | None,
    dependency_inventory: dict | None,
    passed: bool,
) -> dict:
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "repo_root": str(REPO_ROOT),
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "options": {
            "full_backend": bool(args.full_backend),
            "skip_frontend": bool(args.skip_frontend),
            "skip_benchmark_gate": bool(args.skip_benchmark_gate),
            "skip_evidence_pack": bool(args.skip_evidence_pack),
            "evidence_output_dir": str(args.evidence_output_dir),
            "env_file": str(args.env_file) if args.env_file else None,
            "require_production_env": bool(args.require_production_env),
            "dependency_review_file": str(args.dependency_review_file) if args.dependency_review_file else None,
            "require_dependency_review": bool(args.require_dependency_review),
            "external_acceptance_file": str(args.external_acceptance_file) if args.external_acceptance_file else None,
            "require_external_acceptance": bool(args.require_external_acceptance),
            "skip_smoke": bool(args.skip_smoke),
            "smoke_port": int(args.smoke_port),
            "smoke_timeout": int(args.smoke_timeout),
            "keep_pycache": bool(args.keep_pycache),
            "skip_inventory": bool(args.skip_inventory),
            "fail_on_dependency_review": bool(args.fail_on_dependency_review),
        },
        "passed": passed,
        "gates": [asdict(result) for result in gate_results],
        "cleanup": asdict(cleanup_result) if cleanup_result else None,
        "dependency_inventory_summary": dependency_inventory.get("summary") if dependency_inventory else None,
    }


def write_release_report(report_path: Path, report: dict) -> Path:
    output_path = report_path if report_path.is_absolute() else REPO_ROOT / report_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\nrelease report: {output_path}", flush=True)
    return output_path


def dependency_review_required_count(dependency_inventory: dict | None) -> int:
    if not dependency_inventory:
        return 0
    summary = dependency_inventory.get("summary")
    if not isinstance(summary, dict):
        return 0
    value = summary.get("review_required_count")
    return value if isinstance(value, int) else 0


def dependency_review_failure_message(review_required_count: int, dependency_inventory: dict | None = None) -> str:
    summary = dependency_inventory.get("summary") if isinstance(dependency_inventory, dict) else None
    if isinstance(summary, dict):
        missing_count = summary.get("release_blocking_missing_install_count")
        review_required_items = summary.get("review_required")
        typed_review_required_items = (
            [item for item in review_required_items if isinstance(item, dict)]
            if isinstance(review_required_items, list)
            else []
        )
        if (
            isinstance(missing_count, int)
            and missing_count > 0
            and isinstance(review_required_items, list)
            and len(typed_review_required_items) == len(review_required_items)
            and len(typed_review_required_items) == review_required_count
            and all(is_missing_install_review_item(item) for item in typed_review_required_items)
        ):
            return (
                f"dependency inventory has {review_required_count} review-required item(s) because "
                f"{missing_count} release-blocking package(s) are missing in this environment; "
                "regenerate and use the release image dependency inventory before go-live"
            )
    return f"dependency inventory has {review_required_count} review-required item(s)"


def is_missing_install_review_item(item: dict[str, Any]) -> bool:
    reason = str(item.get("reason") or "").lower()
    return item.get("installed") is False and "regenerate inventory in the release image" in reason


def resolve_repo_path(path: Path) -> Path:
    return path if path.is_absolute() else REPO_ROOT / path


def preflight_input_failure_message(args: argparse.Namespace, dependency_inventory: dict | None) -> str | None:
    if args.require_production_env and args.env_file is None:
        return "production env audit requires --env-file"
    if args.env_file is not None and not resolve_repo_path(args.env_file).is_file():
        return f"production env file does not exist: {args.env_file}"
    if args.dependency_review_file is not None and not resolve_repo_path(args.dependency_review_file).is_file():
        return f"dependency review file does not exist: {args.dependency_review_file}"
    if args.external_acceptance_file is not None and not resolve_repo_path(args.external_acceptance_file).is_file():
        return f"external acceptance file does not exist: {args.external_acceptance_file}"
    if args.require_external_acceptance and args.external_acceptance_file is None:
        return "external acceptance file is required"
    review_required_count = dependency_review_required_count(dependency_inventory)
    if args.require_dependency_review and review_required_count and args.dependency_review_file is None:
        return (
            "dependency review file is required because "
            f"{dependency_review_failure_message(review_required_count, dependency_inventory)}"
        )
    return None


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    gate_results: list[GateResult] = []
    cleanup_result: GateResult | None = None
    dependency_inventory: dict | None = None
    passed = False
    try:
        dependency_inventory = build_dependency_inventory(args)
    except Exception as exc:
        cleanup_result = cleanup_pycache_result(keep_pycache=args.keep_pycache)
        report = build_release_report(
            args=args,
            gate_results=gate_results,
            cleanup_result=cleanup_result,
            dependency_inventory=None,
            passed=False,
        )
        if args.report_path:
            write_release_report(args.report_path, report)
        raise SystemExit(f"dependency inventory failed: {exc}") from exc
    review_required_count = dependency_review_required_count(dependency_inventory)
    if args.fail_on_dependency_review and review_required_count:
        cleanup_result = cleanup_pycache_result(keep_pycache=args.keep_pycache)
        report = build_release_report(
            args=args,
            gate_results=gate_results,
            cleanup_result=cleanup_result,
            dependency_inventory=dependency_inventory,
            passed=False,
        )
        if args.report_path:
            write_release_report(args.report_path, report)
        raise SystemExit(dependency_review_failure_message(review_required_count, dependency_inventory))
    input_failure = preflight_input_failure_message(args, dependency_inventory)
    if input_failure:
        cleanup_result = cleanup_pycache_result(keep_pycache=args.keep_pycache)
        report = build_release_report(
            args=args,
            gate_results=gate_results,
            cleanup_result=cleanup_result,
            dependency_inventory=dependency_inventory,
            passed=False,
        )
        if args.report_path:
            write_release_report(args.report_path, report)
        raise SystemExit(input_failure)
    for step in build_subprocess_steps(
        full_backend=args.full_backend,
        skip_frontend=args.skip_frontend,
        skip_benchmark_gate=args.skip_benchmark_gate,
        skip_evidence_pack=args.skip_evidence_pack,
        evidence_output_dir=args.evidence_output_dir,
        env_file=args.env_file,
        require_production_env=args.require_production_env,
        dependency_review_file=args.dependency_review_file,
        require_dependency_review=args.require_dependency_review,
        external_acceptance_file=args.external_acceptance_file,
        require_external_acceptance=args.require_external_acceptance,
    ):
        result = run_command_step(step)
        result = enrich_preflight_gate_result(result, evidence_output_dir=args.evidence_output_dir)
        gate_results.append(result)
        if result.status != "passed":
            cleanup_result = cleanup_pycache_result(keep_pycache=args.keep_pycache)
            report = build_release_report(
                args=args,
                gate_results=gate_results,
                cleanup_result=cleanup_result,
                dependency_inventory=dependency_inventory,
                passed=False,
            )
            if args.report_path:
                write_release_report(args.report_path, report)
            raise SystemExit(f"{step.name} failed: {result.error}")
    if args.skip_smoke:
        gate_results.append(skipped_gate_result("API health smoke", "smoke"))
    else:
        smoke_port = choose_smoke_port(args.smoke_port)
        smoke_result = run_api_smoke(smoke_port, args.smoke_timeout)
        gate_results.append(smoke_result)
        if smoke_result.status != "passed":
            cleanup_result = cleanup_pycache_result(keep_pycache=args.keep_pycache)
            report = build_release_report(
                args=args,
                gate_results=gate_results,
                cleanup_result=cleanup_result,
                dependency_inventory=dependency_inventory,
                passed=False,
            )
            if args.report_path:
                write_release_report(args.report_path, report)
            raise SystemExit(f"API health smoke failed: {smoke_result.error}")
    cleanup_result = cleanup_pycache_result(keep_pycache=args.keep_pycache)
    passed = True
    if args.report_path:
        report = build_release_report(
            args=args,
            gate_results=gate_results,
            cleanup_result=cleanup_result,
            dependency_inventory=dependency_inventory,
            passed=passed,
        )
        write_release_report(args.report_path, report)
    print("\nrelease preflight passed", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
