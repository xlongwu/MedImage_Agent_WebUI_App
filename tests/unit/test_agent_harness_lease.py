from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.backend.app.core.config_schema import AgentHarnessConfig
from src.backend.app.planner.agent_model_adapter import ActionProposal
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.schemas.agent_harness import DraftPlanAction, AgentHarnessStep, ModelCallRecord
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.agent_harness_context_service import HarnessContextSources
from src.backend.app.services.agent_harness_service import AgentHarnessService
from src.backend.app.services.agent_orchestrator import AgentOrchestrator
from src.backend.app.services.mock_store import SQLiteDesktopStore


class Adapter:
    def __init__(self) -> None:
        self.calls = 0

    def propose_action(self, **_kwargs):
        self.calls += 1
        return ActionProposal.rule_based(DraftPlanAction(
            kind="draft_plan", reason="Plan", expected_state="CREATED"
        ))


def _store(tmp_path):
    store = SQLiteDesktopStore(tmp_path / "lease.sqlite")
    store.add_project(ProjectDetail(id="project-1", name="p", study_id="s", modality="rs-fMRI", created_date="today", subjects_count=0, current_pipeline_id="p", sequences=[], scans_count=0, total_size="0", current_model_id="none"), health_status="ready", rawdata_dir="")
    return store


@pytest.mark.parametrize(
    ("network_called", "expected_status", "expected_reason"),
    [
        (True, "STOPPED", "AGENT_HARNESS_CALL_OUTCOME_UNKNOWN"),
        (False, "READY", None),
    ],
)
def test_expired_claim_distinguishes_unknown_network_outcomes_from_pre_network_restarts(
    tmp_path, network_called, expected_status, expected_reason,
) -> None:
    store = _store(tmp_path)
    lifecycle = AgentOrchestrator(store).create(project_id="project-1", command_id="create", actor="user")
    adapter = Adapter()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    service = AgentHarnessService(store, config=AgentHarnessConfig(enabled=True), adapter=adapter, now=lambda: now)
    attempt = service.ensure_attempt(lifecycle=lifecycle, provider_ref="openai_compatible")
    base_context = service.context_builder.build(sources=HarnessContextSources(
        lifecycle=lifecycle, project=store.get_project("project-1"), attempt=attempt,
    ))
    skills = service.skill_loader.load_for_state(state=lifecycle.state, context=base_context)
    context = service.context_builder.build(sources=HarnessContextSources(
        lifecycle=lifecycle, project=store.get_project("project-1"), attempt=attempt,
        skill_refs=skills.references, skill_error_codes=skills.error_codes,
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
            request_hash="request", action_schema_hash="schema", model_parameters_hash="parameters",
            request_bytes=100, request_builder_version="agent-harness-request-v1", response_schema_version=2,
            started_at=now, network_called=network_called, status="started",
        ),),
    ))

    result = service.run_one(lifecycle=lifecycle, actor="user", lease_owner="restarted")

    assert result.attempt.status == expected_status
    assert result.attempt.terminal_reason == expected_reason
    assert adapter.calls == 0
