from __future__ import annotations

from src.backend.app.schemas.agent_lifecycle import AgentLifecycleRecord
from src.backend.app.schemas.desktop import ProjectDetail
from src.backend.app.services.agent_harness_context_service import (
    AgentContextLimitExceededError,
    HarnessContextBuilder,
    HarnessContextSources,
)
from src.backend.app.planner.agent_model_adapter import (
    DefaultAgentModelAdapter,
    build_action_prompt,
    serialize_context_v2,
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


def test_context_is_allowlisted_redacted_and_stably_hashed() -> None:
    builder = HarnessContextBuilder()
    lifecycle = _lifecycle({"science_answers": {"atlas": "aal"}, "memory_context": {
        "token": "secret", "memory_ids": ["m1"],
        "planner_constraints": {"note": "Never expose C:/private/rawdata/sub-001 or provider credentials."},
    }})
    project = _project({"subject_count": 1, "api_key": "secret", "rawdata_dir": "C:/private/rawdata"})

    first = builder.build(sources=HarnessContextSources(lifecycle=lifecycle, project=project))
    second = builder.build(sources=HarnessContextSources(lifecycle=lifecycle, project=project))

    assert first.context_hash == second.context_hash
    assert first.schema_version == 2
    assert tuple(type(first.sections).model_fields) == builder.SECTION_ORDER
    assert set(first.section_hashes) == set(builder.SECTION_ORDER)
    rendered = str(first.model_dump())
    assert "secret" not in rendered
    assert "rawdata" not in rendered.casefold()
    assert "C:/private" not in rendered
    assert first.sections.decision_state.data["confirmed_answers"] == {"atlas": "aal"}
    assert first.prompt_payload()["sections"]["goal"]["data"]["lifecycle_state"] == "CREATED"


def test_context_truncates_to_published_32kib_limit() -> None:
    builder = HarnessContextBuilder()
    lifecycle = _lifecycle({"memory_context": {
        "memory_ids": ["m1"],
        "decision_suggestions": [{"summary": "x" * 2048} for _ in range(24)],
        "planner_constraints": {f"constraint-{index}": "x" * 2048 for index in range(24)},
    }})
    context = builder.build(
        sources=HarnessContextSources(lifecycle=lifecycle, project=_project({}))
    )

    assert len(__import__("json").dumps(context.prompt_payload()).encode()) <= builder.MAX_BYTES
    assert "memory_context" in context.omitted_fields


def test_dynamic_section_or_policy_change_invalidates_context_hash() -> None:
    builder = HarnessContextBuilder()
    lifecycle = _lifecycle({"science_answers": {"atlas": "aal"}})
    project = _project({})
    first = builder.build(sources=HarnessContextSources(lifecycle=lifecycle, project=project))
    changed_answers = builder.build(sources=HarnessContextSources(
        lifecycle=_lifecycle({"science_answers": {"atlas": "schaefer-200"}}), project=project,
    ))
    changed_policy = builder.build(sources=HarnessContextSources(
        lifecycle=lifecycle, project=project, policy_version="agent-harness-policy-v3",
    ))

    assert first.context_hash != changed_answers.context_hash
    assert first.context_hash != changed_policy.context_hash


def test_prompt_or_skill_version_invalidates_context_hash_without_reordering_sections() -> None:
    builder = HarnessContextBuilder()
    lifecycle = _lifecycle()
    project = _project({})
    first = builder.build(sources=HarnessContextSources(lifecycle=lifecycle, project=project))
    changed = builder.build(sources=HarnessContextSources(
        lifecycle=lifecycle, project=project, prompt_template_version="agent-harness-prompt-v3",
        skill_refs=("planning_evidence_review.v1",),
    ))

    assert first.context_hash != changed.context_hash
    assert tuple(changed.prompt_payload()["sections"]) == builder.SECTION_ORDER


def test_required_context_that_cannot_fit_stops_before_provider() -> None:
    builder = HarnessContextBuilder()
    builder.MAX_BYTES = 32

    try:
        builder.build(sources=HarnessContextSources(lifecycle=_lifecycle(), project=_project({})))
    except AgentContextLimitExceededError as error:
        assert str(error) == "AGENT_CONTEXT_LIMIT_EXCEEDED"
    else:
        raise AssertionError("required Context v2 sections must not be string-truncated")


def test_adapter_serializes_context_v2_in_fixed_order_and_rejects_flat_v1() -> None:
    context = HarnessContextBuilder().build(
        sources=HarnessContextSources(lifecycle=_lifecycle(), project=_project({}))
    )
    payload = context.prompt_payload()
    payload["sections"] = dict(reversed(list(payload["sections"].items())))

    serialized = serialize_context_v2(payload)
    prompt = build_action_prompt(payload, repair=False)
    action = DefaultAgentModelAdapter().propose_action(snapshot=payload, provider_ref="rule_based")

    assert tuple(serialized["sections"]) == HarnessContextBuilder.SECTION_ORDER
    assert '"sections":{"goal"' in prompt
    assert action.envelope.expected_state == "CREATED"
    try:
        serialize_context_v2({"schema_version": 1, "lifecycle_state": "CREATED"})
    except ValueError as error:
        assert str(error) == "AGENT_CONTEXT_SCHEMA_INVALID"
    else:
        raise AssertionError("flat Context v1 must not be accepted")
