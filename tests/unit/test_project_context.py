from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from src.backend.app.api import (
    dashboard_routes,
    execute_reviewed_routes,
    llm_planner_routes,
    project_history_routes,
    project_routes,
)
from src.backend.app.main import app
from src.backend.app.planner import project_context, reviewed_plan_store
from src.backend.app.planner.llm_planner import PlannerResponse
from src.backend.app.planner.plan_adapter import adapt_reviewed_plan
from src.backend.app.runtime import desktop_config
from src.backend.app.services.mock_store import SQLiteDesktopStore

client = TestClient(app)


@pytest.fixture
def created_project(tmp_path: Path, monkeypatch) -> dict:
    store = SQLiteDesktopStore(tmp_path / "desktop_state.sqlite")
    config_path = tmp_path / "desktop_config.json"
    config_path.write_text(
        json.dumps(desktop_config.DEFAULT_DESKTOP_CONFIG),
        encoding="utf-8",
    )
    monkeypatch.setattr(desktop_config, "DESKTOP_CONFIG_PATH", config_path)
    monkeypatch.setattr(project_routes, "DEFAULT_PROJECTS_ROOT", tmp_path / "projects")
    monkeypatch.setattr(project_routes, "mock_store", store)
    monkeypatch.setattr(dashboard_routes, "mock_store", store)
    monkeypatch.setattr(project_context, "mock_store", store)
    monkeypatch.setattr(reviewed_plan_store, "mock_store", store)
    monkeypatch.setattr(project_history_routes, "mock_store", store)
    monkeypatch.setattr(execute_reviewed_routes, "mock_store", store)

    response = client.post(
        "/api/projects/create",
        json={
            "project_name": "Project Context Test",
            "rawdata_dir": str(Path("examples/synthetic_bids/rawdata").resolve()),
            "project_dir": str(tmp_path / "created_project"),
            "run_inspection": True,
            "overwrite": False,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _node(plan: dict, node_id: str) -> dict:
    return next(node for node in plan["nodes"] if node["id"] == node_id)


def _safe_created_plan(created_project: dict) -> dict:
    context = project_context.load_project_context(
        created_project["project_id"],
        created_project["project_config_path"],
    )
    return project_context.apply_project_context_to_plan(
        {
            "pipeline_id": "created_project_inspection",
            "nodes": [
                {
                    "id": "data_inspection",
                    "backend": "python",
                    "depends_on": [],
                    "params": {},
                },
                {
                    "id": "motion_qc_subject",
                    "backend": "python",
                    "depends_on": ["data_inspection"],
                    "params": {},
                },
            ],
        },
        context,
    )


def _reviewed_execution_goal_candidate(plan: dict) -> dict:
    return {
        "goal_text": "Inspect and run motion QC",
        "goal_kind": "reviewed_execution_boundary",
        "scope": {"completeness_required": True},
        "criteria": [
            {
                "criterion_id": "terminal",
                "criterion_type": "pipeline_terminal",
                "target": "pipeline",
                "required_evidence": ["pipeline_summary", "node_states"],
                "expected": {
                    "statuses": ["SUCCESS", "COMPLETED"],
                    "active_nodes": 0,
                },
                "failure_semantics": "indeterminate_if_source_incomplete",
            },
            {
                "criterion_id": "nodes",
                "criterion_type": "node_status",
                "target": "required_nodes",
                "required_evidence": ["node_states"],
                "expected": {
                    "node_ids": [node["id"] for node in plan["nodes"]],
                    "statuses": ["SUCCESS", "COMPLETED"],
                },
                "failure_semantics": "indeterminate_if_source_incomplete",
            },
        ],
        "minimum_capability_level": "unavailable",
        "builder_source": "explicit_test_review",
    }


def test_created_project_detail_exposes_context_paths(created_project):
    response = client.get(f"/api/projects/{created_project['project_id']}")
    assert response.status_code == 200
    metadata = response.json()["metadata"]
    assert metadata["project_config_path"] == created_project["project_config_path"]
    assert metadata["dataset_index_path"] == created_project["dataset_index_path"]


def test_plan_api_injects_created_project_paths_before_review(created_project):
    response = client.post(
        "/api/planner/plan-from-goal",
        json={
            "goal": "motion correction",
            "project_id": created_project["project_id"],
            "project_config_path": created_project["project_config_path"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True, json.dumps(payload, indent=2)
    assert payload["project_context"]["project_id"] == created_project["project_id"]
    assert (
        payload["plan"]["project_context"]["project_config_path"]
        == created_project["project_config_path"]
    )

    inspection = _node(payload["plan"], "data_inspection")
    assert inspection["params"]["rawdata_dir"] == created_project["rawdata_dir"]
    assert (
        Path(inspection["params"]["output_dir"])
        == Path(created_project["dataset_index_path"]).parent
    )

    for node_id in ("spm_realign_subject", "motion_qc_subject"):
        assert (
            _node(payload["plan"], node_id)["params"]["dataset_index"]
            == created_project["dataset_index_path"]
        )


def test_plan_api_uses_registered_nifti_context_for_native_full_preprocessing(
    created_project,
):
    converted_root = Path(created_project["project_dir"]) / "converted_bids"
    for subject in ("sub-001", "sub-002", "sub-003"):
        func_dir = converted_root / subject / "func"
        anat_dir = converted_root / subject / "anat"
        func_dir.mkdir(parents=True, exist_ok=True)
        anat_dir.mkdir(parents=True, exist_ok=True)
        (func_dir / f"{subject}_task-rest_bold.nii.gz").write_bytes(b"")
        (anat_dir / f"{subject}_T1w.nii.gz").write_bytes(b"")

    response = client.post(
        "/api/planner/plan-from-goal",
        json={
            "goal": (
                "rs-fMRI preprocessing with slice timing, realignment, motion QC, "
                "nuisance regression, detrending, temporal filtering, ROI time series, "
                "and functional connectivity"
            ),
            "project_id": created_project["project_id"],
            "project_config_path": created_project["project_config_path"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True, json.dumps(payload, indent=2)
    assert payload["clarification_required"] is False
    assert payload["missing_prerequisites"] == []
    assert payload["errors"] == []
    assert payload["project_context"]["diagnostics"]["status"] == "CONVERTED_BIDS"
    assert payload["project_context"]["diagnostics"]["nifti_file_count"] == 6
    assert payload["project_context"]["diagnostics"]["subjects_total"] == 3
    assert payload["project_context"]["diagnostics"]["subject_candidates"] == [
        "sub-001",
        "sub-002",
        "sub-003",
    ]
    assert payload["plan"]["pipeline_id"] == "native_full_preprocessing"
    node_ids = [node["id"] for node in payload["plan"]["nodes"]]
    assert node_ids == ["native_preproc_full_execute"]
    assert "spm_realign_subject" not in node_ids


def test_plan_api_uses_registered_rawdata_bids_for_native_reho(created_project):
    response = client.post(
        "/api/planner/plan-from-goal",
        json={
            "goal": "对已登记的静息态 fMRI 数据执行完整预处理并计算 ReHo，生成质量控制报告。",
            "project_id": created_project["project_id"],
            "project_config_path": created_project["project_config_path"],
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True, json.dumps(payload, indent=2)
    assert payload["project_context"]["diagnostics"]["status"] == "BIDS"
    assert (
        payload["project_context"]["diagnostics"]["preprocessing_input_dir"]
        == created_project["rawdata_dir"]
    )
    assert payload["plan"]["pipeline_id"] == "native_reho"
    native = _node(payload["plan"], "native_preproc_full_execute")
    assert native["params"]["input_bids_dir"] == created_project["rawdata_dir"]
    assert native["params"]["stage_overrides"]["realignment"] is True
    assert native["params"]["stage_overrides"]["temporal_filtering"] is True
    assert native["params"]["stage_overrides"]["reho"] is True
    adapter = adapt_reviewed_plan(payload["plan"])
    assert adapter.ok is True, adapter.errors
    assert adapter.policy["allowed_native_preproc_nodes"] == ["native_preproc_full_execute"]


def test_plan_api_rejects_synthetic_node_for_created_project(
    created_project,
    monkeypatch,
):
    def fake_generate_plan_from_goal(**kwargs):
        return PlannerResponse(
            ok=True,
            provider="rule_based",
            goal=str(kwargs["goal"]),
            plan={
                "pipeline_id": "unsafe_synthetic",
                "nodes": [
                    {
                        "id": "create_synthetic_bids",
                        "backend": "python",
                        "depends_on": [],
                        "params": {},
                    },
                ],
            },
            validation={"ok": True},
        )

    monkeypatch.setattr(
        llm_planner_routes,
        "generate_plan_from_goal",
        fake_generate_plan_from_goal,
    )
    response = client.post(
        "/api/planner/plan-from-goal",
        json={
            "goal": "synthetic",
            "project_id": created_project["project_id"],
            "project_config_path": created_project["project_config_path"],
        },
    )

    payload = response.json()
    assert payload["ok"] is False
    assert payload["plan"] == {}
    assert any("SYNTHETIC_DATA_NOT_ALLOWED" in error for error in payload["errors"])


def test_plan_api_missing_selected_project_returns_clear_error():
    response = client.post(
        "/api/planner/plan-from-goal",
        json={"goal": "motion", "project_id": "missing-project"},
    )
    payload = response.json()
    assert payload["ok"] is False
    assert any("PROJECT_NOT_FOUND" in error for error in payload["errors"])


def test_plan_api_requires_explicit_project_context(monkeypatch):
    def fail_if_called(**kwargs):
        raise AssertionError("planner must not run without explicit project context")

    monkeypatch.setattr(
        llm_planner_routes,
        "generate_plan_from_goal",
        fail_if_called,
    )
    response = client.post(
        "/api/planner/plan-from-goal",
        json={"goal": "motion correction"},
    )
    payload = response.json()
    assert payload["ok"] is False
    assert payload["plan"] == {}
    assert any("PROJECT_CONTEXT_REQUIRED" in error for error in payload["errors"])


def test_explicit_example_project_config_remains_available():
    response = client.post(
        "/api/planner/plan-from-goal",
        json={
            "goal": "motion correction",
            "project_config_path": str(Path("examples/project_config_dataset.yaml").resolve()),
        },
    )
    payload = response.json()
    assert payload["ok"] is True
    assert payload["project_context"]["source"] == "example"
    assert payload["project_context"]["project_id"] is None


def test_execute_reviewed_accepts_consistent_created_project_context(created_project):
    plan = _safe_created_plan(created_project)
    response = client.post(
        "/api/plans/execute-reviewed",
        json={
            "plan": plan,
            "approval": {"approved": True, "approved_nodes": ["*"]},
            "project_config_path": created_project["project_config_path"],
            "dry_run": True,
        },
    )
    assert response.json()["status"] == "DRY_RUN_OK"


def test_execute_reviewed_rejects_dataset_index_mismatch(created_project):
    context = project_context.load_project_context(
        created_project["project_id"],
        created_project["project_config_path"],
    )
    plan = project_context.apply_project_context_to_plan(
        {
            "pipeline_id": "created_subject_plan",
            "nodes": [
                {
                    "id": "motion_qc_subject",
                    "backend": "python",
                    "depends_on": [],
                    "params": {},
                },
            ],
        },
        context,
    )
    plan["nodes"][0]["params"]["dataset_index"] = str(
        Path(created_project["project_dir"]) / "wrong-index.json"
    )

    response = client.post(
        "/api/plans/execute-reviewed",
        json={
            "plan": plan,
            "approval": {"approved": True, "approved_nodes": ["*"]},
            "project_config_path": created_project["project_config_path"],
            "dry_run": True,
        },
    )
    payload = response.json()
    assert payload["status"] == "PROJECT_CONTEXT_MISMATCH"
    assert any("DATASET_INDEX_MISMATCH" in error for error in payload["errors"])


def test_execute_reviewed_rejects_rawdata_mismatch(created_project):
    plan = _safe_created_plan(created_project)
    _node(plan, "data_inspection")["params"]["rawdata_dir"] = str(
        Path(created_project["project_dir"]) / "wrong-rawdata"
    )

    response = client.post(
        "/api/plans/execute-reviewed",
        json={
            "plan": plan,
            "approval": {"approved": True, "approved_nodes": ["*"]},
            "project_config_path": created_project["project_config_path"],
            "dry_run": True,
        },
    )
    payload = response.json()
    assert payload["status"] == "PROJECT_CONTEXT_MISMATCH"
    assert any("RAWDATA_DIR_MISMATCH" in error for error in payload["errors"])


def test_execute_reviewed_rejects_synthetic_node_for_created_project(created_project):
    plan = _safe_created_plan(created_project)
    plan["nodes"].append(
        {
            "id": "create_synthetic_bids",
            "backend": "python",
            "depends_on": [],
            "params": {},
        }
    )

    response = client.post(
        "/api/plans/execute-reviewed",
        json={
            "plan": plan,
            "approval": {"approved": True, "approved_nodes": ["*"]},
            "project_config_path": created_project["project_config_path"],
            "dry_run": True,
        },
    )
    payload = response.json()
    assert payload["status"] == "PROJECT_CONTEXT_MISMATCH"
    assert any("SYNTHETIC_DATA_NOT_ALLOWED" in error for error in payload["errors"])


def test_execute_reviewed_passes_real_config_to_mocked_executor(
    created_project,
    monkeypatch,
    tmp_path,
):
    calls: list[dict[str, str]] = []

    def fake_run_pipeline(project_config_path, pipeline_path, execution_context):
        calls.append(
            {
                "project_config_path": project_config_path,
                "pipeline_path": pipeline_path,
            }
        )
        pipeline = yaml.safe_load(Path(pipeline_path).read_text(encoding="utf-8"))
        return {"status": "SUCCESS", "run_id": pipeline["execution"]["run_id"]}

    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    monkeypatch.setattr(
        "src.backend.app.runtime.execution_gateway.PIPELINE_EXECUTOR",
        fake_run_pipeline,
    )
    monkeypatch.setattr(
        execute_reviewed_routes.pipeline_writer,
        "REVIEWED_PIPELINE_DIR",
        tmp_path / "reviewed_pipelines",
    )
    monkeypatch.setattr(
        execute_reviewed_routes,
        "AUDIT_RECORD_DIR",
        tmp_path / "audit_records",
    )

    plan = _safe_created_plan(created_project)
    saved = client.post(
        f"/api/projects/{created_project['project_id']}/plans",
        json={
            "plan": plan,
            "project_config_path": created_project["project_config_path"],
            "goal": "Inspect and run motion QC",
            "goal_contract_candidate": _reviewed_execution_goal_candidate(plan),
            "reviewed_actor": "test-reviewer",
        },
    )
    assert saved.status_code == 200, saved.text
    reviewed = saved.json()["reviewed_plan"]
    reviewed_plan_id = reviewed["reviewed_plan_id"]

    response = client.post(
        "/api/plans/execute-reviewed",
        json={
            "plan": plan,
            "approval": {
                "approved": True,
                "approved_nodes": ["*"],
                "approval_summary_hash": reviewed["payload"]["approval_envelope"][
                    "summary_hash"
                ],
            },
            "project_id": created_project["project_id"],
            "reviewed_plan_id": reviewed_plan_id,
            "project_config_path": created_project["project_config_path"],
            "dry_run": False,
            "confirm_execution": True,
            "persist_audit": True,
            "write_pipeline_yaml": True,
        },
    )

    payload = response.json()
    assert payload["status"] == "EXECUTION_SUBMITTED"
    assert calls[0]["project_config_path"] == created_project["project_config_path"]
    written_pipeline = yaml.safe_load(Path(calls[0]["pipeline_path"]).read_text(encoding="utf-8"))
    assert (
        _node(written_pipeline, "motion_qc_subject")["params"]["dataset_index"]
        == created_project["dataset_index_path"]
    )
