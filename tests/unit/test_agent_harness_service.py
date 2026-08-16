from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.backend.app.core.config_schema import AgentHarnessConfig
from src.backend.app.planner.agent_model_adapter import (
    ActionCallMetadata,
    ActionProposal,
    build_canonical_model_request,
    canonical_request_bytes,
)
from src.backend.app.core.config_schema import AgentModelRuntimeConfig
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.schemas.agent_harness import (
    AgentActionRecord,
    AgentHarnessStep,
    DraftPlanAction,
    RequestDecisionAction,
    action_envelope_json_schema,
    parse_action_envelope,
)
from src.backend.app.schemas.agent_lifecycle import DecisionItem, PendingDecisionOption
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.agent_harness_service import AgentHarnessService
from src.backend.app.services.agent_evidence_service import AgentEvidenceService
from src.backend.app.services.agent_orchestrator import AgentOrchestrator
from src.backend.app.services.agent_planning_action_service import HarnessActionResult
from src.backend.app.services.mock_store import SQLiteDesktopStore


def _store(tmp_path) -> SQLiteDesktopStore:
    store = SQLiteDesktopStore(tmp_path / "harness.sqlite")
    store.add_project(ProjectDetail(
        id="project-1", name="project", study_id="study", modality="rs-fMRI",
        created_date="today", subjects_count=0, current_pipeline_id="pipeline",
        sequences=[], scans_count=0, total_size="0", current_model_id="none",
    ), health_status="ready", rawdata_dir="")
    return store


def _lifecycle(store):
    lifecycle = AgentOrchestrator(store).create(
        project_id="project-1", command_id="create", actor="user", goal_text="Create a plan"
    )
    evidence = AgentEvidenceService(store).build_snapshot(
        project_id=lifecycle.project_id, lifecycle_id=lifecycle.lifecycle_id,
    )
    return lifecycle.model_copy(update={
        "command_context": {"evidence_snapshot_hash": evidence.snapshot_hash},
        "evidence_snapshot_hash": evidence.snapshot_hash,
    })


def _decision() -> RequestDecisionAction:
    return RequestDecisionAction(
        kind="request_decision", reason="Choose an atlas", expected_state="CREATED",
        input_refs=("goal", "project_evidence"),
        decision=DecisionItem(
            item_id="atlas", kind="atlas", question="Which atlas?", impact="Changes regions.",
            options=(PendingDecisionOption(id="aal", label="AAL", description="AAL atlas"),),
            recommended_option="aal",
        ),
    )


class Adapter:
    def __init__(self, action, *, network_called: bool = False) -> None:
        self.action = action
        self.network_called = network_called
        self.calls = 0
        self.requests = []

    def propose_action(self, *, request):
        self.calls += 1
        self.requests.append(request)
        return ActionProposal(
            envelope=self.action,
            metadata=ActionCallMetadata(
                provider=request.provider, model=request.model, endpoint_class=request.endpoint_class,
                response_hash="response-hash", input_tokens=5, output_tokens=2,
                cached_input_tokens=None, latency_ms=1, provider_request_id="request-id",
                network_called=self.network_called,
            ),
        )


class NoopPlanningActions:
    def __init__(self) -> None:
        self.calls = 0

    def apply(self, *, lifecycle_id: str, action, actor: str):
        self.calls += 1
        return HarnessActionResult(
            lifecycle=type("Lifecycle", (), {"lifecycle_id": lifecycle_id, "state": action.expected_state})(),
            attempt_status="READY", terminal_reason=None,
        )


def _service(store, adapter, actions=None) -> AgentHarnessService:
    kwargs = {"config": AgentHarnessConfig(enabled=True), "adapter": adapter}
    if actions is not None:
        kwargs["planning_action_service"] = actions
    return AgentHarnessService(store, **kwargs)


def test_two_typed_actions_parse_and_removed_actions_or_extra_fields_are_rejected() -> None:
    assert isinstance(parse_action_envelope(_decision()), RequestDecisionAction)
    assert isinstance(parse_action_envelope(DraftPlanAction(
        kind="draft_plan", reason="Plan", expected_state="CREATED"
    )), DraftPlanAction)
    for removed in ("read_evidence", "explain_result", "propose_recovery", "finish"):
        with pytest.raises(ValueError):
            parse_action_envelope({"kind": removed, "reason": "No", "expected_state": "CREATED"})
    with pytest.raises(ValueError):
        parse_action_envelope({
            "kind": "draft_plan", "reason": "Plan", "expected_state": "CREATED", "payload": {},
        })


def test_decision_item_keeps_the_formal_schema_constraints() -> None:
    with pytest.raises(ValueError):
        RequestDecisionAction(
            kind="request_decision", reason="Choose", expected_state="CREATED",
            decision={"item_id": "atlas", "kind": "atlas", "question": "?", "impact": "x", "unknown": True},
        )
    assert _decision().decision.recommended_option == "aal"


def test_canonical_request_hash_covers_every_actual_request_field() -> None:
    base = build_canonical_model_request(
        snapshot={
            "schema_version": 3, "purpose": "plan_draft", "complete": True,
            "required_sections": ["goal", "policy"], "included_sections": ["goal", "policy"],
            "omitted_sections": [], "evidence_refs": [], "evidence_snapshot_hash": "evidence",
            "projection_policy_version": "projection",
            "policy_version": "policy", "redaction_policy_version": "redaction",
            "prompt_template_version": "prompt-v1", "skill_refs": [], "skill_error_codes": [],
            "sections": {name: {"schema_version": 1, "source_hash": name, "source_refs": [], "data": {"value": name}}
                         for name in ("goal", "policy")},
        }, config=AgentModelRuntimeConfig(), repair=False,
    )
    assert stable_hash(canonical_request_bytes(base)) == stable_hash(canonical_request_bytes(base))
    for changed in (
        base.model_copy(update={"system_prompt": "changed"}),
        base.model_copy(update={"context_payload": {**base.context_payload, "extra": "changed"}}),
        base.model_copy(update={"action_schema": {**base.action_schema, "title": "changed"}}),
        base.model_copy(update={"model_parameters": {**base.model_parameters, "temperature": 1}}),
        base.model_copy(update={"repair": True}),
    ):
        assert stable_hash(canonical_request_bytes(changed)) != stable_hash(canonical_request_bytes(base))
    assert action_envelope_json_schema()["oneOf"]


def test_started_record_write_failure_prevents_provider_call(tmp_path, monkeypatch) -> None:
    store = _store(tmp_path)
    lifecycle = _lifecycle(store)
    adapter = Adapter(DraftPlanAction(kind="draft_plan", reason="Plan", expected_state="CREATED"))
    service = _service(store, adapter)
    service.ensure_attempt(lifecycle=lifecycle, provider_ref="rule_based")
    monkeypatch.setattr(store, "update_agent_harness_step", lambda _step: (_ for _ in ()).throw(RuntimeError("write failed")))

    with pytest.raises(RuntimeError, match="write failed"):
        service.run_one(lifecycle=lifecycle, actor="user")
    assert adapter.calls == 0


def test_missing_required_evidence_stops_before_provider_call(tmp_path) -> None:
    store = _store(tmp_path)
    lifecycle = AgentOrchestrator(store).create(
        project_id="project-1", command_id="missing-evidence", actor="user", goal_text="Create a plan",
    ).model_copy(update={"command_context": {"evidence_snapshot_hash": "missing"}})
    adapter = Adapter(DraftPlanAction(kind="draft_plan", reason="Plan", expected_state="CREATED"))
    service = _service(store, adapter)
    service.ensure_attempt(lifecycle=lifecycle, provider_ref="rule_based")

    result = service.run_one(lifecycle=lifecycle, actor="user")

    assert result.attempt.status == "STOPPED"
    assert result.attempt.terminal_reason == "AGENT_CONTEXT_EVIDENCE_MISSING"
    assert adapter.calls == 0


@pytest.mark.parametrize("action", [
    DraftPlanAction(kind="draft_plan", reason="stale", expected_state="PLAN_DRAFTED"),
    DraftPlanAction(kind="draft_plan", reason="bad reference", expected_state="CREATED", input_refs=("unregistered",)),
])
def test_invalid_state_or_input_refs_never_call_business_service(tmp_path, action) -> None:
    store = _store(tmp_path)
    lifecycle = _lifecycle(store)
    actions = NoopPlanningActions()
    service = _service(store, Adapter(action), actions)
    service.ensure_attempt(lifecycle=lifecycle, provider_ref="rule_based")

    result = service.run_one(lifecycle=lifecycle, actor="user")

    assert result.attempt.status == "STOPPED"
    assert actions.calls == 0


def test_action_is_accepted_before_apply_and_marked_applied(tmp_path) -> None:
    store = _store(tmp_path)
    lifecycle = _lifecycle(store)
    service = _service(store, Adapter(_decision()))
    attempt = service.ensure_attempt(lifecycle=lifecycle, provider_ref="rule_based")

    result = service.run_one(lifecycle=lifecycle, actor="user")

    actions = store.list_agent_harness_actions(attempt.attempt_id)
    assert result.attempt.status == "WAITING_FOR_USER"
    assert len(actions) == 1 and actions[0].status == "applied"
    assert store.get_agent_lifecycle(lifecycle.lifecycle_id).pending_decision_batch.items[0] == _decision().decision


def test_accepted_action_is_replayed_locally_after_a_crash_without_model_replay(tmp_path) -> None:
    store = _store(tmp_path)
    lifecycle = _lifecycle(store)
    actions = NoopPlanningActions()
    adapter = Adapter(DraftPlanAction(kind="draft_plan", reason="Plan", expected_state="CREATED"))
    service = _service(store, adapter, actions)
    attempt = service.ensure_attempt(lifecycle=lifecycle, provider_ref="rule_based")
    service.run_one(lifecycle=lifecycle, actor="user")

    first_step = store.list_agent_harness_steps(attempt.attempt_id)[0]
    persisted_attempt = store.get_agent_harness_attempt(lifecycle.lifecycle_id)
    assert persisted_attempt is not None
    crashed = persisted_attempt.model_copy(update={
        "status": "RUNNING",
        "lease_owner": "crashed-owner",
        "lease_expires_at": datetime.now(UTC).replace(year=2000),
    })
    store.update_agent_harness_attempt(
        crashed,
        expected_status="READY",
        expected_step_no=persisted_attempt.next_step_no,
        expected_context_hash=persisted_attempt.context_hash,
    )
    action = DraftPlanAction(kind="draft_plan", reason="Recover", expected_state="CREATED")
    step_id = "replay-step"
    recovered_call = first_step.model_calls[0].model_copy(update={
        "call_id": "replay-call",
        "step_id": step_id,
        "status": "succeeded",
        "completed_at": datetime.now(UTC),
    })
    input_hash = stable_hash({"context_hash": crashed.context_hash, "state": lifecycle.state})
    store.add_agent_harness_step(AgentHarnessStep(
        step_id=step_id,
        attempt_id=attempt.attempt_id,
        project_id="project-1",
        step_no=crashed.next_step_no,
        idempotency_key=f"{attempt.attempt_id}:{crashed.next_step_no}:{input_hash}",
        input_hash=input_hash,
        validation_result="error",
        state_before="CREATED",
        model_calls=(recovered_call,),
        summary="Action accepted before a simulated crash.",
        started_at=datetime.now(UTC),
    ))
    store.add_agent_harness_action(AgentActionRecord(
        action_id="replay-action",
        attempt_id=attempt.attempt_id,
        step_id=step_id,
        request_hash=recovered_call.request_hash,
        response_hash=recovered_call.response_hash,
        action_hash=stable_hash(action.model_dump(mode="json")),
        kind=action.kind,
        expected_state=action.expected_state,
        action_payload=action.model_dump(mode="json"),
        status="accepted",
        created_at=datetime.now(UTC),
    ))
    actions.calls = 0
    adapter.calls = 0

    result = service.run_one(lifecycle=lifecycle, actor="user", lease_owner="restarted")

    assert result.attempt.status == "READY"
    assert actions.calls == 1
    assert adapter.calls == 0
    assert store.list_agent_harness_actions(attempt.attempt_id)[-1].status == "applied"


def test_persisted_ledgers_contain_hashes_and_no_prompt_response_or_credentials(tmp_path) -> None:
    store = _store(tmp_path)
    lifecycle = _lifecycle(store)
    adapter = Adapter(DraftPlanAction(kind="draft_plan", reason="Plan", expected_state="CREATED"))
    service = _service(store, adapter)
    attempt = service.ensure_attempt(lifecycle=lifecycle, provider_ref="rule_based")

    service.run_one(lifecycle=lifecycle, actor="user")

    step = store.list_agent_harness_steps(attempt.attempt_id)[0]
    call = step.model_calls[0]
    rendered = str({"step": step.model_dump(mode="json"), "action": store.list_agent_harness_actions(attempt.attempt_id)[0].model_dump(mode="json")})
    assert call.request_hash and call.action_schema_hash and call.model_parameters_hash
    assert call.request_bytes > 0 and call.request_builder_version == "agent-harness-request-v1"
    assert "system_prompt" not in rendered and "context_payload" not in rendered
    assert "response-hash" in rendered and "credential" not in rendered
