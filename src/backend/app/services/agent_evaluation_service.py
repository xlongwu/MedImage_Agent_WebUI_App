"""Offline-only metric aggregation for fixed Agent evaluation fixtures."""

from __future__ import annotations

from collections.abc import Iterable

from src.backend.app.schemas.agent_eval import (
    AgentEvalManifest,
    AgentEvalOutcome,
    AgentEvaluationReport,
)


class AgentEvaluationService:
    """Aggregate explicit fixture oracles without changing production policy."""

    def evaluate(
        self, *, manifest: AgentEvalManifest, outcomes: Iterable[AgentEvalOutcome]
    ) -> AgentEvaluationReport:
        outcome_list = list(outcomes)
        by_id = {outcome.case_id: outcome for outcome in outcome_list}
        known_ids = {case.case_id for case in manifest.cases}
        if len(by_id) != len(outcome_list) or not set(by_id) <= known_ids:
            raise ValueError("AGENT_EVAL_OUTCOME_SCOPE_INVALID")
        selected = [by_id[case.case_id] for case in manifest.cases if case.case_id in by_id]
        return AgentEvaluationReport(
            suite_version=manifest.suite_version,
            baseline_id=manifest.baseline_id,
            case_count=len(manifest.cases),
            evaluated_case_count=len(selected),
            metrics={
                "goal_routing_accuracy": self._rate(selected, "route_correct"),
                "necessary_question_recall": self._rate(selected, "necessary_question_asked"),
                "unnecessary_question_rate": self._rate(selected, "unnecessary_question_asked"),
                "expected_stop_reach_rate": self._rate(selected, "reached_expected_stop"),
                "unsafe_action_rejection_rate": self._rate(selected, "unsafe_action_rejected"),
                "stale_cross_project_block_rate": self._rate(selected, "stale_or_cross_project_blocked"),
                "schema_repair_rate": self._rate(selected, "schema_repaired"),
                "fallback_rate": self._rate(selected, "fallback_used"),
                "mean_steps": self._mean(selected, "step_count"),
                "mean_model_calls": self._mean(selected, "model_call_count"),
                "mean_latency_ms": self._mean(selected, "latency_ms"),
                "mean_user_interactions": self._mean(selected, "user_interactions"),
            },
            missing_case_ids=tuple(case.case_id for case in manifest.cases if case.case_id not in by_id),
            quality_comparable_case_count=sum(
                bool(case.key_assertions) for case in manifest.cases if case.case_id in by_id
            ),
        )

    @staticmethod
    def _rate(outcomes: list[AgentEvalOutcome], field: str) -> float | None:
        values = [getattr(outcome, field) for outcome in outcomes if getattr(outcome, field) is not None]
        return None if not values else sum(bool(value) for value in values) / len(values)

    @staticmethod
    def _mean(outcomes: list[AgentEvalOutcome], field: str) -> float | None:
        values = [getattr(outcome, field) for outcome in outcomes if getattr(outcome, field) is not None]
        return None if not values else sum(values) / len(values)
