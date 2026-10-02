"""Truthful Agent result projection from bound observation/evaluation evidence."""

from __future__ import annotations

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.schemas.agent_task import (
    AgentResultCriterion,
    AgentResultExplanation,
    AgentTaskArtifactSummary,
    AgentTaskResultSummary,
)


class AgentTaskResultSummaryService:
    def build(self, *, lifecycle, observation, evaluation) -> AgentTaskResultSummary:
        self._validate_bindings(lifecycle=lifecycle, observation=observation, evaluation=evaluation)
        completed, failed, excluded, total = self._subject_counts(observation)
        capability = observation.capability.defensible_level
        registered_artifacts = tuple(
            item for item in observation.artifacts
            if item.exists and item.registration_status == "registered"
        )
        artifacts = tuple(
            AgentTaskArtifactSummary(
                artifact_id=item.artifact_id,
                artifact_type=item.artifact_type,
                label=item.artifact_type.replace("_", " ").title(),
                uri=f"project://{lifecycle.project_id}/artifacts/{item.artifact_id}",
                checksum=item.checksum_sha256,
                capability_level=capability,
                reload_status=(
                    "passed" if item.reload_status == "passed"
                    else "failed" if item.reload_status == "failed"
                    else "not_checked" if item.reload_status == "unknown"
                    else "unavailable"
                ),
            )
            for item in registered_artifacts
        )
        report = next(
            (item for item in registered_artifacts if "report" in item.artifact_type.casefold()),
            None,
        )
        defensible = any(
            item.exists
            and item.registration_status == "registered"
            and item.reload_status == "passed"
            for item in observation.artifacts
        )
        complete = (
            evaluation.status == "satisfied"
            and capability in {"computed", "validated"}
            and defensible
            and not failed
            and not observation.completeness.conflicts
            and not observation.completeness.blocking_facts
        )
        raw_limitations = tuple(dict.fromkeys([
            *observation.scientific.limitation_flags,
            *observation.completeness.blocking_facts,
            *observation.completeness.conflicts,
        ]))
        validation_failures = sum(item.status == "failed" for item in observation.validations)
        if complete:
            outcome = "succeeded"
            summary_code = "result.succeeded"
        elif evaluation.status == "not_satisfied" or failed:
            outcome = "partial" if artifacts or completed else "failed"
            summary_code = "result.partial" if outcome == "partial" else "result.failed"
        else:
            outcome = "partial" if artifacts else "indeterminate"
            summary_code = "result.partial" if outcome == "partial" else "result.indeterminate"
        return AgentTaskResultSummary(
            outcome=outcome,
            summary_code=summary_code,
            validation_checks_passed=(
                len(observation.validations) - validation_failures
                if observation.validations
                else None
            ),
            validation_checks_failed=validation_failures if observation.validations else None,
            completed_subjects=completed,
            failed_subjects=failed,
            excluded_subjects=excluded,
            total_subjects=total,
            limitation_codes=raw_limitations,
            recommended_action_code=None if complete else "review_technical_evidence",
            artifacts=artifacts,
            report_artifact_id=report.artifact_id if report else None,
            report_export_uri=(
                f"/api/projects/{lifecycle.project_id}/preprocessing/runs/"
                f"{lifecycle.run_id}/artifacts/{report.artifact_id}/file"
                if report is not None and lifecycle.run_id
                else None
            ),
            export_disabled_code=(
                None
                if report is not None and lifecycle.run_id
                else "no_registered_report"
            ),
        )

    def build_explanation(
        self,
        *,
        lifecycle,
        observation,
        evaluation,
        generated_text: str | None = None,
        generated_text_rejected: bool = False,
    ) -> AgentResultExplanation:
        """Return a deterministic explanation envelope with optional guarded prose.

        The model may contribute text only.  All outcome, artifact, subject and
        criterion fields are rebuilt from the persisted Observation and Goal
        Evaluation on every call.
        """
        summary = self.build(
            lifecycle=lifecycle,
            observation=observation,
            evaluation=evaluation,
        )
        if generated_text_rejected:
            accepted_text, text_status = None, "conflict_rejected"
        else:
            accepted_text, text_status = self._guard_generated_text(
                outcome=summary.outcome,
                capability=observation.capability.defensible_level,
                generated_text=generated_text,
            )
        return AgentResultExplanation(
            outcome=summary.outcome,
            completed_subjects=summary.completed_subjects,
            failed_subjects=summary.failed_subjects,
            excluded_subjects=summary.excluded_subjects,
            total_subjects=summary.total_subjects,
            artifact_refs=summary.artifacts,
            criteria=tuple(
                AgentResultCriterion(
                    criterion_id=item.criterion_id,
                    status=item.status,
                    reason_code=item.reason_code,
                    evidence_ids=item.evidence_ids,
                )
                for item in evaluation.criterion_results
            ),
            limitation_codes=summary.limitation_codes,
            recommended_action_code=summary.recommended_action_code,
            generated_text=accepted_text,
            generated_text_status=text_status,
        )

    @staticmethod
    def _guard_generated_text(
        *,
        outcome: str,
        capability: str,
        generated_text: str | None,
    ) -> tuple[str | None, str]:
        if generated_text is None:
            return None, "not_requested"
        text = " ".join(str(generated_text).split())
        if not text:
            return None, "not_requested"
        normalized = text.casefold()
        success_claims = ("succeeded", "successful", "completed", "validated")
        failure_claims = ("failed", "failure", "not satisfied", "incomplete")
        conflicts = (
            (outcome != "succeeded" and any(term in normalized for term in success_claims))
            or (outcome == "succeeded" and any(term in normalized for term in failure_claims))
            or (capability != "validated" and "validated" in normalized)
        )
        if conflicts:
            return None, "conflict_rejected"
        return text, "accepted"

    @staticmethod
    def _validate_bindings(*, lifecycle, observation, evaluation) -> None:
        bindings = observation.bindings
        expected = (
            bindings.project_id == lifecycle.project_id
            and bindings.lifecycle_id == lifecycle.lifecycle_id
            and bindings.reviewed_plan_id == lifecycle.reviewed_plan_id
            and bindings.run_id == lifecycle.run_id
            and bindings.execution_ticket_id == lifecycle.execution_ticket_id
            and evaluation.project_id == lifecycle.project_id
            and evaluation.lifecycle_id == lifecycle.lifecycle_id
            and evaluation.observation_id == observation.observation_id
            and evaluation.observation_hash == observation.observation_hash
            and evaluation.reviewed_plan_id == bindings.reviewed_plan_id
            and evaluation.plan_hash == bindings.plan_hash
        )
        if not expected:
            raise SafetyError("AGENT_RESULT_BINDING_MISMATCH", code="AGENT_RESULT_BINDING_MISMATCH")

    @staticmethod
    def _subject_counts(observation) -> tuple[int | None, int | None, int | None, int | None]:
        statuses: dict[str, str] = {}
        for node in observation.nodes:
            if node.subject_id and node.subject_id != "project":
                statuses[node.subject_id] = str(node.status).upper()
        if not statuses:
            return None, None, None, None
        completed = sum(value in {"SUCCESS", "SUCCEEDED", "COMPLETED"} for value in statuses.values())
        failed = sum(value in {"FAILED", "ERROR"} for value in statuses.values())
        excluded = sum(value in {"SKIPPED", "EXCLUDED"} for value in statuses.values())
        return completed, failed, excluded, len(statuses)
