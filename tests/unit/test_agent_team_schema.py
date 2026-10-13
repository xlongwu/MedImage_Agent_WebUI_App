from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from src.backend.app.schemas.agent_harness import (
    AgentHarnessContext,
    AgentHarnessContextSection,
    AgentHarnessContextSections,
    AgentPlanningBaseContext,
    AgentTeamRoleContext,
    agent_harness_context_v4_hash,
    agent_planning_base_context_hash,
    agent_team_role_context_hash,
)
from src.backend.app.schemas.agent_team import (
    AgentTeamAdvisory,
    AgentTeamBudget,
    AgentTeamBudgetSlice,
    AgentTeamContextSnapshot,
    AgentTeamEvent,
    AgentTeamFinding,
    AgentTeamModelCallRecord,
    AgentTeamRecord,
    AgentTeamWakeRecord,
    AgentTeamWorkItem,
    agent_team_advisory_hash,
    agent_team_context_snapshot_hash,
    agent_team_finding_hash,
    canonical_hash,
    team_transition_allowed,
    wake_transition_allowed,
    work_item_transition_allowed,
)
from src.backend.app.schemas.planning import PlanningRequest

NOW = datetime(2026, 10, 13, tzinfo=UTC)
HASH = "a" * 64


def _sections() -> AgentHarnessContextSections:
    return AgentHarnessContextSections(
        **{
            name: AgentHarnessContextSection(source_hash=HASH)
            for name in AgentHarnessContextSections.model_fields
        }
    )


def _base_context() -> AgentPlanningBaseContext:
    sections = _sections()
    payload = {
        "schema_version": 1,
        "planning_epoch": 0,
        "lifecycle_id": "lifecycle-1",
        "project_id": "project-1",
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
    digest = agent_planning_base_context_hash(payload)
    return AgentPlanningBaseContext(
        base_context_hash=digest,
        **{key: value for key, value in payload.items() if key != "schema_version"},
    )


def _advisory_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "advisory_id": "advisory-1",
        "team_id": "team-1",
        "team_epoch": 1,
        "project_id": "project-1",
        "lifecycle_id": "lifecycle-1",
        "base_context_hash": HASH,
        "context_snapshot_hash": HASH,
        "context_projector_hash": HASH,
        "role_registry_hash": HASH,
        "aggregation_policy_hash": HASH,
        "model_profile_hash": HASH,
        "consent_epoch": 1,
        "consent_hash": HASH,
        "policy_version": "policy-v1",
        "redaction_policy_version": "redaction-v1",
        "accepted_finding_ids": (),
        "rejected_finding_ids": (),
        "blocking_codes": (),
        "conflict_codes": (),
        "coverage_by_role": {"science": 1, "safety": 1, "completeness": 1},
        "safety_reviewer_present": True,
        "decision": "accepted",
    }


def _advisory() -> AgentTeamAdvisory:
    payload = _advisory_payload()
    digest = agent_team_advisory_hash(payload)
    return AgentTeamAdvisory(
        advisory_hash=digest,
        **{key: value for key, value in payload.items() if key != "schema_version"},
    )


def _team_v4_context(advisory: AgentTeamAdvisory | None) -> AgentHarnessContext:
    sections = _sections()
    team_fields: dict[str, object]
    if advisory is None:
        team_fields = {
            "planning_mode": "single_agent",
            "team_id": None,
            "team_epoch": None,
            "team_advisory": None,
            "included_sections": ("goal",),
            "section_hashes": {"goal": canonical_hash(sections.goal)},
        }
    else:
        team_fields = {
            "planning_mode": "multi_agent",
            "team_id": advisory.team_id,
            "team_epoch": advisory.team_epoch,
            "team_advisory": advisory,
            "included_sections": ("goal", "team_advisory"),
            "section_hashes": {
                "goal": canonical_hash(sections.goal),
                "team_advisory": advisory.advisory_hash,
            },
        }
    payload: dict[str, object] = {
        "base_context_hash": advisory.base_context_hash
        if advisory
        else _base_context().base_context_hash,
        "planning_epoch": 0,
        "lifecycle_id": "lifecycle-1",
        "project_id": "project-1",
        "purpose": "plan_draft",
        "sections": sections,
        "required_sections": ("goal",),
        "omitted_sections": (),
        "evidence_refs": (),
        "evidence_snapshot_hash": HASH,
        "projection_policy_version": "projection-v1",
        "complete": True,
        "incomplete_reason": None,
        "memory_context_hash": None,
        "project_snapshot_hash": HASH,
        "policy_version": "policy-v1",
        "redaction_policy_version": "redaction-v1",
        "prompt_template_version": "prompt-v1",
        "skill_refs": (),
        "skill_error_codes": (),
        **team_fields,
    }
    unhashed = AgentHarnessContext.model_construct(context_hash=HASH, **payload)
    return AgentHarnessContext(context_hash=agent_harness_context_v4_hash(unhashed), **payload)


def test_canonical_hash_tracks_payload_and_ignores_mapping_order() -> None:
    assert canonical_hash({"a": 1, "b": {"x": 2}}) == canonical_hash({"b": {"x": 2}, "a": 1})
    assert canonical_hash({"value": 1}) != canonical_hash({"value": 2})


def test_base_v1_hash_and_fixed_sections_are_enforced() -> None:
    context = _base_context()
    assert context.base_context_hash == agent_planning_base_context_hash(context)
    with pytest.raises(ValidationError, match="AGENT_PLANNING_BASE_HASH_MISMATCH"):
        AgentPlanningBaseContext(
            **{
                **context.model_dump(),
                "base_context_hash": "b" * 64,
            }
        )


def test_harness_v4_binds_advisory_and_exposes_typed_model_payload() -> None:
    single = _team_v4_context(None)
    assert single.schema_version == 4
    assert single.prompt_payload()["team_advisory"] is None

    multi = _team_v4_context(_advisory())
    assert multi.team_id == "team-1"
    assert (
        multi.prompt_payload()["team_advisory"]["advisory_hash"]
        == multi.team_advisory.advisory_hash
    )
    with pytest.raises(ValidationError, match="AGENT_HARNESS_CONTEXT_HASH_MISMATCH"):
        AgentHarnessContext(
            **{
                **multi.model_dump(mode="python"),
                "context_hash": "b" * 64,
            }
        )


def test_role_context_is_purpose_scoped_and_hashes_its_projected_sections() -> None:
    section = AgentHarnessContextSection(source_hash=HASH, data={"goal": "review"})
    budget = AgentTeamBudgetSlice(
        reserved_input_tokens=500,
        deadline_at=NOW + timedelta(minutes=1),
    )
    payload: dict[str, object] = {
        "schema_version": 1,
        "team_id": "team-1",
        "team_epoch": 1,
        "worker_id": "worker-safety",
        "role_id": "safety_reviewer.v1",
        "reviewer_kind": "safety",
        "project_id": "project-1",
        "lifecycle_id": "lifecycle-1",
        "base_context_hash": HASH,
        "purpose": "project_safety",
        "included_sections": ("goal",),
        "omitted_sections": tuple(
            name for name in AgentHarnessContextSections.model_fields if name != "goal"
        ),
        "sections": {"goal": section.model_dump(mode="json")},
        "section_hashes": {"goal": canonical_hash(section)},
        "evidence_refs": (),
        "model_profile_hash": HASH,
        "prompt_template_version": "safety-review-prompt-v1",
        "output_schema_version": "agent-review-finding-v1",
        "policy_version": "policy-v1",
        "redaction_policy_version": "redaction-v1",
        "consent_epoch": 1,
        "consent_hash": HASH,
        "budget_slice": budget.model_dump(mode="json"),
        "deadline_at": (NOW + timedelta(minutes=1)).isoformat().replace("+00:00", "Z"),
    }
    role_context = AgentTeamRoleContext(
        role_context_hash=agent_team_role_context_hash(payload),
        **{key: value for key, value in payload.items() if key != "schema_version"},
    )
    assert role_context.role_context_hash == agent_team_role_context_hash(role_context)
    with pytest.raises(ValidationError, match="TEAM_ROLE_CONTEXT_PURPOSE_INVALID"):
        AgentTeamRoleContext(
            **{
                **role_context.model_dump(),
                "purpose": "science_prerequisites",
            }
        )


def test_team_and_work_item_contracts_are_frozen_and_reject_bad_bindings() -> None:
    team = AgentTeamRecord(
        team_id="team-1",
        team_epoch=1,
        project_id="project-1",
        lifecycle_id="lifecycle-1",
        snapshot_hash=HASH,
        role_registry_hash=HASH,
        context_projector_hash=HASH,
        aggregation_policy_hash=HASH,
        model_profile_hash=HASH,
        global_policy_hash=HASH,
        consent_epoch=0,
        consent_hash=HASH,
        budget=AgentTeamBudget(
            max_model_calls=6,
            max_repairs=3,
            reserved_input_tokens=1000,
            deadline_at=NOW + timedelta(minutes=3),
        ),
    )
    with pytest.raises(ValidationError):
        team.status = "FAILED"
    with pytest.raises(ValidationError, match="TEAM_COMPLETED_WITHOUT_ADVISORY"):
        AgentTeamRecord(
            **{
                **team.model_dump(),
                "status": "COMPLETED",
            }
        )

    item = AgentTeamWorkItem(
        work_item_id="work-1",
        team_id="team-1",
        team_epoch=1,
        project_id="project-1",
        lifecycle_id="lifecycle-1",
        reviewer_kind="safety",
        role_id="safety_reviewer.v1",
        available_at=NOW,
        deadline_at=NOW + timedelta(minutes=1),
    )
    assert item.status == "PENDING"
    with pytest.raises(ValidationError, match="MULTI_AGENT_REVIEWER_KIND_MISMATCH"):
        AgentTeamWorkItem(
            **{
                **item.model_dump(),
                "role_id": "science_reviewer.v1",
            }
        )


def test_context_call_finding_event_and_wake_records_validate_bindings() -> None:
    snapshot_payload: dict[str, object] = {
        "schema_version": 1,
        "team_id": "team-1",
        "team_epoch": 1,
        "project_id": "project-1",
        "lifecycle_id": "lifecycle-1",
        "base_context_hash": HASH,
        "role_context_hashes": {"science": HASH, "safety": HASH, "completeness": HASH},
        "consent_epoch": 0,
        "consent_hash": HASH,
        "role_registry_hash": HASH,
        "context_projector_hash": HASH,
        "policy_version": "policy-v1",
        "redaction_policy_version": "redaction-v1",
        "prompt_template_version": "prompt-v1",
        "output_schema_version": "finding-v1",
    }
    context = AgentTeamContextSnapshot(
        snapshot_hash=agent_team_context_snapshot_hash(snapshot_payload), **snapshot_payload
    )
    assert len(context.role_context_hashes) == 3
    with pytest.raises(ValidationError, match="TEAM_ROLE_CONTEXT_SET_INVALID"):
        AgentTeamContextSnapshot(
            **{
                **context.model_dump(),
                "role_context_hashes": {"safety": HASH},
            }
        )

    call = AgentTeamModelCallRecord(
        model_call_id="call-1",
        team_id="team-1",
        team_epoch=1,
        project_id="project-1",
        lifecycle_id="lifecycle-1",
        work_item_id="work-1",
        reviewer_kind="safety",
        role_id="safety_reviewer.v1",
        role_registry_hash=HASH,
        role_context_hash=HASH,
        model_profile_hash=HASH,
        request_hash=HASH,
        request_bytes=100,
        provider_id="fake",
        model_id="fake-model",
        endpoint_class="offline-test",
        prompt_template_version="p1",
        output_schema_version="finding-v1",
        consent_epoch=0,
        consent_hash=HASH,
        started_at=NOW,
    )
    assert call.status == "started" and not call.network_called
    with pytest.raises(ValidationError, match="TEAM_MODEL_CALL_RESPONSE_BINDING_INVALID"):
        AgentTeamModelCallRecord(
            **{
                **call.model_dump(),
                "status": "completed",
                "completed_at": NOW,
            }
        )

    finding_payload = {
        "schema_version": 1,
        "reviewer_kind": "safety",
        "severity": "warning",
        "code": "UNSAFE_WRITE_ROOT",
        "message_key": "agent.finding.unsafe_write_root",
        "input_refs": ("policy:project-write-root",),
        "suggested_change": None,
        "finding_id": "finding-1",
        "team_id": "team-1",
        "team_epoch": 1,
        "project_id": "project-1",
        "lifecycle_id": "lifecycle-1",
        "work_item_id": "work-1",
        "role_id": "safety_reviewer.v1",
        "role_context_hash": HASH,
        "model_call_id": "call-1",
        "model_response_hash": HASH,
        "created_at": NOW.isoformat(),
    }
    finding_hash = agent_team_finding_hash(finding_payload)
    finding = AgentTeamFinding(
        finding_hash=finding_hash,
        **{key: value for key, value in finding_payload.items() if key != "schema_version"},
    )
    assert finding.finding_hash == agent_team_finding_hash(finding)
    with pytest.raises(ValidationError, match="TEAM_FINDING_HASH_MISMATCH"):
        AgentTeamFinding(
            **{
                **finding.model_dump(),
                "finding_hash": "b" * 64,
            }
        )

    event = AgentTeamEvent(
        event_id="event-1",
        team_id="team-1",
        team_epoch=1,
        project_id="project-1",
        lifecycle_id="lifecycle-1",
        command_id="cmd-1",
        idempotency_key="event-key",
        source="worker",
        event_type="FINDING_ACCEPTED",
        payload_hash=HASH,
        occurred_at=NOW,
    )
    assert event.payload_hash == HASH
    wake = AgentTeamWakeRecord(
        wake_id="wake-1",
        team_id="team-1",
        team_epoch=1,
        project_id="project-1",
        lifecycle_id="lifecycle-1",
        step_key="role:safety:attempt:1",
        reason="work_item",
        available_at=NOW,
    )
    assert wake.status == "PENDING"


def test_advisory_hash_safety_gate_and_transition_graphs() -> None:
    payload = _advisory_payload()
    digest = agent_team_advisory_hash(payload)
    advisory = AgentTeamAdvisory(
        advisory_hash=digest,
        **{key: value for key, value in payload.items() if key != "schema_version"},
    )
    assert agent_team_advisory_hash(advisory) == digest
    with pytest.raises(ValidationError, match="MULTI_AGENT_REVIEW_SAFETY_REQUIRED"):
        AgentTeamAdvisory(
            **{
                **advisory.model_dump(),
                "safety_reviewer_present": False,
            }
        )

    assert team_transition_allowed("READY", "RUNNING")
    assert team_transition_allowed("AGGREGATING", "COMPLETED")
    assert not team_transition_allowed("COMPLETED", "RUNNING")
    assert work_item_transition_allowed("VALIDATING", "RETRY_READY")
    assert not work_item_transition_allowed("COMPLETED", "CLAIMED")
    assert wake_transition_allowed("CLAIMED", "RETRY")
    assert not wake_transition_allowed("CONSUMED", "CLAIMED")


def test_planning_request_binds_team_advisory_and_sends_only_typed_findings() -> None:
    finding_payload = {
        "schema_version": 1,
        "reviewer_kind": "science",
        "severity": "warning",
        "code": "DATASET_COVERAGE_GAP",
        "message_key": "agent.finding.dataset_coverage_gap",
        "input_refs": ("dataset:summary",),
        "suggested_change": "Review dataset coverage before finalizing the plan.",
        "finding_id": "finding-accepted",
        "team_id": "team-1",
        "team_epoch": 1,
        "project_id": "project-1",
        "lifecycle_id": "lifecycle-1",
        "work_item_id": "work-1",
        "role_id": "science_reviewer.v1",
        "role_context_hash": HASH,
        "model_call_id": "call-1",
        "model_response_hash": HASH,
        "created_at": NOW.isoformat(),
    }
    finding = AgentTeamFinding(
        finding_hash=agent_team_finding_hash(finding_payload),
        **{key: value for key, value in finding_payload.items() if key != "schema_version"},
    )
    advisory_payload = {
        **_advisory_payload(),
        "accepted_finding_ids": (finding.finding_id,),
    }
    advisory = AgentTeamAdvisory(
        advisory_hash=agent_team_advisory_hash(advisory_payload),
        **{key: value for key, value in advisory_payload.items() if key != "schema_version"},
    )
    common = {
        "project_id": "project-1",
        "lifecycle_id": "lifecycle-1",
        "goal": "Plan ALFF",
        "project_config_path": "project.yaml",
        "evidence_snapshot_hash": HASH,
        "revision_reason": "initial",
        "provider_ref": "provider",
        "prompt_version": "prompt-v1",
        "model_profile_hash": HASH,
    }
    request = PlanningRequest(
        **common,
        planning_mode="multi_agent",
        team_id=advisory.team_id,
        team_epoch=advisory.team_epoch,
        base_context_hash=advisory.base_context_hash,
        harness_context_hash=HASH,
        advisory_ref=advisory.advisory_id,
        advisory_hash=advisory.advisory_hash,
        context_snapshot_hash=advisory.context_snapshot_hash,
        context_projector_hash=advisory.context_projector_hash,
        role_registry_hash=advisory.role_registry_hash,
        aggregation_policy_hash=advisory.aggregation_policy_hash,
        team_model_profile_hash=advisory.model_profile_hash,
        global_policy_hash=HASH,
        consent_epoch=advisory.consent_epoch,
        consent_hash=advisory.consent_hash,
        team_advisory=advisory,
        team_findings=(finding,),
    )

    team_review = request.planner_constraints()["team_review"]
    assert request.identity_payload()["advisory_hash"] == advisory.advisory_hash
    assert team_review["accepted_findings"] == [{
        "reviewer_kind": "science",
        "severity": "warning",
        "code": "DATASET_COVERAGE_GAP",
        "message_key": "agent.finding.dataset_coverage_gap",
        "input_refs": ["dataset:summary"],
    }]
    assert "suggested_change" not in team_review["accepted_findings"][0]

    with pytest.raises(ValidationError, match="PLANNING_REQUEST_TEAM_BINDING_INVALID"):
        PlanningRequest(**{
            **request.model_dump(),
            "base_context_hash": "b" * 64,
        })

    single = PlanningRequest(**common)
    assert "planning_mode" not in single.identity_payload()
    assert "team_id" not in single.identity_payload()
