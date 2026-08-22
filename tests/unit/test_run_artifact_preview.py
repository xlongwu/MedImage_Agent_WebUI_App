from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.backend.app.api import (
    dashboard_routes,
    project_routes,
)
from src.backend.app.main import app
from src.backend.app.planner import project_context, reviewed_plan_store
from src.backend.app.runtime import desktop_config
from src.backend.app.schemas.desktop import RunLinkRecord
from src.backend.app.services.mock_store import SQLiteDesktopStore, utc_now_iso
from src.backend.app.services.run_artifact_preview import (
    artifact_preview_payload,
    json_preview_summary,
)


def _artifact(path: Path, *, kind: str, previewable: bool = True) -> dict:
    return {
        "artifact_id": f"artifact_{path.stem}",
        "name": path.name,
        "kind": kind,
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else None,
        "modified_at": None,
        "previewable": path.exists() and previewable,
        "warnings": [],
        "source": "test",
        "suffix": path.suffix.lower(),
    }


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


def _create_project(client: TestClient, tmp_path: Path, name: str = "Preview Project") -> dict:
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


def _artifact_by_name(payload: dict, name: str) -> dict:
    for artifact in payload["artifacts"]:
        if artifact["name"] == name:
            return artifact
    raise AssertionError(f"Artifact not found: {name}; got {payload['artifacts']}")


def test_artifact_preview_json_payload_and_summary(tmp_path):
    path = tmp_path / "qc_metrics.json"
    path.write_text(
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

    payload = artifact_preview_payload(_artifact(path, kind="json"))

    assert payload["ok"] is True
    assert payload["preview_type"] == "json"
    assert payload["json"]["mean_fd"] == 0.12
    assert payload["json_summary"] == json_preview_summary(payload["json"])
    assert payload["json_summary"]["top_level_keys"][:3] == ["status", "ok", "mean_fd"]
    assert payload["json_summary"]["status"] == "PASS"
    assert payload["json_summary"]["warnings"]["count"] == 1
    assert payload["json_summary"]["errors"]["count"] == 0
    field_summaries = {item["key"]: item for item in payload["json_summary"]["field_summaries"]}
    assert field_summaries["subjects"]["type"] == "array"
    assert field_summaries["subjects"]["size"] == 1
    assert field_summaries["thresholds"]["type"] == "object"
    assert field_summaries["thresholds"]["size"] == 1
    assert payload["truncated"] is False


def test_artifact_preview_reports_malformed_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not-json", encoding="utf-8")

    payload = artifact_preview_payload(_artifact(path, kind="json"))

    assert payload["ok"] is False
    assert any("ARTIFACT_JSON_INVALID" in item for item in payload["errors"])


def test_artifact_preview_csv_payload(tmp_path):
    path = tmp_path / "qc_table.csv"
    path.write_text(
        "subject_id,mean_fd,status\nsub-01,0.12,PASS\nsub-02,0.32,WARN\n",
        encoding="utf-8",
    )

    payload = artifact_preview_payload(_artifact(path, kind="csv"))

    assert payload["ok"] is True
    assert payload["preview_type"] == "csv"
    assert payload["csv"]["columns"] == ["subject_id", "mean_fd", "status"]
    assert payload["csv"]["rows"] == [
        ["sub-01", "0.12", "PASS"],
        ["sub-02", "0.32", "WARN"],
    ]
    assert payload["csv"]["displayed_rows"] == 2
    assert payload["truncated"] is False


def test_artifact_preview_csv_truncates_rows(tmp_path):
    path = tmp_path / "large_table.csv"
    rows = ["subject_id,mean_fd"] + [f"sub-{index:03d},{index / 100:.2f}" for index in range(150)]
    path.write_text("\n".join(rows), encoding="utf-8")

    payload = artifact_preview_payload(_artifact(path, kind="csv"))

    assert payload["preview_type"] == "csv"
    assert payload["truncated"] is True
    assert payload["csv"]["displayed_rows"] == 99
    assert any("ARTIFACT_PREVIEW_TRUNCATED" in item for item in payload["warnings"])


@pytest.mark.parametrize(
    ("filename", "kind", "expected_type", "content"),
    [
        ("notes.txt", "text", "text", "plain text preview"),
        ("qc_report.md", "markdown", "markdown", "# QC Report"),
        ("node.log", "log", "log", "ERROR motion_qc_subject failed"),
    ],
)
def test_artifact_preview_text_markdown_and_log_payloads(
    tmp_path,
    filename,
    kind,
    expected_type,
    content,
):
    path = tmp_path / filename
    path.write_text(content, encoding="utf-8")

    payload = artifact_preview_payload(_artifact(path, kind=kind))

    assert payload["ok"] is True
    assert payload["preview_type"] == expected_type
    assert content in payload["content"]
    assert payload["truncated"] is False


def test_artifact_preview_large_text_and_json_are_truncated(tmp_path):
    large_text = tmp_path / "large_report.md"
    large_json = tmp_path / "large_payload.json"
    large_text.write_text("# Large\n" + ("x" * 90_000), encoding="utf-8")
    large_json.write_text(json.dumps({"payload": "x" * 90_000}), encoding="utf-8")

    for path, kind in ((large_text, "markdown"), (large_json, "json")):
        payload = artifact_preview_payload(_artifact(path, kind=kind))
        assert payload["truncated"] is True
        assert any("ARTIFACT_PREVIEW_TRUNCATED" in item for item in payload["warnings"])


def test_artifact_preview_binary_is_metadata_only(tmp_path):
    path = tmp_path / "bold.nii"
    path.write_bytes(b"NIFTI")

    payload = artifact_preview_payload(_artifact(path, kind="nifti", previewable=False))

    assert payload["preview_type"] == "metadata_only"
    assert payload["content"] is None
    assert any("ARTIFACT_NOT_PREVIEWABLE" in item for item in payload["warnings"])


def test_artifact_preview_unsupported_file_is_metadata_only(tmp_path):
    path = tmp_path / "pipeline.yaml"
    path.write_text("pipeline_id: unsupported-preview\n", encoding="utf-8")

    payload = artifact_preview_payload(_artifact(path, kind="yaml", previewable=False))

    assert payload["kind"] == "yaml"
    assert payload["preview_type"] == "metadata_only"
    assert payload["content"] is None
    assert any("ARTIFACT_NOT_PREVIEWABLE" in item for item in payload["warnings"])


def test_run_artifact_detail_api_smoke_uses_project_history_route(tmp_path, monkeypatch):
    store = _isolated_store(tmp_path, monkeypatch)
    client = TestClient(app)
    created = _create_project(client, tmp_path)
    run_id = "run_artifact_detail_smoke"
    project_dir = Path(created["project_dir"])
    reports_dir = project_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    qc_json_path = reports_dir / "qc_metrics.json"
    qc_json_path.write_text(json.dumps({"status": "PASS", "mean_fd": 0.12}), encoding="utf-8")
    summary_path = project_dir / "work" / "pipeline_runs" / run_id / "summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps({"run_id": run_id, "status": "SUCCESS", "outputs": [str(qc_json_path)]}),
        encoding="utf-8",
    )
    _add_run_link(store, created, run_id=run_id, summary_path=summary_path)
    listing = client.get(f"/api/projects/{created['project_id']}/runs/{run_id}/artifacts").json()
    artifact = _artifact_by_name(listing, qc_json_path.name)

    response = client.get(
        f"/api/projects/{created['project_id']}/runs/{run_id}/artifacts/{artifact['artifact_id']}"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["preview_type"] == "json"
    assert payload["json"]["mean_fd"] == 0.12
