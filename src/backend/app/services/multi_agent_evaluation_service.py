"""Deterministic, offline-only evaluation of read-only review findings.

This is deliberately not a production multi-Agent runtime: it has no store,
provider, planner, Approval Gate, Gateway, runner, filesystem-write, or
network dependency.  It only compares frozen fixtures and reports whether the
stage-seven evidence is sufficient to request a separately approved design.
"""

from __future__ import annotations

import hashlib
import json
import re
from math import ceil

from src.backend.app.schemas.agent_eval import (
    AgentReviewFinding,
    MultiAgentCaseResult,
    MultiAgentEvalCase,
    MultiAgentEvalManifest,
    MultiAgentEvaluationReport,
    RecordedEvaluationRun,
)

_REVIEWER_ORDER = {"science": 0, "safety": 1, "completeness": 2}
_SEVERITY_ORDER = {"blocking": 0, "warning": 1}
_FORBIDDEN_SUGGESTION = re.compile(
    r"\b(command|shell|path|approval|approve|ticket|execute|execution|dispatch|runner)\b|命令|路径|审批|票据|执行|分派",
    re.IGNORECASE,
)


class MultiAgentEvaluationService:
    """Compare a single-Agent baseline with up to three frozen reviewers."""

    def evaluate(self, manifest: MultiAgentEvalManifest) -> MultiAgentEvaluationReport:
        results = tuple(self._evaluate_case(case) for case in manifest.cases)
        baseline_metrics = self._metrics(results, arm="baseline")
        candidate_metrics = self._metrics(results, arm="candidate")
        failures = self._gate_failures(manifest, results, baseline_metrics, candidate_metrics)
        gate_passed = not failures
        return MultiAgentEvaluationReport(
            suite_version=manifest.suite_version,
            manifest_hash=self._manifest_hash(manifest),
            case_count=len(results),
            results=results,
            baseline_metrics=baseline_metrics,
            candidate_metrics=candidate_metrics,
            gate_passed=gate_passed,
            gate_failures=tuple(failures),
            conclusion=(
                "production_design_requires_approval" if gate_passed else "continue_single_agent"
            ),
        )

    @staticmethod
    def _manifest_hash(manifest: MultiAgentEvalManifest) -> str:
        payload = json.dumps(
            manifest.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def _evaluate_case(self, case: MultiAgentEvalCase) -> MultiAgentCaseResult:
        rejected: list[str] = []
        partial = False
        accepted: list[AgentReviewFinding] = []
        for review in case.reviewers:
            if review.status != "completed":
                partial = True
                rejected.append(f"MULTI_AGENT_EVAL_REVIEWER_{review.status.upper()}")
                continue
            for finding in review.findings:
                rejection = self._finding_rejection(case, finding)
                if rejection:
                    rejected.append(rejection)
                else:
                    accepted.append(finding)

        findings = self._deduplicate(accepted)
        if any(code in {"MULTI_AGENT_EVAL_INPUT_REF_INVALID", "MULTI_AGENT_EVAL_FORBIDDEN_SUGGESTION"} for code in rejected):
            status = "blocked"
        elif self._has_conflict(findings):
            rejected.append("MULTI_AGENT_EVAL_REVIEW_CONFLICT")
            status = "handoff"
        elif partial:
            status = "partial"
        else:
            status = "completed"
        candidate = case.candidate.model_copy(
            update={
                "call_count": case.candidate.call_count + len(case.reviewers),
                "input_tokens": case.candidate.input_tokens + sum(review.input_tokens for review in case.reviewers),
                "latency_ms": max(
                    case.candidate.latency_ms,
                    *(review.latency_ms for review in case.reviewers),
                ),
            }
        )
        return MultiAgentCaseResult(
            case_id=case.case_id,
            candidate_status=status,
            aggregated_findings=findings,
            rejected_codes=tuple(sorted(set(rejected))),
            baseline=case.baseline,
            candidate=candidate,
            candidate_call_count=candidate.call_count,
            candidate_input_tokens=candidate.input_tokens,
            candidate_latency_ms=candidate.latency_ms,
        )

    @staticmethod
    def _finding_rejection(case: MultiAgentEvalCase, finding: AgentReviewFinding) -> str | None:
        if not set(finding.input_refs).issubset(case.input_refs):
            return "MULTI_AGENT_EVAL_INPUT_REF_INVALID"
        if finding.suggested_change and _FORBIDDEN_SUGGESTION.search(finding.suggested_change):
            return "MULTI_AGENT_EVAL_FORBIDDEN_SUGGESTION"
        return None

    @staticmethod
    def _deduplicate(findings: list[AgentReviewFinding]) -> tuple[AgentReviewFinding, ...]:
        unique: dict[tuple[str, str, tuple[str, ...]], AgentReviewFinding] = {}
        for finding in findings:
            key = (finding.severity, finding.code, tuple(sorted(finding.input_refs)))
            existing = unique.get(key)
            if existing is None or _REVIEWER_ORDER[finding.reviewer_kind] < _REVIEWER_ORDER[existing.reviewer_kind]:
                unique[key] = finding
        return tuple(
            sorted(
                unique.values(),
                key=lambda finding: (
                    _SEVERITY_ORDER[finding.severity],
                    finding.code,
                    _REVIEWER_ORDER[finding.reviewer_kind],
                    finding.input_refs,
                ),
            )
        )

    @staticmethod
    def _has_conflict(findings: tuple[AgentReviewFinding, ...]) -> bool:
        by_code: dict[str, set[str]] = {}
        for finding in findings:
            by_code.setdefault(finding.code, set()).add(finding.severity)
        return any(len(severities) > 1 for severities in by_code.values())

    def _metrics(
        self, results: tuple[MultiAgentCaseResult, ...], *, arm: str
    ) -> dict[str, float | int | None]:
        runs = [result.baseline if arm == "baseline" else result.candidate for result in results]
        # ``results`` deliberately has no reference payload.  Constructing it
        # in the public comparison loop would risk treating a reviewer result as
        # authority, so recall is supplied by the frozen candidate finding list
        # against case-level references in ``_gate_failures`` below.
        false_positive_tasks = sum(bool(run.false_positive_blocking_codes) for run in runs)
        return {
            "false_positive_blocking_task_rate": false_positive_tasks / len(runs) if runs else None,
            "conclusion_consistency_rate": self._consistency_rate(runs),
            "mean_model_calls": self._mean(runs, "call_count"),
            "mean_input_tokens": self._mean(runs, "input_tokens"),
            "p95_latency_ms": self._percentile([run.latency_ms for run in runs], 0.95),
            "mean_human_operations": self._mean(runs, "human_operations"),
        }

    @staticmethod
    def _consistency_rate(runs: list[RecordedEvaluationRun]) -> float | None:
        if not runs:
            return None
        return sum(len(set(run.repeated_conclusion_hashes)) == 1 for run in runs) / len(runs)

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
        failures: list[str] = []
        baseline_recall, candidate_recall = self._blocking_recall(manifest, results)
        candidate["blocking_omission_recall"] = candidate_recall
        baseline["blocking_omission_recall"] = baseline_recall
        if candidate_recall < baseline_recall + 0.10:
            failures.append("MULTI_AGENT_EVAL_GATE_BLOCKING_RECALL")
        if (candidate["false_positive_blocking_task_rate"] or 0.0) > 0.03:
            failures.append("MULTI_AGENT_EVAL_GATE_FALSE_POSITIVE_RATE")
        if (candidate["conclusion_consistency_rate"] or 0.0) < (baseline["conclusion_consistency_rate"] or 0.0):
            failures.append("MULTI_AGENT_EVAL_GATE_CONCLUSION_CONSISTENCY")
        if (candidate["mean_model_calls"] or 0.0) > (baseline["mean_model_calls"] or 0.0) * 2.5:
            failures.append("MULTI_AGENT_EVAL_GATE_MODEL_CALLS")
        if (candidate["mean_input_tokens"] or 0.0) > (baseline["mean_input_tokens"] or 0.0) * 2.0:
            failures.append("MULTI_AGENT_EVAL_GATE_INPUT_TOKENS")
        if (candidate["p95_latency_ms"] or 0.0) > (baseline["p95_latency_ms"] or 0.0) * 1.8:
            failures.append("MULTI_AGENT_EVAL_GATE_P95_LATENCY")
        if (candidate["mean_human_operations"] or 0.0) > (baseline["mean_human_operations"] or 0.0):
            failures.append("MULTI_AGENT_EVAL_GATE_HUMAN_OPERATIONS")
        if not any(case.source_kind == "trace_replay_redacted" for case in manifest.cases):
            failures.append("MULTI_AGENT_EVAL_GATE_REDACTED_TRACE_REPLAY_REQUIRED")
        return failures

    @staticmethod
    def _blocking_recall(
        manifest: MultiAgentEvalManifest,
        results: tuple[MultiAgentCaseResult, ...],
    ) -> tuple[float, float]:
        by_id = {result.case_id: result for result in results}
        references = [set(case.reference_blocking_codes) for case in manifest.cases]
        total = sum(len(reference) for reference in references)
        if not total:
            return 0.0, 0.0
        baseline_found = sum(
            len(set(case.baseline.finding_codes) & set(case.reference_blocking_codes))
            for case in manifest.cases
        )
        candidate_found = sum(
            len(set(by_id[case.case_id].candidate.finding_codes) & set(case.reference_blocking_codes))
            for case in manifest.cases
        )
        return baseline_found / total, candidate_found / total
