from __future__ import annotations

import threading
from datetime import timedelta

from src.backend.app.planner.llm_provider import LLMProviderResult
from src.backend.app.schemas.agent_team import (
    AgentTeamEvent,
    AgentTeamModelCallRecord,
    canonical_hash,
)
from src.backend.app.services.agent_team_scheduler import AgentTeamScheduler
from src.backend.app.services.agent_team_worker_service import AgentTeamWorkerService
from src.backend.app.services.project_agent_settings_service import ProjectAgentSettingsService
from src.backend.app.services.structured_agent_model_adapter import StructuredAgentModelAdapter
from tests.unit.test_agent_team_coordinator_service import NOW, _setup


class _Provider:
    def __init__(self, *, malformed_first_science: bool = False, timeout_science: bool = False):
        self.malformed_first_science = malformed_first_science
        self.timeout_science = timeout_science
        self.calls: list[tuple[str, bool]] = []

    def complete(self, *, request) -> LLMProviderResult:
        role = request.context_payload["role_context"]["reviewer_kind"]
        self.calls.append((role, request.repair))
        if self.timeout_science and role == "science":
            return LLMProviderResult(
                ok=False,
                content="",
                errors=["AGENT_MODEL_PROVIDER_TIMEOUT"],
                model=request.model,
                network_called=True,
            )
        if self.malformed_first_science and role == "science" and not request.repair:
            content = "{}"
        else:
            content = "[]"
        return LLMProviderResult(
            ok=True,
            content=content,
            model=request.model,
            input_tokens=80,
            output_tokens=12,
            cached_input_tokens=5,
            latency_ms=17,
            provider_request_id=f"request-{len(self.calls)}",
            network_called=True,
        )


class _ConcurrentProvider(_Provider):
    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def complete(self, *, request) -> LLMProviderResult:
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.active == 3:
                self.entered.set()
        self.release.wait(timeout=5)
        try:
            return super().complete(request=request)
        finally:
            with self._lock:
                self.active -= 1


def _team_scheduler(
    tmp_path, *, provider: _Provider, now=lambda: NOW, start_workers: bool = False
):
    store, lifecycle, base_context, eligibility, coordinator = _setup(tmp_path)
    created = coordinator.create_team(
        lifecycle=lifecycle,
        base_context=base_context,
        eligibility_input=eligibility,
        command_id="team-create",
        actor="test",
        now=NOW,
    )
    worker = AgentTeamWorkerService(
        multi_agent_config=coordinator.multi_agent_config,
        model_config=coordinator.model_config,
        adapter=StructuredAgentModelAdapter(provider),
    )
    resumed: list[tuple[str, str]] = []
    scheduler = AgentTeamScheduler(
        store,
        worker_service=worker,
        coordinator_service=coordinator,
        policy_service=coordinator.policy_service,
        planning_resume=lambda project_id, lifecycle_id: resumed.append(
            (project_id, lifecycle_id)
        ),
        start_workers=start_workers,
        now=now,
    )
    return store, created.team, scheduler, provider, resumed


def _drain(scheduler: AgentTeamScheduler) -> None:
    for _ in range(20):
        if scheduler.run_once(owner="test-scheduler") is None:
            return


def test_team_scheduler_completes_parallel_roles_and_freezes_advisory(tmp_path) -> None:
    provider = _Provider()
    store, team, scheduler, _, resumed = _team_scheduler(tmp_path, provider=provider)

    _drain(scheduler)

    completed = store.get_agent_team("project-1", team.team_id)
    advisory = store.get_agent_team_advisory("project-1", team.team_id)
    assert completed.status == "COMPLETED"
    assert advisory is not None and advisory.decision == "accepted"
    assert advisory.safety_reviewer_present is True
    assert {item.status for item in store.list_agent_team_work_items("project-1", team.team_id)} == {
        "COMPLETED"
    }
    calls = store.list_agent_team_model_calls("project-1", team.team_id)
    assert len(calls) == 3 and {call.status for call in calls} == {"completed"}
    assert {role for role, _ in provider.calls} == {"science", "safety", "completeness"}
    assert resumed == [("project-1", team.lifecycle_id)]


def test_team_scheduler_performs_only_one_bound_schema_repair(tmp_path) -> None:
    provider = _Provider(malformed_first_science=True)
    store, team, scheduler, _, _ = _team_scheduler(tmp_path, provider=provider)

    _drain(scheduler)

    calls = store.list_agent_team_model_calls("project-1", team.team_id)
    science = sorted(
        (call for call in calls if call.reviewer_kind == "science"),
        key=lambda call: call.attempt_no,
    )
    assert len(science) == 2
    assert science[0].status == "failed" and science[0].schema_valid is False
    assert science[1].status == "completed"
    assert science[1].attempt_no == 2
    assert science[1].repair_of_model_call_id == science[0].model_call_id
    assert [repair for role, repair in provider.calls if role == "science"] == [False, True]
    assert store.get_agent_team("project-1", team.team_id).status == "COMPLETED"


def test_team_scheduler_timeout_is_indeterminate_and_stops_other_roles(tmp_path) -> None:
    provider = _Provider(timeout_science=True)
    store, team, scheduler, _, _ = _team_scheduler(tmp_path, provider=provider)

    for _ in range(10):
        if store.get_agent_team("project-1", team.team_id).status == "NEEDS_ATTENTION":
            break
        scheduler.run_once(owner="test-timeout")

    failed = store.get_agent_team("project-1", team.team_id)
    assert failed.status == "NEEDS_ATTENTION"
    science = next(
        call for call in store.list_agent_team_model_calls("project-1", team.team_id)
        if call.reviewer_kind == "science"
    )
    assert science.status == "indeterminate"
    assert science.error_code == "AGENT_MODEL_PROVIDER_TIMEOUT"
    assert sum(role == "science" for role, _ in provider.calls) == 1
    statuses = {item.status for item in store.list_agent_team_work_items("project-1", team.team_id)}
    assert "INDETERMINATE" in statuses
    assert statuses <= {"INDETERMINATE", "CANCELED", "COMPLETED"}


def test_team_scheduler_reclaims_pre_network_call_without_creating_another_record(tmp_path) -> None:
    clock = [NOW]
    provider = _Provider()
    store, team, scheduler, _, _ = _team_scheduler(
        tmp_path,
        provider=provider,
        now=lambda: clock[0],
    )
    wake = min(
        store.list_agent_team_wakes("project-1", team.team_id),
        key=lambda item: item.wake_id,
    )
    work = store.get_agent_team_work_item("project-1", scheduler._work_item_id(wake.step_key))
    snapshot, role_contexts = store.get_agent_team_context("project-1", team.team_id)
    role_context = role_contexts[work.reviewer_kind]
    consent = ProjectAgentSettingsService(store).get(
        project_id="project-1"
    ).multi_agent_consent
    prepared = scheduler.worker_service.prepare_review(
        team=team,
        work_item=work,
        context_snapshot=snapshot,
        role_context=role_context,
        consent=consent,
        now=NOW,
    )
    call = AgentTeamModelCallRecord(
        model_call_id="pre-network-crash-call",
        team_id=team.team_id,
        team_epoch=team.team_epoch,
        project_id=team.project_id,
        lifecycle_id=team.lifecycle_id,
        work_item_id=work.work_item_id,
        reviewer_kind=work.reviewer_kind,
        role_id=work.role_id,
        role_registry_hash=team.role_registry_hash,
        role_context_hash=role_context.role_context_hash,
        model_profile_hash=team.model_profile_hash,
        request_hash=prepared.request_hash,
        request_bytes=prepared.request_bytes,
        provider_id=prepared.request.provider,
        model_id=prepared.request.model,
        endpoint_class=prepared.request.endpoint_class,
        prompt_template_version=prepared.request.prompt_template_version,
        output_schema_version=role_context.output_schema_version,
        consent_epoch=team.consent_epoch,
        consent_hash=team.consent_hash,
        started_at=NOW,
    )
    store.claim_agent_team_work_and_start_call(
        work_item_id=work.work_item_id,
        expected_fencing_token=0,
        owner="crashed-worker",
        now=NOW,
        lease_expires_at=NOW + timedelta(seconds=1),
        call=call,
        event=AgentTeamEvent(
            event_id="pre-network-start-event",
            team_id=team.team_id,
            team_epoch=team.team_epoch,
            project_id=team.project_id,
            lifecycle_id=team.lifecycle_id,
            command_id="pre-network-start",
            idempotency_key="pre-network-start",
            source="worker",
            event_type="MODEL_CALL_STARTED",
            payload_hash=canonical_hash({"model_call_id": call.model_call_id}),
            occurred_at=NOW,
        ),
    )
    clock[0] = NOW + timedelta(seconds=2)

    scheduler.recover_once_on_startup()

    recovered = store.get_agent_team_model_call("project-1", call.model_call_id)
    assert recovered.status == "completed"
    assert recovered.network_called is True
    calls_for_recovered_work = [
        item
        for item in store.list_agent_team_model_calls("project-1", team.team_id)
        if item.work_item_id == work.work_item_id
    ]
    assert calls_for_recovered_work == [recovered]
    recovered_work = store.get_agent_team_work_item("project-1", work.work_item_id)
    assert recovered_work.fencing_token == 2
    assert recovered_work.status == "COMPLETED"


def test_team_scheduler_runs_three_reviews_concurrently_and_shuts_down(tmp_path) -> None:
    provider = _ConcurrentProvider()
    store, team, scheduler, _, _ = _team_scheduler(
        tmp_path,
        provider=provider,
        start_workers=True,
    )
    scheduler.notify()
    entered_three = provider.entered.wait(timeout=5)
    provider.release.set()
    assert entered_three is True
    for _ in range(500):
        if store.get_agent_team("project-1", team.team_id).status == "COMPLETED":
            break
        threading.Event().wait(0.01)
    assert store.get_agent_team("project-1", team.team_id).status == "COMPLETED"
    assert provider.max_active == 3
    assert scheduler.shutdown() is True
