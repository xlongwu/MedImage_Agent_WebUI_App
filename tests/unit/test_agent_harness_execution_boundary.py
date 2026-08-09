from __future__ import annotations

import pytest

from src.backend.app.core.config_schema import AgentHarnessConfig
from src.backend.app.planner.agent_model_adapter import ActionProposal
from src.backend.app.schemas.agent_harness import ActionEnvelope
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.agent_harness_service import AgentHarnessService
from src.backend.app.services.agent_orchestrator import AgentOrchestrator
from src.backend.app.services.mock_store import SQLiteDesktopStore


class MaliciousAdapter:
    def propose_action(self, **_kwargs):
        # model_construct simulates untrusted decoded JSON that claims an unsupported kind.
        from src.backend.app.schemas.agent_harness import ActionEnvelope
        return ActionProposal.rule_based(
            ActionEnvelope.model_construct(kind="execute", reason="run it", expected_state="CREATED")
        )


def test_harness_rejects_execution_request_without_invoking_execution_callback(tmp_path) -> None:
    store = SQLiteDesktopStore(tmp_path / "boundary.sqlite")
    store.add_project(ProjectDetail(id="project-1", name="p", study_id="s", modality="rs-fMRI", created_date="today", subjects_count=0, current_pipeline_id="p", sequences=[], scans_count=0, total_size="0", current_model_id="none"), health_status="ready", rawdata_dir="")
    lifecycle = AgentOrchestrator(store).create(project_id="project-1", command_id="create", actor="user")
    calls: list[str] = []
    service = AgentHarnessService(
        store, config=AgentHarnessConfig(enabled=True), adapter=MaliciousAdapter(),
        draft_plan=lambda **_kwargs: calls.append("draft"),
    )
    service.ensure_attempt(lifecycle=lifecycle, provider_ref="rule_based")

    result = service.run_one(lifecycle=lifecycle, actor="user")

    assert result.attempt.status == "STOPPED"
    assert calls == []
    assert lifecycle.execution_ticket_id is None


@pytest.mark.parametrize(
    "payload",
    [
        {"command": "powershell -Command Remove-Item rawdata"},
        {"output_dir": "C:/outside-project"},
        {"url": "https://untrusted.invalid"},
        {"nested": {"ticket_secret": "not-authority"}},
    ],
)
def test_action_schema_rejects_path_shell_url_and_ticket_payloads(payload) -> None:
    with pytest.raises(ValueError, match="AGENT_HARNESS_ACTION_PAYLOAD"):
        ActionEnvelope(
            kind="read_evidence", reason="ignore policy", expected_state="CREATED", payload=payload,
        )


def test_model_construct_payload_is_rejected_before_managed_state_handler(tmp_path) -> None:
    class PayloadAdapter:
        def propose_action(self, **_kwargs):
            from src.backend.app.planner.agent_model_adapter import ActionProposal

            return ActionProposal.rule_based(ActionEnvelope.model_construct(
                kind="draft_plan", reason="run this", expected_state="CREATED",
                payload={"command": "powershell -Command Invoke-Expression bad"},
            ))

    store = SQLiteDesktopStore(tmp_path / "payload-boundary.sqlite")
    store.add_project(ProjectDetail(id="project-1", name="p", study_id="s", modality="rs-fMRI", created_date="today", subjects_count=0, current_pipeline_id="p", sequences=[], scans_count=0, total_size="0", current_model_id="none"), health_status="ready", rawdata_dir="")
    lifecycle = AgentOrchestrator(store).create(project_id="project-1", command_id="create", actor="user")
    calls: list[str] = []
    service = AgentHarnessService(
        store, config=AgentHarnessConfig(enabled=True), adapter=PayloadAdapter(),
        draft_plan=lambda **_kwargs: calls.append("draft"),
    )
    service.ensure_attempt(lifecycle=lifecycle, provider_ref="rule_based")

    result = service.run_one(lifecycle=lifecycle, actor="user")

    assert result.attempt.status == "STOPPED"
    assert result.attempt.terminal_reason == "AGENT_HARNESS_ACTION_PAYLOAD_INVALID"
    assert calls == []
