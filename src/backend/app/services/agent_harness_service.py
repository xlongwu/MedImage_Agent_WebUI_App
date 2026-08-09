"""One-step, lease-based control plane for the optional single Agent Harness."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from src.backend.app.agent_skills.loader import AgentSkillLoader
from src.backend.app.core.config_schema import AgentHarnessConfig
from src.backend.app.planner.agent_model_adapter import (
    ActionCallMetadata,
    ActionProposal,
    AgentModelAdapter,
    AgentModelInvalidOutputError,
    AgentModelProviderError,
    DefaultAgentModelAdapter,
)
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.runtime.agent_capability_catalog import assert_capability_allowed
from src.backend.app.schemas.agent_harness import (
    ActionEnvelope,
    AgentHarnessAttempt,
    AgentHarnessContext,
    AgentHarnessStep,
    ModelCallRecord,
)
from src.backend.app.schemas.agent_lifecycle import (
    DecisionItem,
    PendingDecisionBatch,
    PendingDecisionOption,
)
from src.backend.app.services.agent_evidence_service import AgentEvidenceService
from src.backend.app.services.agent_harness_context_service import (
    AgentContextLimitExceededError,
    HarnessContextBuilder,
    HarnessContextSources,
)
from src.backend.app.services.agent_orchestrator import AgentOrchestrator


@dataclass(frozen=True)
class HarnessRunResult:
    lifecycle: object
    attempt: AgentHarnessAttempt | None


@dataclass(frozen=True)
class HarnessLoopResult:
    """Outcome of one finite scheduler wake-up.

    This is intentionally an internal service result.  The lifecycle remains
    the only user-facing state authority.
    """

    outcome: str
    steps_run: int
    lifecycle: object
    attempt: AgentHarnessAttempt | None
    reason: str | None = None


@dataclass(frozen=True)
class HarnessActionResult:
    lifecycle: object
    attempt_status: str
    terminal_reason: str | None
    result_explanation: object | None = None
    action_result_code: str | None = None


class _ModelCallFailure(RuntimeError):
    """A provider outcome whose redacted call entry is already durable."""

    def __init__(self, *, code: str, step: AgentHarnessStep) -> None:
        super().__init__(code)
        self.code = code
        self.step = step


class AgentHarnessService:
    """Run at most one advice-only step; it has no execution dependencies."""

    MAX_LEASE_TAKEOVERS = 2
    _SAFE_LEDGER_TEXT = re.compile(r"[^A-Za-z0-9._:/-]+")

    def __init__(
        self,
        store,
        *,
        config: AgentHarnessConfig,
        adapter: AgentModelAdapter | None = None,
        context_builder: HarnessContextBuilder | None = None,
        skill_loader: AgentSkillLoader | None = None,
        draft_plan: Callable[..., object] | None = None,
        recovery_proposer: Callable[..., object] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.config = config
        self.adapter = adapter or DefaultAgentModelAdapter()
        self.context_builder = context_builder or HarnessContextBuilder()
        self.skill_loader = skill_loader or AgentSkillLoader()
        self.draft_plan = draft_plan
        self.recovery_proposer = recovery_proposer
        self.now = now or (lambda: datetime.now(UTC))
        self.orchestrator = AgentOrchestrator(store)

    def active(self, *, provider_ref: str) -> bool:
        return self.config.enabled and bool(provider_ref.strip())

    def ensure_attempt(self, *, lifecycle, provider_ref: str) -> AgentHarnessAttempt:
        existing = self.store.get_agent_harness_attempt(lifecycle.lifecycle_id)
        if existing is not None:
            if existing.project_id != lifecycle.project_id:
                raise RuntimeError("AGENT_HARNESS_PROJECT_BINDING_INVALID")
            return existing
        now = self.now()
        attempt = AgentHarnessAttempt(
            attempt_id=f"harness_attempt_{uuid4().hex}",
            lifecycle_id=lifecycle.lifecycle_id,
            project_id=lifecycle.project_id,
            provider_ref=provider_ref,
            model_call_phase_allocations={"planning": 4, "result_recovery": 2},
            deadline_at=now + timedelta(seconds=self.config.max_wall_seconds),
            created_at=now,
            updated_at=now,
        )
        try:
            return self.store.create_agent_harness_attempt(attempt)
        except Exception:
            duplicate = self.store.get_agent_harness_attempt(lifecycle.lifecycle_id)
            if duplicate is None:
                raise
            return duplicate

    def resume(self, *, lifecycle, provider_ref: str, actor: str) -> HarnessRunResult:
        """Compatibility entry point for one explicitly requested step."""
        self.prepare_resume(lifecycle=lifecycle, provider_ref=provider_ref)
        return self.run_one(lifecycle=lifecycle, actor=actor)

    def prepare_resume(self, *, lifecycle, provider_ref: str) -> AgentHarnessAttempt:
        attempt = self.ensure_attempt(lifecycle=lifecycle, provider_ref=provider_ref)
        if attempt.status == "WAITING_FOR_USER":
            attempt = self._transition_attempt(attempt, status="READY", terminal_reason=None)
        return attempt

    def stop(self, *, lifecycle_id: str, reason: str) -> AgentHarnessAttempt | None:
        attempt = self.store.get_agent_harness_attempt(lifecycle_id)
        if attempt is None or attempt.status in {"FINISHED", "STOPPED", "FAILED"}:
            return attempt
        return self._transition_attempt(attempt, status="STOPPED", terminal_reason=reason, clear_lease=True)

    def run_one(self, *, lifecycle, actor: str, lease_owner: str | None = None) -> HarnessRunResult:
        attempt = self.store.get_agent_harness_attempt(lifecycle.lifecycle_id)
        if attempt is None:
            return HarnessRunResult(lifecycle=lifecycle, attempt=None)
        terminal_reflector_wake = (
            attempt.status == "READY"
            and attempt.last_wake_reason == "run_reconciled"
            and lifecycle.state in {"SUCCEEDED", "GOAL_SATISFIED", "HUMAN_HANDOFF"}
        )
        if lifecycle.project_id != attempt.project_id or (
            lifecycle.state in {"CANCELED", "SUCCEEDED", "GOAL_SATISFIED", "HUMAN_HANDOFF"}
            and not terminal_reflector_wake
        ):
            return HarnessRunResult(lifecycle=lifecycle, attempt=self.stop(lifecycle_id=lifecycle.lifecycle_id, reason="LIFECYCLE_TERMINAL"))
        if attempt.status in {"FINISHED", "STOPPED", "FAILED", "WAITING_FOR_USER"}:
            return HarnessRunResult(lifecycle=lifecycle, attempt=attempt)
        claimed = self._claim(attempt, lease_owner or f"harness-{uuid4().hex}")
        if claimed is None:
            return HarnessRunResult(lifecycle=lifecycle, attempt=self.store.get_agent_harness_attempt(lifecycle.lifecycle_id))
        if reason := self._budget_stop_reason(claimed):
            return HarnessRunResult(lifecycle=lifecycle, attempt=self._stop_claimed(claimed, reason))
        project = self.store.get_project(lifecycle.project_id)
        evidence_hash = str((lifecycle.command_context or {}).get("evidence_snapshot_hash") or "")
        evidence = self.store.get_agent_evidence_snapshot(evidence_hash) if evidence_hash and hasattr(self.store, "get_agent_evidence_snapshot") else None
        if evidence is not None:
            evidence = AgentEvidenceService.select_for_context(evidence, lifecycle_state=lifecycle.state)
        observation = self._record("get_observation", lifecycle.observation_id)
        evaluation = self._record("get_goal_evaluation", lifecycle.goal_evaluation_id)
        proposal = self._record("get_recovery_proposal", lifecycle.recovery_proposal_id)
        result_summary = self._result_summary(lifecycle, observation, evaluation)
        reviewed_plan = self._record("get_reviewed_plan", lifecycle.reviewed_plan_id)
        run_link = self._run_link(lifecycle)
        last_step = self._last_step(claimed)
        try:
            base_context = self.context_builder.build(sources=HarnessContextSources(
                lifecycle=lifecycle, project=project, evidence_snapshot=evidence,
                reviewed_plan=reviewed_plan, run_link=run_link, observation=observation,
                evaluation=evaluation, recovery_proposal=proposal, result_summary=result_summary,
                last_step=last_step, attempt=claimed,
            ))
            skills = self.skill_loader.load_for_state(state=lifecycle.state, context=base_context)
            built_context = self.context_builder.build(sources=HarnessContextSources(
                lifecycle=lifecycle, project=project, evidence_snapshot=evidence,
                reviewed_plan=reviewed_plan, run_link=run_link, observation=observation,
                evaluation=evaluation, recovery_proposal=proposal, result_summary=result_summary,
                last_step=last_step, attempt=claimed, skill_refs=skills.references,
                skill_error_codes=skills.error_codes,
            ))
        except AgentContextLimitExceededError:
            return HarnessRunResult(
                lifecycle=lifecycle,
                attempt=self._stop_claimed(claimed, "AGENT_CONTEXT_LIMIT_EXCEEDED"),
            )
        # Always rebuild from explicit current sources.  A previously stored
        # attempt hash must not hide a changed dynamic section.
        persisted_context = None
        get_context = getattr(self.store, "get_agent_harness_context", None)
        if callable(get_context):
            persisted_context = get_context(built_context.context_hash)
        context = persisted_context or built_context
        self.store.add_agent_harness_context(context)
        claimed = self._with_attempt(claimed, context_hash=context.context_hash)
        claimed = self.store.update_agent_harness_attempt(
            claimed,
            expected_status="RUNNING",
            expected_step_no=claimed.next_step_no,
            expected_lease_owner=claimed.lease_owner,
        )
        input_hash = stable_hash({"context_hash": context.context_hash, "state": lifecycle.state})
        key = f"{claimed.attempt_id}:{claimed.next_step_no}:{input_hash}"
        prior = self.store.get_agent_harness_step_by_idempotency(key)
        if prior is not None:
            return self._recover_completed_step(claimed=claimed, lifecycle=lifecycle, step=prior)
        step = AgentHarnessStep(
            step_id=f"harness_step_{uuid4().hex}", attempt_id=claimed.attempt_id,
            project_id=claimed.project_id, step_no=claimed.next_step_no, idempotency_key=key,
            input_hash=input_hash, validation_result="error", state_before=lifecycle.state,
            skill_refs=context.skill_refs, started_at=self.now(), summary="Model action requested.",
        )
        self.store.add_agent_harness_step(step)
        try:
            envelope, step = self._propose_with_one_repair(context, claimed, step)
        except _ModelCallFailure as exc:
            code = exc.code
            fallback_to = "deterministic_goal_planner" if (
                code == "AGENT_HARNESS_PROVIDER_UNAVAILABLE" and self.draft_plan is not None
            ) else None
            calls = tuple(
                call.model_copy(update={"fallback_to": fallback_to}) if fallback_to else call
                for call in exc.step.model_calls
            )
            completed = exc.step.model_copy(update={
                "model_calls": calls,
                "completed_at": self.now(),
                "error_code": code,
                "summary": "Harness provider failed; the enabled Harness stopped without creating a plan.",
            })
            self.store.update_agent_harness_step(completed)
            failed = self._stop_claimed(
                claimed, code, model_calls=completed.model_calls, consume_step=True,
            )
            if code == "AGENT_HARNESS_PROVIDER_UNAVAILABLE":
                return self._fallback_to_deterministic_planner(
                    lifecycle=lifecycle, attempt=failed, actor=actor, reason=code,
                )
            return HarnessRunResult(lifecycle=lifecycle, attempt=failed)

        try:
            self._validate_envelope(envelope, lifecycle, context)
            if (
                envelope.kind == "propose_recovery"
                and claimed.recovery_attempts_used >= self.config.max_recovery_attempts
            ):
                raise RuntimeError("AGENT_HARNESS_RECOVERY_BUDGET_EXHAUSTED")
        except Exception as exc:
            code = str(exc).split(":", 1)[0] or "AGENT_MODEL_OUTPUT_INVALID"
            completed = step.model_copy(update={
                "kind": envelope.kind,
                "completed_at": self.now(),
                "error_code": code,
                "validation_result": "rejected",
                "summary": "Harness action was rejected safely.",
            })
            self.store.update_agent_harness_step(completed)
            return HarnessRunResult(
                lifecycle=lifecycle,
                attempt=self._stop_claimed(
                    claimed, code, model_calls=completed.model_calls,
                    proposal_increment=1, consume_step=True,
                ),
            )

        try:
            applied = self._apply(envelope, lifecycle, actor)
            explanation = applied.result_explanation
            completed = step.model_copy(update={
                "kind": envelope.kind,
                "action_hash": stable_hash(envelope.model_dump(mode="json")),
                "requested_capability": envelope.kind, "validation_result": "accepted",
                "state_after": applied.lifecycle.state, "summary": self._summary(envelope.reason),
                "observation_ref": lifecycle.observation_id,
                "evaluation_ref": lifecycle.goal_evaluation_id,
                "recovery_proposal_ref": lifecycle.recovery_proposal_id,
                "result_explanation_hash": (
                    stable_hash(explanation.model_dump(mode="json")) if explanation is not None else None
                ),
                "generated_text": explanation.generated_text if explanation is not None else None,
                "action_result_code": applied.action_result_code,
                "action_result_hash": stable_hash({
                    "lifecycle_id": applied.lifecycle.lifecycle_id,
                    "state_after": applied.lifecycle.state,
                    "attempt_status": applied.attempt_status,
                    "terminal_reason": applied.terminal_reason,
                    "result_explanation_hash": (
                        stable_hash(explanation.model_dump(mode="json")) if explanation is not None else None
                    ),
                    "action_result_code": applied.action_result_code,
                }),
                "completed_at": self.now(),
            })
            self.store.update_agent_harness_step(completed)
            if explanation is not None:
                self._record_result_explanation_event(
                    lifecycle=applied.lifecycle,
                    step=completed,
                    explanation=explanation,
                )
            stop_reason = self._post_completion_budget_reason(
                claimed,
                model_calls=completed.model_calls,
                proposal_increment=1,
                recovery_attempt_increment=1 if envelope.kind == "propose_recovery" else 0,
            )
            finished = self._complete_claim(
                claimed,
                status="STOPPED" if stop_reason and applied.attempt_status == "READY" else applied.attempt_status,
                terminal_reason=stop_reason or applied.terminal_reason,
                model_calls=completed.model_calls,
                proposal_increment=1,
                recovery_attempt_increment=1 if envelope.kind == "propose_recovery" else 0,
            )
            return HarnessRunResult(lifecycle=applied.lifecycle, attempt=finished)
        except Exception:
            completed = step.model_copy(update={"completed_at": self.now(), "error_code": "AGENT_HARNESS_STEP_FAILED", "summary": "Harness step stopped safely."})
            self.store.update_agent_harness_step(completed)
            return HarnessRunResult(
                lifecycle=lifecycle,
                attempt=self._stop_claimed(
                    claimed, "AGENT_HARNESS_STEP_FAILED", model_calls=completed.model_calls,
                    proposal_increment=1, consume_step=True,
                ),
            )

    def run_until_blocked(
        self,
        *,
        lifecycle,
        actor: str,
        wake_reason: str,
        lease_owner: str,
        wake_fingerprint: str | None = None,
    ) -> HarnessLoopResult:
        """Advance only bounded, independently committed Harness steps.

        Each iteration reloads the lifecycle and attempt after ``run_one``.
        No transaction spans steps, so a process loss can recover from the
        persisted step idempotency key and lease fence instead of holding a
        lifecycle-wide lock.
        """
        steps_run = 0
        current_lifecycle = lifecycle
        attempt = self.store.get_agent_harness_attempt(lifecycle.lifecycle_id)
        if attempt is None:
            return HarnessLoopResult("stopped", steps_run, current_lifecycle, None, "ATTEMPT_MISSING")
        if attempt.status == "READY":
            attempt = self._mark_wake(attempt, wake_reason, wake_fingerprint)

        while True:
            current_lifecycle = self.store.get_agent_lifecycle(lifecycle.lifecycle_id) or current_lifecycle
            attempt = self.store.get_agent_harness_attempt(lifecycle.lifecycle_id)
            outcome = self._blocked_outcome(current_lifecycle, attempt)
            if outcome is not None:
                attempt = self._close_terminal_attempt(
                    lifecycle=current_lifecycle,
                    attempt=attempt,
                    outcome=outcome[0],
                )
                return HarnessLoopResult(outcome[0], steps_run, current_lifecycle, attempt, outcome[1])
            if steps_run >= self.config.max_steps_per_wakeup:
                yielded = self._yield(attempt)
                return HarnessLoopResult("yielded", steps_run, current_lifecycle, yielded, "MAX_STEPS_PER_WAKEUP")

            before_step = attempt.next_step_no
            result = self.run_one(
                lifecycle=current_lifecycle,
                actor=actor,
                lease_owner=f"{lease_owner}:{before_step}",
            )
            current_lifecycle = self.store.get_agent_lifecycle(lifecycle.lifecycle_id) or result.lifecycle
            attempt = self.store.get_agent_harness_attempt(lifecycle.lifecycle_id) or result.attempt
            if attempt is None or attempt.next_step_no == before_step:
                return HarnessLoopResult("stopped", steps_run, current_lifecycle, attempt, "LEASE_OR_FENCE_REJECTED")
            steps_run += 1

    def _blocked_outcome(self, lifecycle, attempt: AgentHarnessAttempt | None) -> tuple[str, str | None] | None:
        if attempt is None:
            return "stopped", "ATTEMPT_MISSING"
        if lifecycle.project_id != attempt.project_id:
            return "stopped", "AGENT_HARNESS_PROJECT_BINDING_INVALID"
        if lifecycle.state in {"WAITING_FOR_INPUT", "WAITING_FOR_SCIENCE_DECISION"}:
            return "waiting_for_user", lifecycle.state
        if lifecycle.state in {"WAITING_FOR_APPROVAL", "WAITING_FOR_RECOVERY_APPROVAL", "RECOVERY_PROPOSED"}:
            return "waiting_for_approval", lifecycle.state
        if lifecycle.state in {"RUNNING", "RETRYING", "RECOVERING"}:
            return "waiting_for_runtime", lifecycle.state
        if lifecycle.state == "HUMAN_HANDOFF":
            if attempt.status == "FINISHED":
                return "handoff", attempt.terminal_reason
            if attempt.status == "READY" and attempt.last_wake_reason == "run_reconciled":
                return None
            return "handoff", lifecycle.state
        if lifecycle.state == "CANCELED":
            return "canceled", lifecycle.state
        if lifecycle.state in {"GOAL_SATISFIED", "SUCCEEDED"}:
            if attempt.status == "FINISHED":
                return "finished", attempt.terminal_reason
            if attempt.status == "READY" and attempt.last_wake_reason == "run_reconciled":
                return None
            return "finished", lifecycle.state
        if attempt.status == "WAITING_FOR_USER":
            return "waiting_for_user", attempt.status
        if attempt.status == "FINISHED":
            return "finished", attempt.terminal_reason
        if attempt.status in {"STOPPED", "FAILED"}:
            return "stopped", attempt.terminal_reason
        if attempt.status == "RUNNING":
            return "stopped", "LEASE_ACTIVE"
        return None

    def _propose_with_one_repair(
        self,
        context: AgentHarnessContext,
        attempt: AgentHarnessAttempt,
        step: AgentHarnessStep,
    ) -> tuple[ActionEnvelope, AgentHarnessStep]:
        try:
            proposal, completed_step = self._invoke_model(
                context=context, attempt=attempt, step=step, repair=False,
            )
            return proposal.envelope, completed_step
        except _ModelCallFailure as failure:
            if failure.code != "AGENT_HARNESS_MODEL_OUTPUT_INVALID":
                raise
            repair_reason = self._repair_budget_stop_reason(attempt, failure.step)
            if repair_reason:
                raise _ModelCallFailure(code=repair_reason, step=failure.step) from failure
            proposal, completed_step = self._invoke_model(
                context=context, attempt=attempt, step=failure.step, repair=True,
            )
            return proposal.envelope, completed_step

    def _invoke_model(
        self,
        *,
        context: AgentHarnessContext,
        attempt: AgentHarnessAttempt,
        step: AgentHarnessStep,
        repair: bool,
    ) -> tuple[ActionProposal, AgentHarnessStep]:
        """Persist a call-start row before invoking an untrusted provider."""
        started = self.now()
        phase = self._phase_for(lifecycle_state=step.state_before)
        rendered_skills = self.skill_loader.render(context.skill_refs)
        pending_call = ModelCallRecord(
            call_id=f"harness_call_{uuid4().hex}", step_id=step.step_id,
            attempt_id=attempt.attempt_id, provider=attempt.provider_ref.strip().casefold()[:64] or "unknown",
            phase=phase, endpoint_class="rule_based" if attempt.provider_ref == "rule_based" else "chat_completions",
            prompt_template_version=context.prompt_template_version, context_hash=context.context_hash,
            skill_hashes=tuple(reference.content_hash for reference in context.skill_refs),
            skill_error_codes=tuple(sorted(set(context.skill_error_codes + rendered_skills.error_codes))),
            request_hash=stable_hash({
                "attempt_id": attempt.attempt_id, "step_id": step.step_id,
                "context_hash": context.context_hash, "provider": attempt.provider_ref,
                "repair": repair,
            }),
            repair=repair, started_at=started,
        )
        started_step = step.model_copy(update={"model_calls": (*step.model_calls, pending_call)})
        self.store.update_agent_harness_step(started_step)
        try:
            proposal = self.adapter.propose_action(
                snapshot=context.prompt_payload(), provider_ref=attempt.provider_ref, repair=repair,
            )
            if not isinstance(proposal, ActionProposal):
                raise TypeError("AGENT_HARNESS_ADAPTER_CONTRACT_INVALID")
        except AgentModelProviderError as exc:
            status = "invalid_output" if isinstance(exc, AgentModelInvalidOutputError) else "failed"
            completed = self._complete_model_call(
                pending_call, metadata=exc.metadata, schema_valid=False, status=status, error_code=exc.code,
            )
            completed_step = started_step.model_copy(
                update={"model_calls": (*step.model_calls, completed)}
            )
            self.store.update_agent_harness_step(completed_step)
            raise _ModelCallFailure(code=exc.code, step=completed_step) from exc
        except (ValueError, TypeError) as exc:
            metadata = ActionCallMetadata(
                provider=pending_call.provider, model=None, endpoint_class=pending_call.endpoint_class,
                response_hash=None, input_tokens=None, output_tokens=None, cached_input_tokens=None,
                latency_ms=None, provider_request_id=None, network_called=False,
            )
            completed = self._complete_model_call(
                pending_call, metadata=metadata, schema_valid=False, status="invalid_output",
                error_code="AGENT_HARNESS_MODEL_OUTPUT_INVALID",
            )
            completed_step = started_step.model_copy(
                update={"model_calls": (*step.model_calls, completed)}
            )
            self.store.update_agent_harness_step(completed_step)
            raise _ModelCallFailure(
                code="AGENT_HARNESS_MODEL_OUTPUT_INVALID", step=completed_step,
            ) from exc
        completed = self._complete_model_call(
            pending_call, metadata=proposal.metadata, schema_valid=True, status="succeeded",
        )
        completed_step = started_step.model_copy(update={"model_calls": (*step.model_calls, completed)})
        self.store.update_agent_harness_step(completed_step)
        return proposal, completed_step

    def _complete_model_call(
        self,
        pending: ModelCallRecord,
        *,
        metadata: ActionCallMetadata,
        schema_valid: bool,
        status: str,
        error_code: str | None = None,
    ) -> ModelCallRecord:
        return pending.model_copy(update={
            "provider": self._safe_ledger_text(metadata.provider, limit=64) or pending.provider,
            "model": self._safe_ledger_text(metadata.model, limit=128),
            "endpoint_class": self._safe_ledger_text(metadata.endpoint_class, limit=64) or pending.endpoint_class,
            "response_hash": self._safe_ledger_text(metadata.response_hash, limit=128),
            "schema_valid": schema_valid,
            "completed_at": self.now(),
            "latency_ms": metadata.latency_ms,
            "input_tokens": metadata.input_tokens,
            "output_tokens": metadata.output_tokens,
            "cached_input_tokens": metadata.cached_input_tokens,
            "provider_request_id": self._safe_ledger_text(metadata.provider_request_id, limit=128),
            "network_called": metadata.network_called,
            "status": status,
            "error_code": self._safe_ledger_text(error_code, limit=128),
        })

    def _repair_budget_stop_reason(
        self, attempt: AgentHarnessAttempt, step: AgentHarnessStep
    ) -> str | None:
        if sum(call.repair for call in step.model_calls) >= self.config.max_repairs:
            return "AGENT_HARNESS_REPAIR_BUDGET_EXHAUSTED"
        if attempt.model_calls_used + self._network_call_count(step.model_calls) >= self.config.max_model_calls:
            return "AGENT_HARNESS_MODEL_CALL_BUDGET_EXHAUSTED"
        return None

    def _validate_envelope(self, envelope: ActionEnvelope, lifecycle, context: AgentHarnessContext) -> None:
        if envelope.expected_state != lifecycle.state:
            raise ValueError("AGENT_HARNESS_STALE_ACTION")
        assert_capability_allowed(envelope.kind, lifecycle.state)
        roots = set(type(context.sections).model_fields)
        if any(ref.split(".", 1)[0] not in roots for ref in envelope.input_refs):
            raise ValueError("AGENT_HARNESS_REFERENCE_DENIED")
        if envelope.kind == "request_decision":
            self._validate_decision_payload(envelope.payload)
        if envelope.kind == "explain_result" and set(envelope.payload) - {"generated_text"}:
            raise ValueError("AGENT_HARNESS_EXPLANATION_PAYLOAD_INVALID")

    def _apply(self, envelope: ActionEnvelope, lifecycle, actor: str) -> HarnessActionResult:
        if envelope.kind == "draft_plan":
            if self.draft_plan is None:
                raise RuntimeError("AGENT_HARNESS_DRAFT_PLAN_UNAVAILABLE")
            result = self.draft_plan(lifecycle=lifecycle, command_id=f"harness:{lifecycle.lifecycle_id}", actor=actor)
            status = "WAITING_FOR_USER" if result.state in {"WAITING_FOR_INPUT", "WAITING_FOR_SCIENCE_DECISION"} else "READY"
            if result.state in {"GOAL_SATISFIED", "SUCCEEDED", "HUMAN_HANDOFF", "CANCELED"}:
                status = "FINISHED"
            return HarnessActionResult(result, status, None)
        if envelope.kind == "request_decision":
            decision = self._decision_from_payload(envelope.payload, lifecycle)
            state = "WAITING_FOR_SCIENCE_DECISION" if decision.items[0].kind not in {"missing_input", "goal_revision"} else "WAITING_FOR_INPUT"
            if lifecycle.state == "CREATED" and state == "WAITING_FOR_SCIENCE_DECISION":
                lifecycle = self.orchestrator.transition(
                    project_id=lifecycle.project_id, lifecycle_id=lifecycle.lifecycle_id,
                    to_state="CONTEXT_READY", command_id=f"harness:{lifecycle.lifecycle_id}:context",
                    actor=actor, source_command="harness_context_ready",
                )
            result = self.orchestrator.transition(
                project_id=lifecycle.project_id, lifecycle_id=lifecycle.lifecycle_id, to_state=state,
                command_id=f"harness:{lifecycle.lifecycle_id}:decision:{decision.batch_id}", actor=actor,
                source_command="harness_decision_required", updates={"pending_decision_batch": decision}, reason=decision.items[0].impact,
            )
            return HarnessActionResult(result, "WAITING_FOR_USER", None)
        if envelope.kind == "propose_recovery":
            if self.recovery_proposer is None:
                raise RuntimeError("AGENT_HARNESS_RECOVERY_UNAVAILABLE")
            result = self.recovery_proposer(lifecycle=lifecycle, actor=actor)
            lifecycle_result = result[0] if isinstance(result, tuple) else result
            return HarnessActionResult(lifecycle_result, "READY", None)
        if envelope.kind == "explain_result":
            observation = self._record("get_observation", lifecycle.observation_id)
            evaluation = self._record("get_goal_evaluation", lifecycle.goal_evaluation_id)
            if observation is None or evaluation is None:
                raise RuntimeError("AGENT_EXPLANATION_EVIDENCE_REQUIRED")
            from src.backend.app.services.agent_task_result_summary import (
                AgentTaskResultSummaryService,
            )

            explanation = AgentTaskResultSummaryService().build_explanation(
                lifecycle=lifecycle,
                observation=observation,
                evaluation=evaluation,
                generated_text=envelope.payload.get("generated_text"),
            )
            return HarnessActionResult(
                lifecycle,
                "FINISHED",
                None,
                result_explanation=explanation,
                action_result_code=(
                    "AGENT_EXPLANATION_CONFLICT"
                    if explanation.generated_text_status == "conflict_rejected"
                    else None
                ),
            )
        # Evidence is non-mutating, and finish only terminates the attempt.
        return HarnessActionResult(
            lifecycle,
            "FINISHED" if envelope.kind == "finish" else "READY",
            "MODEL_FINISHED" if envelope.kind == "finish" else None,
        )

    def _claim(self, attempt: AgentHarnessAttempt, owner: str) -> AgentHarnessAttempt | None:
        now = self.now()
        if attempt.status == "RUNNING":
            if attempt.lease_expires_at is None or attempt.lease_expires_at > now or attempt.lease_takeovers >= self.MAX_LEASE_TAKEOVERS:
                return None
            takeovers = attempt.lease_takeovers + 1
            expected = "RUNNING"
        elif attempt.status == "READY":
            takeovers = attempt.lease_takeovers
            expected = "READY"
        else:
            return None
        claimed = self._with_attempt(
            attempt, status="RUNNING", lease_owner=owner,
            lease_expires_at=now + timedelta(seconds=self.config.lease_seconds), lease_takeovers=takeovers,
        )
        try:
            return self.store.update_agent_harness_attempt(
                claimed,
                expected_status=expected,
                expected_step_no=attempt.next_step_no,
                expected_context_hash=attempt.context_hash,
                expected_lease_owner=attempt.lease_owner,
            )
        except RuntimeError:
            return None

    def _budget_stop_reason(self, attempt: AgentHarnessAttempt) -> str | None:
        if attempt.steps_used >= self.config.max_steps:
            return "AGENT_HARNESS_STEP_BUDGET_EXHAUSTED"
        if attempt.model_calls_used >= self.config.max_model_calls:
            return "AGENT_HARNESS_MODEL_CALL_BUDGET_EXHAUSTED"
        if attempt.action_proposals_used >= self.config.max_action_proposals:
            return "AGENT_HARNESS_ACTION_PROPOSAL_BUDGET_EXHAUSTED"
        if self.now() >= attempt.deadline_at:
            return "AGENT_HARNESS_WALL_TIME_BUDGET_EXHAUSTED"
        return None

    def _post_completion_budget_reason(
        self,
        attempt: AgentHarnessAttempt,
        *,
        model_calls: tuple[ModelCallRecord, ...],
        proposal_increment: int,
        recovery_attempt_increment: int,
    ) -> str | None:
        next_steps = attempt.steps_used + 1
        next_calls = attempt.model_calls_used + self._network_call_count(model_calls)
        next_actions = attempt.action_proposals_used + proposal_increment
        next_recoveries = attempt.recovery_attempts_used + recovery_attempt_increment
        input_tokens = self._accumulate_optional(attempt.input_tokens_used, model_calls, "input_tokens")
        output_tokens = self._accumulate_optional(attempt.output_tokens_used, model_calls, "output_tokens")
        if next_steps >= self.config.max_steps:
            return "AGENT_HARNESS_STEP_BUDGET_EXHAUSTED"
        if next_calls >= self.config.max_model_calls:
            return "AGENT_HARNESS_MODEL_CALL_BUDGET_EXHAUSTED"
        if next_actions >= self.config.max_action_proposals:
            return "AGENT_HARNESS_ACTION_PROPOSAL_BUDGET_EXHAUSTED"
        if next_recoveries >= self.config.max_recovery_attempts and recovery_attempt_increment:
            return "AGENT_HARNESS_RECOVERY_BUDGET_EXHAUSTED"
        if self.config.max_input_tokens is not None and input_tokens is not None and input_tokens >= self.config.max_input_tokens:
            return "AGENT_HARNESS_INPUT_TOKEN_BUDGET_EXHAUSTED"
        if self.config.max_output_tokens is not None and output_tokens is not None and output_tokens >= self.config.max_output_tokens:
            return "AGENT_HARNESS_OUTPUT_TOKEN_BUDGET_EXHAUSTED"
        if self.now() >= attempt.deadline_at:
            return "AGENT_HARNESS_WALL_TIME_BUDGET_EXHAUSTED"
        return None

    def _complete_claim(
        self,
        attempt: AgentHarnessAttempt,
        *,
        status: str,
        terminal_reason: str | None,
        model_calls: tuple[ModelCallRecord, ...] = (),
        proposal_increment: int = 0,
        recovery_attempt_increment: int = 0,
    ) -> AgentHarnessAttempt:
        phase_usage = dict(attempt.model_call_phase_usage)
        for call in model_calls:
            if call.network_called:
                phase_usage[call.phase] = phase_usage.get(call.phase, 0) + 1
        updated = self._with_attempt(
            attempt, status=status, terminal_reason=terminal_reason,
            next_step_no=attempt.next_step_no + 1,
            model_calls_used=attempt.model_calls_used + self._network_call_count(model_calls),
            action_proposals_used=attempt.action_proposals_used + proposal_increment,
            steps_used=attempt.steps_used + 1,
            repairs_used=attempt.repairs_used + sum(call.repair for call in model_calls),
            recovery_attempts_used=attempt.recovery_attempts_used + recovery_attempt_increment,
            input_tokens_used=self._accumulate_optional(attempt.input_tokens_used, model_calls, "input_tokens"),
            output_tokens_used=self._accumulate_optional(attempt.output_tokens_used, model_calls, "output_tokens"),
            cached_input_tokens_used=self._accumulate_optional(attempt.cached_input_tokens_used, model_calls, "cached_input_tokens"),
            model_call_phase_usage=phase_usage,
            last_progress_at=self.now(),
            lease_owner=None, lease_expires_at=None,
        )
        return self.store.update_agent_harness_attempt(
            updated,
            expected_status="RUNNING",
            expected_step_no=attempt.next_step_no,
            expected_context_hash=attempt.context_hash,
            expected_lease_owner=attempt.lease_owner,
        )

    @staticmethod
    def _network_call_count(calls: tuple[ModelCallRecord, ...]) -> int:
        return sum(call.network_called for call in calls)

    @staticmethod
    def _accumulate_optional(
        current: int | None,
        calls: tuple[ModelCallRecord, ...],
        field: str,
    ) -> int | None:
        observed = [getattr(call, field) for call in calls if getattr(call, field) is not None]
        if not observed:
            return current
        return (current or 0) + sum(observed)

    @staticmethod
    def _phase_for(*, lifecycle_state: str) -> str:
        if lifecycle_state in {"GOAL_SATISFIED", "SUCCEEDED", "HUMAN_HANDOFF", "RECOVERING"}:
            return "result_recovery"
        return "planning"

    @classmethod
    def _safe_ledger_text(cls, value: object, *, limit: int) -> str | None:
        if not isinstance(value, str):
            return None
        cleaned = cls._SAFE_LEDGER_TEXT.sub("", value.strip())[:limit]
        return cleaned or None

    def _stop_claimed(
        self,
        attempt: AgentHarnessAttempt,
        reason: str,
        *,
        model_calls: tuple[ModelCallRecord, ...] = (),
        proposal_increment: int = 0,
        consume_step: bool = False,
    ) -> AgentHarnessAttempt:
        if consume_step:
            return self._complete_claim(
                attempt, status="STOPPED", terminal_reason=reason, model_calls=model_calls,
                proposal_increment=proposal_increment,
            )
        return self._transition_attempt(
            attempt, status="STOPPED", terminal_reason=reason, clear_lease=True,
        )

    def _transition_attempt(self, attempt: AgentHarnessAttempt, *, status: str, terminal_reason: str | None, clear_lease: bool = False) -> AgentHarnessAttempt:
        updated = self._with_attempt(
            attempt, status=status, terminal_reason=terminal_reason,
            lease_owner=None if clear_lease else attempt.lease_owner,
            lease_expires_at=None if clear_lease else attempt.lease_expires_at,
        )
        return self.store.update_agent_harness_attempt(updated, expected_status=attempt.status)

    def _mark_wake(
        self,
        attempt: AgentHarnessAttempt,
        wake_reason: str,
        wake_fingerprint: str | None,
    ) -> AgentHarnessAttempt:
        updated = self._with_attempt(
            attempt,
            last_wake_reason=wake_reason[:128],
            last_wake_fingerprint=wake_fingerprint,
        )
        return self.store.update_agent_harness_attempt(
            updated,
            expected_status="READY",
            expected_step_no=attempt.next_step_no,
            expected_context_hash=attempt.context_hash,
        )

    def _yield(self, attempt: AgentHarnessAttempt) -> AgentHarnessAttempt:
        if attempt.status != "READY":
            return attempt
        updated = self._with_attempt(attempt, yield_count=attempt.yield_count + 1)
        return self.store.update_agent_harness_attempt(
            updated,
            expected_status="READY",
            expected_step_no=attempt.next_step_no,
            expected_context_hash=attempt.context_hash,
        )

    def _close_terminal_attempt(
        self,
        *,
        lifecycle,
        attempt: AgentHarnessAttempt | None,
        outcome: str,
    ) -> AgentHarnessAttempt | None:
        if attempt is None or attempt.status != "READY":
            return attempt
        if outcome == "canceled":
            return self.stop(lifecycle_id=lifecycle.lifecycle_id, reason="LIFECYCLE_CANCELED")
        if outcome not in {"finished", "handoff"}:
            return attempt
        reason = f"LIFECYCLE_{lifecycle.state}"
        return self._transition_attempt(
            attempt,
            status="FINISHED",
            terminal_reason=reason,
            clear_lease=True,
        )

    def _recover_completed_step(
        self,
        *,
        claimed: AgentHarnessAttempt,
        lifecycle,
        step: AgentHarnessStep,
    ) -> HarnessRunResult:
        """Finish an accepted, persisted step after a crash without another model call."""
        if step.validation_result != "accepted" or step.completed_at is None:
            reason = step.error_code or "AGENT_HARNESS_CALL_OUTCOME_UNKNOWN"
            return HarnessRunResult(
                lifecycle=lifecycle,
                attempt=self._stop_claimed(
                    claimed, reason, model_calls=step.model_calls,
                    proposal_increment=1 if step.kind is not None else 0,
                    consume_step=True,
                ),
            )
        if (
            step.step_no != claimed.next_step_no
            or step.input_hash != stable_hash({"context_hash": claimed.context_hash, "state": lifecycle.state})
            or step.state_after != lifecycle.state
        ):
            return HarnessRunResult(
                lifecycle=lifecycle,
                attempt=self._stop_claimed(claimed, "AGENT_HARNESS_DUPLICATE_STEP"),
            )
        status = "WAITING_FOR_USER" if lifecycle.state in {"WAITING_FOR_INPUT", "WAITING_FOR_SCIENCE_DECISION"} else "READY"
        terminal_reason = None
        if step.kind == "finish":
            status = "FINISHED"
            terminal_reason = "MODEL_FINISHED"
        recovered = self._complete_claim(
            claimed,
            status=status,
            terminal_reason=terminal_reason,
            model_calls=step.model_calls,
            proposal_increment=1,
        )
        return HarnessRunResult(lifecycle=lifecycle, attempt=recovered)

    def _fallback_to_deterministic_planner(
        self,
        *,
        lifecycle,
        attempt: AgentHarnessAttempt,
        actor: str,
        reason: str,
    ) -> HarnessRunResult:
        if self.draft_plan is None:
            return HarnessRunResult(lifecycle=lifecycle, attempt=attempt)
        fallback_attempt = self._with_attempt(
            attempt,
            fallback_from=attempt.provider_ref,
            fallback_to="deterministic_goal_planner",
            fallback_reason=reason,
        )
        fallback_attempt = self.store.update_agent_harness_attempt(
            fallback_attempt,
            expected_status="STOPPED",
            expected_step_no=attempt.next_step_no,
            expected_context_hash=attempt.context_hash,
        )
        try:
            planned = self.draft_plan(
                lifecycle=lifecycle,
                command_id=f"harness-fallback:{lifecycle.lifecycle_id}:{attempt.next_step_no}",
                actor=actor,
            )
        except Exception:
            return HarnessRunResult(lifecycle=lifecycle, attempt=fallback_attempt)
        return HarnessRunResult(lifecycle=planned, attempt=fallback_attempt)

    def _with_attempt(self, attempt: AgentHarnessAttempt, **updates) -> AgentHarnessAttempt:
        return attempt.model_copy(update={**updates, "updated_at": self.now()})

    def _record(self, name: str, record_id: str | None):
        getter = getattr(self.store, name, None)
        return getter(record_id) if callable(getter) and record_id else None

    def _run_link(self, lifecycle):
        if not lifecycle.run_id:
            return None
        getter = getattr(self.store, "get_run_link_by_run_id", None)
        return getter(lifecycle.project_id, lifecycle.run_id) if callable(getter) else None

    def _last_step(self, attempt: AgentHarnessAttempt):
        getter = getattr(self.store, "list_agent_harness_steps", None)
        if not callable(getter):
            return None
        # A completed row for the in-flight step is an idempotency recovery
        # record, not a prior action result.  Including it would change the
        # reconstructed input hash and prevent recovery after a lost claim.
        steps = [step for step in getter(attempt.attempt_id) if step.step_no < attempt.next_step_no]
        return steps[-1] if steps else None

    def _record_result_explanation_event(self, *, lifecycle, step: AgentHarnessStep, explanation) -> None:
        self.orchestrator.record_event(
            project_id=lifecycle.project_id,
            lifecycle_id=lifecycle.lifecycle_id,
            command_id=f"harness:{step.step_id}:result-explanation",
            actor="system-harness",
            source_command="harness_result_explained",
            details={
                "result_explanation_hash": step.result_explanation_hash,
                "observation_ref": step.observation_ref,
                "evaluation_ref": step.evaluation_ref,
                "generated_text_status": explanation.generated_text_status,
            },
        )

    @staticmethod
    def _result_summary(lifecycle, observation, evaluation):
        if observation is None or evaluation is None:
            return None
        try:
            from src.backend.app.services.agent_task_result_summary import (
                AgentTaskResultSummaryService,
            )

            return AgentTaskResultSummaryService().build(
                lifecycle=lifecycle,
                observation=observation,
                evaluation=evaluation,
            )
        except Exception:
            return None

    @staticmethod
    def _summary(reason: str) -> str:
        return str(reason).replace("\n", " ")[:512]

    @staticmethod
    def _validate_decision_payload(payload: dict) -> None:
        required = {"kind", "question", "impact"}
        if set(payload) - {"kind", "question", "impact", "options", "recommended_option"} or not required <= set(payload):
            raise ValueError("AGENT_HARNESS_DECISION_PAYLOAD_INVALID")
        if str(payload["kind"]) not in {
            "missing_input", "goal_revision", "subject_id", "atlas", "global_signal_regression",
            "repetition_time", "template", "overwrite", "experimental_backend", "other",
        }:
            raise ValueError("AGENT_HARNESS_DECISION_PAYLOAD_INVALID")
        if len(str(payload["question"])) > 512 or len(str(payload["impact"])) > 512:
            raise ValueError("AGENT_HARNESS_DECISION_PAYLOAD_INVALID")

    def _decision_from_payload(self, payload: dict, lifecycle) -> PendingDecisionBatch:
        options = tuple(
            PendingDecisionOption(
                id=str(item["id"])[:128], label=str(item["label"])[:256],
                description=str(item.get("description") or "")[:512], recommended=bool(item.get("recommended")),
            )
            for item in payload.get("options", [])[:8]
            if isinstance(item, dict) and {"id", "label"} <= set(item)
        )
        item = DecisionItem(
            item_id=f"harness_{payload['kind']}", kind=str(payload["kind"]),
            question=str(payload["question"]), impact=str(payload["impact"]), options=options,
            recommended_option=(str(payload["recommended_option"]) if payload.get("recommended_option") else None),
        )
        evidence_hash = str((lifecycle.command_context or {}).get("evidence_snapshot_hash") or "harness-evidence-unavailable")
        return PendingDecisionBatch(
            batch_id=f"harness_decision_batch_{uuid4().hex}", lifecycle_id=lifecycle.lifecycle_id,
            project_id=lifecycle.project_id, evidence_snapshot_hash=evidence_hash, items=(item,),
            expires_at=self.now() + timedelta(hours=24), source="harness",
        )
