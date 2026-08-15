"""Pure validation/reducer for a previously assembled Agent trace."""

from __future__ import annotations

from src.backend.app.runtime.agent_capability_catalog import assert_capability_allowed
from src.backend.app.schemas.agent_trace import (
    AgentReplayResult,
    AgentReplayViolation,
    AgentTraceBundle,
)
from src.backend.app.services.agent_trace_service import calculate_trace_integrity_hash


class AgentReplayService:
    """Replay only recorded data; it has no store, provider, or handler dependency."""

    def replay(self, bundle: AgentTraceBundle) -> AgentReplayResult:
        violations: list[AgentReplayViolation] = []
        if bundle.integrity_hash != calculate_trace_integrity_hash(bundle):
            violations.append(self._violation("TRACE_INTEGRITY_HASH_MISMATCH", None, "Trace integrity hash does not match the canonical bundle."))
        for reference in (*bundle.references, *(ref for entry in bundle.entries for ref in entry.references)):
            if reference.status == "missing":
                violations.append(self._violation("TRACE_REFERENCE_MISSING", None, f"Missing {reference.ref_type} reference {reference.ref_id}."))
            elif reference.status == "conflict":
                violations.append(self._violation("TRACE_REFERENCE_CONFLICT", None, f"Conflicting {reference.ref_type} reference {reference.ref_id}."))
        self._check_steps(bundle, violations)
        final_state = self._reduce_events(bundle, violations)
        self._check_budget(bundle, violations)
        return AgentReplayResult(
            trace_id=bundle.trace_id,
            integrity_valid=not any(item.code.startswith("TRACE_INTEGRITY") or item.code.startswith("TRACE_REFERENCE") for item in violations),
            state_valid=not any(item.code in {"TRACE_ENTRY_ORDER_INVALID", "TRACE_STEP_IDEMPOTENCY_DUPLICATE", "TRACE_STEP_STATE_MISMATCH", "TRACE_CAPABILITY_DENIED", "TRACE_LIFECYCLE_EVENT_CHAIN_INVALID", "TRACE_FINAL_STATE_MISMATCH"} for item in violations),
            budget_valid=not any(item.code == "TRACE_BUDGET_MISMATCH" for item in violations),
            final_state=final_state,
            violations=tuple(violations),
        )

    def _check_steps(self, bundle: AgentTraceBundle, violations: list[AgentReplayViolation]) -> None:
        previous_no = 0
        previous_state: str | None = None
        idempotency_keys: set[str] = set()
        for entry in bundle.entries:
            if entry.step_no <= previous_no:
                violations.append(self._violation("TRACE_ENTRY_ORDER_INVALID", entry.step_id, "Step numbers are not strictly increasing."))
            previous_no = entry.step_no
            if entry.idempotency_key in idempotency_keys:
                violations.append(self._violation("TRACE_STEP_IDEMPOTENCY_DUPLICATE", entry.step_id, "Step idempotency key is duplicated."))
            idempotency_keys.add(entry.idempotency_key)
            if previous_state is not None and entry.state_before != previous_state:
                violations.append(self._violation("TRACE_STEP_STATE_MISMATCH", entry.step_id, "Step state does not continue from the preceding step."))
            if entry.state_after is not None:
                previous_state = entry.state_after
            if entry.action_kind is not None and entry.validation_result == "accepted":
                try:
                    assert_capability_allowed(entry.action_kind, entry.state_before)
                except (ValueError, TypeError):
                    violations.append(self._violation("TRACE_CAPABILITY_DENIED", entry.step_id, "Accepted action is not allowed for the recorded state."))

    def _reduce_events(self, bundle: AgentTraceBundle, violations: list[AgentReplayViolation]) -> str | None:
        current = bundle.initial_state
        for event in bundle.lifecycle_events:
            if event.from_state is not None:
                if current is None:
                    current = event.from_state
                if event.from_state != current:
                    violations.append(self._violation("TRACE_LIFECYCLE_EVENT_CHAIN_INVALID", None, f"Lifecycle event {event.event_id} does not continue from the prior state."))
                current = event.to_state
            else:
                current = event.to_state
        if current is not None and current != bundle.final_state:
            violations.append(self._violation("TRACE_FINAL_STATE_MISMATCH", None, "Reduced lifecycle state differs from the recorded final state."))
        return current

    def _check_budget(self, bundle: AgentTraceBundle, violations: list[AgentReplayViolation]) -> None:
        if bundle.budget is None:
            return
        completed = [entry for entry in bundle.entries if entry.completed_at is not None]
        calls = [call for entry in completed for call in entry.model_calls]
        expected = bundle.budget
        actual = {
            "steps_used": len(completed),
            "model_calls_used": sum(call.network_called for call in calls),
            "action_proposals_used": sum(entry.action_kind is not None for entry in completed),
            "repairs_used": sum(call.repair for call in calls),
        }
        for field, value in actual.items():
            if getattr(expected, field) != value:
                violations.append(self._violation("TRACE_BUDGET_MISMATCH", None, f"Recorded {field} does not match completed trace entries."))
                break

    @staticmethod
    def _violation(code: str, entry_id: str | None, message: str) -> AgentReplayViolation:
        return AgentReplayViolation(code=code, entry_id=entry_id, message=message)
