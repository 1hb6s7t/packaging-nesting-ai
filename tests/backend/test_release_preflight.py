from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
import json


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "release_preflight.py"


def load_release_preflight_module():
    spec = importlib.util.spec_from_file_location("release_preflight", SCRIPT_PATH)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_release_preflight_default_steps_cover_release_gates() -> None:
    module = load_release_preflight_module()

    steps = module.build_subprocess_steps(full_backend=False, skip_frontend=False)

    assert [step.name for step in steps] == [
        "backend release gate tests",
        "release evidence pack generation",
        "release evidence pack verification",
        "frontend production build",
    ]
    backend_command = steps[0].command
    assert backend_command[:3] == [sys.executable, "-m", "pytest"]
    assert "tests/backend/test_api.py" in backend_command
    assert "tests/backend/test_migrations.py" in backend_command
    assert "tests/backend/test_health.py" in backend_command
    assert "tests/backend/test_config.py" in backend_command
    assert "tests/backend/test_operation_logs.py" in backend_command
    assert "tests/backend/test_adapters.py" in backend_command
    assert "tests/backend/test_customer_sandbox_pack.py" in backend_command
    assert "tests/backend/test_customer_sandbox_audit.py" in backend_command
    assert "tests/backend/test_conversion_supplier_audit.py" in backend_command
    assert "tests/backend/test_solver_governance_audit.py" in backend_command
    assert "tests/backend/test_notifications.py" in backend_command
    assert "tests/backend/test_notification_channel_audit.py" in backend_command
    assert "tests/backend/test_storage_export_audit.py" in backend_command
    assert "tests/backend/test_external_acceptance_audit.py" in backend_command
    assert "tests/backend/test_deployment_compose.py" in backend_command
    assert "tests/backend/test_deployment_compose_audit.py" in backend_command
    assert "tests/backend/test_route_auth_surface.py" in backend_command
    assert "tests/backend/test_dependency_review_audit.py" in backend_command
    assert "tests/backend/test_dependency_review_template.py" in backend_command
    assert "tests/backend/test_release_image_dependency_audit.py" in backend_command
    assert "tests/backend/test_release_handoff_bundle.py" in backend_command
    assert "tests/backend/test_verify_release_handoff_bundle.py" in backend_command
    assert "tests/backend/test_go_live_readiness_audit.py" in backend_command
    assert "tests/backend/test_go_live_remediation_packet.py" in backend_command
    assert "tests/backend/test_release_inventory.py" in backend_command
    assert "tests/backend/test_release_evidence_pack.py" in backend_command
    assert "tests/backend/test_verify_release_evidence_pack.py" in backend_command
    assert "tests/backend/test_release_preflight.py" in backend_command
    assert "tests/backend/test_verify_release_preflight.py" in backend_command
    assert steps[0].env is not None
    assert str(REPO_ROOT / "backend") in steps[0].env["PYTHONPATH"].split(module.os.pathsep)
    assert steps[1].command[:3] == [sys.executable, "scripts/release_evidence_pack.py", "--output-dir"]
    assert steps[1].cwd == REPO_ROOT
    assert steps[2].command[:3] == [sys.executable, "scripts/verify_release_evidence_pack.py", "--manifest"]
    assert "--output" in steps[2].command
    assert steps[2].cwd == REPO_ROOT
    assert steps[3].command == [module.npm_command(), "run", "build"]
    assert steps[3].cwd == REPO_ROOT / "frontend"


def test_release_preflight_full_backend_can_skip_frontend() -> None:
    module = load_release_preflight_module()

    steps = module.build_subprocess_steps(full_backend=True, skip_frontend=True, skip_evidence_pack=True)

    assert len(steps) == 1
    assert steps[0].name == "backend full test suite"
    assert steps[0].command == [sys.executable, "-m", "pytest", "-q", "tests/backend"]


def test_release_preflight_can_skip_evidence_pack_or_use_custom_output_dir() -> None:
    module = load_release_preflight_module()
    custom_output_dir = Path("tmp/custom-evidence")

    skipped_steps = module.build_subprocess_steps(
        full_backend=False,
        skip_frontend=True,
        skip_evidence_pack=True,
        evidence_output_dir=custom_output_dir,
    )
    evidence_steps = module.build_subprocess_steps(
        full_backend=False,
        skip_frontend=True,
        skip_evidence_pack=False,
        evidence_output_dir=custom_output_dir,
    )

    assert [step.name for step in skipped_steps] == ["backend release gate tests"]
    assert [step.name for step in evidence_steps] == [
        "backend release gate tests",
        "release evidence pack generation",
        "release evidence pack verification",
    ]
    assert str(custom_output_dir) in evidence_steps[1].command
    assert str(custom_output_dir / "release-evidence-pack.json") in evidence_steps[2].command


def test_release_preflight_passes_dependency_review_options_to_evidence_pack() -> None:
    module = load_release_preflight_module()
    review_file = Path("artifacts/dependency-review.json")

    steps = module.build_subprocess_steps(
        full_backend=False,
        skip_frontend=True,
        skip_evidence_pack=False,
        dependency_review_file=review_file,
        require_dependency_review=True,
    )

    command = steps[1].command
    assert "--dependency-review-file" in command
    assert str(review_file) in command
    assert "--require-dependency-review" in command


def test_release_preflight_passes_external_acceptance_options_to_evidence_pack() -> None:
    module = load_release_preflight_module()
    acceptance_file = Path("artifacts/external-acceptance.json")

    steps = module.build_subprocess_steps(
        full_backend=False,
        skip_frontend=True,
        skip_evidence_pack=False,
        external_acceptance_file=acceptance_file,
        require_external_acceptance=True,
    )

    command = steps[1].command
    assert "--external-acceptance-file" in command
    assert str(acceptance_file) in command
    assert "--require-external-acceptance" in command


def test_release_preflight_passes_production_env_options_to_evidence_pack() -> None:
    module = load_release_preflight_module()
    env_file = Path(".env.production")

    steps = module.build_subprocess_steps(
        full_backend=False,
        skip_frontend=True,
        skip_evidence_pack=False,
        env_file=env_file,
        require_production_env=True,
    )

    command = steps[1].command
    assert "--env-file" in command
    assert str(env_file) in command
    assert "--require-production-env" in command


def test_release_preflight_uses_auto_smoke_port_by_default() -> None:
    module = load_release_preflight_module()

    args = module.parse_args([])

    assert args.smoke_port == 0
    assert args.skip_evidence_pack is False
    assert args.evidence_output_dir == Path("tmp/release-preflight-evidence")
    assert args.env_file is None
    assert args.require_production_env is False
    assert args.dependency_review_file is None
    assert args.require_dependency_review is False
    assert args.external_acceptance_file is None
    assert args.require_external_acceptance is False
    assert args.fail_on_dependency_review is False


def test_release_preflight_choose_smoke_port_supports_auto_and_explicit_ports() -> None:
    module = load_release_preflight_module()

    auto_port = module.choose_smoke_port(0)

    assert 0 < auto_port <= 65535
    with module.socket.socket(module.socket.AF_INET, module.socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", auto_port))

    assert module.choose_smoke_port(8030) == 8030


def test_release_preflight_rejects_invalid_smoke_port() -> None:
    module = load_release_preflight_module()

    try:
        module.parse_args(["--smoke-port", "65536"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("expected argparse to reject an invalid smoke port")


def test_release_preflight_report_contains_gate_and_cleanup_evidence() -> None:
    module = load_release_preflight_module()
    args = module.parse_args(["--skip-frontend", "--skip-smoke", "--report-path", "artifacts/preflight.json"])
    gate = module.GateResult(
        name="backend release gate tests",
        kind="command",
        status="passed",
        duration_sec=1.25,
        command=[sys.executable, "-m", "pytest", "-q"],
        cwd=str(REPO_ROOT),
        timeout_sec=120,
        exit_code=0,
    )
    cleanup = module.GateResult(
        name="cleanup pycache",
        kind="cleanup",
        status="passed",
        duration_sec=0.1,
        payload={"removed_count": 3},
    )

    inventory = {"summary": {"dependency_count": 2, "review_required_count": 1}}

    report = module.build_release_report(
        args=args,
        gate_results=[gate],
        cleanup_result=cleanup,
        dependency_inventory=inventory,
        passed=True,
    )

    assert report["schema_version"] == 1
    assert report["repo_root"] == str(REPO_ROOT)
    assert report["options"]["skip_frontend"] is True
    assert report["options"]["skip_evidence_pack"] is False
    assert report["options"]["evidence_output_dir"] == str(Path("tmp/release-preflight-evidence"))
    assert report["options"]["env_file"] is None
    assert report["options"]["require_production_env"] is False
    assert report["options"]["dependency_review_file"] is None
    assert report["options"]["require_dependency_review"] is False
    assert report["options"]["external_acceptance_file"] is None
    assert report["options"]["require_external_acceptance"] is False
    assert report["options"]["skip_smoke"] is True
    assert report["options"]["fail_on_dependency_review"] is False
    assert report["passed"] is True
    assert report["gates"][0]["name"] == "backend release gate tests"
    assert report["gates"][0]["exit_code"] == 0
    assert report["cleanup"]["payload"]["removed_count"] == 3
    assert report["dependency_inventory_summary"]["dependency_count"] == 2
    assert report["dependency_inventory_summary"]["review_required_count"] == 1


def test_release_preflight_dependency_review_helpers() -> None:
    module = load_release_preflight_module()

    assert module.dependency_review_required_count(None) == 0
    assert module.dependency_review_required_count({"summary": {"review_required_count": 2}}) == 2
    assert module.dependency_review_required_count({"summary": {"review_required_count": "2"}}) == 0
    assert module.dependency_review_failure_message(2) == "dependency inventory has 2 review-required item(s)"
    assert (
        module.dependency_review_failure_message(2, local_missing_dependency_inventory())
        == "dependency inventory has 2 review-required item(s) because "
        "2 release-blocking package(s) are missing in this environment; "
        "regenerate and use the release image dependency inventory before go-live"
    )


def test_release_preflight_input_validation_helpers(tmp_path, monkeypatch) -> None:
    module = load_release_preflight_module()
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    args = module.parse_args(["--require-production-env"])
    inventory = {"summary": {"review_required_count": 1}}

    assert module.preflight_input_failure_message(args, inventory) == "production env audit requires --env-file"

    args = module.parse_args(["--env-file", "missing.env"])
    assert module.preflight_input_failure_message(args, inventory) == "production env file does not exist: missing.env"

    args = module.parse_args(["--require-dependency-review"])
    assert (
        module.preflight_input_failure_message(args, inventory)
        == "dependency review file is required because dependency inventory has 1 review-required item(s)"
    )
    assert (
        module.preflight_input_failure_message(args, local_missing_dependency_inventory())
        == "dependency review file is required because dependency inventory has 2 review-required item(s) because "
        "2 release-blocking package(s) are missing in this environment; "
        "regenerate and use the release image dependency inventory before go-live"
    )

    args = module.parse_args(["--dependency-review-file", "missing-review.json"])
    assert module.preflight_input_failure_message(args, inventory) == "dependency review file does not exist: missing-review.json"

    args = module.parse_args(["--require-external-acceptance"])
    assert module.preflight_input_failure_message(args, inventory) == "external acceptance file is required"

    args = module.parse_args(["--external-acceptance-file", "missing-acceptance.json"])
    assert module.preflight_input_failure_message(args, inventory) == "external acceptance file does not exist: missing-acceptance.json"


def test_release_preflight_fails_missing_required_production_env_before_gates(tmp_path, monkeypatch) -> None:
    module = load_release_preflight_module()
    inventory = {"summary": {"dependency_count": 3, "review_required_count": 0}}
    cleanup = module.GateResult(
        name="cleanup pycache",
        kind="cleanup",
        status="passed",
        duration_sec=0,
        payload={"removed_count": 0},
    )
    written: dict[str, object] = {}

    monkeypatch.setattr(module, "build_dependency_inventory", lambda args: inventory)
    monkeypatch.setattr(module, "cleanup_pycache_result", lambda *, keep_pycache: cleanup)
    monkeypatch.setattr(
        module,
        "write_release_report",
        lambda report_path, report: written.setdefault("report", report) or report_path,
    )
    monkeypatch.setattr(
        module,
        "build_subprocess_steps",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("subprocess gates should not run")),
    )

    try:
        module.main(["--require-production-env", "--report-path", str(tmp_path / "preflight.json")])
    except SystemExit as exc:
        assert str(exc) == "production env audit requires --env-file"
    else:
        raise AssertionError("expected preflight to fail on missing production env file")

    report = written["report"]
    assert report["passed"] is False
    assert report["options"]["require_production_env"] is True
    assert report["gates"] == []


def test_release_preflight_can_fail_on_dependency_review_before_gates(tmp_path, monkeypatch) -> None:
    module = load_release_preflight_module()
    inventory = {"summary": {"dependency_count": 3, "review_required_count": 2}}
    cleanup = module.GateResult(
        name="cleanup pycache",
        kind="cleanup",
        status="passed",
        duration_sec=0,
        payload={"removed_count": 0},
    )
    written: dict[str, object] = {}

    monkeypatch.setattr(module, "build_dependency_inventory", lambda args: inventory)
    monkeypatch.setattr(module, "cleanup_pycache_result", lambda *, keep_pycache: cleanup)
    monkeypatch.setattr(
        module,
        "write_release_report",
        lambda report_path, report: written.setdefault("report", report) or report_path,
    )
    monkeypatch.setattr(
        module,
        "build_subprocess_steps",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("subprocess gates should not run")),
    )

    try:
        module.main(
            [
                "--fail-on-dependency-review",
                "--report-path",
                str(tmp_path / "preflight.json"),
                "--skip-frontend",
                "--skip-smoke",
            ]
        )
    except SystemExit as exc:
        assert str(exc) == "dependency inventory has 2 review-required item(s)"
    else:
        raise AssertionError("expected preflight to fail on dependency review")

    report = written["report"]
    assert report["passed"] is False
    assert report["options"]["fail_on_dependency_review"] is True
    assert report["gates"] == []
    assert report["dependency_inventory_summary"]["review_required_count"] == 2
    assert report["cleanup"]["payload"]["removed_count"] == 0


def test_release_preflight_fail_on_dependency_review_points_to_release_image_inventory(tmp_path, monkeypatch) -> None:
    module = load_release_preflight_module()
    cleanup = module.GateResult(
        name="cleanup pycache",
        kind="cleanup",
        status="passed",
        duration_sec=0,
        payload={"removed_count": 0},
    )
    written: dict[str, object] = {}

    monkeypatch.setattr(module, "build_dependency_inventory", lambda args: local_missing_dependency_inventory())
    monkeypatch.setattr(module, "cleanup_pycache_result", lambda *, keep_pycache: cleanup)
    monkeypatch.setattr(
        module,
        "write_release_report",
        lambda report_path, report: written.setdefault("report", report) or report_path,
    )
    monkeypatch.setattr(
        module,
        "build_subprocess_steps",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("subprocess gates should not run")),
    )

    try:
        module.main(
            [
                "--fail-on-dependency-review",
                "--report-path",
                str(tmp_path / "preflight.json"),
                "--skip-frontend",
                "--skip-smoke",
            ]
        )
    except SystemExit as exc:
        assert "regenerate and use the release image dependency inventory before go-live" in str(exc)
    else:
        raise AssertionError("expected preflight to fail on dependency review")

    report = written["report"]
    assert report["passed"] is False
    assert report["dependency_inventory_summary"]["release_blocking_missing_install_count"] == 2


def test_release_preflight_evidence_gate_payloads_include_manifest_and_verification(tmp_path) -> None:
    module = load_release_preflight_module()
    output_dir = tmp_path / "evidence"
    output_dir.mkdir()
    manifest = {
        "schema_version": 1,
        "status": "passed",
        "summary": {
            "artifact_count": 2,
            "passed_count": 1,
            "failed_count": 0,
            "skipped_count": 1,
            "required_failed_count": 0,
        },
        "artifacts": [
            {
                "name": "customer_sandbox_audit",
                "required": True,
                "status": "passed",
                "relative_path": "customer-sandbox-audit.json",
                "size_bytes": 123,
                "sha256": "a" * 64,
                "command": ["python", "scripts/customer_sandbox_audit.py"],
                "summary": {"sensitive_scan_status": "passed"},
            },
            {
                "name": "production_env_audit",
                "required": False,
                "status": "skipped",
                "relative_path": None,
                "size_bytes": None,
                "sha256": None,
            },
        ],
    }
    verification = {
        "schema_version": 1,
        "status": "passed",
        "summary": {
            "artifact_count": 2,
            "verified_count": 1,
            "failed_count": 0,
            "skipped_count": 1,
            "manifest_error_count": 0,
        },
    }
    (output_dir / "release-evidence-pack.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    (output_dir / "release-evidence-verification.json").write_text(
        json.dumps(verification, ensure_ascii=False),
        encoding="utf-8",
    )

    generation_payload = module.build_evidence_pack_manifest_payload(output_dir)
    verification_payload = module.build_evidence_pack_verification_payload(output_dir)
    enriched = module.enrich_preflight_gate_result(
        module.GateResult(
            name="release evidence pack verification",
            kind="command",
            status="passed",
            duration_sec=0.1,
        ),
        evidence_output_dir=output_dir,
    )

    assert generation_payload["manifest_exists"] is True
    assert generation_payload["pack_status"] == "passed"
    assert generation_payload["pack_summary"]["artifact_count"] == 2
    assert generation_payload["artifacts"] == [
        {
            "name": "customer_sandbox_audit",
            "required": True,
            "status": "passed",
            "relative_path": "customer-sandbox-audit.json",
            "size_bytes": 123,
            "sha256": "a" * 64,
        },
        {
            "name": "production_env_audit",
            "required": False,
            "status": "skipped",
            "relative_path": None,
            "size_bytes": None,
            "sha256": None,
        },
    ]
    assert verification_payload["verification_report_exists"] is True
    assert verification_payload["verification_status"] == "passed"
    assert verification_payload["verification_summary"]["verified_count"] == 1
    assert enriched.payload is not None
    assert enriched.payload["verification_summary"]["manifest_error_count"] == 0


def test_release_preflight_clean_pycache_covers_backend_tests_and_scripts(tmp_path, monkeypatch) -> None:
    module = load_release_preflight_module()
    backend_dir = tmp_path / "backend"
    tests_dir = tmp_path / "tests"
    scripts_dir = tmp_path / "scripts"
    for root in (backend_dir, tests_dir, scripts_dir):
        cache_dir = root / "pkg" / "__pycache__"
        cache_dir.mkdir(parents=True)
        (cache_dir / "module.pyc").write_bytes(b"cache")

    monkeypatch.setattr(module, "BACKEND_DIR", backend_dir)
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(module, "SCRIPTS_DIR", scripts_dir)

    removed = module.clean_pycache()

    assert removed == 3
    assert not list(tmp_path.rglob("__pycache__"))


def test_release_preflight_report_writer_resolves_relative_paths(tmp_path, monkeypatch) -> None:
    module = load_release_preflight_module()
    monkeypatch.setattr(module, "REPO_ROOT", tmp_path)
    report = {"schema_version": 1, "passed": True, "gates": []}

    output_path = module.write_release_report(Path("reports/preflight.json"), report)

    assert output_path == tmp_path / "reports" / "preflight.json"
    assert json.loads(output_path.read_text(encoding="utf-8")) == report


def local_missing_dependency_inventory() -> dict:
    return {
        "summary": {
            "dependency_count": 3,
            "review_required_count": 2,
            "release_blocking_missing_install_count": 2,
            "review_required": [
                {
                    "ecosystem": "python",
                    "name": "celery",
                    "scope": "runtime",
                    "installed": False,
                    "version": None,
                    "license": None,
                    "reason": "package is not installed in this environment; regenerate inventory in the release image",
                },
                {
                    "ecosystem": "python",
                    "name": "minio",
                    "scope": "runtime",
                    "installed": False,
                    "version": None,
                    "license": None,
                    "reason": "package is not installed in this environment; regenerate inventory in the release image",
                },
            ],
        }
    }
