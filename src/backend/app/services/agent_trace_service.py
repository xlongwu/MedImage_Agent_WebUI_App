"""Assemble a redacted, read-only trace from canonical Agent records."""

from __future__ import annotations

from collections.abc import Iterable

from src.backend.app.core.exceptions import NotFoundError
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.schemas.agent_trace import (
    AgentTraceBudget,
    AgentTraceBundle,
    AgentTraceEntry,
    AgentTraceLifecycleEvent,
    AgentTracePage,
    AgentTraceReference,
)


def calculate_trace_integrity_hash(bundle: AgentTraceBundle) -> str:
    """Hash every safe trace field except the hash itself.

    Keeping this function shared with replay prevents a projection change from
    silently making the recorded integrity claim unverifiable.
    """

    payload = bundle.model_dump(mode="json", exclude={"integrity_hash"})
    return stable_hash(payload)


class AgentTraceService:
    """Build trace views without writing or invoking any operational service."""

    def __init__(self, store) -> None:
        self.store = store

    def get(self, *, project_id: str, lifecycle_id: str) -> AgentTraceBundle:
        lifecycle = self.store.get_agent_lifecycle(lifecycle_id)
        if lifecycle is None or lifecycle.project_id != project_id:
            raise NotFoundError("AGENT_TRACE_NOT_FOUND", code="AGENT_TRACE_NOT_FOUND")

        issues: list[str] = []
        references: list[AgentTraceReference] = []
        attempt = self._attempt(lifecycle, issues)
        entries = self._entries(attempt, project_id, lifecycle_id, issues)
        events = self._events(lifecycle, project_id, issues)
        self._append_authority_references(lifecycle, project_id, references, issues)
        status = "conflict" if any(issue.endswith("_CONFLICT") for issue in issues) else (
            "incomplete" if issues else "complete"
        )
        context = self._first_context(entries)
        draft = AgentTraceBundle(
            trace_id=f"agent_trace:{lifecycle_id}",
            project_id=project_id,
            lifecycle_id=lifecycle_id,
            attempt_id=getattr(attempt, "attempt_id", None),
            policy_version=getattr(context, "policy_version", None),
            prompt_template_version=getattr(context, "prompt_template_version", None),
            skill_hashes=tuple(sorted({ref.content_hash for entry in entries for ref in entry.context_refs if ref.ref_type == "skill" and ref.content_hash})),
            provider_refs=tuple(sorted({call.provider for entry in entries for call in entry.model_calls})),
            entries=tuple(entries),
            lifecycle_events=tuple(events),
            references=tuple(sorted(references, key=lambda item: (item.ref_type, item.ref_id))),
            budget=self._budget(attempt),
            initial_state=(events[0].from_state if events and events[0].from_state else None),
            final_state=lifecycle.state,
            stop_reason=getattr(attempt, "terminal_reason", None),
            integrity_status=status,
            integrity_issues=tuple(sorted(set(issues))),
            integrity_hash="pending",
        )
        return draft.model_copy(update={"integrity_hash": calculate_trace_integrity_hash(draft)})

    def page(
        self, *, project_id: str, lifecycle_id: str, after: int = 0, limit: int = 50
    ) -> AgentTracePage:
        bundle = self.get(project_id=project_id, lifecycle_id=lifecycle_id)
        if after < 0 or limit < 1:
            raise ValueError("AGENT_TRACE_CURSOR_INVALID")
        entries = bundle.entries[after : after + limit]
        next_cursor = after + len(entries)
        return AgentTracePage(
            trace_id=bundle.trace_id,
            project_id=bundle.project_id,
            lifecycle_id=bundle.lifecycle_id,
            integrity_status=bundle.integrity_status,
            integrity_hash=bundle.integrity_hash,
            final_state=bundle.final_state,
            stop_reason=bundle.stop_reason,
            entries=entries,
            next_cursor=next_cursor if next_cursor < len(bundle.entries) else None,
        )

    def _attempt(self, lifecycle, issues: list[str]):
        attempt = self.store.get_agent_harness_attempt(lifecycle.lifecycle_id)
        if attempt is not None and attempt.project_id != lifecycle.project_id:
            issues.append("ATTEMPT_PROJECT_CONFLICT")
            return None
        return attempt

    def _entries(self, attempt, project_id: str, lifecycle_id: str, issues: list[str]) -> list[AgentTraceEntry]:
        if attempt is None:
            return []
        steps = list(self.store.list_agent_harness_steps(attempt.attempt_id))
        actions = {
            action.step_id: action
            for action in self.store.list_agent_harness_actions(attempt.attempt_id)
        }
        steps.sort(key=lambda step: (step.step_no, step.step_id))
        entries: list[AgentTraceEntry] = []
        expected_step_no = 1
        for step in steps:
            if step.project_id != project_id:
                issues.append("STEP_PROJECT_CONFLICT")
                continue
            if step.step_no != expected_step_no:
                issues.append("STEP_SEQUENCE_MISSING")
                expected_step_no = step.step_no
            expected_step_no += 1
            context_refs = self._contexts(step, project_id, lifecycle_id, issues)
            refs = list(context_refs)
            refs.extend(self._step_references(step, project_id, lifecycle_id, issues))
            entries.append(AgentTraceEntry(
                step_id=step.step_id,
                step_no=step.step_no,
                idempotency_key=step.idempotency_key,
                context_refs=tuple(context_refs),
                model_calls=step.model_calls,
                action_record=actions.get(step.step_id),
                action_kind=step.kind,
                action_hash=step.action_hash,
                action_result_hash=step.action_result_hash,
                validation_result=step.validation_result,
                action_result_code=step.action_result_code or step.error_code,
                state_before=step.state_before,
                state_after=step.state_after,
                started_at=step.started_at,
                completed_at=step.completed_at,
                references=tuple(sorted(refs, key=lambda item: (item.ref_type, item.ref_id))),
            ))
        return entries

    def _contexts(self, step, project_id: str, lifecycle_id: str, issues: list[str]) -> list[AgentTraceReference]:
        hashes = sorted({call.context_hash for call in step.model_calls})
        if not hashes:
            return []
        refs: list[AgentTraceReference] = []
        for context_hash in hashes:
            context = self.store.get_agent_harness_context(context_hash)
            if context is None:
                issues.append("CONTEXT_MISSING")
                refs.append(AgentTraceReference(ref_type="context", ref_id=context_hash, content_hash=context_hash, status="missing"))
            elif context.project_id != project_id or context.lifecycle_id != lifecycle_id:
                issues.append("CONTEXT_CONFLICT")
                refs.append(AgentTraceReference(ref_type="context", ref_id=context_hash, content_hash=context_hash, status="conflict"))
            else:
                refs.append(AgentTraceReference(ref_type="context", ref_id=context_hash, content_hash=context_hash))
                refs.extend(
                    AgentTraceReference(ref_type="skill", ref_id=ref.skill_id, content_hash=ref.content_hash)
                    for ref in context.skill_refs
                )
        return refs

    def _step_references(self, step, project_id: str, lifecycle_id: str, issues: list[str]) -> list[AgentTraceReference]:
        result: list[AgentTraceReference] = []
        for ref_type, record_id, getter_name, hash_name in (
            ("observation", step.observation_ref, "get_observation", "observation_hash"),
            ("evaluation", step.evaluation_ref, "get_goal_evaluation", "goal_evaluation_hash"),
        ):
            if not record_id:
                continue
            getter = getattr(self.store, getter_name)
            record = getter(record_id)
            record_project, record_lifecycle = self._record_scope(record)
            if record is None:
                issues.append(f"{ref_type.upper()}_MISSING")
                result.append(AgentTraceReference(ref_type=ref_type, ref_id=record_id, status="missing"))
            elif record_project != project_id or record_lifecycle != lifecycle_id:
                issues.append(f"{ref_type.upper()}_CONFLICT")
                result.append(AgentTraceReference(ref_type=ref_type, ref_id=record_id, status="conflict"))
            else:
                result.append(AgentTraceReference(ref_type=ref_type, ref_id=record_id, content_hash=getattr(record, hash_name, None)))
        return result

    def _events(self, lifecycle, project_id: str, issues: list[str]) -> list[AgentTraceLifecycleEvent]:
        events = list(self.store.list_agent_lifecycle_events(lifecycle.lifecycle_id))
        events.sort(key=lambda event: (event.occurred_at, event.event_id))
        safe_events: list[AgentTraceLifecycleEvent] = []
        for event in events:
            if event.project_id != project_id or event.lifecycle_id != lifecycle.lifecycle_id:
                issues.append("LIFECYCLE_EVENT_CONFLICT")
                continue
            safe_events.append(AgentTraceLifecycleEvent(
                event_id=event.event_id,
                occurred_at=event.occurred_at,
                from_state=event.from_state,
                to_state=event.to_state,
                source_command=event.source_command,
            ))
        return safe_events

    def _append_authority_references(self, lifecycle, project_id: str, references: list[AgentTraceReference], issues: list[str]) -> None:
        self._append_reference(references, issues, "reviewed_plan", lifecycle.reviewed_plan_id, self.store.get_reviewed_plan, project_id, "plan_hash")
        self._append_reference(references, issues, "ticket", lifecycle.execution_ticket_id, self.store.get_execution_ticket, project_id, "ticket_hash")
        if lifecycle.run_id:
            run = self.store.get_run_link_by_run_id(project_id, lifecycle.run_id)
            if run is None:
                issues.append("RUN_MISSING")
                references.append(AgentTraceReference(ref_type="run", ref_id=lifecycle.run_id, status="missing"))
            elif run.project_id != project_id:
                issues.append("RUN_CONFLICT")
                references.append(AgentTraceReference(ref_type="run", ref_id=lifecycle.run_id, status="conflict"))
            else:
                references.append(AgentTraceReference(ref_type="run", ref_id=lifecycle.run_id))
        self._append_reference(references, issues, "observation", lifecycle.observation_id, self.store.get_observation, project_id, "observation_hash", lifecycle.lifecycle_id)
        self._append_reference(references, issues, "evaluation", lifecycle.goal_evaluation_id, self.store.get_goal_evaluation, project_id, "goal_evaluation_hash", lifecycle.lifecycle_id)
        self._append_reference(references, issues, "recovery", lifecycle.recovery_proposal_id, self.store.get_recovery_proposal, project_id, "recovery_proposal_hash", lifecycle.lifecycle_id)

    def _append_reference(self, refs: list[AgentTraceReference], issues: list[str], ref_type: str, record_id: str | None, getter, project_id: str, hash_name: str, lifecycle_id: str | None = None) -> None:
        if not record_id:
            return
        record = getter(record_id)
        record_project, record_lifecycle = self._record_scope(record)
        if record is None:
            issues.append(f"{ref_type.upper()}_MISSING")
            refs.append(AgentTraceReference(ref_type=ref_type, ref_id=record_id, status="missing"))
        elif record_project != project_id or (lifecycle_id is not None and record_lifecycle != lifecycle_id):
            issues.append(f"{ref_type.upper()}_CONFLICT")
            refs.append(AgentTraceReference(ref_type=ref_type, ref_id=record_id, status="conflict"))
        else:
            refs.append(AgentTraceReference(ref_type=ref_type, ref_id=record_id, content_hash=getattr(record, hash_name, None)))

    @staticmethod
    def _record_scope(record) -> tuple[str | None, str | None]:
        """Return the common scope for direct and bindings-backed authority records."""
        if record is None:
            return None, None
        bindings = getattr(record, "bindings", None)
        return (
            getattr(bindings, "project_id", None) if bindings is not None else getattr(record, "project_id", None),
            getattr(bindings, "lifecycle_id", None) if bindings is not None else getattr(record, "lifecycle_id", None),
        )

    @staticmethod
    def _budget(attempt) -> AgentTraceBudget | None:
        if attempt is None:
            return None
        return AgentTraceBudget(
            steps_used=attempt.steps_used,
            model_calls_used=attempt.model_calls_used,
            action_proposals_used=attempt.action_proposals_used,
            repairs_used=attempt.repairs_used,
            input_tokens_used=attempt.input_tokens_used,
            output_tokens_used=attempt.output_tokens_used,
        )

    def _first_context(self, entries: Iterable[AgentTraceEntry]):
        for entry in entries:
            for reference in entry.context_refs:
                if reference.ref_type == "context" and reference.status == "present":
                    return self.store.get_agent_harness_context(reference.ref_id)
        return None
