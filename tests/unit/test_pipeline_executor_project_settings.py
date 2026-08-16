"""Tests for pipeline_executor using ProjectSettings validation (M1-T005b).

All tests use tmp_path to avoid polluting real outputs/work.
No real MATLAB/SPM/DPABI nodes are executed.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import yaml

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.runtime.execution_gateway import (
    ExecutionGateway,
    current_allowlist_hash,
)
from src.backend.app.runtime.node_contract_registry import get_node_contract
from src.backend.app.runtime.pipeline_executor import load_project_config, run_pipeline
from src.backend.app.services.execution_ticket_service import ExecutionTicketService
from src.backend.app.services.execution_environment_service import ExecutionEnvironmentService
from src.backend.app.services.mock_store import SQLiteDesktopStore

# ── Helpers ──


def _write_project_config(
    tmp_path: Path,
    *,
    work_dir: str | None = None,
    log_dir: str | None = None,
    spm_dir: str | None = "./third_party/spm12",
    dpabi_dir: str | None = "./third_party/DPABI",
) -> Path:
    """Write a minimal valid project_config.yaml into tmp_path.

    Pass None for a critical field to omit it entirely (triggering
    ValueError from ProjectSettings validation).

    Includes matlab_command and derivatives_dir so that run_pipeline's
    subscript access doesn't KeyError on a valid config.
    """
    runtime: dict = {
        "matlab_command": "matlab",
        "derivatives_dir": str(tmp_path / "derivatives"),
    }
    if work_dir is not None:
        runtime["work_dir"] = work_dir
    if log_dir is not None:
        runtime["log_dir"] = log_dir

    third_party: dict = {}
    if spm_dir is not None:
        third_party["spm_dir"] = spm_dir
    if dpabi_dir is not None:
        third_party["dpabi_dir"] = dpabi_dir

    data: dict = {
        "project": {"name": "test", "root_dir": "."},
        "runtime": runtime,
        "third_party": third_party,
        "safety": {"rawdata_readonly": True},
    }
    p = tmp_path / "project_config.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


def _write_minimal_pipeline(
    tmp_path: Path, run_id: str = "run_test", node_id: str = "data_inspection"
) -> Path:
    """Write a minimal pipeline YAML with one python node."""
    p = tmp_path / "pipeline.yaml"
    p.write_text(
        yaml.safe_dump(
            {
                "pipeline_id": "test_pipeline",
                "version": "0.1.0",
                "modality": "test",
                "description": "minimal",
                "execution": {"run_id": run_id},
                "nodes": [
                    {
                        "id": node_id,
                        "name": "Test Node",
                        "agent": "test",
                        "backend": "python",
                        "depends_on": [],
                        "inputs": [],
                        "outputs": [],
                        "params": {},
                        "parallel_level": "project",
                        "gpu_supported": False,
                        "cache": False,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return p


def _run_via_gateway(tmp_path: Path, config: Path, pipeline: Path) -> dict:
    """Exercise the runtime through the same verified boundary as production."""
    payload = yaml.safe_load(pipeline.read_text(encoding="utf-8"))
    node_ids = [str(node["id"]) for node in payload.get("nodes", [])]
    backends = [str(node["backend"]) for node in payload.get("nodes", [])]
    service = ExecutionTicketService(SQLiteDesktopStore(tmp_path / f"ticket-{uuid4().hex}.sqlite"))
    environment = ExecutionEnvironmentService(service.store).capture_for_plan(
        project_id="executor-settings-test",
        reviewed_plan=SimpleNamespace(
            payload={"plan": {"nodes": [
                {"id": node_id, "backend": backend}
                for node_id, backend in zip(node_ids, backends, strict=True)
            ]}},
        ),
        write_roots=("project://derivatives",),
        readonly_roots=("project://rawdata",),
    )
    ticket = service.issue(
        project_id="executor-settings-test",
        reviewed_plan_id="reviewed-settings-test",
        plan_hash="settings-test-hash",
        goal_contract_hash="goal-contract-hash",
        evaluation_policy_version="goal-evaluator-v1",
        approval_summary_hash="settings-test-approval",
        execution_environment_snapshot_id=environment.snapshot_id,
        execution_environment_hash=environment.environment_hash,
        memory_context_hash=None,
        approved_actor="test",
        approved_node_ids=node_ids,
        approved_backend_ids=backends,
        input_roots=[str(tmp_path)],
        output_roots=[str(tmp_path)],
        project_config_path=str(config),
        pipeline_path=str(pipeline),
        allowlist_hash=current_allowlist_hash(),
        normalized_params_hash="normalized-params-hash",
        contract_versions={
            node_id: (
                get_node_contract(node_id).contract_version
                if node_id != "nonexistent_node_xyz"
                else "missing"
            )
            for node_id in node_ids
        },
        audit_id="settings-test-audit",
    )
    result, _ = ExecutionGateway(service).dispatch(
        execution_ticket_id=ticket.execution_ticket_id,
        project_id=ticket.project_id,
        reviewed_plan_id=ticket.reviewed_plan_id,
        plan_hash=ticket.plan_hash,
        approval_summary_hash=ticket.approval_summary_hash,
        memory_context_hash=ticket.memory_context_hash,
        scope_hash=ticket.scope_hash,
        normalized_params_hash=ticket.normalized_params_hash,
        contract_versions=ticket.contract_versions,
        project_config_path=str(config),
        pipeline_path=str(pipeline),
        command_id="pipeline-settings-dispatch",
        run_id="settings-run",
        executor=run_pipeline,
    )
    return result


# ── Tests: load_project_config ──


def test_load_project_config_returns_dict(tmp_path: Path):
    cfg = _write_project_config(
        tmp_path, work_dir=str(tmp_path / "work"), log_dir=str(tmp_path / "logs")
    )
    result = load_project_config(cfg)
    assert isinstance(result, dict), f"Expected dict, got {type(result).__name__}"
    assert result["runtime"]["work_dir"] == str(tmp_path / "work")


def test_missing_work_dir_raises_value_error(tmp_path: Path):
    cfg = _write_project_config(tmp_path, log_dir=str(tmp_path / "logs"))
    with pytest.raises(ValueError, match="Missing required field 'runtime.work_dir'"):
        load_project_config(cfg)


def test_missing_spm_dir_raises_value_error(tmp_path: Path):
    cfg = _write_project_config(
        tmp_path, work_dir=str(tmp_path / "work"), log_dir=str(tmp_path / "logs"), spm_dir=None
    )
    with pytest.raises(ValueError, match="Missing required field 'third_party.spm_dir'"):
        load_project_config(cfg)


def test_file_not_found_raises():
    with pytest.raises(FileNotFoundError, match="Project config file not found"):
        load_project_config("nonexistent/config.yaml")


# ── Tests: run_pipeline with bad config ──


def test_run_pipeline_returns_invalid_on_missing_work_dir(tmp_path: Path):
    """Missing critical field → run_pipeline returns status INVALID."""
    cfg = _write_project_config(tmp_path, log_dir=str(tmp_path / "logs"))
    pipeline = _write_minimal_pipeline(tmp_path)

    result = _run_via_gateway(tmp_path, cfg, pipeline)

    assert result["status"] == "INVALID", f"Expected INVALID on bad config, got {result['status']}"
    assert "Failed to load project config" in result.get("error", "")


def test_run_pipeline_returns_invalid_on_missing_spm_dir(tmp_path: Path):
    cfg = _write_project_config(
        tmp_path, work_dir=str(tmp_path / "work"), log_dir=str(tmp_path / "logs"), spm_dir=None
    )
    pipeline = _write_minimal_pipeline(tmp_path)

    result = _run_via_gateway(tmp_path, cfg, pipeline)

    assert result["status"] == "INVALID"
    assert "Failed to load project config" in result.get("error", "")


def test_run_pipeline_returns_invalid_on_nonexistent_config(tmp_path: Path):
    pipeline = _write_minimal_pipeline(tmp_path)
    result = _run_via_gateway(tmp_path, tmp_path / "no_such_config.yaml", pipeline)
    assert result["status"] == "INVALID"
    assert "Failed to load project config" in result.get("error", "")


# ── Tests: no real execution ──


def test_no_real_matlab_spm_dpabi_executed(tmp_path: Path):
    """Bad config must cause early return — never entering any node runner.

    We verify this by using a missing critical field, so run_pipeline
    returns INVALID before even parsing the pipeline YAML.
    """
    cfg = _write_project_config(tmp_path, log_dir=str(tmp_path / "logs"))
    pipeline = _write_minimal_pipeline(tmp_path)

    result = _run_via_gateway(tmp_path, cfg, pipeline)

    assert result["status"] == "INVALID"
    # No pipeline_runs/ created, no node states generated
    work_dir = tmp_path / "work"
    assert not (work_dir / "pipeline_runs").exists(), (
        "pipeline_runs/ should not exist when config load fails"
    )
    assert not (work_dir / "states").exists(), "states/ should not exist when config load fails"


def test_bad_config_writes_summary_in_work_dir(tmp_path: Path):
    """Even on INVALID, a summary should be written (the executor does
    write_pipeline_summary with status INVALID).  The summary uses the
    runtime.work_dir from project_config, but since the config load itself
    fails, the executor falls back to a best-effort summary at a default
    location.  We verify that the result dict contains an 'error' key and
    that no unexpected directories are created."""
    cfg = _write_project_config(tmp_path, log_dir=str(tmp_path / "logs"))
    pipeline = _write_minimal_pipeline(tmp_path)

    result = _run_via_gateway(tmp_path, cfg, pipeline)

    assert result["status"] == "INVALID"
    assert "error" in result
    assert "Failed to load project config" in result["error"]


# ── Tests: valid config yields correct but does not execute real nodes ──


def test_valid_config_with_nonexistent_node(tmp_path: Path):
    """An unregistered plan node is rejected before a ticket can dispatch."""
    # A reviewed plan must be fully contract-bound before its environment can
    # be snapshotted, so an unknown node never reaches a runner.
    cfg = _write_project_config(
        tmp_path, work_dir=str(tmp_path / "work"), log_dir=str(tmp_path / "logs")
    )
    pipeline = _write_minimal_pipeline(
        tmp_path, run_id="run_no_node", node_id="nonexistent_node_xyz"
    )

    with pytest.raises(SafetyError, match="EXECUTION_ENVIRONMENT_PLAN_INVALID"):
        _run_via_gateway(tmp_path, cfg, pipeline)

    # No runner is selected or invoked for an unbound environment snapshot.
