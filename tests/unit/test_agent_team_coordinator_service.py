from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.backend.app.core.config_schema import AgentModelRuntimeConfig, AgentMultiAgentConfig
from src.backend.app.schemas.agent_harness import (
    AgentHarnessContextSection,
    AgentHarnessContextSections,
    AgentPlanningBaseContext,
    agent_planning_base_context_hash,
)
from src.backend.app.schemas.agent_lifecycle import AgentLifecycleEvent, AgentLifecycleRecord
from src.backend.app.schemas.agent_team import canonical_hash
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.schemas.project_agent_settings import UpdateProjectAgentSettingsRequest
from src.backend.app.services.agent_multi_agent_policy_service import AgentMultiAgentPolicyService
from src.backend.app.services.agent_team_coordinator_service import (
    AgentTeamCoordinatorError,
    AgentTeamCoordinatorService,
    AgentTeamIneligibleError,
)
from src.backend.app.services.agent_team_eligibility_policy import AgentTeamEligibilityInput
from src.backend.app.services.mock_store import SQLiteDesktopStore
from src.backend.app.services.project_agent_settings_service import ProjectAgentSettingsService

NOW = datetime(2026, 10, 13, tzinfo=UTC)
HASH = "a" * 64


def _setup(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    store = SQLiteDesktopStore(tmp_path / "team.sqlite")
    store.add_project(
        ProjectDetail(
            id="project-1", name="Project", study_id="study-1", modality="rs-fMRI",
            created_date="2026-10-13", subjects_count=0, current_pipeline_id="",
            sequences=[], scans_count=0, total_size="0", current_model_id="",
            metadata={"project_dir": str(project_dir)},
        ),
        health_status="ready",
        rawdata_dir="",
    )
    settings_service = ProjectAgentSettingsService(store)
    settings_service.update(
        project_id="project-1",
        request=UpdateProjectAgentSettingsRequest(
            multi_agent_consent_enabled=True,
            multi_agent_max_model_calls=6,
            multi_agent_max_input_tokens=6000,
        ),
    )
    lifecycle = AgentLifecycleRecord(
        lifecycle_id="life-1", project_id="project-1", state="CONTEXT_READY",
        created_at=NOW, updated_at=NOW,
    )
    store.create_agent_lifecycle(
        lifecycle,
        AgentLifecycleEvent(
            event_id="life-created", lifecycle_id="life-1", project_id="project-1",
            command_id="life-create", actor="test", source_command="agent.create",
            occurred_at=NOW, from_state=None, to_state="CONTEXT_READY",
        ),
    )
    sections = AgentHarnessContextSections(
        **{
            name: AgentHarnessContextSection(
                source_hash=HASH,
                data={"section": name},
            )
            for name in AgentHarnessContextSections.model_fields
        }
    )
    base_payload = {
        "schema_version": 1,
        "planning_epoch": 1,
        "lifecycle_id": lifecycle.lifecycle_id,
        "project_id": lifecycle.project_id,
        "sections": sections.model_dump(mode="json"),
        "section_hashes": {
            name: canonical_hash(getattr(sections, name))
            for name in AgentHarnessContextSections.model_fields
        },
        "evidence_refs": ({"type": "project", "record_id": "evidence-1", "hash": HASH},),
        "evidence_snapshot_hash": HASH,
        "projection_policy_version": "projection-v1",
        "complete": True,
        "incomplete_reason": None,
        "project_snapshot_hash": HASH,
        "policy_version": "policy-v1",
        "redaction_policy_version": "redaction-v1",
    }
    base_context = AgentPlanningBaseContext(
        base_context_hash=agent_planning_base_context_hash(base_payload),
        **{key: value for key, value in base_payload.items() if key != "schema_version"},
    )
    consent = settings_service.get(project_id="project-1").multi_agent_consent
    eligibility = AgentTeamEligibilityInput(
        project_id="project-1",
        task_kind="planning",
        independent_evidence_domains=("science", "project"),
        context_is_role_splittable=True,
        provider_allowed=True,
        project_consent_enabled=consent.enabled,
        project_consent_epoch=consent.consent_epoch,
        project_consent_hash=consent.consent_hash,
    )
    multi_config = AgentMultiAgentConfig(enabled=True, max_input_tokens=6000)
    model_config = AgentModelRuntimeConfig(
        enabled=True,
        provider="openai_compatible",
        model="review-model",
        base_url="https://models.example.test/v1",
        api_key="test-key",
    )
    coordinator = AgentTeamCoordinatorService(
        store=store,
        policy_service=AgentMultiAgentPolicyService(store, multi_config),
        multi_agent_config=multi_config,
        model_config=model_config,
    )
    return store, lifecycle, base_context, eligibility, coordinator


def test_coordinator_atomically_creates_roles_and_replays_same_command(tmp_path) -> None:
    store, lifecycle, base_context, eligibility, coordinator = _setup(tmp_path)
    result = coordinator.create_team(
        lifecycle=lifecycle,
        base_context=base_context,
        eligibility_input=eligibility,
        command_id="create-team-1",
        actor="user",
        now=NOW,
    )
    loaded = store.get_agent_team(lifecycle.project_id, result.team.team_id)
    snapshot_and_roles = store.get_agent_team_context(lifecycle.project_id, result.team.team_id)
    work_items = store.list_agent_team_work_items(lifecycle.project_id, result.team.team_id)
    wakes = store.list_agent_team_wakes(lifecycle.project_id, result.team.team_id)
    events = store.list_agent_team_events(lifecycle.project_id, result.team.team_id)

    assert result.eligibility.eligible is True
    assert loaded == result.team
    assert snapshot_and_roles is not None
    assert snapshot_and_roles[0].base_context_hash == base_context.base_context_hash
    assert set(snapshot_and_roles[1]) == {"science", "safety", "completeness"}
    assert len(work_items) == 3 and {item.status for item in work_items} == {"PENDING"}
    assert all(not item.dependency_ids for item in work_items)
    assert len(wakes) == 3 and {wake.status for wake in wakes} == {"PENDING"}
    assert {wake.reason for wake in wakes} == {"work_item"}
    assert any(event.event_type == "TEAM_REQUESTED" for event in events)
    assert store.get_agent_planning_base_context(
        lifecycle.project_id, base_context.base_context_hash
    ) == base_context

    current_lifecycle = store.get_agent_lifecycle(lifecycle.lifecycle_id)
    replay = coordinator.create_team(
        lifecycle=current_lifecycle,
        base_context=base_context,
        eligibility_input=eligibility,
        command_id="create-team-1",
        actor="user",
        now=NOW,
    )
    assert replay.team == result.team
    assert len(store.list_agent_teams(lifecycle.project_id, lifecycle_id=lifecycle.lifecycle_id)) == 1


def test_coordinator_ineligible_request_creates_no_team(tmp_path) -> None:
    store, lifecycle, base_context, eligibility, coordinator = _setup(tmp_path)
    ineligible = eligibility.model_copy(update={
        "independent_evidence_domains": ("science",),
        "has_science_decision": False,
        "has_path_or_scope_risk": False,
        "has_competing_interpretations": False,
    })
    with pytest.raises(AgentTeamIneligibleError) as exc_info:
        coordinator.create_team(
            lifecycle=lifecycle,
            base_context=base_context,
            eligibility_input=ineligible,
            command_id="create-ineligible",
            actor="user",
            now=NOW,
        )
    assert exc_info.value.code == "MULTI_AGENT_EVIDENCE_DOMAINS_INSUFFICIENT"
    assert store.list_agent_teams(lifecycle.project_id) == []


def test_coordinator_rejects_non_openai_provider_before_team_creation(tmp_path) -> None:
    store, lifecycle, base_context, eligibility, coordinator = _setup(tmp_path)
    coordinator.model_config = AgentModelRuntimeConfig()
    with pytest.raises(AgentTeamCoordinatorError, match="AGENT_TEAM_PROVIDER_UNAVAILABLE"):
        coordinator.create_team(
            lifecycle=lifecycle,
            base_context=base_context,
            eligibility_input=eligibility,
            command_id="create-rule-based",
            actor="user",
            now=NOW,
        )
    assert store.list_agent_teams(lifecycle.project_id) == []
