"""Bounded, redacted checks across Agent lifecycle authority records.

The checker deliberately does not repair records, call a model, dispatch work,
or inspect project files.  It only compares durable control-plane data.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.schemas.agent_invariant import (
    AgentInvariantAuditRecord,
    AgentInvariantFinding,
    AgentInvariantReport,
)


class AgentInvariantChecker:
    """Compare the durable records that jointly authorize Agent progression."""

    _PLANNING_STATES = frozenset({"CREATED", "CONTEXT_READY", "PLAN_DRAFTED", "PLAN_VALIDATED"})
    _PLAN_STATES = frozenset({
        "PLAN_VALIDATED", "WAITING_FOR_APPROVAL", "APPROVED", "EXECUTION_READY", "RUNNING",
        "OBSERVING", "EVALUATING", "GOAL_SATISFIED", "SUCCEEDED",
    })

    def __init__(self, store, *, now=None) -> None:
        self.store = store
        self.now = now or (lambda: datetime.now(UTC))

    def check(self, *, project_id: str, lifecycle_id: str, persist_audit: bool = False) -> AgentInvariantReport:
        lifecycle = self.store.get_agent_lifecycle(lifecycle_id)
        if lifecycle is None or lifecycle.project_id != project_id:
            raise SafetyError("AGENT_INVARIANT_LIFECYCLE_NOT_FOUND", code="AGENT_INVARIANT_LIFECYCLE_NOT_FOUND")

        findings: list[AgentInvariantFinding] = []
        self._check_decision_and_wake(lifecycle, findings)
        reviewed = self._check_plan_and_summary(lifecycle, findings)
        self._check_harness(lifecycle, findings)
        self._check_tickets_and_runs(lifecycle, reviewed, findings)
        report = AgentInvariantReport(
            lifecycle_id=lifecycle_id,
            project_id=project_id,
            checked_at=self.now(),
            findings=tuple(findings),
        )
        if persist_audit:
            self.store.add_agent_invariant_audit(self._audit_record(report))
        return report

    def assert_clear(self, *, project_id: str, lifecycle_id: str) -> AgentInvariantReport:
        report = self.check(project_id=project_id, lifecycle_id=lifecycle_id)
        if report.blocking:
            first = report.blocking[0]
            raise SafetyError(first.code, code=first.code, details={"message_key": first.message_key})
        return report

    @staticmethod
    def _finding(
        findings: list[AgentInvariantFinding], *, code: str, severity: str, lifecycle_id: str,
        related_ids: tuple[str, ...] = (), evidence_hashes: tuple[str, ...] = (),
    ) -> None:
        findings.append(AgentInvariantFinding(
            code=code, severity=severity, lifecycle_id=lifecycle_id, related_ids=related_ids,
            message_key=f"agent.invariant.{code.lower()}", evidence_hashes=evidence_hashes,
        ))

    def _check_decision_and_wake(self, lifecycle, findings: list[AgentInvariantFinding]) -> None:
        pending = lifecycle.pending_decision_batch
        if lifecycle.state in {"WAITING_FOR_INPUT", "WAITING_FOR_SCIENCE_DECISION"} and pending is None:
            self._finding(findings, code="AGENT_INV_MULTIPLE_PENDING_DECISIONS", severity="blocking", lifecycle_id=lifecycle.lifecycle_id)
        if pending is not None and pending.lifecycle_id != lifecycle.lifecycle_id:
            self._finding(findings, code="AGENT_INV_MULTIPLE_PENDING_DECISIONS", severity="blocking", lifecycle_id=lifecycle.lifecycle_id, related_ids=(pending.batch_id,))

        if lifecycle.state not in self._PLANNING_STATES:
            return
        list_wakes = getattr(self.store, "list_agent_task_wakes", None)
        wakes = list_wakes(project_id=lifecycle.project_id, include_consumed=False) if callable(list_wakes) else []
        active_wakes = [
            wake for wake in wakes
            if wake.lifecycle_id == lifecycle.lifecycle_id
            and wake.status == "CLAIMED"
            and wake.lease_expires_at is not None
            and wake.lease_expires_at > self.now()
        ]
        if len({wake.lease_owner for wake in active_wakes}) > 1:
            self._finding(
                findings, code="AGENT_INV_DUPLICATE_ACTIVE_LEASE", severity="blocking",
                lifecycle_id=lifecycle.lifecycle_id,
                related_ids=tuple(wake.wake_id for wake in active_wakes)[:12],
            )
        get_attempt = getattr(self.store, "get_agent_harness_attempt", None)
        attempt = get_attempt(lifecycle.lifecycle_id) if callable(get_attempt) else None
        active_lease = bool(
            attempt is not None and attempt.status == "RUNNING" and attempt.lease_expires_at is not None
            and attempt.lease_expires_at > self.now()
        )
        if not active_lease and not any(wake.lifecycle_id == lifecycle.lifecycle_id for wake in wakes):
            self._finding(findings, code="AGENT_INV_WAKE_MISSING", severity="warning", lifecycle_id=lifecycle.lifecycle_id)

    def _check_plan_and_summary(self, lifecycle, findings: list[AgentInvariantFinding]):
        reviewed = self.store.get_reviewed_plan(lifecycle.reviewed_plan_id) if lifecycle.reviewed_plan_id else None
        if lifecycle.state in self._PLAN_STATES and (
            reviewed is None or reviewed.project_id != lifecycle.project_id
            or not reviewed.planning_inputs_hash or not reviewed.evidence_snapshot_hash
        ):
            self._finding(
                findings, code="AGENT_INV_PLAN_WITHOUT_INPUT_HASH", severity="blocking",
                lifecycle_id=lifecycle.lifecycle_id,
                related_ids=(lifecycle.reviewed_plan_id,) if lifecycle.reviewed_plan_id else (),
            )
            return reviewed
        if reviewed is None:
            return None
        raw_summary = reviewed.payload.get("approval_envelope")
        if lifecycle.state in {"WAITING_FOR_APPROVAL", "APPROVED", "EXECUTION_READY", "RUNNING"}:
            if not isinstance(raw_summary, dict) or raw_summary.get("plan_hash") != reviewed.plan_hash:
                self._finding(
                    findings, code="AGENT_INV_SUMMARY_PLAN_MISMATCH", severity="blocking",
                    lifecycle_id=lifecycle.lifecycle_id, related_ids=(reviewed.reviewed_plan_id,),
                    evidence_hashes=(reviewed.plan_hash,),
                )
            planning_request = reviewed.payload.get("planning_request")
            if isinstance(planning_request, dict) and not planning_request.get("model_profile_hash"):
                self._finding(
                    findings, code="AGENT_INV_MODEL_PROFILE_MISSING", severity="blocking",
                    lifecycle_id=lifecycle.lifecycle_id, related_ids=(reviewed.reviewed_plan_id,),
                )
            elif isinstance(planning_request, dict) and (
                not isinstance(raw_summary, dict)
                or raw_summary.get("model_profile_hash") != planning_request.get("model_profile_hash")
            ):
                self._finding(
                    findings, code="AGENT_INV_MODEL_PROFILE_MISMATCH", severity="blocking",
                    lifecycle_id=lifecycle.lifecycle_id, related_ids=(reviewed.reviewed_plan_id,),
                )
        return reviewed

    def _check_harness(self, lifecycle, findings: list[AgentInvariantFinding]) -> None:
        get_attempt = getattr(self.store, "get_agent_harness_attempt", None)
        if not callable(get_attempt):
            return
        attempt = get_attempt(lifecycle.lifecycle_id)
        if attempt is None:
            return
        if attempt.project_id != lifecycle.project_id:
            self._finding(findings, code="AGENT_INV_DUPLICATE_ACTIVE_LEASE", severity="blocking", lifecycle_id=lifecycle.lifecycle_id, related_ids=(attempt.attempt_id,))
            return
        list_steps = getattr(self.store, "list_agent_harness_steps", None)
        list_actions = getattr(self.store, "list_agent_harness_actions", None)
        if not callable(list_steps) or not callable(list_actions):
            return
        steps = list_steps(attempt.attempt_id)
        calls = [call for step in steps for call in step.model_calls]
        completed_hashes = {call.request_hash for call in calls if call.status == "succeeded" and call.completed_at is not None}
        for call in calls:
            if call.network_called and not call.request_hash:
                self._finding(findings, code="AGENT_INV_CALL_WITHOUT_REQUEST_HASH", severity="blocking", lifecycle_id=lifecycle.lifecycle_id, related_ids=(call.call_id,))
            if not call.model_profile_hash:
                self._finding(findings, code="AGENT_INV_MODEL_PROFILE_MISSING", severity="blocking", lifecycle_id=lifecycle.lifecycle_id, related_ids=(call.call_id,))
        for action in list_actions(attempt.attempt_id):
            if action.request_hash not in completed_hashes:
                self._finding(findings, code="AGENT_INV_ACTION_WITHOUT_CALL", severity="blocking", lifecycle_id=lifecycle.lifecycle_id, related_ids=(action.action_id, action.step_id))
            if action.status == "applied" and not self._action_matches_lifecycle(action, lifecycle):
                self._finding(findings, code="AGENT_INV_APPLIED_ACTION_STATE_MISMATCH", severity="blocking", lifecycle_id=lifecycle.lifecycle_id, related_ids=(action.action_id,))

    @staticmethod
    def _action_matches_lifecycle(action, lifecycle) -> bool:
        if action.kind == "request_decision":
            return lifecycle.pending_decision_batch is not None and lifecycle.state in {"WAITING_FOR_INPUT", "WAITING_FOR_SCIENCE_DECISION"}
        # A recovered accepted action can be replayed after a process loss
        # before the deterministic planner has committed its plan transition.
        # The current schema has no separate action-result reference, so an
        # unchanged expected state is an explicitly recoverable checkpoint,
        # not evidence of a completed plan.
        return action.kind == "draft_plan" and (
            lifecycle.reviewed_plan_id is not None or lifecycle.state == action.expected_state
        )

    def _check_tickets_and_runs(self, lifecycle, reviewed, findings: list[AgentInvariantFinding]) -> None:
        list_tickets = getattr(self.store, "list_execution_tickets", None)
        list_runs = getattr(self.store, "list_run_links", None)
        if not callable(list_tickets) or not callable(list_runs):
            return
        tickets = list_tickets(lifecycle.project_id)
        related = [ticket for ticket in tickets if ticket.reviewed_plan_id == lifecycle.reviewed_plan_id]
        if reviewed is not None:
            raw_summary = reviewed.payload.get("approval_envelope")
            if isinstance(raw_summary, dict):
                for ticket in related:
                    if (
                        ticket.approval_summary_hash != raw_summary.get("summary_hash")
                        or ticket.plan_hash != reviewed.plan_hash
                        or ticket.execution_environment_hash != raw_summary.get("execution_environment_hash")
                    ):
                        self._finding(findings, code="AGENT_INV_TICKET_SUMMARY_MISMATCH", severity="blocking", lifecycle_id=lifecycle.lifecycle_id, related_ids=(ticket.execution_ticket_id, reviewed.reviewed_plan_id))
        if lifecycle.execution_ticket_id:
            get_ticket = getattr(self.store, "get_execution_ticket", None)
            ticket = get_ticket(lifecycle.execution_ticket_id) if callable(get_ticket) else None
            if ticket is None or ticket.status not in {"issued", "consumed"}:
                self._finding(findings, code="AGENT_INV_TICKET_WITHOUT_APPROVAL", severity="blocking", lifecycle_id=lifecycle.lifecycle_id, related_ids=(lifecycle.execution_ticket_id,))
        if related and (reviewed is None or not isinstance(reviewed.payload.get("approval_envelope"), dict)):
            self._finding(
                findings, code="AGENT_INV_TICKET_WITHOUT_APPROVAL", severity="blocking",
                lifecycle_id=lifecycle.lifecycle_id,
                related_ids=tuple(ticket.execution_ticket_id for ticket in related)[:12],
            )
        run_ids = {link.run_id for link in list_runs(lifecycle.project_id, lifecycle.reviewed_plan_id) if lifecycle.reviewed_plan_id}
        if lifecycle.run_id:
            run_ids.add(lifecycle.run_id)
        if run_ids and not any(ticket.status == "consumed" for ticket in related):
            self._finding(findings, code="AGENT_INV_RUN_WITHOUT_CONSUMED_TICKET", severity="blocking", lifecycle_id=lifecycle.lifecycle_id, related_ids=tuple(sorted(run_ids))[:12])
        plan_only = bool(reviewed and reviewed.payload.get("execution_performed") is False)
        if plan_only and (related or run_ids):
            self._finding(findings, code="AGENT_INV_PLAN_ONLY_HAS_EXECUTION", severity="blocking", lifecycle_id=lifecycle.lifecycle_id, related_ids=tuple(ticket.execution_ticket_id for ticket in related)[:12])
        self._check_artifact_truth(lifecycle, findings)

    def _check_artifact_truth(self, lifecycle, findings: list[AgentInvariantFinding]) -> None:
        if lifecycle.state not in {"GOAL_SATISFIED", "SUCCEEDED"}:
            return
        if not lifecycle.observation_id:
            self._finding(
                findings, code="AGENT_INV_COMPLETED_WITHOUT_ARTIFACT_TRUTH", severity="blocking",
                lifecycle_id=lifecycle.lifecycle_id,
            )
            return
        get_observation = getattr(self.store, "get_observation", None)
        if not callable(get_observation):
            return
        observation = get_observation(lifecycle.observation_id)
        if observation is None:
            self._finding(
                findings, code="AGENT_INV_COMPLETED_WITHOUT_ARTIFACT_TRUTH", severity="blocking",
                lifecycle_id=lifecycle.lifecycle_id, related_ids=(lifecycle.observation_id,),
            )
            return
        claims_computation = observation.capability.defensible_level in {"computed", "validated"}
        artifact_truth = any(
            artifact.exists
            and artifact.registration_status == "registered"
            and artifact.reload_status == "passed"
            for artifact in observation.artifacts
        )
        if claims_computation and not artifact_truth:
            self._finding(
                findings, code="AGENT_INV_COMPLETED_WITHOUT_ARTIFACT_TRUTH", severity="blocking",
                lifecycle_id=lifecycle.lifecycle_id,
                related_ids=(observation.observation_id,),
                evidence_hashes=(observation.observation_hash,),
            )

    @staticmethod
    def _audit_record(report: AgentInvariantReport) -> AgentInvariantAuditRecord:
        codes = tuple(finding.code for finding in report.findings)
        return AgentInvariantAuditRecord(
            audit_id=f"agent_invariant_{uuid4().hex}", lifecycle_id=report.lifecycle_id,
            project_id=report.project_id, report_hash=stable_hash(report.model_dump(mode="json")),
            finding_codes=codes, blocking_count=len(report.blocking), created_at=report.checked_at,
        )
