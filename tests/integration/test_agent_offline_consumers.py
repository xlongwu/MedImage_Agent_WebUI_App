"""Real offline retrieval and model submission boundaries; no external calls."""
import pytest

from src.backend.app.core.config_schema import AgentHarnessConfig
from src.backend.app.services.agent_replay_service import AgentReplayService
from src.backend.app.services.agent_trace_service import AgentTraceService, calculate_trace_integrity_hash
from src.backend.app.services.mock_store import SQLiteDesktopStore
from tests.unit.test_agent_confirmed_decisions import _confirmed
from tests.unit.test_agent_harness_service import Adapter, _decision, _service
from tests.unit.test_agent_task_commands import AgentTaskCommandService
from tests.unit.test_memory_retrieval import _setup, _remember_scientific


def test_cross_batch_reopen_replays_authoritative_user_answer(tmp_path):
    store, harness, answered = _confirmed(tmp_path)
    reopened = SQLiteDesktopStore(store.db_path)
    adapter = Adapter(_decision().model_copy(update={"expected_state": "PLAN_DRAFTED"}))
    resumed = _service(reopened, adapter)
    resumed.prepare_resume(lifecycle=answered, provider_ref="rule_based")
    result = resumed.run_one(lifecycle=reopened.get_agent_lifecycle(answered.lifecycle_id), actor="user")
    assert reopened.list_agent_harness_steps(result.attempt.attempt_id)[-1].action_result_code == "AGENT_DECISION_ALREADY_CONFIRMED"
    bundle = AgentTraceService(reopened).get(project_id=answered.project_id, lifecycle_id=answered.lifecycle_id)
    assert len(bundle.entries) == 2
    replay = AgentReplayService().replay(bundle)
    assert replay.state_valid and replay.budget_valid and replay.integrity_valid, replay.violations
    # Removing the human transition cannot legitimize the next model step.
    missing = bundle.model_copy(update={"lifecycle_events": tuple(e for e in bundle.lifecycle_events if e.source_command != "answer")})
    missing = missing.model_copy(update={"integrity_hash": calculate_trace_integrity_hash(missing)})
    assert not AgentReplayService().replay(missing).state_valid
    assert adapter.calls == 1
    assert reopened.list_execution_tickets(answered.project_id) == []
    assert reopened.list_run_links(answered.project_id) == []


def _provider_boundary(store, retrieval, project_id):
    # Only a controlled question is submitted: planning/execution is never stubbed as success.
    from src.backend.app.services.agent_orchestrator import AgentOrchestrator
    lifecycle = AgentOrchestrator(store).create(project_id=project_id, command_id="offline-create", actor="user", goal_text="Compute atlas connectivity")
    adapter = Adapter(_decision())
    harness = _service(store, adapter)
    planning = AgentTaskCommandService(store, memory_context_service=retrieval)._planning
    planning.harness_config = AgentHarnessConfig(enabled=True)
    planning.harness_service = harness
    first = planning._harness_or_plan(lifecycle=lifecycle, command_id="offline-first", actor="user")
    return planning, harness, adapter, first


def _memory(request):
    return request.context_payload["safe_context"]["sections"]["memory_context"]["data"]


def test_scientific_retrieval_references_reach_first_provider_and_forget_refresh(tmp_path):
    store, repository, manager, consolidator, retrieval, project_id = _setup(tmp_path)
    _remember_scientific(manager, repository, consolidator, project_id)
    item = repository.list_items(project_id=project_id)[0]
    context = retrieval.build_context(project_id=project_id, goal="Compute atlas connectivity")
    assert {ref.memory_id for ref in context.evidence_refs} == {item.memory_id}
    planning, harness, adapter, first = _provider_boundary(store, retrieval, project_id)
    consumed = _memory(adapter.requests[0])
    assert consumed["context_hash"] == first.command_context["memory_context"]["context_hash"]
    assert consumed["decision_suggestions"][0]["typed_value"]["value"] == "schaefer-200"
    assert consumed["decision_suggestions"][0]["advisory_only"] is True
    assert consumed["evidence_refs"][0]["revision_hash"] == item.revision.content_hash
    assert first.pending_decision_batch is not None
    assert "science_answers" not in first.command_context
    current = planning.answer(project_id=project_id, lifecycle_id=first.lifecycle_id, batch_id=first.pending_decision_batch.batch_id, answers=[{"item_id": "atlas", "value": "aal"}], command_id="offline-answer", actor="user")
    manager.forget(project_id=project_id, memory_id=item.memory_id, command_id="offline-forget", principal="desktop-local-user", expected_item_version=item.item_version, expected_revision_hash=item.revision.content_hash)
    consolidator.consolidate_project(project_id=project_id)
    # Resume the same task, with its cached old context; current retrieval must win.
    adapter.action = _decision().model_copy(update={"expected_state": current.state})
    planning._harness_or_plan(lifecycle=current, command_id="offline-second", actor="user", resume=True)
    refreshed = _memory(adapter.requests[-1])
    assert refreshed["context_hash"] != consumed["context_hash"]
    assert refreshed["decision_suggestions"] == [] and refreshed["evidence_refs"] == []
    tombstone = repository.get_item_by_canonical_key(project_id=project_id, canonical_key=item.canonical_key)
    assert tombstone.status == "forgotten"
    assert "schaefer-200" not in tombstone.model_dump_json()
    assert store.list_execution_tickets(project_id) == [] and store.list_run_links(project_id) == []


def test_disabled_consent_refreshes_cached_context_before_provider(tmp_path):
    store, repository, manager, consolidator, retrieval, project_id = _setup(tmp_path)
    _remember_scientific(manager, repository, consolidator, project_id)
    planning, harness, adapter, first = _provider_boundary(store, retrieval, project_id)
    assert _memory(adapter.requests[0])["decision_suggestions"]
    current = planning.answer(project_id=project_id, lifecycle_id=first.lifecycle_id, batch_id=first.pending_decision_batch.batch_id, answers=[{"item_id": "atlas", "value": "aal"}], command_id="offline-disabled-answer", actor="user")
    store.set_memory_consent(project_id=project_id, command_id="disable-offline", principal="desktop-local-user", generate_enabled=True, use_enabled=False)
    adapter.action = _decision().model_copy(update={"expected_state": current.state})
    resumed = planning._harness_or_plan(lifecycle=current, command_id="offline-disabled", actor="user", resume=True)
    assert adapter.calls == 2, (resumed.state, store.get_agent_harness_attempt(current.lifecycle_id))
    assert _memory(adapter.requests[-1])["status"] == "disabled"
    assert _memory(adapter.requests[-1])["decision_suggestions"] == []


def test_conflicting_memories_remain_advisory_and_project_isolated(tmp_path):
    store, repository, manager, consolidator, retrieval, project_id = _setup(tmp_path)
    _remember_scientific(manager, repository, consolidator, project_id)
    result = manager.remember(project_id=project_id, command_id="offline-conflict", principal="desktop-local-user", kind="project_decision", key="another-atlas", value={"decision_kind": "atlas", "value": "aal"}, summary="Conflicting offline fixture", impact_class="scientific")
    candidate = repository.get_candidate(project_id=project_id, candidate_id=result["candidate_id"])
    manager.review_candidate(project_id=project_id, candidate_id=candidate.candidate_id, command_id="offline-conflict-accept", principal="desktop-local-user", accept=True, expected_candidate_version=candidate.candidate_version, candidate_hash=candidate.candidate_hash)
    consolidator.consolidate_project(project_id=project_id)
    planning, harness, adapter, first = _provider_boundary(store, retrieval, project_id)
    suggestions = _memory(adapter.requests[0])["decision_suggestions"]
    assert {s["typed_value"]["value"] for s in suggestions} == {"aal", "schaefer-200"}
    assert all(s["advisory_only"] for s in suggestions)
    assert "science_answers" not in first.command_context and first.reviewed_plan_id is None
    from src.backend.app.planner.memory_influence_guard import MemoryInfluenceGuard, MemoryInfluenceError
    context = retrieval.build_context(project_id=project_id, goal="Compute atlas connectivity")
    # Either conflicting value must remain protected, regardless of retrieval
    # order. A map keyed by parameter silently dropped the earlier suggestion.
    for value in ("aal", "schaefer-200"):
        plan = {"nodes": [{"id": "native_preproc_full_execute", "backend": "python-cpu", "params": {"atlas": value}}]}
        with pytest.raises(MemoryInfluenceError, match="MEMORY_SCIENTIFIC_CONFIRMATION_REQUIRED"):
            MemoryInfluenceGuard().validate(plan=plan, memory_context=context)
        MemoryInfluenceGuard().validate(plan=plan, memory_context=context, science_answers={"atlas": value})
    from src.backend.app.core.exceptions import SafetyError
    root_hash = planning.evidence_service.build_snapshot(project_id=project_id, lifecycle_id=first.lifecycle_id, memory_context=context).snapshot_hash
    pending = planning._decision_batch({}, first.command_context, {}, root_hash, context, first)
    assert pending is not None and len(pending.items) == 2
    command_context = dict(first.command_context)
    command_context.pop("harness_evidence_purpose", None)
    command_context["pending_plan_hash"] = pending.plan_hash_before
    current = planning.orchestrator.transition(project_id=project_id, lifecycle_id=first.lifecycle_id,
        to_state=first.state, command_id="offline-memory-batch", actor="test",
        source_command="offline_fixture", allow_same_state=True,
        updates={"pending_decision_batch": pending, "command_context": command_context})
    with pytest.raises(SafetyError) as error:
        planning.answer(project_id=project_id, lifecycle_id=current.lifecycle_id,
            batch_id=pending.batch_id, answers=[{"item_id": item.item_id, "value": item.options[0].id} for item in pending.items],
            command_id="offline-conflicting-answer", actor="user")
    assert error.value.code == "AGENT_DECISION_BATCH_INVALID"
    assert "conflicting_decision_values" in str(error.value.details)
    assert store.get_agent_lifecycle(first.lifecycle_id) == current
    other = next(project.id for project in store.list_projects() if project.id != project_id)
    assert retrieval.build_context(project_id=other, goal="atlas connectivity").decision_suggestions == ()
    assert store.list_execution_tickets(project_id) == []


def test_actual_memory_snapshot_binds_saved_plan_and_approval(tmp_path):
    from pathlib import Path
    from src.backend.app.services.approval_summary_service import ApprovalSummaryService
    store, repository, manager, consolidator, retrieval, project_id = _setup(tmp_path)
    _remember_scientific(manager, repository, consolidator, project_id)
    (tmp_path / "project").mkdir()
    store.update_project_metadata(project_id, {
        "project_dir": str(tmp_path / "project"),
        "project_config_path": str(Path("examples/project_config_synthetic_smoke.yaml").resolve()),
    })
    commands = AgentTaskCommandService(store, memory_context_service=retrieval)
    task = commands.create(project_id=project_id, goal="检查 BIDS 数据并准备 atlas 预处理方案，不执行计算，不修改 rawdata。", command_id="memory-plan", actor="user")
    if task.pending_decision_batch is not None:
        answers = [{"item_id": item.item_id, "value": item.options[0].id} for item in task.pending_decision_batch.items]
        task = commands.answer(project_id=project_id, lifecycle_id=task.lifecycle_id, batch_id=task.pending_decision_batch.batch_id, answers=answers, command_id="memory-plan-answer", actor="user")
    assert task.state == "SUCCEEDED", (task.state, task.last_error)
    plan = store.get_reviewed_plan(task.reviewed_plan_id)
    context = retrieval.build_context(project_id=project_id, goal=task.goal_text)
    assert context.evidence_refs and plan.memory_context_hash == context.context_hash
    assert plan.memory_context_refs == [ref.model_dump(mode="json") for ref in context.evidence_refs]
    summary = ApprovalSummaryService().build(project=store.get_project(project_id), reviewed_plan=plan)
    assert summary.memory_context_hash == context.context_hash
    assert {ref["revision_hash"] for ref in summary.memory_refs} == {ref.revision_hash for ref in context.evidence_refs}
    assert SQLiteDesktopStore(store.db_path).get_reviewed_plan(plan.reviewed_plan_id).memory_context_refs == plan.memory_context_refs
    assert store.list_execution_tickets(project_id) == [] and store.list_run_links(project_id) == []


@pytest.mark.parametrize("change", ["project", "hash"])
def test_cached_foreign_or_corrupt_memory_is_rejected_before_provider(tmp_path, change):
    from src.backend.app.core.exceptions import SafetyError
    from src.backend.app.services.agent_orchestrator import AgentOrchestrator
    store, repository, manager, consolidator, retrieval, project_id = _setup(tmp_path)
    context = retrieval.build_context(project_id=project_id, goal="atlas")
    lifecycle = AgentOrchestrator(store).create(project_id=project_id, command_id="invalid-memory-create", actor="user", goal_text="atlas")
    raw = context.model_dump(mode="json")
    raw["project_id" if change == "project" else "context_hash"] = "foreign"
    lifecycle = lifecycle.model_copy(update={"command_context": {"memory_context": raw}})
    planning = AgentTaskCommandService(store)._planning
    harness = _service(store, Adapter(_decision()))
    planning.harness_service = harness
    with pytest.raises(SafetyError, match="AGENT_MEMORY_CONTEXT_BINDING_INVALID"):
        planning._harness_or_plan(lifecycle=lifecycle, command_id="invalid-memory", actor="user")
    assert harness.adapter.calls == 0
