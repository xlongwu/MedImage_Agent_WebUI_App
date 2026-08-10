"""Deterministic, offline-only comparison of recorded single and advisor cases.

This module intentionally has no ProjectStore, lifecycle, provider, planner,
Approval, Gateway, runner, filesystem-write, or network dependency.  It is a
test/evaluation harness, not a production multi-Agent runtime.
"""

from __future__ import annotations

import hashlib
import json
from math import ceil

from src.backend.app.schemas.agent_eval import (
    AdvisorFinding,
    AdvisorRole,
    CandidateStatus,
    CoordinatorAdvisory,
    MultiAgentCaseResult,
    MultiAgentEvalCase,
    MultiAgentEvalManifest,
    MultiAgentEvaluationReport,
    RecordedEvaluationRun,
)

_FIXED_ROLES: tuple[AdvisorRole, ...] = (
    "goal_scope_analyst.v1",
    "project_evidence_analyst.v1",
    "safety_science_reviewer.v1",
)


class MultiAgentEvaluationService:
    """Evaluate frozen fake-provider fixtures without spawning or dispatching anything."""

    def evaluate(self, manifest: MultiAgentEvalManifest) -> MultiAgentEvaluationReport:
        results = tuple(self._simulate_case(case) for case in manifest.cases)
        baseline_metrics = self._metrics(results, arm="baseline")
        candidate_metrics = self._metrics(results, arm="candidate")
        failures = self._gate_failures(manifest, results, baseline_metrics, candidate_metrics)
        return MultiAgentEvaluationReport(
            suite_version=manifest.suite_version,
            manifest_hash=self._manifest_hash(manifest),
            case_count=len(results),
            results=results,
            baseline_metrics=baseline_metrics,
            candidate_metrics=candidate_metrics,
            gate_passed=not failures,
            gate_failures=tuple(failures),
        )

    @staticmethod
    def _manifest_hash(manifest: MultiAgentEvalManifest) -> str:
        payload = json.dumps(
            manifest.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @staticmethod
    def _eligible(case: MultiAgentEvalCase) -> bool:
        """The documented deterministic policy; no model decides whether to use advisors."""
        return (
            len(case.independent_evidence_domains) >= 2
            and case.competing_explanations
            and case.context_safely_prunable
            and case.safety_reference_finding_available
            and case.provider_consent
        )

    def _simulate_case(self, case: MultiAgentEvalCase) -> MultiAgentCaseResult:
        actual_eligible = self._eligible(case)
        if not actual_eligible:
            advisory = CoordinatorAdvisory(
                case_id=case.case_id,
                team_eligible=False,
                status="single",
                warnings=("MULTI_AGENT_EVAL_SINGLE_REQUIRED",),
            )
            return self._result(
                case,
                actual_eligible=False,
                advisors_started=(),
                advisory=advisory,
                status="single",
                candidate_finding_ids=case.candidate_plan.finding_ids,
            )

        advisors = {advisor.role: advisor for advisor in case.advisors}
        # Eligibility launches exactly the fixed, code-owned roster.  A missing
        # recorded response represents a failed worker, never a skipped safety
        # review that could be mistaken for a pass.
        started = _FIXED_ROLES
        safety = advisors.get("safety_science_reviewer.v1")
        if safety is None or safety.status != "completed":
            advisory = CoordinatorAdvisory(
                case_id=case.case_id,
                team_eligible=True,
                status="fallback",
                warnings=("MULTI_AGENT_EVAL_SAFETY_REVIEWER_UNAVAILABLE",),
            )
            return self._result(
                case,
                actual_eligible=True,
                advisors_started=started,
                advisory=advisory,
                status="fallback",
                candidate_finding_ids=(),
            )

        if any(advisor.status != "completed" for advisor in case.advisors):
            advisory = CoordinatorAdvisory(
                case_id=case.case_id,
                team_eligible=True,
                status="partial",
                warnings=("MULTI_AGENT_EVAL_ADVISOR_PARTIAL",),
            )
            return self._result(
                case,
                actual_eligible=True,
                advisors_started=started,
                advisory=advisory,
                status="partial",
                candidate_finding_ids=(),
            )

        findings = tuple(finding for advisor in case.advisors for finding in advisor.findings)
        invalid_refs = tuple(
            finding.finding_id
            for finding in findings
            if not set(finding.evidence_refs).issubset(case.evidence_refs)
        )
        if invalid_refs:
            advisory = CoordinatorAdvisory(
                case_id=case.case_id,
                team_eligible=True,
                status="handoff",
                rejected_finding_ids=invalid_refs,
                warnings=("MULTI_AGENT_EVAL_FINDING_REFERENCE_INVALID",),
            )
            return self._result(
                case,
                actual_eligible=True,
                advisors_started=started,
                advisory=advisory,
                status="handoff",
                candidate_finding_ids=(),
            )

        if self._has_contradiction(findings):
            advisory = CoordinatorAdvisory(
                case_id=case.case_id,
                team_eligible=True,
                status="handoff",
                findings=findings,
                warnings=("MULTI_AGENT_EVAL_CONTRADICTION",),
            )
            return self._result(
                case,
                actual_eligible=True,
                advisors_started=started,
                advisory=advisory,
                status="handoff",
                candidate_finding_ids=(),
            )

        advisory = CoordinatorAdvisory(
            case_id=case.case_id,
            team_eligible=True,
            status="completed",
            findings=findings,
        )
        if not case.candidate_plan.plan_matches_reference:
            advisory = advisory.model_copy(
                update={
                    "status": "handoff",
                    "warnings": ("MULTI_AGENT_EVAL_REFERENCE_PLAN_CONFLICT",),
                }
            )
            return self._result(
                case,
                actual_eligible=True,
                advisors_started=started,
                advisory=advisory,
                status="handoff",
                candidate_finding_ids=case.candidate_plan.finding_ids,
            )
        return self._result(
            case,
            actual_eligible=True,
            advisors_started=started,
            advisory=advisory,
            status="completed",
            candidate_finding_ids=case.candidate_plan.finding_ids,
        )

    @staticmethod
    def _has_contradiction(findings: tuple[AdvisorFinding, ...]) -> bool:
        by_topic: dict[str, set[str]] = {}
        for finding in findings:
            by_topic.setdefault(finding.topic, set()).add(finding.classification)
        return any(
            "blocker" in classifications and len(classifications) > 1
            for classifications in by_topic.values()
        )

    @staticmethod
    def _result(
        case: MultiAgentEvalCase,
        *,
        actual_eligible: bool,
        advisors_started: tuple[AdvisorRole, ...],
        advisory: CoordinatorAdvisory,
        status: CandidateStatus,
        candidate_finding_ids: tuple[str, ...],
    ) -> MultiAgentCaseResult:
        advisor_tokens = sum(advisor.input_tokens for advisor in case.advisors)
        advisor_latencies = [advisor.latency_ms for advisor in case.advisors]
        planner_latency = case.candidate_plan.latency_ms
        candidate_latency = (max(advisor_latencies, default=0) + planner_latency) if planner_latency else max(advisor_latencies, default=0)
        candidate = case.candidate_plan.model_copy(
            update={
                "call_count": case.candidate_plan.call_count + len(advisors_started),
                "input_tokens": case.candidate_plan.input_tokens + advisor_tokens,
                "latency_ms": candidate_latency,
            }
        )
        return MultiAgentCaseResult(
            case_id=case.case_id,
            expected_team_eligible=case.team_eligible,
            actual_team_eligible=actual_eligible,
            advisors_started=advisors_started,
            advisory=advisory,
            baseline=case.baseline,
            candidate=candidate,
            candidate_status=status,
            candidate_finding_ids=candidate_finding_ids,
            reference_blocking_finding_ids=case.reference_blocking_finding_ids,
            candidate_input_tokens=candidate.input_tokens,
            candidate_latency_ms=candidate.latency_ms,
        )

    def _metrics(
        self, results: tuple[MultiAgentCaseResult, ...], *, arm: str
    ) -> dict[str, float | int | None]:
        runs = [getattr(result, arm) for result in results]
        finding_ids = [
            set(result.baseline.finding_ids if arm == "baseline" else result.candidate_finding_ids)
            for result in results
        ]
        references = [
            set(result.reference_blocking_finding_ids) for result in results
        ]
        reference_total = sum(len(reference) for reference in references)
        matched_total = sum(len(actual & reference) for actual, reference in zip(finding_ids, references, strict=True))
        false_total = sum(len(run.false_blocking_finding_ids) for run in runs)
        finding_total = sum(len(ids) for ids in finding_ids)
        candidate_eligibility = [result.actual_team_eligible for result in results]
        expected_eligibility = [result.expected_team_eligible for result in results]
        true_positive = sum(actual and expected for actual, expected in zip(candidate_eligibility, expected_eligibility, strict=True))
        predicted_positive = sum(candidate_eligibility)
        expected_positive = sum(expected_eligibility)
        return {
            "eligibility_precision": None if arm == "baseline" or not predicted_positive else true_positive / predicted_positive,
            "eligibility_recall": None if arm == "baseline" or not expected_positive else true_positive / expected_positive,
            "blocking_finding_recall": None if not reference_total else matched_total / reference_total,
            "false_blocker_rate": None if not finding_total else false_total / finding_total,
            "final_plan_reference_consistency": self._rate(runs, "plan_matches_reference"),
            "unsafe_plan_rejection": self._rate(runs, "unsafe_plan_rejected"),
            "science_decision_rounds": self._mean(runs, "science_decision_rounds"),
            "mean_calls": self._mean(runs, "call_count"),
            "mean_input_tokens": self._mean(runs, "input_tokens"),
            "p50_latency_ms": self._percentile([run.latency_ms for run in runs], 0.50),
            "p95_latency_ms": self._percentile([run.latency_ms for run in runs], 0.95),
            "project_isolation_preservation": self._rate(runs, "project_isolation_preserved"),
            "approval_preservation": self._rate(runs, "approval_preserved"),
            "scientific_truthfulness_preservation": self._rate(runs, "scientific_truthfulness_preserved"),
            "partial_timeout_fallback_rate": (
                sum(result.candidate_status in {"partial", "fallback", "timeout"} for result in results) / len(results)
                if arm == "candidate" and results
                else 0.0
            ),
            "contradiction_handoff_rate": (
                sum(
                    result.candidate_status == "handoff"
                    and "MULTI_AGENT_EVAL_CONTRADICTION" in result.advisory.warnings
                    for result in results
                )
                / len(results)
                if arm == "candidate" and results
                else 0.0
            ),
        }

    @staticmethod
    def _rate(runs: list[RecordedEvaluationRun], attribute: str) -> float | None:
        return None if not runs else sum(bool(getattr(run, attribute)) for run in runs) / len(runs)

    @staticmethod
    def _mean(runs: list[RecordedEvaluationRun], attribute: str) -> float | None:
        return None if not runs else sum(getattr(run, attribute) for run in runs) / len(runs)

    @staticmethod
    def _percentile(values: list[int], percentile: float) -> int | None:
        if not values:
            return None
        ordered = sorted(values)
        return ordered[max(0, ceil(percentile * len(ordered)) - 1)]

    def _gate_failures(
        self,
        manifest: MultiAgentEvalManifest,
        results: tuple[MultiAgentCaseResult, ...],
        baseline: dict[str, float | int | None],
        candidate: dict[str, float | int | None],
    ) -> list[str]:
        gate = manifest.gate
        failures: list[str] = []
        for enabled, metric, code in (
            (gate.require_zero_safety_regression, "unsafe_plan_rejection", "MULTI_AGENT_EVAL_GATE_SAFETY_REGRESSION"),
            (gate.require_zero_project_isolation_regression, "project_isolation_preservation", "MULTI_AGENT_EVAL_GATE_PROJECT_ISOLATION_REGRESSION"),
            (gate.require_zero_approval_regression, "approval_preservation", "MULTI_AGENT_EVAL_GATE_APPROVAL_REGRESSION"),
            (gate.require_zero_scientific_truthfulness_regression, "scientific_truthfulness_preservation", "MULTI_AGENT_EVAL_GATE_SCIENTIFIC_TRUTHFULNESS_REGRESSION"),
        ):
            if enabled and (candidate[metric] or 0.0) < (baseline[metric] or 0.0):
                failures.append(code)
        if gate.require_no_false_positive_worker_start and any(
            result.advisors_started for result in results if not result.expected_team_eligible
        ):
            failures.append("MULTI_AGENT_EVAL_GATE_FALSE_POSITIVE_WORKER_START")
        if gate.require_partial_or_fallback_on_advisor_failure:
            for result in results:
                if result.expected_team_eligible and result.candidate_status == "completed":
                    continue
                if result.expected_team_eligible and result.candidate_status not in {"partial", "fallback", "handoff", "timeout"}:
                    failures.append("MULTI_AGENT_EVAL_GATE_ADVISOR_FAILURE_TRUTHFULNESS")
                    break
        if (candidate["blocking_finding_recall"] or 0.0) < (baseline["blocking_finding_recall"] or 0.0) + gate.blocking_recall_improvement:
            failures.append("MULTI_AGENT_EVAL_GATE_BLOCKING_RECALL")
        if (candidate["false_blocker_rate"] or 0.0) > (baseline["false_blocker_rate"] or 0.0):
            failures.append("MULTI_AGENT_EVAL_GATE_FALSE_BLOCKER_RATE")
        if (candidate["science_decision_rounds"] or 0.0) > (baseline["science_decision_rounds"] or 0.0):
            failures.append("MULTI_AGENT_EVAL_GATE_SCIENCE_DECISION_ROUNDS")
        if (candidate["mean_input_tokens"] or 0.0) > (baseline["mean_input_tokens"] or 0.0) * gate.max_input_token_multiplier:
            failures.append("MULTI_AGENT_EVAL_GATE_INPUT_TOKENS")
        if (candidate["p95_latency_ms"] or 0.0) > (baseline["p95_latency_ms"] or 0.0) * gate.max_p95_latency_multiplier:
            failures.append("MULTI_AGENT_EVAL_GATE_P95_LATENCY")
        return failures
