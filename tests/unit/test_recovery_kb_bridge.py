"""Error KB ↔ canonical recovery decision bridge and retry backoff wiring."""
from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from src.backend.app.services.recovery_execution_service import _approved_retry_backoff
from src.backend.app.services.run_diagnosis_service import RunDiagnosisService
from src.backend.app.schemas.observation import (
    NodeObservation,
    ObservationBindings,
    ObservationCompleteness,
    ObservationRecord,
    ObservationSourceRef,
)
from src.backend.app.services.observation_collector import calculate_observation_hash

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=UTC)


def _observation_with_error(error: str, *, node_id: str = "functional_connectivity_subject") -> ObservationRecord:
    from tests.unit.test_recovery_diagnosis import _observation as _base_observation

    node = NodeObservation(
        node_id=node_id,
        subject_id="sub-02",
        status="FAILED",
        attempt=0,
        errors=(error,),
        evidence_ids=("node-source",),
    )
    return _base_observation().model_copy(update={"nodes": (node,)})


def _build(observation: ObservationRecord, contract_resolver):
    from tests.unit.test_recovery_diagnosis import _evaluation, _ticket

    ticket = _ticket()
    service = RunDiagnosisService(contract_resolver)
    return service.build(
        observation=observation,
        evaluation=_evaluation(observation),
        ticket=ticket,
        created_at=NOW,
    )


def _contract_resolver(node_id: str):
    from src.backend.app.runtime.node_contract_registry import get_node_contract

    return get_node_contract(node_id)


def test_kb_classified_failure_sets_retryability_instead_of_unknown() -> None:
    observation = _observation_with_error("CUDA_ERROR: CUDA out of memory")
    diagnosis = _build(observation, _contract_resolver)

    fact = next(fact for fact in diagnosis.facts if fact.category == "CUDA_ERROR")
    assert fact.retryability == "retryable"
    assert fact.confidence_source == "error_kb"
    assert diagnosis.root_cause_status == "known"


def test_kb_non_retryable_failure_is_marked_non_retryable() -> None:
    observation = _observation_with_error("PERMISSION_DENIED: matlab: command not found")
    diagnosis = _build(observation, _contract_resolver)

    fact = next(fact for fact in diagnosis.facts if fact.category == "PERMISSION_DENIED")
    assert fact.retryability == "non_retryable"
    assert fact.confidence_source == "error_kb"


def test_contract_rule_still_takes_precedence_over_kb() -> None:
    observation = _observation_with_error("NODE_FAILED: temporary read failure")
    diagnosis = _build(observation, _contract_resolver)

    fact = next(fact for fact in diagnosis.facts if fact.category == "NODE_FAILED")
    assert fact.retryability == "retryable"
    assert fact.confidence_source == "contract_rule"


def test_unclassified_failure_stays_unknown() -> None:
    observation = _observation_with_error("MYSTERY_CODE: totally novel failure")
    diagnosis = _build(observation, _contract_resolver)

    fact = next(fact for fact in diagnosis.facts if fact.category == "MYSTERY_CODE")
    assert fact.retryability == "unknown"
    assert fact.confidence_source == "explicit_state"
    assert diagnosis.root_cause_status == "unknown"


# ── approved retry backoff ──────────────────────────────────────────────────


def _candidate(action: str, node_ids: tuple[str, ...]) -> SimpleNamespace:
    return SimpleNamespace(action=action, target_node_ids=node_ids)


def test_retry_backoff_honors_node_contract_fixed_backoff(monkeypatch) -> None:
    import src.backend.app.services.recovery_execution_service as module

    monkeypatch.setattr(module, "get_node_contract", lambda node_id: SimpleNamespace(
        retry_policy=SimpleNamespace(backoff_policy="fixed", backoff_seconds=3)
    ))

    assert _approved_retry_backoff(_candidate("SAFE_RETRY", ("reho_subject",))) == 3.0
    assert _approved_retry_backoff(
        _candidate("RETRY_FAILED_SUBJECTS", ("reho_subject",))
    ) == 3.0


def test_retry_backoff_ignores_non_retry_actions_and_no_backoff(monkeypatch) -> None:
    import src.backend.app.services.recovery_execution_service as module

    monkeypatch.setattr(module, "get_node_contract", lambda node_id: SimpleNamespace(
        retry_policy=SimpleNamespace(backoff_policy="none", backoff_seconds=0)
    ))

    assert _approved_retry_backoff(_candidate("RESUME", ("reho_subject",))) == 0.0
    assert _approved_retry_backoff(_candidate("REPLAN", ("reho_subject",))) == 0.0
    assert _approved_retry_backoff(_candidate("SAFE_RETRY", ("reho_subject",))) == 0.0


def test_retry_backoff_is_capped_against_contract_mistakes(monkeypatch) -> None:
    import src.backend.app.services.recovery_execution_service as module

    monkeypatch.setattr(module, "get_node_contract", lambda node_id: SimpleNamespace(
        retry_policy=SimpleNamespace(backoff_policy="fixed", backoff_seconds=3600)
    ))

    assert _approved_retry_backoff(_candidate("SAFE_RETRY", ("reho_subject",))) == 30.0
