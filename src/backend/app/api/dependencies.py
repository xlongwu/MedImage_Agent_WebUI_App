from __future__ import annotations

from threading import Lock
from typing import Any, Protocol

from fastapi import Depends

from src.backend.app.schemas.agent_evidence import EvidenceSnapshot
from src.backend.app.schemas.agent_harness import AgentActionRecord, AgentHarnessAttempt, AgentHarnessContext, AgentHarnessStep
from src.backend.app.schemas.agent_invariant import AgentInvariantAuditRecord
from src.backend.app.schemas.agent_lifecycle import AgentLifecycleEvent, AgentLifecycleRecord
from src.backend.app.schemas.agent_task_wake import AgentTaskWakeRecord
from src.backend.app.schemas.desktop import (
    DatasetSummary,
    ProjectDetail,
    ProjectSummary,
    ReviewedPlanRecord,
    RunLinkRecord,
    StudyOverview,
)
from src.backend.app.schemas.execution_ticket import ExecutionTicket, ExecutionTicketEvent
from src.backend.app.schemas.gateway_dispatch import GatewayDispatch, GatewayDispatchEvent
from src.backend.app.schemas.goal_contract import GoalEvaluationRecord
from src.backend.app.schemas.observation import ObservationRecord
from src.backend.app.schemas.recovery import DiagnosisRecord, RecoveryProposal
from src.backend.app.schemas.recovery_attempt import (
    RecoveryApprovalEvent,
    RecoveryApprovalRecord,
    RecoveryAttemptEvent,
    RecoveryAttemptRecord,
    RecoveryQuotaReservation,
)


class ProjectStore(Protocol):
    def list_projects(self) -> list[ProjectSummary]: ...

    def get_project(self, project_id: str) -> ProjectDetail | None: ...

    def update_project_metadata(
        self, project_id: str, updates: dict[str, object]
    ) -> ProjectDetail | None: ...

    def get_study_overview(self, study_id: str) -> StudyOverview | None: ...

    def get_dataset_summary(self, project_id: str) -> DatasetSummary | None: ...

    def list_import_records(self, project_id: str) -> list[dict[str, Any]]: ...

    def list_import_paths(self, project_id: str) -> list[str]: ...

    def list_reviewed_plans(self, project_id: str) -> list[ReviewedPlanRecord]: ...

    def get_reviewed_plan(self, reviewed_plan_id: str) -> ReviewedPlanRecord | None: ...

    def add_reviewed_plan(self, record: ReviewedPlanRecord) -> ReviewedPlanRecord: ...

    def update_reviewed_plan(
        self, reviewed_plan_id: str, **updates: object
    ) -> ReviewedPlanRecord | None: ...

    def list_run_links(
        self,
        project_id: str,
        reviewed_plan_id: str | None = None,
    ) -> list[RunLinkRecord]: ...

    def get_run_link_by_run_id(
        self,
        project_id: str,
        run_id: str,
    ) -> RunLinkRecord | None: ...

    def add_run_link(self, record: RunLinkRecord) -> RunLinkRecord: ...

    def get_execution_ticket(
        self,
        execution_ticket_id: str,
    ) -> ExecutionTicket | None: ...

    def list_execution_tickets(self, project_id: str) -> list[ExecutionTicket]: ...

    def list_execution_ticket_events(
        self,
        execution_ticket_id: str,
    ) -> list[ExecutionTicketEvent]: ...

    def get_gateway_dispatch_by_ticket(
        self, execution_ticket_id: str
    ) -> GatewayDispatch | None: ...

    def list_gateway_dispatch_events(
        self, dispatch_id: str
    ) -> list[GatewayDispatchEvent]: ...

    def create_agent_lifecycle(
        self,
        record: AgentLifecycleRecord,
        event: AgentLifecycleEvent,
    ) -> AgentLifecycleRecord: ...

    def create_agent_lifecycle_with_wake(
        self, record: AgentLifecycleRecord, event: AgentLifecycleEvent, wake: AgentTaskWakeRecord
    ) -> AgentLifecycleRecord: ...

    def get_agent_lifecycle(self, lifecycle_id: str) -> AgentLifecycleRecord | None: ...

    def list_agent_lifecycles(self, project_id: str) -> list[AgentLifecycleRecord]: ...

    def transition_agent_lifecycle(
        self,
        record: AgentLifecycleRecord,
        event: AgentLifecycleEvent,
        *,
        expected_state: str,
    ) -> AgentLifecycleRecord: ...

    def transition_agent_lifecycle_with_wake(
        self, record: AgentLifecycleRecord, event: AgentLifecycleEvent, wake: AgentTaskWakeRecord, *, expected_state: str
    ) -> AgentLifecycleRecord: ...

    def list_agent_lifecycle_events(
        self,
        lifecycle_id: str,
    ) -> list[AgentLifecycleEvent]: ...

    def add_agent_evidence_snapshot(self, record: EvidenceSnapshot) -> EvidenceSnapshot: ...

    def get_agent_evidence_snapshot(self, snapshot_hash: str) -> EvidenceSnapshot | None: ...

    def create_agent_harness_attempt(self, record: AgentHarnessAttempt) -> AgentHarnessAttempt: ...

    def get_agent_harness_attempt(self, lifecycle_id: str) -> AgentHarnessAttempt | None: ...

    def update_agent_harness_attempt(
        self,
        record: AgentHarnessAttempt,
        *,
        expected_status: str,
        expected_step_no: int | None = None,
        expected_context_hash: str | None = None,
        expected_lease_owner: str | None = None,
    ) -> AgentHarnessAttempt: ...

    def add_agent_harness_context(self, record: AgentHarnessContext) -> AgentHarnessContext: ...

    def get_agent_harness_context(self, context_hash: str) -> AgentHarnessContext | None: ...

    def add_agent_harness_step(self, record: AgentHarnessStep) -> AgentHarnessStep: ...

    def update_agent_harness_step(self, record: AgentHarnessStep) -> AgentHarnessStep: ...

    def get_agent_harness_step_by_idempotency(self, idempotency_key: str) -> AgentHarnessStep | None: ...

    def list_agent_harness_steps(self, attempt_id: str) -> list[AgentHarnessStep]: ...

    def add_agent_harness_action(self, record: AgentActionRecord) -> AgentActionRecord: ...

    def update_agent_harness_action(self, record: AgentActionRecord, *, expected_status: str) -> AgentActionRecord: ...

    def list_agent_harness_actions(self, attempt_id: str) -> list[AgentActionRecord]: ...

    def add_agent_invariant_audit(self, record: AgentInvariantAuditRecord) -> AgentInvariantAuditRecord: ...

    def list_agent_invariant_audits(self, lifecycle_id: str) -> list[AgentInvariantAuditRecord]: ...

    def enqueue_agent_task_wake(self, record: AgentTaskWakeRecord) -> AgentTaskWakeRecord: ...

    def claim_next_agent_task_wake(self, *, owner: str, now, lease_expires_at) -> AgentTaskWakeRecord | None: ...

    def complete_agent_task_wake(self, record: AgentTaskWakeRecord, *, owner: str, now) -> AgentTaskWakeRecord: ...

    def retry_agent_task_wake(self, record: AgentTaskWakeRecord, *, owner: str, now, available_at, error_code: str) -> AgentTaskWakeRecord: ...

    def list_agent_task_wakes(self, *, project_id: str, include_consumed: bool = False) -> list[AgentTaskWakeRecord]: ...

    def add_observation(self, record: ObservationRecord) -> ObservationRecord: ...

    def get_observation(self, observation_id: str) -> ObservationRecord | None: ...

    def list_observations(
        self,
        project_id: str,
        *,
        lifecycle_id: str | None = None,
        run_id: str | None = None,
    ) -> list[ObservationRecord]: ...

    def add_goal_evaluation(
        self,
        record: GoalEvaluationRecord,
    ) -> GoalEvaluationRecord: ...

    def get_goal_evaluation(
        self,
        goal_evaluation_id: str,
    ) -> GoalEvaluationRecord | None: ...

    def list_goal_evaluations(
        self,
        project_id: str,
        *,
        lifecycle_id: str | None = None,
        observation_id: str | None = None,
    ) -> list[GoalEvaluationRecord]: ...

    def add_recovery_diagnosis(self, record: DiagnosisRecord) -> DiagnosisRecord: ...

    def get_recovery_diagnosis(self, diagnosis_id: str) -> DiagnosisRecord | None: ...

    def list_recovery_diagnoses(
        self,
        project_id: str,
        *,
        lifecycle_id: str | None = None,
    ) -> list[DiagnosisRecord]: ...

    def add_recovery_proposal(self, record: RecoveryProposal) -> RecoveryProposal: ...

    def get_recovery_proposal(self, proposal_id: str) -> RecoveryProposal | None: ...

    def list_recovery_proposals(
        self,
        project_id: str,
        *,
        lifecycle_id: str | None = None,
    ) -> list[RecoveryProposal]: ...

    def add_recovery_approval(
        self, record: RecoveryApprovalRecord, event: RecoveryApprovalEvent
    ) -> RecoveryApprovalRecord: ...

    def get_recovery_approval(self, approval_id: str) -> RecoveryApprovalRecord | None: ...

    def list_recovery_approvals(
        self, project_id: str, *, lifecycle_id: str | None = None
    ) -> list[RecoveryApprovalRecord]: ...

    def list_recovery_approval_events(self, approval_id: str) -> list[RecoveryApprovalEvent]: ...

    def update_recovery_approval(
        self,
        record: RecoveryApprovalRecord,
        event: RecoveryApprovalEvent,
        *,
        expected_status: str,
    ) -> RecoveryApprovalRecord: ...

    def create_recovery_attempt(
        self, record: RecoveryAttemptRecord, event: RecoveryAttemptEvent
    ) -> RecoveryAttemptRecord: ...

    def get_recovery_attempt(self, attempt_id: str) -> RecoveryAttemptRecord | None: ...

    def get_recovery_attempt_by_idempotency(
        self, idempotency_key: str
    ) -> RecoveryAttemptRecord | None: ...

    def list_recovery_attempts(
        self, project_id: str, *, lifecycle_id: str | None = None
    ) -> list[RecoveryAttemptRecord]: ...

    def list_recovery_attempt_events(self, attempt_id: str) -> list[RecoveryAttemptEvent]: ...

    def transition_recovery_attempt(
        self,
        record: RecoveryAttemptRecord,
        event: RecoveryAttemptEvent,
        *,
        expected_status: str,
    ) -> RecoveryAttemptRecord: ...

    def reserve_recovery_quota(
        self, reservation: RecoveryQuotaReservation
    ) -> RecoveryQuotaReservation: ...

    def get_recovery_quota_reservation(
        self, reservation_id: str
    ) -> RecoveryQuotaReservation | None: ...

    def list_recovery_quota_reservations(
        self, project_id: str, *, lifecycle_id: str | None = None
    ) -> list[RecoveryQuotaReservation]: ...

    def update_recovery_quota_reservation(
        self, record: RecoveryQuotaReservation, *, expected_status: str
    ) -> RecoveryQuotaReservation: ...

    def get_memory_consent(self, project_id: str) -> dict[str, object]: ...

    def set_memory_consent(
        self,
        *,
        project_id: str,
        command_id: str,
        principal: str,
        generate_enabled: bool,
        use_enabled: bool,
        explicitly_authorized_backfill: bool = False,
    ) -> dict[str, object]: ...

    def list_memory_outbox(
        self, project_id: str, *, after_sequence: int = 0, limit: int = 100
    ) -> list[dict[str, object]]: ...

    def get_memory_outbox_max_sequence(self, project_id: str) -> int: ...

    def get_memory_source_projection(
        self, *, project_id: str, source_type: str, source_id: str
    ) -> dict[str, object] | None: ...

    def append_memory_forget_ledger(self, **kwargs: object) -> dict[str, object]: ...

    def list_memory_forget_ledger(self, project_id: str) -> list[dict[str, object]]: ...


import src.backend.app.services.mock_store as _mock_store_module  # noqa: E402


def get_project_store() -> ProjectStore:
    return _mock_store_module.mock_store


_AGENT_TASK_SERVICES_GUARD = Lock()
_AGENT_TASK_SERVICES: dict[int, object] = {}


def _build_agent_task_command_service(store: ProjectStore):
    """Compose the Agent Task command graph at the application boundary.

    All mutable and environment-derived dependencies are created here, rather
    than by individual domain services.  The only intentional cycle is the
    scheduler/planner pair; it is completed explicitly after both objects are
    constructed.
    """
    from src.backend.app.core.config import ConfigService
    from src.backend.app.planner.memory_influence_guard import MemoryInfluenceGuard
    from src.backend.app.planner.reviewed_plan_store import save_reviewed_plan
    from src.backend.app.services.agent_approval_execution_service import AgentApprovalExecutionService
    from src.backend.app.services.agent_evidence_service import AgentEvidenceService
    from src.backend.app.services.agent_harness_service import AgentHarnessService
    from src.backend.app.services.agent_planning_action_service import AgentPlanningActionService
    from src.backend.app.services.agent_planning_service import AgentPlanningService
    from src.backend.app.services.agent_recovery_command_service import AgentRecoveryCommandService
    from src.backend.app.services.agent_task_command_service import AgentTaskCommandService
    from src.backend.app.services.agent_task_reconciler import AgentTaskReconciler
    from src.backend.app.services.agent_task_scheduler import AgentTaskScheduler
    from src.backend.app.services.approval_summary_service import ApprovalSummaryService
    from src.backend.app.services.execution_environment_service import ExecutionEnvironmentService
    from src.backend.app.services.goal_planning_service import GoalPlanningService
    from src.backend.app.services.memory_repository import MemoryRepository
    from src.backend.app.services.memory_retrieval_service import MemoryRetrievalService
    from src.backend.app.services.recovery_execution_service import RecoveryExecutionService
    from src.backend.app.services.reviewed_conversion_service import ReviewedConversionService
    from src.backend.app.services.reviewed_execution_service import ReviewedExecutionService

    config = ConfigService()
    memory_context_service = None
    memory_initialization_error = None
    if config.memory.enabled and config.memory.use_enabled:
        try:
            memory_context_service = MemoryRetrievalService(
                repository=MemoryRepository(config.memory.store_path),
                project_store=store,
                config=config.memory,
            )
        except Exception:
            memory_initialization_error = "MEMORY_STORE_UNHEALTHY"

    executor = ReviewedExecutionService()
    summary_service = ApprovalSummaryService(
        environment_service=ExecutionEnvironmentService(store)
    )
    reconciler = AgentTaskReconciler(store)

    def dry_runner(**kwargs):
        from src.backend.app.api.execute_reviewed_routes import ExecuteReviewedRequest

        return executor.execute(ExecuteReviewedRequest(
            plan=kwargs["plan"],
            approval=kwargs["approval"],
            project_id=kwargs["project_id"],
            reviewed_plan_id=kwargs.get("reviewed_plan_id"),
            project_config_path=kwargs["project_config_path"],
            dry_run=True,
            actor=kwargs["actor"],
            lifecycle_id=kwargs.get("lifecycle_id"),
        ))

    planning = AgentPlanningService(
        store,
        planner=None,
        goal_planning_service=GoalPlanningService(),
        plan_saver=save_reviewed_plan,
        summary_service=summary_service,
        conversion_checker=ReviewedConversionService().check_readiness,
        conversion_node_id=ReviewedConversionService.NODE_ID,
        memory_context_service=memory_context_service,
        memory_initialization_error=memory_initialization_error,
        memory_influence_guard=MemoryInfluenceGuard(),
        harness_config=config.harness,
        evidence_service=AgentEvidenceService(store),
    )
    scheduler = AgentTaskScheduler(store, planning_service=planning)
    planning.bind_scheduler(scheduler)
    reconciler.planning_waker = planning.enqueue_resume
    if config.harness.enabled:
        action_service = AgentPlanningActionService(
            store,
            draft_plan=lambda **kwargs: planning.draft_plan(**kwargs),
        )
        harness = AgentHarnessService(
            store,
            config=config.harness,
            draft_plan=lambda **kwargs: planning.draft_plan(**kwargs),
            planning_action_service=action_service,
        )
        planning.bind_harness(harness)

    approval = AgentApprovalExecutionService(
        store,
        executor=executor,
        summary_service=summary_service,
        dry_runner=dry_runner,
        reconcile_once=reconciler.reconcile_once,
        monitor_scheduler=reconciler.start_bounded_monitor,
    )
    recovery = AgentRecoveryCommandService(
        store,
        stop_planning=(planning.harness_service.stop if planning.harness_service else None),
        recovery_execution_factory=RecoveryExecutionService,
    )
    return AgentTaskCommandService(
        store,
        planning_service=planning,
        approval_execution_service=approval,
        recovery_command_service=recovery,
    )


def get_agent_task_command_service_for_store(store: ProjectStore):
    """Return the one dependency-assembled command service for this store."""
    key = id(store)
    with _AGENT_TASK_SERVICES_GUARD:
        service = _AGENT_TASK_SERVICES.get(key)
        if service is None or getattr(service, "store", None) is not store:
            service = _build_agent_task_command_service(store)
            _AGENT_TASK_SERVICES[key] = service
        return service


def get_agent_task_command_service(
    store: ProjectStore = Depends(get_project_store),
):
    return get_agent_task_command_service_for_store(store)
