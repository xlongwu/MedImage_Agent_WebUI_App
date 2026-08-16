from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.backend.app.schemas.desktop import ReviewedPlanRecord
from src.backend.app.services.execution_environment_service import ExecutionEnvironmentService
from src.backend.app.services.mock_store import SQLiteDesktopStore


def _reviewed(*, node_id: str = "functional_connectivity_subject") -> ReviewedPlanRecord:
    return ReviewedPlanRecord(
        reviewed_plan_id="reviewed-environment",
        project_id="project-environment",
        project_config_path="project.yaml",
        plan_hash="plan-hash",
        created_at="2026-08-16T00:00:00Z",
        updated_at="2026-08-16T00:00:00Z",
        payload={
            "plan": {"nodes": [{"id": node_id, "backend": "python"}]},
        },
    )


def _service(tmp_path, *, config=None, now=None) -> ExecutionEnvironmentService:
    return ExecutionEnvironmentService(
        SQLiteDesktopStore(tmp_path / "environment.sqlite"),
        config_reader=lambda: config or {"gpu_mode": "prefer"},
        now=now,
    )


def test_same_plan_environment_has_same_hash_but_new_snapshot_identity(tmp_path) -> None:
    first_time = datetime(2026, 8, 16, tzinfo=UTC)
    service = _service(tmp_path, now=lambda: first_time)
    first = service.capture_for_plan(
        project_id="project-environment",
        reviewed_plan=_reviewed(),
        write_roots=("project://derivatives", "project://work"),
        readonly_roots=("project://rawdata",),
    )
    service.now = lambda: first_time + timedelta(minutes=5)
    second = service.capture_for_plan(
        project_id="project-environment",
        reviewed_plan=_reviewed(),
        write_roots=("project://work", "project://derivatives"),
        readonly_roots=("project://rawdata",),
    )

    assert first.snapshot_id != second.snapshot_id
    assert first.captured_at != second.captured_at
    assert first.environment_hash == second.environment_hash


def test_plan_selected_contract_change_changes_environment_hash(tmp_path) -> None:
    service = _service(tmp_path)
    python_snapshot = service.capture_for_plan(
        project_id="project-environment",
        reviewed_plan=_reviewed(),
        write_roots=("project://derivatives",),
        readonly_roots=("project://rawdata",),
    )
    native_snapshot = service.capture_for_plan(
        project_id="project-environment",
        reviewed_plan=_reviewed(node_id="native_preproc_full_execute"),
        write_roots=("project://derivatives",),
        readonly_roots=("project://rawdata",),
    )

    assert python_snapshot.contract_versions != native_snapshot.contract_versions
    assert python_snapshot.node_registry_hash != native_snapshot.node_registry_hash
    assert python_snapshot.environment_hash != native_snapshot.environment_hash


def test_unrelated_config_does_not_change_selected_python_environment(tmp_path) -> None:
    first = _service(tmp_path, config={"gpu_mode": "prefer", "matlab_command": "private-a"})
    second = _service(tmp_path, config={"gpu_mode": "prefer", "matlab_command": "private-b"})
    kwargs = {
        "project_id": "project-environment",
        "reviewed_plan": _reviewed(),
        "write_roots": ("project://derivatives",),
        "readonly_roots": ("project://rawdata",),
        "persist": False,
    }

    assert first.capture_for_plan(**kwargs).environment_hash == second.capture_for_plan(**kwargs).environment_hash


def test_persisted_snapshot_has_no_private_configuration_path(tmp_path) -> None:
    private_matlab = str(tmp_path / "private" / "matlab.exe")
    service = _service(tmp_path, config={"matlab_command": private_matlab})
    snapshot = service.capture_for_plan(
        project_id="project-environment",
        reviewed_plan=_reviewed(node_id="spm_realign_subject"),
        write_roots=("project://derivatives",),
        readonly_roots=("project://rawdata",),
    )
    persisted = service.store.get_execution_environment_snapshot(snapshot.snapshot_id)

    assert persisted is not None
    assert private_matlab not in persisted.model_dump_json()
    assert persisted.tool_capabilities[0].installation_path_hash
