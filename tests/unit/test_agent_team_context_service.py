from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from src.backend.app.schemas.agent_harness import (
    AgentHarnessContextSection,
    AgentHarnessContextSections,
    AgentPlanningBaseContext,
    agent_planning_base_context_hash,
    agent_team_role_context_hash,
)
from src.backend.app.schemas.agent_team import (
    AgentTeamBudget,
    AgentTeamBudgetSlice,
    AgentTeamRecord,
    canonical_hash,
)
from src.backend.app.schemas.project_agent_settings import ProjectMultiAgentConsent
from src.backend.app.services.agent_review_context_projector import (
    AgentReviewContextProjector,
    context_projector_hash,
)
from src.backend.app.services.agent_review_role_registry import role_for, role_registry_hash
from src.backend.app.services.agent_team_context_service import AgentTeamContextService

NOW = datetime(2026, 10, 13, tzinfo=UTC)
HASH = "a" * 64
INCLUDED = {
    "science": ("goal", "project_evidence", "plan_state", "budget"),
    "safety": ("goal", "policy", "project_evidence", "execution_state"),
    "completeness": (
        "goal", "decision_state", "project_evidence", "plan_state", "last_action_result"
    ),
}


def _base_context(*, unsafe_policy: bool = False) -> AgentPlanningBaseContext:
    data = {
        "goal": {"goal": "Review the requested analysis", "scope": "planning only"},
        "policy": {
            "rawdata_read_only": True,
            "approved_write_roots": ["project-output"],
        },
        "project_evidence": {"facts": [{"key": "subject_count", "value": 4}]},
        "decision_state": {"unresolved_gaps": []},
        "plan_state": {"reviewed_plan_id": "", "node_count": 0},
        "execution_state": {"status": "not_started"},
        "latest_observation": None,
        "last_action_result": None,
        "memory_context": {"memory_ids": ["private-memory-ref"]},
        "budget": {"model_calls_used": 0},
    }
    if unsafe_policy:
        data["policy"] = {"note": "C:/private/rawdata/subject-01.nii.gz"}
    sections = AgentHarnessContextSections(
        **{
            name: AgentHarnessContextSection(source_hash=HASH, data=data[name] or {})
            for name in AgentHarnessContextSections.model_fields
        }
    )
    payload = {
        "schema_version": 1,
        "planning_epoch": 2,
        "lifecycle_id": "life-1",
        "project_id": "project-1",
        "sections": sections.model_dump(mode="json"),
        "section_hashes": {
            name: canonical_hash(getattr(sections, name))
            for name in AgentHarnessContextSections.model_fields
        },
        "evidence_refs": ({"type": "project", "record_id": "project-1", "hash": "project-hash"},),
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


def _inputs(*, unsafe_policy: bool = False):
    base = _base_context(unsafe_policy=unsafe_policy)
    consent = ProjectMultiAgentConsent.build(
        project_id="project-1",
        enabled=True,
        consent_epoch=1,
        max_model_calls=6,
        max_input_tokens=6000,
        updated_at=NOW,
    )
    team = AgentTeamRecord(
        team_id="team-1",
        team_epoch=1,
        project_id="project-1",
        lifecycle_id="life-1",
        snapshot_hash=HASH,
        role_registry_hash=role_registry_hash(),
        context_projector_hash=context_projector_hash(),
        aggregation_policy_hash=HASH,
        model_profile_hash=HASH,
        global_policy_hash=HASH,
        consent_epoch=consent.consent_epoch,
        consent_hash=consent.consent_hash,
        budget=AgentTeamBudget(
            max_model_calls=3,
            max_repairs=0,
            reserved_input_tokens=3000,
            deadline_at=NOW + timedelta(minutes=3),
        ),
        created_at=NOW,
        updated_at=NOW,
    )
    slices = {
        kind: AgentTeamBudgetSlice(
            max_model_calls=1,
            reserved_input_tokens=1000,
            deadline_at=NOW + timedelta(minutes=2),
        )
        for kind in INCLUDED
    }
    return base, team, consent, slices


def test_team_contexts_use_fixed_sections_registry_and_consent_binding() -> None:
    base, team, consent, slices = _inputs()
    service = AgentTeamContextService()
    roles = service.project_roles(
        base_context=base,
        team=team,
        consent=consent,
        budget_slices=slices,
    )

    assert set(roles) == set(INCLUDED)
    for kind, context in roles.items():
        assert context.role_id == role_for(kind).role_id
        assert context.purpose == role_for(kind).purpose
        assert context.included_sections == INCLUDED[kind]
        assert set(context.omitted_sections).isdisjoint(context.included_sections)
        assert set(context.included_sections) | set(context.omitted_sections) == set(
            AgentHarnessContextSections.model_fields
        )
        assert context.base_context_hash == base.base_context_hash
        assert context.consent_epoch == consent.consent_epoch
        assert context.consent_hash == consent.consent_hash
        assert context.model_profile_hash == team.model_profile_hash
        assert context.role_context_hash == agent_team_role_context_hash(context)
        assert "memory_context" in context.omitted_sections
        assert "private-memory-ref" not in str(context.model_dump(mode="json"))

    assert roles["science"].sections.keys() == set(INCLUDED["science"])
    assert roles["safety"].sections.keys() == set(INCLUDED["safety"])
    assert roles["completeness"].sections.keys() == set(INCLUDED["completeness"])
    assert context_projector_hash() == team.context_projector_hash
    snapshot, snapshot_roles = service.project_snapshot(
        base_context=base,
        team_id=team.team_id,
        team_epoch=team.team_epoch,
        project_id=team.project_id,
        lifecycle_id=team.lifecycle_id,
        model_profile_hash=team.model_profile_hash,
        consent=consent,
        budget_slices=slices,
    )
    assert snapshot.base_context_hash == base.base_context_hash
    assert snapshot.team_id == team.team_id
    assert snapshot.role_context_hashes == {
        kind: context.role_context_hash for kind, context in snapshot_roles.items()
    }
    assert {kind: context.role_context_hash for kind, context in roles.items()} == {
        kind: context.role_context_hash for kind, context in snapshot_roles.items()
    }
    assert snapshot.role_registry_hash == team.role_registry_hash
    assert snapshot.context_projector_hash == team.context_projector_hash


def test_projection_rejects_disabled_or_cross_project_consent() -> None:
    base, team, consent, slices = _inputs()
    disabled = ProjectMultiAgentConsent.default_for_project("project-1")
    with pytest.raises(ValueError, match="AGENT_TEAM_CONTEXT_BINDING_INVALID"):
        AgentTeamContextService().project_roles(
            base_context=base,
            team=team,
            consent=disabled,
            budget_slices=slices,
        )
    cross_project = ProjectMultiAgentConsent.build(
        project_id="project-2",
        enabled=True,
        consent_epoch=1,
        max_model_calls=6,
        max_input_tokens=6000,
        updated_at=NOW,
    )
    with pytest.raises(ValueError, match="AGENT_TEAM_CONTEXT_BINDING_INVALID"):
        AgentTeamContextService().project_roles(
            base_context=base,
            team=team,
            consent=cross_project,
            budget_slices=slices,
        )


def test_projection_rejects_sensitive_content_and_hashes_role_scope() -> None:
    base, team, consent, slices = _inputs(unsafe_policy=True)
    with pytest.raises(ValueError, match="MULTI_AGENT_REVIEW_CONTEXT_REDACTION_VIOLATION"):
        AgentTeamContextService().project_roles(
            base_context=base,
            team=team,
            consent=consent,
            budget_slices=slices,
        )

    safe_base, safe_team, safe_consent, safe_slices = _inputs()
    one = AgentReviewContextProjector().project_base_context(
        base_context=safe_base,
        team_id=safe_team.team_id,
        team_epoch=safe_team.team_epoch,
        reviewer_kind="science",
        consent=safe_consent,
        model_profile_hash=safe_team.model_profile_hash,
        budget_slice=safe_slices["science"],
        deadline_at=safe_slices["science"].deadline_at,
    )
    changed_budget = safe_slices["science"].model_copy(
        update={"reserved_input_tokens": safe_slices["science"].reserved_input_tokens - 1}
    )
    two = AgentReviewContextProjector().project_base_context(
        base_context=safe_base,
        team_id=safe_team.team_id,
        team_epoch=safe_team.team_epoch,
        reviewer_kind="science",
        consent=safe_consent,
        model_profile_hash=safe_team.model_profile_hash,
        budget_slice=changed_budget,
        deadline_at=changed_budget.deadline_at,
    )
    assert one.role_context_hash != two.role_context_hash
