from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from src.backend.app.core.config_schema import AgentHarnessConfig
from src.backend.app.schemas.agent_harness import (
    AgentActionRecord,
    AgentHarnessStep,
    ModelCallRecord,
)
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.agent_harness_context_service import (
    HarnessContextBuilder,
    HarnessContextSources,
)
from src.backend.app.services.agent_harness_service import AgentHarnessService
from src.backend.app.services.agent_orchestrator import AgentOrchestrator
from src.backend.app.services.agent_replay_service import AgentReplayService
from src.backend.app.services.agent_trace_service import (
    AgentTraceService,
    calculate_trace_integrity_hash,
)
from src.backend.app.services.mock_store import SQLiteDesktopStore


def _project() -> ProjectDetail:
    return ProjectDetail(
        id="project-trace", name="trace", study_id="study-trace", modality="rs-fMRI",
        created_date="today", subjects_count=0, current_pipeline_id="pipeline",
        sequences=[], scans_count=0, total_size="0", current_model_id="none",
    )


def _trace_store(tmp_path) -> tuple[SQLiteDesktopStore, object]:
    store = SQLiteDesktopStore(tmp_path / "trace.sqlite")
    project = _project()
    store.add_project(project, health_status="ready", rawdata_dir="")
    lifecycle = AgentOrchestrator(store).create(
        project_id=project.id, command_id="trace-create", actor="user", goal_text="Trace a plan"
    )
    attempt = AgentHarnessService(
        store, config=AgentHarnessConfig(enabled=True)
    ).ensure_attempt(lifecycle=lifecycle, provider_ref="rule_based")
    context = HarnessContextBuilder().build(
        sources=HarnessContextSources(lifecycle=lifecycle, project=project, attempt=attempt)
    )
    store.add_agent_harness_context(context)
    now = datetime.now(UTC)
    call = ModelCallRecord(
        call_id="call-trace", step_id="step-trace", attempt_id=attempt.attempt_id,
        provider="rule_based", phase="planning", endpoint_class="rule_based",
        prompt_template_version=context.prompt_template_version, context_hash=context.context_hash,
        request_hash="request-trace", action_schema_hash="action-schema", model_parameters_hash="model-parameters", model_profile_hash="a" * 64,
        request_bytes=100, request_builder_version="agent-harness-request-v1", response_schema_version=2,
        response_hash="response-trace", schema_valid=True,
        started_at=now, completed_at=now, status="succeeded",
    )
    step = AgentHarnessStep(
        step_id="step-trace", attempt_id=attempt.attempt_id, project_id=project.id,
        step_no=1, idempotency_key="trace-key", kind="draft_plan",
        input_hash="input-trace", action_hash="action-trace", action_result_hash="result-trace",
        validation_result="accepted",
        model_calls=(call,), state_before="CREATED", state_after="CREATED",
        started_at=now, completed_at=now, summary="safe summary",
    )
    store.add_agent_harness_step(step)
    store.add_agent_harness_action(AgentActionRecord(
        action_id="action-trace", attempt_id=attempt.attempt_id, step_id=step.step_id,
        request_hash="request-trace", response_hash="response-trace", action_hash="action-trace",
        kind="draft_plan", expected_state="CREATED", status="applied", created_at=now, completed_at=now,
    ))
    store.update_agent_harness_attempt(
        attempt.model_copy(update={"steps_used": 1, "action_proposals_used": 1}),
        expected_status="READY",
    )
    return store, lifecycle


def _bind_team_trace(store, lifecycle):
    team_lifecycle = lifecycle.model_copy(
        update={
            "planning_mode": "multi_agent",
            "planning_team_id": "team-trace",
            "planning_team_epoch": 1,
        }
    )
    consent_hash = "c" * 64
    registry_hash = "r" * 64
    projector_hash = "p" * 64
    aggregation_hash = "g" * 64
    profile_hash = "m" * 64
    snapshot_hash = "s" * 64
    base_hash = "b" * 64
    context_hashes = {kind: f"{kind[0]}" * 64 for kind in ("science", "safety", "completeness")}
    team = SimpleNamespace(
        team_id="team-trace",
        team_epoch=1,
        project_id=lifecycle.project_id,
        lifecycle_id=lifecycle.lifecycle_id,
        status="COMPLETED",
        snapshot_hash=snapshot_hash,
        role_registry_hash=registry_hash,
        context_projector_hash=projector_hash,
        aggregation_policy_hash=aggregation_hash,
        model_profile_hash=profile_hash,
        global_policy_hash="h" * 64,
        consent_epoch=2,
        consent_hash=consent_hash,
        advisory_id="advisory-trace",
        budget=SimpleNamespace(max_model_calls=3),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    snapshot = SimpleNamespace(
        team_id=team.team_id,
        team_epoch=team.team_epoch,
        project_id=team.project_id,
        lifecycle_id=team.lifecycle_id,
        snapshot_hash=snapshot_hash,
        base_context_hash=base_hash,
        role_context_hashes=context_hashes,
        role_registry_hash=registry_hash,
        context_projector_hash=projector_hash,
        consent_epoch=team.consent_epoch,
        consent_hash=consent_hash,
    )
    role_contexts = {
        kind: SimpleNamespace(
            project_id=team.project_id,
            lifecycle_id=team.lifecycle_id,
            team_id=team.team_id,
            team_epoch=team.team_epoch,
            base_context_hash=base_hash,
            role_context_hash=context_hashes[kind],
            consent_epoch=team.consent_epoch,
            consent_hash=consent_hash,
        )
        for kind in ("science", "safety", "completeness")
    }
    base = SimpleNamespace(
        project_id=team.project_id,
        lifecycle_id=team.lifecycle_id,
        base_context_hash=base_hash,
    )
    role_ids = {
        "science": "science_reviewer.v1",
        "safety": "safety_reviewer.v1",
        "completeness": "completeness_reviewer.v1",
    }
    work_items = [
        SimpleNamespace(
            team_id=team.team_id,
            team_epoch=team.team_epoch,
            project_id=team.project_id,
            lifecycle_id=team.lifecycle_id,
            reviewer_kind=kind,
            role_id=role_ids[kind],
            status="COMPLETED",
            error_code=None,
        )
        for kind in role_ids
    ]
    calls = [
        SimpleNamespace(
            model_call_id=f"call-{kind}",
            team_id=team.team_id,
            team_epoch=team.team_epoch,
            project_id=team.project_id,
            lifecycle_id=team.lifecycle_id,
            reviewer_kind=kind,
            role_context_hash=context_hashes[kind],
            role_registry_hash=registry_hash,
            model_profile_hash=profile_hash,
            consent_epoch=team.consent_epoch,
            consent_hash=consent_hash,
            work_item_id=f"work-{kind}",
            status="completed",
            network_called=True,
            request_hash=f"{kind[0]}" * 64,
            response_hash=f"{kind[0]}" * 64,
            input_tokens=10,
            output_tokens=2,
            latency_ms=50,
        )
        for kind in role_ids
    ]
    advisory = SimpleNamespace(
        advisory_id=team.advisory_id,
        advisory_hash="a" * 64,
        team_id=team.team_id,
        team_epoch=team.team_epoch,
        project_id=team.project_id,
        lifecycle_id=team.lifecycle_id,
        base_context_hash=base_hash,
        context_snapshot_hash=snapshot_hash,
        context_projector_hash=projector_hash,
        role_registry_hash=registry_hash,
        aggregation_policy_hash=aggregation_hash,
        model_profile_hash=profile_hash,
        consent_epoch=team.consent_epoch,
        consent_hash=consent_hash,
        conflict_codes=(),
    )
    store.get_agent_lifecycle = lambda record_id: (
        team_lifecycle if record_id == team_lifecycle.lifecycle_id else None
    )
    store.get_agent_team = lambda _project_id, _team_id: team
    store.get_agent_team_advisory = lambda _project_id, _team_id: advisory
    store.get_agent_team_context = lambda _project_id, _team_id: (snapshot, role_contexts)
    store.get_agent_planning_base_context = lambda _project_id, _context_hash: base
    store.list_agent_team_work_items = lambda _project_id, _team_id: work_items
    store.list_agent_team_model_calls = lambda _project_id, _team_id: calls
    store.list_agent_team_findings = lambda _project_id, _team_id: []
    store.list_agent_team_events = lambda _project_id, _team_id: []
    return team_lifecycle, snapshot, role_contexts


def test_trace_is_read_only_redacted_and_replays_without_operational_dependencies(tmp_path) -> None:
    store, lifecycle = _trace_store(tmp_path)
    before = store.get_agent_harness_attempt(lifecycle.lifecycle_id)

    bundle = AgentTraceService(store).get(
        project_id=lifecycle.project_id, lifecycle_id=lifecycle.lifecycle_id
    )
    replay = AgentReplayService().replay(bundle)

    assert bundle.integrity_status == "complete"
    assert bundle.entries[0].context_refs[0].ref_type == "context"
    assert "Create a plan" not in bundle.model_dump_json()
    assert bundle.entries[0].context_projection is not None
    assert bundle.entries[0].context_projection.complete is True
    assert replay.integrity_valid is True
    assert replay.state_valid is True
    assert replay.budget_valid is True
    assert replay.violations == ()
    assert bundle.entries[0].action_record.status == "applied"
    assert store.get_agent_harness_attempt(lifecycle.lifecycle_id) == before


def test_trace_marks_missing_context_and_tamper_is_localized(tmp_path) -> None:
    store, lifecycle = _trace_store(tmp_path)
    step = store.list_agent_harness_steps(
        store.get_agent_harness_attempt(lifecycle.lifecycle_id).attempt_id
    )[0]
    missing_call = step.model_calls[0].model_copy(update={"context_hash": "missing-context"})
    store.update_agent_harness_step(step.model_copy(update={"model_calls": (missing_call,)}))

    bundle = AgentTraceService(store).get(
        project_id=lifecycle.project_id, lifecycle_id=lifecycle.lifecycle_id
    )
    replay = AgentReplayService().replay(bundle)
    tampered = bundle.model_copy(update={"final_state": "SUCCEEDED"})

    assert bundle.integrity_status == "incomplete"
    assert "CONTEXT_MISSING" in bundle.integrity_issues
    assert any(item.code == "TRACE_REFERENCE_MISSING" for item in replay.violations)
    assert any(
        item.code == "TRACE_INTEGRITY_HASH_MISMATCH"
        for item in AgentReplayService().replay(tampered).violations
    )


def test_trace_accepts_directly_scoped_goal_evaluation_references(tmp_path) -> None:
    store, lifecycle = _trace_store(tmp_path)
    attempt = store.get_agent_harness_attempt(lifecycle.lifecycle_id)
    step = store.list_agent_harness_steps(attempt.attempt_id)[0]
    store.update_agent_harness_step(step.model_copy(update={"evaluation_ref": "evaluation-trace"}))
    store.get_goal_evaluation = lambda _record_id: SimpleNamespace(
        project_id=lifecycle.project_id,
        lifecycle_id=lifecycle.lifecycle_id,
        goal_evaluation_hash="evaluation-hash",
    )

    bundle = AgentTraceService(store).get(
        project_id=lifecycle.project_id, lifecycle_id=lifecycle.lifecycle_id
    )

    assert bundle.integrity_status == "complete"
    assert any(reference.ref_type == "evaluation" for reference in bundle.entries[0].references)


def test_replay_rejects_an_accepted_action_that_is_not_in_the_capability_catalog(tmp_path) -> None:
    store, lifecycle = _trace_store(tmp_path)
    bundle = AgentTraceService(store).get(
        project_id=lifecycle.project_id, lifecycle_id=lifecycle.lifecycle_id
    )
    entry = bundle.entries[0].model_copy(update={"action_kind": "execute"})
    draft = bundle.model_copy(update={"entries": (entry,), "integrity_hash": "pending"})
    unsafe = draft.model_copy(update={"integrity_hash": calculate_trace_integrity_hash(draft)})

    result = AgentReplayService().replay(unsafe)

    assert result.integrity_valid is True
    assert any(item.code == "TRACE_CAPABILITY_DENIED" for item in result.violations)


def test_team_trace_contains_only_bound_identity_and_replays_binding(tmp_path) -> None:
    store, lifecycle = _trace_store(tmp_path)
    team_lifecycle, _snapshot, _role_contexts = _bind_team_trace(store, lifecycle)

    bundle = AgentTraceService(store).get(
        project_id=team_lifecycle.project_id,
        lifecycle_id=team_lifecycle.lifecycle_id,
    )
    replay = AgentReplayService().replay(bundle)

    assert bundle.planning_mode == "multi_agent"
    assert bundle.team is not None
    assert bundle.team.binding_status == "valid"
    assert bundle.team.model_call_count == 3
    assert bundle.team.advisory_hash == "a" * 64
    assert replay.integrity_valid is True
    assert replay.violations == ()
    assert "system_prompt" not in bundle.model_dump_json()
    assert "reviewer output" not in bundle.model_dump_json()


def test_team_trace_replay_rejects_context_binding_conflicts(tmp_path) -> None:
    store, lifecycle = _trace_store(tmp_path)
    team_lifecycle, snapshot, role_contexts = _bind_team_trace(store, lifecycle)
    mismatched_snapshot = SimpleNamespace(**{**vars(snapshot), "snapshot_hash": "x" * 64})
    store.get_agent_team_context = lambda _project_id, _team_id: (
        mismatched_snapshot,
        role_contexts,
    )

    bundle = AgentTraceService(store).get(
        project_id=team_lifecycle.project_id,
        lifecycle_id=team_lifecycle.lifecycle_id,
    )
    replay = AgentReplayService().replay(bundle)

    assert bundle.team is not None
    assert bundle.team.binding_status == "conflict"
    assert any(item.code == "TRACE_TEAM_BINDING_INVALID" for item in replay.violations)
