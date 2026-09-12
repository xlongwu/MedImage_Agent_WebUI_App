"""Build structured, non-executable recovery diagnosis from immutable evidence."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from functools import lru_cache

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.schemas.execution_ticket import ExecutionTicket
from src.backend.app.schemas.goal_contract import GoalEvaluationRecord
from src.backend.app.schemas.node_contract import NodeContract
from src.backend.app.schemas.observation import ObservationRecord
from src.backend.app.schemas.recovery import (
    DiagnosisFact,
    DiagnosisRecord,
    GoalGap,
    RecoveryBindings,
)

_ERROR_CODE = re.compile(r"^([A-Z][A-Z0-9_]{2,})(?::|\b)")


def _default_kb_classifier(message: str) -> dict:
    from src.backend.app.tools.error_classifier import classify_error

    return classify_error(message)


def calculate_diagnosis_hash(record: DiagnosisRecord | dict[str, object]) -> str:
    payload = record.model_dump(mode="json") if isinstance(record, DiagnosisRecord) else dict(record)
    payload.pop("diagnosis_hash", None)
    return stable_hash(payload)


def _bindings(
    observation: ObservationRecord,
    evaluation: GoalEvaluationRecord,
    ticket: ExecutionTicket,
) -> RecoveryBindings:
    binding = observation.bindings
    mismatches = []
    for label, left, right in (
        ("project", binding.project_id, evaluation.project_id),
        ("lifecycle", binding.lifecycle_id, evaluation.lifecycle_id),
        ("reviewed_plan", binding.reviewed_plan_id, evaluation.reviewed_plan_id),
        ("plan_hash", binding.plan_hash, evaluation.plan_hash),
        ("observation", observation.observation_id, evaluation.observation_id),
        ("observation_hash", observation.observation_hash, evaluation.observation_hash),
        ("goal_contract", binding.goal_contract_id, evaluation.goal_contract_id),
        ("goal_contract_hash", binding.goal_contract_hash, evaluation.goal_contract_hash),
        ("ticket", binding.execution_ticket_id, ticket.execution_ticket_id),
        ("ticket_plan", binding.plan_hash, ticket.plan_hash),
    ):
        if left != right:
            mismatches.append(label)
    if mismatches:
        raise SafetyError(
            "RECOVERY_EVIDENCE_BINDING_MISMATCH: " + ",".join(mismatches),
            code="RECOVERY_EVIDENCE_BINDING_MISMATCH",
        )
    return RecoveryBindings(
        project_id=binding.project_id,
        lifecycle_id=binding.lifecycle_id,
        reviewed_plan_id=binding.reviewed_plan_id,
        plan_hash=binding.plan_hash,
        execution_ticket_id=binding.execution_ticket_id,
        run_id=binding.run_id,
        goal_contract_id=binding.goal_contract_id or evaluation.goal_contract_id,
        goal_contract_hash=binding.goal_contract_hash or evaluation.goal_contract_hash,
        observation_id=observation.observation_id,
        observation_hash=observation.observation_hash,
        goal_evaluation_id=evaluation.goal_evaluation_id,
        goal_evaluation_hash=evaluation.goal_evaluation_hash,
    )


def _category(message: str, default: str) -> str:
    match = _ERROR_CODE.match(message.strip())
    return match.group(1) if match else default


def _retryability(category: str, contract: NodeContract | None) -> str:
    if contract is None:
        return "unknown"
    policy = contract.retry_policy
    if category in policy.non_retryable_error_classes:
        return "non_retryable"
    if policy.retryable and category in policy.retryable_error_classes:
        return "retryable"
    return "unknown"


class RunDiagnosisService:
    VERSION = "run-diagnosis-v1"

    def __init__(
        self,
        contract_resolver: Callable[[str], NodeContract],
        error_kb_classifier: Callable[[str], dict] | None = None,
    ) -> None:
        self.contract_resolver = contract_resolver
        # The curated ERROR_KB supplements contract error classes: when a node
        # contract cannot classify a failure, the KB's retryable flag decides
        # the fact's retryability instead of leaving it "unknown" (which would
        # force root_cause_status=unknown and a human handoff downstream).
        self._classify = lru_cache(maxsize=256)(
            lambda message: (error_kb_classifier or _default_kb_classifier)(message)
        )

    def build(
        self,
        *,
        observation: ObservationRecord,
        evaluation: GoalEvaluationRecord,
        ticket: ExecutionTicket,
        created_at: datetime | None = None,
    ) -> DiagnosisRecord:
        bindings = _bindings(observation, evaluation, ticket)
        facts: list[DiagnosisFact] = []
        blocking = list(observation.completeness.conflicts)
        blocking.extend(observation.completeness.blocking_facts)

        for index, conflict in enumerate(observation.completeness.conflicts, start=1):
            facts.append(
                DiagnosisFact(
                    fact_id=f"conflict-{index:03d}",
                    category="EVIDENCE_CONFLICT",
                    scope="project",
                    severity="blocking",
                    evidence_ids=tuple(source.source_id for source in observation.sources),
                    confidence_source="explicit_state",
                    retryability="non_retryable",
                    message=conflict,
                )
            )
        for index, item in enumerate(observation.completeness.blocking_facts, start=1):
            facts.append(
                DiagnosisFact(
                    fact_id=f"safety-{index:03d}",
                    category="SAFETY_POLICY_BLOCKED",
                    scope="project",
                    severity="blocking",
                    confidence_source="explicit_state",
                    retryability="non_retryable",
                    message=item,
                )
            )

        for node in sorted(
            observation.nodes,
            key=lambda item: (item.node_id, item.subject_id, item.session_id or ""),
        ):
            if node.status.upper() not in {"FAILED", "ERROR", "TIMEOUT", "INTERRUPTED"} and not node.errors:
                continue
            try:
                contract = self.contract_resolver(node.node_id)
            except (KeyError, SafetyError):
                contract = None
                blocking.append(f"NODE_CONTRACT_UNKNOWN:{node.node_id}")
            messages = node.errors or (f"NODE_FAILED: status={node.status}",)
            for offset, message in enumerate(messages, start=1):
                category = _category(message, "NODE_FAILED")
                retryability = _retryability(category, contract)
                confidence_source = "contract_rule" if retryability != "unknown" else "explicit_state"
                if retryability == "unknown":
                    kb_match = self._classify(message)
                    if kb_match.get("classified"):
                        retryability = (
                            "retryable" if kb_match.get("retryable") else "non_retryable"
                        )
                        confidence_source = "error_kb"
                facts.append(
                    DiagnosisFact(
                        fact_id=f"node-{len(facts) + 1:03d}-{offset:02d}",
                        category=category,
                        scope="subject" if node.subject_id != "project" else "node",
                        severity="error",
                        node_id=node.node_id,
                        subject_id=None if node.subject_id == "project" else node.subject_id,
                        session_id=node.session_id,
                        evidence_ids=node.evidence_ids,
                        confidence_source=confidence_source,
                        retryability=retryability,
                        message=message,
                    )
                )

        for validation in observation.validations:
            if validation.status not in {"failed", "warning"}:
                continue
            facts.append(
                DiagnosisFact(
                    fact_id=f"validation-{len(facts) + 1:03d}",
                    category="VALIDATION_FAILED",
                    scope="validation",
                    severity="error" if validation.status == "failed" else "warning",
                    evidence_ids=validation.evidence_ids,
                    confidence_source="validator",
                    retryability="non_retryable" if validation.status == "failed" else "unknown",
                    message="; ".join(validation.blocking_issues) or validation.validation_id,
                )
            )

        gaps = tuple(
            GoalGap(
                criterion_id=result.criterion_id,
                criterion_type=result.criterion_type,
                status=result.status,
                reason_code=result.reason_code,
                expected=result.expected,
                actual=result.actual,
                evidence_ids=result.evidence_ids,
                affected_nodes=result.affected_nodes,
                affected_subjects=result.affected_subjects,
                affected_artifacts=result.affected_artifacts,
            )
            for result in evaluation.criterion_results
            if result.status in {"failed", "indeterminate"}
        )
        known = any(fact.retryability != "unknown" for fact in facts)
        unknown_failure = any(
            fact.severity in {"error", "blocking"} and fact.retryability == "unknown"
            for fact in facts
        )
        root_status = (
            "unknown"
            if blocking or unknown_failure or not (facts or gaps)
            else "known"
            if known
            else "probable"
        )
        identity_payload = {
            "bindings": bindings.model_dump(mode="json"),
            "facts": [fact.model_dump(mode="json") for fact in facts],
            "goal_gaps": [gap.model_dump(mode="json") for gap in gaps],
            "root_cause_status": root_status,
            "blocking_safety_issues": sorted(set(blocking)),
        }
        record = DiagnosisRecord(
            diagnosis_id=f"diagnosis_{stable_hash(identity_payload)[:24]}",
            diagnoser_version=self.VERSION,
            bindings=bindings,
            created_at=created_at or datetime.now(UTC),
            facts=tuple(facts),
            goal_gaps=gaps,
            root_cause_status=root_status,
            blocking_safety_issues=tuple(sorted(set(blocking))),
            diagnosis_hash="pending",
        )
        return record.model_copy(update={"diagnosis_hash": calculate_diagnosis_hash(record)})


def adapt_legacy_diagnosis(
    *,
    legacy: dict[str, object],
    bindings: RecoveryBindings,
    created_at: datetime | None = None,
) -> DiagnosisRecord:
    """Read-only compatibility adapter; legacy retry steps convey no authority."""
    facts = []
    for index, issue in enumerate(legacy.get("issues", []) if isinstance(legacy.get("issues"), list) else [], start=1):
        if not isinstance(issue, dict):
            continue
        facts.append(
            DiagnosisFact(
                fact_id=f"legacy-{index:03d}",
                category=str(issue.get("category") or "LEGACY_UNKNOWN_ERROR"),
                scope="subject" if issue.get("subject_id") else "node",
                severity="error",
                node_id=str(issue.get("node") or "") or None,
                subject_id=str(issue.get("subject_id") or "") or None,
                confidence_source="legacy_classifier",
                retryability="unknown",
                message=str(issue.get("message") or "Legacy advisory issue"),
            )
        )
    blocking = ("LEGACY_ADVISORY_NOT_EXECUTION_AUTHORITY",)
    identity_payload = {
        "bindings": bindings.model_dump(mode="json"),
        "facts": [fact.model_dump(mode="json") for fact in facts],
        "legacy_source_ref": str(legacy.get("diagnosis_path") or "legacy-diagnosis"),
    }
    record = DiagnosisRecord(
        diagnosis_id=f"diagnosis_legacy_{stable_hash(identity_payload)[:17]}",
        bindings=bindings,
        created_at=created_at or datetime.now(UTC),
        facts=tuple(facts),
        goal_gaps=(),
        root_cause_status="unknown",
        blocking_safety_issues=blocking,
        legacy_source_ref=str(legacy.get("diagnosis_path") or "legacy-diagnosis"),
        diagnosis_hash="pending",
    )
    return record.model_copy(update={"diagnosis_hash": calculate_diagnosis_hash(record)})
