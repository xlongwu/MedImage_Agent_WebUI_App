"""Controlled child-ticket recovery execution and evidence-loop coordination."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import yaml

from src.backend.app.core.exceptions import SafetyError, StateStoreError
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.runtime.execution_gateway import ExecutionGateway
from src.backend.app.runtime.node_contract_registry import get_node_contract
from src.backend.app.schemas.desktop import RunLinkRecord
from src.backend.app.schemas.recovery_attempt import (
    RecoveryAttemptEvent,
    RecoveryAttemptRecord,
)
from src.backend.app.services.agent_orchestrator import AgentOrchestrator
from src.backend.app.services.execution_ticket_service import ExecutionTicketService
from src.backend.app.services.recovery_policy_service import RecoveryPolicyService

_ATTEMPT_TRANSITIONS = {
    "PROPOSED": {"APPROVED", "HANDOFF"},
    "APPROVED": {"TICKET_ISSUED", "HANDOFF"},
    "TICKET_ISSUED": {"RUNNING", "HANDOFF"},
    "RUNNING": {"EXECUTION_SUCCEEDED", "EXECUTION_FAILED", "HANDOFF"},
    "EXECUTION_SUCCEEDED": {"OBSERVED", "HANDOFF"},
    "EXECUTION_FAILED": {"OBSERVED", "HANDOFF"},
    "OBSERVED": {"EVALUATED", "HANDOFF"},
    "EVALUATED": set(),
    "REPLAN_CREATED": set(),
    "HANDOFF": set(),
}


def calculate_recovery_attempt_hash(
    value: RecoveryAttemptRecord | dict[str, object],
) -> str:
    payload = (
        value.model_dump(mode="json") if isinstance(value, RecoveryAttemptRecord) else dict(value)
    )
    payload.pop("recovery_attempt_hash", None)
    return stable_hash(payload)


class RecoveryExecutionService:
    def __init__(self, store) -> None:
        self.store = store
        self.policy = RecoveryPolicyService(store)
        self.ticket_service = ExecutionTicketService(store)

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
    ):
        orchestrator = AgentOrchestrator(self.store)
        lifecycle = orchestrator.get(project_id=project_id, lifecycle_id=lifecycle_id)
        if lifecycle.state != "RECOVERY_PROPOSED" or lifecycle.recovery_proposal_id != proposal_id:
            existing = [
                item
                for item in self.store.list_recovery_approvals(
                    project_id, lifecycle_id=lifecycle_id
                )
                if item.command_id == command_id
                and item.recovery_proposal_id == proposal_id
                and item.candidate_id == candidate_id
            ]
            if len(existing) == 1:
                approval = existing[0]
                if (
                    lifecycle.state == "WAITING_FOR_RECOVERY_APPROVAL"
                    and lifecycle.recovery_approval_id == approval.recovery_approval_id
                ):
                    lifecycle = orchestrator.transition(
                        project_id=project_id,
                        lifecycle_id=lifecycle_id,
                        to_state="RECOVERY_READY",
                        command_id=command_id,
                        actor=actor,
                        source_command="recovery_approved",
                        updates={"recovery_approval_id": approval.recovery_approval_id},
                        details={"approval_mode": approval.approval_mode},
                    )
                return lifecycle, approval
            raise SafetyError(
                "RECOVERY_APPROVAL_STATE_INVALID", code="RECOVERY_APPROVAL_STATE_INVALID"
            )
        approval = self.policy.approve(
            project_id=project_id,
            lifecycle_id=lifecycle_id,
            proposal_id=proposal_id,
            candidate_id=candidate_id,
            command_id=command_id,
            actor=actor,
            expires_in_seconds=expires_in_seconds,
        )
        _waiting = orchestrator.transition(
            project_id=project_id,
            lifecycle_id=lifecycle_id,
            to_state="WAITING_FOR_RECOVERY_APPROVAL",
            command_id=f"{command_id}:waiting",
            actor=actor,
            source_command="recovery_approval_requested",
            updates={"recovery_approval_id": approval.recovery_approval_id},
        )
        ready = orchestrator.transition(
            project_id=project_id,
            lifecycle_id=lifecycle_id,
            to_state="RECOVERY_READY",
            command_id=command_id,
            actor=actor,
            source_command="recovery_approved",
            updates={"recovery_approval_id": approval.recovery_approval_id},
            details={"approval_mode": approval.approval_mode},
        )
        return ready, approval

    def _attempt_event(
        self,
        record: RecoveryAttemptRecord,
        *,
        command_id: str,
        event_type: str,
        from_status: str | None,
        reason_code: str | None = None,
    ) -> RecoveryAttemptEvent:
        return RecoveryAttemptEvent(
            event_id=f"recovery_attempt_event_{uuid4().hex}",
            recovery_attempt_id=record.recovery_attempt_id,
            project_id=record.project_id,
            lifecycle_id=record.lifecycle_id,
            command_id=command_id,
            event_type=event_type,
            from_status=from_status,
            to_status=record.status,
            occurred_at=datetime.now(UTC),
            audit_id=record.audit_id,
            reason_code=reason_code,
            attempt_hash=record.recovery_attempt_hash,
        )

    def _transition_attempt(
        self,
        record: RecoveryAttemptRecord,
        to_status: str,
        *,
        command_id: str,
        reason_code: str | None = None,
        updates: dict[str, object] | None = None,
    ) -> RecoveryAttemptRecord:
        if to_status not in _ATTEMPT_TRANSITIONS[record.status]:
            raise SafetyError(
                "RECOVERY_ATTEMPT_TRANSITION_INVALID", code="RECOVERY_ATTEMPT_TRANSITION_INVALID"
            )
        updated = record.model_copy(
            update={
                **(updates or {}),
                "status": to_status,
                "updated_at": datetime.now(UTC),
                "recovery_attempt_hash": "pending",
            }
        )
        updated = updated.model_copy(
            update={"recovery_attempt_hash": calculate_recovery_attempt_hash(updated)}
        )
        event = self._attempt_event(
            updated,
            command_id=command_id,
            event_type=to_status.lower(),
            from_status=record.status,
            reason_code=reason_code,
        )
        try:
            return self.store.transition_recovery_attempt(
                updated,
                event,
                expected_status=record.status,
            )
        except RuntimeError as exc:
            raise StateStoreError("RECOVERY_ATTEMPT_CONCURRENT_TRANSITION") from exc

    def _create_attempt(
        self,
        *,
        proposal,
        candidate,
        approval,
        command_id: str,
        idempotency_key: str,
    ) -> RecoveryAttemptRecord:
        identity = stable_hash(
            {
                "project": proposal.bindings.project_id,
                "lifecycle": proposal.bindings.lifecycle_id,
                "proposal": proposal.recovery_proposal_hash,
                "candidate": candidate.candidate_hash,
                "idempotency": idempotency_key,
            }
        )
        attempt_id = f"recovery_attempt_{identity[:20]}"
        now = datetime.now(UTC)
        record = RecoveryAttemptRecord(
            recovery_attempt_id=attempt_id,
            project_id=proposal.bindings.project_id,
            lifecycle_id=proposal.bindings.lifecycle_id,
            recovery_proposal_id=proposal.recovery_proposal_id,
            recovery_proposal_hash=proposal.recovery_proposal_hash,
            candidate_id=candidate.candidate_id,
            candidate_hash=candidate.candidate_hash,
            action=candidate.action,
            target_node_ids=candidate.target_node_ids,
            target_subject_ids=candidate.target_subject_ids,
            checkpoint_id=candidate.checkpoint_id,
            parent_reviewed_plan_id=proposal.bindings.reviewed_plan_id,
            parent_plan_hash=proposal.bindings.plan_hash,
            goal_contract_hash=proposal.bindings.goal_contract_hash,
            parent_execution_ticket_id=proposal.bindings.execution_ticket_id,
            parent_ticket_hash=approval.parent_ticket_hash,
            parent_run_id=proposal.bindings.run_id,
            recovery_approval_id=approval.recovery_approval_id,
            recovery_run_id=f"recovery_{identity[:16]}",
            output_namespace=f"recovery_attempts/{attempt_id}",
            audit_id=approval.audit_id,
            command_id=command_id,
            idempotency_key=idempotency_key,
            created_at=now,
            updated_at=now,
            recovery_attempt_hash="pending",
        )
        record = record.model_copy(
            update={"recovery_attempt_hash": calculate_recovery_attempt_hash(record)}
        )
        try:
            return self.store.create_recovery_attempt(
                record,
                self._attempt_event(
                    record,
                    command_id=f"{command_id}:proposed",
                    event_type="proposed",
                    from_status=None,
                ),
            )
        except sqlite3.IntegrityError:
            existing = self.store.get_recovery_attempt_by_idempotency(idempotency_key)
            if existing is not None:
                return existing
            raise

    @staticmethod
    def _safe_child_paths(parent, attempt: RecoveryAttemptRecord) -> tuple[Path, Path, Path]:
        if not parent.output_roots:
            raise SafetyError(
                "RECOVERY_PARENT_OUTPUT_ROOT_REQUIRED", code="RECOVERY_PARENT_OUTPUT_ROOT_REQUIRED"
            )
        root = (
            Path(parent.output_roots[0]) / "recovery_attempts" / attempt.recovery_attempt_id
        ).resolve()
        parent_root = Path(parent.output_roots[0]).resolve()
        try:
            root.relative_to(parent_root)
        except ValueError as exc:
            raise SafetyError(
                "RECOVERY_OUTPUT_PATH_ESCAPE", code="RECOVERY_OUTPUT_PATH_ESCAPE"
            ) from exc
        return root, root / "control" / "project_config.yaml", root / "control" / "pipeline.yaml"

    @staticmethod
    def _write_child_files(
        parent, candidate, attempt, root: Path, config_path: Path, pipeline_path: Path
    ) -> None:
        if root.exists():
            raise SafetyError(
                "RECOVERY_OUTPUT_NAMESPACE_COLLISION", code="RECOVERY_OUTPUT_NAMESPACE_COLLISION"
            )
        source_config = Path(parent.project_config_path)
        source_pipeline = Path(parent.pipeline_path)
        config = yaml.safe_load(source_config.read_text(encoding="utf-8")) or {}
        pipeline = yaml.safe_load(source_pipeline.read_text(encoding="utf-8")) or {}
        nodes = [
            deepcopy(node)
            for node in pipeline.get("nodes", [])
            if isinstance(node, dict) and str(node.get("id")) in candidate.target_node_ids
        ]
        if {str(node.get("id")) for node in nodes} != set(candidate.target_node_ids):
            raise SafetyError(
                "RECOVERY_PIPELINE_SCOPE_MISSING", code="RECOVERY_PIPELINE_SCOPE_MISSING"
            )
        target_nodes = set(candidate.target_node_ids)
        for node in nodes:
            node["depends_on"] = [
                dep for dep in (node.get("depends_on") or []) if dep in target_nodes
            ]
            contract = get_node_contract(str(node["id"]))
            for key, rule in contract.parameter_schema.items():
                if rule.path_access == "write" and node.get("params", {}).get(key):
                    raise SafetyError(
                        "RECOVERY_EXPLICIT_WRITE_PATH_NOT_ISOLATABLE",
                        code="RECOVERY_EXPLICIT_WRITE_PATH_NOT_ISOLATABLE",
                    )
            if node.get("outputs"):
                raise SafetyError(
                    "RECOVERY_EXPLICIT_OUTPUT_NOT_ISOLATABLE",
                    code="RECOVERY_EXPLICIT_OUTPUT_NOT_ISOLATABLE",
                )
        pipeline["nodes"] = nodes
        execution = dict(pipeline.get("execution") or {})
        execution["run_id"] = attempt.recovery_run_id
        execution["recovery_attempt_id"] = attempt.recovery_attempt_id
        execution["parent_run_id"] = attempt.parent_run_id
        pipeline["execution"] = execution
        runtime = dict(config.get("runtime") or {})
        runtime.update(
            work_dir=str(root / "work"),
            log_dir=str(root / "logs"),
            derivatives_dir=str(root / "derivatives"),
        )
        config["runtime"] = runtime
        root.mkdir(parents=True, exist_ok=False)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_tmp = config_path.with_suffix(".tmp")
        pipeline_tmp = pipeline_path.with_suffix(".tmp")
        config_tmp.write_text(yaml.safe_dump(config, sort_keys=True), encoding="utf-8")
        pipeline_tmp.write_text(yaml.safe_dump(pipeline, sort_keys=False), encoding="utf-8")
        config_tmp.replace(config_path)
        pipeline_tmp.replace(pipeline_path)

    def execute(
        self,
        *,
        project_id: str,
        lifecycle_id: str,
        proposal_id: str,
        candidate_id: str,
        command_id: str,
        actor: str,
        executor: Callable[..., dict[str, object]] | None = None,
        close_loop: bool = True,
    ) -> tuple[object, RecoveryAttemptRecord, dict[str, object] | None]:
        orchestrator = AgentOrchestrator(self.store)
        lifecycle = orchestrator.get(project_id=project_id, lifecycle_id=lifecycle_id)
        existing = [
            item
            for item in self.store.list_recovery_attempts(project_id, lifecycle_id=lifecycle_id)
            if item.command_id == command_id
            and item.recovery_proposal_id == proposal_id
            and item.candidate_id == candidate_id
        ]
        if len(existing) == 1:
            previous = existing[0]
            if previous.status not in {"EVALUATED", "HANDOFF", "REPLAN_CREATED"}:
                previous = self._handoff(
                    previous,
                    command_id=f"{command_id}:replay-handoff",
                    reason="RECOVERY_PARTIAL_ATTEMPT_REQUIRES_RECONCILIATION",
                )
                lifecycle = self._lifecycle_handoff(
                    orchestrator,
                    lifecycle,
                    command_id,
                    actor,
                    previous.handoff_reasons,
                )
            return lifecycle, previous, None
        if len(existing) > 1:
            raise StateStoreError("RECOVERY_IDEMPOTENCY_COLLISION")
        if lifecycle.state != "RECOVERY_READY" or lifecycle.recovery_approval_id is None:
            raise SafetyError(
                "RECOVERY_EXECUTION_STATE_INVALID", code="RECOVERY_EXECUTION_STATE_INVALID"
            )
        proposal, candidate, parent, policy_and_quota = self.policy.authorize_candidate(
            project_id=project_id,
            lifecycle_id=lifecycle_id,
            proposal_id=proposal_id,
            candidate_id=candidate_id,
            require_execution=True,
        )
        approval = self.policy.validate_approval(
            lifecycle.recovery_approval_id,
            proposal=proposal,
            candidate=candidate,
        )
        _, quota = policy_and_quota
        idempotency_key = stable_hash(
            {
                "project": project_id,
                "lifecycle": lifecycle_id,
                "proposal": proposal.recovery_proposal_hash,
                "candidate": candidate.candidate_hash,
                "command": command_id,
            }
        )
        attempt = self._create_attempt(
            proposal=proposal,
            candidate=candidate,
            approval=approval,
            command_id=command_id,
            idempotency_key=idempotency_key,
        )
        child = (
            self.store.get_execution_ticket(attempt.child_execution_ticket_id)
            if attempt.child_execution_ticket_id
            else None
        )
        if (
            child is not None
            and child.status == "consumed"
            and attempt.status in {"TICKET_ISSUED", "RUNNING"}
        ):
            attempt = self._handoff(
                attempt,
                command_id=f"{command_id}:crash-handoff",
                reason="DISPATCH_OUTCOME_UNKNOWN_AFTER_RESTART",
            )
            lifecycle = self._lifecycle_handoff(
                orchestrator, lifecycle, command_id, actor, attempt.handoff_reasons
            )
            return lifecycle, attempt, None
        if attempt.status == "EXECUTION_SUCCEEDED" and close_loop:
            return self._close_loop(orchestrator, attempt, command_id, actor, None)
        if attempt.status == "PROPOSED":
            try:
                reservation = self.policy.reserve_quota(
                    proposal=proposal,
                    candidate=candidate,
                    attempt_id=attempt.recovery_attempt_id,
                    quota=quota,
                )
            except Exception as exc:
                attempt = self._handoff(
                    attempt,
                    command_id=f"{command_id}:quota-handoff",
                    reason=str(getattr(exc, "code", None) or exc),
                )
                lifecycle = self._lifecycle_handoff(
                    orchestrator, lifecycle, command_id, actor, attempt.handoff_reasons
                )
                return lifecycle, attempt, None
            attempt = self._transition_attempt(
                attempt,
                "APPROVED",
                command_id=f"{command_id}:reserved",
                updates={"quota_reservation_id": reservation.reservation_id},
            )
        else:
            reservation = self.store.get_recovery_quota_reservation(
                attempt.quota_reservation_id or ""
            )
            if reservation is None:
                raise SafetyError(
                    "RECOVERY_RESERVATION_NOT_FOUND", code="RECOVERY_RESERVATION_NOT_FOUND"
                )
        root, config_path, pipeline_path = self._safe_child_paths(parent, attempt)
        if attempt.status == "APPROVED":
            try:
                self._write_child_files(
                    parent, candidate, attempt, root, config_path, pipeline_path
                )
                child = self.ticket_service.issue_recovery_child(
                    parent=parent,
                    proposal=proposal,
                    candidate=candidate,
                    approval=approval,
                    attempt=attempt,
                    reservation=reservation,
                    project_config_path=str(config_path),
                    pipeline_path=str(pipeline_path),
                    output_root=str(root),
                    input_roots=tuple(sorted(set(parent.input_roots) | set(parent.output_roots))),
                )
                attempt = self._transition_attempt(
                    attempt,
                    "TICKET_ISSUED",
                    command_id=f"{command_id}:ticket-issued",
                    updates={
                        "child_execution_ticket_id": child.execution_ticket_id,
                        "child_ticket_hash": child.canonical_hash,
                    },
                )
            except Exception as exc:
                attempt = self._handoff(
                    attempt,
                    command_id=f"{command_id}:issue-handoff",
                    reason=str(getattr(exc, "code", None) or exc),
                )
                lifecycle = self._lifecycle_handoff(
                    orchestrator, lifecycle, command_id, actor, attempt.handoff_reasons
                )
                return lifecycle, attempt, None
        assert child is not None
        if lifecycle.state == "RECOVERY_READY":
            lifecycle = orchestrator.transition(
                project_id=project_id,
                lifecycle_id=lifecycle_id,
                to_state="RECOVERING",
                command_id=f"{command_id}:recovering",
                actor=actor,
                source_command="recovery_dispatch_ready",
                updates={
                    "parent_execution_ticket_id": lifecycle.execution_ticket_id,
                    "parent_run_id": lifecycle.run_id,
                    "execution_ticket_id": child.execution_ticket_id,
                    "run_id": attempt.recovery_run_id,
                    "audit_id": child.audit_id,
                    "recovery_attempt_id": attempt.recovery_attempt_id,
                },
            )
        if attempt.status == "TICKET_ISSUED":
            attempt = self._transition_attempt(
                attempt,
                "RUNNING",
                command_id=f"{command_id}:running",
                updates={"dispatch_started_at": datetime.now(UTC)},
            )
        runner_started = False
        if executor is None:
            from src.backend.app.runtime import execution_gateway as execution_gateway_module

            selected_executor = execution_gateway_module.PIPELINE_EXECUTOR
        else:
            selected_executor = executor

        def tracked_executor(**kwargs):
            nonlocal runner_started
            runner_started = True
            return selected_executor(**kwargs)

        try:
            result, consumed = ExecutionGateway(self.ticket_service).dispatch(
                execution_ticket_id=child.execution_ticket_id,
                project_id=child.project_id,
                reviewed_plan_id=child.reviewed_plan_id,
                plan_hash=child.plan_hash,
                goal_contract_hash=child.goal_contract_hash,
                evaluation_policy_version=child.evaluation_policy_version,
                approval_summary_hash=child.approval_summary_hash,
                memory_context_hash=child.memory_context_hash,
                scope_hash=child.scope_hash,
                normalized_params_hash=child.normalized_params_hash,
                contract_versions=child.contract_versions,
                project_config_path=child.project_config_path,
                pipeline_path=child.pipeline_path,
                command_id=command_id,
                run_id=child.recovery_run_id or attempt.recovery_run_id,
                executor=tracked_executor,
            )
        except Exception as exc:
            refreshed_child = self.store.get_execution_ticket(child.execution_ticket_id)
            error_code = str(getattr(exc, "code", None) or "RECOVERY_DISPATCH_FAILED")
            if (
                not runner_started
                or refreshed_child is None
                or refreshed_child.status != "consumed"
            ):
                attempt = self._handoff(
                    attempt,
                    command_id=f"{command_id}:dispatch-handoff",
                    reason="RECOVERY_DISPATCH_REJECTED_BEFORE_RUNNER",
                )
                lifecycle = self._lifecycle_handoff(
                    orchestrator, lifecycle, command_id, actor, attempt.handoff_reasons
                )
                return lifecycle, attempt, None
            attempt = self._transition_attempt(
                attempt,
                "EXECUTION_FAILED",
                command_id=f"{command_id}:execution-failed",
                reason_code=error_code,
                updates={
                    "execution_status": "FAILED",
                    "error_codes": (error_code,),
                    "dispatch_completed_at": datetime.now(UTC),
                },
            )
            failure_result = {
                "status": "FAILED",
                "error_code": error_code,
            }
            try:
                self._register_run_link(
                    attempt, refreshed_child, failure_result, config_path, pipeline_path
                )
                if close_loop:
                    return self._close_loop(
                        orchestrator, attempt, command_id, actor, failure_result
                    )
                return lifecycle, attempt, failure_result
            except Exception as persistence_exc:
                attempt = self._handoff(
                    attempt,
                    command_id=f"{command_id}:persistence-handoff",
                    reason=str(
                        getattr(persistence_exc, "code", None)
                        or "RECOVERY_POST_DISPATCH_PERSISTENCE_FAILED"
                    ),
                )
                lifecycle = self._lifecycle_handoff(
                    orchestrator, lifecycle, command_id, actor, attempt.handoff_reasons
                )
                return lifecycle, attempt, failure_result
        execution_status = str(result.get("status") or "UNKNOWN")
        attempt = self._transition_attempt(
            attempt,
            "EXECUTION_SUCCEEDED"
            if execution_status in {"SUCCESS", "COMPLETED"}
            else "EXECUTION_FAILED",
            command_id=f"{command_id}:execution-complete",
            updates={
                "execution_status": execution_status,
                "dispatch_completed_at": datetime.now(UTC),
            },
        )
        try:
            self._register_run_link(attempt, consumed, result, config_path, pipeline_path)
            if not close_loop:
                return lifecycle, attempt, result
            return self._close_loop(orchestrator, attempt, command_id, actor, result)
        except Exception as exc:
            attempt = self._handoff(
                attempt,
                command_id=f"{command_id}:persistence-handoff",
                reason=str(
                    getattr(exc, "code", None) or "RECOVERY_POST_DISPATCH_PERSISTENCE_FAILED"
                ),
            )
            lifecycle = self._lifecycle_handoff(
                orchestrator, lifecycle, command_id, actor, attempt.handoff_reasons
            )
            return lifecycle, attempt, result

    def _register_run_link(
        self, attempt, child, result, config_path: Path, pipeline_path: Path
    ) -> None:
        now = datetime.now(UTC).isoformat()
        run_started = (attempt.dispatch_started_at or attempt.created_at).isoformat()
        record = RunLinkRecord(
            run_link_id=f"runlink_{uuid4().hex}",
            project_id=attempt.project_id,
            reviewed_plan_id=attempt.parent_reviewed_plan_id,
            run_id=attempt.recovery_run_id,
            pipeline_path=str(pipeline_path),
            summary_path=str(result.get("summary_path")) if result.get("summary_path") else None,
            project_config_path=str(config_path),
            audit_id=child.audit_id,
            status=str(result.get("status") or "UNKNOWN"),
            created_at=run_started,
            updated_at=now,
            payload={
                "recovery_attempt_id": attempt.recovery_attempt_id,
                "parent_run_id": attempt.parent_run_id,
                "parent_execution_ticket_id": attempt.parent_execution_ticket_id,
                "output_namespace": attempt.output_namespace,
                "attempt_output_root": str(config_path.parents[1]),
                "state_root": str(config_path.parents[1] / "work"),
            },
        )
        try:
            self.store.add_run_link(record)
        except Exception as exc:
            raise StateStoreError("RECOVERY_RUN_LINK_PERSISTENCE_FAILED") from exc

    def _close_loop(self, orchestrator, attempt, command_id, actor, result):
        current = orchestrator.get(
            project_id=attempt.project_id,
            lifecycle_id=attempt.lifecycle_id,
        )
        previous_observation = current.observation_id
        previous_goal_evaluation = current.goal_evaluation_id
        observed_lifecycle = orchestrator.observe(
            project_id=attempt.project_id,
            lifecycle_id=attempt.lifecycle_id,
            command_id=f"{command_id}:observe",
            actor=actor,
            previous_observation_id=previous_observation,
            recovery_attempt_id=attempt.recovery_attempt_id,
        )
        attempt = self._transition_attempt(
            attempt,
            "OBSERVED",
            command_id=f"{command_id}:observed",
            updates={"observation_id": observed_lifecycle.observation_id},
        )
        evaluated_lifecycle, evaluation = orchestrator.evaluate_goal(
            project_id=attempt.project_id,
            lifecycle_id=attempt.lifecycle_id,
            command_id=f"{command_id}:evaluate",
            actor=actor,
            previous_goal_evaluation_id=previous_goal_evaluation,
        )
        attempt = self._transition_attempt(
            attempt,
            "EVALUATED",
            command_id=f"{command_id}:evaluated",
            updates={
                "goal_evaluation_id": evaluation.goal_evaluation_id,
                "goal_evaluation_status": evaluation.status,
            },
        )
        return evaluated_lifecycle, attempt, result

    def _handoff(self, attempt, *, command_id: str, reason: str):
        prior_attempts = tuple(
            item.recovery_attempt_id
            for item in self.store.list_recovery_attempts(
                attempt.project_id, lifecycle_id=attempt.lifecycle_id
            )
            if item.recovery_attempt_id != attempt.recovery_attempt_id
        )
        proposal = self.store.get_recovery_proposal(attempt.recovery_proposal_id)
        diagnosis = (
            self.store.get_recovery_diagnosis(proposal.diagnosis_id)
            if proposal is not None
            else None
        )
        return self._transition_attempt(
            attempt,
            "HANDOFF",
            command_id=command_id,
            reason_code=reason,
            updates={
                "handoff_reasons": tuple(sorted({*attempt.handoff_reasons, reason})),
                "prior_recovery_attempt_ids": prior_attempts,
                "remaining_goal_gap_ids": tuple(gap.criterion_id for gap in diagnosis.goal_gaps)
                if diagnosis is not None
                else (),
                "safe_human_actions": (
                    "inspect_recovery_evidence_and_audit_timeline",
                    "review_remaining_goal_gaps",
                    "verify_quota_or_create_a_new_reviewed_plan",
                ),
            },
        )

    @staticmethod
    def _lifecycle_handoff(orchestrator, lifecycle, command_id, actor, reasons):
        if lifecycle.state == "HUMAN_HANDOFF":
            return lifecycle
        return orchestrator.transition(
            project_id=lifecycle.project_id,
            lifecycle_id=lifecycle.lifecycle_id,
            to_state="HUMAN_HANDOFF",
            command_id=f"{command_id}:human-handoff",
            actor=actor,
            source_command="recovery_handoff",
            reason=";".join(reasons),
        )

    def recover_incomplete_attempts(self, project_id: str, lifecycle_id: str):
        recovered = []
        for attempt in self.store.list_recovery_attempts(project_id, lifecycle_id=lifecycle_id):
            if attempt.status in {"EVALUATED", "HANDOFF", "REPLAN_CREATED"}:
                continue
            recovered.append(
                self._handoff(
                    attempt,
                    command_id=f"recovery-audit:{attempt.recovery_attempt_id}",
                    reason="RECOVERY_OUTCOME_REQUIRES_RECONCILIATION_AFTER_RESTART",
                )
            )
        if any(item.status == "HANDOFF" for item in recovered):
            orchestrator = AgentOrchestrator(self.store)
            lifecycle = orchestrator.get(project_id=project_id, lifecycle_id=lifecycle_id)
            self._lifecycle_handoff(
                orchestrator,
                lifecycle,
                "recovery-restart-audit",
                "system-recovery",
                ("RECOVERY_RECONCILIATION_REQUIRED_AFTER_RESTART",),
            )
        return recovered
