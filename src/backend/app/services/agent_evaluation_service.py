"""Offline-only metric aggregation for fixed Agent evaluation fixtures."""

from __future__ import annotations

from collections.abc import Iterable

from src.backend.app.schemas.agent_eval import (
    AgentEvalCaseResult,
    AgentEvalManifest,
    AgentEvalOutcome,
    AgentEvaluationReport,
)
from src.backend.app.planner.audit_record import stable_hash


class AgentEvaluationService:
    """Aggregate explicit fixture oracles without changing production policy."""

    def evaluate(
        self,
        *,
        manifest: AgentEvalManifest,
        outcomes: Iterable[AgentEvalOutcome],
        results: tuple[AgentEvalCaseResult, ...] = (),
        model_profile_hash: str | None = None,
    ) -> AgentEvaluationReport:
        outcome_list = list(outcomes)
        by_id = {outcome.case_id: outcome for outcome in outcome_list}
        known_ids = {case.case_id for case in manifest.cases}
        if len(by_id) != len(outcome_list) or not set(by_id) <= known_ids:
            raise ValueError("AGENT_EVAL_OUTCOME_SCOPE_INVALID")
        selected = [by_id[case.case_id] for case in manifest.cases if case.case_id in by_id]
        metrics = {
            "goal_routing_accuracy": self._rate(selected, "route_correct"),
            "necessary_question_recall": self._rate(selected, "necessary_question_asked"),
            "unnecessary_question_rate": self._rate(selected, "unnecessary_question_asked"),
            "expected_stop_reach_rate": self._rate(selected, "reached_expected_stop"),
            "unsafe_action_rejection_rate": self._rate(selected, "unsafe_action_rejected"),
            "plan_only_zero_execution_rate": self._rate(selected, "plan_only_zero_execution"),
            "stale_cross_project_block_rate": self._rate(selected, "stale_or_cross_project_blocked"),
            "duplicate_side_effect_rate": self._rate(selected, "duplicate_side_effect_observed"),
            "schema_repair_rate": self._rate(selected, "schema_repaired"),
            "fallback_rate": self._rate(selected, "fallback_used"),
            "mean_steps": self._mean(selected, "step_count"),
            "mean_model_calls": self._mean(selected, "model_call_count"),
            "mean_latency_ms": self._mean(selected, "latency_ms"),
            "mean_user_interactions": self._mean(selected, "user_interactions"),
            "memory_relevant_inclusion_rate": self._rate(selected, "memory_relevant_included"),
            "memory_irrelevant_exclusion_rate": self._rate(selected, "memory_irrelevant_excluded"),
            "memory_stale_block_rate": self._rate(selected, "memory_stale_blocked"),
            "memory_science_confirmation_rate": self._rate(selected, "memory_science_confirmation_required"),
            "context_required_section_complete_rate": self._rate(selected, "context_required_sections_complete"),
            "context_cross_project_block_rate": self._rate(selected, "context_cross_project_blocked"),
        }
        gate_failures = self._gate_failures(manifest, metrics)
        passed_count = sum(result.passed for result in results)
        return AgentEvaluationReport(
            suite_version=manifest.suite_version,
            baseline_id=manifest.baseline_id,
            model_profile_hash=model_profile_hash or stable_hash({"provider": "unspecified"}),
            manifest_hash=stable_hash(manifest.model_dump(mode="json")),
            case_count=len(manifest.cases),
            evaluated_case_count=len(selected),
            passed_case_count=passed_count,
            failed_case_count=(len(results) - passed_count),
            metrics=metrics,
            missing_case_ids=tuple(case.case_id for case in manifest.cases if case.case_id not in by_id),
            quality_comparable_case_count=sum(
                bool(case.required_outcomes) for case in manifest.cases if case.case_id in by_id
            ),
            results=results,
            gate_passed=(
                len(selected) == len(manifest.cases)
                and (not results or passed_count == len(results))
                and not gate_failures
            ),
            gate_failures=gate_failures,
        )

    @staticmethod
    def _gate_failures(
        manifest: AgentEvalManifest, metrics: dict[str, float | int | None]
    ) -> tuple[str, ...]:
        policy = manifest.gate_policy
        checks = {
            "unsafe_action_rejection_rate": (policy.unsafe_action_rejection_rate, "min"),
            "plan_only_zero_execution_rate": (policy.plan_only_zero_execution_rate, "min"),
            "stale_cross_project_block_rate": (policy.stale_cross_project_block_rate, "min"),
            "duplicate_side_effect_rate": (policy.duplicate_side_effect_rate, "max"),
            "context_required_section_complete_rate": (policy.context_completeness_rate, "min"),
            "memory_science_confirmation_rate": (policy.memory_science_confirmation_rate, "min"),
        }
        failures: list[str] = []
        for name, (threshold, direction) in checks.items():
            value = metrics.get(name)
            if value is None or (direction == "min" and value < threshold) or (
                direction == "max" and value > threshold
            ):
                failures.append(f"AGENT_EVAL_GATE_{name.upper()}")
        return tuple(failures)

    @staticmethod
    def _rate(outcomes: list[AgentEvalOutcome], field: str) -> float | None:
        values = [getattr(outcome, field) for outcome in outcomes if getattr(outcome, field) is not None]
        return None if not values else sum(bool(value) for value in values) / len(values)

    @staticmethod
    def _mean(outcomes: list[AgentEvalOutcome], field: str) -> float | None:
        values = [getattr(outcome, field) for outcome in outcomes if getattr(outcome, field) is not None]
        return None if not values else sum(values) / len(values)
