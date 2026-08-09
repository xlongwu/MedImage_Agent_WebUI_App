from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event

from src.backend.app.core.config_schema import AgentHarnessConfig
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.planner.agent_model_adapter import ActionProposal
from src.backend.app.schemas.agent_harness import ActionEnvelope, AgentHarnessStep, ModelCallRecord
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.agent_harness_service import AgentHarnessService
from src.backend.app.services.agent_harness_context_service import HarnessContextSources
from src.backend.app.services.agent_orchestrator import AgentOrchestrator
from src.backend.app.services.mock_store import SQLiteDesktopStore


class FinishAdapter:
    def __init__(self): self.calls = 0
    def propose_action(self, **_kwargs):
        self.calls += 1
        return ActionProposal.rule_based(
            ActionEnvelope(kind="finish", reason="done", expected_state="CREATED")
        )


def test_expired_lease_is_taken_over_once_and_step_is_idempotent(tmp_path) -> None:
    store = SQLiteDesktopStore(tmp_path / "lease.sqlite")
    store.add_project(ProjectDetail(id="project-1", name="p", study_id="s", modality="rs-fMRI", created_date="today", subjects_count=0, current_pipeline_id="p", sequences=[], scans_count=0, total_size="0", current_model_id="none"), health_status="ready", rawdata_dir="")
    lifecycle = AgentOrchestrator(store).create(project_id="project-1", command_id="create", actor="user")
    adapter = FinishAdapter()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    service = AgentHarnessService(store, config=AgentHarnessConfig(enabled=True), adapter=adapter, now=lambda: now)
    attempt = service.ensure_attempt(lifecycle=lifecycle, provider_ref="rule_based").model_copy(update={"status": "RUNNING", "lease_expires_at": now - timedelta(seconds=1)})
    store.update_agent_harness_attempt(attempt, expected_status="READY")

    first = service.run_one(lifecycle=lifecycle, actor="user", lease_owner="restarted")
    second = service.run_one(lifecycle=lifecycle, actor="user", lease_owner="again")

    assert first.attempt.lease_takeovers == 1
    assert first.attempt.status == "FINISHED"
    assert second.attempt.status == "FINISHED"
    assert adapter.calls == 1


def test_two_owners_accept_only_one_concurrent_step(tmp_path) -> None:
    class BlockingAdapter:
        def __init__(self) -> None:
            self.calls = 0
            self.started = Event()
            self.release = Event()

        def propose_action(self, **_kwargs):
            self.calls += 1
            self.started.set()
            assert self.release.wait(timeout=2)
            return ActionProposal.rule_based(
                ActionEnvelope(kind="finish", reason="done", expected_state="CREATED")
            )

    store = SQLiteDesktopStore(tmp_path / "concurrent.sqlite")
    store.add_project(ProjectDetail(id="project-1", name="p", study_id="s", modality="rs-fMRI", created_date="today", subjects_count=0, current_pipeline_id="p", sequences=[], scans_count=0, total_size="0", current_model_id="none"), health_status="ready", rawdata_dir="")
    lifecycle = AgentOrchestrator(store).create(project_id="project-1", command_id="create", actor="user")
    adapter = BlockingAdapter()
    service = AgentHarnessService(store, config=AgentHarnessConfig(enabled=True), adapter=adapter)
    service.ensure_attempt(lifecycle=lifecycle, provider_ref="rule_based")

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(service.run_one, lifecycle=lifecycle, actor="user", lease_owner="first")
        assert adapter.started.wait(timeout=2)
        second = executor.submit(service.run_one, lifecycle=lifecycle, actor="user", lease_owner="second")
        adapter.release.set()
        first_result = first.result(timeout=2)
        second_result = second.result(timeout=2)

    assert adapter.calls == 1
    assert {first_result.attempt.status, second_result.attempt.status} <= {"RUNNING", "FINISHED"}
    steps = store.list_agent_harness_steps(first_result.attempt.attempt_id)
    assert len(steps) == 1
    assert steps[0].validation_result == "accepted"


def test_expired_claim_recovers_completed_step_without_another_model_call(tmp_path) -> None:
    store = SQLiteDesktopStore(tmp_path / "crash-recovery.sqlite")
    store.add_project(ProjectDetail(id="project-1", name="p", study_id="s", modality="rs-fMRI", created_date="today", subjects_count=0, current_pipeline_id="p", sequences=[], scans_count=0, total_size="0", current_model_id="none"), health_status="ready", rawdata_dir="")
    lifecycle = AgentOrchestrator(store).create(project_id="project-1", command_id="create", actor="user")
    adapter = FinishAdapter()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    service = AgentHarnessService(store, config=AgentHarnessConfig(enabled=True), adapter=adapter, now=lambda: now)
    attempt = service.ensure_attempt(lifecycle=lifecycle, provider_ref="rule_based")
    context = service.context_builder.build(sources=HarnessContextSources(
        lifecycle=lifecycle, project=store.get_project("project-1"), attempt=attempt,
    ))
    store.add_agent_harness_context(context)
    claimed = attempt.model_copy(update={
        "status": "RUNNING",
        "context_hash": context.context_hash,
        "lease_owner": "crashed-owner",
        "lease_expires_at": now - timedelta(seconds=1),
    })
    store.update_agent_harness_attempt(claimed, expected_status="READY")
    input_hash = stable_hash({"context_hash": context.context_hash, "state": lifecycle.state})
    store.add_agent_harness_step(AgentHarnessStep(
        step_id="persisted-step",
        attempt_id=attempt.attempt_id,
        project_id="project-1",
        step_no=1,
        idempotency_key=f"{attempt.attempt_id}:1:{input_hash}",
        kind="read_evidence",
        input_hash=input_hash,
        output_hash="output",
        requested_capability="read_evidence",
        validation_result="accepted",
        model_calls=(ModelCallRecord(
            call_id="persisted-call", step_id="persisted-step", attempt_id=attempt.attempt_id,
            provider="openai_compatible", phase="planning", endpoint_class="chat_completions",
            prompt_template_version="agent-harness-prompt-v2", context_hash=context.context_hash,
            request_hash="request", response_hash="response", schema_valid=True,
            started_at=now, completed_at=now, network_called=True, status="succeeded",
        ),),
        state_before="CREATED",
        state_after="CREATED",
        summary="Persisted before process loss.",
        started_at=now,
        completed_at=now,
    ))

    result = service.run_one(lifecycle=lifecycle, actor="user", lease_owner="restarted")

    assert result.attempt.status == "READY"
    assert result.attempt.next_step_no == 2
    assert result.attempt.model_calls_used == 1
    assert adapter.calls == 0


def test_crashed_started_provider_call_is_reconciled_without_retrying_provider(tmp_path) -> None:
    store = SQLiteDesktopStore(tmp_path / "unknown-call.sqlite")
    store.add_project(ProjectDetail(id="project-1", name="p", study_id="s", modality="rs-fMRI", created_date="today", subjects_count=0, current_pipeline_id="p", sequences=[], scans_count=0, total_size="0", current_model_id="none"), health_status="ready", rawdata_dir="")
    lifecycle = AgentOrchestrator(store).create(project_id="project-1", command_id="create", actor="user")
    adapter = FinishAdapter()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    service = AgentHarnessService(store, config=AgentHarnessConfig(enabled=True), adapter=adapter, now=lambda: now)
    attempt = service.ensure_attempt(lifecycle=lifecycle, provider_ref="openai_compatible")
    context = service.context_builder.build(sources=HarnessContextSources(
        lifecycle=lifecycle, project=store.get_project("project-1"), attempt=attempt,
    ))
    store.add_agent_harness_context(context)
    claimed = attempt.model_copy(update={
        "status": "RUNNING", "context_hash": context.context_hash, "lease_owner": "crashed-owner",
        "lease_expires_at": now - timedelta(seconds=1),
    })
    store.update_agent_harness_attempt(claimed, expected_status="READY")
    input_hash = stable_hash({"context_hash": context.context_hash, "state": lifecycle.state})
    store.add_agent_harness_step(AgentHarnessStep(
        step_id="started-step", attempt_id=attempt.attempt_id, project_id="project-1", step_no=1,
        idempotency_key=f"{attempt.attempt_id}:1:{input_hash}", input_hash=input_hash,
        validation_result="error", state_before="CREATED", summary="Provider request started.", started_at=now,
        model_calls=(ModelCallRecord(
            call_id="started-call", step_id="started-step", attempt_id=attempt.attempt_id,
            provider="openai_compatible", phase="planning", endpoint_class="chat_completions",
            prompt_template_version="agent-harness-prompt-v2", context_hash=context.context_hash,
            request_hash="request", started_at=now, network_called=True, status="started",
        ),),
    ))

    result = service.run_one(lifecycle=lifecycle, actor="user", lease_owner="restarted")

    assert result.attempt.status == "STOPPED"
    assert result.attempt.terminal_reason == "AGENT_HARNESS_CALL_OUTCOME_UNKNOWN"
    assert result.attempt.model_calls_used == 1
    assert adapter.calls == 0
