from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from src.backend.app.schemas.agent_eval import (
    AgentReviewFinding,
    MultiAgentEvalManifest,
    RecordedReviewerRun,
)
from src.backend.app.services.multi_agent_evaluation_service import MultiAgentEvaluationService

_ROOT = Path(__file__).parents[2]
_MANIFEST = _ROOT / "tests" / "fixtures" / "agent_eval" / "multi_agent" / "manifest.json"


def _manifest() -> MultiAgentEvalManifest:
    return MultiAgentEvalManifest.model_validate_json(_MANIFEST.read_text(encoding="utf-8"))


def _finding(*, reviewer_kind: str = "science", severity: str = "blocking", code: str = "EXTRA_BLOCK", input_refs: tuple[str, ...] = ("goal",), suggested_change: str | None = None) -> AgentReviewFinding:
    return AgentReviewFinding(
        reviewer_kind=reviewer_kind,
        severity=severity,
        code=code,
        message_key=f"agent.review.{code.lower()}",
        input_refs=input_refs,
        suggested_change=suggested_change,
    )


def test_frozen_manifest_covers_stage_seven_scenarios_and_both_languages() -> None:
    manifest = _manifest()

    assert manifest.schema_version == 2
    assert {case.language for case in manifest.cases} == {"en", "zh-CN"}
    assert {
        scenario for case in manifest.cases for scenario in case.scenarios
    } >= {
        "plan_only", "missing_prerequisite", "alff_falff", "reho", "motion", "qc",
        "unsupported_goal", "environment_unavailable", "unsafe_write_root", "invalid_model_action",
    }
    assert all(case.source_kind == "synthetic" for case in manifest.cases)
    serialized = _MANIFEST.read_text(encoding="utf-8")
    assert "rawdata" not in serialized.lower()
    assert "password" not in serialized.lower()
    assert "system_prompt" not in serialized.lower()


def test_review_finding_is_fixed_and_cannot_carry_authority_fields() -> None:
    with pytest.raises(ValidationError):
        AgentReviewFinding.model_validate(
            {
                "reviewer_kind": "safety",
                "severity": "blocking",
                "code": "FORGED_AUTHORITY",
                "message_key": "agent.review.forged_authority",
                "input_refs": ["goal"],
                "approval": "forged",
            }
        )


def test_synthetic_preflight_comparison_reports_metrics_but_keeps_single_agent() -> None:
    report = MultiAgentEvaluationService().evaluate(_manifest())

    assert report.gate_passed is False
    assert report.conclusion == "continue_single_agent"
    assert report.gate_failures == ("MULTI_AGENT_EVAL_GATE_REDACTED_TRACE_REPLAY_REQUIRED",)
    assert report.baseline_metrics["blocking_omission_recall"] == 0.3
    assert report.candidate_metrics["blocking_omission_recall"] == 1.0
    assert report.candidate_metrics["false_positive_blocking_task_rate"] == 0.0
    assert report.candidate_metrics["mean_model_calls"] == 2.0
    assert report.candidate_metrics["mean_input_tokens"] == 600.0
    assert report.candidate_metrics["p95_latency_ms"] == 150
    assert report.candidate_metrics["mean_human_operations"] == 1.0


def test_timeout_keeps_other_review_findings_and_returns_a_partial_result() -> None:
    manifest = _manifest()
    original = manifest.cases[0]
    science = RecordedReviewerRun(
        reviewer_kind="science", status="completed", findings=(_finding(code="SCIENCE_CHECK", input_refs=("goal",)),), input_tokens=20, latency_ms=20
    )
    timeout = original.reviewers[0].model_copy(update={"status": "timeout", "findings": ()})
    altered_case = original.model_copy(update={"reviewers": (science, timeout)})
    report = MultiAgentEvaluationService().evaluate(
        manifest.model_copy(update={"cases": (altered_case, *manifest.cases[1:])})
    )
    result = report.results[0]

    assert result.candidate_status == "partial"
    assert [finding.code for finding in result.aggregated_findings] == ["SCIENCE_CHECK"]
    assert "MULTI_AGENT_EVAL_REVIEWER_TIMEOUT" in result.rejected_codes


def test_unavailable_reviewer_is_also_reported_as_a_safe_partial_result() -> None:
    manifest = _manifest()
    original = manifest.cases[0]
    unavailable = original.reviewers[0].model_copy(update={"status": "unavailable", "findings": ()})

    result = MultiAgentEvaluationService().evaluate(
        manifest.model_copy(
            update={"cases": (original.model_copy(update={"reviewers": (unavailable,)}), *manifest.cases[1:])}
        )
    ).results[0]

    assert result.candidate_status == "partial"
    assert "MULTI_AGENT_EVAL_REVIEWER_UNAVAILABLE" in result.rejected_codes


def test_duplicate_findings_are_deduplicated_and_review_order_is_irrelevant() -> None:
    manifest = _manifest()
    original = manifest.cases[0]
    duplicate = RecordedReviewerRun(
        reviewer_kind="science", status="completed", findings=(_finding(code="DUPLICATE", input_refs=("goal",)),), input_tokens=20, latency_ms=20
    )
    duplicate_safety = RecordedReviewerRun(
        reviewer_kind="safety", status="completed", findings=(_finding(reviewer_kind="safety", code="DUPLICATE", input_refs=("goal",)),), input_tokens=20, latency_ms=20
    )
    case_a = original.model_copy(update={"reviewers": (duplicate, duplicate_safety)})
    case_b = original.model_copy(update={"reviewers": (duplicate_safety, duplicate)})

    result_a = MultiAgentEvaluationService().evaluate(manifest.model_copy(update={"cases": (case_a, *manifest.cases[1:])})).results[0]
    result_b = MultiAgentEvaluationService().evaluate(manifest.model_copy(update={"cases": (case_b, *manifest.cases[1:])})).results[0]

    assert len(result_a.aggregated_findings) == 1
    assert result_a.aggregated_findings == result_b.aggregated_findings


@pytest.mark.parametrize(
    ("finding", "expected_status", "expected_code"),
    [
        (_finding(severity="warning", code="PLAN_CONFLICT"), "handoff", "MULTI_AGENT_EVAL_REVIEW_CONFLICT"),
        (_finding(code="CROSS_CONTEXT", input_refs=("other-project",)), "blocked", "MULTI_AGENT_EVAL_INPUT_REF_INVALID"),
        (_finding(code="UNKNOWN_CONTEXT", input_refs=("unknown-ref",)), "blocked", "MULTI_AGENT_EVAL_INPUT_REF_INVALID"),
        (_finding(code="COMMAND_SUGGESTION", suggested_change="Run a shell command."), "blocked", "MULTI_AGENT_EVAL_FORBIDDEN_SUGGESTION"),
        (_finding(code="PATH_SUGGESTION", suggested_change="Change the output path."), "blocked", "MULTI_AGENT_EVAL_FORBIDDEN_SUGGESTION"),
        (_finding(code="APPROVAL_SUGGESTION", suggested_change="Approve this plan."), "blocked", "MULTI_AGENT_EVAL_FORBIDDEN_SUGGESTION"),
        (_finding(code="TICKET_SUGGESTION", suggested_change="Create an execution ticket."), "blocked", "MULTI_AGENT_EVAL_FORBIDDEN_SUGGESTION"),
    ],
)
def test_conflicts_invalid_references_and_authority_suggestions_fail_closed(
    finding: AgentReviewFinding, expected_status: str, expected_code: str
) -> None:
    manifest = _manifest()
    original = manifest.cases[0]
    first = RecordedReviewerRun(
        reviewer_kind="science", status="completed", findings=(finding,), input_tokens=20, latency_ms=20
    )
    reviewers = (first,)
    if expected_status == "handoff":
        reviewers = (
            first,
            RecordedReviewerRun(
                reviewer_kind="safety", status="completed",
                findings=(_finding(reviewer_kind="safety", severity="blocking", code="PLAN_CONFLICT"),),
                input_tokens=20, latency_ms=20,
            ),
        )
    result = MultiAgentEvaluationService().evaluate(
        manifest.model_copy(update={"cases": (original.model_copy(update={"reviewers": reviewers}), *manifest.cases[1:])})
    ).results[0]

    assert result.candidate_status == expected_status
    assert expected_code in result.rejected_codes


def test_evaluation_has_no_production_dependencies_or_mode_switch() -> None:
    service_path = _ROOT / "src" / "backend" / "app" / "services" / "multi_agent_evaluation_service.py"
    imported_modules = [
        node.module for node in ast.walk(ast.parse(service_path.read_text(encoding="utf-8")))
        if isinstance(node, ast.ImportFrom) and node.module
    ]

    assert imported_modules == ["__future__", "math", "src.backend.app.schemas.agent_eval"]
    assert "AgentHarnessAttempt" not in service_path.read_text(encoding="utf-8")


def test_offline_script_emits_a_valid_report_without_claiming_a_production_gate() -> None:
    completed = subprocess.run(
        [sys.executable, str(_ROOT / "scripts" / "run_multi_agent_evaluation.py"), "--manifest", str(_MANIFEST), "--summary"],
        cwd=_ROOT, check=False, capture_output=True, encoding="utf-8", timeout=30,
    )
    report = json.loads(completed.stdout)

    assert completed.returncode == 0, completed.stderr
    assert report["gate_passed"] is False
    assert report["conclusion"] == "continue_single_agent"
    assert report["gate_failures"] == ["MULTI_AGENT_EVAL_GATE_REDACTED_TRACE_REPLAY_REQUIRED"]
