"""Runner governance tests: contract timeout enforcement and structured errors."""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.runtime import pipeline_executor
from src.backend.app.runtime.pipeline_executor import (
    _invoke_runner,
    _runtime_failure_code,
    _runner_timeout_seconds,
    run_pipeline,
)


# ── _runtime_failure_code ────────────────────────────────────────────────────


def test_runtime_failure_code_prefers_stable_application_codes() -> None:
    assert _runtime_failure_code(SafetyError("blocked", code="SAFETY_POLICY_BLOCKED")) == (
        "SAFETY_POLICY_BLOCKED"
    )


def test_runtime_failure_code_reads_leading_message_token() -> None:
    assert _runtime_failure_code(RuntimeError("TRANSIENT_IO: file busy")) == "TRANSIENT_IO"


def test_runtime_failure_code_defaults_plain_exceptions_to_node_failed() -> None:
    assert _runtime_failure_code(RuntimeError("disk on fire")) == "NODE_FAILED"


# ── _runner_timeout_seconds ─────────────────────────────────────────────────


def test_runner_timeout_seconds_reads_contract_resource_budget(monkeypatch) -> None:
    contract = SimpleNamespace(
        resources=SimpleNamespace(runner_timeout_seconds=120)
    )
    monkeypatch.setattr(pipeline_executor, "get_node_contract", lambda node_id: contract)

    assert _runner_timeout_seconds("any_node") == 120


def test_runner_timeout_seconds_is_unlimited_without_contract_or_budget(monkeypatch) -> None:
    def _missing(node_id: str) -> Any:
        raise KeyError(node_id)

    monkeypatch.setattr(pipeline_executor, "get_node_contract", _missing)
    assert _runner_timeout_seconds("unknown_node") is None

    unlimited = SimpleNamespace(resources=SimpleNamespace(runner_timeout_seconds=None))
    monkeypatch.setattr(pipeline_executor, "get_node_contract", lambda node_id: unlimited)
    assert _runner_timeout_seconds("legacy_node") is None


# ── _invoke_runner ───────────────────────────────────────────────────────────


def test_invoke_runner_returns_result_and_no_detail_on_success() -> None:
    result, detail = _invoke_runner(lambda: {"ok": True}, node_id="n", subject_id="sub-01")

    assert result == {"ok": True}
    assert detail is None


def test_invoke_runner_structures_runner_exceptions() -> None:
    def _boom() -> Any:
        raise ValueError("input file missing")

    result, detail = _invoke_runner(_boom, node_id="reho_subject", subject_id="sub-07")

    assert result is None
    assert detail == {
        "error_code": "NODE_FAILED",
        "error_type": "ValueError",
        "message": "input file missing",
        "node_id": "reho_subject",
        "subject_id": "sub-07",
    }


def test_invoke_runner_enforces_contract_timeout(monkeypatch) -> None:
    contract = SimpleNamespace(resources=SimpleNamespace(runner_timeout_seconds=1))
    monkeypatch.setattr(pipeline_executor, "get_node_contract", lambda node_id: contract)

    started = time.monotonic()
    result, detail = _invoke_runner(lambda: time.sleep(5), node_id="slow_node", subject_id="sub-01")
    elapsed = time.monotonic() - started

    assert result is None
    assert detail is not None
    assert detail["error_code"] == "NODE_RUNNER_TIMEOUT"
    assert detail["timeout_seconds"] == 1
    assert detail["node_id"] == "slow_node"
    assert detail["subject_id"] == "sub-01"
    assert elapsed < 10


# ── run_pipeline integration (project-level node) ───────────────────────────


def _write_pipeline(tmp_path: Path) -> Path:
    pipeline = {
        "pipeline_id": "governance_smoke",
        "version": "1",
        "execution": {"run_id": "run_gov", "stop_on_failure": True},
        "nodes": [{"id": "gov_node", "name": "gov", "backend": "python"}],
    }
    path = tmp_path / "pipeline.yaml"
    path.write_text(yaml.safe_dump(pipeline), encoding="utf-8")
    return path


def _fabricate_execution_context(tmp_path: Path, pipeline_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        ticket=SimpleNamespace(is_expired=lambda: False),
        ticket_service=None,
        verified_project_config_path=str((tmp_path / "project_config.yaml").resolve()),
        verified_pipeline_path=str(pipeline_path.resolve()),
    )


def _patch_executor_seams(monkeypatch, runner: Any, timeout: int | None) -> None:
    monkeypatch.setattr(pipeline_executor, "assert_verified_execution_context", lambda ctx: None)
    monkeypatch.setattr(pipeline_executor, "enforce_node_capabilities", lambda ctx, node: None)
    monkeypatch.setattr(
        pipeline_executor, "enforce_recovery_pipeline_scope", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(
        pipeline_executor,
        "ToolExecutionContext",
        SimpleNamespace(from_ticket=lambda ticket, service: SimpleNamespace()),
    )
    monkeypatch.setattr(pipeline_executor, "get_node_runner", lambda node_id: runner)
    contract = SimpleNamespace(resources=SimpleNamespace(runner_timeout_seconds=timeout))
    monkeypatch.setattr(pipeline_executor, "get_node_contract", lambda node_id: contract)


def _run(tmp_path: Path, monkeypatch, runner: Any, timeout: int | None) -> dict[str, Any]:
    pipeline_path = _write_pipeline(tmp_path)
    monkeypatch.setattr(
        pipeline_executor,
        "load_project_config",
        lambda path: {
            "runtime": {
                "work_dir": str(tmp_path / "work"),
                "log_dir": str(tmp_path / "logs"),
                "matlab_command": "matlab",
            },
            "third_party": {"spm_dir": "", "dpabi_dir": ""},
        },
    )
    _patch_executor_seams(monkeypatch, runner, timeout)
    return run_pipeline(
        str((tmp_path / "project_config.yaml").resolve()),
        pipeline_path,
        execution_context=_fabricate_execution_context(tmp_path, pipeline_path),
    )


def test_run_pipeline_records_structured_error_details(tmp_path, monkeypatch) -> None:
    def _runner(context, node) -> dict[str, Any]:
        raise RuntimeError("TRANSIENT_IO: output file locked")

    result = _run(tmp_path, monkeypatch, _runner, timeout=None)

    assert result["status"] == "FAILED"
    assert any("TRANSIENT_IO" in message for message in result["errors"])

    state = json.loads(Path(result["node_states"][0]).read_text(encoding="utf-8"))
    detail = state["error_details"][0]
    assert detail["error_code"] == "TRANSIENT_IO"
    assert detail["error_type"] == "RuntimeError"
    assert detail["node_id"] == "gov_node"


def test_run_pipeline_enforces_contract_timeout_for_project_node(tmp_path, monkeypatch) -> None:
    def _runner(context, node) -> dict[str, Any]:
        time.sleep(2)
        return {"ok": True}

    result = _run(tmp_path, monkeypatch, _runner, timeout=1)

    assert result["status"] == "FAILED"
    state = json.loads(Path(result["node_states"][0]).read_text(encoding="utf-8"))
    detail = state["error_details"][0]
    assert detail["error_code"] == "NODE_RUNNER_TIMEOUT"
    assert detail["timeout_seconds"] == 1
    assert "runner_timeout_seconds=1" in detail["message"]
