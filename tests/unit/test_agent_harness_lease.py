from __future__ import annotations

from datetime import UTC, datetime, timedelta

from src.backend.app.core.config_schema import AgentHarnessConfig
from src.backend.app.schemas.agent_harness import ActionEnvelope
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.agent_harness_service import AgentHarnessService
from src.backend.app.services.agent_orchestrator import AgentOrchestrator
from src.backend.app.services.mock_store import SQLiteDesktopStore


class FinishAdapter:
    def __init__(self): self.calls = 0
    def propose_action(self, **_kwargs):
        self.calls += 1
        return ActionEnvelope(kind="finish", reason="done", expected_state="CREATED")


def test_expired_lease_is_taken_over_once_and_step_is_idempotent(tmp_path) -> None:
    store = SQLiteDesktopStore(tmp_path / "lease.sqlite")
    store.add_project(ProjectDetail(id="project-1", name="p", study_id="s", modality="rs-fMRI", created_date="today", subjects_count=0, current_pipeline_id="p", sequences=[], scans_count=0, total_size="0", current_model_id="none"), health_status="ready", rawdata_dir="")
    lifecycle = AgentOrchestrator(store).create(project_id="project-1", command_id="create", actor="user")
    adapter = FinishAdapter()
    now = datetime(2026, 1, 1, tzinfo=UTC)
    service = AgentHarnessService(store, config=AgentHarnessConfig(enabled=True), adapter=adapter, now=lambda: now)
    attempt = service.ensure_attempt(lifecycle=lifecycle, provider_ref="mock").model_copy(update={"status": "RUNNING", "lease_expires_at": now - timedelta(seconds=1)})
    store.update_agent_harness_attempt(attempt, expected_status="READY")

    first = service.run_one(lifecycle=lifecycle, actor="user", lease_owner="restarted")
    second = service.run_one(lifecycle=lifecycle, actor="user", lease_owner="again")

    assert first.attempt.lease_takeovers == 1
    assert first.attempt.status == "FINISHED"
    assert second.attempt.status == "FINISHED"
    assert adapter.calls == 1
