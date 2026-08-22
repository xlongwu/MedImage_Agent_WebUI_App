"""One-step, lease-based control plane for the optional single Agent Harness."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from src.backend.app.agent_skills.loader import AgentSkillLoader
from src.backend.app.core.config_schema import AgentHarnessConfig, AgentModelRuntimeConfig
from src.backend.app.core.agent_logging import agent_log_context
from src.backend.app.core.exceptions import SafetyError
from src.backend.app.planner.agent_model_adapter import (
    ActionCallMetadata,
    ActionProposal,
    AgentModelAdapter,
    AgentModelInvalidOutputError,
    AgentModelProviderError,
    DefaultAgentModelAdapter,
    REQUEST_BUILDER_VERSION,
    build_canonical_model_request,
    canonical_request_bytes,
)
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.runtime.agent_capability_catalog import (
    assert_capability_allowed,
    assert_capability_context_and_output_allowed,
)
from src.backend.app.schemas.agent_harness import (
    ActionEnvelope,
    AgentActionRecord,
    AgentHarnessAttempt,
    AgentHarnessContext,
    AgentHarnessStep,
    ModelCallRecord,
    parse_action_envelope,
)
from src.backend.app.services.agent_evidence_service import AgentEvidenceService
from src.backend.app.services.agent_harness_context_service import (
    AgentContextIncompleteError,
    AgentContextLimitExceededError,
    HarnessContextBuilder,
    HarnessContextSources,
)
from src.backend.app.services.agent_invariant_checker import AgentInvariantChecker
from src.backend.app.services.agent_orchestrator import AgentOrchestrator
from src.backend.app.services.agent_planning_action_service import (
    AgentPlanningActionService,
    HarnessActionResult,
)


logger = logging.getLogger(__name__)


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
        model_config: AgentModelRuntimeConfig | None = None,
        adapter: AgentModelAdapter | None = None,
        context_builder: HarnessContextBuilder | None = None,
        skill_loader: AgentSkillLoader | None = None,
        draft_plan: Callable[..., object] | None = None,
        planning_action_service: AgentPlanningActionService | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.config = config
        self.model_config = model_config or AgentModelRuntimeConfig()
        self.adapter = adapter or DefaultAgentModelAdapter(config=self.model_config)
        self.context_builder = context_builder or HarnessContextBuilder()
        self.skill_loader = skill_loader or AgentSkillLoader()
        self.draft_plan = draft_plan
        self.now = now or (lambda: datetime.now(UTC))
        self.orchestrator = AgentOrchestrator(store)
        self.planning_action_service = planning_action_service or AgentPlanningActionService(
            store,
            draft_plan=lambda **kwargs: self._draft_plan(**kwargs),
            now=self.now,
        )

    def _draft_plan(self, **kwargs):
        if self.draft_plan is None:
            raise RuntimeError("AGENT_HARNESS_DRAFT_PLAN_UNAVAILABLE")
        return self.draft_plan(**kwargs)

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
        AgentInvariantChecker(self.store, now=self.now).assert_clear(
            project_id=lifecycle.project_id,
            lifecycle_id=lifecycle.lifecycle_id,
        )
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
        # A reclaimed step may already contain a durable started provider call.
        # Reconcile it before reading fresh context: rebuilding cannot make an
        # unknown external outcome safe to retry.
        if claimed.context_hash:
            previous_key = f"{claimed.attempt_id}:{claimed.next_step_no}:{stable_hash({'context_hash': claimed.context_hash, 'state': lifecycle.state})}"
            prior = self.store.get_agent_harness_step_by_idempotency(previous_key)
            if prior is not None:
                return self._recover_completed_step(claimed=claimed, lifecycle=lifecycle, step=prior)
        project = self.store.get_project(lifecycle.project_id)
        evidence_hash = str((lifecycle.command_context or {}).get("evidence_snapshot_hash") or "")
        purpose = self.context_builder.purpose_for(lifecycle)
        try:
            if not evidence_hash:
                # Initial Harness wake-ups precede deterministic planning.  Build
                # the bounded registered snapshot here; no raw file is opened and
                # later reads still require its exact typed hash and scope.
                evidence_hash = AgentEvidenceService(self.store).build_snapshot(
                    project_id=lifecycle.project_id, lifecycle_id=lifecycle.lifecycle_id,
                ).snapshot_hash
            evidence = AgentEvidenceService(self.store).read_for_context(
                snapshot_hash=evidence_hash, project_id=lifecycle.project_id,
                lifecycle_id=lifecycle.lifecycle_id, purpose=purpose, now=self.now(),
            )
        except (SafetyError, AgentContextIncompleteError) as exc:
            code = getattr(exc, "code", None) or str(exc).split(":", 1)[0]
            return HarnessRunResult(
                lifecycle=lifecycle,
                attempt=self._stop_claimed(claimed, code or "AGENT_CONTEXT_EVIDENCE_MISSING"),
            )
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
                last_step=last_step, attempt=claimed, purpose=purpose,
            ))
            if not base_context.complete:
                raise AgentContextIncompleteError(base_context.incomplete_reason or "AGENT_CONTEXT_INCOMPLETE")
            skills = self.skill_loader.load_for_state(state=lifecycle.state, context=base_context)
            built_context = self.context_builder.build(sources=HarnessContextSources(
                lifecycle=lifecycle, project=project, evidence_snapshot=evidence,
                reviewed_plan=reviewed_plan, run_link=run_link, observation=observation,
                evaluation=evaluation, recovery_proposal=proposal, result_summary=result_summary,
                last_step=last_step, attempt=claimed, purpose=purpose, skill_refs=skills.references,
                skill_error_codes=skills.error_codes,
            ))
            if not built_context.complete:
                raise AgentContextIncompleteError(built_context.incomplete_reason or "AGENT_CONTEXT_INCOMPLETE")
        except (AgentContextLimitExceededError, AgentContextIncompleteError) as exc:
            return HarnessRunResult(
                lifecycle=lifecycle,
                attempt=self._stop_claimed(claimed, str(exc)),
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

        accepted_action = AgentActionRecord(
            action_id=f"harness_action_{uuid4().hex}",
            attempt_id=claimed.attempt_id,
            step_id=step.step_id,
            request_hash=step.model_calls[-1].request_hash,
            response_hash=step.model_calls[-1].response_hash,
            action_hash=stable_hash(envelope.model_dump(mode="json")),
            kind=envelope.kind,
            expected_state=envelope.expected_state,
            action_payload=envelope.model_dump(mode="json"),
            status="accepted",
            created_at=self.now(),
        )
        try:
            self.store.add_agent_harness_action(accepted_action)
        except Exception:
            completed = step.model_copy(update={
                "kind": envelope.kind,
                "completed_at": self.now(),
                "error_code": "AGENT_HARNESS_ACTION_RECORD_FAILED",
                "summary": "Harness action was not applied because its accepted record was not durable.",
            })
            self.store.update_agent_harness_step(completed)
            return HarnessRunResult(
                lifecycle=lifecycle,
                attempt=self._stop_claimed(
                    claimed, "AGENT_HARNESS_ACTION_RECORD_FAILED", model_calls=completed.model_calls,
                    proposal_increment=1, consume_step=True,
                ),
            )
        try:
            applied = self._apply(envelope, lifecycle, actor)
        except Exception as exc:
            error_code = str(exc).split(":", 1)[0] or "AGENT_HARNESS_STEP_FAILED"
            rejected_action = accepted_action.model_copy(update={
                "status": "rejected", "error_code": error_code[:128], "completed_at": self.now(),
            })
            self.store.update_agent_harness_action(rejected_action, expected_status="accepted")
            completed = step.model_copy(update={
                "kind": envelope.kind,
                "action_id": accepted_action.action_id,
                "action_hash": accepted_action.action_hash,
                "completed_at": self.now(),
                "error_code": error_code,
                "action_result_code": error_code,
                "validation_result": "accepted",
                "summary": "Harness action application was rejected safely.",
            })
            self.store.update_agent_harness_step(completed)
            return HarnessRunResult(
                lifecycle=lifecycle,
                attempt=self._stop_claimed(
                    claimed, error_code, model_calls=completed.model_calls,
                    proposal_increment=1, consume_step=True,
                ),
            )
        try:
            self.store.update_agent_harness_action(
                accepted_action.model_copy(update={"status": "applied", "completed_at": self.now()}),
                expected_status="accepted",
            )
        except Exception:
            completed = step.model_copy(update={
                "kind": envelope.kind,
                "action_id": accepted_action.action_id,
                "action_hash": accepted_action.action_hash,
                "completed_at": self.now(),
                "error_code": "AGENT_HARNESS_ACTION_RECORD_APPLY_FAILED",
                "validation_result": "accepted",
                "summary": "Action side effect completed but its terminal audit update requires reconciliation.",
            })
            self.store.update_agent_harness_step(completed)
            return HarnessRunResult(
                lifecycle=applied.lifecycle,
                attempt=self._stop_claimed(
                    claimed, "AGENT_HARNESS_ACTION_RECORD_APPLY_FAILED", model_calls=completed.model_calls,
                    proposal_increment=1, consume_step=True,
                ),
            )
        try:
            completed = step.model_copy(update={
                "kind": envelope.kind,
                "action_id": accepted_action.action_id,
                "action_hash": accepted_action.action_hash,
                "validation_result": "accepted",
                "state_after": applied.lifecycle.state, "summary": self._summary(envelope.reason),
                "observation_ref": lifecycle.observation_id,
                "evaluation_ref": lifecycle.goal_evaluation_id,
                "action_result_code": applied.action_result_code,
                "action_result_hash": stable_hash({
                    "lifecycle_id": applied.lifecycle.lifecycle_id,
                    "state_after": applied.lifecycle.state,
                    "attempt_status": applied.attempt_status,
                    "terminal_reason": applied.terminal_reason,
                    "action_result_code": applied.action_result_code,
                }),
                "completed_at": self.now(),
            })
            self.store.update_agent_harness_step(completed)
            stop_reason = self._post_completion_budget_reason(
                claimed,
                model_calls=completed.model_calls,
                proposal_increment=1,
            )
            finished = self._complete_claim(
                claimed,
                status="STOPPED" if stop_reason and applied.attempt_status == "READY" else applied.attempt_status,
                terminal_reason=stop_reason or applied.terminal_reason,
                model_calls=completed.model_calls,
                proposal_increment=1,
            )
            return HarnessRunResult(lifecycle=applied.lifecycle, attempt=finished)
        except Exception:
            completed = step.model_copy(update={"completed_at": self.now(), "error_code": "AGENT_HARNESS_STEP_FAILED", "summary": "Harness step stopped safely."})
            self.store.update_agent_harness_step(completed)
            return HarnessRunResult(
                lifecycle=applied.lifecycle,
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
        request = build_canonical_model_request(
            snapshot=context.prompt_payload(), config=self.model_config, repair=repair,
        )
        serialized_request = canonical_request_bytes(request)
        pending_call = ModelCallRecord(
            call_id=f"harness_call_{uuid4().hex}", step_id=step.step_id,
            attempt_id=attempt.attempt_id, provider=request.provider,
            phase=phase, model=request.model, endpoint_class=request.endpoint_class,
            prompt_template_version=request.prompt_template_version, context_hash=context.context_hash,
            skill_hashes=tuple(reference.content_hash for reference in context.skill_refs),
            skill_error_codes=tuple(sorted(set(context.skill_error_codes))),
            request_hash=stable_hash(serialized_request),
            action_schema_hash=stable_hash(request.action_schema),
            model_parameters_hash=stable_hash(request.model_parameters),
            model_profile_hash=request.model_profile_hash,
            request_bytes=len(serialized_request),
            request_builder_version=REQUEST_BUILDER_VERSION,
            response_schema_version=2,
            repair=repair, started_at=started,
        )
        started_step = step.model_copy(update={"model_calls": (*step.model_calls, pending_call)})
        self.store.update_agent_harness_step(started_step)
        logger.info(
            "agent_model_call_started",
            extra={"medimage": agent_log_context(
                project_id=context.project_id,
                lifecycle_id=context.lifecycle_id,
                event_code="AGENT_MODEL_CALL_STARTED",
            )},
        )
        try:
            proposal = self.adapter.propose_action(request=request)
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
            logger.warning(
                "agent_model_call_failed",
                extra={"medimage": agent_log_context(
                    project_id=context.project_id,
                    lifecycle_id=context.lifecycle_id,
                    event_code="AGENT_MODEL_CALL_FAILED",
                )},
            )
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
            logger.warning(
                "agent_model_call_invalid",
                extra={"medimage": agent_log_context(
                    project_id=context.project_id,
                    lifecycle_id=context.lifecycle_id,
                    event_code="AGENT_MODEL_CALL_INVALID",
                )},
            )
            raise _ModelCallFailure(
                code="AGENT_HARNESS_MODEL_OUTPUT_INVALID", step=completed_step,
            ) from exc
        completed = self._complete_model_call(
            pending_call, metadata=proposal.metadata, schema_valid=True, status="succeeded",
        )
        completed_step = started_step.model_copy(update={"model_calls": (*step.model_calls, completed)})
        self.store.update_agent_harness_step(completed_step)
        logger.info(
            "agent_model_call_completed",
            extra={"medimage": agent_log_context(
                project_id=context.project_id,
                lifecycle_id=context.lifecycle_id,
                event_code="AGENT_MODEL_CALL_COMPLETED",
            )},
        )
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
        capability = assert_capability_allowed(envelope.kind, lifecycle.state)
        roots = set(context.included_sections)
        requested_sections = {ref.split(".", 1)[0] for ref in envelope.input_refs}
        if not requested_sections.issubset(roots):
            raise ValueError("AGENT_HARNESS_REFERENCE_DENIED")
        output_type = {
            "request_decision": "decision_request",
            "draft_plan": "reviewed_plan_request",
        }.get(envelope.kind)
        if output_type is None:
            raise ValueError("AGENT_HARNESS_CAPABILITY_DENIED")
        assert_capability_context_and_output_allowed(
            capability,
            context_sections=requested_sections,
            output_type=output_type,
        )
        # ``model_construct`` can bypass Pydantic, so re-validate the typed
        # union immediately before any managed-state mutation.
        from src.backend.app.schemas.agent_harness import parse_action_envelope

        parse_action_envelope(envelope)

    def _apply(self, envelope: ActionEnvelope, lifecycle, actor: str) -> HarnessActionResult:
        return self.planning_action_service.apply(
            lifecycle_id=lifecycle.lifecycle_id,
            action=envelope,
            actor=actor,
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
    ) -> str | None:
        next_steps = attempt.steps_used + 1
        next_calls = attempt.model_calls_used + self._network_call_count(model_calls)
        next_actions = attempt.action_proposals_used + proposal_increment
        input_tokens = self._accumulate_optional(attempt.input_tokens_used, model_calls, "input_tokens")
        output_tokens = self._accumulate_optional(attempt.output_tokens_used, model_calls, "output_tokens")
        if next_steps >= self.config.max_steps:
            return "AGENT_HARNESS_STEP_BUDGET_EXHAUSTED"
        if next_calls >= self.config.max_model_calls:
            return "AGENT_HARNESS_MODEL_CALL_BUDGET_EXHAUSTED"
        if next_actions >= self.config.max_action_proposals:
            return "AGENT_HARNESS_ACTION_PROPOSAL_BUDGET_EXHAUSTED"
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
        """Recover one persisted step without another provider invocation."""
        action = self._action_for_step(claimed.attempt_id, step.step_id)
        if action is not None and action.status == "accepted":
            return self._recover_accepted_action(
                claimed=claimed, lifecycle=lifecycle, step=step, action=action,
            )
        if self._is_pre_network_call(step):
            skipped = step.model_copy(update={
                "completed_at": self.now(),
                "error_code": "AGENT_HARNESS_CALL_NOT_SENT",
                "summary": "Recovered a provider call before the network boundary.",
            })
            self.store.update_agent_harness_step(skipped)
            recovered = self._complete_claim(
                claimed,
                status="READY",
                terminal_reason=None,
                model_calls=skipped.model_calls,
            )
            return HarnessRunResult(lifecycle=lifecycle, attempt=recovered)
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
        recovered = self._complete_claim(
            claimed,
            status=status,
            terminal_reason=terminal_reason,
            model_calls=step.model_calls,
            proposal_increment=1,
        )
        return HarnessRunResult(lifecycle=lifecycle, attempt=recovered)

    def _recover_accepted_action(
        self,
        *,
        claimed: AgentHarnessAttempt,
        lifecycle,
        step: AgentHarnessStep,
        action: AgentActionRecord,
    ) -> HarnessRunResult:
        """Finish or replay a durable local action, never its model request."""
        try:
            envelope = parse_action_envelope(action.action_payload)
            if envelope.kind != action.kind or envelope.expected_state != action.expected_state:
                raise ValueError("AGENT_HARNESS_ACTION_PAYLOAD_INVALID")
            if lifecycle.state == action.expected_state:
                applied = self._apply(envelope, lifecycle, "system-agent-task-recovery")
                lifecycle = applied.lifecycle
                action_result_code = applied.action_result_code
            else:
                # The action service only leaves expected_state after its side
                # effect is durable. Replaying here could duplicate a plan.
                action_result_code = "AGENT_HARNESS_ACTION_RECONCILED"
            self.store.update_agent_harness_action(
                action.model_copy(update={"status": "applied", "completed_at": self.now()}),
                expected_status="accepted",
            )
        except Exception as exc:
            code = str(
                getattr(exc, "code", None)
                or str(exc).split(":", 1)[0]
                or "AGENT_HARNESS_ACTION_REPLAY_FAILED"
            )
            try:
                self.store.update_agent_harness_action(
                    action.model_copy(update={
                        "status": "rejected", "error_code": code[:128], "completed_at": self.now(),
                    }),
                    expected_status="accepted",
                )
            except Exception:
                pass
            return HarnessRunResult(
                lifecycle=lifecycle,
                attempt=self._stop_claimed(
                    claimed, code, model_calls=step.model_calls,
                    proposal_increment=1, consume_step=True,
                ),
            )
        completed = step.model_copy(update={
            "kind": action.kind,
            "action_id": action.action_id,
            "action_hash": action.action_hash,
            "validation_result": "accepted",
            "state_after": lifecycle.state,
            "action_result_code": action_result_code,
            "action_result_hash": stable_hash({
                "lifecycle_id": lifecycle.lifecycle_id,
                "state_after": lifecycle.state,
                "action_result_code": action_result_code,
            }),
            "completed_at": self.now(),
            "summary": "Recovered a durable Harness action without a model replay.",
        })
        self.store.update_agent_harness_step(completed)
        status = (
            "WAITING_FOR_USER"
            if lifecycle.state in {"WAITING_FOR_INPUT", "WAITING_FOR_SCIENCE_DECISION"}
            else "READY"
        )
        recovered = self._complete_claim(
            claimed,
            status=status,
            terminal_reason=None,
            model_calls=completed.model_calls,
            proposal_increment=1,
        )
        return HarnessRunResult(lifecycle=lifecycle, attempt=recovered)

    def _action_for_step(self, attempt_id: str, step_id: str) -> AgentActionRecord | None:
        getter = getattr(self.store, "list_agent_harness_actions", None)
        if not callable(getter):
            return None
        return next((item for item in getter(attempt_id) if item.step_id == step_id), None)

    @staticmethod
    def _is_pre_network_call(step: AgentHarnessStep) -> bool:
        return bool(step.model_calls) and all(
            call.status == "started" and not call.network_called for call in step.model_calls
        )

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
