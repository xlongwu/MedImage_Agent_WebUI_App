"""One-step, lease-based control plane for the optional single Agent Harness."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from src.backend.app.core.config_schema import AgentHarnessConfig
from src.backend.app.planner.agent_model_adapter import AgentModelAdapter, DefaultAgentModelAdapter
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.runtime.agent_capability_catalog import assert_capability_allowed
from src.backend.app.schemas.agent_harness import (
    ActionEnvelope,
    AgentHarnessAttempt,
    AgentHarnessContext,
    AgentHarnessStep,
)
from src.backend.app.schemas.agent_lifecycle import PendingDecision, PendingDecisionOption
from src.backend.app.services.agent_harness_context_service import HarnessContextBuilder
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


class AgentHarnessService:
    """Run at most one advice-only step; it has no execution dependencies."""

    MAX_LEASE_TAKEOVERS = 2

    def __init__(
        self,
        store,
        *,
        config: AgentHarnessConfig,
        adapter: AgentModelAdapter | None = None,
        context_builder: HarnessContextBuilder | None = None,
        draft_plan: Callable[..., object] | None = None,
        recovery_proposer: Callable[..., object] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.store = store
        self.config = config
        self.adapter = adapter or DefaultAgentModelAdapter()
        self.context_builder = context_builder or HarnessContextBuilder()
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
        if lifecycle.project_id != attempt.project_id or lifecycle.state in {"CANCELED", "SUCCEEDED", "GOAL_SATISFIED", "HUMAN_HANDOFF"}:
            return HarnessRunResult(lifecycle=lifecycle, attempt=self.stop(lifecycle_id=lifecycle.lifecycle_id, reason="LIFECYCLE_TERMINAL"))
        if attempt.status in {"FINISHED", "STOPPED", "FAILED", "WAITING_FOR_USER"}:
            return HarnessRunResult(lifecycle=lifecycle, attempt=attempt)
        claimed = self._claim(attempt, lease_owner or f"harness-{uuid4().hex}")
        if claimed is None:
            return HarnessRunResult(lifecycle=lifecycle, attempt=self.store.get_agent_harness_attempt(lifecycle.lifecycle_id))
        if self._budget_exhausted(claimed):
            return HarnessRunResult(lifecycle=lifecycle, attempt=self._stop_claimed(claimed, "AGENT_HARNESS_BUDGET_EXHAUSTED"))
        project = self.store.get_project(lifecycle.project_id)
        context = self.context_builder.build(lifecycle=lifecycle, project=project)
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
            started_at=self.now(), summary="Model action requested.",
        )
        self.store.add_agent_harness_step(step)
        try:
            envelope, model_call_count = self._propose_with_one_repair(context, claimed)
            self._validate_envelope(envelope, lifecycle, context)
        except RuntimeError as exc:
            code = str(exc).split(":", 1)[0]
            completed = step.model_copy(update={
                "completed_at": self.now(),
                "error_code": code,
                "summary": "Harness provider failed; the enabled Harness stopped without creating a plan.",
            })
            self.store.update_agent_harness_step(completed)
            failed = self._stop_claimed(claimed, code)
            if code == "AGENT_HARNESS_PROVIDER_UNAVAILABLE":
                return self._fallback_to_deterministic_planner(
                    lifecycle=lifecycle,
                    attempt=failed,
                    actor=actor,
                    reason=code,
                )
            return HarnessRunResult(lifecycle=lifecycle, attempt=failed)
        except Exception as exc:
            code = str(exc).split(":", 1)[0] or "AGENT_MODEL_OUTPUT_INVALID"
            completed = step.model_copy(update={"completed_at": self.now(), "error_code": code, "summary": "Harness action was rejected safely."})
            self.store.update_agent_harness_step(completed)
            return HarnessRunResult(lifecycle=lifecycle, attempt=self._stop_claimed(claimed, code))

        try:
            updated_lifecycle, next_status, terminal_reason = self._apply(envelope, lifecycle, actor)
            completed = step.model_copy(update={
                "kind": envelope.kind, "output_hash": stable_hash(envelope.model_dump(mode="json")),
                "requested_capability": envelope.kind, "validation_result": "accepted",
                "model_call_count": model_call_count,
                "state_after": updated_lifecycle.state, "summary": self._summary(envelope.reason),
                "completed_at": self.now(),
            })
            self.store.update_agent_harness_step(completed)
            finished = self._complete_claim(
                claimed, status=next_status, terminal_reason=terminal_reason,
                model_call_increment=model_call_count, proposal_increment=1,
            )
            return HarnessRunResult(lifecycle=updated_lifecycle, attempt=finished)
        except Exception:
            completed = step.model_copy(update={"completed_at": self.now(), "error_code": "AGENT_HARNESS_STEP_FAILED", "summary": "Harness step stopped safely."})
            self.store.update_agent_harness_step(completed)
            return HarnessRunResult(lifecycle=lifecycle, attempt=self._stop_claimed(claimed, "AGENT_HARNESS_STEP_FAILED"))

    def run_until_blocked(
        self,
        *,
        lifecycle,
        actor: str,
        wake_reason: str,
        lease_owner: str,
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
            attempt = self._mark_wake(attempt, wake_reason)

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
            return "handoff", lifecycle.state
        if lifecycle.state == "CANCELED":
            return "canceled", lifecycle.state
        if lifecycle.state in {"GOAL_SATISFIED", "SUCCEEDED"}:
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
        self, context: AgentHarnessContext, attempt: AgentHarnessAttempt
    ) -> tuple[ActionEnvelope, int]:
        try:
            return (
                self.adapter.propose_action(
                    snapshot=context.allowed_fields_json, provider_ref=attempt.provider_ref, repair=False
                ),
                1,
            )
        except (ValueError, TypeError) as exc:
            if attempt.model_calls_used + 2 > self.config.max_model_calls:
                raise RuntimeError("AGENT_MODEL_OUTPUT_INVALID") from exc
            try:
                return (
                    self.adapter.propose_action(
                        snapshot=context.allowed_fields_json, provider_ref=attempt.provider_ref, repair=True
                    ),
                    2,
                )
            except (ValueError, TypeError) as exc:
                raise RuntimeError("AGENT_MODEL_OUTPUT_INVALID") from exc

    def _validate_envelope(self, envelope: ActionEnvelope, lifecycle, context: AgentHarnessContext) -> None:
        if envelope.expected_state != lifecycle.state:
            raise ValueError("AGENT_HARNESS_STALE_ACTION")
        assert_capability_allowed(envelope.kind, lifecycle.state)
        roots = set(context.allowed_fields_json)
        if any(ref.split(".", 1)[0] not in roots for ref in envelope.input_refs):
            raise ValueError("AGENT_HARNESS_REFERENCE_DENIED")
        if envelope.kind == "request_decision":
            self._validate_decision_payload(envelope.payload)

    def _apply(self, envelope: ActionEnvelope, lifecycle, actor: str) -> tuple[object, str, str | None]:
        if envelope.kind == "draft_plan":
            if self.draft_plan is None:
                raise RuntimeError("AGENT_HARNESS_DRAFT_PLAN_UNAVAILABLE")
            result = self.draft_plan(lifecycle=lifecycle, command_id=f"harness:{lifecycle.lifecycle_id}", actor=actor)
            status = "WAITING_FOR_USER" if result.state in {"WAITING_FOR_INPUT", "WAITING_FOR_SCIENCE_DECISION"} else "READY"
            if result.state in {"GOAL_SATISFIED", "SUCCEEDED", "HUMAN_HANDOFF", "CANCELED"}:
                status = "FINISHED"
            return result, status, None
        if envelope.kind == "request_decision":
            decision = self._decision_from_payload(envelope.payload)
            state = "WAITING_FOR_SCIENCE_DECISION" if decision.kind not in {"missing_input", "goal_revision"} else "WAITING_FOR_INPUT"
            if lifecycle.state == "CREATED" and state == "WAITING_FOR_SCIENCE_DECISION":
                lifecycle = self.orchestrator.transition(
                    project_id=lifecycle.project_id, lifecycle_id=lifecycle.lifecycle_id,
                    to_state="CONTEXT_READY", command_id=f"harness:{lifecycle.lifecycle_id}:context",
                    actor=actor, source_command="harness_context_ready",
                )
            result = self.orchestrator.transition(
                project_id=lifecycle.project_id, lifecycle_id=lifecycle.lifecycle_id, to_state=state,
                command_id=f"harness:{lifecycle.lifecycle_id}:decision:{decision.kind}", actor=actor,
                source_command="harness_decision_required", updates={"pending_decision": decision}, reason=decision.impact,
            )
            return result, "WAITING_FOR_USER", None
        if envelope.kind == "propose_recovery":
            if self.recovery_proposer is None:
                raise RuntimeError("AGENT_HARNESS_RECOVERY_UNAVAILABLE")
            result = self.recovery_proposer(lifecycle=lifecycle, actor=actor)
            return result, "READY", None
        # Evidence/explanation are non-mutating, and finish only terminates the attempt.
        return lifecycle, "FINISHED" if envelope.kind in {"finish", "explain_result"} else "READY", (
            "MODEL_FINISHED" if envelope.kind == "finish" else None
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

    def _budget_exhausted(self, attempt: AgentHarnessAttempt) -> bool:
        return (
            attempt.model_calls_used >= self.config.max_model_calls
            or attempt.tool_proposals_used >= self.config.max_tool_proposals
            or self.now() >= attempt.deadline_at
        )

    def _complete_claim(
        self,
        attempt: AgentHarnessAttempt,
        *,
        status: str,
        terminal_reason: str | None,
        model_call_increment: int,
        proposal_increment: int,
    ) -> AgentHarnessAttempt:
        updated = self._with_attempt(
            attempt, status=status, terminal_reason=terminal_reason,
            next_step_no=attempt.next_step_no + 1,
            model_calls_used=attempt.model_calls_used + model_call_increment,
            tool_proposals_used=attempt.tool_proposals_used + proposal_increment,
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

    def _stop_claimed(self, attempt: AgentHarnessAttempt, reason: str) -> AgentHarnessAttempt:
        return self._complete_claim(
            attempt,
            status="STOPPED",
            terminal_reason=reason,
            model_call_increment=0,
            proposal_increment=0,
        )

    def _transition_attempt(self, attempt: AgentHarnessAttempt, *, status: str, terminal_reason: str | None, clear_lease: bool = False) -> AgentHarnessAttempt:
        updated = self._with_attempt(
            attempt, status=status, terminal_reason=terminal_reason,
            lease_owner=None if clear_lease else attempt.lease_owner,
            lease_expires_at=None if clear_lease else attempt.lease_expires_at,
        )
        return self.store.update_agent_harness_attempt(updated, expected_status=attempt.status)

    def _mark_wake(self, attempt: AgentHarnessAttempt, wake_reason: str) -> AgentHarnessAttempt:
        updated = self._with_attempt(attempt, last_wake_reason=wake_reason[:128])
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
        if (
            step.validation_result != "accepted"
            or step.completed_at is None
            or step.step_no != claimed.next_step_no
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
            model_call_increment=step.model_call_count,
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

    @staticmethod
    def _decision_from_payload(payload: dict) -> PendingDecision:
        options = tuple(
            PendingDecisionOption(
                id=str(item["id"])[:128], label=str(item["label"])[:256],
                description=str(item.get("description") or "")[:512], recommended=bool(item.get("recommended")),
            )
            for item in payload.get("options", [])[:8]
            if isinstance(item, dict) and {"id", "label"} <= set(item)
        )
        return PendingDecision(
            decision_id=f"harness_decision_{uuid4().hex}", kind=str(payload["kind"]),
            question=str(payload["question"]), impact=str(payload["impact"]), options=options,
            recommended_option=(str(payload["recommended_option"]) if payload.get("recommended_option") else None),
        )
