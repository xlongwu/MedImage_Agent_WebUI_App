"""CI-safe integration test for reviewed execution synthetic smoke.

Uses FastAPI TestClient to call POST /api/plans/execute-reviewed with
a mocked executor.  Verifies the full route → gate → writer → audit →
mocked executor chain without running real tools, MATLAB, SPM, DPABI,
or GPU.

The executor is monkeypatched — run_pipeline() is never actually called.
All file writes go to tmp_path.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from src.backend.app.main import app
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.mock_store import SQLiteDesktopStore

client = TestClient(app)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _write_config(tmp_path: Path) -> str:
    """Write a valid project_config.yaml to tmp_path. Returns the path."""
    cfg = tmp_path / "project_config.yaml"
    config = {
        "project": {"name": "ci_smoke", "description": "CI smoke test", "root_dir": "."},
        "runtime": {
            "work_dir": str(tmp_path / "project" / "work"),
            "log_dir": str(tmp_path / "project" / "logs"),
            "derivatives_dir": str(tmp_path / "project" / "derivatives"),
            "report_dir": str(tmp_path / "project" / "reports"),
        },
        "third_party": {
            "spm_dir": str(tmp_path / "third_party" / "spm"),
            "dpabi_dir": str(tmp_path / "third_party" / "dpabi"),
        },
        "safety": {
            "rawdata_readonly": True,
            "allow_overwrite_derivatives": False,
            "require_confirmation": True,
        },
    }
    cfg.write_text(yaml.safe_dump(config), encoding="utf-8")
    return str(cfg)


def _safe_plan():
    """Return a safe Python-only reviewed plan dict."""
    return {
        "pipeline_id": "synthetic_reviewed_smoke",
        "nodes": [
            {
                "id": "data_inspection",
                "backend": "python",
                "depends_on": [],
                "params": {},
            },
        ],
    }


def _unsafe_plan():
    """Return a plan with an SPM node that should be blocked."""
    return {
        "pipeline_id": "blocked_spm_plan",
        "nodes": [
            {
                "id": "spm_realign_subject",
                "backend": "matlab-spm",
                "depends_on": [],
                "params": {},
            },
        ],
    }


def _request_body(plan: dict, project_config_path: str, **overrides) -> dict:
    """Build a valid execute-reviewed request body."""
    body = {
        "plan": plan,
        "approval": {
            "approved": True,
            "approved_by": "ci-smoke",
            "approved_nodes": ["*"],
            "rejected_nodes": [],
        },
        "project_config_path": project_config_path,
        "dry_run": False,
        "confirm_execution": True,
        "persist_audit": True,
        "write_pipeline_yaml": True,
        "actor": "ci-smoke",
    }
    body.update(overrides)
    return body


def _persist_review_context(monkeypatch, tmp_path: Path, plan: dict, config_path: str):
    """Create the persisted project/review identity required for real execution."""
    from src.backend.app.api import execute_reviewed_routes
    from src.backend.app.planner import project_context, reviewed_plan_store
    from src.backend.app.planner.goal_contract_builder import (
        build_goal_contract_semantics,
    )

    project_id = f"synthetic-smoke-{tmp_path.name}"
    project_dir = tmp_path / "project"
    rawdata_dir = project_dir / "rawdata"
    dataset_index_path = project_dir / "dataset_index.json"
    rawdata_dir.mkdir(parents=True, exist_ok=True)
    dataset_index_path.write_text('{"subjects": []}', encoding="utf-8")
    store = SQLiteDesktopStore(tmp_path / "desktop.sqlite")
    store.add_project(
        ProjectDetail(
            id=project_id,
            name="Synthetic reviewed smoke",
            study_id=project_id,
            modality="rs-fMRI",
            created_date="test",
            subjects_count=0,
            current_pipeline_id=str(plan.get("pipeline_id") or "test"),
            sequences=[],
            scans_count=0,
            total_size="0 B",
            current_model_id="none",
            metadata={
                "source": "created",
                "project_dir": str(project_dir),
                "rawdata_dir": str(rawdata_dir),
                "dataset_index_path": str(dataset_index_path),
                "project_config_path": config_path,
            },
        ),
        health_status="Ready",
        rawdata_dir=str(rawdata_dir),
    )
    for module in (execute_reviewed_routes, project_context, reviewed_plan_store):
        monkeypatch.setattr(module, "mock_store", store)
    context = project_context.load_project_context(project_id, config_path)
    plan = project_context.apply_project_context_to_plan(plan, context)
    goal_candidate = build_goal_contract_semantics(plan, "synthetic execution smoke")
    candidate_semantics = goal_candidate.semantics
    if not goal_candidate.ok:
        candidate_semantics = {
            "goal_text": "synthetic execution smoke",
            "goal_kind": "reviewed_execution_boundary",
            "scope": {"completeness_required": True},
            "criteria": [
                {
                    "criterion_id": "reviewed-nodes",
                    "criterion_type": "node_status",
                    "target": "required_nodes",
                    "required_evidence": ["node_states"],
                    "expected": {
                        "node_ids": [node["id"] for node in plan["nodes"]],
                        "statuses": ["SUCCESS", "COMPLETED"],
                    },
                    "failure_semantics": "indeterminate_if_source_incomplete",
                }
            ],
            "minimum_capability_level": "unavailable",
            "builder_source": "explicit_test_review",
        }
    assert candidate_semantics is not None
    reviewed = reviewed_plan_store.save_reviewed_plan(
        project_id=project_id,
        project_config_path=config_path,
        plan=plan,
        validation={"ok": True},
        goal="synthetic execution smoke",
        provider="test",
        goal_contract_candidate=candidate_semantics,
        reviewed_actor="test-reviewer",
    )
    return (
        plan,
        project_id,
        reviewed.reviewed_plan_id,
        reviewed.payload["approval_envelope"]["summary_hash"],
    )


# ══════════════════════════════════════════════════════════════════════════════
# Smoke: safe Python-only plan → EXECUTION_SUBMITTED
# ══════════════════════════════════════════════════════════════════════════════


def test_safe_plan_execution_submitted(monkeypatch, tmp_path):
    """Full integration: safe plan, mocked executor, check all gates."""
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")

    # ── Mock executor ──
    executor_calls = []

    def _fake_run_pipeline(*, project_config_path, pipeline_path, execution_context):
        pipeline = yaml.safe_load(Path(pipeline_path).read_text(encoding="utf-8"))
        executor_calls.append(
            {
                "project_config_path": project_config_path,
                "pipeline_path": pipeline_path,
                "ticket_id": execution_context.ticket.execution_ticket_id,
            }
        )
        return {"status": "SUCCESS", "run_id": pipeline["execution"]["run_id"]}

    monkeypatch.setattr(
        "src.backend.app.runtime.execution_gateway.PIPELINE_EXECUTOR",
        _fake_run_pipeline,
    )

    # ── Redirect file writes ──
    reviewed_dir = tmp_path / "reviewed_pipelines"
    audit_dir = tmp_path / "audit_records"
    monkeypatch.setattr(
        "src.backend.app.api.execute_reviewed_routes.pipeline_writer.REVIEWED_PIPELINE_DIR",
        reviewed_dir,
    )
    monkeypatch.setattr(
        "src.backend.app.api.execute_reviewed_routes.AUDIT_RECORD_DIR",
        audit_dir,
    )

    # ── Create config ──
    config_path = _write_config(tmp_path)
    plan, project_id, reviewed_plan_id, approval_summary_hash = _persist_review_context(
        monkeypatch, tmp_path, _safe_plan(), config_path
    )

    # ── Call API ──
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_request_body(
            plan,
            config_path,
            project_id=project_id,
            reviewed_plan_id=reviewed_plan_id,
            approval={
                "approved": True,
                "approved_by": "ci-smoke",
                "approved_nodes": ["*"],
                "rejected_nodes": [],
                "approval_summary_hash": approval_summary_hash,
            },
        ),
    )

    # ── Assert ──
    assert resp.status_code == 200  # 1
    data = resp.json()

    assert data["status"] == "EXECUTION_SUBMITTED"  # 2
    assert data["ok"] is True  # 3
    assert data["execution"]["executor_called"] is True  # 4
    assert data["execution"]["submitted"] is True  # 5
    assert data["execution"]["run_id"] is not None  # 6
    assert data["execution"]["run_id"].startswith("run_")  # 6b
    assert data["executor_result"]["run_id"] == data["run_id"]

    # ── Executor called once ──
    assert len(executor_calls) == 1  # 7
    call = executor_calls[0]
    assert data["execution_ticket"]["execution_ticket_id"] == call["ticket_id"]
    assert call["project_config_path"] == config_path  # 8
    assert Path(call["pipeline_path"]).exists()  # 9

    # ── Pipeline YAML ──
    yaml_path = Path(call["pipeline_path"])
    content = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))  # 10
    assert content is not None
    assert "version" in content  # 11
    assert "modality" in content
    assert "execution" in content
    assert "nodes" in content

    # ── Audit ──
    assert data["audit"]["persisted"] is True  # 12
    assert "audit_id" in data["audit"]  # 13
    audit_files = list(audit_dir.glob("*.json"))
    assert len(audit_files) >= 1  # 14

    # ── No rawdata ──
    rawdata = tmp_path / "data"
    derivatives = tmp_path / "derivatives"
    assert not rawdata.exists() or list(rawdata.glob("*")) == []  # 15
    assert not derivatives.exists() or list(derivatives.glob("*")) == []  # 16

    # ── JSON serializable ──
    json.loads(resp.text)  # 18


# ══════════════════════════════════════════════════════════════════════════════
# Blocked: unsafe SPM plan → EXECUTION_POLICY_BLOCKED
# ══════════════════════════════════════════════════════════════════════════════


def test_unsafe_spm_plan_blocked(monkeypatch, tmp_path):
    """SPM plan should be blocked by execution policy."""
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")

    executor_calls = []

    def _fake_run_pipeline(*args, **kwargs):
        executor_calls.append(1)
        return {"status": "SHOULD_NOT_BE_CALLED"}

    monkeypatch.setattr(
        "src.backend.app.runtime.execution_gateway.PIPELINE_EXECUTOR",
        _fake_run_pipeline,
    )

    reviewed_dir = tmp_path / "reviewed_pipelines"
    audit_dir = tmp_path / "audit_records"
    monkeypatch.setattr(
        "src.backend.app.api.execute_reviewed_routes.pipeline_writer.REVIEWED_PIPELINE_DIR",
        reviewed_dir,
    )
    monkeypatch.setattr(
        "src.backend.app.api.execute_reviewed_routes.AUDIT_RECORD_DIR",
        audit_dir,
    )

    config_path = _write_config(tmp_path)
    plan, project_id, reviewed_plan_id, approval_summary_hash = _persist_review_context(
        monkeypatch, tmp_path, _unsafe_plan(), config_path
    )
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_request_body(
            plan,
            config_path,
            project_id=project_id,
            reviewed_plan_id=reviewed_plan_id,
            approval={
                "approved": True,
                "approved_by": "ci-smoke",
                "approved_nodes": ["*"],
                "rejected_nodes": [],
                "approval_summary_hash": approval_summary_hash,
            },
        ),
    )

    data = resp.json()
    assert resp.status_code == 200
    # Policy blocked (SPM nodes are blocked by execution policy)
    assert data["status"] in (
        "EXECUTION_POLICY_BLOCKED",
        "SAFE_EXECUTION_POLICY_BLOCKED",
        "APPROVAL_GATE_BLOCKED",
        "REVIEWED_PLAN_NEEDS_GOAL_REVIEW",
    )
    assert data["execution"]["executor_called"] is False
    assert len(executor_calls) == 0  # executor NOT called


# ══════════════════════════════════════════════════════════════════════════════
# dry_run=true regression
# ══════════════════════════════════════════════════════════════════════════════


def test_dry_run_true_does_not_call_executor(monkeypatch, tmp_path):
    """dry_run=true must never call the executor."""
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")

    executor_calls = []

    def _fake_run_pipeline(*args, **kwargs):
        executor_calls.append(1)
        return {"status": "SHOULD_NOT_BE_CALLED"}

    monkeypatch.setattr(
        "src.backend.app.runtime.execution_gateway.PIPELINE_EXECUTOR",
        _fake_run_pipeline,
    )

    config_path = _write_config(tmp_path)
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_request_body(_safe_plan(), config_path, dry_run=True),
    )

    data = resp.json()
    assert data["status"] == "DRY_RUN_OK"
    assert data["execution"]["executor_called"] is False
    assert len(executor_calls) == 0
