"""Persistence and lifecycle rules for backend-issued execution tickets."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from src.backend.app.core.exceptions import SafetyError, StateStoreError
from src.backend.app.core.agent_logging import agent_log_context
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.schemas.execution_ticket import (
    ExecutionRetryPolicy,
    ExecutionTicket,
    ExecutionTicketEvent,
)


logger = logging.getLogger(__name__)


class ExecutionTicketStore(Protocol):
    def add_execution_ticket(self, ticket: ExecutionTicket) -> ExecutionTicket: ...

    def get_execution_ticket(self, execution_ticket_id: str) -> ExecutionTicket | None: ...

    def update_execution_ticket(
        self, execution_ticket_id: str, **updates: object
    ) -> ExecutionTicket | None: ...

    def consume_execution_ticket(
        self,
        execution_ticket_id: str,
        *,
        idempotency_key: str,
        consumed_at: datetime,
    ) -> tuple[ExecutionTicket | None, bool]: ...

    def add_execution_ticket_event(
        self, event: ExecutionTicketEvent
    ) -> ExecutionTicketEvent: ...

    def list_execution_ticket_events(
        self, execution_ticket_id: str
    ) -> list[ExecutionTicketEvent]: ...


_IMMUTABLE_FIELDS = (
    "schema_version",
    "ticket_kind",
    "execution_ticket_id",
    "project_id",
    "reviewed_plan_id",
    "plan_hash",
    "goal_contract_hash",
    "evaluation_policy_version",
    "approval_summary_hash",
    "execution_environment_snapshot_id",
    "execution_environment_hash",
    "execution_provider_kind",
    "sandbox_policies",
    "sandbox_policy_version",
    "sandbox_policies_hash",
    "sandbox_provider",
    "memory_context_hash",
    "approved_actor",
    "approved_node_ids",
    "approved_backend_ids",
    "input_roots",
    "output_roots",
    "readonly_roots",
    "project_config_path",
    "pipeline_path",
    "scope_hash",
    "allowlist_hash",
    "normalized_params_hash",
    "contract_versions",
    "audit_id",
    "issued_at",
    "expires_at",
    "retry_policy",
    "parent_execution_ticket_id",
    "parent_ticket_hash",
    "parent_run_id",
    "recovery_approval_id",
    "recovery_proposal_id",
    "recovery_proposal_hash",
    "recovery_candidate_id",
    "recovery_candidate_hash",
    "recovery_attempt_id",
    "quota_reservation_id",
    "recovery_action",
    "recovery_node_ids",
    "recovery_subject_ids",
    "checkpoint_id",
    "recovery_run_id",
    "output_namespace",
)


def _canonical_path(value: str | Path) -> str:
    return str(Path(value).expanduser().resolve())


def _canonical_ticket_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {field: payload[field] for field in _IMMUTABLE_FIELDS}


def calculate_ticket_hash(ticket: ExecutionTicket | dict[str, Any]) -> str:
    model = ticket if isinstance(ticket, ExecutionTicket) else ExecutionTicket(**ticket)
    payload = model.model_dump(mode="python")
    return stable_hash(_canonical_ticket_payload(payload))


def calculate_execution_scope_hash(
    *,
    approved_node_ids: list[str] | tuple[str, ...],
    approved_backend_ids: list[str] | tuple[str, ...],
    input_roots: list[str] | tuple[str, ...],
    output_roots: list[str] | tuple[str, ...],
    readonly_roots: list[str] | tuple[str, ...] = (),
) -> str:
    """Hash the complete canonical execution scope bound to a ticket."""
    return stable_hash(
        {
            "approved_node_ids": sorted(set(approved_node_ids)),
            "approved_backend_ids": sorted(set(approved_backend_ids)),
            "input_roots": sorted({_canonical_path(path) for path in input_roots}),
            "output_roots": sorted({_canonical_path(path) for path in output_roots}),
            "readonly_roots": sorted({_canonical_path(path) for path in readonly_roots}),
        }
    )


class ExecutionTicketService:
    def __init__(self, store: ExecutionTicketStore) -> None:
        self.store = store

    def _event(
        self,
        *,
        ticket_id: str,
        project_id: str,
        event_type: str,
        audit_id: str | None = None,
        reason: str | None = None,
        details: dict[str, object] | None = None,
    ) -> ExecutionTicketEvent:
        event = ExecutionTicketEvent(
            event_id=f"ticket_event_{uuid4().hex}",
            execution_ticket_id=ticket_id,
            project_id=project_id,
            event_type=event_type,
            occurred_at=datetime.now(UTC),
            audit_id=audit_id,
            reason=reason,
            details=details or {},
        )
        try:
            return self.store.add_execution_ticket_event(event)
        except Exception as exc:  # fail closed when security audit cannot persist
            raise StateStoreError(
                "EXECUTION_TICKET_EVENT_WRITE_FAILED",
                details={
                    "ticket_id": ticket_id,
                    "event_type": event_type,
                    "cause": str(exc),
                },
            ) from exc

    def record_rejection(
        self,
        *,
        project_id: str,
        reason: str,
        ticket_id: str | None = None,
        audit_id: str | None = None,
        details: dict[str, object] | None = None,
    ) -> ExecutionTicketEvent:
        event = self._event(
            ticket_id=ticket_id or f"missing_ticket_{uuid4().hex}",
            project_id=project_id,
            event_type="rejected",
            audit_id=audit_id,
            reason=reason,
            details=details,
        )
        logger.warning(
            "execution_ticket_rejected",
            extra={"medimage": agent_log_context(
                project_id=project_id,
                execution_ticket_id=ticket_id,
                event_code="EXECUTION_TICKET_REJECTED",
            )},
        )
        return event

    def issue(
        self,
        *,
        project_id: str,
        reviewed_plan_id: str,
        plan_hash: str,
        approval_summary_hash: str,
        execution_environment_snapshot_id: str,
        execution_environment_hash: str,
        sandbox_policies: tuple[dict[str, object], ...] = (),
        sandbox_policy_version: str = "windows-sandbox-v1",
        sandbox_policies_hash: str | None = None,
        execution_provider_kind: str = "local",
        memory_context_hash: str | None,
        approved_actor: str,
        approved_node_ids: list[str] | tuple[str, ...],
        approved_backend_ids: list[str] | tuple[str, ...],
        input_roots: list[str] | tuple[str, ...],
        output_roots: list[str] | tuple[str, ...],
        readonly_roots: list[str] | tuple[str, ...] = (),
        project_config_path: str,
        pipeline_path: str,
        allowlist_hash: str,
        normalized_params_hash: str,
        contract_versions: dict[str, str] | tuple[tuple[str, str], ...],
        audit_id: str,
        goal_contract_hash: str,
        evaluation_policy_version: str,
        max_retry_count: int = 0,
        max_lifecycle_recovery_attempts: int | None = None,
        max_node_attempts: int | None = None,
        max_subject_node_attempts: int | None = None,
        max_replans: int | None = None,
        max_recovery_wall_seconds: int | None = None,
        expires_in_seconds: int = 900,
    ) -> ExecutionTicket:
        if (
            not audit_id
            or not approval_summary_hash
            or not reviewed_plan_id
            or not normalized_params_hash
            or not contract_versions
            or not execution_environment_snapshot_id
            or not execution_environment_hash
        ):
            raise SafetyError(
                "EXECUTION_TICKET_BINDING_REQUIRED",
                code="EXECUTION_TICKET_BINDING_REQUIRED",
            )
        from src.backend.app.schemas.sandbox import SandboxPolicySet
        from src.backend.app.services.sandbox_policy_service import SandboxPolicyService, empty_policy_set

        policy_set = SandboxPolicySet(
            policies=tuple(sandbox_policies),
            policies_hash=sandbox_policies_hash or empty_policy_set().policies_hash,
        )
        SandboxPolicyService.verify(policy_set)
        now = datetime.now(UTC)
        ticket_id = f"ticket_{uuid4().hex}"
        payload: dict[str, Any] = {
            "schema_version": 5,
            "execution_ticket_id": ticket_id,
            "project_id": project_id,
            "reviewed_plan_id": reviewed_plan_id,
            "plan_hash": plan_hash,
            "goal_contract_hash": goal_contract_hash,
            "evaluation_policy_version": evaluation_policy_version,
            "approval_summary_hash": approval_summary_hash,
            "execution_environment_snapshot_id": execution_environment_snapshot_id,
            "execution_environment_hash": execution_environment_hash,
            "execution_provider_kind": execution_provider_kind,
            "sandbox_policies": tuple(item.model_dump(mode="json") for item in policy_set.policies),
            "sandbox_policy_version": sandbox_policy_version,
            "sandbox_policies_hash": policy_set.policies_hash,
            "sandbox_provider": "windows_restricted_process",
            "memory_context_hash": memory_context_hash,
            "approved_actor": approved_actor,
            "approved_node_ids": tuple(sorted(set(approved_node_ids))),
            "approved_backend_ids": tuple(sorted(set(approved_backend_ids))),
            "input_roots": tuple(sorted({_canonical_path(p) for p in input_roots})),
            "output_roots": tuple(sorted({_canonical_path(p) for p in output_roots})),
            "readonly_roots": tuple(sorted({_canonical_path(p) for p in readonly_roots})),
            "project_config_path": _canonical_path(project_config_path),
            "pipeline_path": _canonical_path(pipeline_path),
            "scope_hash": calculate_execution_scope_hash(
                approved_node_ids=approved_node_ids,
                approved_backend_ids=approved_backend_ids,
                input_roots=input_roots,
                output_roots=output_roots,
                readonly_roots=readonly_roots,
            ),
            "allowlist_hash": allowlist_hash,
            "normalized_params_hash": normalized_params_hash,
            "contract_versions": tuple(sorted(dict(contract_versions).items())),
            "audit_id": audit_id,
            "issued_at": now,
            "expires_at": now + timedelta(seconds=max(1, expires_in_seconds)),
            "retry_policy": ExecutionRetryPolicy(
                max_retry_count=max_retry_count,
                allowed_node_ids=tuple(sorted(set(approved_node_ids))),
                require_approval=True,
                max_lifecycle_recovery_attempts=max_lifecycle_recovery_attempts,
                max_node_attempts=max_node_attempts,
                max_subject_node_attempts=max_subject_node_attempts,
                max_replans=max_replans,
                max_recovery_wall_seconds=max_recovery_wall_seconds,
            ),
            "status": "issued",
            "canonical_hash": "pending",
        }
        ticket = ExecutionTicket(**payload)
        ticket = ticket.model_copy(
            update={"canonical_hash": calculate_ticket_hash(ticket)}
        )
        try:
            stored = self.store.add_execution_ticket(ticket)
        except Exception as exc:
            raise StateStoreError("EXECUTION_TICKET_WRITE_FAILED") from exc
        self._event(
            ticket_id=stored.execution_ticket_id,
            project_id=stored.project_id,
            event_type="issued",
            audit_id=stored.audit_id,
        )
        logger.info(
            "execution_ticket_issued",
            extra={"medimage": agent_log_context(
                project_id=stored.project_id,
                reviewed_plan_id=stored.reviewed_plan_id,
                execution_ticket_id=stored.execution_ticket_id,
                event_code="EXECUTION_TICKET_ISSUED",
            )},
        )
        return stored

    def validate(
        self,
        execution_ticket_id: str,
        *,
        project_id: str,
        reviewed_plan_id: str,
        plan_hash: str,
        approval_summary_hash: str,
        memory_context_hash: str | None,
        scope_hash: str,
        allowlist_hash: str,
        normalized_params_hash: str,
        contract_versions: dict[str, str] | tuple[tuple[str, str], ...],
        project_config_path: str,
        pipeline_path: str,
        goal_contract_hash: str | None = None,
        evaluation_policy_version: str | None = None,
        replay_idempotency_key: str | None = None,
    ) -> ExecutionTicket:
        ticket = self.store.get_execution_ticket(execution_ticket_id)
        if ticket is None:
            self.record_rejection(
                project_id=project_id,
                ticket_id=execution_ticket_id,
                reason="EXECUTION_TICKET_NOT_FOUND",
            )
            raise SafetyError(
                "EXECUTION_TICKET_NOT_FOUND", code="EXECUTION_TICKET_NOT_FOUND"
            )
        reason: str | None = None
        if calculate_ticket_hash(ticket) != ticket.canonical_hash:
            reason = "EXECUTION_TICKET_TAMPERED"
        elif ticket.project_id != project_id:
            reason = "EXECUTION_TICKET_PROJECT_MISMATCH"
        elif ticket.reviewed_plan_id != reviewed_plan_id:
            reason = "EXECUTION_TICKET_PLAN_MISMATCH"
        elif ticket.plan_hash != plan_hash:
            reason = "EXECUTION_TICKET_HASH_MISMATCH"
        elif goal_contract_hash is not None and ticket.goal_contract_hash != goal_contract_hash:
            reason = "EXECUTION_TICKET_GOAL_CONTRACT_MISMATCH"
        elif (
            evaluation_policy_version is not None
            and ticket.evaluation_policy_version != evaluation_policy_version
        ):
            reason = "EXECUTION_TICKET_EVALUATION_POLICY_MISMATCH"
        elif ticket.approval_summary_hash != approval_summary_hash:
            reason = "EXECUTION_TICKET_APPROVAL_MISMATCH"
        elif ticket.memory_context_hash != memory_context_hash:
            reason = "EXECUTION_TICKET_MEMORY_CONTEXT_MISMATCH"
        elif ticket.scope_hash != scope_hash:
            reason = "EXECUTION_TICKET_SCOPE_MISMATCH"
        elif ticket.allowlist_hash != allowlist_hash:
            reason = "EXECUTION_TICKET_ALLOWLIST_MISMATCH"
        elif ticket.normalized_params_hash != normalized_params_hash:
            reason = "EXECUTION_TICKET_PARAMETER_HASH_MISMATCH"
        elif ticket.contract_versions != tuple(sorted(dict(contract_versions).items())):
            reason = "EXECUTION_TICKET_CONTRACT_VERSION_MISMATCH"
        elif ticket.project_config_path != _canonical_path(project_config_path):
            reason = "EXECUTION_TICKET_PROJECT_CONFIG_MISMATCH"
        elif ticket.pipeline_path != _canonical_path(pipeline_path):
            reason = "EXECUTION_TICKET_PIPELINE_MISMATCH"
        elif ticket.status == "revoked":
            reason = "EXECUTION_TICKET_REVOKED"
        elif ticket.status == "consumed" and ticket.idempotency_key != replay_idempotency_key:
            reason = "EXECUTION_TICKET_REPLAYED"
        elif ticket.status == "expired" or ticket.is_expired():
            reason = "EXECUTION_TICKET_EXPIRED"
            self.store.update_execution_ticket(ticket.execution_ticket_id, status="expired")
        elif ticket.status not in {"issued", "consumed"}:
            reason = "EXECUTION_TICKET_INVALID_STATUS"
        elif ticket.ticket_kind == "recovery_child":
            try:
                self._validate_recovery_child(ticket)
            except SafetyError as exc:
                reason = str(exc.code or "RECOVERY_CHILD_TICKET_INVALID")
        if reason:
            self.record_rejection(
                project_id=project_id,
                ticket_id=ticket.execution_ticket_id,
                audit_id=ticket.audit_id,
                reason=reason,
            )
            raise SafetyError(reason, code=reason)
        self._event(
            ticket_id=ticket.execution_ticket_id,
            project_id=ticket.project_id,
            event_type="validated",
            audit_id=ticket.audit_id,
        )
        return ticket

    @staticmethod
    def _is_within_path(value: str, roots: tuple[str, ...]) -> bool:
        path = Path(value).resolve()
        return any(
            path == Path(root).resolve() or Path(root).resolve() in path.parents
            for root in roots
        )

    def issue_recovery_child(
        self,
        *,
        parent: ExecutionTicket,
        proposal,
        candidate,
        approval,
        attempt,
        reservation,
        project_config_path: str,
        pipeline_path: str,
        output_root: str,
        input_roots: tuple[str, ...],
        expires_in_seconds: int = 600,
    ) -> ExecutionTicket:
        from src.backend.app.services.recovery_policy_service import (
            RecoveryPolicyService,
            calculate_quota_reservation_hash,
            calculate_recovery_approval_hash,
        )
        from src.backend.app.services.recovery_proposal_engine import (
            calculate_recovery_candidate_hash,
            calculate_recovery_proposal_hash,
        )

        if parent.status != "consumed" or calculate_ticket_hash(parent) != parent.canonical_hash:
            raise SafetyError("RECOVERY_PARENT_TICKET_INVALID", code="RECOVERY_PARENT_TICKET_INVALID")
        if calculate_recovery_proposal_hash(proposal) != proposal.recovery_proposal_hash:
            raise SafetyError("RECOVERY_PROPOSAL_TAMPERED", code="RECOVERY_PROPOSAL_TAMPERED")
        if calculate_recovery_candidate_hash(candidate) != candidate.candidate_hash:
            raise SafetyError("RECOVERY_CANDIDATE_TAMPERED", code="RECOVERY_CANDIDATE_TAMPERED")
        policy_service = RecoveryPolicyService(self.store)
        policy_service.validate_approval(
            approval.recovery_approval_id,
            proposal=proposal,
            candidate=candidate,
        )
        if calculate_recovery_approval_hash(approval) != approval.recovery_approval_hash:
            raise SafetyError("RECOVERY_APPROVAL_TAMPERED", code="RECOVERY_APPROVAL_TAMPERED")
        if (
            reservation.status != "reserved"
            or calculate_quota_reservation_hash(reservation) != reservation.reservation_hash
            or reservation.effective_limits != approval.quota_snapshot.effective_limits
            or attempt.status != "APPROVED"
        ):
            raise SafetyError("RECOVERY_CHILD_QUOTA_INVALID", code="RECOVERY_CHILD_QUOTA_INVALID")
        policy_service.validate_reservation(reservation, parent=parent, candidate=candidate)
        if (
            proposal.bindings.execution_ticket_id != parent.execution_ticket_id
            or proposal.bindings.plan_hash != parent.plan_hash
            or proposal.bindings.goal_contract_hash != parent.goal_contract_hash
            or approval.recovery_proposal_hash != proposal.recovery_proposal_hash
            or approval.candidate_hash != candidate.candidate_hash
            or attempt.recovery_attempt_id != reservation.recovery_attempt_id
            or attempt.recovery_approval_id != approval.recovery_approval_id
        ):
            raise SafetyError("RECOVERY_CHILD_BINDING_MISMATCH", code="RECOVERY_CHILD_BINDING_MISMATCH")
        if candidate.action not in {"SAFE_RETRY", "RETRY_FAILED_SUBJECTS", "RESUME"}:
            raise SafetyError("RECOVERY_CHILD_ACTION_INVALID", code="RECOVERY_CHILD_ACTION_INVALID")
        if set(candidate.target_node_ids) - set(parent.approved_node_ids):
            raise SafetyError("RECOVERY_CHILD_NODE_SCOPE_EXPANDED", code="RECOVERY_CHILD_NODE_SCOPE_EXPANDED")
        canonical_output = _canonical_path(output_root)
        expected_namespace = f"recovery_attempts/{attempt.recovery_attempt_id}"
        expected_output = _canonical_path(Path(parent.output_roots[0]) / expected_namespace)
        if (
            attempt.output_namespace != expected_namespace
            or canonical_output != expected_output
            or not self._is_within_path(canonical_output, parent.output_roots)
            or canonical_output in parent.output_roots
        ):
            raise SafetyError("RECOVERY_CHILD_OUTPUT_NOT_ISOLATED", code="RECOVERY_CHILD_OUTPUT_NOT_ISOLATED")
        if any(self._is_within_path(canonical_output, (root,)) for root in parent.readonly_roots):
            raise SafetyError("RECOVERY_CHILD_OUTPUT_READONLY", code="RECOVERY_CHILD_OUTPUT_READONLY")
        allowed_inputs = tuple(sorted(set(parent.input_roots) | set(parent.output_roots)))
        canonical_inputs = tuple(sorted({_canonical_path(path) for path in input_roots}))
        if any(not self._is_within_path(path, allowed_inputs) for path in canonical_inputs):
            raise SafetyError("RECOVERY_CHILD_INPUT_SCOPE_EXPANDED", code="RECOVERY_CHILD_INPUT_SCOPE_EXPANDED")
        canonical_config = _canonical_path(project_config_path)
        canonical_pipeline = _canonical_path(pipeline_path)
        if not all(
            self._is_within_path(path, (canonical_output,))
            for path in (canonical_config, canonical_pipeline)
        ):
            raise SafetyError("RECOVERY_CHILD_CONTROL_PATH_INVALID", code="RECOVERY_CHILD_CONTROL_PATH_INVALID")
        now = datetime.now(UTC)
        child_id = f"recovery_ticket_{uuid4().hex}"
        contract_versions = tuple(
            (node_id, dict(parent.contract_versions)[node_id])
            for node_id in sorted(candidate.target_node_ids)
        )
        payload = {
            "schema_version": 5,
            "ticket_kind": "recovery_child",
            "execution_ticket_id": child_id,
            "project_id": parent.project_id,
            "reviewed_plan_id": parent.reviewed_plan_id,
            "plan_hash": parent.plan_hash,
            "goal_contract_hash": parent.goal_contract_hash,
            "evaluation_policy_version": parent.evaluation_policy_version,
            "approval_summary_hash": approval.recovery_approval_id,
            "execution_environment_snapshot_id": parent.execution_environment_snapshot_id,
            "execution_environment_hash": parent.execution_environment_hash,
            "execution_provider_kind": parent.execution_provider_kind,
            "sandbox_policies": parent.sandbox_policies,
            "sandbox_policy_version": parent.sandbox_policy_version,
            "sandbox_policies_hash": parent.sandbox_policies_hash,
            "sandbox_provider": parent.sandbox_provider,
            "memory_context_hash": parent.memory_context_hash,
            "approved_actor": approval.approved_actor,
            "approved_node_ids": tuple(sorted(candidate.target_node_ids)),
            "approved_backend_ids": parent.approved_backend_ids,
            "input_roots": canonical_inputs,
            "output_roots": (canonical_output,),
            "readonly_roots": parent.readonly_roots,
            "project_config_path": canonical_config,
            "pipeline_path": canonical_pipeline,
            "scope_hash": calculate_execution_scope_hash(
                approved_node_ids=tuple(sorted(candidate.target_node_ids)),
                approved_backend_ids=parent.approved_backend_ids,
                input_roots=canonical_inputs,
                output_roots=(canonical_output,),
                readonly_roots=parent.readonly_roots,
            ),
            "allowlist_hash": parent.allowlist_hash,
            "normalized_params_hash": parent.normalized_params_hash,
            "contract_versions": contract_versions,
            "audit_id": approval.audit_id,
            "issued_at": now,
            "expires_at": now + timedelta(seconds=max(1, min(expires_in_seconds, 900))),
            "retry_policy": ExecutionRetryPolicy(),
            "parent_execution_ticket_id": parent.execution_ticket_id,
            "parent_ticket_hash": parent.canonical_hash,
            "parent_run_id": proposal.bindings.run_id,
            "recovery_approval_id": approval.recovery_approval_id,
            "recovery_proposal_id": proposal.recovery_proposal_id,
            "recovery_proposal_hash": proposal.recovery_proposal_hash,
            "recovery_candidate_id": candidate.candidate_id,
            "recovery_candidate_hash": candidate.candidate_hash,
            "recovery_attempt_id": attempt.recovery_attempt_id,
            "quota_reservation_id": reservation.reservation_id,
            "recovery_action": candidate.action,
            "recovery_node_ids": tuple(sorted(candidate.target_node_ids)),
            "recovery_subject_ids": tuple(sorted(candidate.target_subject_ids)),
            "checkpoint_id": candidate.checkpoint_id,
            "recovery_run_id": attempt.recovery_run_id,
            "output_namespace": attempt.output_namespace,
            "status": "issued",
            "canonical_hash": "pending",
        }
        ticket = ExecutionTicket(**payload)
        ticket = ticket.model_copy(update={"canonical_hash": calculate_ticket_hash(ticket)})
        try:
            stored = self.store.add_execution_ticket(ticket)
        except Exception as exc:
            raise StateStoreError("RECOVERY_CHILD_TICKET_WRITE_FAILED") from exc
        self._event(
            ticket_id=stored.execution_ticket_id,
            project_id=stored.project_id,
            event_type="recovery_child_issued",
            audit_id=stored.audit_id,
            details={
                "parent_ticket_id": parent.execution_ticket_id,
                "recovery_attempt_id": attempt.recovery_attempt_id,
                "recovery_proposal_id": proposal.recovery_proposal_id,
            },
        )
        logger.info(
            "recovery_execution_ticket_issued",
            extra={"medimage": agent_log_context(
                project_id=stored.project_id,
                reviewed_plan_id=stored.reviewed_plan_id,
                execution_ticket_id=stored.execution_ticket_id,
                run_id=stored.recovery_run_id,
                event_code="RECOVERY_EXECUTION_TICKET_ISSUED",
            )},
        )
        return stored

    def _validate_recovery_child(self, ticket: ExecutionTicket) -> None:
        from src.backend.app.services.recovery_policy_service import (
            calculate_quota_reservation_hash,
            calculate_recovery_approval_hash,
        )
        from src.backend.app.services.recovery_proposal_engine import (
            calculate_recovery_candidate_hash,
            calculate_recovery_proposal_hash,
        )

        parent = self.store.get_execution_ticket(ticket.parent_execution_ticket_id or "")
        proposal = self.store.get_recovery_proposal(ticket.recovery_proposal_id or "")
        approval = self.store.get_recovery_approval(ticket.recovery_approval_id or "")
        attempt = self.store.get_recovery_attempt(ticket.recovery_attempt_id or "")
        reservation = self.store.get_recovery_quota_reservation(ticket.quota_reservation_id or "")
        if parent is None or parent.status != "consumed" or parent.canonical_hash != ticket.parent_ticket_hash:
            raise SafetyError("RECOVERY_CHILD_PARENT_INVALID", code="RECOVERY_CHILD_PARENT_INVALID")
        if (
            ticket.execution_environment_snapshot_id
            != parent.execution_environment_snapshot_id
            or ticket.execution_environment_hash != parent.execution_environment_hash
            or ticket.execution_provider_kind != parent.execution_provider_kind
        ):
            raise SafetyError(
                "RECOVERY_CHILD_ENVIRONMENT_SWITCHED",
                code="RECOVERY_CHILD_ENVIRONMENT_SWITCHED",
            )
        if (
            proposal is None
            or proposal.recovery_proposal_hash != ticket.recovery_proposal_hash
            or calculate_recovery_proposal_hash(proposal) != proposal.recovery_proposal_hash
        ):
            raise SafetyError("RECOVERY_CHILD_PROPOSAL_INVALID", code="RECOVERY_CHILD_PROPOSAL_INVALID")
        candidates = [item for item in proposal.candidates if item.candidate_id == ticket.recovery_candidate_id]
        if (
            len(candidates) != 1
            or candidates[0].candidate_hash != ticket.recovery_candidate_hash
            or calculate_recovery_candidate_hash(candidates[0]) != candidates[0].candidate_hash
        ):
            raise SafetyError("RECOVERY_CHILD_CANDIDATE_INVALID", code="RECOVERY_CHILD_CANDIDATE_INVALID")
        if (
            approval is None
            or approval.status != "active"
            or approval.expires_at <= datetime.now(UTC)
            or approval.candidate_hash != ticket.recovery_candidate_hash
            or calculate_recovery_approval_hash(approval) != approval.recovery_approval_hash
            or approval.recovery_approval_id != ticket.recovery_approval_id
            or approval.parent_execution_ticket_id != parent.execution_ticket_id
            or approval.parent_ticket_hash != parent.canonical_hash
            or approval.parent_plan_hash != ticket.plan_hash
            or approval.goal_contract_hash != ticket.goal_contract_hash
            or approval.recovery_proposal_id != ticket.recovery_proposal_id
        ):
            raise SafetyError("RECOVERY_CHILD_APPROVAL_INVALID", code="RECOVERY_CHILD_APPROVAL_INVALID")
        if (
            attempt is None
            or attempt.status not in {"TICKET_ISSUED", "RUNNING"}
            or attempt.child_execution_ticket_id != ticket.execution_ticket_id
            or attempt.quota_reservation_id != ticket.quota_reservation_id
            or attempt.recovery_proposal_id != ticket.recovery_proposal_id
            or attempt.recovery_proposal_hash != ticket.recovery_proposal_hash
            or attempt.candidate_id != ticket.recovery_candidate_id
            or attempt.candidate_hash != ticket.recovery_candidate_hash
            or attempt.recovery_approval_id != ticket.recovery_approval_id
            or attempt.parent_execution_ticket_id != parent.execution_ticket_id
            or attempt.parent_ticket_hash != parent.canonical_hash
            or attempt.action != ticket.recovery_action
            or set(attempt.target_node_ids) != set(ticket.recovery_node_ids)
            or set(attempt.target_subject_ids) != set(ticket.recovery_subject_ids)
            or attempt.checkpoint_id != ticket.checkpoint_id
            or attempt.recovery_run_id != ticket.recovery_run_id
            or attempt.output_namespace != ticket.output_namespace
        ):
            raise SafetyError("RECOVERY_CHILD_ATTEMPT_INVALID", code="RECOVERY_CHILD_ATTEMPT_INVALID")
        if (
            reservation is None
            or reservation.status != "reserved"
            or reservation.recovery_attempt_id != attempt.recovery_attempt_id
            or calculate_quota_reservation_hash(reservation) != reservation.reservation_hash
            or reservation.effective_limits != approval.quota_snapshot.effective_limits
            or reservation.recovery_proposal_id != ticket.recovery_proposal_id
            or reservation.candidate_id != ticket.recovery_candidate_id
            or reservation.action != ticket.recovery_action
            or set(reservation.node_ids) != set(ticket.recovery_node_ids)
            or set(reservation.subject_ids) != set(ticket.recovery_subject_ids)
        ):
            raise SafetyError("RECOVERY_CHILD_QUOTA_INVALID", code="RECOVERY_CHILD_QUOTA_INVALID")
        if set(ticket.recovery_node_ids) != set(ticket.approved_node_ids):
            raise SafetyError("RECOVERY_CHILD_NODE_SCOPE_INVALID", code="RECOVERY_CHILD_NODE_SCOPE_INVALID")
        candidate = candidates[0]
        if (
            candidate.action != ticket.recovery_action
            or set(candidate.target_node_ids) != set(ticket.recovery_node_ids)
            or set(candidate.target_subject_ids) != set(ticket.recovery_subject_ids)
            or candidate.checkpoint_id != ticket.checkpoint_id
            or proposal.bindings.execution_ticket_id != parent.execution_ticket_id
            or proposal.bindings.project_id != ticket.project_id
            or proposal.bindings.run_id != ticket.parent_run_id
            or ticket.plan_hash != parent.plan_hash
            or ticket.goal_contract_hash != parent.goal_contract_hash
            or ticket.reviewed_plan_id != parent.reviewed_plan_id
            or set(dict(ticket.contract_versions)) != set(ticket.recovery_node_ids)
            or any(
                dict(parent.contract_versions).get(node_id) != version
                for node_id, version in ticket.contract_versions
            )
        ):
            raise SafetyError("RECOVERY_CHILD_SCOPE_BINDING_DRIFT", code="RECOVERY_CHILD_SCOPE_BINDING_DRIFT")
        if (
            len(ticket.output_roots) != 1
            or ticket.output_namespace != f"recovery_attempts/{ticket.recovery_attempt_id}"
            or Path(ticket.output_roots[0]).resolve()
            != (Path(parent.output_roots[0]).resolve() / ticket.output_namespace).resolve()
            or any(not self._is_within_path(root, parent.output_roots) for root in ticket.output_roots)
            or any(root in parent.output_roots for root in ticket.output_roots)
            or any(self._is_within_path(root, parent.readonly_roots) for root in ticket.output_roots)
            or any(
                not self._is_within_path(root, (*parent.input_roots, *parent.output_roots))
                for root in ticket.input_roots
            )
            or ticket.readonly_roots != parent.readonly_roots
            or not all(
                self._is_within_path(path, ticket.output_roots)
                for path in (ticket.project_config_path, ticket.pipeline_path)
            )
        ):
            raise SafetyError("RECOVERY_CHILD_PATH_BINDING_DRIFT", code="RECOVERY_CHILD_PATH_BINDING_DRIFT")
        from src.backend.app.services.recovery_policy_service import RecoveryPolicyService

        RecoveryPolicyService(self.store).validate_reservation(
            reservation,
            parent=parent,
            candidate=candidates[0],
        )

    def consume(self, ticket: ExecutionTicket, *, idempotency_key: str) -> ExecutionTicket:
        if ticket.is_expired():
            raise SafetyError(
                "EXECUTION_TICKET_NOT_CONSUMABLE",
                code="EXECUTION_TICKET_NOT_CONSUMABLE",
            )
        consumed_at = datetime.now(UTC)
        if ticket.ticket_kind == "recovery_child":
            from src.backend.app.services.recovery_policy_service import RecoveryPolicyService

            RecoveryPolicyService(self.store).consume_reservation(
                ticket.quota_reservation_id or ""
            )
        updated, newly_consumed = self.store.consume_execution_ticket(
            ticket.execution_ticket_id,
            idempotency_key=idempotency_key,
            consumed_at=consumed_at,
        )
        if (
            updated is None
            or updated.status != "consumed"
            or updated.idempotency_key != idempotency_key
        ):
            raise StateStoreError("EXECUTION_TICKET_CONSUME_FAILED")
        if newly_consumed:
            self._event(
                ticket_id=ticket.execution_ticket_id,
                project_id=ticket.project_id,
                event_type="consumed",
                audit_id=ticket.audit_id,
                details={"idempotency_key": idempotency_key},
            )
            logger.info(
                "execution_ticket_consumed",
                extra={"medimage": agent_log_context(
                    project_id=ticket.project_id,
                    reviewed_plan_id=ticket.reviewed_plan_id,
                    execution_ticket_id=ticket.execution_ticket_id,
                    event_code="EXECUTION_TICKET_CONSUMED",
                )},
            )
        return updated

    def revoke(self, execution_ticket_id: str, *, reason: str) -> ExecutionTicket:
        ticket = self.store.get_execution_ticket(execution_ticket_id)
        if ticket is None:
            raise SafetyError("EXECUTION_TICKET_NOT_FOUND", code="EXECUTION_TICKET_NOT_FOUND")
        revoked = self.store.update_execution_ticket(
            execution_ticket_id,
            status="revoked",
            revoked_at=datetime.now(UTC).isoformat(),
            revocation_reason=reason,
        )
        if revoked is None:
            raise StateStoreError("EXECUTION_TICKET_REVOKE_FAILED")
        self._event(
            ticket_id=ticket.execution_ticket_id,
            project_id=ticket.project_id,
            event_type="revoked",
            audit_id=ticket.audit_id,
            reason=reason,
        )
        logger.warning(
            "execution_ticket_revoked",
            extra={"medimage": agent_log_context(
                project_id=ticket.project_id,
                reviewed_plan_id=ticket.reviewed_plan_id,
                execution_ticket_id=ticket.execution_ticket_id,
                event_code="EXECUTION_TICKET_REVOKED",
            )},
        )
        return revoked
