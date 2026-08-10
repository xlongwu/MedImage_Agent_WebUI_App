from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.backend.app.schemas.agent_eval import AdvisorFinding, MultiAgentEvalManifest
from src.backend.app.services.multi_agent_evaluation_service import MultiAgentEvaluationService

_MANIFEST = Path(__file__).parents[1] / "fixtures" / "agent_eval" / "multi_agent" / "manifest.json"


def test_frozen_multi_agent_manifest_has_the_required_balanced_offline_corpus() -> None:
    manifest = MultiAgentEvalManifest.model_validate_json(_MANIFEST.read_text(encoding="utf-8"))

    assert len(manifest.cases) == 30
    assert {case.category for case in manifest.cases} == {
        "eligible",
        "ineligible",
        "adversarial",
    }
    assert all(sum(case.category == category for case in manifest.cases) >= 10 for category in {
        "eligible",
        "ineligible",
        "adversarial",
    })
    assert all(case.synthetic_or_redacted in {"synthetic", "redacted"} for case in manifest.cases)
    assert manifest.gate.blocking_recall_improvement == 0.10
    assert manifest.gate.max_input_token_multiplier == 3.0
    assert manifest.gate.max_p95_latency_multiplier == 2.5


def test_advisor_findings_have_no_action_or_permission_surface() -> None:
    with pytest.raises(ValidationError):
        AdvisorFinding.model_validate(
            {
                "finding_id": "forged",
                "role": "safety_science_reviewer.v1",
                "topic": "safety",
                "classification": "blocker",
                "evidence_refs": ["ev-1"],
                "summary": "Do not trust this.",
                "approval": "forged",
            }
        )


def test_offline_simulation_uses_the_fixed_roles_and_passes_the_confirmed_gate() -> None:
    manifest = MultiAgentEvalManifest.model_validate_json(_MANIFEST.read_text(encoding="utf-8"))

    report = MultiAgentEvaluationService().evaluate(manifest)

    assert report.case_count == 30
    assert report.gate_passed
    assert report.candidate_metrics["eligibility_precision"] == 1.0
    assert report.candidate_metrics["eligibility_recall"] == 1.0
    assert report.candidate_metrics["blocking_finding_recall"] > report.baseline_metrics[
        "blocking_finding_recall"
    ]
    assert all(
        not result.advisors_started
        for result in report.results
        if not result.expected_team_eligible
    )


def test_advisor_failure_or_conflict_cannot_be_reported_as_completed_safety_review() -> None:
    manifest = MultiAgentEvalManifest.model_validate_json(_MANIFEST.read_text(encoding="utf-8"))
    by_id = {result.case_id: result for result in MultiAgentEvaluationService().evaluate(manifest).results}

    assert by_id["adversarial-01"].candidate_status == "fallback"
    assert "MULTI_AGENT_EVAL_SAFETY_REVIEWER_UNAVAILABLE" in by_id["adversarial-01"].advisory.warnings
    assert by_id["adversarial-02"].candidate_status == "handoff"
    assert "MULTI_AGENT_EVAL_CONTRADICTION" in by_id["adversarial-02"].advisory.warnings
    assert by_id["adversarial-03"].candidate_status == "handoff"
    assert "MULTI_AGENT_EVAL_FINDING_REFERENCE_INVALID" in by_id["adversarial-03"].advisory.warnings


def test_false_positive_eligibility_fails_the_gate_and_evaluator_has_no_production_dependencies() -> None:
    manifest = MultiAgentEvalManifest.model_validate_json(_MANIFEST.read_text(encoding="utf-8"))
    original = manifest.cases[10]
    changed = original.model_copy(
        update={
            "independent_evidence_domains": ("project", "safety"),
            "competing_explanations": True,
            "context_safely_prunable": True,
            "safety_reference_finding_available": True,
            "provider_consent": True,
        }
    )
    altered = manifest.model_copy(update={"cases": (*manifest.cases[:10], changed, *manifest.cases[11:])})

    report = MultiAgentEvaluationService().evaluate(altered)

    assert "MULTI_AGENT_EVAL_GATE_FALSE_POSITIVE_WORKER_START" in report.gate_failures
    service_path = Path(__file__).parents[2] / "src" / "backend" / "app" / "services" / "multi_agent_evaluation_service.py"
    imported_modules = [
        node.module
        for node in ast.walk(ast.parse(service_path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom) and node.module
    ]
    assert not any(
        module.startswith(("src.backend.app.api", "src.backend.app.runtime", "src.backend.app.services"))
        for module in imported_modules
    )


def test_reference_plan_conflict_and_safety_regression_are_visible_gate_failures() -> None:
    manifest = MultiAgentEvalManifest.model_validate_json(_MANIFEST.read_text(encoding="utf-8"))
    original = manifest.cases[0]
    unsafe_candidate = original.candidate_plan.model_copy(
        update={"plan_matches_reference": False, "unsafe_plan_rejected": False}
    )
    changed = original.model_copy(update={"candidate_plan": unsafe_candidate})
    altered = manifest.model_copy(update={"cases": (changed, *manifest.cases[1:])})

    report = MultiAgentEvaluationService().evaluate(altered)
    result = next(item for item in report.results if item.case_id == original.case_id)

    assert result.candidate_status == "handoff"
    assert "MULTI_AGENT_EVAL_REFERENCE_PLAN_CONFLICT" in result.advisory.warnings
    assert "MULTI_AGENT_EVAL_GATE_SAFETY_REGRESSION" in report.gate_failures


def test_offline_evaluation_script_emits_a_gate_report_without_creating_an_output_file() -> None:
    script = Path(__file__).parents[2] / "scripts" / "run_multi_agent_evaluation.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--manifest", str(_MANIFEST), "--summary"],
        cwd=Path(__file__).parents[2],
        check=False,
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )

    report = json.loads(completed.stdout)

    assert completed.returncode == 0, completed.stderr
    assert report["gate_passed"] is True
    assert report["case_count"] == 30
    assert set(report["candidate_metrics"]) >= {
        "eligibility_precision",
        "blocking_finding_recall",
        "false_blocker_rate",
        "p95_latency_ms",
        "partial_timeout_fallback_rate",
        "contradiction_handoff_rate",
    }
