from __future__ import annotations

from src.backend.app.planner.llm_planner import generate_plan_from_goal


def _constraints(*, t1_ids: list[str]) -> dict[str, object]:
    return {
        "project_context": {
            "project_id": "project-1",
            "diagnostics": {
                "project_dir": "C:/research/project-1",
                "registered_t1_artifact_ids": t1_ids,
            },
        }
    }


def test_explicit_acpc_goal_creates_reviewed_native_node() -> None:
    result = generate_plan_from_goal(
        "对已登记 T1w 进行前联合定位",
        provider="rule_based",
        constraints=_constraints(t1_ids=["t1-001"]),
    )
    assert result.ok
    assert result.plan["nodes"][0]["id"] == "native_auto_acpc_align"
    assert result.plan["nodes"][0]["params"]["source_t1_artifact_id"] == "t1-001"
    assert result.plan["metadata"]["goal_kind"] == "acpc_alignment"


def test_english_anterior_commissure_localization_goal_creates_native_node() -> None:
    result = generate_plan_from_goal(
        "Locate the anterior commissure in the registered T1w image",
        provider="rule_based",
        constraints=_constraints(t1_ids=["t1-001"]),
    )
    assert result.ok
    assert result.plan["nodes"][0]["id"] == "native_auto_acpc_align"


def test_anterior_commissure_explanation_is_not_treated_as_localization() -> None:
    result = generate_plan_from_goal(
        "解释前联合的解剖含义",
        provider="rule_based",
        constraints=_constraints(t1_ids=["t1-001"]),
    )
    assert not result.ok
    assert result.planner_evidence is not None
    assert result.planner_evidence.failure_code == "UNSUPPORTED_GOAL"


def test_acpc_goal_without_a_selected_registered_t1_requests_input() -> None:
    result = generate_plan_from_goal(
        "AC-PC alignment",
        provider="rule_based",
        constraints=_constraints(t1_ids=[]),
    )
    assert not result.ok
    assert result.clarification_required
    assert result.plan == {}
    assert result.missing_prerequisites == [
        "REGISTERED_T1_ARTIFACT_REQUIRED: ACPC alignment requires exactly one selected registered T1w artifact."
    ]
