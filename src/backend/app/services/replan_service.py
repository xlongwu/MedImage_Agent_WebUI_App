"""Create complete, unapproved reviewed-plan candidates from recovery proposals."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from src.backend.app.core.exceptions import SafetyError, StateStoreError
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.planner.plan_validator import validate_plan
from src.backend.app.planner.reviewed_plan_store import save_reviewed_plan
from src.backend.app.schemas.goal_contract import GoalContract, GoalContractCandidate
from src.backend.app.schemas.planning import PlanningRequest
from src.backend.app.schemas.recovery_attempt import RecoveryAttemptEvent, RecoveryAttemptRecord
from src.backend.app.services.agent_orchestrator import AgentOrchestrator
from src.backend.app.services.recovery_execution_service import calculate_recovery_attempt_hash
from src.backend.app.services.recovery_policy_service import RecoveryPolicyService


class ReplanService:
    ACTIONS = {"PARAMETER_CHANGE", "BACKEND_SWITCH", "REPLAN"}

    def __init__(self, store) -> None:
        self.store = store
        self.policy = RecoveryPolicyService(store)

    @staticmethod
    def _apply(plan: dict[str, object], change) -> dict[str, object]:
        updated = deepcopy(plan)
        nodes = [node for node in updated.get("nodes", []) if isinstance(node, dict)]
        by_id = {str(node.get("id")): node for node in nodes if node.get("id")}
        for node_id, patch in change.parameter_patch.items():
            if node_id not in by_id:
                raise SafetyError("REPLAN_PATCH_NODE_MISSING", code="REPLAN_PATCH_NODE_MISSING")
            by_id[node_id]["params"] = {**dict(by_id[node_id].get("params") or {}), **patch}
        for node_id, backend in change.backend_patch.items():
            if node_id not in by_id:
                raise SafetyError("REPLAN_PATCH_NODE_MISSING", code="REPLAN_PATCH_NODE_MISSING")
            by_id[node_id]["backend"] = backend
        if change.replacement_node_ids is not None:
            requested = set(change.replacement_node_ids)
            if not requested.issubset(by_id):
                raise SafetyError(
                    "REPLAN_FULL_NODE_SPEC_REQUIRED",
                    code="REPLAN_FULL_NODE_SPEC_REQUIRED",
                )
            nodes = [node for node in nodes if str(node.get("id")) in requested]
            by_id = {str(node["id"]): node for node in nodes}
        for node_id, dependencies in change.dag_patch.items():
            if node_id not in by_id or not set(dependencies).issubset(by_id):
                raise SafetyError("REPLAN_DAG_PATCH_INVALID", code="REPLAN_DAG_PATCH_INVALID")
            by_id[node_id]["depends_on"] = list(dependencies)
        if any(
            value is not None
            for value in (
                change.input_roots,
                change.output_roots,
                change.readonly_roots,
                change.goal_contract_hash,
                change.approval_summary_hash,
                change.allowlist_hash,
            )
        ):
            raise SafetyError(
                "REPLAN_FULL_REVIEWED_PLAN_REQUIRED",
                code="REPLAN_FULL_REVIEWED_PLAN_REQUIRED",
            )
        metadata = dict(updated.get("metadata") or {})
        for key, value in (
            ("subject_ids", change.subject_scope),
            ("session_ids", change.session_scope),
            ("output_scope", change.output_scope),
        ):
            if value is not None:
                metadata[key] = list(value)
        updated["metadata"] = metadata
        updated["nodes"] = nodes
        return updated

    def create_replan(
        self,
        *,
        project_id: str,
        lifecycle_id: str,
        proposal_id: str,
        candidate_id: str,
        command_id: str,
        actor: str,
    ):
        orchestrator = AgentOrchestrator(self.store)
        lifecycle = orchestrator.get(
            project_id=project_id,
            lifecycle_id=lifecycle_id,
        )
        existing = [
            item
            for item in self.store.list_recovery_attempts(
                project_id, lifecycle_id=lifecycle_id
            )
            if item.command_id == command_id
            and item.recovery_proposal_id == proposal_id
            and item.candidate_id == candidate_id
        ]
        if len(existing) == 1 and existing[0].status == "REPLAN_CREATED":
            reviewed = self.store.get_reviewed_plan(lifecycle.reviewed_plan_id or "")
            lineage = reviewed.payload.get("lineage") if reviewed and isinstance(reviewed.payload, dict) else None
            if (
                reviewed is None
                or not isinstance(lineage, dict)
                or lineage.get("recovery_proposal_id") != proposal_id
                or lineage.get("recovery_candidate_id") != candidate_id
            ):
                raise StateStoreError("REPLAN_IDEMPOTENCY_BINDING_DRIFT")
            return lifecycle, reviewed, existing[0]
        if existing:
            raise StateStoreError("REPLAN_IDEMPOTENCY_COLLISION")
        if lifecycle.state != "RECOVERY_PROPOSED":
            raise SafetyError("REPLAN_STATE_INVALID", code="REPLAN_STATE_INVALID")
        proposal, candidate, parent_ticket, policy_and_quota = self.policy.authorize_candidate(
            project_id=project_id,
            lifecycle_id=lifecycle_id,
            proposal_id=proposal_id,
            candidate_id=candidate_id,
            require_execution=False,
        )
        if candidate.action not in self.ACTIONS or not candidate.changes_reviewed_plan:
            raise SafetyError("REPLAN_ACTION_INVALID", code="REPLAN_ACTION_INVALID")
        if candidate.change_request is None:
            raise SafetyError("REPLAN_FULL_PATCH_REQUIRED", code="REPLAN_FULL_PATCH_REQUIRED")
        parent_plan = self.store.get_reviewed_plan(proposal.bindings.reviewed_plan_id)
        if parent_plan is None or parent_plan.plan_hash != proposal.bindings.plan_hash:
            raise SafetyError("REPLAN_PARENT_PLAN_INVALID", code="REPLAN_PARENT_PLAN_INVALID")
        plan = parent_plan.payload.get("plan") if isinstance(parent_plan.payload, dict) else None
        contract_payload = parent_plan.payload.get("goal_contract") if isinstance(parent_plan.payload, dict) else None
        if not isinstance(plan, dict) or not isinstance(contract_payload, dict):
            raise SafetyError("REPLAN_PARENT_CONTRACT_REQUIRED", code="REPLAN_PARENT_CONTRACT_REQUIRED")
        candidate_plan = self._apply(plan, candidate.change_request)
        validation = validate_plan(candidate_plan)
        if not validation.ok or not validation.normalized_plan:
            raise SafetyError(
                "REPLAN_VALIDATION_FAILED",
                code="REPLAN_VALIDATION_FAILED",
                details={"errors": [item.message for item in validation.errors]},
            )
        goal = GoalContract(**contract_payload)
        goal_candidate = GoalContractCandidate(
            schema_version=goal.schema_version,
            goal_text=goal.goal_text,
            goal_kind=goal.goal_kind,
            scope=goal.scope,
            criteria=goal.criteria,
            minimum_capability_level=goal.minimum_capability_level,
            allowed_limitation_flags=goal.allowed_limitation_flags,
            forbidden_limitation_flags=goal.forbidden_limitation_flags,
            evaluation_policy_version=goal.evaluation_policy_version,
            builder_source="recovery_replan_review_candidate",
            warnings=(*goal.warnings, "RECOVERY_REPLAN_REQUIRES_NEW_APPROVAL"),
        )
        _, quota = policy_and_quota
        identity = stable_hash(
            {
                "proposal": proposal.recovery_proposal_hash,
                "candidate": candidate.candidate_hash,
                "command": command_id,
            }
        )
        attempt_id = f"recovery_replan_{identity[:20]}"
        try:
            reservation = self.policy.reserve_quota(
                proposal=proposal,
                candidate=candidate,
                attempt_id=attempt_id,
                quota=quota,
            )
            reservation = self.policy.consume_reservation(reservation.reservation_id)
        except Exception as exc:
            self._handoff(
                orchestrator,
                lifecycle,
                command_id,
                actor,
                str(getattr(exc, "code", None) or "REPLAN_QUOTA_RESERVATION_FAILED"),
            )
            raise
        try:
            raw_memory_context = lifecycle.command_context.get("memory_context")
            memory_context = raw_memory_context if isinstance(raw_memory_context, dict) else {}
            parent_request = parent_plan.payload.get("planning_request")
            if not isinstance(parent_request, dict) or not isinstance(parent_request.get("model_profile_hash"), str):
                raise SafetyError("AGENT_INV_MODEL_PROFILE_MISSING", code="AGENT_INV_MODEL_PROFILE_MISSING")
            planning_request = PlanningRequest(
                project_id=project_id,
                lifecycle_id=lifecycle_id,
                goal=goal.goal_text,
                project_config_path=parent_plan.project_config_path,
                evidence_snapshot_hash=str(
                    lifecycle.evidence_snapshot_hash
                    or parent_plan.evidence_snapshot_hash
                    or "recovery-evidence-unavailable"
                ),
                science_answers={
                    str(key): str(value)
                    for key, value in dict(lifecycle.command_context.get("science_answers") or {}).items()
                },
                memory_context_hash=str(memory_context.get("context_hash") or "") or None,
                memory_context_refs=tuple(memory_context.get("evidence_refs") or ()),
                parent_reviewed_plan_id=parent_plan.reviewed_plan_id,
                parent_plan_hash=parent_plan.plan_hash,
                revision_reason="recovery_replan",
                recovery_proposal_hash=proposal.recovery_proposal_hash,
                recovery_candidate_hash=candidate.candidate_hash,
                provider_ref="recovery_replan_service",
                prompt_version="recovery-replan-v1",
                model_profile_hash=parent_request["model_profile_hash"],
            )
            reviewed = save_reviewed_plan(
                project_id=project_id,
                project_config_path=parent_plan.project_config_path,
                plan=validation.normalized_plan,
                validation=validation.to_dict(),
                goal=goal.goal_text,
                provider="recovery_replan_service",
                status="NEEDS_APPROVAL",
                warnings=["RECOVERY_REPLAN_REQUIRES_NEW_APPROVAL"],
                goal_contract_candidate=goal_candidate,
                reviewed_actor=actor,
                lineage={
                    "parent_reviewed_plan_id": parent_plan.reviewed_plan_id,
                    "parent_plan_hash": parent_plan.plan_hash,
                    "recovery_proposal_id": proposal.recovery_proposal_id,
                    "recovery_candidate_id": candidate.candidate_id,
                    "recovery_action": candidate.action,
                    "quota_reservation_id": reservation.reservation_id,
                },
                planning_request=planning_request,
                store=self.store,
            )
            if not reviewed.plan_path or not Path(reviewed.plan_path).is_file():
                raise StateStoreError("REPLAN_SNAPSHOT_PERSISTENCE_FAILED")
        except Exception as exc:
            self._handoff(
                orchestrator,
                lifecycle,
                command_id,
                actor,
                "REPLAN_PERSISTENCE_FAILED",
            )
            raise StateStoreError("REPLAN_PERSISTENCE_FAILED") from exc
        now = datetime.now(UTC)
        attempt = RecoveryAttemptRecord(
            recovery_attempt_id=attempt_id,
            project_id=project_id,
            lifecycle_id=lifecycle_id,
            recovery_proposal_id=proposal.recovery_proposal_id,
            recovery_proposal_hash=proposal.recovery_proposal_hash,
            candidate_id=candidate.candidate_id,
            candidate_hash=candidate.candidate_hash,
            action=candidate.action,
            target_node_ids=candidate.target_node_ids,
            target_subject_ids=candidate.target_subject_ids,
            parent_reviewed_plan_id=parent_plan.reviewed_plan_id,
            parent_plan_hash=parent_plan.plan_hash,
            goal_contract_hash=goal.goal_contract_hash,
            parent_execution_ticket_id=parent_ticket.execution_ticket_id,
            parent_ticket_hash=parent_ticket.canonical_hash,
            parent_run_id=proposal.bindings.run_id,
            quota_reservation_id=reservation.reservation_id,
            recovery_run_id=f"replan_{identity[:16]}",
            output_namespace="",
            status="REPLAN_CREATED",
            audit_id=f"replan_audit_{uuid4().hex}",
            command_id=command_id,
            idempotency_key=stable_hash({"replan": identity}),
            created_at=now,
            updated_at=now,
            recovery_attempt_hash="pending",
        )
        attempt = attempt.model_copy(
            update={"recovery_attempt_hash": calculate_recovery_attempt_hash(attempt)}
        )
        event = RecoveryAttemptEvent(
            event_id=f"recovery_attempt_event_{uuid4().hex}",
            recovery_attempt_id=attempt.recovery_attempt_id,
            project_id=project_id,
            lifecycle_id=lifecycle_id,
            command_id=command_id,
            event_type="replan_created",
            from_status=None,
            to_status="REPLAN_CREATED",
            occurred_at=now,
            audit_id=attempt.audit_id,
            attempt_hash=attempt.recovery_attempt_hash,
        )
        try:
            self.store.create_recovery_attempt(attempt, event)
        except Exception as exc:
            self._handoff(
                orchestrator,
                lifecycle,
                command_id,
                actor,
                "REPLAN_ATTEMPT_PERSISTENCE_FAILED",
            )
            raise StateStoreError("REPLAN_ATTEMPT_PERSISTENCE_FAILED") from exc
        goal_payload = reviewed.payload.get("goal_contract") or {}
        drafted = orchestrator.transition(
            project_id=project_id,
            lifecycle_id=lifecycle_id,
            to_state="PLAN_DRAFTED",
            command_id=f"{command_id}:plan-drafted",
            actor=actor,
            source_command="recovery_replan_created",
            updates={
                "parent_execution_ticket_id": lifecycle.execution_ticket_id,
                "parent_run_id": lifecycle.run_id,
                "reviewed_plan_id": reviewed.reviewed_plan_id,
                "execution_ticket_id": None,
                "audit_id": None,
                "run_id": None,
                "goal_contract_id": goal_payload.get("goal_contract_id"),
                "goal_contract_hash": goal_payload.get("goal_contract_hash"),
                "observation_id": None,
                "observation_summary": None,
                "goal_evaluation_id": None,
                "goal_evaluation_summary": None,
                "diagnosis_id": None,
                "diagnosis_summary": None,
                "recovery_proposal_id": None,
                "recovery_proposal_summary": None,
                "recovery_approval_id": None,
                "recovery_attempt_id": attempt.recovery_attempt_id,
            },
        )
        raw_summary = reviewed.payload.get("approval_envelope")
        if not isinstance(raw_summary, dict) or not str(raw_summary.get("summary_hash") or ""):
            self._handoff(
                orchestrator, drafted, command_id, actor, "REPLAN_APPROVAL_SUMMARY_MISSING"
            )
            raise StateStoreError("REPLAN_APPROVAL_SUMMARY_MISSING")
        updated = orchestrator.transition(
            project_id=project_id,
            lifecycle_id=lifecycle_id,
            to_state="PLAN_VALIDATED",
            command_id=f"{command_id}:validated",
            actor=actor,
            source_command="recovery_replan_validated",
        )
        updated = orchestrator.transition(
            project_id=project_id,
            lifecycle_id=lifecycle_id,
            to_state="WAITING_FOR_APPROVAL",
            command_id=f"{command_id}:approval",
            actor=actor,
            source_command="recovery_replan_approval_summary_ready",
            details={"approval_summary_hash": str(raw_summary["summary_hash"])},
        )
        return updated, reviewed, attempt

    @staticmethod
    def _handoff(orchestrator, lifecycle, command_id: str, actor: str, reason: str) -> None:
        if lifecycle.state == "HUMAN_HANDOFF":
            return
        try:
            orchestrator.transition(
                project_id=lifecycle.project_id,
                lifecycle_id=lifecycle.lifecycle_id,
                to_state="HUMAN_HANDOFF",
                command_id=f"{command_id}:human-handoff",
                actor=actor,
                source_command="replan_fail_closed",
                reason=reason,
            )
        except Exception as exc:
            raise StateStoreError("REPLAN_HANDOFF_PERSISTENCE_FAILED") from exc
