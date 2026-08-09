"""End-to-end smoke test covering the full real project run lifecycle.

Reuses the ``real_project_smoke`` fixture from
``test_real_project_safe_smoke`` which already creates a project, saves a
reviewed plan, and patches store / execution paths.

This module adds the remaining lifecycle checks:

* Execute-reviewed with the real pipeline executor (steps 3-4).
* Run detail retrieval (step 5).
* Run summary preview (step 6).
* Artifact discovery (step 7).
* Artifact preview for at least one previewable artifact (step 8).
* Wrong-project scoping (step 9).
"""

from __future__ import annotations

from pathlib import Path

from src.backend.app.runtime.pipeline_executor import (
    run_pipeline as real_run_pipeline,
)
from tests.integration.test_real_project_safe_smoke import (
    real_project_smoke,  # noqa: F401  — pytest fixture
)


def test_full_run_lifecycle_smoke(real_project_smoke, monkeypatch, tmp_path):  # noqa: F811
    """Execute a reviewed plan with the real executor, then verify every API
    endpoint in the post-execution lifecycle.

    The ``real_project_smoke`` fixture already:

    * created a project from ``examples/synthetic_bids/rawdata``
    * saved a reviewed plan with a single ``data_inspection`` node
    * set ``MEDIMAGE_ENABLE_REVIEWED_EXECUTION=1``
    * patched ``AUDIT_RECORD_DIR`` and ``REVIEWED_PIPELINE_DIR``
    """
    client = real_project_smoke["client"]
    created = real_project_smoke["created"]
    plan = real_project_smoke["plan"]
    reviewed = real_project_smoke["reviewed"]
    rawdata_dir = real_project_smoke["rawdata_dir"]
    project_id = created["project_id"]

    # ── Patch the real pipeline executor ────────────────────────────────
    monkeypatch.setattr(
        "src.backend.app.runtime.execution_gateway.PIPELINE_EXECUTOR",
        real_run_pipeline,
    )

    # ══════════════════════════════════════════════════════════════════════
    # 3-4. Execute the reviewed plan
    # ══════════════════════════════════════════════════════════════════════
    body = {
        "plan": plan,
        "approval": {
            "approved": True,
            "approved_by": "lifecycle-smoke-test",
            "approved_nodes": ["*"],
            "rejected_nodes": [],
            "approval_summary_hash": reviewed["payload"]["approval_envelope"][
                "summary_hash"
            ],
        },
        "project_id": project_id,
        "reviewed_plan_id": reviewed["reviewed_plan_id"],
        "project_config_path": created["project_config_path"],
        "dry_run": False,
        "persist_audit": True,
        "write_pipeline_yaml": True,
        "confirm_execution": True,
        "actor": "lifecycle-smoke-test",
    }
    response = client.post("/api/plans/execute-reviewed", json=body)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "EXECUTION_SUBMITTED", payload
    run_id = payload["run_id"]
    run_link_id = payload["run_link_id"]
    assert run_id.startswith("run_")
    assert run_link_id
    assert payload["executor_result"]["status"] == "SUCCESS"
    assert Path(payload["pipeline_path"]).is_file()
    assert Path(payload["summary_path"]).is_file()

    # ══════════════════════════════════════════════════════════════════════
    # 5. Run detail retrieval
    # ══════════════════════════════════════════════════════════════════════
    detail = client.get(f"/api/projects/{project_id}/runs/{run_id}")
    assert detail.status_code == 200, detail.text
    detail_body = detail.json()
    run_link = detail_body["run_link"]
    assert run_link["run_id"] == run_id
    assert run_link["run_link_id"] == run_link_id
    assert run_link["status"] == "SUCCESS"
    assert run_link["reviewed_plan_id"] == reviewed["reviewed_plan_id"]
    assert Path(run_link["summary_path"]).is_file()

    # ══════════════════════════════════════════════════════════════════════
    # 6. Run summary preview
    # ══════════════════════════════════════════════════════════════════════
    summary = detail_body.get("summary_preview")
    assert summary is not None, (
        f"summary_preview is None; "
        f"error={detail_body.get('summary_preview_error')}, "
        f"warnings={detail_body.get('warnings')}"
    )
    assert summary["run_id"] == run_id
    assert summary["status"] in ("SUCCESS", "COMPLETED")
    assert isinstance(summary.get("nodes_total"), int)
    assert summary["nodes_total"] >= 1
    # The single data_inspection node should have succeeded
    assert summary.get("nodes_succeeded") == 1
    assert summary.get("nodes_failed") == 0

    # ══════════════════════════════════════════════════════════════════════
    # 7. Artifact discovery
    # ══════════════════════════════════════════════════════════════════════
    artifacts_resp = client.get(f"/api/projects/{project_id}/runs/{run_id}/artifacts")
    assert artifacts_resp.status_code == 200, artifacts_resp.text
    artifacts = artifacts_resp.json()["artifacts"]
    assert isinstance(artifacts, list)
    assert len(artifacts) >= 1, f"Expected at least 1 artifact, got {len(artifacts)}: {artifacts}"

    # The data_inspection node produces dataset_index.json,
    # data_completeness_report.json, and subject_table.csv.
    # The summary.json and pipeline YAML are also discoverable.
    names = {a["name"] for a in artifacts}
    assert "summary.json" in names, f"summary.json not in {names}"

    # ══════════════════════════════════════════════════════════════════════
    # 8. Artifact preview (at least one previewable artifact)
    # ══════════════════════════════════════════════════════════════════════
    previewable = [a for a in artifacts if a.get("previewable")]
    assert len(previewable) >= 1, (
        f"No previewable artifacts among {[(a['name'], a.get('kind')) for a in artifacts]}"
    )
    target = previewable[0]
    preview_resp = client.get(
        f"/api/projects/{project_id}/runs/{run_id}/artifacts/{target['artifact_id']}"
    )
    assert preview_resp.status_code == 200, preview_resp.text
    preview = preview_resp.json()
    assert preview["ok"] is True
    assert preview["preview_type"] in (
        "json",
        "csv",
        "markdown",
        "text",
        "log",
        "metadata_only",
    ), f"Unexpected preview_type: {preview['preview_type']}"
    assert preview["artifact_id"] == target["artifact_id"]
    assert preview["exists"] is True

    # For a JSON artifact, json_summary must be populated
    if preview["preview_type"] == "json":
        assert preview["json_summary"] is not None

    # ══════════════════════════════════════════════════════════════════════
    # 9. Wrong-project scoping
    # ══════════════════════════════════════════════════════════════════════
    second_resp = client.post(
        "/api/projects/create",
        json={
            "project_name": "Wrong Project Scope Test",
            "rawdata_dir": str(rawdata_dir),
            "project_dir": str(tmp_path / "wrong_project_scope"),
        },
    )
    assert second_resp.status_code == 200, second_resp.text
    second = second_resp.json()

    # Run detail must not leak across projects
    wrong_detail = client.get(f"/api/projects/{second['project_id']}/runs/{run_id}")
    assert wrong_detail.status_code == 404, (
        f"Expected 404, got {wrong_detail.status_code}: {wrong_detail.text}"
    )

    # Artifact list must not leak across projects
    wrong_artifacts = client.get(f"/api/projects/{second['project_id']}/runs/{run_id}/artifacts")
    assert wrong_artifacts.status_code == 404, f"Expected 404, got {wrong_artifacts.status_code}"

    # Artifact preview must not leak across projects
    existing_artifact_id = artifacts[0]["artifact_id"]
    wrong_preview = client.get(
        f"/api/projects/{second['project_id']}/runs/{run_id}/artifacts/{existing_artifact_id}"
    )
    assert wrong_preview.status_code == 404, f"Expected 404, got {wrong_preview.status_code}"

    # The wrong project's own run list must not contain the original run
    second_runs = client.get(f"/api/projects/{second['project_id']}/runs")
    assert second_runs.status_code == 200
    second_run_ids = {r["run_id"] for r in second_runs.json()["runs"]}
    assert run_id not in second_run_ids, (
        f"Run {run_id} leaked into second project's run list: {second_run_ids}"
    )
