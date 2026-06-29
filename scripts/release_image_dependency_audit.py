from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DOCKERFILE = Path("backend/Dockerfile")
DEFAULT_IMAGE_TAG = "packaging-nesting-api:release-inventory"
DEFAULT_INVENTORY_OUTPUT = Path("artifacts/dependency-inventory-release-image.json")
DEFAULT_REVIEW_OUTPUT = Path("artifacts/dependency-review-audit-release-image.json")
DEFAULT_REPORT_OUTPUT = Path("artifacts/release-image-dependency-audit.json")
CONTAINER_WORKDIR = "/workspace"


@dataclass(frozen=True)
class CommandExecution:
    name: str
    command: list[str]
    cwd: str
    timeout_sec: int
    exit_code: int
    duration_sec: float
    stdout_tail: str = ""
    stderr_tail: str = ""


CommandRunner = Callable[[str, list[str], Path, int], CommandExecution]


def build_release_image_dependency_audit(
    *,
    image_tag: str = DEFAULT_IMAGE_TAG,
    dockerfile: Path = DEFAULT_DOCKERFILE,
    inventory_output: Path = DEFAULT_INVENTORY_OUTPUT,
    review_output: Path = DEFAULT_REVIEW_OUTPUT,
    dependency_review_file: Path | None = None,
    skip_build: bool = False,
    docker_executable: str = "docker",
    command_timeout_sec: int = 600,
    command_runner: CommandRunner | None = None,
) -> dict[str, Any]:
    runner = command_runner or run_command
    resolved_dockerfile = resolve_repo_path(dockerfile)
    resolved_inventory_output = resolve_repo_path(inventory_output)
    resolved_review_output = resolve_repo_path(review_output)
    resolved_dependency_review_file = resolve_repo_path(dependency_review_file) if dependency_review_file else None

    commands: list[CommandExecution] = []
    errors: list[str] = []
    warnings: list[str] = []

    if not resolved_dockerfile.is_file():
        errors.append(f"backend Dockerfile does not exist: {resolved_dockerfile}")

    if resolved_dependency_review_file is not None and not resolved_dependency_review_file.is_file():
        errors.append(f"dependency review file does not exist: {resolved_dependency_review_file}")

    if errors:
        return build_report(
            image_tag=image_tag,
            dockerfile=resolved_dockerfile,
            inventory_output=resolved_inventory_output,
            review_output=resolved_review_output,
            dependency_review_file=resolved_dependency_review_file,
            commands=commands,
            errors=errors,
            warnings=warnings,
        )

    if not skip_build:
        commands.append(
            runner(
                "docker_build",
                [
                    docker_executable,
                    "build",
                    "-f",
                    str(resolved_dockerfile),
                    "-t",
                    image_tag,
                    str(REPO_ROOT),
                ],
                REPO_ROOT,
                command_timeout_sec,
            )
        )

    if not commands or commands[-1].exit_code == 0:
        resolved_inventory_output.parent.mkdir(parents=True, exist_ok=True)
        commands.append(
            runner(
                "release_image_inventory",
                docker_run_command(
                    docker_executable=docker_executable,
                    image_tag=image_tag,
                    script="scripts/release_inventory.py",
                    script_args=[
                        "--output",
                        container_path_for_repo_file(resolved_inventory_output),
                    ],
                ),
                REPO_ROOT,
                command_timeout_sec,
            )
        )

    if commands and commands[-1].exit_code == 0:
        resolved_review_output.parent.mkdir(parents=True, exist_ok=True)
        review_args = [
            "--inventory",
            container_path_for_repo_file(resolved_inventory_output),
            "--output",
            container_path_for_repo_file(resolved_review_output),
        ]
        if resolved_dependency_review_file is not None:
            review_args.extend(["--review-file", container_path_for_repo_file(resolved_dependency_review_file)])
        commands.append(
            runner(
                "release_image_dependency_review",
                docker_run_command(
                    docker_executable=docker_executable,
                    image_tag=image_tag,
                    script="scripts/dependency_review_audit.py",
                    script_args=review_args,
                ),
                REPO_ROOT,
                command_timeout_sec,
            )
        )

    for command in commands:
        if command.exit_code != 0:
            errors.append(f"{command.name} failed with exit code {command.exit_code}")

    inventory_payload = read_json_if_present(resolved_inventory_output, errors, "release image dependency inventory")
    review_payload = read_json_if_present(resolved_review_output, errors, "release image dependency review audit")

    inventory_summary = inventory_payload.get("summary") if isinstance(inventory_payload, dict) else {}
    review_summary = review_payload.get("summary") if isinstance(review_payload, dict) else {}
    missing_install_count = int_value(inventory_summary.get("missing_install_count")) if isinstance(inventory_summary, dict) else None
    release_blocking_missing_install_count = (
        int_value(inventory_summary.get("release_blocking_missing_install_count"))
        if isinstance(inventory_summary, dict)
        else None
    )
    blocking_missing_install_count = (
        release_blocking_missing_install_count
        if release_blocking_missing_install_count is not None
        else missing_install_count
    )
    review_required_count = int_value(inventory_summary.get("review_required_count")) if isinstance(inventory_summary, dict) else None
    review_status = review_payload.get("status") if isinstance(review_payload, dict) else None

    if blocking_missing_install_count not in {0, None}:
        errors.append(
            "release image dependency inventory has "
            f"{blocking_missing_install_count} release-blocking missing installed package(s)"
        )
    if review_status not in {"passed", None}:
        errors.append(f"release image dependency review audit must be passed, got {review_status}")
    if review_required_count not in {0, None} and review_status == "passed":
        approved_count = int_value(review_summary.get("approved_count")) if isinstance(review_summary, dict) else None
        if approved_count != review_required_count:
            errors.append(
                "release image dependency review approved_count must match review_required_count: "
                f"{approved_count} != {review_required_count}"
            )

    return build_report(
        image_tag=image_tag,
        dockerfile=resolved_dockerfile,
        inventory_output=resolved_inventory_output,
        review_output=resolved_review_output,
        dependency_review_file=resolved_dependency_review_file,
        commands=commands,
        errors=errors,
        warnings=warnings,
        inventory_summary=dict(inventory_summary) if isinstance(inventory_summary, dict) else {},
        review_summary=dict(review_summary) if isinstance(review_summary, dict) else {},
        review_status=str(review_status) if review_status else None,
    )


def docker_run_command(
    *,
    docker_executable: str,
    image_tag: str,
    script: str,
    script_args: list[str],
) -> list[str]:
    return [
        docker_executable,
        "run",
        "--rm",
        "--mount",
        f"type=bind,source={docker_mount_source(REPO_ROOT)},target={CONTAINER_WORKDIR}",
        "-w",
        CONTAINER_WORKDIR,
        image_tag,
        "python",
        script,
        *script_args,
    ]


def run_command(name: str, command: list[str], cwd: Path, timeout_sec: int) -> CommandExecution:
    started = time.perf_counter()
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            timeout=timeout_sec,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        exit_code = result.returncode
        stdout = result.stdout or ""
        stderr = result.stderr or ""
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else str(exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else str(exc.stderr or "")
        stderr = (stderr + f"\ncommand timed out after {timeout_sec} seconds").strip()
    return CommandExecution(
        name=name,
        command=command,
        cwd=str(cwd),
        timeout_sec=timeout_sec,
        exit_code=exit_code,
        duration_sec=round(time.perf_counter() - started, 2),
        stdout_tail=tail(stdout),
        stderr_tail=tail(stderr),
    )


def build_report(
    *,
    image_tag: str,
    dockerfile: Path,
    inventory_output: Path,
    review_output: Path,
    dependency_review_file: Path | None,
    commands: list[CommandExecution],
    errors: list[str],
    warnings: list[str],
    inventory_summary: dict[str, Any] | None = None,
    review_summary: dict[str, Any] | None = None,
    review_status: str | None = None,
) -> dict[str, Any]:
    failed_commands = [command for command in commands if command.exit_code != 0]
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "passed" if not errors and not failed_commands else "failed",
        "repo_root": str(REPO_ROOT),
        "image_tag": image_tag,
        "dockerfile": str(dockerfile),
        "inventory_output": str(inventory_output),
        "dependency_review_output": str(review_output),
        "dependency_review_file": str(dependency_review_file) if dependency_review_file else None,
        "summary": {
            "command_count": len(commands),
            "failed_command_count": len(failed_commands),
            "missing_install_count": (inventory_summary or {}).get("missing_install_count"),
            "release_blocking_missing_install_count": (inventory_summary or {}).get(
                "release_blocking_missing_install_count"
            ),
            "review_required_count": (inventory_summary or {}).get("review_required_count"),
            "dependency_review_status": review_status,
            "error_count": len(errors) + len(failed_commands),
            "warning_count": len(warnings),
        },
        "commands": [asdict(command) for command in commands],
        "inventory_summary": inventory_summary or {},
        "dependency_review_summary": review_summary or {},
        "errors": errors,
        "warnings": warnings,
    }


def read_json_if_present(path: Path, errors: list[str], label: str) -> dict[str, Any]:
    if not path.is_file():
        errors.append(f"{label} was not written: {path}")
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        errors.append(f"{label} could not be read: {exc}")
        return {}


def container_path_for_repo_file(path: Path) -> str:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"path must be inside the repository: {path}") from exc
    return f"{CONTAINER_WORKDIR}/{relative.as_posix()}"


def docker_mount_source(path: Path) -> str:
    resolved = path.resolve()
    if os.name == "nt":
        return resolved.as_posix()
    return str(resolved)


def resolve_repo_path(path: Path | None) -> Path | None:
    if path is None:
        return None
    return path if path.is_absolute() else REPO_ROOT / path


def int_value(value: Any) -> int | None:
    return value if isinstance(value, int) else None


def tail(value: str, limit: int = 4000) -> str:
    return value[-limit:] if len(value) > limit else value


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    output_path = resolve_repo_path(path)
    assert output_path is not None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return output_path


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate dependency inventory and review audit inside the backend release image.")
    parser.add_argument("--image-tag", default=DEFAULT_IMAGE_TAG, help="Docker image tag to build or reuse.")
    parser.add_argument("--dockerfile", type=Path, default=DEFAULT_DOCKERFILE, help="Backend Dockerfile path.")
    parser.add_argument("--inventory-output", type=Path, default=DEFAULT_INVENTORY_OUTPUT)
    parser.add_argument("--review-output", type=Path, default=DEFAULT_REVIEW_OUTPUT)
    parser.add_argument("--dependency-review-file", type=Path, help="Optional dependency acknowledgement file for review-required items.")
    parser.add_argument("--skip-build", action="store_true", help="Reuse --image-tag without running docker build.")
    parser.add_argument("--docker-executable", default="docker")
    parser.add_argument("--command-timeout-sec", type=int, default=600)
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT_OUTPUT, help="JSON report path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    report = build_release_image_dependency_audit(
        image_tag=args.image_tag,
        dockerfile=args.dockerfile,
        inventory_output=args.inventory_output,
        review_output=args.review_output,
        dependency_review_file=args.dependency_review_file,
        skip_build=args.skip_build,
        docker_executable=args.docker_executable,
        command_timeout_sec=args.command_timeout_sec,
    )
    output_path = write_json(args.output, report)
    summary = report["summary"]
    print(
        "release image dependency audit "
        f"{report['status']} "
        f"report={output_path} "
        f"missing_install={summary['missing_install_count']} "
        f"review_required={summary['review_required_count']} "
        f"review_status={summary['dependency_review_status']}",
        flush=True,
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
