"""Template input is controlled registration, never a scientific path answer."""
from pathlib import Path

import nibabel as nib
import numpy as np
import pytest

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.schemas.project_agent_settings import ScientificResourceInput
from src.backend.app.services.agent_planning_service import AgentPlanningService
from src.backend.app.services.project_agent_settings_service import ProjectAgentSettingsService
from tests.unit.test_project_agent_settings import _store


def _resource(tmp_path, *, valid=True):
    path = tmp_path / "project/resources/templates/template.nii.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    if valid:
        nib.save(nib.Nifti1Image(np.ones((3, 3, 3), dtype=np.float32), np.eye(4)), path)
    else:
        path.write_bytes(b"not NIfTI")
    return path


def test_empty_template_item_requires_registration_and_disallows_answer():
    items = AgentPlanningService._science_decision_items(
        {"nodes": [], "metadata": {"science_decisions": {"template_required": True, "template_candidates": []}}}, {}, {},
    )
    template = items[0]
    assert template.readiness == "input_required"
    assert template.allowed_actions == ("register_template",)
    assert template.options == ()


def test_template_registration_checks_actual_nifti_format_and_space(tmp_path):
    store = _store(tmp_path)
    path = _resource(tmp_path, valid=False)
    resource = ScientificResourceInput(name="fixture", path=str(path), license="CC0", space="MNI152")
    with pytest.raises(SafetyError, match="AGENT_TEMPLATE_RESOURCE_INVALID"):
        ProjectAgentSettingsService(store).verify_template(project_id="project-1", resource=resource)
    _resource(tmp_path)
    verified = ProjectAgentSettingsService(store).verify_template(project_id="project-1", resource=resource)
    assert verified["space"] == "MNI152"
    assert verified["checksum"].startswith("sha256:")
    assert path.exists()


def _planning_fixture(tmp_path):
    from src.backend.app.services.agent_orchestrator import AgentOrchestrator
    from src.backend.app.schemas.agent_lifecycle import PendingDecisionBatch
    from src.backend.app.services.agent_evidence_service import AgentEvidenceService
    from src.backend.app.services.agent_task_scheduler import AgentTaskScheduler
    from tests.unit.test_agent_task_commands import AgentTaskCommandService
    from datetime import UTC, datetime, timedelta

    store = _store(tmp_path)
    store.update_project_metadata("project-1", {"project_config_path": str(Path("examples/project_config_synthetic_smoke.yaml").resolve())})

    def planner(**_):
        defaults = store.get_project("project-1").metadata.get("agent_defaults", {})
        resource = defaults.get("default_template")
        return {"ok": True, "validation": {"ok": True}, "warnings": [], "plan": {
            "pipeline_id": "contract-fixture", "nodes": [{"id": "functional_connectivity_subject", "backend": "python", "params": {}}],
            "metadata": {"science_decisions": {
                "template_required": True, "template_candidates": [resource] if resource else [],
                "atlas_required": True, "atlas_candidates": [{"name": "atlas", "path": "atlas-fixture", "license": "CC0", "checksum": "a" * 64}],
                "tr_conflict": True, "tr_values": {"bids": 2.0, "project": 3.0},
            }},
        }}

    service = AgentTaskCommandService(store, planner=planner)
    task = service.create(project_id="project-1", goal="Plan fixture", command_id="create", actor="test")
    assert task.state == "WAITING_FOR_SCIENCE_DECISION"
    # The command's scheduler must be durable; this fixture explicitly drains
    # one checkpoint so it can inspect the next batch without a background race.
    service._planning.bind_scheduler(AgentTaskScheduler(store, planning_service=service._planning, start_workers=False))
    return store, service, task


def test_registration_replaces_batch_then_confirms_all_science_once(tmp_path):
    from src.backend.app.services.mock_store import SQLiteDesktopStore
    store, service, task = _planning_fixture(tmp_path)
    original_batch = task.pending_decision_batch
    path = _resource(tmp_path)
    args = dict(project_id="project-1", lifecycle_id=task.lifecycle_id, batch_id=original_batch.batch_id,
                command_id="register-template", actor="test",
                resource=ScientificResourceInput(name="fixture", path=str(path), license="CC0", space="MNI152"))
    resumed = service._planning.register_template(**args)
    assert resumed.pending_decision_batch is None
    service._planning.scheduler.run_once(owner="fixture")
    current = store.get_agent_lifecycle(task.lifecycle_id)
    assert len(current.pending_decision_batch.items) == 3
    assert current.pending_decision_batch.batch_id != original_batch.batch_id
    assert current.pending_decision_batch.evidence_snapshot_hash != original_batch.evidence_snapshot_hash
    assert all(item.readiness == "ready" for item in current.pending_decision_batch.items)
    assert service._planning.register_template(**args).lifecycle_id == task.lifecycle_id
    reopened = SQLiteDesktopStore(store.db_path)
    assert reopened.get_agent_lifecycle(task.lifecycle_id).pending_decision_batch == current.pending_decision_batch
    with pytest.raises(SafetyError, match="AGENT_DECISION_STALE"):
        service.answer(project_id="project-1", lifecycle_id=task.lifecycle_id, batch_id=original_batch.batch_id,
                       answers=[{"item_id": "template", "value": str(path)}], command_id="old-answer", actor="test")
    answered = service.answer(project_id="project-1", lifecycle_id=task.lifecycle_id, batch_id=current.pending_decision_batch.batch_id,
        answers=[{"item_id": item.item_id, "value": item.options[0].id} for item in current.pending_decision_batch.items],
        command_id="confirm-all", actor="test")
    assert answered.state == "WAITING_FOR_APPROVAL"
    assert store.list_execution_tickets("project-1") == []
    assert store.list_run_links("project-1") == []


def test_registered_template_content_drift_rejects_old_science_answers(tmp_path):
    store, service, task = _planning_fixture(tmp_path)
    path = _resource(tmp_path)
    resumed = service._planning.register_template(
        project_id="project-1", lifecycle_id=task.lifecycle_id, batch_id=task.pending_decision_batch.batch_id,
        command_id="register-template", actor="test",
        resource=ScientificResourceInput(name="fixture", path=str(path), license="CC0", space="MNI152"),
    )
    service._planning.scheduler.run_once(owner="fixture")
    batch = store.get_agent_lifecycle(resumed.lifecycle_id).pending_decision_batch
    nib.save(nib.Nifti1Image(np.full((3, 3, 3), 7, dtype=np.float32), np.eye(4)), path)
    with pytest.raises(SafetyError, match="AGENT_DECISION_EVIDENCE_STALE"):
        service._planning.answer(
            project_id="project-1", lifecycle_id=task.lifecycle_id, batch_id=batch.batch_id,
            answers=[{"item_id": item.item_id, "value": item.options[0].id} for item in batch.items],
            command_id="stale-answer", actor="test",
        )


def test_registration_rejects_cross_project_and_failed_resource_without_change(tmp_path):
    store, service, task = _planning_fixture(tmp_path)
    path = _resource(tmp_path, valid=False)
    args = dict(project_id="project-1", lifecycle_id=task.lifecycle_id, batch_id=task.pending_decision_batch.batch_id,
                command_id="register-template", actor="test",
                resource=ScientificResourceInput(name="fixture", path=str(path), license="CC0", space="MNI152"))
    with pytest.raises(SafetyError, match="LIFECYCLE_NOT_FOUND"):
        service._planning.register_template(**{**args, "project_id": "other"})
    with pytest.raises(SafetyError, match="AGENT_TEMPLATE_RESOURCE_INVALID"):
        service._planning.register_template(**args)
    assert store.get_agent_lifecycle(task.lifecycle_id) == task
    assert "agent_defaults" not in store.get_project("project-1").metadata


def test_registration_transaction_failure_does_not_publish_resource_or_new_wake(tmp_path, monkeypatch):
    store, service, task = _planning_fixture(tmp_path)
    path = _resource(tmp_path)
    before = store.list_agent_task_wakes(project_id="project-1", include_consumed=True)

    def fail(*_):
        raise RuntimeError("fixture event write interrupted")

    monkeypatch.setattr(store, "_insert_agent_lifecycle_event", fail)
    with pytest.raises(RuntimeError, match="interrupted"):
        service._planning.register_template(project_id="project-1", lifecycle_id=task.lifecycle_id,
            batch_id=task.pending_decision_batch.batch_id, command_id="register", actor="test",
            resource=ScientificResourceInput(name="fixture", path=str(path), license="CC0", space="MNI152"))
    assert store.get_agent_lifecycle(task.lifecycle_id) == task
    assert "agent_defaults" not in store.get_project("project-1").metadata
    assert store.list_agent_task_wakes(project_id="project-1", include_consumed=True) == before


def test_zero_multiple_and_filtered_candidates_have_real_answerability(tmp_path):
    store = _store(tmp_path)
    path = _resource(tmp_path)
    settings = ProjectAgentSettingsService(store)
    registered = settings.verify_template(project_id="project-1", resource=ScientificResourceInput(
        name="fixture", path=str(path), license="CC0", space="MNI152"))
    second_path = path.with_name("other.nii.gz")
    nib.save(nib.Nifti1Image(np.full((3, 3, 3), 2, dtype=np.float32), np.eye(4)), second_path)
    second = settings.verify_template(project_id="project-1", resource=ScientificResourceInput(
        name="other", path=str(second_path), license="CC0", space="MNI152"))
    for candidates, count in [([], 0), ([registered], 1), ([registered, second], 2),
                              ([{**registered, "checksum": "sha256:" + "0" * 64}], 0),
                              ([{**registered, "license": ""}], 0)]:
        item, = AgentPlanningService._science_decision_items({"nodes": [], "metadata": {
            "science_decisions": {"template_required": True, "template_candidates": candidates}}}, {}, store.get_project("project-1").metadata)
        assert len(item.options) == count
        assert item.readiness == ("ready" if count else "input_required")
    # Revalidation of a registered checksum rejects subsequent content drift.
    nib.save(nib.Nifti1Image(np.full((3, 3, 3), 3, dtype=np.float32), np.eye(4)), path)
    assert settings.verified_template_candidates(project_dir=tmp_path / "project", candidates=[registered]) == ()


def test_template_registration_public_api_rejects_cross_project_and_unknown_checksum(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from fastapi.testclient import TestClient
    from src.backend.app.main import create_app
    from src.backend.app.api.dependencies import get_project_store, get_agent_task_command_service
    store, service, task = _planning_fixture(tmp_path)
    path = _resource(tmp_path)
    app = create_app()
    app.dependency_overrides[get_project_store] = lambda: store
    app.dependency_overrides[get_agent_task_command_service] = lambda: SimpleNamespace(register_template=service._planning.register_template)
    payload = {"batch_id": task.pending_decision_batch.batch_id, "command_id": "register-api", "actor": "test",
               "resource": {"name": "fixture", "path": str(path), "license": "CC0", "space": "MNI152"}}
    with TestClient(app) as client:
        endpoint = f"/api/projects/project-1/agent/tasks/{task.lifecycle_id}/register-template"
        assert client.post(endpoint.replace("project-1", "other"), json=payload).status_code == 404
        assert client.post(endpoint, json={**payload, "resource": {**payload["resource"], "checksum": "fake"}}).status_code == 422
        response = client.post(endpoint, json=payload)
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "preparing"
    assert store.list_execution_tickets("project-1") == []
