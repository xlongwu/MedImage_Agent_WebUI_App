from __future__ import annotations

import pytest

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.schemas.goal_contract import CriterionResult
from src.backend.app.services.agent_task_result_summary import AgentTaskResultSummaryService
from tests.unit.test_agent_task_read_model import _lifecycle, _terminal_evidence


def test_result_summary_requires_bound_reloadable_registered_evidence() -> None:
    lifecycle = _lifecycle("GOAL_SATISFIED").model_copy(
        update={"observation_id": "observation-1", "goal_evaluation_id": "evaluation-1"}
    )
    observation, evaluation = _terminal_evidence()
    summary = AgentTaskResultSummaryService().build(
        lifecycle=lifecycle,
        observation=observation,
        evaluation=evaluation,
    )
    assert summary.outcome == "succeeded"
    assert summary.completed_subjects == 1
    assert summary.artifacts[0].checksum == "abc123"
    assert summary.artifacts[0].reload_status == "passed"


def test_result_summary_downgrades_reload_failure_and_explains_limitations() -> None:
    lifecycle = _lifecycle("GOAL_SATISFIED")
    observation, evaluation = _terminal_evidence(reload_status="failed", completeness="partial")
    observation = observation.model_copy(
        update={
            "scientific": observation.scientific.model_copy(
                update={"limitation_flags": ("preview_only", "metadata_only")}
            )
        }
    )
    summary = AgentTaskResultSummaryService().build(
        lifecycle=lifecycle,
        observation=observation,
        evaluation=evaluation,
    )
    assert summary.outcome == "partial"
    assert any("preview" in item.lower() for item in summary.limitations)
    assert any("metadata" in item.lower() for item in summary.limitations)


def test_result_summary_accepts_satisfied_required_evidence_when_only_optional_source_is_missing() -> None:
    lifecycle = _lifecycle("GOAL_SATISFIED")
    observation, evaluation = _terminal_evidence(completeness="partial")
    observation = observation.model_copy(
        update={
            "completeness": observation.completeness.model_copy(
                update={"missing_sources": ("logs",)}
            )
        }
    )

    summary = AgentTaskResultSummaryService().build(
        lifecycle=lifecycle,
        observation=observation,
        evaluation=evaluation,
    )

    assert summary.outcome == "succeeded"
    assert summary.completed_subjects == 1


def test_result_summary_reports_completed_subject_with_scientific_limitations() -> None:
    lifecycle = _lifecycle("GOAL_SATISFIED")
    observation, evaluation = _terminal_evidence()
    observation = observation.model_copy(
        update={
            "capability": observation.capability.model_copy(
                update={
                    "observed_level": "metadata_only",
                    "defensible_level": "metadata_only",
                }
            ),
            "scientific": observation.scientific.model_copy(
                update={
                    "status": "metadata_only",
                    "limitation_flags": ("simplified",),
                }
            ),
        }
    )
    evaluation = evaluation.model_copy(update={"status": "not_satisfied"})

    summary = AgentTaskResultSummaryService().build(
        lifecycle=lifecycle,
        observation=observation,
        evaluation=evaluation,
    )

    assert summary.outcome == "partial"
    assert summary.completed_subjects == 1
    assert summary.failed_subjects == 0
    assert any("simplified" in item.lower() for item in summary.limitations)


def test_result_summary_rejects_cross_run_binding() -> None:
    lifecycle = _lifecycle("GOAL_SATISFIED")
    observation, evaluation = _terminal_evidence()
    observation = observation.model_copy(
        update={"bindings": observation.bindings.model_copy(update={"run_id": "other-run"})}
    )
    with pytest.raises(SafetyError, match="AGENT_RESULT_BINDING_MISMATCH"):
        AgentTaskResultSummaryService().build(
            lifecycle=lifecycle,
            observation=observation,
            evaluation=evaluation,
        )


def test_result_explanation_is_derived_from_evidence_and_rejects_conflicting_prose() -> None:
    lifecycle = _lifecycle("GOAL_SATISFIED").model_copy(
        update={"observation_id": "observation-1", "goal_evaluation_id": "evaluation-1"}
    )
    observation, evaluation = _terminal_evidence()
    evaluation = evaluation.model_copy(
        update={
            "criterion_results": (
                CriterionResult(
                    criterion_id="registered-artifact",
                    criterion_type="artifact_present",
                    status="passed",
                    reason_code="ARTIFACT_REGISTERED",
                ),
            )
        }
    )

    explanation = AgentTaskResultSummaryService().build_explanation(
        lifecycle=lifecycle,
        observation=observation,
        evaluation=evaluation,
        generated_text="The registered result is available for review.",
    )

    assert explanation.outcome == "succeeded"
    assert explanation.artifact_refs[0].artifact_id == "artifact-1"
    assert explanation.criteria[0].reason_code == "ARTIFACT_REGISTERED"
    assert explanation.generated_text_status == "accepted"

    partial_observation, partial_evaluation = _terminal_evidence(
        reload_status="failed", completeness="partial"
    )
    rejected = AgentTaskResultSummaryService().build_explanation(
        lifecycle=lifecycle,
        observation=partial_observation,
        evaluation=partial_evaluation,
        generated_text="The run succeeded and is fully validated.",
    )

    assert rejected.outcome == "partial"
    assert rejected.generated_text is None
    assert rejected.generated_text_status == "conflict_rejected"
