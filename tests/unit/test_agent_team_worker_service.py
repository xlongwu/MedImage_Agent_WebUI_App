from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from src.backend.app.core.config_schema import (
    AgentModelRuntimeConfig,
    AgentMultiAgentConfig,
    multi_agent_policy_hash,
)
from src.backend.app.planner.agent_model_adapter import canonical_request_bytes
from src.backend.app.planner.llm_provider import LLMProviderResult
from src.backend.app.schemas.agent_harness import (
    AgentHarnessContextSection,
    AgentHarnessContextSections,
    AgentPlanningBaseContext,
    agent_planning_base_context_hash,
)
from src.backend.app.schemas.agent_team import (
    AgentTeamBudget,
    AgentTeamBudgetSlice,
    AgentTeamRecord,
    AgentTeamWorkItem,
    canonical_hash,
)
from src.backend.app.schemas.project_agent_settings import ProjectMultiAgentConsent
from src.backend.app.services.agent_review_context_projector import context_projector_hash
from src.backend.app.services.agent_review_finding_aggregator import aggregation_policy_hash
from src.backend.app.services.agent_review_role_registry import role_for, role_registry_hash
from src.backend.app.services.agent_team_context_service import AgentTeamContextService
from src.backend.app.services.agent_team_worker_service import (
    AgentTeamWorkerError,
    AgentTeamWorkerService,
)
from src.backend.app.services.structured_agent_model_adapter import (
    StructuredAgentModelAdapter,
    team_review_model_profile_hash,
)

NOW = datetime(2026, 10, 13, tzinfo=UTC)
HASH = "a" * 64


class _Provider:
    def __init__(self, findings: list[dict[str, object]]) -> None:
        self.findings = findings

    def complete(self, *, request) -> LLMProviderResult:
        return LLMProviderResult(
            ok=True,
            content=json.dumps(self.findings),
            model=request.model,
            input_tokens=80,
            output_tokens=24,
            cached_input_tokens=8,
            latency_ms=17,
            provider_request_id="team-request-1",
            network_called=True,
        )


def _fixture(findings: list[dict[str, object]]):
    multi_config = AgentMultiAgentConfig(enabled=True, max_input_tokens=6000)
    model_config = AgentModelRuntimeConfig(
        enabled=True,
        provider="openai_compatible",
        model="review-model",
        base_url="https://models.example.test/v1",
        api_key="test-key",
    )
    consent = ProjectMultiAgentConsent.build(
        project_id="project-1",
        enabled=True,
        consent_epoch=3,
        max_model_calls=6,
        max_input_tokens=6000,
        updated_at=NOW,
    )
    data = {
        "goal": {"goal": "Review this research plan", "scope": "planning only"},
        "policy": {"rawdata_read_only": True, "approved_write_roots": ["project-output"]},
        "project_evidence": {"facts": [{"key": "evidence", "value": "available"}]},
        "decision_state": {"unresolved_gaps": []},
        "plan_state": {"node_count": 0},
        "execution_state": {"status": "not_started"},
        "latest_observation": {"status": "none"},
        "last_action_result": {"status": "none"},
        "memory_context": {"memory_ids": []},
        "budget": {"model_calls_used": 0},
    }
    sections = AgentHarnessContextSections(
        **{
            name: AgentHarnessContextSection(source_hash=HASH, data=data[name])
            for name in AgentHarnessContextSections.model_fields
        }
    )
    base_payload = {
        "schema_version": 1,
        "planning_epoch": 1,
        "lifecycle_id": "life-1",
        "project_id": "project-1",
        "sections": sections.model_dump(mode="json"),
        "section_hashes": {
            name: canonical_hash(getattr(sections, name))
            for name in AgentHarnessContextSections.model_fields
        },
        "evidence_refs": ({"type": "project", "record_id": "evidence-1", "hash": HASH},),
        "evidence_snapshot_hash": HASH,
        "projection_policy_version": "projector-v1",
        "complete": True,
        "incomplete_reason": None,
        "project_snapshot_hash": HASH,
        "policy_version": "policy-v1",
        "redaction_policy_version": "redaction-v1",
    }
    base = AgentPlanningBaseContext(
        base_context_hash=agent_planning_base_context_hash(base_payload),
        **{key: value for key, value in base_payload.items() if key != "schema_version"},
    )
    model_profile_hash = team_review_model_profile_hash(
        config=model_config,
        max_output_tokens=multi_config.max_output_tokens_per_call,
    )
    deadline = NOW + timedelta(minutes=2)
    slices = {
        kind: AgentTeamBudgetSlice(
            max_model_calls=2,
            reserved_input_tokens=2000,
            deadline_at=deadline,
        )
        for kind in ("science", "safety", "completeness")
    }
    snapshot, role_contexts = AgentTeamContextService().project_snapshot(
        base_context=base,
        team_id="team-1",
        team_epoch=1,
        project_id="project-1",
        lifecycle_id="life-1",
        model_profile_hash=model_profile_hash,
        consent=consent,
        budget_slices=slices,
    )
    team = AgentTeamRecord(
        team_id="team-1",
        team_epoch=1,
        project_id="project-1",
        lifecycle_id="life-1",
        snapshot_hash=snapshot.snapshot_hash,
        role_registry_hash=role_registry_hash(),
        context_projector_hash=context_projector_hash(),
        aggregation_policy_hash=aggregation_policy_hash(),
        model_profile_hash=model_profile_hash,
        global_policy_hash=multi_agent_policy_hash(multi_config),
        consent_epoch=consent.consent_epoch,
        consent_hash=consent.consent_hash,
        budget=AgentTeamBudget(
            max_model_calls=6,
            max_repairs=3,
            reserved_input_tokens=6000,
            deadline_at=NOW + timedelta(minutes=3),
        ),
        created_at=NOW,
        updated_at=NOW,
    )
    role = role_for("science")
    work_item = AgentTeamWorkItem(
        work_item_id="work-1",
        team_id=team.team_id,
        team_epoch=team.team_epoch,
        project_id=team.project_id,
        lifecycle_id=team.lifecycle_id,
        reviewer_kind="science",
        role_id=role.role_id,
        available_at=NOW,
        deadline_at=deadline,
        created_at=NOW,
        updated_at=NOW,
    )
    worker = AgentTeamWorkerService(
        multi_agent_config=multi_config,
        model_config=model_config,
        adapter=StructuredAgentModelAdapter(_Provider(findings)),
    )
    return worker, team, work_item, snapshot, role_contexts["science"], consent


def _finding(*, code: str = "SCIENCE_EVIDENCE_GAP", ref: str = "section:goal"):
    return {
        "reviewer_kind": "science",
        "severity": "warning",
        "code": code,
        "message_key": "review.science.evidence_gap",
        "input_refs": [ref],
    }


def test_worker_prepares_bound_request_and_accepts_allowlisted_findings() -> None:
    worker, team, work_item, snapshot, role_context, consent = _fixture([_finding()])
    prepared = worker.prepare_review(
        team=team,
        work_item=work_item,
        context_snapshot=snapshot,
        role_context=role_context,
        consent=consent,
        now=NOW,
    )
    result = worker.run_review(prepared)

    assert result.findings[0].code == "SCIENCE_EVIDENCE_GAP"
    assert result.request_hash == prepared.request_hash
    assert prepared.request_hash != HASH
    assert prepared.request_bytes == len(canonical_request_bytes(prepared.request))
    assert prepared.request.context_payload["role_registry_hash"] == team.role_registry_hash
    assert prepared.request.context_payload["consent_hash"] == consent.consent_hash
    assert prepared.request.action_schema["items"]["properties"]["code"]["enum"] == list(
        role_for("science").allowed_finding_codes
    )
    assert result.metadata.network_called is True
    assert result.metadata.cached_input_tokens == 8


@pytest.mark.parametrize(
    ("finding", "expected"),
    [
        (_finding(code="MODEL_INVENTED_CODE"), "MULTI_AGENT_REVIEW_FINDING_CODE_UNKNOWN"),
        (_finding(ref="section:policy"), "MULTI_AGENT_REVIEW_INPUT_REF_INVALID"),
    ],
)
def test_worker_rejects_unregistered_codes_and_foreign_refs(finding, expected) -> None:
    worker, team, work_item, snapshot, role_context, consent = _fixture([finding])
    prepared = worker.prepare_review(
        team=team,
        work_item=work_item,
        context_snapshot=snapshot,
        role_context=role_context,
        consent=consent,
        now=NOW,
    )
    with pytest.raises(AgentTeamWorkerError) as exc_info:
        worker.run_review(prepared)
    assert exc_info.value.code == expected
    assert exc_info.value.prepared is prepared
    assert exc_info.value.metadata is not None


def test_worker_rejects_revoked_consent_before_request_creation() -> None:
    worker, team, work_item, snapshot, role_context, consent = _fixture([])
    revoked = consent.model_copy(update={"enabled": False})
    with pytest.raises(AgentTeamWorkerError) as exc_info:
        worker.prepare_review(
            team=team,
            work_item=work_item,
            context_snapshot=snapshot,
            role_context=role_context,
            consent=revoked,
            now=NOW,
        )
    assert exc_info.value.code == "MULTI_AGENT_CONSENT_INVALIDATED"


def test_worker_rejects_global_policy_drift_before_request_creation() -> None:
    worker, team, work_item, snapshot, role_context, consent = _fixture([])
    changed_team = team.model_copy(update={"global_policy_hash": "b" * 64})
    with pytest.raises(AgentTeamWorkerError) as exc_info:
        worker.prepare_review(
            team=changed_team,
            work_item=work_item,
            context_snapshot=snapshot,
            role_context=role_context,
            consent=consent,
            now=NOW,
        )
    assert exc_info.value.code == "AGENT_TEAM_CONTEXT_BINDING_INVALID"


def test_worker_binds_single_repair_to_the_failed_call() -> None:
    worker, team, work_item, snapshot, role_context, consent = _fixture([_finding()])
    retry_item = work_item.model_copy(update={
        "attempt_no": 2,
        "status": "RETRY_READY",
        "model_call_id": "call-1",
    })
    prepared = worker.prepare_review(
        team=team,
        work_item=retry_item,
        context_snapshot=snapshot,
        role_context=role_context,
        consent=consent,
        repair_of_model_call_id="call-1",
        now=NOW,
    )
    assert prepared.request.repair is True
    assert prepared.repair_of_model_call_id == "call-1"
    assert prepared.request.context_payload["repair_instruction"].startswith("Return one corrected")
