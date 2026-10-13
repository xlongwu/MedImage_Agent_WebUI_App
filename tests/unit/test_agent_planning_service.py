from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.backend.app.core.config import ConfigService
from src.backend.app.core.config_schema import AgentModelRuntimeConfig, AgentMultiAgentConfig
from src.backend.app.core.exceptions import SafetyError
from src.backend.app.planner.llm_provider import LLMProviderResult
from src.backend.app.planner.memory_influence_guard import MemoryInfluenceGuard
from src.backend.app.planner.reviewed_plan_store import save_reviewed_plan
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.schemas.project_agent_settings import (
    ProjectMultiAgentConsent,
    UpdateProjectAgentSettingsRequest,
)
from src.backend.app.services.agent_evidence_service import AgentEvidenceService
from src.backend.app.services.agent_multi_agent_policy_service import AgentMultiAgentPolicyService
from src.backend.app.services.agent_orchestrator import AgentOrchestrator
from src.backend.app.services.agent_planning_service import AgentPlanningService
from src.backend.app.services.agent_task_scheduler import AgentTaskScheduler
from src.backend.app.services.agent_team_coordinator_service import AgentTeamCoordinatorService
from src.backend.app.services.agent_team_scheduler import AgentTeamScheduler
from src.backend.app.services.agent_team_worker_service import AgentTeamWorkerService
from src.backend.app.services.approval_summary_service import ApprovalSummaryService
from src.backend.app.services.goal_planning_service import GoalPlanningService
from src.backend.app.services.mock_store import SQLiteDesktopStore
from src.backend.app.services.project_agent_settings_service import ProjectAgentSettingsService
from src.backend.app.services.reviewed_conversion_service import ReviewedConversionService
from src.backend.app.services.structured_agent_model_adapter import StructuredAgentModelAdapter
from src.backend.app.services.team_ticket_policy_verifier import TeamTicketPolicyVerifier


def test_planning_service_advances_a_persisted_lifecycle_without_execution_dependencies(tmp_path) -> None:
    """The planning seam may request missing input but cannot dispatch work."""
    store = SQLiteDesktopStore(tmp_path / "planning.sqlite")
    project = ProjectDetail(
        id="project-1", name="project", study_id="study", modality="rs-fMRI", created_date="today",
        subjects_count=0, current_pipeline_id="pipeline", sequences=[], scans_count=0,
        total_size="0", current_model_id="none",
    )
    store.add_project(project, health_status="ready", rawdata_dir="")
    lifecycle = AgentOrchestrator(store).create(
        project_id=project.id, command_id="create-1", actor="researcher", goal_text="Plan ALFF",
        goal_hash="goal-hash", planning_wake_reason="create",
    )
    service = AgentPlanningService(
        store, planner=None, goal_planning_service=GoalPlanningService(), plan_saver=save_reviewed_plan,
        summary_service=ApprovalSummaryService(),
        conversion_checker=ReviewedConversionService().check_readiness,
        conversion_node_id=ReviewedConversionService.NODE_ID,
        memory_influence_guard=MemoryInfluenceGuard(), harness_config=ConfigService().harness,
        evidence_service=AgentEvidenceService(store),
    )

    advanced = service.advance_planning(
        project_id=project.id, lifecycle_id=lifecycle.lifecycle_id, wake_reason="create"
    )

    assert advanced.state == "WAITING_FOR_INPUT"
    assert advanced.execution_ticket_id is None
    assert advanced.run_id is None


def test_create_team_mode_is_persisted_with_the_initial_planning_wake(tmp_path) -> None:
    store = SQLiteDesktopStore(tmp_path / "team-request.sqlite")
    project = ProjectDetail(
        id="project-1", name="project", study_id="study", modality="rs-fMRI", created_date="today",
        subjects_count=0, current_pipeline_id="pipeline", sequences=[], scans_count=0,
        total_size="0", current_model_id="none",
    )
    store.add_project(project, health_status="ready", rawdata_dir="")

    class Scheduler:
        def notify(self):
            return None

    service = AgentPlanningService(
        store, planner=None, goal_planning_service=GoalPlanningService(), plan_saver=save_reviewed_plan,
        summary_service=ApprovalSummaryService(),
        conversion_checker=ReviewedConversionService().check_readiness,
        conversion_node_id=ReviewedConversionService.NODE_ID,
        memory_influence_guard=MemoryInfluenceGuard(), harness_config=ConfigService().harness,
        evidence_service=AgentEvidenceService(store), scheduler=Scheduler(),
    )

    lifecycle = service.create(
        project_id=project.id,
        goal="Compare two registered evidence domains",
        command_id="create-team-request",
        actor="researcher",
        team_mode="team",
    )

    persisted = store.get_agent_lifecycle(lifecycle.lifecycle_id)
    wakes = store.list_agent_task_wakes(project_id=project.id)
    assert persisted.command_context["requested_team_mode"] == "team"
    assert len(wakes) == 1 and wakes[0].reason == "create"


def test_requested_team_creates_durable_work_from_the_planning_wake(tmp_path) -> None:
    store = SQLiteDesktopStore(tmp_path / "team-start.sqlite")
    consent = ProjectMultiAgentConsent.build(
        project_id="project-1", enabled=True, consent_epoch=1, max_input_tokens=16000,
    )
    project = ProjectDetail(
        id="project-1", name="project", study_id="study", modality="rs-fMRI", created_date="today",
        subjects_count=2, current_pipeline_id="pipeline", sequences=[], scans_count=2,
        total_size="0", current_model_id="none",
        metadata={
            "project_config_path": str(tmp_path / "project.yaml"),
            "agent_defaults": {"multi_agent_consent": consent.model_dump(mode="json")},
        },
    )
    store.add_project(project, health_status="ready", rawdata_dir="")
    lifecycle = AgentOrchestrator(store).create(
        project_id=project.id,
        command_id="create-team",
        actor="researcher",
        goal_text="Review project and dataset evidence before planning",
        goal_hash="goal-hash",
        command_context={"requested_team_mode": "team"},
    )
    multi_config = AgentMultiAgentConfig(enabled=True, max_input_tokens=16000)
    model_config = AgentModelRuntimeConfig(
        enabled=True,
        provider="openai_compatible",
        model="review-model",
        base_url="https://models.example.test/v1",
        api_key="test-key",
    )
    policy = AgentMultiAgentPolicyService(store, multi_config)
    coordinator = AgentTeamCoordinatorService(
        store=store,
        policy_service=policy,
        multi_agent_config=multi_config,
        model_config=model_config,
    )

    class TeamScheduler:
        notified = False

        def notify(self):
            self.notified = True

    team_scheduler = TeamScheduler()
    service = AgentPlanningService(
        store, planner=None, goal_planning_service=GoalPlanningService(), plan_saver=save_reviewed_plan,
        summary_service=ApprovalSummaryService(),
        conversion_checker=ReviewedConversionService().check_readiness,
        conversion_node_id=ReviewedConversionService.NODE_ID,
        memory_influence_guard=MemoryInfluenceGuard(), harness_config=ConfigService().harness,
        model_config=model_config, evidence_service=AgentEvidenceService(store),
    )
    service.bind_team_runtime(
        coordinator=coordinator,
        scheduler=team_scheduler,
        policy_service=policy,
        config=multi_config,
    )

    assert service._start_requested_team(lifecycle=lifecycle, wake_reason="create") is True

    persisted = store.get_agent_lifecycle(lifecycle.lifecycle_id)
    assert persisted.state == "CONTEXT_READY"
    assert persisted.planning_mode == "multi_agent"
    assert persisted.planning_team_id
    assert team_scheduler.notified is True
    assert len(store.list_agent_team_work_items(project.id, persisted.planning_team_id)) == 3
    assert len(store.list_agent_team_wakes(project.id, persisted.planning_team_id)) == 3

    class OfflineProvider:
        def complete(self, *, request):
            return LLMProviderResult(
                ok=True,
                content="[]",
                model=request.model,
                input_tokens=50,
                output_tokens=8,
                latency_ms=1,
                provider_request_id=f"offline-{request.context_payload['role_context']['reviewer_kind']}",
                network_called=True,
            )

    task_scheduler = AgentTaskScheduler(
        store, planning_service=service, start_workers=False
    )
    service.bind_scheduler(task_scheduler)
    team_scheduler = AgentTeamScheduler(
        store,
        worker_service=AgentTeamWorkerService(
            multi_agent_config=multi_config,
            model_config=model_config,
            adapter=StructuredAgentModelAdapter(OfflineProvider()),
        ),
        coordinator_service=coordinator,
        policy_service=policy,
        planning_resume=service.enqueue_team_resume,
        start_workers=False,
    )
    service.team_scheduler = team_scheduler
    for _ in range(24):
        team_scheduler.run_once(owner="test-team-scheduler")

    completed_team = store.get_agent_team(project.id, persisted.planning_team_id)
    planning_wakes = store.list_agent_task_wakes(project_id=project.id)
    assert completed_team.status == "COMPLETED"
    assert any(wake.reason == "team_completed" for wake in planning_wakes)
    assert store.get_agent_lifecycle(lifecycle.lifecycle_id).reviewed_plan_id is None

    planning_requests = []

    def capture_request(*, request):
        planning_requests.append(request)
        return {"ok": False, "errors": ["offline_planner_probe"]}

    service.planner = capture_request
    for _ in range(8):
        task_scheduler.run_once(owner="test-agent-task-scheduler")
        if planning_requests:
            break
    assert len(planning_requests) == 1
    request = planning_requests[0]
    assert request.planning_mode == "multi_agent"
    assert request.team_id == persisted.planning_team_id
    assert request.team_advisory.advisory_hash == request.advisory_hash
    assert request.harness_context_hash
    assert request.consent_hash == consent.consent_hash

    class ApprovalStore:
        def __getattr__(self, name):
            return getattr(store, name)

        def get_agent_lifecycle(self, lifecycle_id):
            current = store.get_agent_lifecycle(lifecycle_id)
            return current.model_copy(
                update={"state": "WAITING_FOR_APPROVAL", "reviewed_plan_id": "reviewed-1"}
            )

    reviewed_plan = SimpleNamespace(
        project_id=project.id,
        reviewed_plan_id="reviewed-1",
        payload={"planning_request": request.model_dump(mode="json")},
    )
    verifier = TeamTicketPolicyVerifier(
        ApprovalStore(), multi_agent_config=multi_config, model_config=model_config
    )
    assert verifier.verify_reviewed_plan(
        reviewed_plan, lifecycle_id=lifecycle.lifecycle_id
    )

    policy_drift_verifier = TeamTicketPolicyVerifier(
        ApprovalStore(),
        multi_agent_config=multi_config.model_copy(
            update={"max_input_tokens": multi_config.max_input_tokens - 1}
        ),
        model_config=model_config,
    )
    with pytest.raises(SafetyError, match="TEAM_TICKET_POLICY_CHANGED"):
        policy_drift_verifier.verify_reviewed_plan(
            reviewed_plan, lifecycle_id=lifecycle.lifecycle_id
        )

    ProjectAgentSettingsService(store).update(
        project_id=project.id,
        request=UpdateProjectAgentSettingsRequest(multi_agent_consent_enabled=False),
    )
    with pytest.raises(SafetyError, match="MULTI_AGENT_CONSENT_INVALIDATED"):
        verifier.verify_reviewed_plan(
            reviewed_plan, lifecycle_id=lifecycle.lifecycle_id
        )
