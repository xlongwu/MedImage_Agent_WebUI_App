"""Deterministic read-only projection over canonical Agent lifecycle ledgers."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import ValidationError

from src.backend.app.core.exceptions import NotFoundError, SafetyError
from src.backend.app.schemas.agent_task import (
    AgentTaskApprovalSummary,
    AgentTaskBackendSelection,
    AgentTaskDecision,
    AgentTaskDecisionOption,
    AgentTaskEvent,
    AgentTaskEventPage,
    AgentTaskEvidenceLink,
    AgentTaskListResponse,
    AgentTaskNextAction,
    AgentTaskProgress,
    AgentTaskRecoverySummary,
    AgentTaskResponse,
    AgentTaskResultSummary,
    AgentTaskTechnicalDetails,
)
from src.backend.app.core.config import ConfigService
from src.backend.app.schemas.agent_harness import AgentHarnessSummary
from src.backend.app.services.agent_task_result_summary import AgentTaskResultSummaryService


class AgentTaskReadStore(Protocol):
    def get_project(self, project_id: str): ...
    def list_agent_lifecycles(self, project_id: str): ...
    def get_agent_lifecycle(self, lifecycle_id: str): ...
    def get_reviewed_plan(self, reviewed_plan_id: str): ...
    def get_execution_ticket(self, execution_ticket_id: str): ...
    def get_run_link_by_run_id(self, project_id: str, run_id: str): ...
    def get_observation(self, observation_id: str): ...
    def get_goal_evaluation(self, goal_evaluation_id: str): ...
    def get_recovery_diagnosis(self, diagnosis_id: str): ...
    def get_recovery_proposal(self, proposal_id: str): ...
    def get_recovery_approval(self, approval_id: str): ...
    def get_recovery_attempt(self, attempt_id: str): ...
    def list_agent_lifecycle_events(self, lifecycle_id: str): ...
    def list_execution_ticket_events(self, execution_ticket_id: str): ...
    def list_observations(self, project_id: str, **filters): ...
    def list_goal_evaluations(self, project_id: str, **filters): ...
    def list_recovery_diagnoses(self, project_id: str, **filters): ...
    def list_recovery_proposals(self, project_id: str, **filters): ...
    def list_recovery_approvals(self, project_id: str, **filters): ...
    def list_recovery_attempts(self, project_id: str, **filters): ...
    def get_agent_harness_attempt(self, lifecycle_id: str): ...
    def list_agent_harness_steps(self, attempt_id: str): ...


_STATE_MAP: dict[str, str] = {
    "CREATED": "preparing",
    "WAITING_FOR_INPUT": "waiting_for_user",
    "CONTEXT_READY": "preparing",
    "PLAN_DRAFTED": "preparing",
    "WAITING_FOR_SCIENCE_DECISION": "waiting_for_user",
    "PLAN_VALIDATED": "preparing",
    "WAITING_FOR_APPROVAL": "waiting_for_user",
    "APPROVED": "preparing",
    "EXECUTION_READY": "preparing",
    "RUNNING": "running",
    "OBSERVING": "running",
    "EVALUATING": "running",
    "RETRYING": "running",
    "RECOVERING": "running",
    "FAILED": "needs_attention",
    "DIAGNOSING": "needs_attention",
    "RETRY_PROPOSED": "needs_attention",
    "WAITING_FOR_RETRY_APPROVAL": "waiting_for_user",
    "RECOVERY_PROPOSED": "waiting_for_user",
    "WAITING_FOR_RECOVERY_APPROVAL": "waiting_for_user",
    "RECOVERY_READY": "needs_attention",
    "HUMAN_HANDOFF": "needs_attention",
    "GOAL_SATISFIED": "completed",
    "SUCCEEDED": "completed",
    "CANCELED": "completed",
}

_PHASE_MAP: dict[str, str] = {
    "CREATED": "context",
    "WAITING_FOR_INPUT": "context",
    "CONTEXT_READY": "planning",
    "PLAN_DRAFTED": "planning",
    "WAITING_FOR_SCIENCE_DECISION": "planning",
    "PLAN_VALIDATED": "plan_ready",
    "WAITING_FOR_APPROVAL": "plan_ready",
    "APPROVED": "data_preparation",
    "EXECUTION_READY": "data_preparation",
    "RUNNING": "execution",
    "OBSERVING": "validation",
    "EVALUATING": "validation",
    "FAILED": "recovery",
    "DIAGNOSING": "recovery",
    "RETRY_PROPOSED": "recovery",
    "WAITING_FOR_RETRY_APPROVAL": "recovery",
    "RETRYING": "recovery",
    "RECOVERY_PROPOSED": "recovery",
    "WAITING_FOR_RECOVERY_APPROVAL": "recovery",
    "RECOVERY_READY": "recovery",
    "RECOVERING": "recovery",
    "HUMAN_HANDOFF": "complete",
    "GOAL_SATISFIED": "complete",
    "SUCCEEDED": "complete",
    "CANCELED": "complete",
}

_SOURCE_ORDER = {
    "lifecycle": 0,
    "reviewed_plan": 1,
    "ticket": 2,
    "run": 3,
    "observation": 4,
    "goal_evaluation": 5,
    "diagnosis": 6,
    "recovery": 7,
    "artifact": 8,
}


@dataclass(frozen=True)
class _EventEnvelope:
    event: AgentTaskEvent
    source_order: int

    @property
    def sort_key(self) -> tuple[str, int, str]:
        return (
            self.event.occurred_at.astimezone(UTC).isoformat(),
            self.source_order,
            self.event.event_id,
        )


class AgentTaskReadModel:
    def __init__(self, store: AgentTaskReadStore) -> None:
        self.store = store

    def list(self, *, project_id: str) -> AgentTaskListResponse:
        self._project(project_id)
        items = tuple(self._project_record(item) for item in self.store.list_agent_lifecycles(project_id))
        return AgentTaskListResponse(items=items, total=len(items))

    def get(self, *, project_id: str, task_id: str) -> AgentTaskResponse:
        self._project(project_id)
        lifecycle = self.store.get_agent_lifecycle(task_id)
        if lifecycle is None or lifecycle.project_id != project_id:
            raise NotFoundError("AGENT_TASK_NOT_FOUND", code="AGENT_TASK_NOT_FOUND")
        return self._project_record(lifecycle)

    def events(
        self,
        *,
        project_id: str,
        task_id: str,
        after: str | None = None,
        limit: int = 50,
    ) -> AgentTaskEventPage:
        cursor = None
        if after:
            cursor = self._decode_cursor(after)
            if cursor.get("project_id") != project_id or cursor.get("task_id") != task_id:
                raise SafetyError(
                    "AGENT_TASK_CURSOR_SCOPE_MISMATCH",
                    code="AGENT_TASK_CURSOR_SCOPE_MISMATCH",
                    status_code=404,
                )
        lifecycle = self._lifecycle(project_id, task_id)
        envelopes = sorted(self._collect_events(lifecycle), key=lambda item: item.sort_key)
        start = 0
        if cursor is not None:
            cursor_key = tuple(cursor.get("sort_key", ()))
            matches = [index for index, item in enumerate(envelopes) if item.sort_key == cursor_key]
            if len(matches) != 1:
                raise SafetyError(
                    "AGENT_TASK_CURSOR_STALE",
                    code="AGENT_TASK_CURSOR_STALE",
                    status_code=400,
                )
            start = matches[0] + 1
        selected = envelopes[start : start + limit]
        end = start + len(selected)
        next_cursor = None
        if selected and end < len(envelopes):
            next_cursor = self._encode_cursor(project_id, task_id, selected[-1].sort_key)
        return AgentTaskEventPage(
            items=tuple(item.event for item in selected),
            next_cursor=next_cursor,
        )

    def _project_record(self, lifecycle) -> AgentTaskResponse:
        state = str(lifecycle.state)
        plan = self.store.get_reviewed_plan(lifecycle.reviewed_plan_id) if lifecycle.reviewed_plan_id else None
        ticket = self.store.get_execution_ticket(lifecycle.execution_ticket_id) if lifecycle.execution_ticket_id else None
        observation = self._bound_observation(lifecycle)
        evaluation = self._bound_evaluation(lifecycle, observation)
        diagnosis = self._bound_diagnosis(lifecycle)
        proposal = self._bound_proposal(lifecycle)
        approval = self.store.get_recovery_approval(lifecycle.recovery_approval_id) if lifecycle.recovery_approval_id else None

        approval_summary = self._approval_summary(plan)
        result_summary = self._result_summary(lifecycle, observation, evaluation)
        public_state = _STATE_MAP.get(state, "needs_attention")
        if state in {"GOAL_SATISFIED", "SUCCEEDED"} and public_state == "completed" and (
            result_summary is None or result_summary.outcome != "succeeded"
        ):
            public_state = "needs_attention"
        if state == "WAITING_FOR_APPROVAL" and approval_summary is None:
            public_state = "needs_attention"

        outcome = result_summary.outcome if result_summary else None
        if (
            outcome is None
            and public_state == "needs_attention"
            and state in {"GOAL_SATISFIED", "SUCCEEDED"}
        ):
            outcome = "indeterminate"
        if state == "FAILED" and outcome is None:
            outcome = "failed"
        if state == "CANCELED":
            outcome = "canceled"

        progress = self._progress(state, observation)
        recovery = self._recovery_summary(proposal, diagnosis, approval)
        next_action = self._next_action(
            state=state,
            public_state=public_state,
            approval_summary=approval_summary,
            recovery=recovery,
            decision_batch_id=(
                lifecycle.pending_decision_batch.batch_id
                if getattr(lifecycle, "pending_decision_batch", None) is not None
                else None
            ),
            decision_kind=(
                lifecycle.pending_decision_batch.items[0].kind
                if getattr(lifecycle, "pending_decision_batch", None) is not None
                else None
            ),
        )
        goal = self._goal_summary(plan, lifecycle)
        technical = self._technical_details(lifecycle, plan, ticket, observation, evaluation)
        harness_summary = self._harness_summary(lifecycle)
        return AgentTaskResponse(
            task_id=lifecycle.lifecycle_id,
            project_id=lifecycle.project_id,
            state=public_state,
            outcome=outcome,
            goal_summary=goal,
            current_action=self._current_action(public_state, state),
            next_action=next_action,
            progress=progress,
            decisions=self._decisions(lifecycle),
            decision_batch=self._decision_batch(lifecycle),
            approval_summary=approval_summary,
            result_summary=result_summary,
            recovery=recovery,
            evidence_links=self._evidence_links(lifecycle, observation, evaluation),
            technical_details=technical,
            harness_summary=harness_summary,
            created_at=lifecycle.created_at,
            updated_at=lifecycle.updated_at,
        )

    def _harness_summary(self, lifecycle) -> AgentHarnessSummary | None:
        getter = getattr(self.store, "get_agent_harness_attempt", None)
        if getter is None:
            return None
        attempt = getter(lifecycle.lifecycle_id)
        if attempt is None or attempt.project_id != lifecycle.project_id:
            return None
        steps = self.store.list_agent_harness_steps(attempt.attempt_id)
        latest = steps[-1] if steps else None
        config = ConfigService().harness
        return AgentHarnessSummary(
            status=attempt.status,
            model_calls_used=attempt.model_calls_used,
            model_calls_limit=config.max_model_calls,
            tool_proposals_used=attempt.tool_proposals_used,
            tool_proposals_limit=config.max_tool_proposals,
            next_step=(f"step {attempt.next_step_no}" if attempt.status in {"READY", "RUNNING"} else None),
            terminal_reason=attempt.terminal_reason,
            latest_step_id=latest.step_id if latest else None,
            latest_step_summary=latest.summary if latest else None,
            last_wake_reason=attempt.last_wake_reason,
            yield_count=attempt.yield_count,
            fallback_from=attempt.fallback_from,
            fallback_to=attempt.fallback_to,
            fallback_reason=attempt.fallback_reason,
        )

    def _project(self, project_id: str):
        project = self.store.get_project(project_id)
        if project is None:
            raise NotFoundError("AGENT_TASK_PROJECT_NOT_FOUND", code="AGENT_TASK_PROJECT_NOT_FOUND")
        return project

    def _lifecycle(self, project_id: str, task_id: str):
        self._project(project_id)
        lifecycle = self.store.get_agent_lifecycle(task_id)
        if lifecycle is None or lifecycle.project_id != project_id:
            raise NotFoundError("AGENT_TASK_NOT_FOUND", code="AGENT_TASK_NOT_FOUND")
        return lifecycle

    def _bound_observation(self, lifecycle):
        if not lifecycle.observation_id:
            return None
        record = self.store.get_observation(lifecycle.observation_id)
        if record is None:
            return None
        bindings = record.bindings
        if bindings.project_id != lifecycle.project_id or bindings.lifecycle_id != lifecycle.lifecycle_id:
            return None
        if lifecycle.run_id and bindings.run_id != lifecycle.run_id:
            return None
        return record

    def _bound_evaluation(self, lifecycle, observation):
        if not lifecycle.goal_evaluation_id or observation is None:
            return None
        record = self.store.get_goal_evaluation(lifecycle.goal_evaluation_id)
        if record is None:
            return None
        if (
            record.project_id != lifecycle.project_id
            or record.lifecycle_id != lifecycle.lifecycle_id
            or record.observation_id != observation.observation_id
            or record.observation_hash != observation.observation_hash
        ):
            return None
        return record

    def _bound_diagnosis(self, lifecycle):
        if not lifecycle.diagnosis_id:
            return None
        record = self.store.get_recovery_diagnosis(lifecycle.diagnosis_id)
        if record is None or record.bindings.project_id != lifecycle.project_id:
            return None
        if record.bindings.lifecycle_id != lifecycle.lifecycle_id:
            return None
        return record

    def _bound_proposal(self, lifecycle):
        if not lifecycle.recovery_proposal_id:
            return None
        record = self.store.get_recovery_proposal(lifecycle.recovery_proposal_id)
        if record is None or record.bindings.project_id != lifecycle.project_id:
            return None
        if record.bindings.lifecycle_id != lifecycle.lifecycle_id:
            return None
        return record

    @staticmethod
    def _approval_summary(plan) -> AgentTaskApprovalSummary | None:
        if plan is None or not isinstance(plan.payload, dict):
            return None
        payload = plan.payload.get("approval_summary")
        if not isinstance(payload, dict):
            return None
        try:
            return AgentTaskApprovalSummary.model_validate(payload)
        except ValidationError:
            return None

    @staticmethod
    def _goal_summary(plan, lifecycle) -> str:
        if plan is not None and isinstance(plan.payload, dict):
            contract = plan.payload.get("goal_contract")
            if isinstance(contract, dict) and contract.get("goal_text"):
                return str(contract["goal_text"])
            if plan.payload.get("goal"):
                return str(plan.payload["goal"])
        return str(getattr(lifecycle, "goal_text", None) or "Research workflow task")

    @staticmethod
    def _subject_counts(observation) -> tuple[int | None, int | None, int | None, int | None]:
        if observation is None:
            return None, None, None, None
        statuses: dict[str, str] = {}
        for node in observation.nodes:
            if node.subject_id and node.subject_id != "project":
                statuses[node.subject_id] = str(node.status).upper()
        if not statuses:
            return None, None, None, None
        completed = sum(value in {"SUCCESS", "SUCCEEDED", "COMPLETED"} for value in statuses.values())
        failed = sum(value in {"FAILED", "ERROR"} for value in statuses.values())
        excluded = sum(value in {"SKIPPED", "EXCLUDED"} for value in statuses.values())
        return completed, failed, excluded, len(statuses)

    def _progress(self, state: str, observation) -> AgentTaskProgress:
        completed, failed, excluded, total = self._subject_counts(observation)
        percent = None
        if total:
            percent = min(100, round(100 * (completed + failed + excluded) / total))
        return AgentTaskProgress(
            phase=_PHASE_MAP.get(state, "recovery"),
            percent=percent,
            completed_subjects=completed,
            failed_subjects=failed,
            excluded_subjects=excluded,
            total_subjects=total,
        )

    def _result_summary(self, lifecycle, observation, evaluation) -> AgentTaskResultSummary | None:
        if lifecycle.reviewed_plan_id:
            plan = self.store.get_reviewed_plan(lifecycle.reviewed_plan_id)
            payload = plan.payload if plan is not None and isinstance(plan.payload, dict) else {}
            plan_payload = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
            metadata = plan_payload.get("metadata") if isinstance(plan_payload.get("metadata"), dict) else {}
            if (
                lifecycle.state == "SUCCEEDED"
                and metadata.get("plan_only") is True
                and payload.get("execution_status") == "NOT_EXECUTED_PLAN_ONLY"
            ):
                from src.backend.app.schemas.agent_task import AgentTaskArtifactSummary

                return AgentTaskResultSummary(
                    outcome="succeeded",
                    title="Preprocessing plan prepared",
                    summary=(
                        "A reviewed metadata-only preprocessing plan was created. "
                        "No numerical computation ran and rawdata was not modified."
                    ),
                    limitations=("Metadata-only plan; no numerical result was produced.",),
                    recommended_action="Review the saved plan details.",
                    artifacts=(
                        AgentTaskArtifactSummary(
                            artifact_id=lifecycle.reviewed_plan_id,
                            artifact_type="reviewed_plan",
                            label="Reviewed preprocessing plan",
                            uri=f"project://{lifecycle.project_id}/plans/{lifecycle.reviewed_plan_id}",
                            checksum=plan.plan_hash,
                            capability_level="metadata_only",
                            reload_status="passed",
                        ),
                    ),
                )
        if observation is None or evaluation is None:
            return None
        try:
            return AgentTaskResultSummaryService().build(
                lifecycle=lifecycle,
                observation=observation,
                evaluation=evaluation,
            )
        except SafetyError:
            return None

    @staticmethod
    def _recovery_summary(proposal, diagnosis, approval) -> AgentTaskRecoverySummary | None:
        if proposal is None:
            return None
        candidate = next(
            (item for item in proposal.candidates if item.candidate_id == proposal.recommended_candidate_id),
            None,
        )
        if candidate is None or not candidate.eligible:
            return None
        diagnosis_text = "Recovery evidence requires review."
        if diagnosis is not None:
            diagnosis_text = f"Root cause: {diagnosis.root_cause_status}; {len(diagnosis.facts)} evidence fact(s)."
        return AgentTaskRecoverySummary(
            proposal_id=proposal.recovery_proposal_id,
            diagnosis=diagnosis_text,
            affected_subjects=candidate.target_subject_ids,
            recommended_action=candidate.action,
            untouched_scope=("All subjects and nodes outside the approved recovery scope",),
            requires_new_plan=candidate.changes_reviewed_plan,
            approval_summary_hash=(approval.recovery_approval_hash if approval is not None else None),
        )

    @staticmethod
    def _next_action(
        *,
        state: str,
        public_state: str,
        approval_summary,
        recovery,
        decision_batch_id: str | None,
        decision_kind: str | None,
    ) -> AgentTaskNextAction:
        if state == "WAITING_FOR_INPUT":
            if decision_kind == "goal_revision":
                return AgentTaskNextAction(
                    type="revise_goal",
                    title="Revise the research goal",
                    description="The current goal did not match a supported bounded workflow.",
                    requires_user=True,
                    decision_batch_id=decision_batch_id,
                )
            return AgentTaskNextAction(
                type="provide_input",
                title="Provide the required project input",
                description="Planning will resume after the missing project evidence is supplied.",
                requires_user=True,
                decision_batch_id=decision_batch_id,
            )
        if state == "WAITING_FOR_SCIENCE_DECISION":
            return AgentTaskNextAction(
                type="answer_science_decision",
                title="Answer the scientific decision",
                description="The answer changes the reviewed scientific plan and its hashes.",
                requires_user=True,
                decision_batch_id=decision_batch_id,
            )
        if state == "CANCELED":
            return AgentTaskNextAction(
                type="none",
                title="Task canceled",
                description=None,
                requires_user=False,
            )
        if state == "WAITING_FOR_APPROVAL" and approval_summary is not None:
            return AgentTaskNextAction(
                type="approve_execution",
                title="Review and approve execution",
                description="Approve the bounded reviewed plan after checking its scope and limitations.",
                requires_user=True,
            )
        if state in {"RECOVERY_PROPOSED", "WAITING_FOR_RECOVERY_APPROVAL", "WAITING_FOR_RETRY_APPROVAL"} and recovery:
            return AgentTaskNextAction(
                type="approve_recovery",
                title="Review the recovery proposal",
                description="Only the displayed recovery scope can be approved.",
                requires_user=True,
            )
        if public_state == "completed":
            return AgentTaskNextAction(
                type="review_results",
                title="Review results",
                description=None,
                requires_user=False,
            )
        if public_state == "needs_attention":
            return AgentTaskNextAction(
                type="view_attention",
                title="Review attention items",
                description="Inspect the evidence and choose a safe next step.",
                requires_user=True,
            )
        return AgentTaskNextAction(
            type="none",
            title="No action required",
            description=None,
            requires_user=False,
        )

    @staticmethod
    def _decisions(lifecycle) -> tuple[AgentTaskDecision, ...]:
        pending = getattr(lifecycle, "pending_decision_batch", None)
        if pending is None:
            return ()
        return tuple(
            AgentTaskDecision(
                item_id=item.item_id,
                kind=item.kind,
                question=item.question,
                impact=item.impact,
                options=tuple(
                    AgentTaskDecisionOption(
                        id=option.id,
                        label=option.label,
                        description=option.description,
                        recommended=option.recommended,
                    )
                    for option in item.options
                ),
                recommended_option=item.recommended_option,
                source=item.source,
                memory_id=item.memory_id,
                recommendation_source=item.recommendation_source,
                answer_type=item.answer_type,
                required=item.required,
                evidence_refs=item.evidence_refs,
            )
            for item in pending.items
        )

    @staticmethod
    def _decision_batch(lifecycle):
        from src.backend.app.schemas.agent_task import AgentTaskDecisionBatch

        pending = getattr(lifecycle, "pending_decision_batch", None)
        if pending is None:
            return None
        return AgentTaskDecisionBatch(
            batch_id=pending.batch_id,
            evidence_snapshot_hash=pending.evidence_snapshot_hash,
            plan_hash_before=pending.plan_hash_before,
            expires_at=pending.expires_at,
        )

    @staticmethod
    def _current_action(public_state: str, internal_state: str) -> str:
        if public_state == "preparing":
            return "Preparing the reviewed research workflow."
        if public_state == "waiting_for_user":
            return "Waiting for one reviewed decision."
        if public_state == "running":
            return "Processing the approved research workflow."
        if public_state == "completed":
            return "The research goal has defensible result evidence."
        if internal_state not in _STATE_MAP:
            return "This task uses an unsupported internal state and needs review."
        return "The task needs attention before it can continue."

    def _technical_details(self, lifecycle, plan, ticket, observation, evaluation):
        node_ids: tuple[str, ...] = ()
        backend = None
        if ticket is not None:
            node_ids = tuple(ticket.approved_node_ids)
            selected = ticket.approved_backend_ids[0] if ticket.approved_backend_ids else None
            backend = AgentTaskBackendSelection(requested="auto", selected=selected)
        raw_context = lifecycle.command_context.get("memory_context")
        memory_context = raw_context if isinstance(raw_context, dict) else {}
        raw_consent = lifecycle.command_context.get("memory_consent")
        memory_consent = raw_consent if isinstance(raw_consent, dict) else {}
        return AgentTaskTechnicalDetails(
            lifecycle_id=lifecycle.lifecycle_id,
            internal_state=str(lifecycle.state),
            reviewed_plan_id=lifecycle.reviewed_plan_id,
            plan_hash=(plan.plan_hash if plan is not None else None),
            goal_contract_id=lifecycle.goal_contract_id,
            goal_hash=lifecycle.goal_contract_hash,
            ticket_id=lifecycle.execution_ticket_id,
            run_id=lifecycle.run_id,
            observation_id=(observation.observation_id if observation is not None else None),
            evaluation_id=(evaluation.goal_evaluation_id if evaluation is not None else None),
            backend=backend,
            node_ids=node_ids,
            memory_context_hash=(
                str(memory_context.get("context_hash") or "") or None
            ),
            memory_refs=tuple(memory_context.get("evidence_refs") or ()),
            memory_retrieval_policy_version=(
                str(memory_context.get("retrieval_policy_version") or "") or None
            ),
            memory_status=(
                str(memory_context["status"])
                if memory_context.get("status") in {"disabled", "enabled", "partial"}
                else None
            ),
            memory_used_bytes=(
                int(memory_context["used_bytes"])
                if isinstance(memory_context.get("used_bytes"), int)
                and not isinstance(memory_context.get("used_bytes"), bool)
                and int(memory_context["used_bytes"]) >= 0
                else None
            ),
            memory_omitted_count=(
                int(memory_context["omitted_count"])
                if isinstance(memory_context.get("omitted_count"), int)
                and not isinstance(memory_context.get("omitted_count"), bool)
                and int(memory_context["omitted_count"]) >= 0
                else None
            ),
            memory_warnings=tuple(
                str(value)
                for value in lifecycle.command_context.get("memory_warnings", [])
                if value
            ),
            memory_available=(
                bool(memory_consent["available"])
                if "available" in memory_consent
                else None
            ),
            memory_generate_enabled=(
                bool(memory_consent["generate_enabled"])
                if "generate_enabled" in memory_consent
                else None
            ),
            memory_use_enabled=(
                bool(memory_consent["use_enabled"])
                if "use_enabled" in memory_consent
                else None
            ),
        )

    def _evidence_links(self, lifecycle, observation, evaluation) -> tuple[AgentTaskEvidenceLink, ...]:
        entries: list[tuple[str, str, str, str | None]] = [
            (lifecycle.lifecycle_id, "task_details", "Task details", lifecycle.lifecycle_id),
            ("reviewed-plan", "reviewed_plan", "Reviewed plan", lifecycle.reviewed_plan_id),
            ("ticket", "execution_ticket", "Execution ticket", lifecycle.execution_ticket_id),
            ("run", "run", "Run", lifecycle.run_id),
            ("observation", "observation", "Observation", observation.observation_id if observation else None),
            ("evaluation", "goal_evaluation", "Goal evaluation", evaluation.goal_evaluation_id if evaluation else None),
            ("diagnosis", "diagnosis", "Diagnosis", lifecycle.diagnosis_id),
            ("recovery", "recovery", "Recovery", lifecycle.recovery_proposal_id),
            ("audit", "audit", "Audit", lifecycle.audit_id),
        ]
        links = []
        for link_id, link_type, label, record_id in entries:
            if record_id is None and link_type != "task_details":
                continue
            links.append(
                AgentTaskEvidenceLink(
                    id=link_id,
                    type=link_type,
                    label=label,
                    uri=self._uri(lifecycle.project_id, link_type, str(record_id)),
                    available=record_id is not None,
                )
            )
        return tuple(links)

    def _collect_events(self, lifecycle) -> list[_EventEnvelope]:
        project_id = lifecycle.project_id
        task_id = lifecycle.lifecycle_id
        items: list[_EventEnvelope] = []
        for event in self.store.list_agent_lifecycle_events(task_id):
            if event.project_id != project_id or event.lifecycle_id != task_id:
                continue
            items.append(
                self._event(
                    event_id=event.event_id,
                    task_id=task_id,
                    project_id=project_id,
                    source="lifecycle",
                    event_type=event.source_command,
                    occurred_at=event.occurred_at,
                    title=str(event.to_state).replace("_", " ").title(),
                    summary=event.reason or f"Lifecycle moved to {event.to_state}.",
                    evidence_id=event.event_id,
                )
            )
        if lifecycle.execution_ticket_id:
            for event in self.store.list_execution_ticket_events(lifecycle.execution_ticket_id):
                if event.project_id != project_id:
                    continue
                items.append(
                    self._event(
                        event_id=event.event_id,
                        task_id=task_id,
                        project_id=project_id,
                        source="ticket",
                        event_type=event.event_type,
                        occurred_at=event.occurred_at,
                        title="Execution ticket update",
                        summary=event.reason or event.event_type,
                        evidence_id=event.execution_ticket_id,
                    )
                )
        for record in self.store.list_observations(project_id, lifecycle_id=task_id):
            items.append(self._event(
                event_id=record.observation_id, task_id=task_id, project_id=project_id,
                source="observation", event_type="observation_collected", occurred_at=record.collected_at,
                title="Run evidence observed", summary=record.completeness.status,
                evidence_id=record.observation_id,
            ))
        for record in self.store.list_goal_evaluations(project_id, lifecycle_id=task_id):
            items.append(self._event(
                event_id=record.goal_evaluation_id, task_id=task_id, project_id=project_id,
                source="goal_evaluation", event_type="goal_evaluated", occurred_at=record.evaluated_at,
                title="Research goal evaluated", summary=record.status,
                evidence_id=record.goal_evaluation_id,
            ))
        for record in self.store.list_recovery_diagnoses(project_id, lifecycle_id=task_id):
            items.append(self._event(
                event_id=record.diagnosis_id, task_id=task_id, project_id=project_id,
                source="diagnosis", event_type="diagnosis_created", occurred_at=record.created_at,
                title="Failure evidence diagnosed", summary=record.root_cause_status,
                evidence_id=record.diagnosis_id,
            ))
        for record in self.store.list_recovery_proposals(project_id, lifecycle_id=task_id):
            items.append(self._event(
                event_id=record.recovery_proposal_id, task_id=task_id, project_id=project_id,
                source="recovery", event_type="recovery_proposed", occurred_at=record.created_at,
                title="Recovery proposed", summary=record.recommended_candidate_id,
                evidence_id=record.recovery_proposal_id,
            ))
        for record in self.store.list_recovery_approvals(project_id, lifecycle_id=task_id):
            items.append(self._event(
                event_id=record.recovery_approval_id, task_id=task_id, project_id=project_id,
                source="recovery", event_type="recovery_approved", occurred_at=record.approved_at,
                title="Recovery approved", summary=record.action,
                evidence_id=record.recovery_approval_id,
            ))
        for record in self.store.list_recovery_attempts(project_id, lifecycle_id=task_id):
            items.append(self._event(
                event_id=record.recovery_attempt_id, task_id=task_id, project_id=project_id,
                source="recovery", event_type="recovery_attempt", occurred_at=record.updated_at,
                title="Recovery attempt updated", summary=record.status,
                evidence_id=record.recovery_attempt_id,
            ))
        return items

    def _event(
        self,
        *,
        event_id: str,
        task_id: str,
        project_id: str,
        source: str,
        event_type: str,
        occurred_at: datetime,
        title: str,
        summary: str,
        evidence_id: str,
    ) -> _EventEnvelope:
        return _EventEnvelope(
            event=AgentTaskEvent(
                event_id=event_id,
                task_id=task_id,
                project_id=project_id,
                source=source,
                type=event_type,
                occurred_at=occurred_at,
                title=title,
                summary=summary,
                evidence_uri=self._uri(project_id, source, evidence_id),
            ),
            source_order=_SOURCE_ORDER[source],
        )

    @staticmethod
    def _uri(project_id: str, kind: str, record_id: str) -> str:
        safe_project = project_id.replace("/", "_").replace("\\", "_")
        safe_kind = kind.replace("/", "_").replace("\\", "_")
        safe_record = record_id.replace("/", "_").replace("\\", "_")
        return f"project://{safe_project}/{safe_kind}/{safe_record}"

    @staticmethod
    def _encode_cursor(project_id: str, task_id: str, sort_key: tuple[str, int, str]) -> str:
        body = {
            "v": 1,
            "project_id": project_id,
            "task_id": task_id,
            "sort_key": list(sort_key),
        }
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        envelope = {"body": body, "digest": hashlib.sha256(canonical.encode("utf-8")).hexdigest()}
        encoded = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return base64.urlsafe_b64encode(encoded).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str) -> dict[str, Any]:
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            envelope = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
            body = envelope["body"]
            canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            if envelope.get("digest") != digest or body.get("v") != 1:
                raise ValueError("cursor digest mismatch")
            return body
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise SafetyError(
                "AGENT_TASK_CURSOR_INVALID",
                code="AGENT_TASK_CURSOR_INVALID",
                status_code=400,
            ) from exc
