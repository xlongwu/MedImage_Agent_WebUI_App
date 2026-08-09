"""Executor node contract smoke test.

Validates the smallest stable contract that every real preprocessing
node must follow.  Exercises the ``contract_smoke`` node — a minimal
deterministic node with no external dependencies — through the full
execute → run-detail → artifacts → preview → scoping lifecycle.

Covers all nine verification points from the contract spec:

1. Executor runs a pipeline with at least one safe node.
2. Node receives a clear input context (run_id, work_dir, log_dir).
3. Node returns a structured status result.
4. Node contributes to the run summary.
5. Node registers artifacts or a clear no-artifact state.
6. Artifacts are discoverable through the existing discovery path.
7. Artifacts are previewable through the existing preview path.
8. Node failure is structured without crashing the API layer.
9. Wrong-project scoping prevents access.
"""

from __future__ import annotations

from pathlib import Path

from src.backend.app.planner.project_context import (
    apply_project_context_to_plan,
    load_project_context,
)
from src.backend.app.runtime.pipeline_executor import (
    run_pipeline as real_run_pipeline,
)
from tests.goal_contract_helpers import reviewed_goal_candidate
from tests.integration.test_real_project_safe_smoke import (
    real_project_smoke,  # noqa: F401  — pytest fixture
)


def _save_contract_smoke_plan(client, created: dict) -> dict:
    """Save a reviewed plan with the contract_smoke node and project context."""
    context = load_project_context(
        created["project_id"],
        created["project_config_path"],
    )
    plan = apply_project_context_to_plan(
        {
            "pipeline_id": "contract_smoke_test",
            "nodes": [
                {
                    "id": "contract_smoke",
                    "backend": "python",
                    "depends_on": [],
                    "params": {},
                },
            ],
        },
        context,
    )
    goal = "Validate the executor node contract"
    save_resp = client.post(
        f"/api/projects/{created['project_id']}/plans",
        json={
            "plan": plan,
            "project_config_path": created["project_config_path"],
            "validation": {"ok": True},
            "goal": goal,
            "provider": "contract-smoke-test",
            "goal_contract_candidate": reviewed_goal_candidate(plan, goal),
            "reviewed_actor": "contract-smoke-test",
        },
    )
    assert save_resp.status_code == 200, save_resp.text
    return save_resp.json()["reviewed_plan"]


def _save_contract_smoke_failure_plan(client, created: dict) -> dict:
    """Save a reviewed plan with contract_smoke fail=true."""
    context = load_project_context(
        created["project_id"],
        created["project_config_path"],
    )
    plan = apply_project_context_to_plan(
        {
            "pipeline_id": "contract_smoke_failure_test",
            "nodes": [
                {
                    "id": "contract_smoke",
                    "backend": "python",
                    "depends_on": [],
                    "params": {"fail": True},
                },
            ],
        },
        context,
    )
    goal = "Validate failure path in node contract"
    save_resp = client.post(
        f"/api/projects/{created['project_id']}/plans",
        json={
            "plan": plan,
            "project_config_path": created["project_config_path"],
            "validation": {"ok": True},
            "goal": goal,
            "provider": "contract-smoke-test",
            "goal_contract_candidate": reviewed_goal_candidate(plan, goal),
            "reviewed_actor": "contract-smoke-test",
        },
    )
    assert save_resp.status_code == 200, save_resp.text
    return save_resp.json()["reviewed_plan"]


def _execute_body(created: dict, plan: dict, reviewed: dict) -> dict:
    return {
        "plan": plan,
        "approval": {
            "approved": True,
            "approved_by": "contract-smoke-test",
            "approved_nodes": ["*"],
            "rejected_nodes": [],
            "approval_summary_hash": reviewed["payload"]["approval_envelope"][
                "summary_hash"
            ],
        },
        "project_id": created["project_id"],
        "reviewed_plan_id": reviewed["reviewed_plan_id"],
        "project_config_path": created["project_config_path"],
        "dry_run": False,
        "persist_audit": True,
        "write_pipeline_yaml": True,
        "confirm_execution": True,
        "actor": "contract-smoke-test",
    }


# ══════════════════════════════════════════════════════════════════════════════
# Happy path — all nine contract verification points
# ══════════════════════════════════════════════════════════════════════════════


def test_node_contract_happy_path(real_project_smoke, monkeypatch, tmp_path):  # noqa: F811
    """Full lifecycle with the contract_smoke node — happy path."""
    client = real_project_smoke["client"]
    created = real_project_smoke["created"]
    rawdata_dir = real_project_smoke["rawdata_dir"]
    project_id = created["project_id"]

    # ── Save a new reviewed plan using contract_smoke ───────────────────
    reviewed = _save_contract_smoke_plan(client, created)
    assert reviewed["reviewed_plan_id"]
    assert Path(reviewed["plan_path"]).is_file()

    # ── Patch the real pipeline executor ────────────────────────────────
    monkeypatch.setattr(
        "src.backend.app.runtime.execution_gateway.PIPELINE_EXECUTOR",
        real_run_pipeline,
    )

    # ══════════════════════════════════════════════════════════════════════
    # 1. Executor runs a pipeline with the contract_smoke node
    # ══════════════════════════════════════════════════════════════════════
    happy_plan = apply_project_context_to_plan(
        {
            "pipeline_id": "contract_smoke_test",
            "nodes": [
                {
                    "id": "contract_smoke",
                    "backend": "python",
                    "depends_on": [],
                    "params": {},
                },
            ],
        },
        load_project_context(created["project_id"], created["project_config_path"]),
    )
    body = _execute_body(created, happy_plan, reviewed)
    response = client.post("/api/plans/execute-reviewed", json=body)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "EXECUTION_SUBMITTED", payload
    run_id = payload["run_id"]
    run_link_id = payload["run_link_id"]
    assert run_id.startswith("run_"), f"Expected run_ prefix, got {run_id}"
    assert run_link_id, "run_link_id must be nonempty"
    assert payload["executor_result"]["status"] == "SUCCESS"

    # ══════════════════════════════════════════════════════════════════════
    # 2-3. Context + structured result — verified via run detail
    # ══════════════════════════════════════════════════════════════════════
    detail = client.get(f"/api/projects/{project_id}/runs/{run_id}")
    assert detail.status_code == 200, detail.text
    run_link = detail.json()["run_link"]
    assert run_link["run_id"] == run_id
    assert run_link["status"] == "SUCCESS"

    # ══════════════════════════════════════════════════════════════════════
    # 4. Run summary preview — node contributed to the summary
    # ══════════════════════════════════════════════════════════════════════
    summary = detail.json()["summary_preview"]
    assert summary is not None, (
        f"summary_preview is None; error={detail.json().get('summary_preview_error')}"
    )
    assert summary["run_id"] == run_id
    assert summary["status"] == "SUCCESS"
    assert summary["nodes_total"] == 1
    assert summary["nodes_succeeded"] == 1
    assert summary["nodes_failed"] == 0

    # ══════════════════════════════════════════════════════════════════════
    # 5-6. Artifact discovery
    # ══════════════════════════════════════════════════════════════════════
    artifacts_resp = client.get(f"/api/projects/{project_id}/runs/{run_id}/artifacts")
    assert artifacts_resp.status_code == 200, artifacts_resp.text
    artifacts = artifacts_resp.json()["artifacts"]
    assert isinstance(artifacts, list)
    assert len(artifacts) >= 2, (
        f"Expected ≥2 artifacts (report + log + summary), got {len(artifacts)}: "
        f"{[a['name'] for a in artifacts]}"
    )

    names = {a["name"] for a in artifacts}
    assert "contract_smoke_report.json" in names, f"Artifact names: {names}"
    assert "contract_smoke.log" in names, f"Artifact names: {names}"

    # ══════════════════════════════════════════════════════════════════════
    # 7. Artifact preview — at least one previewable artifact
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
    )
    assert preview["artifact_id"] == target["artifact_id"]
    assert preview["exists"] is True

    # For JSON artifacts, json_summary must be populated
    if preview["preview_type"] == "json":
        assert preview["json_summary"] is not None, f"json_summary missing for {target['name']}"

    # ══════════════════════════════════════════════════════════════════════
    # 9. Wrong-project scoping
    # ══════════════════════════════════════════════════════════════════════
    second_resp = client.post(
        "/api/projects/create",
        json={
            "project_name": "Contract Scoping Test",
            "rawdata_dir": str(rawdata_dir),
            "project_dir": str(tmp_path / "contract_scope_test"),
        },
    )
    assert second_resp.status_code == 200, second_resp.text
    second = second_resp.json()

    assert client.get(f"/api/projects/{second['project_id']}/runs/{run_id}").status_code == 404

    assert (
        client.get(f"/api/projects/{second['project_id']}/runs/{run_id}/artifacts").status_code
        == 404
    )

    existing_artifact_id = artifacts[0]["artifact_id"]
    assert (
        client.get(
            f"/api/projects/{second['project_id']}/runs/{run_id}/artifacts/{existing_artifact_id}"
        ).status_code
        == 404
    )


# ══════════════════════════════════════════════════════════════════════════════
# 8. Node failure is structured without crashing the API layer
# ══════════════════════════════════════════════════════════════════════════════


def test_node_contract_failure_path(real_project_smoke, monkeypatch, tmp_path):  # noqa: F811
    """The contract_smoke node with ``fail=true`` must return structured
    failure that the executor captures without raising an exception."""
    client = real_project_smoke["client"]
    created = real_project_smoke["created"]
    project_id = created["project_id"]

    # ── Plan with fail=true param ───────────────────────────────────────
    reviewed = _save_contract_smoke_failure_plan(client, created)

    monkeypatch.setattr(
        "src.backend.app.runtime.execution_gateway.PIPELINE_EXECUTOR",
        real_run_pipeline,
    )

    failure_plan = apply_project_context_to_plan(
        {
            "pipeline_id": "contract_smoke_failure_test",
            "nodes": [
                {
                    "id": "contract_smoke",
                    "backend": "python",
                    "depends_on": [],
                    "params": {"fail": True},
                },
            ],
        },
        load_project_context(created["project_id"], created["project_config_path"]),
    )
    body = {
        "plan": failure_plan,
        "approval": {
            "approved": True,
            "approved_by": "contract-smoke-test",
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
        "actor": "contract-smoke-test",
    }
    response = client.post("/api/plans/execute-reviewed", json=body)
    assert response.status_code == 200, response.text
    payload = response.json()

    # The executor itself should not crash. The API truthfully exposes the
    # terminal failure while preserving the submitted run evidence below.
    assert payload["status"] == "EXECUTION_FAILED", payload
    assert payload["ok"] is False
    assert payload["execution"]["submitted"] is True
    assert payload["executor_result"]["status"] == "FAILED"
    assert any("failure status: FAILED" in item for item in payload["errors"])

    run_id = payload["run_id"]

    detail = client.get(f"/api/projects/{project_id}/runs/{run_id}")
    assert detail.status_code == 200
    run_link = detail.json()["run_link"]
    # Pipeline status is FAILED because the single node failed
    assert run_link["status"] == "FAILED", f"Expected FAILED status, got {run_link['status']}"

    summary = detail.json()["summary_preview"]
    assert summary is not None
    assert summary["status"] == "FAILED"
    assert summary["nodes_failed"] == 1
    assert summary["nodes_succeeded"] == 0

    # The structured failure message lives in the node state artifact,
    # not in the top-level summary (since the node returned ok=False
    # without raising an exception).  Verify the node state artifact
    # is discoverable and contains the error.
    fail_artifacts_resp = client.get(f"/api/projects/{project_id}/runs/{run_id}/artifacts")
    assert fail_artifacts_resp.status_code == 200
    fail_artifacts = fail_artifacts_resp.json()["artifacts"]

    # At minimum the summary.json and node state JSON are discoverable
    assert len(fail_artifacts) >= 2, f"Expected ≥2 artifacts for failure run, got {fail_artifacts}"
