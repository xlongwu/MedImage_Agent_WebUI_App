from __future__ import annotations

from typing import Any, Protocol

from src.backend.app.schemas.agent_lifecycle import AgentLifecycleEvent, AgentLifecycleRecord
from src.backend.app.schemas.agent_harness import AgentHarnessAttempt, AgentHarnessContext, AgentHarnessStep
from src.backend.app.schemas.agent_evidence import EvidenceSnapshot
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

    def get_agent_lifecycle(self, lifecycle_id: str) -> AgentLifecycleRecord | None: ...

    def list_agent_lifecycles(self, project_id: str) -> list[AgentLifecycleRecord]: ...

    def transition_agent_lifecycle(
        self,
        record: AgentLifecycleRecord,
        event: AgentLifecycleEvent,
        *,
        expected_state: str,
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
