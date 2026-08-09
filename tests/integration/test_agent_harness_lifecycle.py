from __future__ import annotations

from src.backend.app.core.config_schema import AgentHarnessConfig
from src.backend.app.schemas.agent_harness import ActionEnvelope
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.agent_harness_service import AgentHarnessService
from src.backend.app.services.agent_task_command_service import AgentTaskCommandService
from src.backend.app.services.mock_store import SQLiteDesktopStore


class DraftAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def propose_action(self, **_kwargs):
        self.calls += 1
        return ActionEnvelope(kind="draft_plan", reason="Build the reviewed plan", expected_state="CREATED")


class UnavailableAdapter:
    def propose_action(self, **_kwargs):
        raise RuntimeError("AGENT_HARNESS_PROVIDER_UNAVAILABLE")


def test_enabled_harness_persists_step_and_falls_into_existing_input_gate(tmp_path) -> None:
    """The Harness only reaches the existing planning service; it cannot execute."""
    store = SQLiteDesktopStore(tmp_path / "lifecycle.sqlite")
    store.add_project(ProjectDetail(
        id="project-1", name="project", study_id="study", modality="rs-fMRI", created_date="today",
        subjects_count=0, current_pipeline_id="pipeline", sequences=[], scans_count=0, total_size="0", current_model_id="none",
    ), health_status="ready", rawdata_dir="")
    adapter = DraftAdapter()
    harness = AgentHarnessService(store, config=AgentHarnessConfig(enabled=True), adapter=adapter)
    service = AgentTaskCommandService(store, harness_service=harness)

    lifecycle = service.create(project_id="project-1", goal="Plan preprocessing", command_id="create-1", actor="researcher")

    attempt = store.get_agent_harness_attempt(lifecycle.lifecycle_id)
    steps = store.list_agent_harness_steps(attempt.attempt_id)
    assert lifecycle.state == "WAITING_FOR_INPUT"  # existing project-config prerequisite
    assert attempt.status == "WAITING_FOR_USER"
    assert [step.kind for step in steps] == ["draft_plan"]
    assert adapter.calls == 1
    assert lifecycle.execution_ticket_id is None
    assert store.list_execution_tickets("project-1") == []


def test_enabled_harness_provider_failure_stops_without_deterministic_plan_fallback(tmp_path) -> None:
    store = SQLiteDesktopStore(tmp_path / "provider-failure.sqlite")
    store.add_project(ProjectDetail(
        id="project-1", name="project", study_id="study", modality="rs-fMRI", created_date="today",
        subjects_count=0, current_pipeline_id="pipeline", sequences=[], scans_count=0, total_size="0", current_model_id="none",
    ), health_status="ready", rawdata_dir="")
    harness = AgentHarnessService(
        store,
        config=AgentHarnessConfig(enabled=True),
        adapter=UnavailableAdapter(),
    )
    service = AgentTaskCommandService(store, harness_service=harness)

    lifecycle = service.create(
        project_id="project-1",
        goal="Plan preprocessing",
        command_id="create-provider-failure",
        actor="researcher",
    )

    attempt = store.get_agent_harness_attempt(lifecycle.lifecycle_id)
    steps = store.list_agent_harness_steps(attempt.attempt_id)
    assert lifecycle.state == "CREATED"
    assert lifecycle.reviewed_plan_id is None
    assert lifecycle.execution_ticket_id is None
    assert attempt.status == "STOPPED"
    assert attempt.terminal_reason == "AGENT_HARNESS_PROVIDER_UNAVAILABLE"
    assert steps[0].error_code == "AGENT_HARNESS_PROVIDER_UNAVAILABLE"
    assert store.list_execution_tickets("project-1") == []


def test_cancel_stops_an_injected_harness_attempt(tmp_path) -> None:
    store = SQLiteDesktopStore(tmp_path / "cancel-harness.sqlite")
    store.add_project(ProjectDetail(
        id="project-1", name="project", study_id="study", modality="rs-fMRI", created_date="today",
        subjects_count=0, current_pipeline_id="pipeline", sequences=[], scans_count=0, total_size="0", current_model_id="none",
    ), health_status="ready", rawdata_dir="")
    harness = AgentHarnessService(
        store,
        config=AgentHarnessConfig(enabled=True),
        adapter=DraftAdapter(),
    )
    service = AgentTaskCommandService(store, harness_service=harness)

    waiting = service.create(
        project_id="project-1", goal="Plan preprocessing", command_id="create-cancel", actor="researcher"
    )
    canceled = service.cancel(
        project_id="project-1", lifecycle_id=waiting.lifecycle_id, command_id="cancel-harness", actor="researcher"
    )

    attempt = store.get_agent_harness_attempt(waiting.lifecycle_id)
    assert canceled.state == "CANCELED"
    assert attempt.status == "STOPPED"
    assert attempt.terminal_reason == "LIFECYCLE_CANCELED"
