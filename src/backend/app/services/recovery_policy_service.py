"""Final authority for recovery approval, quota, and binding decisions."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

from src.backend.app.core.exceptions import SafetyError, StateStoreError
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.runtime.node_contract_registry import get_node_contract
from src.backend.app.schemas.execution_ticket import ExecutionTicket
from src.backend.app.schemas.recovery import (
    RecoveryCandidate,
    RecoveryProposal,
    RecoveryQuotaLimits,
    RecoveryQuotaUsage,
)
from src.backend.app.schemas.recovery_attempt import (
    RecoveryApprovalEvent,
    RecoveryApprovalRecord,
    RecoveryQuotaReservation,
)
from src.backend.app.services.recovery_proposal_engine import (
    calculate_recovery_candidate_hash,
    calculate_recovery_proposal_hash,
    decide_recovery_quota,
)


DEFAULT_PROJECT_RECOVERY_POLICY = RecoveryQuotaLimits(
    max_lifecycle_recovery_attempts=2,
    max_node_attempts=1,
    max_subject_node_attempts=1,
    max_replans=1,
    max_recovery_wall_seconds=600,
)


def default_project_recovery_policy() -> dict[str, int]:
    """Return the explicit fail-closed quota policy persisted for new projects."""
    return {
        key: int(value)
        for key, value in DEFAULT_PROJECT_RECOVERY_POLICY.model_dump().items()
        if value is not None
    }


class RecoveryPolicyStore(Protocol):
    def get_project(self, project_id: str): ...
    def get_agent_lifecycle(self, lifecycle_id: str): ...
    def get_reviewed_plan(self, reviewed_plan_id: str): ...
    def get_run_link_by_run_id(self, project_id: str, run_id: str): ...
    def get_recovery_proposal(self, proposal_id: str) -> RecoveryProposal | None: ...
    def get_recovery_diagnosis(self, diagnosis_id: str): ...
    def get_observation(self, observation_id: str): ...
    def get_execution_ticket(self, ticket_id: str) -> ExecutionTicket | None: ...
    def add_recovery_approval(self, record, event): ...
    def get_recovery_approval(self, approval_id: str): ...
    def list_recovery_approvals(self, project_id: str, *, lifecycle_id: str | None = None): ...
    def list_recovery_approval_events(self, approval_id: str): ...
    def update_recovery_approval(self, record, event, *, expected_status: str): ...
    def reserve_recovery_quota(self, reservation): ...
    def get_recovery_quota_reservation(self, reservation_id: str): ...
    def list_recovery_quota_reservations(self, project_id: str, *, lifecycle_id: str | None = None): ...
    def update_recovery_quota_reservation(self, record, *, expected_status: str): ...


def calculate_recovery_approval_hash(
    value: RecoveryApprovalRecord | dict[str, object],
) -> str:
    payload = value.model_dump(mode="json") if isinstance(value, RecoveryApprovalRecord) else dict(value)
    payload.pop("recovery_approval_hash", None)
    return stable_hash(payload)


def calculate_quota_reservation_hash(
    value: RecoveryQuotaReservation | dict[str, object],
) -> str:
    payload = value.model_dump(mode="json") if isinstance(value, RecoveryQuotaReservation) else dict(value)
    payload.pop("reservation_hash", None)
    return stable_hash(payload)


def _candidate(proposal: RecoveryProposal, candidate_id: str) -> RecoveryCandidate:
    matches = [item for item in proposal.candidates if item.candidate_id == candidate_id]
    if len(matches) != 1:
        raise SafetyError("RECOVERY_CANDIDATE_NOT_FOUND", code="RECOVERY_CANDIDATE_NOT_FOUND")
    candidate = matches[0]
    if calculate_recovery_candidate_hash(candidate) != candidate.candidate_hash:
        raise SafetyError("RECOVERY_CANDIDATE_TAMPERED", code="RECOVERY_CANDIDATE_TAMPERED")
    return candidate


class RecoveryPolicyService:
    EXECUTABLE_ACTIONS = {"SAFE_RETRY", "RETRY_FAILED_SUBJECTS", "RESUME"}
    QUOTA_DIMENSIONS = (
        "max_lifecycle_recovery_attempts",
        "max_node_attempts",
        "max_subject_node_attempts",
        "max_replans",
        "max_recovery_wall_seconds",
    )

    def __init__(self, store: RecoveryPolicyStore) -> None:
        self.store = store

    def _project_policy(self, project_id: str) -> tuple[dict[str, object], RecoveryQuotaLimits]:
        project = self.store.get_project(project_id)
        metadata = getattr(project, "metadata", {}) if project is not None else {}
        raw = metadata.get("recovery_policy") if isinstance(metadata, dict) else None
        policy = dict(raw) if isinstance(raw, dict) else {}
        quota = RecoveryQuotaLimits(
            **{
                key: policy.get(key)
                for key in (
                    "max_lifecycle_recovery_attempts",
                    "max_node_attempts",
                    "max_subject_node_attempts",
                    "max_replans",
                    "max_recovery_wall_seconds",
                )
            }
        )
        return policy, quota

    def current_usage(
        self,
        project_id: str,
        lifecycle_id: str,
        *,
        exclude_reservation_id: str | None = None,
    ) -> RecoveryQuotaUsage:
        reservations = self.store.list_recovery_quota_reservations(
            project_id,
            lifecycle_id=lifecycle_id,
        )
        active = [
            item
            for item in reservations
            if item.status in {"reserved", "consumed"}
            and item.reservation_id != exclude_reservation_id
        ]
        node_counts: dict[str, int] = {}
        subject_counts: dict[tuple[str, str], int] = {}
        for item in active:
            for node_id in item.node_ids:
                node_counts[node_id] = node_counts.get(node_id, 0) + 1
                for subject_id in item.subject_ids:
                    key = (node_id, subject_id)
                    subject_counts[key] = subject_counts.get(key, 0) + 1
        return RecoveryQuotaUsage(
            lifecycle_recovery_attempts=len(active),
            node_attempts=max(node_counts.values(), default=0),
            subject_node_attempts=max(subject_counts.values(), default=0),
            replans=sum(item.reserves_replan for item in active),
            recovery_wall_seconds=sum(item.reserved_wall_seconds for item in active),
        )

    def _cap_to_lifecycle_history(
        self,
        decision,
        *,
        project_id: str,
        lifecycle_id: str,
        usage: RecoveryQuotaUsage,
    ):
        reservations = self.store.list_recovery_quota_reservations(
            project_id, lifecycle_id=lifecycle_id
        )
        historical = [
            item.effective_limits
            for item in reservations
            if item.status in {"reserved", "consumed"}
        ]
        if not historical:
            return decision
        effective = {
            dimension: min(
                decision.effective_limits[dimension],
                *(limits[dimension] for limits in historical),
            )
            for dimension in self.QUOTA_DIMENSIONS
        }
        usage_by_limit = {
            "max_lifecycle_recovery_attempts": usage.lifecycle_recovery_attempts,
            "max_node_attempts": usage.node_attempts,
            "max_subject_node_attempts": usage.subject_node_attempts,
            "max_replans": usage.replans,
            "max_recovery_wall_seconds": usage.recovery_wall_seconds,
        }
        exhausted = tuple(
            sorted(
                dimension
                for dimension, limit in effective.items()
                if usage_by_limit[dimension] >= limit
            )
        )
        reasons = list(decision.reason_codes)
        if exhausted and "RECOVERY_QUOTA_EXHAUSTED" not in reasons:
            reasons.append("RECOVERY_QUOTA_EXHAUSTED")
        return decision.model_copy(
            update={
                "effective_limits": effective,
                "exhausted_dimensions": exhausted,
                "executable": not decision.missing_dimensions and not exhausted,
                "reason_codes": tuple(reasons),
            }
        )

    def validate_reservation(
        self,
        reservation: RecoveryQuotaReservation,
        *,
        parent: ExecutionTicket,
        candidate: RecoveryCandidate,
    ) -> RecoveryQuotaReservation:
        stored = self.store.get_recovery_quota_reservation(reservation.reservation_id)
        if (
            stored is None
            or stored != reservation
            or stored.status != "reserved"
            or calculate_quota_reservation_hash(stored) != stored.reservation_hash
            or stored.project_id != parent.project_id
            or stored.candidate_id != candidate.candidate_id
            or set(stored.node_ids) != set(candidate.target_node_ids)
            or set(stored.subject_ids) != set(candidate.target_subject_ids)
        ):
            raise SafetyError("RECOVERY_QUOTA_RESERVATION_INVALID", code="RECOVERY_QUOTA_RESERVATION_INVALID")
        try:
            contracts = [get_node_contract(node_id) for node_id in candidate.target_node_ids]
        except KeyError as exc:
            raise SafetyError(
                "RECOVERY_NODE_CONTRACT_MISSING",
                code="RECOVERY_NODE_CONTRACT_MISSING",
            ) from exc
        _, project_quota = self._project_policy(parent.project_id)
        decision = decide_recovery_quota(
            ticket=parent,
            node_contracts=contracts,
            project_policy=project_quota,
            usage=self.current_usage(
                stored.project_id,
                stored.lifecycle_id,
                exclude_reservation_id=stored.reservation_id,
            ),
        )
        if not decision.executable:
            raise SafetyError("RECOVERY_QUOTA_CHANGED", code="RECOVERY_QUOTA_CHANGED")
        limits = {
            key: min(value, stored.effective_limits[key])
            for key, value in decision.effective_limits.items()
        }
        active = [
            item
            for item in self.store.list_recovery_quota_reservations(
                stored.project_id, lifecycle_id=stored.lifecycle_id
            )
            if item.status in {"reserved", "consumed"}
        ]
        if len(active) > limits["max_lifecycle_recovery_attempts"]:
            raise SafetyError("RECOVERY_QUOTA_LIFECYCLE_EXCEEDED", code="RECOVERY_QUOTA_LIFECYCLE_EXCEEDED")
        for node_id in stored.node_ids:
            if sum(node_id in item.node_ids for item in active) > limits["max_node_attempts"]:
                raise SafetyError("RECOVERY_QUOTA_NODE_EXCEEDED", code="RECOVERY_QUOTA_NODE_EXCEEDED")
            for subject_id in stored.subject_ids:
                if sum(
                    node_id in item.node_ids and subject_id in item.subject_ids
                    for item in active
                ) > limits["max_subject_node_attempts"]:
                    raise SafetyError(
                        "RECOVERY_QUOTA_SUBJECT_NODE_EXCEEDED",
                        code="RECOVERY_QUOTA_SUBJECT_NODE_EXCEEDED",
                    )
        if sum(item.reserves_replan for item in active) > limits["max_replans"]:
            raise SafetyError("RECOVERY_QUOTA_REPLAN_EXCEEDED", code="RECOVERY_QUOTA_REPLAN_EXCEEDED")
        if sum(item.reserved_wall_seconds for item in active) > limits["max_recovery_wall_seconds"]:
            raise SafetyError("RECOVERY_QUOTA_WALL_EXCEEDED", code="RECOVERY_QUOTA_WALL_EXCEEDED")
        return stored

    def authorize_candidate(
        self,
        *,
        project_id: str,
        lifecycle_id: str,
        proposal_id: str,
        candidate_id: str,
        require_execution: bool,
    ) -> tuple[RecoveryProposal, RecoveryCandidate, ExecutionTicket, object]:
        proposal = self.store.get_recovery_proposal(proposal_id)
        if (
            proposal is None
            or proposal.bindings.project_id != project_id
            or proposal.bindings.lifecycle_id != lifecycle_id
        ):
            raise SafetyError("RECOVERY_PROPOSAL_BINDING_MISMATCH", code="RECOVERY_PROPOSAL_BINDING_MISMATCH")
        if calculate_recovery_proposal_hash(proposal) != proposal.recovery_proposal_hash:
            raise SafetyError("RECOVERY_PROPOSAL_TAMPERED", code="RECOVERY_PROPOSAL_TAMPERED")
        candidate = _candidate(proposal, candidate_id)
        if not candidate.eligible:
            raise SafetyError("RECOVERY_CANDIDATE_NOT_ELIGIBLE", code="RECOVERY_CANDIDATE_NOT_ELIGIBLE")
        parent = self.store.get_execution_ticket(proposal.bindings.execution_ticket_id)
        if parent is None or parent.project_id != project_id:
            raise SafetyError("RECOVERY_PARENT_TICKET_NOT_FOUND", code="RECOVERY_PARENT_TICKET_NOT_FOUND")
        if parent.status != "consumed":
            raise SafetyError("RECOVERY_PARENT_TICKET_NOT_CONSUMED", code="RECOVERY_PARENT_TICKET_NOT_CONSUMED")
        from src.backend.app.services.execution_ticket_service import calculate_ticket_hash

        if calculate_ticket_hash(parent) != parent.canonical_hash:
            raise SafetyError("RECOVERY_PARENT_TICKET_TAMPERED", code="RECOVERY_PARENT_TICKET_TAMPERED")
        if (
            parent.plan_hash != proposal.bindings.plan_hash
            or parent.goal_contract_hash != proposal.bindings.goal_contract_hash
        ):
            raise SafetyError("RECOVERY_PARENT_BINDING_DRIFT", code="RECOVERY_PARENT_BINDING_DRIFT")
        lifecycle = self.store.get_agent_lifecycle(lifecycle_id)
        reviewed = self.store.get_reviewed_plan(proposal.bindings.reviewed_plan_id)
        run_link = self.store.get_run_link_by_run_id(project_id, proposal.bindings.run_id)
        if (
            lifecycle is None
            or lifecycle.project_id != project_id
            or lifecycle.reviewed_plan_id != proposal.bindings.reviewed_plan_id
            or lifecycle.execution_ticket_id != parent.execution_ticket_id
            or lifecycle.run_id != proposal.bindings.run_id
            or lifecycle.goal_contract_hash != proposal.bindings.goal_contract_hash
            or lifecycle.recovery_proposal_id != proposal.recovery_proposal_id
            or reviewed is None
            or reviewed.plan_hash != proposal.bindings.plan_hash
            or run_link is None
            or run_link.reviewed_plan_id != proposal.bindings.reviewed_plan_id
            or run_link.audit_id != parent.audit_id
        ):
            raise SafetyError("RECOVERY_LIFECYCLE_BINDING_DRIFT", code="RECOVERY_LIFECYCLE_BINDING_DRIFT")
        diagnosis = self.store.get_recovery_diagnosis(proposal.diagnosis_id)
        if (
            diagnosis is None
            or diagnosis.diagnosis_hash != proposal.diagnosis_hash
            or diagnosis.bindings != proposal.bindings
        ):
            raise SafetyError("RECOVERY_DIAGNOSIS_BINDING_DRIFT", code="RECOVERY_DIAGNOSIS_BINDING_DRIFT")
        if require_execution:
            if candidate.action not in self.EXECUTABLE_ACTIONS or not candidate.executable:
                raise SafetyError("RECOVERY_CANDIDATE_NOT_EXECUTABLE", code="RECOVERY_CANDIDATE_NOT_EXECUTABLE")
            if candidate.risk != "low" or candidate.canonical_diff.changes_reviewed_contract:
                raise SafetyError("RECOVERY_CANDIDATE_REQUIRES_NEW_PLAN", code="RECOVERY_CANDIDATE_REQUIRES_NEW_PLAN")
            if set(candidate.target_node_ids) - set(parent.approved_node_ids):
                raise SafetyError("RECOVERY_NODE_SCOPE_EXPANDED", code="RECOVERY_NODE_SCOPE_EXPANDED")
            if candidate.action == "RETRY_FAILED_SUBJECTS":
                failed_subjects = {
                    fact.subject_id for fact in diagnosis.facts if fact.subject_id is not None
                }
                if not failed_subjects or set(candidate.target_subject_ids) != failed_subjects:
                    raise SafetyError("RECOVERY_FAILED_SUBJECT_SCOPE_DRIFT", code="RECOVERY_FAILED_SUBJECT_SCOPE_DRIFT")
            if candidate.action == "RESUME":
                checkpoint = candidate.checkpoint_evidence
                observation = self.store.get_observation(proposal.bindings.observation_id)
                if (
                    checkpoint is None
                    or observation is None
                    or observation.observation_hash != proposal.bindings.observation_hash
                    or not checkpoint.verified
                    or checkpoint.checkpoint_id != candidate.checkpoint_id
                    or checkpoint.plan_hash != parent.plan_hash
                    or checkpoint.normalized_params_hash != parent.normalized_params_hash
                    or set(checkpoint.input_roots) != set(parent.input_roots)
                    or set(checkpoint.output_roots) != set(parent.output_roots)
                    or set(checkpoint.backend_ids) != set(parent.approved_backend_ids)
                    or set(checkpoint.remaining_node_ids) != set(candidate.target_node_ids)
                    or set(checkpoint.completed_node_ids) & set(checkpoint.remaining_node_ids)
                    or set(checkpoint.remaining_node_ids) - set(parent.approved_node_ids)
                ):
                    raise SafetyError("RECOVERY_CHECKPOINT_BINDING_DRIFT", code="RECOVERY_CHECKPOINT_BINDING_DRIFT")
                evidence_ids = {
                    source.source_id for source in observation.sources
                } | {
                    artifact.artifact_id for artifact in observation.artifacts
                } | {
                    validation.validation_id for validation in observation.validations
                }
                evidence_ids.update(
                    evidence_id
                    for artifact in observation.artifacts
                    for evidence_id in artifact.evidence_ids
                )
                evidence_ids.update(
                    evidence_id
                    for validation in observation.validations
                    for evidence_id in validation.evidence_ids
                )
                completed_artifacts = [
                    artifact
                    for artifact in observation.artifacts
                    if artifact.owner_node_id in checkpoint.completed_node_ids
                ]
                if (
                    not checkpoint.evidence_ids
                    or not set(checkpoint.evidence_ids).issubset(evidence_ids)
                    or any(
                        not any(
                            artifact.owner_node_id == node_id
                            and artifact.exists
                            and artifact.registration_status == "registered"
                            and artifact.reload_status in {"passed", "not_required"}
                            for artifact in completed_artifacts
                        )
                        for node_id in checkpoint.completed_node_ids
                    )
                ):
                    raise SafetyError(
                        "RECOVERY_CHECKPOINT_ARTIFACTS_UNVERIFIED",
                        code="RECOVERY_CHECKPOINT_ARTIFACTS_UNVERIFIED",
                    )
        contracts = []
        for node_id in candidate.target_node_ids:
            try:
                contract = get_node_contract(node_id)
            except KeyError as exc:
                raise SafetyError("RECOVERY_NODE_CONTRACT_MISSING", code="RECOVERY_NODE_CONTRACT_MISSING") from exc
            if contract.resources.process_mode == "sandbox_process" or contract.backend in {"matlab-spm", "dpabi", "gpu"}:
                raise SafetyError("RECOVERY_EXTERNAL_EXECUTION_DISABLED", code="RECOVERY_EXTERNAL_EXECUTION_DISABLED")
            contracts.append(contract)
        if require_execution and candidate.action == "RETRY_FAILED_SUBJECTS":
            if any(not contract.retry_policy.supports_subject_subset for contract in contracts):
                raise SafetyError("RECOVERY_SUBJECT_SUBSET_UNSUPPORTED", code="RECOVERY_SUBJECT_SUBSET_UNSUPPORTED")
        if require_execution and candidate.action == "RESUME":
            checkpoint = candidate.checkpoint_evidence
            if any(
                not contract.retry_policy.supports_resume
                or contract.retry_policy.checkpoint_schema != checkpoint.schema_id
                for contract in contracts
            ):
                raise SafetyError("RECOVERY_RESUME_UNSUPPORTED", code="RECOVERY_RESUME_UNSUPPORTED")
        policy, project_quota = self._project_policy(project_id)
        usage = self.current_usage(project_id, lifecycle_id)
        quota = decide_recovery_quota(
            ticket=parent,
            node_contracts=contracts,
            project_policy=project_quota,
            usage=usage,
        )
        quota = self._cap_to_lifecycle_history(
            quota,
            project_id=project_id,
            lifecycle_id=lifecycle_id,
            usage=usage,
        )
        if not quota.executable:
            raise SafetyError(
                "RECOVERY_QUOTA_NOT_EXECUTABLE",
                code="RECOVERY_QUOTA_NOT_EXECUTABLE",
                details={
                    "missing": quota.missing_dimensions,
                    "exhausted": quota.exhausted_dimensions,
                },
            )
        return proposal, candidate, parent, (policy, quota)

    def approval_mode(self, *, policy: dict[str, object], candidate: RecoveryCandidate) -> str:
        if candidate.action not in self.EXECUTABLE_ACTIONS:
            return "new_plan_approval" if candidate.changes_reviewed_plan else "not_executable"
        configured = str(policy.get("approval_mode") or "explicit_retry_approval")
        if configured == "within_original_approval":
            allowed = {str(item) for item in policy.get("within_original_approval_actions", []) if isinstance(item, str)}
            if candidate.action in allowed:
                return "within_original_approval"
        return "explicit_retry_approval"

    def approve(
        self,
        *,
        project_id: str,
        lifecycle_id: str,
        proposal_id: str,
        candidate_id: str,
        command_id: str,
        actor: str,
        expires_in_seconds: int = 900,
    ) -> RecoveryApprovalRecord:
        proposal, candidate, parent, policy_and_quota = self.authorize_candidate(
            project_id=project_id,
            lifecycle_id=lifecycle_id,
            proposal_id=proposal_id,
            candidate_id=candidate_id,
            require_execution=True,
        )
        policy, quota = policy_and_quota
        mode = self.approval_mode(policy=policy, candidate=candidate)
        if mode not in {"within_original_approval", "explicit_retry_approval"}:
            raise SafetyError("RECOVERY_APPROVAL_NEW_PLAN_REQUIRED", code="RECOVERY_APPROVAL_NEW_PLAN_REQUIRED")
        existing = [
            item
            for item in self.store.list_recovery_approvals(project_id, lifecycle_id=lifecycle_id)
            if item.command_id == command_id
        ]
        if existing:
            return existing[0]
        now = datetime.now(UTC)
        identity = stable_hash(
            {
                "proposal": proposal.recovery_proposal_hash,
                "candidate": candidate.candidate_hash,
                "command_id": command_id,
                "actor": actor,
            }
        )
        record = RecoveryApprovalRecord(
            recovery_approval_id=f"recovery_approval_{identity[:20]}",
            project_id=project_id,
            lifecycle_id=lifecycle_id,
            recovery_proposal_id=proposal.recovery_proposal_id,
            recovery_proposal_hash=proposal.recovery_proposal_hash,
            candidate_id=candidate.candidate_id,
            candidate_hash=candidate.candidate_hash,
            action=candidate.action,
            target_node_ids=candidate.target_node_ids,
            target_subject_ids=candidate.target_subject_ids,
            parent_reviewed_plan_id=proposal.bindings.reviewed_plan_id,
            parent_plan_hash=proposal.bindings.plan_hash,
            goal_contract_hash=proposal.bindings.goal_contract_hash,
            parent_execution_ticket_id=parent.execution_ticket_id,
            parent_ticket_hash=parent.canonical_hash,
            parent_run_id=proposal.bindings.run_id,
            quota_snapshot=quota,
            proposal_approval_class=candidate.approval_class,
            approval_mode=mode,
            approved_actor=actor,
            approved_at=now,
            expires_at=now + timedelta(seconds=max(1, min(expires_in_seconds, 3600))),
            command_id=command_id,
            idempotency_key=stable_hash({"command": command_id, "candidate": candidate.candidate_hash}),
            audit_id=f"recovery_audit_{uuid4().hex}",
            recovery_approval_hash="pending",
        )
        record = record.model_copy(
            update={"recovery_approval_hash": calculate_recovery_approval_hash(record)}
        )
        event = RecoveryApprovalEvent(
            event_id=f"recovery_approval_event_{uuid4().hex}",
            recovery_approval_id=record.recovery_approval_id,
            project_id=project_id,
            event_type="approved",
            occurred_at=now,
            actor=actor,
            command_id=command_id,
            audit_id=record.audit_id,
        )
        try:
            return self.store.add_recovery_approval(record, event)
        except sqlite3.IntegrityError as exc:
            raise SafetyError("RECOVERY_APPROVAL_COMMAND_REPLAYED", code="RECOVERY_APPROVAL_COMMAND_REPLAYED") from exc
        except Exception as exc:
            raise StateStoreError("RECOVERY_APPROVAL_PERSISTENCE_FAILED") from exc

    def validate_approval(
        self,
        approval_id: str,
        *,
        proposal: RecoveryProposal,
        candidate: RecoveryCandidate,
    ) -> RecoveryApprovalRecord:
        approval = self.store.get_recovery_approval(approval_id)
        if approval is None:
            raise SafetyError("RECOVERY_APPROVAL_NOT_FOUND", code="RECOVERY_APPROVAL_NOT_FOUND")
        if calculate_recovery_approval_hash(approval) != approval.recovery_approval_hash:
            raise SafetyError("RECOVERY_APPROVAL_TAMPERED", code="RECOVERY_APPROVAL_TAMPERED")
        now = datetime.now(UTC)
        if approval.status == "active" and now >= approval.expires_at:
            expired = approval.model_copy(
                update={"status": "expired", "recovery_approval_hash": "pending"}
            )
            expired = expired.model_copy(
                update={"recovery_approval_hash": calculate_recovery_approval_hash(expired)}
            )
            event = RecoveryApprovalEvent(
                event_id=f"recovery_approval_event_{uuid4().hex}",
                recovery_approval_id=approval.recovery_approval_id,
                project_id=approval.project_id,
                event_type="expired",
                occurred_at=now,
                actor="system",
                command_id=f"expire:{approval.recovery_approval_id}",
                reason_code="RECOVERY_APPROVAL_EXPIRED",
                audit_id=approval.audit_id,
            )
            try:
                self.store.update_recovery_approval(
                    expired, event, expected_status="active"
                )
            except RuntimeError:
                pass
            except Exception as exc:
                raise StateStoreError("RECOVERY_APPROVAL_EXPIRY_PERSISTENCE_FAILED") from exc
            approval = expired
        if approval.status != "active":
            raise SafetyError("RECOVERY_APPROVAL_INACTIVE", code="RECOVERY_APPROVAL_INACTIVE")
        if (
            approval.recovery_proposal_hash != proposal.recovery_proposal_hash
            or approval.candidate_hash != candidate.candidate_hash
            or approval.parent_plan_hash != proposal.bindings.plan_hash
            or approval.goal_contract_hash != proposal.bindings.goal_contract_hash
        ):
            raise SafetyError("RECOVERY_APPROVAL_BINDING_DRIFT", code="RECOVERY_APPROVAL_BINDING_DRIFT")
        return approval

    def revoke(
        self,
        approval_id: str,
        *,
        command_id: str,
        actor: str,
        reason_code: str = "RECOVERY_APPROVAL_REVOKED",
    ) -> RecoveryApprovalRecord:
        approval = self.store.get_recovery_approval(approval_id)
        if approval is None:
            raise SafetyError("RECOVERY_APPROVAL_NOT_FOUND", code="RECOVERY_APPROVAL_NOT_FOUND")
        if calculate_recovery_approval_hash(approval) != approval.recovery_approval_hash:
            raise SafetyError("RECOVERY_APPROVAL_TAMPERED", code="RECOVERY_APPROVAL_TAMPERED")
        if approval.status != "active":
            return approval
        updated = approval.model_copy(update={"status": "revoked", "recovery_approval_hash": "pending"})
        updated = updated.model_copy(
            update={"recovery_approval_hash": calculate_recovery_approval_hash(updated)}
        )
        event = RecoveryApprovalEvent(
            event_id=f"recovery_approval_event_{uuid4().hex}",
            recovery_approval_id=approval.recovery_approval_id,
            project_id=approval.project_id,
            event_type="revoked",
            occurred_at=datetime.now(UTC),
            actor=actor,
            command_id=command_id,
            reason_code=reason_code,
            audit_id=approval.audit_id,
        )
        try:
            return self.store.update_recovery_approval(updated, event, expected_status="active")
        except Exception as exc:
            raise StateStoreError("RECOVERY_APPROVAL_REVOCATION_FAILED") from exc

    def reserve_quota(
        self,
        *,
        proposal: RecoveryProposal,
        candidate: RecoveryCandidate,
        attempt_id: str,
        quota,
    ) -> RecoveryQuotaReservation:
        now = datetime.now(UTC)
        wall = min(300, quota.effective_limits["max_recovery_wall_seconds"])
        identity = stable_hash(
            {
                "proposal": proposal.recovery_proposal_hash,
                "candidate": candidate.candidate_hash,
                "attempt": attempt_id,
            }
        )
        record = RecoveryQuotaReservation(
            reservation_id=f"recovery_reservation_{identity[:20]}",
            project_id=proposal.bindings.project_id,
            lifecycle_id=proposal.bindings.lifecycle_id,
            recovery_proposal_id=proposal.recovery_proposal_id,
            candidate_id=candidate.candidate_id,
            recovery_attempt_id=attempt_id,
            action=candidate.action,
            node_ids=candidate.target_node_ids,
            subject_ids=candidate.target_subject_ids,
            reserves_replan=candidate.action in {"PARAMETER_CHANGE", "BACKEND_SWITCH", "REPLAN"},
            reserved_wall_seconds=wall,
            effective_limits=quota.effective_limits,
            created_at=now,
            reservation_hash="pending",
        )
        record = record.model_copy(
            update={"reservation_hash": calculate_quota_reservation_hash(record)}
        )
        try:
            return self.store.reserve_recovery_quota(record)
        except RuntimeError as exc:
            raise SafetyError(str(exc), code=str(exc)) from exc
        except Exception as exc:
            raise StateStoreError("RECOVERY_QUOTA_RESERVATION_FAILED") from exc

    def consume_reservation(self, reservation_id: str) -> RecoveryQuotaReservation:
        reservation = self.store.get_recovery_quota_reservation(reservation_id)
        if reservation is None or reservation.status != "reserved":
            raise SafetyError("RECOVERY_QUOTA_RESERVATION_INVALID", code="RECOVERY_QUOTA_RESERVATION_INVALID")
        if calculate_quota_reservation_hash(reservation) != reservation.reservation_hash:
            raise SafetyError("RECOVERY_QUOTA_RESERVATION_TAMPERED", code="RECOVERY_QUOTA_RESERVATION_TAMPERED")
        updated = reservation.model_copy(
            update={"status": "consumed", "consumed_at": datetime.now(UTC), "reservation_hash": "pending"}
        )
        updated = updated.model_copy(
            update={"reservation_hash": calculate_quota_reservation_hash(updated)}
        )
        return self.store.update_recovery_quota_reservation(
            updated,
            expected_status="reserved",
        )
