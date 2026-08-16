from __future__ import annotations

from src.backend.app.core.config_schema import AgentHarnessConfig
from src.backend.app.planner.agent_model_adapter import ActionCallMetadata, ActionProposal, AgentModelProviderError
from src.backend.app.core.config import ConfigService
from src.backend.app.planner.memory_influence_guard import MemoryInfluenceGuard
from src.backend.app.planner.reviewed_plan_store import save_reviewed_plan
from src.backend.app.schemas.agent_harness import DraftPlanAction
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.agent_harness_service import AgentHarnessService
from src.backend.app.services.agent_approval_execution_service import AgentApprovalExecutionService
from src.backend.app.services.agent_evidence_service import AgentEvidenceService
from src.backend.app.services.agent_planning_service import AgentPlanningService
from src.backend.app.services.agent_recovery_command_service import AgentRecoveryCommandService
from src.backend.app.services.agent_task_command_service import AgentTaskCommandService
from src.backend.app.services.agent_task_reconciler import AgentTaskReconciler
from src.backend.app.services.agent_task_scheduler import AgentTaskScheduler
from src.backend.app.services.approval_summary_service import ApprovalSummaryService
from src.backend.app.services.goal_planning_service import GoalPlanningService
from src.backend.app.services.mock_store import SQLiteDesktopStore
from src.backend.app.services.recovery_execution_service import RecoveryExecutionService
from src.backend.app.services.reviewed_conversion_service import ReviewedConversionService
from src.backend.app.services.reviewed_execution_service import ReviewedExecutionService


class DraftAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def propose_action(self, **_kwargs):
        self.calls += 1
        return ActionProposal.rule_based(
            DraftPlanAction(kind="draft_plan", reason="Build the reviewed plan", expected_state="CREATED")
        )


class UnavailableAdapter:
    def propose_action(self, **_kwargs):
        raise AgentModelProviderError(
            "AGENT_HARNESS_PROVIDER_UNAVAILABLE",
            ActionCallMetadata(
                provider="openai_compatible", model=None, endpoint_class="chat_completions",
                response_hash=None, input_tokens=None, output_tokens=None,
                cached_input_tokens=None, latency_ms=None, provider_request_id=None,
                network_called=False,
            ),
        )


def _service(store, harness):
    planning = AgentPlanningService(
        store, planner=None, goal_planning_service=GoalPlanningService(),
        plan_saver=save_reviewed_plan, summary_service=ApprovalSummaryService(),
        conversion_checker=ReviewedConversionService().check_readiness,
        conversion_node_id=ReviewedConversionService.NODE_ID,
        memory_influence_guard=MemoryInfluenceGuard(), harness_config=harness.config,
        harness_service=harness, evidence_service=AgentEvidenceService(store),
    )
    scheduler = AgentTaskScheduler(store, planning_service=planning, start_workers=False)
    planning.bind_scheduler(scheduler)
    reconciler = AgentTaskReconciler(store)
    approval = AgentApprovalExecutionService(
        store, executor=ReviewedExecutionService(), summary_service=ApprovalSummaryService(),
        dry_runner=lambda **_kwargs: {"ok": True, "status": "DRY_RUN_OK"},
        reconcile_once=reconciler.reconcile_once, monitor_scheduler=reconciler.start_bounded_monitor,
    )
    recovery = AgentRecoveryCommandService(
        store, stop_planning=harness.stop, recovery_execution_factory=RecoveryExecutionService,
    )
    return AgentTaskCommandService(
        store, planning_service=planning, approval_execution_service=approval,
        recovery_command_service=recovery,
    ), scheduler


def test_enabled_harness_persists_step_and_falls_into_existing_input_gate(tmp_path) -> None:
    """The Harness only reaches the existing planning service; it cannot execute."""
    store = SQLiteDesktopStore(tmp_path / "lifecycle.sqlite")
    store.add_project(ProjectDetail(
        id="project-1", name="project", study_id="study", modality="rs-fMRI", created_date="today",
        subjects_count=0, current_pipeline_id="pipeline", sequences=[], scans_count=0, total_size="0", current_model_id="none",
    ), health_status="ready", rawdata_dir="")
    adapter = DraftAdapter()
    harness = AgentHarnessService(store, config=AgentHarnessConfig(enabled=True), adapter=adapter)
    service, scheduler = _service(store, harness)

    lifecycle = service.create(project_id="project-1", goal="Plan preprocessing", command_id="create-1", actor="researcher")
    assert lifecycle.state == "CREATED"
    assert scheduler.run_once(owner="test") == lifecycle.lifecycle_id
    lifecycle = store.get_agent_lifecycle(lifecycle.lifecycle_id)

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
    service, scheduler = _service(store, harness)

    lifecycle = service.create(
        project_id="project-1",
        goal="Plan preprocessing",
        command_id="create-provider-failure",
        actor="researcher",
    )
    assert scheduler.run_once(owner="test") == lifecycle.lifecycle_id

    attempt = store.get_agent_harness_attempt(lifecycle.lifecycle_id)
    steps = store.list_agent_harness_steps(attempt.attempt_id)
    assert store.get_agent_lifecycle(lifecycle.lifecycle_id).state == "WAITING_FOR_INPUT"
    assert lifecycle.reviewed_plan_id is None
    assert lifecycle.execution_ticket_id is None
    assert attempt.status == "STOPPED"
    assert attempt.terminal_reason == "AGENT_HARNESS_PROVIDER_UNAVAILABLE"
    assert attempt.fallback_from == "rule_based"
    assert attempt.fallback_to == "deterministic_goal_planner"
    assert attempt.fallback_reason == "AGENT_HARNESS_PROVIDER_UNAVAILABLE"
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
    service, scheduler = _service(store, harness)

    waiting = service.create(
        project_id="project-1", goal="Plan preprocessing", command_id="create-cancel", actor="researcher"
    )
    canceled = service.cancel(
        project_id="project-1", lifecycle_id=waiting.lifecycle_id, command_id="cancel-harness", actor="researcher"
    )

    assert canceled.state == "CANCELED"
    assert store.get_agent_harness_attempt(waiting.lifecycle_id) is None
