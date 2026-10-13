from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from src.backend.app.schemas.agent_harness import (
    AgentHarnessContextSection,
    AgentHarnessContextSections,
    AgentPlanningBaseContext,
    AgentTeamRoleContext,
    agent_planning_base_context_hash,
    agent_team_role_context_hash,
)
from src.backend.app.schemas.agent_lifecycle import AgentLifecycleEvent, AgentLifecycleRecord
from src.backend.app.schemas.agent_team import (
    AgentTeamBudget,
    AgentTeamBudgetSlice,
    AgentTeamContextSnapshot,
    AgentTeamEvent,
    AgentTeamFinding,
    AgentTeamModelCallRecord,
    AgentTeamRecord,
    AgentTeamWakeRecord,
    AgentTeamWorkItem,
    agent_team_context_snapshot_hash,
    agent_team_finding_hash,
    canonical_hash,
)
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.schemas.project_agent_settings import UpdateProjectAgentSettingsRequest
from src.backend.app.services.mock_store import SQLiteDesktopStore
from src.backend.app.services.project_agent_settings_service import ProjectAgentSettingsService

NOW = datetime(2026, 10, 13, 18, tzinfo=UTC)
HASH = "a" * 64
ROLE_IDS = {
    "science": "science_reviewer.v1",
    "safety": "safety_reviewer.v1",
    "completeness": "completeness_reviewer.v1",
}
PURPOSES = {
    "science": "science_prerequisites",
    "safety": "project_safety",
    "completeness": "goal_coverage",
}


def _store(tmp_path: Path, *, project_id: str = "project-1") -> SQLiteDesktopStore:
    project_dir = tmp_path / project_id
    project_dir.mkdir(parents=True)
    store = SQLiteDesktopStore(tmp_path / "team.sqlite")
    store.add_project(
        ProjectDetail(
            id=project_id,
            name="Project",
            study_id=f"study-{project_id}",
            modality="rs-fMRI",
            created_date="2026-10-13",
            subjects_count=0,
            current_pipeline_id="",
            sequences=[],
            scans_count=0,
            total_size="0",
            current_model_id="",
            metadata={"project_dir": str(project_dir)},
        ),
        health_status="ready",
        rawdata_dir="",
    )
    ProjectAgentSettingsService(store).update(
        project_id=project_id,
        request=UpdateProjectAgentSettingsRequest(
            multi_agent_consent_enabled=True,
            multi_agent_max_model_calls=6,
            multi_agent_max_input_tokens=6000,
        ),
    )
    return store


def _base_context(project_id: str, lifecycle_id: str) -> AgentPlanningBaseContext:
    sections = AgentHarnessContextSections(
        **{
            name: AgentHarnessContextSection(source_hash=HASH, data={"section": name})
            for name in AgentHarnessContextSections.model_fields
        }
    )
    payload = {
        "schema_version": 1,
        "planning_epoch": 0,
        "lifecycle_id": lifecycle_id,
        "project_id": project_id,
        "sections": sections.model_dump(mode="json"),
        "section_hashes": {
            name: canonical_hash(getattr(sections, name))
            for name in AgentHarnessContextSections.model_fields
        },
        "evidence_refs": (),
        "evidence_snapshot_hash": HASH,
        "projection_policy_version": "projection-v1",
        "complete": True,
        "incomplete_reason": None,
        "project_snapshot_hash": HASH,
        "policy_version": "policy-v1",
        "redaction_policy_version": "redaction-v1",
    }
    return AgentPlanningBaseContext(
        base_context_hash=agent_planning_base_context_hash(payload),
        **{key: value for key, value in payload.items() if key != "schema_version"},
    )


def _create_lifecycle(store: SQLiteDesktopStore, project_id: str, lifecycle_id: str) -> None:
    record = AgentLifecycleRecord(
        lifecycle_id=lifecycle_id,
        project_id=project_id,
        state="CONTEXT_READY",
        created_at=NOW,
        updated_at=NOW,
    )
    event = AgentLifecycleEvent(
        event_id=f"{lifecycle_id}-created",
        lifecycle_id=lifecycle_id,
        project_id=project_id,
        command_id=f"{lifecycle_id}-create-command",
        actor="test",
        source_command="agent.create",
        occurred_at=NOW,
        from_state=None,
        to_state="CONTEXT_READY",
    )
    store.create_agent_lifecycle(record, event)


def _bundle(store: SQLiteDesktopStore, project_id: str, lifecycle_id: str):
    _create_lifecycle(store, project_id, lifecycle_id)
    base = _base_context(project_id, lifecycle_id)
    consent = ProjectAgentSettingsService(store).get(project_id=project_id).multi_agent_consent
    team_id = f"team-{lifecycle_id}"
    deadline = NOW + timedelta(minutes=3)
    role_contexts: dict[str, AgentTeamRoleContext] = {}
    for kind, role_id in ROLE_IDS.items():
        goal = AgentHarnessContextSection(source_hash=HASH, data={"goal": "review"})
        payload = {
            "schema_version": 1,
            "team_id": team_id,
            "team_epoch": 1,
            "worker_id": f"worker-{kind}",
            "role_id": role_id,
            "reviewer_kind": kind,
            "project_id": project_id,
            "lifecycle_id": lifecycle_id,
            "base_context_hash": base.base_context_hash,
            "purpose": PURPOSES[kind],
            "included_sections": ("goal",),
            "omitted_sections": tuple(
                name for name in AgentHarnessContextSections.model_fields if name != "goal"
            ),
            "sections": {"goal": goal.model_dump(mode="json")},
            "section_hashes": {"goal": canonical_hash(goal)},
            "evidence_refs": (),
            "model_profile_hash": HASH,
            "prompt_template_version": "review-prompt-v1",
            "output_schema_version": "review-finding-v1",
            "policy_version": "policy-v1",
            "redaction_policy_version": "redaction-v1",
            "consent_epoch": consent.consent_epoch,
            "consent_hash": consent.consent_hash,
            "budget_slice": AgentTeamBudgetSlice(
                max_model_calls=2,
                reserved_input_tokens=1000,
                deadline_at=deadline,
            ).model_dump(mode="json"),
            "deadline_at": deadline,
        }
        role_contexts[kind] = AgentTeamRoleContext(
            role_context_hash=agent_team_role_context_hash(payload),
            **{key: value for key, value in payload.items() if key != "schema_version"},
        )
    snapshot_payload = {
        "schema_version": 1,
        "team_id": team_id,
        "team_epoch": 1,
        "project_id": project_id,
        "lifecycle_id": lifecycle_id,
        "base_context_hash": base.base_context_hash,
        "role_context_hashes": {
            kind: context.role_context_hash for kind, context in role_contexts.items()
        },
        "consent_epoch": consent.consent_epoch,
        "consent_hash": consent.consent_hash,
        "role_registry_hash": HASH,
        "context_projector_hash": HASH,
        "policy_version": "policy-v1",
        "redaction_policy_version": "redaction-v1",
        "prompt_template_version": "review-prompt-v1",
        "output_schema_version": "review-finding-v1",
    }
    snapshot = AgentTeamContextSnapshot(
        snapshot_hash=agent_team_context_snapshot_hash(snapshot_payload), **snapshot_payload
    )
    team = AgentTeamRecord(
        team_id=team_id,
        team_epoch=1,
        project_id=project_id,
        lifecycle_id=lifecycle_id,
        snapshot_hash=snapshot.snapshot_hash,
        role_registry_hash=HASH,
        context_projector_hash=HASH,
        aggregation_policy_hash=HASH,
        model_profile_hash=HASH,
        global_policy_hash=HASH,
        consent_epoch=consent.consent_epoch,
        consent_hash=consent.consent_hash,
        budget=AgentTeamBudget(
            max_model_calls=3,
            max_repairs=0,
            reserved_input_tokens=3000,
            deadline_at=deadline,
        ),
        created_at=NOW,
        updated_at=NOW,
    )
    work_items = tuple(
        AgentTeamWorkItem(
            work_item_id=f"work-{lifecycle_id}-{kind}",
            team_id=team_id,
            team_epoch=1,
            project_id=project_id,
            lifecycle_id=lifecycle_id,
            reviewer_kind=kind,
            role_id=role_id,
            available_at=NOW,
            deadline_at=deadline,
            created_at=NOW,
            updated_at=NOW,
        )
        for kind, role_id in ROLE_IDS.items()
    )
    command_id = f"{lifecycle_id}-team-request"
    event = AgentTeamEvent(
        event_id=f"{team_id}-requested",
        team_id=team_id,
        team_epoch=1,
        project_id=project_id,
        lifecycle_id=lifecycle_id,
        command_id=command_id,
        idempotency_key=f"{team_id}:requested",
        source="coordinator",
        event_type="TEAM_REQUESTED",
        payload_hash=canonical_hash({"team_id": team_id}),
        occurred_at=NOW,
    )
    wakes = tuple(
        AgentTeamWakeRecord(
            wake_id=f"{team_id}-wake-{item.reviewer_kind}",
            team_id=team_id,
            team_epoch=1,
            project_id=project_id,
            lifecycle_id=lifecycle_id,
            step_key=f"work:{item.work_item_id}:attempt:1",
            reason="work_item",
            status="PENDING",
            available_at=NOW,
            created_at=NOW,
            updated_at=NOW,
        )
        for item in work_items
    )
    current = store.get_agent_lifecycle(lifecycle_id)
    assert current is not None
    bound_lifecycle = AgentLifecycleRecord.model_validate(
        {
            **current.model_dump(mode="python"),
            "planning_mode": "multi_agent",
            "planning_team_id": team_id,
            "planning_team_epoch": 1,
            "updated_at": NOW + timedelta(seconds=1),
            "last_command_id": command_id,
        }
    )
    lifecycle_event = AgentLifecycleEvent(
        event_id=f"{team_id}-lifecycle-bound",
        lifecycle_id=lifecycle_id,
        project_id=project_id,
        command_id=command_id,
        actor="coordinator",
        source_command="agent.team.request",
        occurred_at=NOW + timedelta(seconds=1),
        from_state=current.state,
        to_state=current.state,
    )
    bundle = {
        "lifecycle_record": bound_lifecycle,
        "lifecycle_event": lifecycle_event,
        "expected_team_id": None,
        "expected_team_epoch": 0,
        "base_context": base,
        "team": team,
        "context_snapshot": snapshot,
        "role_contexts": role_contexts,
        "work_items": work_items,
        "event": event,
        "wakes": wakes,
    }
    return bundle


def _start_call(
    store: SQLiteDesktopStore, bundle: dict[str, object], *, lease_seconds: int = 60
):
    team = bundle["team"]
    work = next(item for item in bundle["work_items"] if item.reviewer_kind == "science")
    role_context = bundle["role_contexts"]["science"]
    call = AgentTeamModelCallRecord(
        model_call_id=f"call-{team.team_id}-science-1",
        team_id=team.team_id,
        team_epoch=team.team_epoch,
        project_id=team.project_id,
        lifecycle_id=team.lifecycle_id,
        work_item_id=work.work_item_id,
        reviewer_kind="science",
        role_id="science_reviewer.v1",
        role_registry_hash=team.role_registry_hash,
        role_context_hash=role_context.role_context_hash,
        model_profile_hash=role_context.model_profile_hash,
        request_hash="c" * 64,
        request_bytes=128,
        provider_id="offline-test",
        model_id="fixed-test-model",
        endpoint_class="offline-test",
        prompt_template_version=role_context.prompt_template_version,
        output_schema_version=role_context.output_schema_version,
        consent_epoch=team.consent_epoch,
        consent_hash=team.consent_hash,
        started_at=NOW + timedelta(seconds=2),
    )
    event = AgentTeamEvent(
        event_id=f"{call.model_call_id}-started-event",
        team_id=team.team_id,
        team_epoch=team.team_epoch,
        project_id=team.project_id,
        lifecycle_id=team.lifecycle_id,
        command_id=f"{call.model_call_id}:start",
        idempotency_key=f"{call.model_call_id}:start",
        source="worker",
        event_type="MODEL_CALL_STARTED",
        payload_hash=canonical_hash({"model_call_id": call.model_call_id}),
        occurred_at=call.started_at,
    )
    return store.claim_agent_team_work_and_start_call(
        work_item_id=work.work_item_id,
        expected_fencing_token=0,
        owner="worker-1",
        now=NOW + timedelta(seconds=2),
        lease_expires_at=NOW + timedelta(seconds=lease_seconds),
        call=call,
        event=event,
    )


def _completed_call(call: AgentTeamModelCallRecord) -> AgentTeamModelCallRecord:
    payload = {
        **call.model_dump(mode="python"),
        "network_called": True,
        "status": "completed",
        "response_hash": "d" * 64,
        "schema_valid": True,
        "provider_request_id": "provider-request-1",
        "input_tokens": 450,
        "output_tokens": 60,
        "latency_ms": 250,
        "completed_at": NOW + timedelta(seconds=5),
    }
    return AgentTeamModelCallRecord.model_validate(payload)


def _finding(call: AgentTeamModelCallRecord) -> AgentTeamFinding:
    payload = {
        "schema_version": 1,
        "reviewer_kind": "science",
        "severity": "warning",
        "code": "MISSING_PREREQUISITE",
        "message_key": "agent.finding.missing_prerequisite",
        "input_refs": ("goal:analysis-scope",),
        "suggested_change": None,
        "finding_id": f"finding-{call.model_call_id}",
        "team_id": call.team_id,
        "team_epoch": call.team_epoch,
        "project_id": call.project_id,
        "lifecycle_id": call.lifecycle_id,
        "work_item_id": call.work_item_id,
        "role_id": call.role_id,
        "role_context_hash": call.role_context_hash,
        "model_call_id": call.model_call_id,
        "model_response_hash": call.response_hash,
    }
    return AgentTeamFinding(
        finding_hash=agent_team_finding_hash(payload),
        **{key: value for key, value in payload.items() if key != "schema_version"},
    )


def _completion_event(call: AgentTeamModelCallRecord) -> AgentTeamEvent:
    return AgentTeamEvent(
        event_id=f"{call.model_call_id}-completed-event",
        team_id=call.team_id,
        team_epoch=call.team_epoch,
        project_id=call.project_id,
        lifecycle_id=call.lifecycle_id,
        command_id=f"{call.model_call_id}:complete",
        idempotency_key=f"{call.model_call_id}:complete",
        source="worker",
        event_type="MODEL_CALL_COMPLETED",
        payload_hash=canonical_hash({"model_call_id": call.model_call_id, "response_hash": call.response_hash}),
        occurred_at=call.completed_at,
    )


def _completion_wake(call: AgentTeamModelCallRecord) -> AgentTeamWakeRecord:
    return AgentTeamWakeRecord(
        wake_id=f"{call.model_call_id}-next-wake",
        team_id=call.team_id,
        team_epoch=call.team_epoch,
        project_id=call.project_id,
        lifecycle_id=call.lifecycle_id,
        step_key=f"{call.model_call_id}:completed",
        reason="aggregate",
        status="PENDING",
        available_at=call.completed_at,
        created_at=call.completed_at,
        updated_at=call.completed_at,
    )


def test_team_bundle_persists_atomically_and_is_project_scoped(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _store(tmp_path, project_id="project-2")
    bundle = _bundle(store, "project-1", "life-1")

    team = store.create_agent_team_bundle(**bundle)
    assert store.create_agent_team_bundle(**bundle) == team
    reopened = SQLiteDesktopStore(store.db_path)

    assert reopened.get_agent_team("project-1", team.team_id) == team
    assert reopened.get_agent_team("project-2", team.team_id) is None
    snapshot, roles = reopened.get_agent_team_context("project-1", team.team_id)
    assert snapshot.snapshot_hash == team.snapshot_hash
    assert set(roles) == set(ROLE_IDS)
    assert len(reopened.list_agent_team_work_items("project-1", team.team_id)) == 3
    assert len(reopened.list_agent_team_events("project-1", team.team_id)) == 1
    assert len(reopened.list_agent_team_wakes("project-1", team.team_id)) == 3
    assert reopened.get_agent_planning_base_context(
        "project-1", bundle["base_context"].base_context_hash
    ) == bundle["base_context"]
    assert reopened.get_agent_planning_base_context(
        "project-2", bundle["base_context"].base_context_hash
    ) is None
    lifecycle = reopened.get_agent_lifecycle("life-1")
    assert lifecycle.planning_team_id == team.team_id
    assert lifecycle.planning_team_epoch == 1
    assert reopened.get_agent_team("other-project", team.team_id) is None

    with sqlite3.connect(store.db_path) as conn:
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {
        "agent_planning_base_contexts",
        "agent_teams",
        "agent_team_contexts",
        "agent_team_work_items",
        "agent_team_model_calls",
        "agent_team_findings",
        "agent_team_advisories",
        "agent_team_events",
        "agent_team_wake_outbox",
    } <= tables


def test_base_context_replay_ignores_non_identity_creation_time(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _create_lifecycle(store, "project-1", "life-base-replay")
    base = _base_context("project-1", "life-base-replay")

    first = store.add_agent_planning_base_context(base)
    replay = store.add_agent_planning_base_context(
        base.model_copy(update={"created_at": NOW + timedelta(seconds=5)})
    )

    assert replay == first
    assert store.get_agent_planning_base_context(
        "project-1", base.base_context_hash
    ) == first


def test_team_bundle_rolls_back_when_consent_was_revoked(tmp_path: Path) -> None:
    store = _store(tmp_path)
    bundle = _bundle(store, "project-1", "life-revoked")
    ProjectAgentSettingsService(store).update(
        project_id="project-1",
        request=UpdateProjectAgentSettingsRequest(multi_agent_consent_enabled=False),
    )

    with pytest.raises(RuntimeError, match="MULTI_AGENT_CONSENT_INVALIDATED"):
        store.create_agent_team_bundle(**bundle)

    assert store.get_agent_lifecycle("life-revoked").planning_mode == "single_agent"
    assert store.get_agent_team("project-1", bundle["team"].team_id) is None
    assert store.get_agent_planning_base_context(
        "project-1", bundle["base_context"].base_context_hash
    ) is None
    assert store.list_agent_teams("project-1", lifecycle_id="life-revoked") == []


def test_claim_fencing_and_consent_preflight_cancel_before_network(tmp_path: Path) -> None:
    store = _store(tmp_path)
    bundle = _bundle(store, "project-1", "life-preflight")
    store.create_agent_team_bundle(**bundle)
    work, call = _start_call(store, bundle)
    assert work.status == "MODEL_STARTED"
    assert work.fencing_token == 1
    assert call.status == "started" and not call.network_called

    with pytest.raises(RuntimeError, match="AGENT_TEAM_FENCING_TOKEN_STALE"):
        store.mark_agent_team_call_network_called(
            project_id="project-1",
            model_call_id=call.model_call_id,
            owner="worker-1",
            fencing_token=0,
            now=NOW + timedelta(seconds=3),
        )

    ProjectAgentSettingsService(store).update(
        project_id="project-1",
        request=UpdateProjectAgentSettingsRequest(multi_agent_consent_enabled=False),
    )
    canceled = store.mark_agent_team_call_network_called(
        project_id="project-1",
        model_call_id=call.model_call_id,
        owner="worker-1",
        fencing_token=1,
        now=NOW + timedelta(seconds=3),
    )
    assert canceled.status == "canceled"
    assert canceled.network_called is False
    assert store.list_agent_team_findings("project-1", call.team_id) == []
    assert store.get_agent_team_work_item("project-1", work.work_item_id).status == "CANCELED"
    assert store.get_agent_team("project-1", call.team_id).status == "NEEDS_ATTENTION"


def test_team_wake_claim_takeover_is_fenced_and_idempotently_completed(tmp_path: Path) -> None:
    store = _store(tmp_path)
    bundle = _bundle(store, "project-1", "life-wake-fencing")
    team = store.create_agent_team_bundle(**bundle)
    lease_expires = NOW + timedelta(seconds=3)
    claimed = [
        store.claim_next_agent_team_wake(
            owner=f"worker-{index}",
            now=NOW,
            lease_expires_at=lease_expires,
        )
        for index in range(3)
    ]
    assert all(wake is not None for wake in claimed)
    assert len({wake.wake_id for wake in claimed}) == 3

    reclaimed = store.claim_next_agent_team_wake(
        owner="replacement-worker",
        now=NOW + timedelta(seconds=4),
        lease_expires_at=NOW + timedelta(seconds=20),
    )
    assert reclaimed is not None
    prior = next(wake for wake in claimed if wake.wake_id == reclaimed.wake_id)
    assert reclaimed.fencing_token == prior.fencing_token + 1
    with pytest.raises(RuntimeError, match="AGENT_TEAM_WAKE_CONCURRENT_UPDATE"):
        store.complete_agent_team_wake(
            prior,
            owner=prior.lease_owner,
            now=NOW + timedelta(seconds=5),
        )
    completed = store.complete_agent_team_wake(
        reclaimed,
        owner="replacement-worker",
        now=NOW + timedelta(seconds=5),
    )
    assert completed.status == "CONSUMED"
    assert store.get_agent_team("project-1", team.team_id).status == "READY"


def test_expired_prenetwork_call_is_reclaimed_with_a_new_fencing_token(tmp_path: Path) -> None:
    store = _store(tmp_path)
    bundle = _bundle(store, "project-1", "life-reclaim")
    store.create_agent_team_bundle(**bundle)
    first_work, call = _start_call(store, bundle, lease_seconds=3)
    event = AgentTeamEvent(
        event_id=f"{call.model_call_id}-reclaimed-event",
        team_id=call.team_id,
        team_epoch=call.team_epoch,
        project_id=call.project_id,
        lifecycle_id=call.lifecycle_id,
        command_id=f"{call.model_call_id}:reclaim:2",
        idempotency_key=f"{call.model_call_id}:reclaim:2",
        source="scheduler",
        event_type="WORK_ITEM_CLAIMED",
        payload_hash=canonical_hash({"model_call_id": call.model_call_id, "fencing_token": 2}),
        occurred_at=NOW + timedelta(seconds=4),
    )

    reclaimed_work, reclaimed_call = store.reclaim_agent_team_call(
        project_id="project-1",
        model_call_id=call.model_call_id,
        expected_fencing_token=1,
        owner="worker-2",
        now=NOW + timedelta(seconds=4),
        lease_expires_at=NOW + timedelta(seconds=30),
        event=event,
    )
    assert reclaimed_call == call
    assert reclaimed_work.lease_owner == "worker-2"
    assert reclaimed_work.fencing_token == 2

    with pytest.raises(RuntimeError, match="AGENT_TEAM_FENCING_TOKEN_STALE"):
        store.mark_agent_team_call_network_called(
            project_id="project-1",
            model_call_id=call.model_call_id,
            owner="worker-1",
            fencing_token=1,
            now=NOW + timedelta(seconds=5),
        )
    marked = store.mark_agent_team_call_network_called(
        project_id="project-1",
        model_call_id=call.model_call_id,
        owner="worker-2",
        fencing_token=2,
        now=NOW + timedelta(seconds=5),
    )
    assert marked.network_called is True
    assert first_work.fencing_token == 1


def test_completion_is_atomic_idempotent_and_rejects_late_revocation(tmp_path: Path) -> None:
    store = _store(tmp_path)
    bundle = _bundle(store, "project-1", "life-complete")
    team = store.create_agent_team_bundle(**bundle)
    _, started = _start_call(store, bundle)
    store.mark_agent_team_call_network_called(
        project_id="project-1",
        model_call_id=started.model_call_id,
        owner="worker-1",
        fencing_token=1,
        now=NOW + timedelta(seconds=3),
    )
    completed = _completed_call(started)
    finding = _finding(completed)
    event = _completion_event(completed)
    wake = _completion_wake(completed)

    result = store.complete_agent_team_model_call(
        project_id="project-1",
        model_call_id=completed.model_call_id,
        owner="worker-1",
        fencing_token=1,
        now=NOW + timedelta(seconds=6),
        completed_call=completed,
        findings=(finding,),
        event=event,
        wake=wake,
    )
    assert result == completed
    assert store.complete_agent_team_model_call(
        project_id="project-1",
        model_call_id=completed.model_call_id,
        owner="worker-1",
        fencing_token=1,
        now=NOW + timedelta(seconds=6),
        completed_call=completed,
        findings=(finding,),
        event=event,
        wake=wake,
    ) == completed
    assert store.get_agent_team_work_item("project-1", started.work_item_id).status == "COMPLETED"
    assert store.list_agent_team_findings("project-1", team.team_id) == [finding]
    assert len(store.list_agent_team_wakes("project-1", team.team_id)) == 4
    reopened = SQLiteDesktopStore(store.db_path)
    assert reopened.get_agent_team_model_call("project-1", completed.model_call_id) == completed
    assert reopened.list_agent_team_findings("project-1", team.team_id) == [finding]
    assert len(reopened.list_agent_team_wakes("project-1", team.team_id)) == 4

    revoked_store = _store(tmp_path / "revoked")
    revoked_bundle = _bundle(revoked_store, "project-1", "life-late-revocation")
    revoked_team = revoked_store.create_agent_team_bundle(**revoked_bundle)
    _, revoked_started = _start_call(revoked_store, revoked_bundle)
    revoked_store.mark_agent_team_call_network_called(
        project_id="project-1",
        model_call_id=revoked_started.model_call_id,
        owner="worker-1",
        fencing_token=1,
        now=NOW + timedelta(seconds=3),
    )
    late_response = _completed_call(revoked_started)
    ProjectAgentSettingsService(revoked_store).update(
        project_id="project-1",
        request=UpdateProjectAgentSettingsRequest(multi_agent_consent_enabled=False),
    )
    canceled = revoked_store.complete_agent_team_model_call(
        project_id="project-1",
        model_call_id=late_response.model_call_id,
        owner="worker-1",
        fencing_token=1,
        now=NOW + timedelta(seconds=6),
        completed_call=late_response,
        findings=(_finding(late_response),),
        event=_completion_event(late_response),
        wake=_completion_wake(late_response),
    )
    assert canceled.status == "canceled"
    assert revoked_store.list_agent_team_findings("project-1", revoked_team.team_id) == []
    revoked_wakes = revoked_store.list_agent_team_wakes("project-1", revoked_team.team_id)
    assert len(revoked_wakes) == 3
    assert {wake.status for wake in revoked_wakes} == {"CANCELED"}
    assert revoked_store.get_agent_team_work_item(
        "project-1", revoked_bundle["work_items"][0].work_item_id
    ).status == "CANCELED"
