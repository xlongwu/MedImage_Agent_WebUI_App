from __future__ import annotations

from pathlib import Path

import pytest

from src.backend.app.schemas.agent_eval import AgentEvalManifest, AgentEvalOutcome
from src.backend.app.services.agent_evaluation_service import AgentEvaluationService

_MANIFEST = Path(__file__).parents[1] / "fixtures" / "agent_eval" / "v2" / "manifest.json"


def test_versioned_eval_manifest_uses_only_registered_drivers_and_bilingual_oracles() -> None:
    manifest = AgentEvalManifest.model_validate_json(_MANIFEST.read_text(encoding="utf-8"))

    assert {case.driver for case in manifest.cases} == {
        "plan_only", "decision_required", "provider_failure", "invalid_action",
        "invalid_json", "invalid_action_type", "repair_then_valid",
        "provider_timeout", "missing_api_key", "unknown_call_outcome",
        "duplicate_command", "restart_recovery", "approval_drift", "unsafe_path",
        "memory_relevant_preference", "memory_irrelevant_preference",
        "memory_stale_authoritative_source", "memory_science_confirmation_required",
        "memory_disabled_zero_probe", "memory_partial_health",
        "context_required_section_missing", "context_optional_section_omitted",
        "context_size_limit", "context_cross_project_reference",
    }
    assert {case.language for case in manifest.cases} == {"en", "zh-CN"}
    assert all(case.goal and case.expected_final_state for case in manifest.cases)


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
    assert report.gate_passed is False
    assert report.gate_failures
    with pytest.raises(ValueError, match="AGENT_EVAL_OUTCOME_SCOPE_INVALID"):
        AgentEvaluationService().evaluate(
            manifest=manifest, outcomes=[AgentEvalOutcome(case_id="unknown")]
        )


# ── token cost metrics ───────────────────────────────────────────────────────


def test_token_metrics_are_aggregated_into_report() -> None:
    manifest = AgentEvalManifest.model_validate_json(_MANIFEST.read_text(encoding="utf-8"))
    outcomes = [
        AgentEvalOutcome(
            case_id=manifest.cases[0].case_id,
            route_correct=True,
            reached_expected_stop=True,
            step_count=2,
            model_call_count=2,
            latency_ms=100,
            input_tokens=300,
            output_tokens=40,
            cached_input_tokens=120,
        ),
        AgentEvalOutcome(
            case_id=manifest.cases[1].case_id,
            route_correct=True,
            reached_expected_stop=True,
            step_count=1,
            model_call_count=1,
            latency_ms=50,
            input_tokens=100,
            output_tokens=60,
            cached_input_tokens=0,
        ),
    ]

    report = AgentEvaluationService().evaluate(manifest=manifest, outcomes=outcomes)

    assert report.metrics["total_input_tokens"] == 400
    assert report.metrics["total_output_tokens"] == 100
    assert report.metrics["total_cached_input_tokens"] == 120
    assert report.metrics["mean_input_tokens"] == 200
    assert report.metrics["mean_output_tokens"] == 50


def test_token_metrics_stay_none_without_model_calls() -> None:
    manifest = AgentEvalManifest.model_validate_json(_MANIFEST.read_text(encoding="utf-8"))

    report = AgentEvaluationService().evaluate(
        manifest=manifest,
        outcomes=[AgentEvalOutcome(case_id=manifest.cases[0].case_id, route_correct=True)],
    )

    assert report.metrics["total_input_tokens"] is None
    assert report.metrics["mean_output_tokens"] is None


# ── baseline comparison ──────────────────────────────────────────────────────


def _baseline_report(metrics: dict) -> "AgentEvaluationReport":
    from src.backend.app.schemas.agent_eval import AgentEvaluationReport

    return AgentEvaluationReport(
        suite_version="suite",
        baseline_id="base",
        model_profile_hash="a" * 64,
        manifest_hash="b" * 64,
        case_count=24,
        evaluated_case_count=24,
        passed_case_count=24,
        failed_case_count=0,
        quality_comparable_case_count=0,
        metrics=metrics,
    )


def test_compare_with_baseline_flags_metric_regressions() -> None:
    from src.backend.app.services.agent_evaluation_service import compare_with_baseline

    baseline = _baseline_report({
        "goal_routing_accuracy": 1.0,
        "unnecessary_question_rate": 0.0,
        "mean_latency_ms": 100,
        "duplicate_side_effect_rate": 0.0,
    })
    current = _baseline_report({
        "goal_routing_accuracy": 0.9,
        "unnecessary_question_rate": 0.5,
        "mean_latency_ms": 200,
        "duplicate_side_effect_rate": 0.0,
    })

    regressions = compare_with_baseline(current, baseline)

    assert set(regressions) == {
        "AGENT_EVAL_REGRESSION_GOAL_ROUTING_ACCURACY",
        "AGENT_EVAL_REGRESSION_UNNECESSARY_QUESTION_RATE",
        "AGENT_EVAL_REGRESSION_MEAN_LATENCY_MS",
    }


def test_compare_with_baseline_ignores_improvements_and_missing_values() -> None:
    from src.backend.app.services.agent_evaluation_service import compare_with_baseline

    baseline = _baseline_report({
        "goal_routing_accuracy": 0.9,
        "unnecessary_question_rate": 0.5,
        "mean_latency_ms": 200,
        "fallback_rate": None,
    })
    current = _baseline_report({
        "goal_routing_accuracy": 1.0,
        "unnecessary_question_rate": 0.0,
        "mean_latency_ms": 100,
        "fallback_rate": 0.5,
    })

    assert compare_with_baseline(current, baseline) == ()


def test_compare_with_baseline_within_tolerance_passes() -> None:
    from src.backend.app.services.agent_evaluation_service import compare_with_baseline

    baseline = _baseline_report({"goal_routing_accuracy": 1.0, "mean_latency_ms": 100})
    current = _baseline_report({"goal_routing_accuracy": 0.99, "mean_latency_ms": 101})

    assert compare_with_baseline(current, baseline) == ()
