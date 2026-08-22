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
from src.backend.app.services.run_summary_preview import (
    load_run_summary_preview,
    summary_preview_payload,
)


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


def _create_project(client: TestClient, tmp_path: Path, name: str = "Summary Project") -> dict:
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
    )
    return store.add_run_link(record)


def _project(store: SQLiteDesktopStore, created: dict) -> ProjectDetail:
    project = store.get_project(created["project_id"])
    assert project is not None
    return project


def _write_summary(created: dict, run_id: str, payload: dict) -> Path:
    summary_path = Path(created["project_dir"]) / "work" / "pipeline_runs" / run_id / "summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(payload), encoding="utf-8")
    return summary_path


def test_load_run_summary_preview_returns_counts_warnings_errors_and_failed_nodes(
    tmp_path,
    monkeypatch,
):
    store = _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    run_id = "run_summary_preview"
    summary_path = _write_summary(
        created,
        run_id,
        {
            "run_id": run_id,
            "status": "SUCCESS",
            "started_at": "2026-06-05T01:00:00Z",
            "ended_at": "2026-06-05T01:01:00Z",
            "nodes_total": 2,
            "nodes_success": 1,
            "nodes_failed": 1,
            "warnings": ["top-level warning"],
            "node_results": [
                {
                    "node_id": "data_inspection",
                    "ok": True,
                    "outputs": ["dataset_index.json"],
                },
                {
                    "node_id": "motion_qc_subject",
                    "ok": False,
                    "warnings": ["motion warning"],
                    "errors": ["motion failed"],
                },
            ],
        },
    )
    record = _add_run_link(store, created, run_id=run_id, summary_path=summary_path)

    preview, warnings, error = load_run_summary_preview(_project(store, created), record)

    assert warnings == []
    assert error is None
    assert preview is not None
    assert preview["run_id"] == run_id
    assert preview["nodes_total"] == 2
    assert preview["nodes_succeeded"] == 1
    assert preview["nodes_failed"] == 1
    assert preview["finished_at"] == "2026-06-05T01:01:00Z"
    assert "top-level warning" in preview["warnings"]
    assert "motion_qc_subject: motion warning" in preview["warnings"]
    assert preview["failed_nodes"][0]["node_id"] == "motion_qc_subject"
    assert preview["errors"][0]["node_id"] == "motion_qc_subject"


def test_load_run_summary_preview_reports_missing_summary(tmp_path, monkeypatch):
    store = _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    run_id = "run_missing_summary"
    summary_path = Path(created["project_dir"]) / "work" / "pipeline_runs" / run_id / "summary.json"
    record = _add_run_link(store, created, run_id=run_id, summary_path=summary_path)

    preview, warnings, error = load_run_summary_preview(_project(store, created), record)

    assert preview is None
    assert error is None
    assert any("SUMMARY_FILE_MISSING" in item for item in warnings)


def test_load_run_summary_preview_reports_malformed_json(tmp_path, monkeypatch):
    store = _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    run_id = "run_bad_summary"
    summary_path = Path(created["project_dir"]) / "work" / "pipeline_runs" / run_id / "summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text("{not valid json", encoding="utf-8")
    record = _add_run_link(store, created, run_id=run_id, summary_path=summary_path)

    preview, warnings, error = load_run_summary_preview(_project(store, created), record)

    assert preview is None
    assert warnings == []
    assert error is not None
    assert "SUMMARY_JSON_INVALID" in error


def test_load_run_summary_preview_rejects_summary_path_outside_project_outputs(
    tmp_path,
    monkeypatch,
):
    store = _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    run_id = "run_outside_summary"
    outside_summary = tmp_path / "outside-summary.json"
    outside_summary.write_text(json.dumps({"run_id": run_id}), encoding="utf-8")
    record = _add_run_link(store, created, run_id=run_id, summary_path=outside_summary)

    preview, warnings, error = load_run_summary_preview(_project(store, created), record)

    assert preview is None
    assert error is None
    assert any("SUMMARY_PATH_OUTSIDE_PROJECT_OUTPUTS" in item for item in warnings)


def test_load_run_summary_preview_rejects_summary_path_in_rawdata(tmp_path, monkeypatch):
    store = _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    run_id = "run_rawdata_summary"
    rawdata_summary = Path(created["rawdata_dir"]) / "dataset_description.json"
    record = _add_run_link(store, created, run_id=run_id, summary_path=rawdata_summary)

    preview, warnings, error = load_run_summary_preview(_project(store, created), record)

    assert preview is None
    assert error is None
    assert any("SUMMARY_PATH_IN_RAWDATA_REJECTED" in item for item in warnings)


def test_summary_preview_truncates_large_raw_payload(tmp_path, monkeypatch):
    store = _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    record = _add_run_link(store, created, run_id="run_large_summary", summary_path=None)

    preview = summary_preview_payload(
        {"run_id": "run_large_summary", "status": "SUCCESS", "payload": "x" * 25_000},
        record,
    )

    assert preview["raw_truncated"] is True
    assert preview["raw"]["truncated"] is True
    assert "payload" in preview["raw"]["top_level_keys"]


def test_run_detail_api_smoke_includes_summary_preview(tmp_path, monkeypatch):
    store = _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    run_id = "run_summary_api_smoke"
    summary_path = _write_summary(
        created,
        run_id,
        {"run_id": run_id, "status": "SUCCESS", "nodes_total": 1, "nodes_success": 1},
    )
    _add_run_link(store, created, run_id=run_id, summary_path=summary_path)

    response = client.get(f"/api/projects/{created['project_id']}/runs/{run_id}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary_preview"]["run_id"] == run_id
    assert payload["summary_preview"]["nodes_succeeded"] == 1
    assert payload["summary_preview_error"] is None
