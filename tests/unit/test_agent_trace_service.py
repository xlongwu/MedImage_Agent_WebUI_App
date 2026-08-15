from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from src.backend.app.core.config_schema import AgentHarnessConfig
from src.backend.app.schemas.agent_harness import AgentActionRecord, AgentHarnessStep, ModelCallRecord
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
        project_id=project.id, command_id="trace-create", actor="user"
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
        request_hash="request-trace", action_schema_hash="action-schema", model_parameters_hash="model-parameters",
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


def test_trace_is_read_only_redacted_and_replays_without_operational_dependencies(tmp_path) -> None:
    store, lifecycle = _trace_store(tmp_path)
    before = store.get_agent_harness_attempt(lifecycle.lifecycle_id)

    bundle = AgentTraceService(store).get(
        project_id=lifecycle.project_id, lifecycle_id=lifecycle.lifecycle_id
    )
    replay = AgentReplayService().replay(bundle)

    assert bundle.integrity_status == "complete"
    assert bundle.entries[0].context_refs[0].ref_type == "context"
    assert "goal" not in bundle.model_dump_json()
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
