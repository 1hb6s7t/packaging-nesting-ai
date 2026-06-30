from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class CliRunResult:
    command: list[str]
    status: str
    duration_ms: int
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None


def resolve_cli_command(
    *,
    options: dict[str, Any],
    command_option: str,
    binary_option: str,
    env_var: str,
    default_binary_names: list[str],
) -> list[str] | None:
    raw_command = options.get(command_option) or options.get("external_solver_command")
    if raw_command:
        return parse_command(raw_command)
    binary = options.get(binary_option) or options.get("external_solver_binary") or os.environ.get(env_var)
    if binary:
        parsed = parse_command(binary)
        if not parsed:
            return None
        if len(parsed) > 1:
            return parsed
        resolved = resolve_binary(parsed[0])
        return [resolved] if resolved else None
    for name in default_binary_names:
        resolved = resolve_binary(name)
        if resolved:
            return [resolved]
    return None


def run_json_cli(command: list[str], payload: dict[str, Any], *, timeout_sec: int) -> CliRunResult:
    started = time.perf_counter()
    try:
        result = subprocess.run(
            command,
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return CliRunResult(
            command=command,
            status="timeout",
            duration_ms=int((time.perf_counter() - started) * 1000),
            stdout=exc.stdout if isinstance(exc.stdout, str) else "",
            stderr=exc.stderr if isinstance(exc.stderr, str) else "",
            error=f"external solver timed out after {timeout_sec} seconds",
        )
    except OSError as exc:
        return CliRunResult(
            command=command,
            status="failed",
            duration_ms=int((time.perf_counter() - started) * 1000),
            error=str(exc),
        )
    return CliRunResult(
        command=command,
        status="passed" if result.returncode == 0 else "failed",
        duration_ms=int((time.perf_counter() - started) * 1000),
        exit_code=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr,
        error=None if result.returncode == 0 else f"external solver exited with {result.returncode}",
    )


def parse_command(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str):
        return shlex.split(value, posix=os.name != "nt")
    return []


def resolve_binary(value: str) -> str | None:
    if os.path.exists(value):
        return value
    return shutil.which(value)
