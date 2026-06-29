from __future__ import annotations

import argparse
import copy
import json
import re
import secrets
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, get_origin
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.core.config import Settings, is_production_environment, production_security_setting_errors


SENSITIVE_KEY_MARKERS = (
    "PASSWORD",
    "SECRET",
    "TOKEN",
    "API_KEY",
    "KEY",
    "AUTH",
    "CREDENTIAL",
)

FULLY_REDACTED_KEYS = {
    "EXTERNAL_ALERT_WEBHOOK_URL",
}

SENSITIVE_QUERY_MARKERS = (
    "PASSWORD",
    "SECRET",
    "TOKEN",
    "API_KEY",
    "KEY",
    "AUTH",
    "SIGNATURE",
    "CREDENTIAL",
)

RECOMMENDED_PRODUCTION_KEYS = (
    "APP_ENV",
    "DATABASE_URL",
    "REDIS_URL",
    "STORAGE_BACKEND",
    "TASK_EXECUTION_BACKEND",
    "AUTH_SECRET_KEY",
    "DEFAULT_ADMIN_EMAIL",
    "DEFAULT_ADMIN_PASSWORD",
    "SECURITY_HEADERS_ENABLED",
    "SECURITY_HSTS_ENABLED",
)

PLACEHOLDER_MARKERS = (
    "<REPLACE",
    "REPLACE_WITH",
    "CHANGE_ME",
    "CHANGEME",
    "TODO",
)
TEMPLATE_DOMAIN_PATTERN = re.compile(r"(?:^|[/:@.])(?:[A-Za-z0-9-]+\.)*example(?:[.:/]|$)", re.IGNORECASE)
GENERATED_DRAFT_SECRET_KEYS = ("AUTH_SECRET_KEY", "DEFAULT_ADMIN_PASSWORD")


@dataclass(frozen=True)
class EnvParseResult:
    values: dict[str, str]
    errors: list[str]


@dataclass(frozen=True)
class EnvValueError:
    message: str


def build_production_env_draft(
    *,
    template_file: Path = REPO_ROOT / ".env.production.example",
    output_file: Path,
) -> dict[str, Any]:
    resolved_template = template_file if template_file.is_absolute() else REPO_ROOT / template_file
    resolved_output = output_file if output_file.is_absolute() else REPO_ROOT / output_file
    errors: list[str] = []
    if not resolved_template.is_file():
        errors.append(f"production env template does not exist: {resolved_template}")
        return production_env_draft_report(
            template_file=resolved_template,
            output_file=resolved_output,
            generated_secret_keys=[],
            placeholder_keys=[],
            audit_report={},
            errors=errors,
        )

    generated_values = {
        "AUTH_SECRET_KEY": secrets.token_urlsafe(48),
        "DEFAULT_ADMIN_PASSWORD": f"Admin-{secrets.token_urlsafe(18)}",
    }
    draft_text, generated_secret_keys = render_env_draft(
        resolved_template.read_text(encoding="utf-8-sig"),
        generated_values,
    )
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    resolved_output.write_text(draft_text, encoding="utf-8")
    parse_result = parse_env_file(resolved_output)
    placeholder_keys = placeholder_env_keys(parse_result.values)
    audit_report = build_env_audit_report(resolved_output)
    return production_env_draft_report(
        template_file=resolved_template,
        output_file=resolved_output,
        generated_secret_keys=generated_secret_keys,
        placeholder_keys=placeholder_keys,
        audit_report=audit_report,
        errors=parse_result.errors,
    )


def render_env_draft(template_text: str, generated_values: dict[str, str]) -> tuple[str, list[str]]:
    generated_secret_keys: list[str] = []
    seen_keys: set[str] = set()
    rendered_lines: list[str] = []
    for raw_line in template_text.splitlines():
        parsed_key = env_line_key(raw_line)
        normalized_key = parsed_key.upper() if parsed_key else None
        if normalized_key:
            seen_keys.add(normalized_key)
        if normalized_key in generated_values:
            rendered_lines.append(f"{normalized_key}={generated_values[normalized_key]}")
            generated_secret_keys.append(normalized_key)
        else:
            rendered_lines.append(raw_line)
    for key, value in generated_values.items():
        if key not in seen_keys:
            rendered_lines.append(f"{key}={value}")
            generated_secret_keys.append(key)
    return "\n".join(rendered_lines).rstrip() + "\n", sorted(set(generated_secret_keys))


def env_line_key(raw_line: str) -> str | None:
    stripped = raw_line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[7:].lstrip()
    if "=" not in stripped:
        return None
    key = stripped.split("=", 1)[0].strip()
    return key if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key) else None


def placeholder_env_keys(env_values: dict[str, str]) -> list[str]:
    return sorted(key for key, value in env_values.items() if is_placeholder_value(value))


def production_env_draft_report(
    *,
    template_file: Path,
    output_file: Path,
    generated_secret_keys: list[str],
    placeholder_keys: list[str],
    audit_report: dict[str, Any],
    errors: list[str],
) -> dict[str, Any]:
    audit_errors = list(audit_report.get("errors") or []) if isinstance(audit_report, dict) else []
    return {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "status": "failed" if errors else ("pending" if placeholder_keys or audit_errors else "ready"),
        "template_file": str(template_file),
        "output_file": str(output_file),
        "summary": {
            "generated_secret_count": len(generated_secret_keys),
            "placeholder_key_count": len(placeholder_keys),
            "audit_status": audit_report.get("status") if isinstance(audit_report, dict) else None,
            "audit_error_count": len(audit_errors),
            "error_count": len(errors),
        },
        "generated_secret_keys": generated_secret_keys,
        "placeholder_keys": placeholder_keys,
        "audit_errors": audit_errors,
        "errors": errors,
    }


def parse_env_file(env_file: Path) -> EnvParseResult:
    if not env_file.exists():
        return EnvParseResult(values={}, errors=[f"env file not found: {env_file}"])
    values: dict[str, str] = {}
    errors: list[str] = []
    first_seen_lines: dict[str, int] = {}
    for line_number, raw_line in enumerate(env_file.read_text(encoding="utf-8-sig").splitlines(), start=1):
        parsed = parse_env_line(raw_line, line_number)
        if parsed is None:
            continue
        if isinstance(parsed, str):
            errors.append(parsed)
            continue
        key, value = parsed
        normalized_key = key.upper()
        if normalized_key in first_seen_lines:
            errors.append(
                f"line {line_number}: duplicate environment key {key!r} "
                f"(first defined on line {first_seen_lines[normalized_key]})"
            )
        else:
            first_seen_lines[normalized_key] = line_number
        values[key] = value
    return EnvParseResult(values=values, errors=errors)


def parse_env_line(raw_line: str, line_number: int) -> tuple[str, str] | str | None:
    stripped = raw_line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[7:].lstrip()
    if "=" not in stripped:
        return f"line {line_number}: expected KEY=value"
    key, raw_value = stripped.split("=", 1)
    key = key.strip()
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
        return f"line {line_number}: invalid environment key {key!r}"
    value_or_error = parse_env_value(raw_value.strip(), line_number)
    if isinstance(value_or_error, EnvValueError):
        return value_or_error.message
    return key, value_or_error


def parse_env_value(raw_value: str, line_number: int) -> str | EnvValueError:
    if not raw_value:
        return ""
    if raw_value[0] in {"'", '"'}:
        return parse_quoted_env_value(raw_value, line_number)
    return strip_inline_comment(raw_value)


def parse_quoted_env_value(raw_value: str, line_number: int) -> str | EnvValueError:
    quote = raw_value[0]
    escaped = False
    for index in range(1, len(raw_value)):
        char = raw_value[index]
        if quote == '"' and escaped:
            escaped = False
            continue
        if quote == '"' and char == "\\":
            escaped = True
            continue
        if char == quote:
            tail = raw_value[index + 1 :].strip()
            if tail and not tail.startswith("#"):
                return EnvValueError(f"line {line_number}: unexpected text after quoted value")
            value = raw_value[1:index]
            if quote == '"':
                value = unescape_double_quoted_value(value)
            return value
    return EnvValueError(f"line {line_number}: unterminated quoted value")


def strip_inline_comment(raw_value: str) -> str:
    for index, char in enumerate(raw_value):
        if char == "#" and (index == 0 or raw_value[index - 1].isspace()):
            return raw_value[:index].rstrip()
    return raw_value.strip()


def unescape_double_quoted_value(value: str) -> str:
    escapes = {"n": "\n", "r": "\r", "t": "\t", '"': '"', "\\": "\\"}
    result: list[str] = []
    escaped = False
    for char in value:
        if escaped:
            result.append(escapes.get(char, char))
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        result.append(char)
    if escaped:
        result.append("\\")
    return "".join(result)


def build_settings_from_env_values(env_values: dict[str, str]) -> Settings:
    settings_input = default_settings_input()
    normalized_env = {key.upper(): value for key, value in env_values.items()}
    for field_name, field in Settings.model_fields.items():
        input_key = field.alias or field_name
        candidates = [str(input_key).upper(), field_name.upper()]
        for candidate in candidates:
            if candidate in normalized_env:
                settings_input[input_key] = coerce_env_value(field_name, normalized_env[candidate])
                break
    return Settings(**settings_input)


def default_settings_input() -> dict[str, Any]:
    settings_input: dict[str, Any] = {}
    for field_name, field in Settings.model_fields.items():
        input_key = field.alias or field_name
        settings_input[input_key] = copy.deepcopy(field.get_default(call_default_factory=True))
    return settings_input


def coerce_env_value(field_name: str, value: str) -> Any:
    field = Settings.model_fields[field_name]
    if get_origin(field.annotation) is list:
        return parse_list_value(value)
    return value


def parse_list_value(value: str) -> list[str]:
    stripped = value.strip()
    if not stripped:
        return []
    if stripped.startswith("["):
        parsed = json.loads(stripped)
        if not isinstance(parsed, list):
            raise ValueError("expected a JSON list")
        return [str(item) for item in parsed]
    return [item.strip() for item in stripped.split(",") if item.strip()]


def build_env_audit_report(env_file: Path) -> dict[str, Any]:
    resolved_env_file = env_file if env_file.is_absolute() else REPO_ROOT / env_file
    parse_result = parse_env_file(resolved_env_file)
    errors = list(parse_result.errors)
    settings: Settings | None = None
    if not errors:
        try:
            settings = build_settings_from_env_values(parse_result.values)
        except Exception as exc:
            errors.append(f"settings validation failed: {exc}")
    is_production = False
    if settings is not None:
        is_production = is_production_environment(settings.environment)
        if not is_production:
            errors.append("APP_ENV must be prod or production for production environment audit")
        errors.extend(production_security_setting_errors(settings))
    errors.extend(placeholder_value_errors(parse_result.values))
    errors.extend(template_domain_value_errors(parse_result.values))
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "env_file": str(resolved_env_file),
        "status": "passed" if not errors else "failed",
        "is_production": is_production,
        "error_count": len(errors),
        "errors": errors,
        "missing_recommended_keys": missing_recommended_keys(parse_result.values, settings),
        "redacted_settings": redacted_settings_snapshot(settings) if settings is not None else {},
    }
    return report


def placeholder_value_errors(env_values: dict[str, str]) -> list[str]:
    return [
        f"{key} contains a placeholder value and must be replaced before production audit"
        for key, value in sorted(env_values.items(), key=lambda item: item[0].upper())
        if is_placeholder_value(value)
    ]


def is_placeholder_value(value: str) -> bool:
    upper_value = value.upper()
    return any(marker in upper_value for marker in PLACEHOLDER_MARKERS)


def template_domain_value_errors(env_values: dict[str, str]) -> list[str]:
    return [
        f"{key} contains an example/template domain and must be replaced before production audit"
        for key, value in sorted(env_values.items(), key=lambda item: item[0].upper())
        if contains_template_domain(value)
    ]


def contains_template_domain(value: str) -> bool:
    return bool(TEMPLATE_DOMAIN_PATTERN.search(value))


def missing_recommended_keys(env_values: dict[str, str], settings: Settings | None) -> list[str]:
    present = {key.upper() for key in env_values}
    expected = list(RECOMMENDED_PRODUCTION_KEYS)
    if settings is not None:
        if settings.storage_backend.strip().lower() == "minio":
            expected.extend(["MINIO_ENDPOINT", "MINIO_BUCKET", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY"])
        if settings.storage_backend.strip().lower() == "local":
            expected.append("STORAGE_ROOT")
        smtp_configured = bool(
            settings.smtp_host or settings.smtp_from_email or settings.smtp_username or settings.smtp_password
        )
        if smtp_configured:
            expected.extend(["SMTP_HOST", "SMTP_FROM_EMAIL"])
            if settings.smtp_username or settings.smtp_password:
                expected.extend(["SMTP_USERNAME", "SMTP_PASSWORD"])
    return sorted(key for key in expected if key not in present)


def redacted_settings_snapshot(settings: Settings) -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    for field_name, field in Settings.model_fields.items():
        key = str(field.alias or field_name.upper())
        value = getattr(settings, field_name)
        snapshot[key] = redact_value(key, json_safe_value(value))
    return snapshot


def json_safe_value(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [json_safe_value(item) for item in value]
    return value


def redact_value(key: str, value: Any) -> Any:
    if value is None:
        return None
    key_upper = key.upper()
    if key_upper in FULLY_REDACTED_KEYS:
        return "***" if value else value
    if isinstance(value, str) and is_url_like(value):
        value = redact_url_sensitive_parts(value)
    if any(marker in key_upper for marker in SENSITIVE_KEY_MARKERS):
        return "***" if value else value
    return value


def is_url_like(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", value))


def redact_url_password(value: str) -> str:
    parts = urlsplit(value)
    if parts.password is None:
        return value
    username = parts.username or ""
    host = parts.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = username
    if username:
        netloc += ":***@"
    else:
        netloc = "***@"
    netloc += host
    if parts.port is not None:
        netloc += f":{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def redact_url_sensitive_parts(value: str) -> str:
    return redact_url_query_secrets(redact_url_password(value))


def redact_url_query_secrets(value: str) -> str:
    parts = urlsplit(value)
    if not parts.query:
        return value
    query_items = parse_qsl(parts.query, keep_blank_values=True)
    redacted_items = [
        (name, "***" if is_sensitive_query_name(name) else item_value)
        for name, item_value in query_items
    ]
    if redacted_items == query_items:
        return value
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            urlencode(redacted_items, doseq=True, safe="*"),
            parts.fragment,
        )
    )


def is_sensitive_query_name(name: str) -> bool:
    upper_name = name.upper()
    return any(marker in upper_name for marker in SENSITIVE_QUERY_MARKERS)


def write_report(output_path: Path, report: dict[str, Any]) -> Path:
    resolved = output_path if output_path.is_absolute() else REPO_ROOT / output_path
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return resolved


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit a production env file and write a redacted JSON report.")
    parser.add_argument("--env-file", type=Path, help="Production env file to audit.")
    parser.add_argument(
        "--write-draft",
        type=Path,
        help="Write a production env draft with generated application secrets and remaining external placeholders.",
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=REPO_ROOT / ".env.production.example",
        help="Template file for --write-draft.",
    )
    parser.add_argument("--output", type=Path, help="Write the JSON audit report to this path.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.write_draft:
        report = build_production_env_draft(template_file=args.template, output_file=args.write_draft)
        if args.output:
            output_path = write_report(args.output, report)
            print(f"production env draft report: {output_path}", flush=True)
        summary = report["summary"]
        print(
            "production env draft "
            f"{report['status']} "
            f"generated_secrets={summary['generated_secret_count']} "
            f"placeholders={summary['placeholder_key_count']} "
            f"audit_status={summary['audit_status']}",
            flush=True,
        )
        return 0 if report["status"] in {"ready", "pending"} else 1
    if args.env_file is None:
        raise SystemExit("--env-file is required unless --write-draft is used")
    report = build_env_audit_report(args.env_file)
    if args.output:
        output_path = write_report(args.output, report)
        print(f"production env audit report: {output_path}", flush=True)
    print(
        "production env audit "
        f"{report['status']} "
        f"errors={report['error_count']} "
        f"missing_recommended={len(report['missing_recommended_keys'])}",
        flush=True,
    )
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
