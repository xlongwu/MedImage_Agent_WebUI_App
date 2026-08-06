from __future__ import annotations

from src.backend.app.core.config_schema import AgentHarnessConfig
from src.backend.app.schemas.agent_harness import ActionEnvelope
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.agent_harness_service import AgentHarnessService
from src.backend.app.services.agent_orchestrator import AgentOrchestrator
from src.backend.app.services.mock_store import SQLiteDesktopStore


class Adapter:
    def __init__(self, *actions):
        self.actions = list(actions)
        self.calls = 0

    def propose_action(self, **_kwargs):
        self.calls += 1
        return self.actions.pop(0)


def _store(tmp_path) -> SQLiteDesktopStore:
    store = SQLiteDesktopStore(tmp_path / "harness.sqlite")
    store.add_project(ProjectDetail(
        id="project-1", name="project", study_id="study", modality="rs-fMRI", created_date="today",
        subjects_count=1, current_pipeline_id="pipeline", sequences=[], scans_count=1, total_size="0", current_model_id="none",
    ), health_status="ready", rawdata_dir="")
    return store


def _created(store):
    return AgentOrchestrator(store).create(project_id="project-1", command_id="create", actor="user", goal_text="Make a plan")


def test_request_decision_is_persisted_and_waits_for_user(tmp_path) -> None:
    store = _store(tmp_path)
    lifecycle = _created(store)
    adapter = Adapter(ActionEnvelope(
        kind="request_decision", reason="Need atlas", expected_state="CREATED",
        payload={"kind": "atlas", "question": "Choose atlas", "impact": "Changes regions", "options": [{"id": "aal", "label": "AAL"}]},
    ))
    service = AgentHarnessService(store, config=AgentHarnessConfig(enabled=True), adapter=adapter)
    service.ensure_attempt(lifecycle=lifecycle, provider_ref="mock")

    result = service.run_one(lifecycle=lifecycle, actor="user")

    assert result.lifecycle.state == "WAITING_FOR_SCIENCE_DECISION"
    assert result.attempt.status == "WAITING_FOR_USER"
    assert store.list_agent_harness_steps(result.attempt.attempt_id)[0].kind == "request_decision"


def test_invalid_or_stale_action_stops_without_second_model_call(tmp_path) -> None:
    store = _store(tmp_path)
    lifecycle = _created(store)
    adapter = Adapter(ActionEnvelope(kind="finish", reason="wrong state", expected_state="PLAN_DRAFTED"))
    service = AgentHarnessService(store, config=AgentHarnessConfig(enabled=True), adapter=adapter)
    service.ensure_attempt(lifecycle=lifecycle, provider_ref="mock")

    result = service.run_one(lifecycle=lifecycle, actor="user")

    assert result.attempt.status == "STOPPED"
    assert result.attempt.terminal_reason == "AGENT_HARNESS_STALE_ACTION"
    assert adapter.calls == 1


def test_budget_exhaustion_happens_before_model_call(tmp_path) -> None:
    store = _store(tmp_path)
    lifecycle = _created(store)
    adapter = Adapter(ActionEnvelope(kind="finish", reason="done", expected_state="CREATED"))
    service = AgentHarnessService(store, config=AgentHarnessConfig(enabled=True, max_model_calls=1), adapter=adapter)
    attempt = service.ensure_attempt(lifecycle=lifecycle, provider_ref="mock").model_copy(update={"model_calls_used": 1})
    store.update_agent_harness_attempt(attempt, expected_status="READY")

    result = service.run_one(lifecycle=lifecycle, actor="user")

    assert result.attempt.terminal_reason == "AGENT_HARNESS_BUDGET_EXHAUSTED"
    assert adapter.calls == 0


def test_invalid_json_gets_one_repair_and_counts_both_model_calls(tmp_path) -> None:
    class RepairAdapter:
        def __init__(self) -> None:
            self.repairs: list[bool] = []

        def propose_action(self, *, repair: bool, **_kwargs):
            self.repairs.append(repair)
            if not repair:
                raise ValueError("invalid JSON")
            return ActionEnvelope(kind="finish", reason="fixed", expected_state="CREATED")

    store = _store(tmp_path)
    lifecycle = _created(store)
    adapter = RepairAdapter()
    service = AgentHarnessService(store, config=AgentHarnessConfig(enabled=True), adapter=adapter)
    service.ensure_attempt(lifecycle=lifecycle, provider_ref="mock")

    result = service.run_one(lifecycle=lifecycle, actor="user")

    assert adapter.repairs == [False, True]
    assert result.attempt.model_calls_used == 2
    assert result.attempt.status == "FINISHED"
