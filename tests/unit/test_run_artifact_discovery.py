from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from src.backend.app.api import (
    dashboard_routes,
    project_routes,
)
from src.backend.app.main import app
from src.backend.app.planner import project_context, reviewed_plan_store
from src.backend.app.runtime import desktop_config
from src.backend.app.schemas.desktop import ProjectDetail, RunLinkRecord
from src.backend.app.services.mock_store import SQLiteDesktopStore, utc_now_iso
from src.backend.app.services.run_artifact_discovery import (
    artifact_id_for_path,
    discover_run_artifacts,
    find_run_artifact,
)
from src.backend.app.services.run_summary_preview import resolve_run_summary_path


def _isolated_store(tmp_path: Path, monkeypatch) -> SQLiteDesktopStore:
    store = SQLiteDesktopStore(tmp_path / "desktop_state.sqlite")
    monkeypatch.setattr(
        desktop_config,
        "DESKTOP_CONFIG_PATH",
        tmp_path / "desktop_config.json",
    )
    monkeypatch.setattr(project_routes, "DEFAULT_PROJECTS_ROOT", tmp_path / "projects")
    for module in (
        project_routes,
        dashboard_routes,
        project_context,
        reviewed_plan_store,
    ):
        monkeypatch.setattr(module, "mock_store", store)
    desktop_config.DESKTOP_CONFIG_PATH.write_text(
        json.dumps(desktop_config.DEFAULT_DESKTOP_CONFIG),
        encoding="utf-8",
    )
    return store


def _create_project(client: TestClient, tmp_path: Path, name: str = "Artifact Project") -> dict:
    response = client.post(
        "/api/projects/create",
        json={
            "project_name": name,
            "rawdata_dir": str(Path("examples/synthetic_bids/rawdata").resolve()),
            "project_dir": str(tmp_path / name.replace(" ", "_")),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _add_run_link(
    store: SQLiteDesktopStore,
    created: dict,
    *,
    run_id: str,
    summary_path: Path | str | None,
    status: str = "SUCCESS",
    payload: dict | None = None,
) -> RunLinkRecord:
    now = utc_now_iso()
    record = RunLinkRecord(
        run_link_id=f"link-{run_id}",
        project_id=created["project_id"],
        reviewed_plan_id=f"reviewed-{run_id}",
        run_id=run_id,
        pipeline_path=str(Path(created["project_dir"]) / "work" / f"{run_id}.yaml"),
        summary_path=str(summary_path) if summary_path is not None else None,
        project_config_path=created["project_config_path"],
        status=status,
        created_at=now,
        updated_at=now,
        payload=payload or {},
    )
    return store.add_run_link(record)


def _project(store: SQLiteDesktopStore, created: dict) -> ProjectDetail:
    project = store.get_project(created["project_id"])
    assert project is not None
    return project


def _artifact_by_name(artifacts: list[dict], name: str) -> dict:
    for artifact in artifacts:
        if artifact["name"] == name:
            return artifact
    raise AssertionError(f"Artifact not found: {name}; got {artifacts}")


def test_recovery_summary_is_accepted_from_its_bound_isolated_output_root(
    tmp_path,
    monkeypatch,
):
    store = _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path, "Recovery Summary Project")
    project = _project(store, created)
    attempt_id = "recovery_attempt_bound"
    attempt_root = Path(created["project_dir"]) / "recovery_attempts" / attempt_id
    control = attempt_root / "control"
    control.mkdir(parents=True)
    config_path = control / "project_config.yaml"
    pipeline_path = control / "pipeline.yaml"
    config_path.write_text("project: {}\n", encoding="utf-8")
    pipeline_path.write_text("nodes: []\n", encoding="utf-8")
    summary_path = attempt_root / "work" / "pipeline_runs" / "recovery-run" / "summary.json"
    summary_path.parent.mkdir(parents=True)
    summary_path.write_text('{"status":"SUCCESS"}', encoding="utf-8")
    now = utc_now_iso()
    record = RunLinkRecord(
        run_link_id="recovery-summary-link",
        project_id=created["project_id"],
        reviewed_plan_id="reviewed-recovery-summary",
        run_id="recovery-run",
        pipeline_path=str(pipeline_path),
        summary_path=str(summary_path),
        project_config_path=str(config_path),
        status="SUCCESS",
        created_at=now,
        updated_at=now,
        payload={
            "recovery_attempt_id": attempt_id,
            "output_namespace": f"recovery_attempts/{attempt_id}",
            "attempt_output_root": str(attempt_root),
            "state_root": str(attempt_root / "work"),
        },
    )

    resolved, warnings = resolve_run_summary_path(project, record)

    assert resolved == summary_path.resolve()
    assert warnings == []


def _write_run_artifact_fixture(
    store: SQLiteDesktopStore,
    created: dict,
    *,
    run_id: str = "run_artifacts",
) -> dict[str, Path]:
    project_dir = Path(created["project_dir"])
    work_dir = project_dir / "work"
    reports_dir = project_dir / "reports"
    logs_dir = project_dir / "logs"
    derivatives_dir = project_dir / "derivatives"
    for path in (work_dir, reports_dir, logs_dir, derivatives_dir):
        path.mkdir(parents=True, exist_ok=True)

    pipeline_path = work_dir / f"{run_id}.yaml"
    pipeline_path.write_text("pipeline_id: artifact-fixture\n", encoding="utf-8")
    report_path = reports_dir / "qc_report.md"
    report_path.write_text("# QC Report\n\nAll clear.\n", encoding="utf-8")
    qc_json_path = reports_dir / "qc_metrics.json"
    qc_json_path.write_text(
        json.dumps(
            {
                "status": "PASS",
                "ok": True,
                "mean_fd": 0.12,
                "warnings": ["minor motion note"],
                "errors": [],
                "subjects": [{"subject_id": "sub-01", "mean_fd": 0.12}],
                "thresholds": {"mean_fd": 0.2},
            }
        ),
        encoding="utf-8",
    )
    csv_path = reports_dir / "qc_table.csv"
    csv_path.write_text(
        "subject_id,mean_fd,status\nsub-01,0.12,PASS\nsub-02,0.32,WARN\n",
        encoding="utf-8",
    )
    log_path = logs_dir / "node.log"
    log_path.write_text(
        "node log line\nERROR motion_qc_subject failed\nTraceback line\n",
        encoding="utf-8",
    )
    binary_path = derivatives_dir / "bold.nii"
    binary_path.write_bytes(b"NIFTI")
    mat_path = derivatives_dir / "motion_params.mat"
    mat_path.write_bytes(b"MATLAB")
    missing_path = reports_dir / "missing_report.md"
    state_path = work_dir / "states" / run_id / "data_inspection.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "node": "data_inspection",
                "status": "SUCCESS",
                "outputs": [str(report_path), str(qc_json_path)],
                "stdout_log": str(log_path),
                "result_json": str(qc_json_path),
            }
        ),
        encoding="utf-8",
    )
    summary_path = work_dir / "pipeline_runs" / run_id / "summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "status": "SUCCESS",
                "nodes_total": 1,
                "nodes_success": 1,
                "nodes_failed": 0,
                "node_states": [str(state_path)],
                "outputs": {
                    "report": str(report_path),
                    "binary": str(binary_path),
                    "csv": str(csv_path),
                    "mat": str(mat_path),
                },
                "artifacts": {
                    "qc_json": str(qc_json_path),
                    "missing": str(missing_path),
                },
            }
        ),
        encoding="utf-8",
    )
    record = _add_run_link(store, created, run_id=run_id, summary_path=summary_path)
    store.update_run_link(record.run_link_id, pipeline_path=str(pipeline_path))
    return {
        "summary": summary_path,
        "pipeline": pipeline_path,
        "report": report_path,
        "qc_json": qc_json_path,
        "csv": csv_path,
        "log": log_path,
        "binary": binary_path,
        "mat": mat_path,
        "missing": missing_path,
        "state": state_path,
    }


def test_discover_run_artifacts_finds_outputs_and_enriches_records(tmp_path, monkeypatch):
    store = _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    paths = _write_run_artifact_fixture(store, created)
    project = _project(store, created)
    record = store.get_run_link_by_run_id(created["project_id"], "run_artifacts")
    assert record is not None

    artifacts, warnings = discover_run_artifacts(project, record)

    assert warnings == []
    names = {item["name"] for item in artifacts}
    assert paths["summary"].name in names
    assert paths["pipeline"].name in names
    assert paths["report"].name in names
    assert paths["qc_json"].name in names
    assert paths["csv"].name in names
    assert paths["log"].name in names
    assert paths["mat"].name in names
    node_state = _artifact_by_name(artifacts, paths["state"].name)
    assert node_state["artifact_type"] == "node_state"
    missing = _artifact_by_name(artifacts, paths["missing"].name)
    assert missing["exists"] is False
    assert any("ARTIFACT_FILE_MISSING" in item for item in missing["warnings"])
    qc_json = _artifact_by_name(artifacts, paths["qc_json"].name)
    assert qc_json["artifact_id"] == artifact_id_for_path(paths["qc_json"])
    assert qc_json["json_summary"]["status"] == "PASS"
    assert qc_json["qc_summary"]["status"] == "PASS"
    assert qc_json["qc_summary"]["subject_id"] == "sub-01"
    assert {"label": "mean_fd", "value": "0.12"} in qc_json["qc_summary"]["metrics"]
    log = _artifact_by_name(artifacts, paths["log"].name)
    assert log["node_id"] == "data_inspection"
    assert "motion_qc_subject failed" in log["error_excerpt"]
    binary = _artifact_by_name(artifacts, paths["binary"].name)
    assert binary["kind"] == "nifti"
    assert binary["previewable"] is False
    assert "qc_summary" not in binary
    assert "error_excerpt" not in binary
    mat = _artifact_by_name(artifacts, paths["mat"].name)
    assert mat["kind"] == "matlab"
    assert mat["previewable"] is False

    artifacts_again, _ = discover_run_artifacts(project, record)
    assert (
        _artifact_by_name(artifacts_again, paths["qc_json"].name)["artifact_id"]
        == qc_json["artifact_id"]
    )
    found, find_warnings = find_run_artifact(project, record, qc_json["artifact_id"])
    assert find_warnings == []
    assert found is not None
    assert found["path"] == str(paths["qc_json"].resolve())


def test_discover_run_artifacts_rejects_rawdata_and_outside_paths(tmp_path, monkeypatch):
    store = _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    run_id = "run_rejected_artifacts"
    project_dir = Path(created["project_dir"])
    summary_path = project_dir / "work" / "pipeline_runs" / run_id / "summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    outside_path = tmp_path / "outside_report.md"
    outside_path.write_text("# outside\n", encoding="utf-8")
    rawdata_path = Path(created["rawdata_dir"]) / "dataset_description.json"
    summary_path.write_text(
        json.dumps(
            {
                "run_id": run_id,
                "status": "SUCCESS",
                "outputs": [str(outside_path), str(rawdata_path)],
            }
        ),
        encoding="utf-8",
    )
    record = _add_run_link(store, created, run_id=run_id, summary_path=summary_path)

    artifacts, warnings = discover_run_artifacts(_project(store, created), record)

    assert any("ARTIFACT_PATH_OUTSIDE_PROJECT_OUTPUTS" in item for item in warnings)
    assert any("ARTIFACT_PATH_IN_RAWDATA_REJECTED" in item for item in warnings)
    names = {item["name"] for item in artifacts}
    assert outside_path.name not in names
    assert rawdata_path.name not in names


def test_discover_run_artifacts_accepts_project_data_outputs(tmp_path, monkeypatch):
    store = _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    run_id = "run_project_data_artifact"
    project_dir = Path(created["project_dir"])
    data_path = project_dir / "data" / "dataset_index.json"
    data_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_text(json.dumps({"subjects": ["sub-001"]}), encoding="utf-8")
    summary_path = project_dir / "work" / "pipeline_runs" / run_id / "summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps({"run_id": run_id, "status": "SUCCESS", "outputs": [str(data_path)]}),
        encoding="utf-8",
    )
    record = _add_run_link(store, created, run_id=run_id, summary_path=summary_path)

    artifacts, warnings = discover_run_artifacts(_project(store, created), record)

    assert not any("ARTIFACT_PATH_OUTSIDE_PROJECT_OUTPUTS" in item for item in warnings)
    assert data_path.name in {item["name"] for item in artifacts}


def test_discover_run_artifacts_loads_only_selected_run_registry(tmp_path, monkeypatch):
    store = _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    project_dir = Path(created["project_dir"])
    run_id = "run_selected"
    other_run_id = "run_other"
    selected_output = (
        project_dir
        / "preprocessing_native_runs"
        / run_id
        / "sub-001"
        / "artifacts"
        / "temporal_filtering"
        / "sub-001_task-rest_desc-filtered_bold.nii.gz"
    )
    other_output = (
        project_dir
        / "preprocessing_native_runs"
        / other_run_id
        / "sub-002"
        / "artifacts"
        / "temporal_filtering"
        / "sub-002_task-rest_desc-filtered_bold.nii.gz"
    )
    selected_output.parent.mkdir(parents=True)
    other_output.parent.mkdir(parents=True)
    selected_output.write_bytes(b"SELECTED")
    other_output.write_bytes(b"OTHER")
    summary_path = project_dir / "work" / "pipeline_runs" / run_id / "summary.json"
    summary_path.parent.mkdir(parents=True)
    summary_path.write_text(
        json.dumps({"run_id": run_id, "status": "SUCCESS"}),
        encoding="utf-8",
    )
    registry_path = summary_path.parent / "preprocessing_artifact_registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "preprocessing_run_id": run_id,
                "project_id": created["project_id"],
                "artifacts": [
                    {
                        "artifact_id": "selected-filtered",
                        "artifact_type": "filtered_bold",
                        "subject_id": "sub-001",
                        "stage_id": "temporal_filtering",
                        "path": selected_output.relative_to(project_dir).as_posix(),
                        "path_kind": "project_relative",
                    },
                    {
                        "artifact_id": "cross-run-filtered",
                        "artifact_type": "filtered_bold",
                        "subject_id": "sub-002",
                        "stage_id": "temporal_filtering",
                        "path": other_output.relative_to(project_dir).as_posix(),
                        "path_kind": "project_relative",
                        "metadata": {"preprocessing_run_id": other_run_id},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    audit_path = project_dir / "reports" / "audit_records" / "audit-selected.json"
    audit_path.parent.mkdir(parents=True)
    audit_path.write_text(json.dumps({"audit_id": "audit-selected"}), encoding="utf-8")
    record = _add_run_link(
        store,
        created,
        run_id=run_id,
        summary_path=summary_path,
        payload={
            "audit": {
                "project_audit_path": str(audit_path),
                "audit_path": "outputs/reports/audit_records/audit-selected.json",
            }
        },
    )

    artifacts, warnings = discover_run_artifacts(_project(store, created), record)

    by_name = {item["name"]: item for item in artifacts}
    assert selected_output.name in by_name
    assert by_name[selected_output.name]["registered_artifact_id"] == "selected-filtered"
    assert by_name[selected_output.name]["artifact_type"] == "filtered_bold"
    assert by_name[audit_path.name]["artifact_type"] == "audit_record"
    assert sum(item["name"] == audit_path.name for item in artifacts) == 1
    assert not any(
        item["source"] == "run_link.payload" and not item["exists"] for item in artifacts
    )
    assert other_output.name not in by_name
    assert any("ARTIFACT_REGISTRY_ENTRY_RUN_MISMATCH" in item for item in warnings)


def test_run_artifacts_list_api_smoke_uses_project_history_route(tmp_path, monkeypatch):
    store = _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    paths = _write_run_artifact_fixture(store, created)

    response = client.get(f"/api/projects/{created['project_id']}/runs/run_artifacts/artifacts")

    assert response.status_code == 200
    payload = response.json()
    names = {item["name"] for item in payload["artifacts"]}
    assert paths["report"].name in names
    assert payload["warnings"] == []


def test_run_artifacts_wrong_project_returns_not_found(tmp_path, monkeypatch):
    store = _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    first = _create_project(client, tmp_path, "Artifact First Project")
    second = _create_project(client, tmp_path, "Artifact Second Project")
    _write_run_artifact_fixture(store, first, run_id="run_project_scoped_artifacts")

    response = client.get(
        f"/api/projects/{second['project_id']}/runs/run_project_scoped_artifacts/artifacts"
    )

    assert response.status_code == 404
