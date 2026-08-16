from __future__ import annotations

from src.backend.app.agent_skills.schemas import SkillContextRef
from src.backend.app.planner.agent_model_adapter import (
    DefaultAgentModelAdapter,
    build_canonical_model_request,
    build_action_prompt,
    serialize_context_v3,
)
from src.backend.app.core.config_schema import AgentModelRuntimeConfig
from src.backend.app.schemas.agent_evidence import EvidenceFact, EvidenceSnapshot, EvidenceSourceRef
from src.backend.app.schemas.agent_lifecycle import AgentLifecycleRecord
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.agent_harness_context_service import (
    AgentContextLimitExceededError,
    HarnessContextBuilder,
    HarnessContextSources,
)


def _project(metadata: dict) -> ProjectDetail:
    return ProjectDetail(
        id="project-1", name="project", study_id="study", modality="rs-fMRI",
        created_date="today", subjects_count=1, current_pipeline_id="pipeline",
        sequences=[], scans_count=1, total_size="0", current_model_id="none", metadata=metadata,
    )


def _lifecycle(context: dict | None = None) -> AgentLifecycleRecord:
    return AgentLifecycleRecord(
        lifecycle_id="lifecycle-1", project_id="project-1", state="CREATED",
        goal_text="Create a plan", command_context=context or {},
    )


def _evidence() -> EvidenceSnapshot:
    ref = EvidenceSourceRef(source_type="project", source_id="project-1", source_hash="project-hash")
    return EvidenceSnapshot(
        snapshot_hash="evidence-hash", project_id="project-1", lifecycle_id="lifecycle-1",
        requested_types=("project", "dataset"),
        facts=(EvidenceFact(key="subject_count", value=1, source_refs=(ref,)),), source_refs=(ref,),
    )


def test_context_is_allowlisted_redacted_and_stably_hashed() -> None:
    builder = HarnessContextBuilder()
    lifecycle = _lifecycle({"science_answers": {"atlas": "aal"}, "memory_context": {
        "token": "secret", "memory_ids": ["m1"],
        "planner_constraints": {"note": "Never expose C:/private/rawdata/sub-001 or provider credentials."},
    }})
    project = _project({"subject_count": 1, "api_key": "secret", "rawdata_dir": "C:/private/rawdata"})

    first = builder.build(sources=HarnessContextSources(lifecycle=lifecycle, project=project, evidence_snapshot=_evidence()))
    second = builder.build(sources=HarnessContextSources(lifecycle=lifecycle, project=project, evidence_snapshot=_evidence()))

    assert first.context_hash == second.context_hash
    assert first.schema_version == 3
    assert first.complete is True
    assert first.purpose == "plan_draft"
    assert tuple(type(first.sections).model_fields) == builder.SECTION_ORDER
    assert set(first.section_hashes) == set(builder.SECTION_ORDER)
    rendered = str(first.model_dump())
    assert "secret" not in rendered
    assert "c:/private/rawdata" not in rendered.casefold()
    assert "C:/private" not in rendered
    assert first.sections.decision_state.data["confirmed_answers"] == {"atlas": "aal"}
    assert first.prompt_payload()["sections"]["goal"]["data"]["lifecycle_state"] == "CREATED"
    assert first.evidence_refs == ({"type": "project", "record_id": "project-1", "hash": "project-hash"},)


def test_purpose_selects_only_its_required_projection_and_plan_catalog() -> None:
    builder = HarnessContextBuilder()
    lifecycle = _lifecycle({"science_answers": {"atlas": "aal"}})
    decision = builder.build(sources=HarnessContextSources(
        lifecycle=lifecycle, project=_project({}), evidence_snapshot=_evidence(), purpose="decision_request",
    ))
    plan = builder.build(sources=HarnessContextSources(
        lifecycle=lifecycle, project=_project({}), evidence_snapshot=_evidence(), purpose="plan_draft",
    ))

    assert decision.complete and plan.complete
    assert decision.purpose == "decision_request" and plan.purpose == "plan_draft"
    assert "allowed_nodes" not in decision.sections.policy.data
    assert plan.sections.policy.data["allowed_nodes"]
    assert set(decision.required_sections).issubset(decision.included_sections)
    assert set(plan.required_sections).issubset(plan.included_sections)


def test_missing_required_section_is_marked_incomplete_and_not_serializable() -> None:
    context = HarnessContextBuilder().build(sources=HarnessContextSources(
        lifecycle=AgentLifecycleRecord(lifecycle_id="lifecycle-1", project_id="project-1"),
        project=_project({}), evidence_snapshot=_evidence(),
    ))

    assert context.complete is False
    assert context.incomplete_reason == "AGENT_CONTEXT_REQUIRED_SECTION_MISSING:goal"
    try:
        serialize_context_v3(context.prompt_payload())
    except ValueError as error:
        assert str(error) == "AGENT_CONTEXT_INCOMPLETE"
    else:
        raise AssertionError("incomplete Context must not be serializable for a provider")


def test_context_truncates_to_published_32kib_limit() -> None:
    builder = HarnessContextBuilder()
    lifecycle = _lifecycle({"memory_context": {
        "memory_ids": ["m1"],
        "decision_suggestions": [{"summary": "x" * 2048} for _ in range(24)],
        "planner_constraints": {f"constraint-{index}": "x" * 2048 for index in range(24)},
    }})
    context = builder.build(
        sources=HarnessContextSources(lifecycle=lifecycle, project=_project({}), evidence_snapshot=_evidence())
    )

    assert len(__import__("json").dumps(context.prompt_payload()).encode()) <= builder.MAX_BYTES
    assert any(item.startswith("memory_context:") for item in context.omitted_sections)


def test_dynamic_section_or_policy_change_invalidates_context_hash() -> None:
    builder = HarnessContextBuilder()
    lifecycle = _lifecycle({"science_answers": {"atlas": "aal"}})
    project = _project({})
    first = builder.build(sources=HarnessContextSources(lifecycle=lifecycle, project=project, evidence_snapshot=_evidence()))
    changed_answers = builder.build(sources=HarnessContextSources(
        lifecycle=_lifecycle({"science_answers": {"atlas": "schaefer-200"}}), project=project, evidence_snapshot=_evidence(),
    ))
    changed_policy = builder.build(sources=HarnessContextSources(
        lifecycle=lifecycle, project=project, evidence_snapshot=_evidence(), policy_version="agent-harness-policy-v4",
    ))

    assert first.context_hash != changed_answers.context_hash
    assert first.context_hash != changed_policy.context_hash


def test_evidence_memory_and_catalog_changes_invalidate_context_hash(monkeypatch) -> None:
    from src.backend.app.runtime.tool_catalog import ToolCatalogItem
    import src.backend.app.runtime.tool_catalog as tool_catalog

    builder = HarnessContextBuilder()
    lifecycle = _lifecycle({"memory_context": {"context_hash": "memory-one", "memory_ids": ["m1"]}})
    baseline = builder.build(sources=HarnessContextSources(
        lifecycle=lifecycle, project=_project({}), evidence_snapshot=_evidence(),
    ))
    changed_evidence = builder.build(sources=HarnessContextSources(
        lifecycle=lifecycle, project=_project({}),
        evidence_snapshot=_evidence().model_copy(update={"snapshot_hash": "evidence-two"}),
    ))
    changed_memory = builder.build(sources=HarnessContextSources(
        lifecycle=_lifecycle({"memory_context": {"context_hash": "memory-two", "memory_ids": ["m1"]}}),
        project=_project({}), evidence_snapshot=_evidence(),
    ))
    monkeypatch.setattr(tool_catalog, "build_tool_catalog", lambda: [ToolCatalogItem(
        id="changed-node", name="Changed", backend="python", parallel_level="project",
        description="safe", requires_approval=False, manual_required=False, risk_level="low",
    )])
    changed_catalog = builder.build(sources=HarnessContextSources(
        lifecycle=lifecycle, project=_project({}), evidence_snapshot=_evidence(),
    ))

    assert len({
        baseline.context_hash, changed_evidence.context_hash, changed_memory.context_hash,
        changed_catalog.context_hash,
    }) == 4


def test_prompt_or_skill_version_invalidates_context_hash_without_reordering_sections() -> None:
    builder = HarnessContextBuilder()
    lifecycle = _lifecycle()
    project = _project({})
    first = builder.build(sources=HarnessContextSources(lifecycle=lifecycle, project=project, evidence_snapshot=_evidence()))
    changed = builder.build(sources=HarnessContextSources(
        lifecycle=lifecycle, project=project, evidence_snapshot=_evidence(), prompt_template_version="agent-harness-prompt-v4",
        skill_refs=(SkillContextRef(
            skill_id="planning_evidence_review.v1", version="v1",
            content_hash="a" * 64, sections=("goal",),
        ),),
    ))

    assert first.context_hash != changed.context_hash
    assert tuple(changed.prompt_payload()["sections"]) == tuple(
        name for name in builder.SECTION_ORDER if name in changed.included_sections
    )


def test_skill_prompt_receives_only_manifest_allowed_context_sections() -> None:
    context = HarnessContextBuilder().build(sources=HarnessContextSources(
        lifecycle=_lifecycle(), project=_project({}), evidence_snapshot=_evidence(),
        skill_refs=(SkillContextRef(
            skill_id="planning_evidence_review.v1", version="v1",
            content_hash="a" * 64, sections=("goal", "policy"),
        ),),
    ))

    serialized = serialize_context_v3(context.prompt_payload())

    assert tuple(serialized["sections"]) == ("goal", "policy")


def test_required_context_that_cannot_fit_stops_before_provider() -> None:
    builder = HarnessContextBuilder()
    builder.MAX_BYTES = 32

    try:
        builder.build(sources=HarnessContextSources(lifecycle=_lifecycle(), project=_project({}), evidence_snapshot=_evidence()))
    except AgentContextLimitExceededError as error:
        assert str(error) == "AGENT_CONTEXT_REQUIRED_SECTION_TOO_LARGE"
    else:
        raise AssertionError("required Context v3 sections must not be string-truncated")


def test_adapter_serializes_context_v2_in_fixed_order_and_rejects_flat_v1() -> None:
    context = HarnessContextBuilder().build(
        sources=HarnessContextSources(lifecycle=_lifecycle(), project=_project({}), evidence_snapshot=_evidence())
    )
    payload = context.prompt_payload()
    payload["sections"] = dict(reversed(list(payload["sections"].items())))

    serialized = serialize_context_v3(payload)
    request = build_canonical_model_request(
        snapshot=payload, config=AgentModelRuntimeConfig(), repair=False
    )
    prompt = build_action_prompt(request)
    action = DefaultAgentModelAdapter(config=AgentModelRuntimeConfig()).propose_action(request=request)

    assert tuple(serialized["sections"]) == tuple(name for name in HarnessContextBuilder.SECTION_ORDER if name in context.included_sections)
    assert '"safe_context"' in prompt
    assert action.envelope.expected_state == "CREATED"
    try:
        serialize_context_v3({"schema_version": 2, "lifecycle_state": "CREATED"})
    except ValueError as error:
        assert str(error) == "AGENT_CONTEXT_SCHEMA_INVALID"
    else:
        raise AssertionError("flat Context v1 must not be accepted")
