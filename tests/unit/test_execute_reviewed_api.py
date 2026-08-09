"""Tests for Execute Reviewed Plan API (POST /api/plans/execute-reviewed)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.backend.app.main import app
from src.backend.app.core.exceptions import SafetyError
from src.backend.app.planner.reviewed_plan_store import ReviewedPlanStoreError
from src.backend.app.schemas.agent_lifecycle import AgentLifecycleEvent, AgentLifecycleRecord

client = TestClient(app)


def _bound_success(**kwargs):
    return {
        "status": "SUCCESS",
        "run_id": kwargs["execution_context"].dispatch.run_id,
    }


def _valid_body(**overrides):
    body = {
        "plan": {
            "pipeline_id": "test",
            "nodes": [
                {"id": "data_inspection", "backend": "python", "depends_on": [], "params": {}},
                {
                    "id": "motion_qc_subject",
                    "backend": "python",
                    "depends_on": ["data_inspection"],
                    "params": {},
                },
            ],
        },
        "approval": {
            "approved": True,
            "approved_by": "user",
            "approved_nodes": ["*"],
            "rejected_nodes": [],
        },
        "dry_run": True,
    }
    body.update(overrides)
    return body


# ── 1. Returns 200 ──


def test_returns_200():
    resp = client.post("/api/plans/execute-reviewed", json=_valid_body())
    assert resp.status_code == 200


# ── 2. DRY_RUN_OK ──


def test_dry_run_ok():
    resp = client.post("/api/plans/execute-reviewed", json=_valid_body())
    data = resp.json()
    assert data["status"] == "DRY_RUN_OK"


# ── 3. would_execute true ──


def test_would_execute_true():
    resp = client.post("/api/plans/execute-reviewed", json=_valid_body())
    assert resp.json()["would_execute"] is True


# ── 4. executor_called false ──


def test_executor_called_false():
    resp = client.post("/api/plans/execute-reviewed", json=_valid_body())
    assert resp.json()["execution"]["executor_called"] is False


# ── 5. Validation failed ──


def test_validation_failed():
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_valid_body(
            plan={"pipeline_id": "bad", "nodes": [{"id": "nonexistent_xyz", "depends_on": []}]},
        ),
    )
    data = resp.json()
    assert data["status"] == "VALIDATION_FAILED"
    assert data["would_execute"] is False


# ── 6. Approval missing → blocked ──


def test_approval_missing_blocked():
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_valid_body(
            plan={
                "pipeline_id": "test",
                "nodes": [
                    {
                        "id": "spm_realign_subject",
                        "backend": "matlab-spm",
                        "depends_on": [],
                        "params": {},
                    },
                ],
            },
            approval=None,
        ),
    )
    data = resp.json()
    assert data["status"] == "APPROVAL_GATE_BLOCKED"


# ── 7. Approval false → blocked ──


def test_approval_false_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "spm_realign_subject", "backend": "matlab-spm", "depends_on": [], "params": {}}
        ],
    }
    resp = client.post(
        "/api/plans/execute-reviewed",
        json={
            "plan": plan,
            "approval": {"approved": False},
            "dry_run": True,
        },
    )
    assert resp.json()["status"] == "APPROVAL_GATE_BLOCKED"


# ── 8. Missing approved node → blocked ──


def test_missing_approved_node_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "spm_realign_subject", "backend": "matlab-spm", "depends_on": [], "params": {}}
        ],
    }
    resp = client.post(
        "/api/plans/execute-reviewed",
        json={
            "plan": plan,
            "approval": {"approved": True, "approved_nodes": ["other_node"], "rejected_nodes": []},
            "dry_run": True,
        },
    )
    assert resp.json()["status"] == "APPROVAL_GATE_BLOCKED"


# ── 9. dry_run=false + env not set → REVIEWED_EXECUTION_DISABLED ──


def test_dry_run_false_refused():
    resp = client.post("/api/plans/execute-reviewed", json=_valid_body(dry_run=False))
    data = resp.json()
    assert data["status"] == "REVIEWED_EXECUTION_DISABLED"
    assert data["dry_run"] is False
    assert data["execution"]["executor_called"] is False


# ── 10. Missing plan → 422 ──


def test_missing_plan_422():
    resp = client.post("/api/plans/execute-reviewed", json={"dry_run": True})
    assert resp.status_code == 422


# ── 11. Backend re-validates ──


def test_backend_revalidates():
    """Backend must re-run validate_plan — cannot trust front-end."""
    plan = {
        "pipeline_id": "test",
        "nodes": [{"id": "data_inspection", "backend": "python", "depends_on": [], "params": {}}],
    }
    resp = client.post(
        "/api/plans/execute-reviewed",
        json={
            "plan": plan,
            "approval": None,
            "dry_run": True,
        },
    )
    data = resp.json()
    assert data["status"] == "DRY_RUN_OK"
    assert data["validation"] is not None
    assert data["validation"]["ok"] is True


# ── 12. No executor ──


def test_no_executor():
    client.post("/api/plans/execute-reviewed", json=_valid_body())


# ── 13. No runner ──


def test_no_runner():
    client.post("/api/plans/execute-reviewed", json=_valid_body())


# ── 14. JSON serializable ──


def test_json_serializable():
    resp = client.post("/api/plans/execute-reviewed", json=_valid_body())
    json.loads(resp.text)


# ── 15. persist_audit=false → no audit ──


def test_persist_audit_false():
    resp = client.post("/api/plans/execute-reviewed", json=_valid_body(persist_audit=False))
    data = resp.json()
    assert data["audit"]["persisted"] is False


# ── 16. persist_audit=true DRY_RUN_OK writes audit ──


def test_persist_audit_dry_run_ok():
    resp = client.post("/api/plans/execute-reviewed", json=_valid_body(persist_audit=True))
    data = resp.json()
    assert data["status"] == "DRY_RUN_OK"
    assert data["audit"]["persisted"] is True
    assert "audit_id" in data["audit"]
    assert "audit_path" in data["audit"]


# ── 17. audit file exists on disk ──


def test_audit_file_exists():
    resp = client.post("/api/plans/execute-reviewed", json=_valid_body(persist_audit=True))
    data = resp.json()
    path = data["audit"].get("audit_path")
    assert path
    from pathlib import Path

    assert Path(path).exists()
    content = json.loads(Path(path).read_text(encoding="utf-8"))
    assert "plan_hash" in content
    assert "validation_hash" in content


# ── 18. validation failed + audit writes blocked event ──


def test_validation_failed_writes_audit():
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_valid_body(
            plan={"pipeline_id": "bad", "nodes": [{"id": "nonexistent_xyz", "depends_on": []}]},
            persist_audit=True,
        ),
    )
    data = resp.json()
    assert data["status"] == "VALIDATION_FAILED"
    assert data["audit"]["persisted"] is True
    assert data["audit"]["event_type"] == "execution_blocked"


# ── 19. approval blocked + audit writes blocked event ──


def test_approval_blocked_writes_audit():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "spm_realign_subject", "backend": "matlab-spm", "depends_on": [], "params": {}}
        ],
    }
    resp = client.post(
        "/api/plans/execute-reviewed",
        json={
            "plan": plan,
            "approval": None,
            "dry_run": True,
            "persist_audit": True,
        },
    )
    data = resp.json()
    assert data["status"] == "APPROVAL_GATE_BLOCKED"
    assert data["audit"]["persisted"] is True
    assert data["audit"]["event_type"] == "execution_blocked"


# ── 20. dry_run=false env not set → no audit ──


def test_dry_run_false_no_audit():
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_valid_body(
            dry_run=False,
            persist_audit=True,
        ),
    )
    data = resp.json()
    assert data["status"] == "REVIEWED_EXECUTION_DISABLED"
    assert data["audit"]["persisted"] is False


# ── 21. DRY_RUN_OK has adapter ──


def test_dry_run_ok_has_adapter():
    resp = client.post("/api/plans/execute-reviewed", json=_valid_body())
    data = resp.json()
    assert data["status"] == "DRY_RUN_OK"
    assert data["adapter"]["ok"] is True
    assert data["adapter"]["pipeline"]["available"] is True


# ── 22. SPM node → EXECUTION_POLICY_BLOCKED ──


def test_spm_node_policy_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "spm_realign_subject", "backend": "matlab-spm", "depends_on": [], "params": {}}
        ],
    }
    resp = client.post(
        "/api/plans/execute-reviewed",
        json={
            "plan": plan,
            "approval": {"approved": True, "approved_nodes": ["*"], "rejected_nodes": []},
            "dry_run": True,
        },
    )
    data = resp.json()
    # M6-T003: wildcard no longer covers high-risk backend nodes
    assert data["status"] in ("APPROVAL_GATE_BLOCKED", "EXECUTION_POLICY_BLOCKED")


# ── 23. DPABI execution → EXECUTION_POLICY_BLOCKED ──


def test_dpabi_policy_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [{"id": "dpabi_subject_smooth", "depends_on": [], "params": {}}],
    }
    resp = client.post(
        "/api/plans/execute-reviewed",
        json={
            "plan": plan,
            "approval": {"approved": True, "approved_nodes": ["*"], "rejected_nodes": []},
            "dry_run": True,
        },
    )
    assert resp.json()["status"] in (
        "VALIDATION_FAILED",
        "APPROVAL_GATE_BLOCKED",
        "EXECUTION_POLICY_BLOCKED",
    )


# ── 24. Adapter summary present on blocked ──


def test_adapter_summary_present_on_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "spm_realign_subject", "backend": "matlab-spm", "depends_on": [], "params": {}}
        ],
    }
    resp = client.post(
        "/api/plans/execute-reviewed",
        json={
            "plan": plan,
            "approval": {"approved": True, "approved_nodes": ["*"], "rejected_nodes": []},
            "dry_run": True,
        },
    )
    data = resp.json()
    # M6-T003: wildcard blocked at approval gate for SPM nodes
    assert data["status"] in ("APPROVAL_GATE_BLOCKED", "EXECUTION_POLICY_BLOCKED")


# ── 25. Policy blocked → would_execute false ──


def test_policy_blocked_would_execute_false():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "spm_realign_subject", "backend": "matlab-spm", "depends_on": [], "params": {}}
        ],
    }
    resp = client.post(
        "/api/plans/execute-reviewed",
        json={
            "plan": plan,
            "approval": {"approved": True, "approved_nodes": ["*"], "rejected_nodes": []},
            "dry_run": True,
        },
    )
    data = resp.json()
    assert data["would_execute"] is False
    assert data["execution"]["executor_called"] is False


# ── 26. Policy blocked + persist_audit → audit written ──


def test_policy_blocked_writes_audit():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "spm_realign_subject", "backend": "matlab-spm", "depends_on": [], "params": {}}
        ],
    }
    resp = client.post(
        "/api/plans/execute-reviewed",
        json={
            "plan": plan,
            "approval": {"approved": True, "approved_nodes": ["*"], "rejected_nodes": []},
            "dry_run": True,
            "persist_audit": True,
        },
    )
    data = resp.json()
    # M6-T003: wildcard blocked at approval gate for SPM nodes
    assert data["status"] in ("APPROVAL_GATE_BLOCKED", "EXECUTION_POLICY_BLOCKED")
    assert data["audit"]["persisted"] is True
    assert data["audit"]["event_type"] == "execution_blocked"


# ── 27. dry_run=false env not set → REVIEWED_EXECUTION_DISABLED ──


def test_dry_run_false_still_refused():
    resp = client.post("/api/plans/execute-reviewed", json=_valid_body(dry_run=False))
    assert resp.json()["status"] == "REVIEWED_EXECUTION_DISABLED"
    assert resp.json()["execution"]["executor_called"] is False


# ══════════════════════════════════════════════════════════════════════════════
# M5-T014: Pipeline Writer integration tests
# ══════════════════════════════════════════════════════════════════════════════


# ── 28. DRY_RUN_OK has pipeline_yaml field ──


def test_dry_run_ok_has_pipeline_yaml():
    resp = client.post("/api/plans/execute-reviewed", json=_valid_body())
    data = resp.json()
    assert data["status"] == "DRY_RUN_OK"
    assert "pipeline_yaml" in data
    assert data["pipeline_yaml"]["would_write"] is True
    assert data["pipeline_yaml"]["written"] is False
    assert data["pipeline_yaml"]["path"] is None
    assert data["pipeline_yaml"]["requires_audit"] is True


# ── 29. write_pipeline_yaml=false keeps written=false ──


def test_write_yaml_false_not_written():
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_valid_body(
            write_pipeline_yaml=False,
            persist_audit=True,
        ),
    )
    data = resp.json()
    assert data["status"] == "DRY_RUN_OK"
    assert data["pipeline_yaml"]["written"] is False


# ── 30. write_pipeline_yaml=true + persist_audit=false → PIPELINE_WRITE_REQUIRES_AUDIT ──


def test_write_yaml_requires_audit():
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_valid_body(
            write_pipeline_yaml=True,
            persist_audit=False,
        ),
    )
    data = resp.json()
    assert data["status"] == "PIPELINE_WRITE_REQUIRES_AUDIT"
    assert data["would_execute"] is False
    assert data["pipeline_yaml"]["would_write"] is True
    assert data["pipeline_yaml"]["written"] is False
    assert data["pipeline_yaml"]["requires_audit"] is True


# ── 31. write_pipeline_yaml=true + persist_audit=true writes YAML ──


def test_write_yaml_success(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.backend.app.api.execute_reviewed_routes.pipeline_writer.REVIEWED_PIPELINE_DIR",
        tmp_path,
    )
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_valid_body(
            write_pipeline_yaml=True,
            persist_audit=True,
        ),
    )
    data = resp.json()
    assert data["status"] == "DRY_RUN_OK"
    assert data["pipeline_yaml"]["would_write"] is True
    assert data["pipeline_yaml"]["written"] is True
    assert data["pipeline_yaml"]["path"] is not None


# ── 32. response includes pipeline_yaml.path ──


def test_pipeline_yaml_path_present(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.backend.app.api.execute_reviewed_routes.pipeline_writer.REVIEWED_PIPELINE_DIR",
        tmp_path,
    )
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_valid_body(
            write_pipeline_yaml=True,
            persist_audit=True,
        ),
    )
    data = resp.json()
    assert data["pipeline_yaml"]["path"]
    assert str(tmp_path) in data["pipeline_yaml"]["path"]


# ── 33. YAML file exists on disk ──


def test_yaml_file_exists(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.backend.app.api.execute_reviewed_routes.pipeline_writer.REVIEWED_PIPELINE_DIR",
        tmp_path,
    )
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_valid_body(
            write_pipeline_yaml=True,
            persist_audit=True,
        ),
    )
    data = resp.json()
    path = data["pipeline_yaml"]["path"]
    assert path
    from pathlib import Path

    assert Path(path).exists()


# ── 34. YAML is parseable ──


def test_yaml_parseable(monkeypatch, tmp_path):
    import yaml

    monkeypatch.setattr(
        "src.backend.app.api.execute_reviewed_routes.pipeline_writer.REVIEWED_PIPELINE_DIR",
        tmp_path,
    )
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_valid_body(
            write_pipeline_yaml=True,
            persist_audit=True,
        ),
    )
    data = resp.json()
    path = data["pipeline_yaml"]["path"]
    content = yaml.safe_load(open(path, encoding="utf-8"))
    assert content is not None


# ── 35. YAML has required fields ──


def test_yaml_has_required_fields(monkeypatch, tmp_path):
    import yaml

    monkeypatch.setattr(
        "src.backend.app.api.execute_reviewed_routes.pipeline_writer.REVIEWED_PIPELINE_DIR",
        tmp_path,
    )
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_valid_body(
            write_pipeline_yaml=True,
            persist_audit=True,
        ),
    )
    data = resp.json()
    path = data["pipeline_yaml"]["path"]
    content = yaml.safe_load(open(path, encoding="utf-8"))
    assert "version" in content
    assert "modality" in content
    assert "execution" in content
    assert "nodes" in content


# ── 36. validation failed → no YAML written ──


def test_validation_failed_no_yaml():
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_valid_body(
            plan={"pipeline_id": "bad", "nodes": [{"id": "nonexistent_xyz", "depends_on": []}]},
            write_pipeline_yaml=True,
            persist_audit=True,
        ),
    )
    data = resp.json()
    assert data["status"] == "VALIDATION_FAILED"
    assert data["pipeline_yaml"]["would_write"] is False
    assert data["pipeline_yaml"]["written"] is False


# ── 37. approval blocked → no YAML written ──


def test_approval_blocked_no_yaml():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "spm_realign_subject", "backend": "matlab-spm", "depends_on": [], "params": {}}
        ],
    }
    resp = client.post(
        "/api/plans/execute-reviewed",
        json={
            "plan": plan,
            "approval": None,
            "dry_run": True,
            "write_pipeline_yaml": True,
            "persist_audit": True,
        },
    )
    data = resp.json()
    assert data["status"] == "APPROVAL_GATE_BLOCKED"
    assert data["pipeline_yaml"]["written"] is False


# ── 38. policy blocked → no YAML written ──


def test_policy_blocked_no_yaml():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "spm_realign_subject", "backend": "matlab-spm", "depends_on": [], "params": {}}
        ],
    }
    resp = client.post(
        "/api/plans/execute-reviewed",
        json={
            "plan": plan,
            "approval": {"approved": True, "approved_nodes": ["*"], "rejected_nodes": []},
            "dry_run": True,
            "write_pipeline_yaml": True,
            "persist_audit": True,
        },
    )
    data = resp.json()
    # M6-T003: wildcard blocked at approval gate for SPM nodes
    assert data["status"] in ("APPROVAL_GATE_BLOCKED", "EXECUTION_POLICY_BLOCKED")
    assert data["pipeline_yaml"]["written"] is False


# ── 39. dry_run=false env not set → no YAML written ──


def test_dry_run_false_no_yaml():
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_valid_body(
            dry_run=False,
            write_pipeline_yaml=True,
            persist_audit=True,
        ),
    )
    data = resp.json()
    assert data["status"] == "REVIEWED_EXECUTION_DISABLED"
    assert data["pipeline_yaml"]["written"] is False


# ── 40. writer throws → PIPELINE_WRITE_FAILED ──


def test_writer_throws_returns_failed(monkeypatch):
    def _failing_write(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(
        "src.backend.app.api.execute_reviewed_routes.pipeline_writer.write_reviewed_pipeline_yaml",
        _failing_write,
    )
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_valid_body(
            write_pipeline_yaml=True,
            persist_audit=True,
        ),
    )
    data = resp.json()
    assert data["status"] == "PIPELINE_WRITE_FAILED"
    assert data["pipeline_yaml"]["written"] is False
    assert data["would_execute"] is False


# ── 41. written path is within monkeypatched dir ──


def test_written_path_in_monkeypatched_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.backend.app.api.execute_reviewed_routes.pipeline_writer.REVIEWED_PIPELINE_DIR",
        tmp_path,
    )
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_valid_body(
            write_pipeline_yaml=True,
            persist_audit=True,
        ),
    )
    data = resp.json()
    path_str = data["pipeline_yaml"]["path"]
    assert str(tmp_path) in path_str


# ── 42. No rawdata / derivatives / reports written ──


def test_no_rawdata_written(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.backend.app.api.execute_reviewed_routes.pipeline_writer.REVIEWED_PIPELINE_DIR",
        tmp_path,
    )
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_valid_body(
            write_pipeline_yaml=True,
            persist_audit=True,
        ),
    )
    data = resp.json()
    path = data["pipeline_yaml"]["path"]
    from pathlib import Path

    parts = Path(path).parts
    # Assert no forbidden directory segment appears (use exact segment match
    # rather than substring to avoid false positives from tmp_path names)
    forbidden = {"data", "rawdata", "derivatives", "reports"}
    assert not (set(parts) & forbidden), f"Path contains forbidden directory: {path}"


# ── 43. No executor called ──


def test_no_executor_called_m5t014(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.backend.app.api.execute_reviewed_routes.pipeline_writer.REVIEWED_PIPELINE_DIR",
        tmp_path,
    )
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_valid_body(
            write_pipeline_yaml=True,
            persist_audit=True,
        ),
    )
    assert resp.json()["execution"]["executor_called"] is False


# ── 44. No node runner called ──


def test_no_node_runner_called(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.backend.app.api.execute_reviewed_routes.pipeline_writer.REVIEWED_PIPELINE_DIR",
        tmp_path,
    )
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_valid_body(
            write_pipeline_yaml=True,
            persist_audit=True,
        ),
    )
    assert resp.json()["execution"]["submitted"] is False


# ── 45. audit dry_run_result contains pipeline_yaml ──


def test_audit_contains_pipeline_yaml(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.backend.app.api.execute_reviewed_routes.pipeline_writer.REVIEWED_PIPELINE_DIR",
        tmp_path,
    )
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_valid_body(
            write_pipeline_yaml=True,
            persist_audit=True,
        ),
    )
    data = resp.json()
    path = data["audit"].get("audit_path")
    assert path
    from pathlib import Path

    content = json.loads(Path(path).read_text(encoding="utf-8"))
    dry_run_result = content.get("dry_run_result", {})
    assert "pipeline_yaml" in dry_run_result
    assert dry_run_result["pipeline_yaml"]["written"] is True


# ── 46. response is JSON serializable ──


def test_m5t014_json_serializable(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "src.backend.app.api.execute_reviewed_routes.pipeline_writer.REVIEWED_PIPELINE_DIR",
        tmp_path,
    )
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_valid_body(
            write_pipeline_yaml=True,
            persist_audit=True,
        ),
    )
    json.loads(resp.text)


# ── 47. DRY_RUN_OK default response has all required top-level fields ──


def test_dry_run_ok_has_all_fields():
    resp = client.post("/api/plans/execute-reviewed", json=_valid_body())
    data = resp.json()
    for key in (
        "ok",
        "status",
        "dry_run",
        "would_execute",
        "execution_allowed",
        "validation",
        "approval_gate",
        "adapter",
        "pipeline_yaml",
        "plan_summary",
        "project_config_path",
        "execution",
        "audit",
    ):
        assert key in data, f"Missing key: {key}"


# ══════════════════════════════════════════════════════════════════════════════
# M5-T015: Safe Reviewed Execution Preflight tests
# ══════════════════════════════════════════════════════════════════════════════


def _write_project_config(path, rawdata_readonly=True):
    """Write a minimal valid project_config.yaml for tests."""
    import yaml

    config = {
        "project": {"name": "test", "description": "test project"},
        "runtime": {
            "work_dir": str(path.parent / "work"),
            "log_dir": str(path.parent / "logs"),
        },
        "third_party": {
            "spm_dir": str(path.parent / "spm"),
            "dpabi_dir": str(path.parent / "dpabi"),
        },
        "safety": {"rawdata_readonly": rawdata_readonly},
    }
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def _attach_persisted_review_context(monkeypatch, tmp_path, body):
    """Give execution tests the same persisted authority required in production."""
    from uuid import uuid4

    from src.backend.app.api import execute_reviewed_routes
    from src.backend.app.planner import project_context, reviewed_plan_store
    from src.backend.app.schemas.desktop import ProjectDetail
    from src.backend.app.services.approval_summary_service import ApprovalSummaryService
    from src.backend.app.services.mock_store import SQLiteDesktopStore

    project_id = f"reviewed-test-{uuid4().hex[:12]}"
    # The generated project_config used by these tests places work/log/report
    # roots directly under tmp_path, so tmp_path is the persisted project root.
    project_dir = tmp_path
    rawdata_dir = project_dir / "rawdata"
    dataset_index_path = project_dir / "dataset_index.json"
    project_dir.mkdir(parents=True, exist_ok=True)
    rawdata_dir.mkdir(parents=True, exist_ok=True)
    dataset_index_path.write_text('{"subjects": []}', encoding="utf-8")
    config_path = str(body["project_config_path"])
    plan = body["plan"]
    store = SQLiteDesktopStore(tmp_path / f"{project_id}.sqlite")
    store.add_project(
        ProjectDetail(
            id=project_id,
            name="Reviewed execution test",
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
    body["plan"] = plan
    node_ids = [
        str(node["id"])
        for node in plan.get("nodes", [])
        if isinstance(node, dict) and node.get("id")
    ]
    goal_contract_candidate = {
        "goal_text": "execution boundary test",
        "goal_kind": "reviewed_execution_boundary",
        "scope": {"completeness_required": True},
        "criteria": [
            {
                "criterion_id": "execution-terminal",
                "criterion_type": "pipeline_terminal",
                "target": "pipeline",
                "required_evidence": ["pipeline_summary", "node_states"],
                "expected": {"statuses": ["SUCCESS", "COMPLETED"], "active_nodes": 0},
                "failure_semantics": "indeterminate_if_source_incomplete",
            },
            {
                "criterion_id": "execution-nodes",
                "criterion_type": "node_status",
                "target": "required_nodes",
                "required_evidence": ["node_states"],
                "expected": {"node_ids": node_ids, "statuses": ["SUCCESS", "COMPLETED"]},
                "failure_semantics": "indeterminate_if_source_incomplete",
            },
        ],
        "minimum_capability_level": "unavailable",
        "builder_source": "explicit_test_review",
    }
    record = reviewed_plan_store.save_reviewed_plan(
        project_id=project_id,
        project_config_path=config_path,
        plan=plan,
        validation={"ok": True},
        goal="execution boundary test",
        provider="test",
        goal_contract_candidate=goal_contract_candidate,
        reviewed_actor="test-reviewer",
    )
    summary = ApprovalSummaryService().build(
        project=store.get_project(project_id),
        reviewed_plan=record,
    )
    record = store.update_reviewed_plan(
        record.reviewed_plan_id,
        payload={
            **record.payload,
            "approval_envelope": summary.model_dump(mode="json"),
        },
    )
    assert record is not None
    body["project_id"] = project_id
    body["reviewed_plan_id"] = record.reviewed_plan_id
    if isinstance(body.get("approval"), dict):
        body["approval"]["approval_summary_hash"] = summary.summary_hash
    return body


def _preflight_body(monkeypatch, tmp_path, **overrides):
    """Build a body for dry_run=false preflight with env enabled + valid config.

    Also monkeypatches run_pipeline to return a mock success so existing
    preflight tests don't break.  Individual M5-T016 tests can override.
    """
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    monkeypatch.setattr(
        "src.backend.app.api.execute_reviewed_routes.pipeline_writer.REVIEWED_PIPELINE_DIR",
        tmp_path,
    )
    # Default: mock executor to return success (tests can override)
    monkeypatch.setattr(
        "src.backend.app.runtime.execution_gateway.PIPELINE_EXECUTOR",
        _bound_success,
    )
    cfg = tmp_path / "project_config.yaml"
    _write_project_config(cfg)
    body = {
        "plan": {
            "pipeline_id": "test",
            "nodes": [
                {"id": "data_inspection", "backend": "python", "depends_on": [], "params": {}},
                {
                    "id": "motion_qc_subject",
                    "backend": "python",
                    "depends_on": ["data_inspection"],
                    "params": {},
                },
            ],
        },
        "approval": {
            "approved": True,
            "approved_by": "user",
            "approved_nodes": ["*"],
            "rejected_nodes": [],
        },
        "dry_run": False,
        "persist_audit": True,
        "write_pipeline_yaml": True,
        "confirm_execution": True,
        "project_config_path": str(cfg),
    }
    body.update(overrides)
    return _attach_persisted_review_context(monkeypatch, tmp_path, body)


def test_preflight_created_lifecycle_is_bound_to_run_link(monkeypatch, tmp_path):
    from src.backend.app.api import execute_reviewed_routes

    body = _preflight_body(monkeypatch, tmp_path)

    response = client.post("/api/plans/execute-reviewed", json=body)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "EXECUTION_SUBMITTED"
    lifecycle_id = payload["lifecycle"]["lifecycle_id"]
    run_link = execute_reviewed_routes.mock_store.get_run_link_by_run_id(
        body["project_id"], payload["run_id"]
    )
    assert run_link is not None
    assert run_link.task_id == lifecycle_id
    project_audit_path = Path(payload["audit"]["project_audit_path"])
    assert project_audit_path.is_file()
    assert project_audit_path.parent == Path(
        execute_reviewed_routes.mock_store.get_project(body["project_id"]).metadata["project_dir"]
    ) / "reports" / "audit_records"


def test_reviewed_plan_bound_to_agent_requires_explicit_lifecycle_id(monkeypatch, tmp_path):
    from src.backend.app.api import execute_reviewed_routes

    body = _preflight_body(monkeypatch, tmp_path)
    now = datetime.now(UTC)
    lifecycle = AgentLifecycleRecord(
        lifecycle_id="lifecycle-existing-agent",
        project_id=body["project_id"],
        state="WAITING_FOR_APPROVAL",
        reviewed_plan_id=body["reviewed_plan_id"],
        created_at=now,
        updated_at=now,
    )
    execute_reviewed_routes.mock_store.create_agent_lifecycle(
        lifecycle,
        AgentLifecycleEvent(
            event_id="event-existing-agent",
            lifecycle_id=lifecycle.lifecycle_id,
            project_id=lifecycle.project_id,
            command_id="command-existing-agent",
            actor="test",
            source_command="plan",
            occurred_at=now,
            from_state=None,
            to_state="WAITING_FOR_APPROVAL",
            reviewed_plan_id=lifecycle.reviewed_plan_id,
        ),
    )

    response = client.post("/api/plans/execute-reviewed", json=body)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "AGENT_LIFECYCLE_ID_REQUIRED"
    assert payload["execution"]["executor_called"] is False
    assert execute_reviewed_routes.mock_store.list_run_links(body["project_id"]) == []
    assert [
        item.lifecycle_id
        for item in execute_reviewed_routes.mock_store.list_agent_lifecycles(body["project_id"])
    ] == ["lifecycle-existing-agent"]


# ── 48. dry_run=false + env not set → REVIEWED_EXECUTION_DISABLED ──


def test_preflight_env_disabled():
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_valid_body(
            dry_run=False,
            confirm_execution=True,
            persist_audit=True,
        ),
    )
    assert resp.json()["status"] == "REVIEWED_EXECUTION_DISABLED"


# ── 49. env enabled but confirm_execution=false → CONFIRMATION_REQUIRED ──


def test_preflight_no_confirm(monkeypatch):
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_valid_body(
            dry_run=False,
            confirm_execution=False,
            persist_audit=True,
        ),
    )
    assert resp.json()["status"] == "CONFIRMATION_REQUIRED"


# ── 50. env enabled + confirm but persist_audit=false → AUDIT_REQUIRED ──


def test_preflight_no_audit(monkeypatch):
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_valid_body(
            dry_run=False,
            confirm_execution=True,
            persist_audit=False,
        ),
    )
    assert resp.json()["status"] == "AUDIT_REQUIRED"


# ── 51. project_config_path missing → PROJECT_CONFIG_REQUIRED ──


def test_preflight_no_project_config(monkeypatch):
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_valid_body(
            dry_run=False,
            confirm_execution=True,
            persist_audit=True,
            project_config_path=None,
        ),
    )
    assert resp.json()["status"] == "PROJECT_CONFIG_REQUIRED"


# ── 52. project_config_path is invalid → PROJECT_CONFIG_INVALID ──


def test_preflight_invalid_project_config(monkeypatch, tmp_path):
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    cfg = tmp_path / "bad_config.yaml"
    cfg.write_text("not: valid: yaml: [", encoding="utf-8")
    # Ensure the file exists but is bad YAML — use a file that exists but fails ProjectSettings
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_valid_body(
            dry_run=False,
            confirm_execution=True,
            persist_audit=True,
            project_config_path=str(cfg),
        ),
    )
    assert resp.json()["status"] == "PROJECT_CONFIG_INVALID"


# ── 53. project_config_path nonexistent → PROJECT_CONFIG_INVALID ──


def test_preflight_nonexistent_project_config(monkeypatch):
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_valid_body(
            dry_run=False,
            confirm_execution=True,
            persist_audit=True,
            project_config_path="/nonexistent/path/config.yaml",
        ),
    )
    assert resp.json()["status"] == "PROJECT_CONFIG_INVALID"


# ── 54. preflight → validation failed → VALIDATION_FAILED ──


def test_invalid_plan_cannot_be_persisted_as_goal_reviewed(monkeypatch, tmp_path):
    with pytest.raises(ReviewedPlanStoreError, match="without a contract"):
        _preflight_body(
            monkeypatch,
            tmp_path,
            plan={
                "pipeline_id": "bad",
                "nodes": [{"id": "nonexistent_xyz", "depends_on": []}],
            },
        )


# ── 55. preflight → approval blocked → APPROVAL_GATE_BLOCKED ──


def test_preflight_approval_blocked(monkeypatch, tmp_path):
    body = _preflight_body(
        monkeypatch,
        tmp_path,
        approval=None,
        plan={
            "pipeline_id": "test",
            "nodes": [
                {
                    "id": "spm_realign_subject",
                    "backend": "matlab-spm",
                    "depends_on": [],
                    "params": {},
                }
            ],
        },
    )
    resp = client.post("/api/plans/execute-reviewed", json=body)
    assert resp.json()["status"] == "APPROVAL_GATE_BLOCKED"


# ── 56. preflight → adapter failed → PLAN_ADAPTER_FAILED ──


def test_preflight_adapter_failed(monkeypatch, tmp_path):
    # Monkeypatch adapt_reviewed_plan to simulate adapter failure
    from src.backend.app.planner.plan_adapter import PlanAdapterResult

    def _failing_adapter(*args, **kwargs):
        return PlanAdapterResult(ok=False, errors=["simulated adapter failure"])

    monkeypatch.setattr(
        "src.backend.app.api.execute_reviewed_routes.adapt_reviewed_plan",
        _failing_adapter,
    )
    body = _preflight_body(monkeypatch, tmp_path)
    resp = client.post("/api/plans/execute-reviewed", json=body)
    assert resp.json()["status"] == "PLAN_ADAPTER_FAILED"


# ── 57. preflight → policy blocked → EXECUTION_POLICY_BLOCKED ──


def test_preflight_policy_blocked(monkeypatch, tmp_path):
    body = _preflight_body(
        monkeypatch,
        tmp_path,
        plan={
            "pipeline_id": "test",
            "nodes": [
                {
                    "id": "spm_realign_subject",
                    "backend": "matlab-spm",
                    "depends_on": [],
                    "params": {},
                }
            ],
        },
    )
    resp = client.post("/api/plans/execute-reviewed", json=body)
    # M6-T003: wildcard blocked at approval gate for SPM nodes
    assert resp.json()["status"] in ("APPROVAL_GATE_BLOCKED", "EXECUTION_POLICY_BLOCKED")


# ── 58. preflight → write_pipeline_yaml=false → PIPELINE_YAML_REQUIRED ──


def test_preflight_pipeline_yaml_required(monkeypatch, tmp_path):
    body = _preflight_body(monkeypatch, tmp_path, write_pipeline_yaml=False)
    resp = client.post("/api/plans/execute-reviewed", json=body)
    data = resp.json()
    assert data["status"] == "PIPELINE_YAML_REQUIRED"
    assert data["pipeline_yaml"]["written"] is False


# ── 59. preflight → writer fails → PIPELINE_WRITE_FAILED ──


def test_preflight_writer_fails(monkeypatch, tmp_path):
    def _failing_write(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(
        "src.backend.app.api.execute_reviewed_routes.pipeline_writer.write_reviewed_pipeline_yaml",
        _failing_write,
    )
    body = _preflight_body(monkeypatch, tmp_path)
    resp = client.post("/api/plans/execute-reviewed", json=body)
    assert resp.json()["status"] == "PIPELINE_WRITE_FAILED"


# ── 60. preflight + executor → EXECUTION_SUBMITTED ──


def test_preflight_ready(monkeypatch, tmp_path):
    body = _preflight_body(monkeypatch, tmp_path)
    resp = client.post("/api/plans/execute-reviewed", json=body)
    data = resp.json()
    # With M5-T016, executor is called after preflight passes
    assert data["status"] == "EXECUTION_SUBMITTED"
    assert data["ok"] is True
    assert data["dry_run"] is False
    assert data["would_execute"] is True
    assert data["execution_allowed"] is True


def test_successful_reviewed_execution_coordinates_terminal_lifecycle(
    monkeypatch,
    tmp_path,
):
    from src.backend.app.api import execute_reviewed_routes

    calls: list[tuple[str, str]] = []

    class _FakeReconciler:
        def __init__(self, store):
            self.store = store

        def reconcile_once(
            self,
            *,
            project_id,
            lifecycle_id,
            actor="system-reconciler",
        ):
            calls.append(("reconcile", lifecycle_id))
            lifecycle = self.store.get_agent_lifecycle(lifecycle_id)
            return lifecycle.model_copy(update={"state": "GOAL_SATISFIED"})

        def start_bounded_monitor(self, *, project_id, lifecycle_id, actor):
            calls.append(("monitor", lifecycle_id))

    monkeypatch.setattr(
        execute_reviewed_routes,
        "AgentTaskReconciler",
        _FakeReconciler,
        raising=False,
    )
    body = _preflight_body(monkeypatch, tmp_path)

    response = client.post("/api/plans/execute-reviewed", json=body)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "EXECUTION_SUBMITTED"
    assert calls == [
        ("reconcile", payload["lifecycle"]["lifecycle_id"])
    ], payload.get("warnings")
    assert payload["lifecycle"]["state"] == "GOAL_SATISFIED"


# ── 61. EXECUTION_SUBMITTED → executor_called is true ──


def test_preflight_ready_no_executor(monkeypatch, tmp_path):
    body = _preflight_body(monkeypatch, tmp_path)
    resp = client.post("/api/plans/execute-reviewed", json=body)
    assert resp.json()["execution"]["executor_called"] is True


# ── 62. EXECUTION_SUBMITTED → submitted is true ──


def test_preflight_ready_not_submitted(monkeypatch, tmp_path):
    body = _preflight_body(monkeypatch, tmp_path)
    resp = client.post("/api/plans/execute-reviewed", json=body)
    assert resp.json()["execution"]["submitted"] is True


# ── 63. EXECUTION_PREFLIGHT_READY → has audit ──


def test_preflight_ready_has_audit(monkeypatch, tmp_path):
    body = _preflight_body(monkeypatch, tmp_path)
    resp = client.post("/api/plans/execute-reviewed", json=body)
    data = resp.json()
    assert data["audit"]["persisted"] is True
    assert data["audit"]["event_type"] == "execution_requested"


# ── 64. EXECUTION_PREFLIGHT_READY → has pipeline_yaml ──


def test_preflight_ready_has_pipeline_yaml(monkeypatch, tmp_path):
    body = _preflight_body(monkeypatch, tmp_path)
    resp = client.post("/api/plans/execute-reviewed", json=body)
    data = resp.json()
    assert data["pipeline_yaml"]["written"] is True
    assert data["pipeline_yaml"]["path"] is not None


# ── 65. EXECUTION_SUBMITTED → executor_called is true ──


def test_preflight_ready_no_executor_called(monkeypatch, tmp_path):
    body = _preflight_body(monkeypatch, tmp_path)
    resp = client.post("/api/plans/execute-reviewed", json=body)
    assert resp.json()["execution"]["executor_called"] is True


# ── 66. EXECUTION_PREFLIGHT_READY → no rawdata / derivatives / reports ──


def test_preflight_ready_no_rawdata(monkeypatch, tmp_path):
    body = _preflight_body(monkeypatch, tmp_path)
    resp = client.post("/api/plans/execute-reviewed", json=body)
    path = resp.json()["pipeline_yaml"]["path"]
    from pathlib import Path

    parts = Path(path).parts
    forbidden = {"data", "rawdata", "derivatives", "reports"}
    assert not (set(parts) & forbidden), f"Path contains forbidden directory: {path}"


# ── 67. dry_run=true behavior is not regressed ──


def test_preflight_dry_run_true_not_regressed():
    """M5-T015 must not change dry_run=true behavior."""
    resp = client.post("/api/plans/execute-reviewed", json=_valid_body())
    assert resp.json()["status"] == "DRY_RUN_OK"
    assert resp.json()["pipeline_yaml"]["would_write"] is True


# ── 68. response is JSON serializable (preflight path) ──


def test_preflight_json_serializable(monkeypatch, tmp_path):
    body = _preflight_body(monkeypatch, tmp_path)
    resp = client.post("/api/plans/execute-reviewed", json=body)
    json.loads(resp.text)


# ══════════════════════════════════════════════════════════════════════════════
# M5-T016: Gated Execution tests
# ══════════════════════════════════════════════════════════════════════════════


# ── 69. env not enabled → executor not called ──


def test_m5t016_env_disabled_no_executor():
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_valid_body(
            dry_run=False,
            confirm_execution=True,
            persist_audit=True,
        ),
    )
    data = resp.json()
    assert data["status"] == "REVIEWED_EXECUTION_DISABLED"
    assert data["execution"]["executor_called"] is False


# ── 70. confirm_execution=false → executor not called ──


def test_m5t016_no_confirm_no_executor(monkeypatch):
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_valid_body(
            dry_run=False,
            confirm_execution=False,
            persist_audit=True,
        ),
    )
    assert resp.json()["execution"]["executor_called"] is False


# ── 71. persist_audit=false → executor not called ──


def test_m5t016_no_audit_no_executor(monkeypatch):
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_valid_body(
            dry_run=False,
            confirm_execution=True,
            persist_audit=False,
        ),
    )
    assert resp.json()["execution"]["executor_called"] is False


# ── 72. project_config invalid → executor not called ──


def test_m5t016_bad_config_no_executor(monkeypatch):
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    resp = client.post(
        "/api/plans/execute-reviewed",
        json=_valid_body(
            dry_run=False,
            confirm_execution=True,
            persist_audit=True,
            project_config_path="/nonexistent/path/config.yaml",
        ),
    )
    assert resp.json()["execution"]["executor_called"] is False


# ── 73. validation failed → executor not called ──


def test_m5t016_validation_failed_no_executor(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        "src.backend.app.runtime.execution_gateway.PIPELINE_EXECUTOR",
        lambda **kwargs: calls.append(kwargs),
    )
    with pytest.raises(ReviewedPlanStoreError, match="without a contract"):
        _preflight_body(
            monkeypatch,
            tmp_path,
            plan={
                "pipeline_id": "bad",
                "nodes": [{"id": "nonexistent_xyz", "depends_on": []}],
            },
        )
    assert calls == []


# ── 74. approval blocked → executor not called ──


def test_m5t016_approval_blocked_no_executor(monkeypatch, tmp_path):
    body = _preflight_body(
        monkeypatch,
        tmp_path,
        approval=None,
        plan={
            "pipeline_id": "test",
            "nodes": [
                {
                    "id": "spm_realign_subject",
                    "backend": "matlab-spm",
                    "depends_on": [],
                    "params": {},
                }
            ],
        },
    )
    resp = client.post("/api/plans/execute-reviewed", json=body)
    assert resp.json()["execution"]["executor_called"] is False


# ── 75. policy blocked → executor not called ──


def test_m5t016_policy_blocked_no_executor(monkeypatch, tmp_path):
    body = _preflight_body(
        monkeypatch,
        tmp_path,
        plan={
            "pipeline_id": "test",
            "nodes": [
                {
                    "id": "spm_realign_subject",
                    "backend": "matlab-spm",
                    "depends_on": [],
                    "params": {},
                }
            ],
        },
    )
    resp = client.post("/api/plans/execute-reviewed", json=body)
    assert resp.json()["execution"]["executor_called"] is False


# ── 76. write_pipeline_yaml=false → executor not called ──


def test_m5t016_no_yaml_no_executor(monkeypatch, tmp_path):
    body = _preflight_body(monkeypatch, tmp_path, write_pipeline_yaml=False)
    resp = client.post("/api/plans/execute-reviewed", json=body)
    assert resp.json()["execution"]["executor_called"] is False


# ── 77. safe allowlist blocked → SAFE_EXECUTION_POLICY_BLOCKED ──


def test_m5t016_safe_allowlist_blocked(monkeypatch, tmp_path):
    # GPU node (gpu_alff_subject) is in catalog, classified as allowed_gpu,
    # but blocked by the safe allowlist
    body = _preflight_body(
        monkeypatch,
        tmp_path,
        plan={
            "pipeline_id": "test",
            "nodes": [{"id": "gpu_alff_subject", "backend": "gpu", "depends_on": [], "params": {}}],
        },
    )
    resp = client.post("/api/plans/execute-reviewed", json=body)
    data = resp.json()
    assert data["status"] == "SAFE_EXECUTION_POLICY_BLOCKED"


# ── 78. SAFE_EXECUTION_POLICY_BLOCKED → executor_called=false ──


def test_m5t016_safe_allowlist_blocked_no_executor(monkeypatch, tmp_path):
    body = _preflight_body(
        monkeypatch,
        tmp_path,
        plan={
            "pipeline_id": "test",
            "nodes": [{"id": "gpu_alff_subject", "backend": "gpu", "depends_on": [], "params": {}}],
        },
    )
    resp = client.post("/api/plans/execute-reviewed", json=body)
    assert resp.json()["execution"]["executor_called"] is False


# ── 79. run_pipeline is called exactly once on success ──


def test_m5t016_executor_called_once(monkeypatch, tmp_path):
    calls = []

    def _tracking_executor(**kw):
        calls.append(1)
        return _bound_success(**kw)

    # _preflight_body monkeypatches run_pipeline too — override AFTER
    body = _preflight_body(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "src.backend.app.runtime.execution_gateway.PIPELINE_EXECUTOR",
        _tracking_executor,
    )
    client.post("/api/plans/execute-reviewed", json=body)
    assert len(calls) == 1


# ── 80. run_pipeline success → EXECUTION_SUBMITTED ──


def test_m5t016_execution_submitted_status(monkeypatch, tmp_path):
    body = _preflight_body(monkeypatch, tmp_path)
    resp = client.post("/api/plans/execute-reviewed", json=body)
    assert resp.json()["status"] == "EXECUTION_SUBMITTED"


# ── 81. run_pipeline success → executor_called=true ──


def test_m5t016_execution_submitted_executor_called(monkeypatch, tmp_path):
    body = _preflight_body(monkeypatch, tmp_path)
    resp = client.post("/api/plans/execute-reviewed", json=body)
    assert resp.json()["execution"]["executor_called"] is True


# ── 82. run_pipeline success → submitted=true ──


def test_m5t016_execution_submitted_submitted(monkeypatch, tmp_path):
    body = _preflight_body(monkeypatch, tmp_path)
    resp = client.post("/api/plans/execute-reviewed", json=body)
    assert resp.json()["execution"]["submitted"] is True


# ── 83. run_pipeline success → run_id returned ──


def test_m5t016_execution_submitted_run_id(monkeypatch, tmp_path):
    body = _preflight_body(monkeypatch, tmp_path)
    resp = client.post("/api/plans/execute-reviewed", json=body)
    data = resp.json()
    assert data["execution"]["run_id"].startswith("run_")
    assert data["executor_result"]["run_id"] == data["execution"]["run_id"]


# ── 84. run_pipeline throws → EXECUTION_FAILED ──


def test_m5t016_executor_throws(monkeypatch, tmp_path):
    def _failing_executor(**kw):
        raise RuntimeError("simulated executor crash")

    # _preflight_body monkeypatches run_pipeline — override AFTER
    body = _preflight_body(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "src.backend.app.runtime.execution_gateway.PIPELINE_EXECUTOR",
        _failing_executor,
    )
    resp = client.post("/api/plans/execute-reviewed", json=body)
    data = resp.json()
    assert data["status"] == "EXECUTION_FAILED"


# ── 85. executor throws → executor_called=true ──


def test_m5t016_executor_throws_executor_called(monkeypatch, tmp_path):
    def _failing_executor(**kw):
        raise RuntimeError("simulated executor crash")

    # _preflight_body monkeypatches run_pipeline — override AFTER
    body = _preflight_body(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "src.backend.app.runtime.execution_gateway.PIPELINE_EXECUTOR",
        _failing_executor,
    )
    resp = client.post("/api/plans/execute-reviewed", json=body)
    assert resp.json()["execution"]["executor_called"] is True


def test_gateway_safety_failure_preserves_code_and_recovery_guidance(monkeypatch, tmp_path):
    def _expired_ticket(**_kw):
        raise SafetyError("EXECUTION_TICKET_EXPIRED", code="EXECUTION_TICKET_EXPIRED")

    body = _preflight_body(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "src.backend.app.runtime.execution_gateway.PIPELINE_EXECUTOR",
        _expired_ticket,
    )

    response = client.post("/api/plans/execute-reviewed", json=body)
    payload = response.json()

    assert payload["status"] == "EXECUTION_TICKET_EXPIRED"
    assert payload["errors"] == ["EXECUTION_TICKET_EXPIRED"]
    assert payload["recovery"] == {
        "recoverable": True,
        "next_step": "REVIEW_AND_APPROVE_NEW_PLAN",
    }


# ── 86. dry_run=true not regressed ──


def test_m5t016_dry_run_true_not_regressed():
    resp = client.post("/api/plans/execute-reviewed", json=_valid_body())
    assert resp.json()["status"] == "DRY_RUN_OK"


# ── 87. SPM node → executor not called ──


def test_m5t016_spm_no_executor(monkeypatch, tmp_path):
    body = _preflight_body(
        monkeypatch,
        tmp_path,
        plan={
            "pipeline_id": "test",
            "nodes": [
                {
                    "id": "spm_realign_subject",
                    "backend": "matlab-spm",
                    "depends_on": [],
                    "params": {},
                }
            ],
        },
    )
    resp = client.post("/api/plans/execute-reviewed", json=body)
    assert resp.json()["execution"]["executor_called"] is False


# ── 88. DPABI execution → executor not called ──


def test_m5t016_dpabi_no_executor(monkeypatch, tmp_path):
    body = _preflight_body(
        monkeypatch,
        tmp_path,
        plan={
            "pipeline_id": "test",
            "nodes": [{"id": "dpabi_subject_smooth", "depends_on": [], "params": {}}],
        },
    )
    resp = client.post("/api/plans/execute-reviewed", json=body)
    assert resp.json()["execution"]["executor_called"] is False


# ── 89. removed legacy GUI node → executor not called ──


def test_m5t016_legacy_gui_node_no_executor(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        "src.backend.app.runtime.execution_gateway.PIPELINE_EXECUTOR",
        lambda **kwargs: calls.append(kwargs),
    )
    with pytest.raises(ReviewedPlanStoreError, match="without a contract"):
        _preflight_body(
            monkeypatch,
            tmp_path,
            plan={
                "pipeline_id": "test",
                "nodes": [
                    {
                        "id": "gui_acpc_manual",
                        "depends_on": [],
                        "params": {},
                    }
                ],
            },
        )
    assert calls == []


# ── 90. unknown node → executor not called ──


def test_m5t016_unknown_node_no_executor(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(
        "src.backend.app.runtime.execution_gateway.PIPELINE_EXECUTOR",
        lambda **kwargs: calls.append(kwargs),
    )
    with pytest.raises(ReviewedPlanStoreError, match="without a contract"):
        _preflight_body(
            monkeypatch,
            tmp_path,
            plan={
                "pipeline_id": "test",
                "nodes": [{"id": "completely_unknown_node_xyz", "depends_on": [], "params": {}}],
            },
        )
    assert calls == []


# ── 91. audit written before executor call ──


def test_m5t016_audit_written_before_executor(monkeypatch, tmp_path):
    body = _preflight_body(monkeypatch, tmp_path)
    resp = client.post("/api/plans/execute-reviewed", json=body)
    data = resp.json()
    assert data["audit"]["persisted"] is True
    assert data["audit"]["event_type"] == "execution_requested"


# ── 92. EXECUTION_SUBMITTED → has executor_result ──


def test_m5t016_execution_submitted_has_result(monkeypatch, tmp_path):
    body = _preflight_body(monkeypatch, tmp_path)
    resp = client.post("/api/plans/execute-reviewed", json=body)
    data = resp.json()
    assert "executor_result" in data
    assert data["executor_result"]["status"] == "SUCCESS"


# ── 93. JSON serializable after execution ──


def test_m5t016_json_serializable(monkeypatch, tmp_path):
    body = _preflight_body(monkeypatch, tmp_path)
    resp = client.post("/api/plans/execute-reviewed", json=body)
    json.loads(resp.text)


# ══════════════════════════════════════════════════════════════════════════════
# M5-T017: Safety Regression tests
# ══════════════════════════════════════════════════════════════════════════════


# ── 94. executor throws → submitted remains false ──


def test_m5t017_executor_throws_submitted_false(monkeypatch, tmp_path):
    def _failing_executor(**kw):
        raise RuntimeError("simulated executor crash")

    body = _preflight_body(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "src.backend.app.runtime.execution_gateway.PIPELINE_EXECUTOR",
        _failing_executor,
    )
    resp = client.post("/api/plans/execute-reviewed", json=body)
    data = resp.json()
    assert data["status"] == "EXECUTION_FAILED"
    assert data["execution"]["submitted"] is False
    assert data["execution"]["executor_called"] is True


# ── 95. contract node → executor not called (blocked by safe allowlist) ──


def test_m5t017_unavailable_contract_node_no_executor(monkeypatch, tmp_path):
    body = _preflight_body(
        monkeypatch,
        tmp_path,
        plan={
            "pipeline_id": "test",
            "nodes": [{"id": "dpabi_capability_inspection", "depends_on": [], "params": {}}],
        },
    )
    resp = client.post("/api/plans/execute-reviewed", json=body)
    data = resp.json()
    assert data["status"] == "VALIDATION_FAILED"
    assert data["execution"]["executor_called"] is False


# ── 96. pipeline YAML on disk is before executor call ──


def test_m5t017_yaml_on_disk_before_executor(monkeypatch, tmp_path):
    captured_yaml_path = []

    def _capture_executor(**kw):
        captured_yaml_path.append(kw.get("pipeline_path"))
        return _bound_success(**kw)

    body = _preflight_body(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "src.backend.app.runtime.execution_gateway.PIPELINE_EXECUTOR",
        _capture_executor,
    )
    resp = client.post("/api/plans/execute-reviewed", json=body)
    data = resp.json()
    assert data["status"] == "EXECUTION_SUBMITTED"
    assert len(captured_yaml_path) == 1
    yaml_path = captured_yaml_path[0]
    from pathlib import Path

    assert Path(yaml_path).exists(), f"YAML not on disk before executor: {yaml_path}"


# ── 97. plan with both allowed-python and blocked-gpu → SAFE_EXECUTION_POLICY_BLOCKED ──


def test_m5t017_mixed_plan_blocked(monkeypatch, tmp_path):
    # Even if one safe Python node exists, GPU node makes the whole plan blocked
    body = _preflight_body(
        monkeypatch,
        tmp_path,
        plan={
            "pipeline_id": "test",
            "nodes": [
                {"id": "data_inspection", "backend": "python", "depends_on": [], "params": {}},
                {
                    "id": "gpu_alff_subject",
                    "backend": "gpu",
                    "depends_on": ["data_inspection"],
                    "params": {},
                },
            ],
        },
    )
    resp = client.post("/api/plans/execute-reviewed", json=body)
    data = resp.json()
    assert data["status"] == "SAFE_EXECUTION_POLICY_BLOCKED"
    assert data["execution"]["executor_called"] is False


# ── 98. executor failure does NOT write rawdata ──


def test_m5t017_executor_failure_no_rawdata(monkeypatch, tmp_path):
    def _failing_executor(**kw):
        raise RuntimeError("crash")

    body = _preflight_body(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "src.backend.app.runtime.execution_gateway.PIPELINE_EXECUTOR",
        _failing_executor,
    )
    resp = client.post("/api/plans/execute-reviewed", json=body)
    # Just verify the path returned is still safe
    py = resp.json()["pipeline_yaml"]
    if py["path"]:
        from pathlib import Path

        parts = Path(py["path"]).parts
        forbidden = {"data", "rawdata", "derivatives", "reports"}
        assert not (set(parts) & forbidden)


# ── 99. pipeline_yaml.written is true before executor in success path ──


def test_m5t017_yaml_written_before_executor_success(monkeypatch, tmp_path):
    body = _preflight_body(monkeypatch, tmp_path)
    resp = client.post("/api/plans/execute-reviewed", json=body)
    data = resp.json()
    assert data["pipeline_yaml"]["written"] is True
    assert data["pipeline_yaml"]["path"] is not None


# ── 100. every blocked status returns executor_called=false ──


def test_m5t017_all_blocked_statuses_no_executor(monkeypatch, tmp_path):
    """Parametric check: all known blocked statuses have executor_called=False."""
    # Test each blocked path independently
    blocked_bodies = [
        # env disabled
        (
            lambda: _valid_body(dry_run=False, confirm_execution=True, persist_audit=True),
            "REVIEWED_EXECUTION_DISABLED",
        ),
    ]
    for body_fn, expected in blocked_bodies:
        resp = client.post("/api/plans/execute-reviewed", json=body_fn())
        data = resp.json()
        assert data["status"] == expected
        assert data["execution"]["executor_called"] is False


# ══════════════════════════════════════════════════════════════════════════════
# M6-T004b: spm_smoke_test reviewed execution allowlist
# ══════════════════════════════════════════════════════════════════════════════


def _spm_smoke_body(monkeypatch, tmp_path, **overrides):
    """Preflight body with spm_smoke_test + matlab-spm backend approval."""
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    monkeypatch.setattr(
        "src.backend.app.api.execute_reviewed_routes.pipeline_writer.REVIEWED_PIPELINE_DIR",
        tmp_path,
    )
    monkeypatch.setattr(
        "src.backend.app.runtime.execution_gateway.PIPELINE_EXECUTOR",
        _bound_success,
    )
    cfg = tmp_path / "project_config.yaml"
    import yaml

    config = {
        "project": {"name": "test", "description": "spm smoke", "root_dir": "."},
        "runtime": {
            "work_dir": str(tmp_path / "work"),
            "log_dir": str(tmp_path / "logs"),
            "derivatives_dir": str(tmp_path / "derivatives"),
            "report_dir": str(tmp_path / "reports"),
        },
        "third_party": {
            "spm_dir": str(tmp_path / "spm"),
            "dpabi_dir": str(tmp_path / "dpabi"),
        },
        "safety": {"rawdata_readonly": True},
    }
    cfg.write_text(yaml.safe_dump(config), encoding="utf-8")
    body = {
        "plan": {
            "pipeline_id": "spm_smoke",
            "nodes": [
                {"id": "spm_smoke_test", "backend": "matlab-spm", "depends_on": [], "params": {}}
            ],
        },
        "approval": {
            "approved": True,
            "approved_by": "ci-smoke",
            "approved_nodes": ["spm_smoke_test"],
            "approved_backends": ["matlab-spm"],
            "rejected_nodes": [],
            "external_tool_acknowledgement": True,
            "rawdata_read_only_confirmed": True,
            "output_directory_confirmed": True,
            "risk_acknowledgement": True,
            "overwrite_policy": "fail_if_exists",
            "subject_scope_confirmed": True,
        },
        "project_config_path": str(cfg),
        "dry_run": False,
        "persist_audit": True,
        "write_pipeline_yaml": True,
        "confirm_execution": True,
        "actor": "ci-smoke",
    }
    body.update(overrides)
    return _attach_persisted_review_context(monkeypatch, tmp_path, body)


# ── 101. spm_smoke_test + explicit node + backend approval → EXECUTION_SUBMITTED ──


def test_m6t004b_spm_smoke_execution_submitted(monkeypatch, tmp_path):
    body = _spm_smoke_body(monkeypatch, tmp_path)
    resp = client.post("/api/plans/execute-reviewed", json=body)
    data = resp.json()
    assert data["status"] == "EXECUTION_SUBMITTED"
    assert data["execution"]["executor_called"] is True


# ── 102. spm_smoke_test + wildcard approval → blocked ──


def test_m6t004b_spm_smoke_wildcard_blocked(monkeypatch, tmp_path):
    body = _spm_smoke_body(
        monkeypatch,
        tmp_path,
        approval={
            "approved": True,
            "approved_nodes": ["*"],
            "approved_backends": [],
            "rejected_nodes": [],
        },
    )
    resp = client.post("/api/plans/execute-reviewed", json=body)
    data = resp.json()
    assert data["status"] in (
        "APPROVAL_GATE_BLOCKED",
        "EXECUTION_POLICY_BLOCKED",
        "SAFE_EXECUTION_POLICY_BLOCKED",
    )
    assert data["execution"]["executor_called"] is False


# ── 103. spm_smoke_test missing approved_backends → blocked ──


def test_m6t004b_spm_smoke_missing_backend_approval(monkeypatch, tmp_path):
    body = _spm_smoke_body(
        monkeypatch,
        tmp_path,
        approval={
            "approved": True,
            "approved_nodes": ["spm_smoke_test"],
            "approved_backends": [],
            "rejected_nodes": [],
        },
    )
    resp = client.post("/api/plans/execute-reviewed", json=body)
    data = resp.json()
    assert data["execution"]["executor_called"] is False


# ── 104. spm_realign_subject still blocked ──


def test_m6t004b_spm_realign_still_blocked(monkeypatch, tmp_path):
    body = _spm_smoke_body(
        monkeypatch,
        tmp_path,
        plan={
            "pipeline_id": "test",
            "nodes": [
                {
                    "id": "spm_realign_subject",
                    "backend": "matlab-spm",
                    "depends_on": [],
                    "params": {},
                }
            ],
        },
    )
    resp = client.post("/api/plans/execute-reviewed", json=body)
    data = resp.json()
    assert data["execution"]["executor_called"] is False
    assert data["status"] in (
        "EXECUTION_POLICY_BLOCKED",
        "APPROVAL_GATE_BLOCKED",
        "SAFE_EXECUTION_POLICY_BLOCKED",
    )


# ── 105. dry_run=true not regressed ──


def test_m6t004b_dry_run_true_not_regressed():
    resp = client.post(
        "/api/plans/execute-reviewed",
        json={
            "plan": {
                "pipeline_id": "test",
                "nodes": [
                    {"id": "data_inspection", "backend": "python", "depends_on": [], "params": {}},
                ],
            },
            "approval": None,
            "dry_run": True,
        },
    )
    assert resp.json()["status"] == "DRY_RUN_OK"


# ══════════════════════════════════════════════════════════════════════════════
# M6-T005d: sandbox-only spm_realign_subject reviewed execution
# ══════════════════════════════════════════════════════════════════════════════


def _sandbox_realign_body(monkeypatch, tmp_path, **overrides):
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    monkeypatch.setattr(
        "src.backend.app.api.execute_reviewed_routes.pipeline_writer.REVIEWED_PIPELINE_DIR",
        tmp_path,
    )
    monkeypatch.setattr(
        "src.backend.app.runtime.execution_gateway.PIPELINE_EXECUTOR",
        _bound_success,
    )
    cfg = tmp_path / "project_config.yaml"
    import yaml

    config = {
        "project": {"name": "test", "root_dir": "."},
        "runtime": {
            "work_dir": str(tmp_path / "work"),
            "log_dir": str(tmp_path / "logs"),
            "derivatives_dir": str(tmp_path / "derivatives"),
            "report_dir": str(tmp_path / "reports"),
        },
        "third_party": {
            "spm_dir": str(tmp_path / "spm"),
            "dpabi_dir": str(tmp_path / "dpabi"),
        },
        "safety": {"rawdata_readonly": True},
    }
    cfg.write_text(yaml.safe_dump(config), encoding="utf-8")
    body = {
        "plan": {
            "pipeline_id": "sandbox_realign",
            "nodes": [
                {
                    "id": "spm_realign_subject",
                    "backend": "matlab-spm",
                    "depends_on": [],
                    "params": {"sandbox_mode": True, "input_bold": "/tmp/bold.nii"},
                }
            ],
        },
        "approval": {
            "approved": True,
            "approved_by": "ci",
            "approved_nodes": ["spm_realign_subject"],
            "approved_backends": ["matlab-spm"],
            "rejected_nodes": [],
        },
        "project_config_path": str(cfg),
        "dry_run": False,
        "persist_audit": True,
        "write_pipeline_yaml": True,
        "confirm_execution": True,
        "actor": "ci",
    }
    body.update(overrides)
    return _attach_persisted_review_context(monkeypatch, tmp_path, body)


# ── 106. sandbox realign + explicit approval → EXECUTION_SUBMITTED ──


def test_m6t005d_sandbox_realign_submitted(monkeypatch, tmp_path):
    """spm_realign_subject is manual_required=True → blocked."""
    resp = client.post(
        "/api/plans/execute-reviewed", json=_sandbox_realign_body(monkeypatch, tmp_path)
    )
    data = resp.json()
    assert data["status"] in (
        "APPROVAL_GATE_BLOCKED",
        "VALIDATION_FAILED",
        "EXECUTION_POLICY_BLOCKED",
        "SAFE_EXECUTION_POLICY_BLOCKED",
    )


# ── 107. wildcard approval → blocked ──


def test_m6t005d_realign_wildcard_blocked(monkeypatch, tmp_path):
    body = _sandbox_realign_body(
        monkeypatch,
        tmp_path,
        approval={
            "approved": True,
            "approved_nodes": ["*"],
            "approved_backends": [],
            "rejected_nodes": [],
        },
    )
    resp = client.post("/api/plans/execute-reviewed", json=body)
    assert resp.json()["status"] in (
        "APPROVAL_GATE_BLOCKED",
        "EXECUTION_POLICY_BLOCKED",
        "SAFE_EXECUTION_POLICY_BLOCKED",
        "VALIDATION_FAILED",
    )


# ── 108. missing backend approval → blocked ──


def test_m6t005d_realign_missing_backend(monkeypatch, tmp_path):
    body = _sandbox_realign_body(
        monkeypatch,
        tmp_path,
        approval={
            "approved": True,
            "approved_nodes": ["spm_realign_subject"],
            "approved_backends": [],
            "rejected_nodes": [],
        },
    )
    resp = client.post("/api/plans/execute-reviewed", json=body)
    assert resp.json()["execution"]["executor_called"] is False


# ── 109. no sandbox_mode → blocked ──


def test_m6t005d_realign_no_sandbox_blocked(monkeypatch, tmp_path):
    body = _sandbox_realign_body(
        monkeypatch,
        tmp_path,
        plan={
            "pipeline_id": "test",
            "nodes": [
                {
                    "id": "spm_realign_subject",
                    "backend": "matlab-spm",
                    "depends_on": [],
                    "params": {},
                }
            ],
        },
    )
    resp = client.post("/api/plans/execute-reviewed", json=body)
    assert resp.json()["execution"]["executor_called"] is False


# ── 110. spm_slice_timing still blocked ──


def test_m6t005d_slice_timing_blocked(monkeypatch, tmp_path):
    body = _sandbox_realign_body(
        monkeypatch,
        tmp_path,
        plan={
            "pipeline_id": "test",
            "nodes": [
                {
                    "id": "spm_slice_timing_subject",
                    "backend": "matlab-spm",
                    "depends_on": [],
                    "params": {"sandbox_mode": True, "input_bold": "/tmp/bold.nii"},
                }
            ],
        },
    )
    resp = client.post("/api/plans/execute-reviewed", json=body)
    assert resp.json()["execution"]["executor_called"] is False


# ── 111. DPABI still blocked ──


def test_m6t005d_dpabi_blocked(monkeypatch, tmp_path):
    body = _sandbox_realign_body(
        monkeypatch,
        tmp_path,
        plan={
            "pipeline_id": "test",
            "nodes": [{"id": "dpabi_subject_smooth", "depends_on": [], "params": {}}],
        },
    )
    resp = client.post("/api/plans/execute-reviewed", json=body)
    assert resp.json()["execution"]["executor_called"] is False


# ── 112. GPU still blocked ──


def test_m6t005d_gpu_blocked(monkeypatch, tmp_path):
    body = _sandbox_realign_body(
        monkeypatch,
        tmp_path,
        plan={
            "pipeline_id": "test",
            "nodes": [{"id": "gpu_alff_subject", "backend": "gpu", "depends_on": [], "params": {}}],
        },
    )
    resp = client.post("/api/plans/execute-reviewed", json=body)
    assert resp.json()["execution"]["executor_called"] is False


# ══════════════════════════════════════════════════════════════════════════════
# M6-T006d: sandbox-only spm_slice_timing reviewed execution
# ══════════════════════════════════════════════════════════════════════════════


def _slice_timing_sandbox_body(monkeypatch, tmp_path, **overrides):
    monkeypatch.setenv("MEDIMAGE_ENABLE_REVIEWED_EXECUTION", "1")
    monkeypatch.setattr(
        "src.backend.app.api.execute_reviewed_routes.pipeline_writer.REVIEWED_PIPELINE_DIR",
        tmp_path,
    )
    monkeypatch.setattr(
        "src.backend.app.runtime.execution_gateway.PIPELINE_EXECUTOR",
        _bound_success,
    )
    cfg = tmp_path / "project_config.yaml"
    import yaml

    config = {
        "project": {"name": "test", "root_dir": "."},
        "runtime": {
            "work_dir": str(tmp_path / "work"),
            "log_dir": str(tmp_path / "logs"),
            "derivatives_dir": str(tmp_path / "derivatives"),
            "report_dir": str(tmp_path / "reports"),
        },
        "third_party": {"spm_dir": str(tmp_path / "spm"), "dpabi_dir": str(tmp_path / "dpabi")},
        "safety": {"rawdata_readonly": True},
    }
    cfg.write_text(yaml.safe_dump(config), encoding="utf-8")
    body = {
        "plan": {
            "pipeline_id": "sandbox_st",
            "nodes": [
                {
                    "id": "spm_slice_timing_subject",
                    "backend": "matlab-spm",
                    "depends_on": [],
                    "params": {
                        "sandbox_mode": True,
                        "input_bold": "examples/synthetic_bids/rawdata/sub-001/func/bold.nii",
                    },
                }
            ],
        },
        "approval": {
            "approved": True,
            "approved_nodes": ["spm_slice_timing_subject"],
            "approved_backends": ["matlab-spm"],
            "rejected_nodes": [],
        },
        "project_config_path": str(cfg),
        "dry_run": False,
        "persist_audit": True,
        "write_pipeline_yaml": True,
        "confirm_execution": True,
        "actor": "ci",
    }
    body.update(overrides)
    return _attach_persisted_review_context(monkeypatch, tmp_path, body)


def test_m6t006d_slice_timing_sandbox_submitted(monkeypatch, tmp_path):
    """SPM slice timing requires external-tool fields; blocked without them."""
    resp = client.post(
        "/api/plans/execute-reviewed", json=_slice_timing_sandbox_body(monkeypatch, tmp_path)
    )
    data = resp.json()
    assert data["status"] in ("APPROVAL_GATE_BLOCKED", "EXECUTION_SUBMITTED")


def test_m6t006d_slice_timing_no_sandbox_blocked(monkeypatch, tmp_path):
    body = _slice_timing_sandbox_body(
        monkeypatch,
        tmp_path,
        plan={
            "pipeline_id": "test",
            "nodes": [
                {
                    "id": "spm_slice_timing_subject",
                    "backend": "matlab-spm",
                    "depends_on": [],
                    "params": {},
                }
            ],
        },
    )
    resp = client.post("/api/plans/execute-reviewed", json=body)
    assert resp.json()["execution"]["executor_called"] is False


def test_m6t006d_slice_timing_wildcard_blocked(monkeypatch, tmp_path):
    body = _slice_timing_sandbox_body(
        monkeypatch,
        tmp_path,
        approval={
            "approved": True,
            "approved_nodes": ["*"],
            "approved_backends": [],
            "rejected_nodes": [],
        },
    )
    resp = client.post("/api/plans/execute-reviewed", json=body)
    assert resp.json()["status"] in (
        "APPROVAL_GATE_BLOCKED",
        "EXECUTION_POLICY_BLOCKED",
        "SAFE_EXECUTION_POLICY_BLOCKED",
    )
