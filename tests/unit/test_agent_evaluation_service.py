from __future__ import annotations

from pathlib import Path

import pytest

from src.backend.app.schemas.agent_eval import AgentEvalManifest, AgentEvalOutcome
from src.backend.app.services.agent_evaluation_service import AgentEvaluationService

_MANIFEST = Path(__file__).parents[1] / "fixtures" / "agent_eval" / "v1" / "manifest.json"


def test_versioned_eval_manifest_covers_required_categories_and_bilingual_oracles() -> None:
    manifest = AgentEvalManifest.model_validate_json(_MANIFEST.read_text(encoding="utf-8"))

    assert {case.category for case in manifest.cases} == {
        "normal", "recovery", "provider", "safety", "stability"
    }
    assert {case.language for case in manifest.cases} == {"en", "zh-CN"}
    assert all(case.forbidden_calls and case.key_assertions for case in manifest.cases)


def test_metric_aggregation_preserves_unknown_quality_and_rejects_out_of_scope_outcomes() -> None:
    manifest = AgentEvalManifest.model_validate_json(_MANIFEST.read_text(encoding="utf-8"))
    outcomes = [
        AgentEvalOutcome(
            case_id=manifest.cases[0].case_id, route_correct=True,
            reached_expected_stop=True, unsafe_action_rejected=True,
            step_count=2, model_call_count=1, latency_ms=100, user_interactions=0,
        )
    ]

    report = AgentEvaluationService().evaluate(manifest=manifest, outcomes=outcomes)

    assert report.evaluated_case_count == 1
    assert report.metrics["goal_routing_accuracy"] == 1.0
    assert report.metrics["necessary_question_recall"] is None
    assert len(report.missing_case_ids) == len(manifest.cases) - 1
    with pytest.raises(ValueError, match="AGENT_EVAL_OUTCOME_SCOPE_INVALID"):
        AgentEvaluationService().evaluate(
            manifest=manifest, outcomes=[AgentEvalOutcome(case_id="unknown")]
        )
