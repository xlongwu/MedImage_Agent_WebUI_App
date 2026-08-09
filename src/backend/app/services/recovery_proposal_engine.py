"""Pure deterministic recovery proposal engine with no execution dependencies."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from src.backend.app.core.exceptions import SafetyError
from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.runtime.node_contract_registry import validate_and_normalize_parameters
from src.backend.app.schemas.execution_ticket import ExecutionTicket
from src.backend.app.schemas.node_contract import NodeContract
from src.backend.app.schemas.recovery import (
    CanonicalDiffEntry,
    CanonicalRecoveryDiff,
    CheckpointEvidence,
    DiagnosisRecord,
    RecoveryCandidate,
    RecoveryChangeRequest,
    RecoveryExecutionSnapshot,
    RecoveryProposal,
    RecoveryQuotaDecision,
    RecoveryQuotaLimits,
    RecoveryQuotaSource,
    RecoveryQuotaUsage,
)

_DIMENSIONS = (
    "max_lifecycle_recovery_attempts",
    "max_node_attempts",
    "max_subject_node_attempts",
    "max_replans",
    "max_recovery_wall_seconds",
)

_DIFF_FIELDS = (
    "normalized_params",
    "node_ids",
    "contract_versions",
    "dag_dependencies",
    "backend_ids",
    "execution_backend_policy",
    "roots",
    "scope",
    "artifact_types",
    "output_policy",
    "goal_contract",
    "approval_context",
    "safe_allowlist",
)

_ACTION_PRIORITY = {
    "RETRY_FAILED_SUBJECTS": 0,
    "SAFE_RETRY": 1,
    "RESUME": 2,
    "PARAMETER_CHANGE": 3,
    "BACKEND_SWITCH": 4,
    "REPLAN": 5,
    "HUMAN_HANDOFF": 99,
}


def calculate_recovery_proposal_hash(
    record: RecoveryProposal | dict[str, object],
) -> str:
    payload = (
        record.model_dump(mode="json") if isinstance(record, RecoveryProposal) else dict(record)
    )
    payload.pop("recovery_proposal_hash", None)
    return stable_hash(payload)


def calculate_recovery_candidate_hash(
    candidate: RecoveryCandidate | dict[str, object],
) -> str:
    payload = (
        candidate.model_dump(mode="json")
        if isinstance(candidate, RecoveryCandidate)
        else dict(candidate)
    )
    payload.pop("candidate_hash", None)
    return stable_hash(payload)


def calculate_canonical_diff_hash(
    value: CanonicalRecoveryDiff | dict[str, object],
) -> str:
    payload = (
        value.model_dump(mode="json") if isinstance(value, CanonicalRecoveryDiff) else dict(value)
    )
    payload.pop("canonical_diff_hash", None)
    return stable_hash(payload)


def _limits_from(value: object) -> RecoveryQuotaLimits:
    if isinstance(value, RecoveryQuotaLimits):
        return value
    if value is None:
        return RecoveryQuotaLimits()
    if isinstance(value, dict):
        return RecoveryQuotaLimits(**value)
    values = {dimension: getattr(value, dimension, None) for dimension in _DIMENSIONS}
    return RecoveryQuotaLimits(**values)


def decide_recovery_quota(
    *,
    ticket: ExecutionTicket,
    node_contracts: Iterable[NodeContract],
    project_policy: RecoveryQuotaLimits | dict[str, int | None] | None,
    usage: RecoveryQuotaUsage,
) -> RecoveryQuotaDecision:
    sources = [
        RecoveryQuotaSource(
            source_type="ticket",
            source_id=ticket.execution_ticket_id,
            limits=_limits_from(ticket.retry_policy),
        ),
        RecoveryQuotaSource(
            source_type="project_policy",
            source_id=ticket.project_id,
            limits=_limits_from(project_policy),
        ),
    ]
    for contract in sorted(node_contracts, key=lambda item: item.node_id):
        sources.append(
            RecoveryQuotaSource(
                source_type="node_contract",
                source_id=f"{contract.node_id}@{contract.contract_version}",
                limits=_limits_from(contract.retry_policy),
            )
        )
    missing: list[str] = []
    effective: dict[str, int] = {}
    for dimension in _DIMENSIONS:
        values = []
        for source in sources:
            value = getattr(source.limits, dimension)
            if value is None:
                missing.append(f"{source.source_type}:{source.source_id}:{dimension}")
                values.append(0)
            else:
                values.append(value)
        effective[dimension] = min(values) if values else 0
    usage_by_limit = {
        "max_lifecycle_recovery_attempts": usage.lifecycle_recovery_attempts,
        "max_node_attempts": usage.node_attempts,
        "max_subject_node_attempts": usage.subject_node_attempts,
        "max_replans": usage.replans,
        "max_recovery_wall_seconds": usage.recovery_wall_seconds,
    }
    exhausted = sorted(
        dimension for dimension, limit in effective.items() if usage_by_limit[dimension] >= limit
    )
    reasons = []
    if missing:
        reasons.append("RECOVERY_QUOTA_DIMENSION_MISSING")
    if exhausted:
        reasons.append("RECOVERY_QUOTA_EXHAUSTED")
    return RecoveryQuotaDecision(
        sources=tuple(sources),
        effective_limits=effective,
        usage=usage,
        missing_dimensions=tuple(sorted(missing)),
        exhausted_dimensions=tuple(exhausted),
        executable=not missing and not exhausted,
        reason_codes=tuple(reasons),
    )


def build_execution_snapshot(
    *,
    plan: dict[str, object],
    ticket: ExecutionTicket,
    contracts: dict[str, NodeContract],
) -> RecoveryExecutionSnapshot:
    nodes = [node for node in plan.get("nodes", []) if isinstance(node, dict)]
    metadata = plan.get("metadata") if isinstance(plan.get("metadata"), dict) else {}
    params = {
        str(node.get("id")): dict(node.get("params") or {}) for node in nodes if node.get("id")
    }
    node_ids = tuple(sorted(params))
    backend_ids = tuple(
        sorted(
            (
                str(node.get("id")),
                str(
                    node.get("backend") or contracts.get(str(node.get("id")), None).backend
                    if contracts.get(str(node.get("id")))
                    else ""
                ),
            )
            for node in nodes
            if node.get("id")
        )
    )
    dag = tuple(
        sorted(
            (
                str(node.get("id")),
                tuple(sorted(str(item) for item in (node.get("depends_on") or []))),
            )
            for node in nodes
            if node.get("id")
        )
    )
    policy = {
        node_id: {
            key: params.get(node_id, {}).get(key)
            for key in ("precision", "device", "fallback_policy")
            if key in params.get(node_id, {})
        }
        for node_id in node_ids
    }
    artifacts = tuple(
        sorted(
            (
                node_id,
                tuple(sorted(artifact.artifact_type for artifact in contract.output_schema)),
            )
            for node_id, contract in contracts.items()
            if node_id in node_ids
        )
    )
    output_policy = tuple(
        sorted(
            (
                node_id,
                contract.idempotency.output_collision_policy,
                contract.idempotency.attempt_output_strategy,
            )
            for node_id, contract in contracts.items()
            if node_id in node_ids
        )
    )

    def scope_values(*keys: str) -> tuple[str, ...]:
        result = []
        for key in keys:
            value = metadata.get(key)
            if isinstance(value, list | tuple):
                result.extend(str(item) for item in value)
        return tuple(sorted(set(result)))

    return RecoveryExecutionSnapshot(
        normalized_params=params,
        node_ids=node_ids,
        contract_versions=tuple(sorted(ticket.contract_versions)),
        dag_dependencies=dag,
        backend_ids=backend_ids,
        execution_backend_policy=policy,
        input_roots=tuple(sorted(ticket.input_roots)),
        output_roots=tuple(sorted(ticket.output_roots)),
        readonly_roots=tuple(sorted(ticket.readonly_roots)),
        subject_scope=scope_values("subject_ids", "subjects"),
        session_scope=scope_values("session_ids", "sessions"),
        output_scope=scope_values("output_scope"),
        artifact_types=artifacts,
        output_policy=output_policy,
        goal_contract_hash=ticket.goal_contract_hash,
        approval_summary_hash=ticket.approval_summary_hash,
        allowlist_hash=ticket.allowlist_hash,
    )


def apply_change_request(
    original: RecoveryExecutionSnapshot,
    changes: RecoveryChangeRequest,
    contracts: dict[str, NodeContract],
) -> RecoveryExecutionSnapshot:
    payload = original.model_dump(mode="python")
    params = {key: dict(value) for key, value in original.normalized_params.items()}
    for node_id, patch in changes.parameter_patch.items():
        params.setdefault(node_id, {}).update(patch)
    payload["normalized_params"] = params
    backends = dict(original.backend_ids)
    backends.update(changes.backend_patch)
    payload["backend_ids"] = tuple(sorted(backends.items()))
    node_ids = changes.replacement_node_ids or original.node_ids
    payload["node_ids"] = tuple(sorted(node_ids))
    dag = dict(original.dag_dependencies)
    dag.update({key: tuple(sorted(value)) for key, value in changes.dag_patch.items()})
    payload["dag_dependencies"] = tuple(
        sorted((key, value) for key, value in dag.items() if key in node_ids)
    )
    for field in ("input_roots", "output_roots", "readonly_roots"):
        value = getattr(changes, field)
        if value is not None:
            payload[field] = tuple(sorted(value))
    for field in ("subject_scope", "session_scope", "output_scope"):
        value = getattr(changes, field)
        if value is not None:
            payload[field] = tuple(sorted(value))
    for field in ("goal_contract_hash", "approval_summary_hash", "allowlist_hash"):
        value = getattr(changes, field)
        if value is not None:
            payload[field] = value
    payload["artifact_types"] = tuple(
        sorted(
            (
                node_id,
                tuple(sorted(item.artifact_type for item in contracts[node_id].output_schema)),
            )
            for node_id in node_ids
            if node_id in contracts
        )
    )
    payload["output_policy"] = tuple(
        sorted(
            (
                node_id,
                contracts[node_id].idempotency.output_collision_policy,
                contracts[node_id].idempotency.attempt_output_strategy,
            )
            for node_id in node_ids
            if node_id in contracts
        )
    )
    payload["contract_versions"] = tuple(
        sorted(
            (node_id, contracts[node_id].contract_version)
            for node_id in node_ids
            if node_id in contracts
        )
    )
    return RecoveryExecutionSnapshot(**payload)


def canonical_recovery_diff(
    original: RecoveryExecutionSnapshot,
    candidate: RecoveryExecutionSnapshot,
) -> CanonicalRecoveryDiff:
    before = original.model_dump(mode="json")
    after = candidate.model_dump(mode="json")
    values = {
        "normalized_params": (before["normalized_params"], after["normalized_params"]),
        "node_ids": (before["node_ids"], after["node_ids"]),
        "contract_versions": (before["contract_versions"], after["contract_versions"]),
        "dag_dependencies": (before["dag_dependencies"], after["dag_dependencies"]),
        "backend_ids": (before["backend_ids"], after["backend_ids"]),
        "execution_backend_policy": (
            before["execution_backend_policy"],
            after["execution_backend_policy"],
        ),
        "roots": (
            {key: before[key] for key in ("input_roots", "output_roots", "readonly_roots")},
            {key: after[key] for key in ("input_roots", "output_roots", "readonly_roots")},
        ),
        "scope": (
            {key: before[key] for key in ("subject_scope", "session_scope", "output_scope")},
            {key: after[key] for key in ("subject_scope", "session_scope", "output_scope")},
        ),
        "artifact_types": (before["artifact_types"], after["artifact_types"]),
        "output_policy": (before["output_policy"], after["output_policy"]),
        "goal_contract": (before["goal_contract_hash"], after["goal_contract_hash"]),
        "approval_context": (before["approval_summary_hash"], after["approval_summary_hash"]),
        "safe_allowlist": (
            before["allowlist_hash"],
            after["allowlist_hash"],
        ),
    }
    entries = []
    for dimension in _DIFF_FIELDS:
        left, right = values[dimension]
        changed = stable_hash(left) != stable_hash(right)
        entries.append(
            CanonicalDiffEntry(
                dimension=dimension,
                before_hash=stable_hash(left),
                after_hash=stable_hash(right),
                changed=changed,
                classification="new_reviewed_plan" if changed else "unchanged",
                details={},
            )
        )
    diff = CanonicalRecoveryDiff(
        entries=tuple(entries),
        changes_reviewed_contract=any(entry.changed for entry in entries),
        canonical_diff_hash="pending",
    )
    return diff.model_copy(update={"canonical_diff_hash": calculate_canonical_diff_hash(diff)})


def _contains_rawdata_output(snapshot: RecoveryExecutionSnapshot) -> bool:
    return any(
        "rawdata" in {part.lower() for part in path.replace("\\", "/").split("/")}
        for path in snapshot.output_roots
    )


def _checkpoint_valid(
    checkpoint: CheckpointEvidence,
    original: RecoveryExecutionSnapshot,
    ticket: ExecutionTicket,
) -> bool:
    if not checkpoint.verified or checkpoint.plan_hash != ticket.plan_hash:
        return False
    if checkpoint.normalized_params_hash != ticket.normalized_params_hash:
        return False
    if set(checkpoint.backend_ids) != {value for _, value in original.backend_ids}:
        return False
    if set(checkpoint.input_roots) != set(original.input_roots) or set(
        checkpoint.output_roots
    ) != set(original.output_roots):
        return False
    remaining = set(checkpoint.remaining_node_ids)
    completed = set(checkpoint.completed_node_ids)
    if (
        not remaining
        or remaining & completed
        or not (remaining | completed).issubset(original.node_ids)
    ):
        return False
    dag = dict(original.dag_dependencies)
    return all(set(dag.get(node_id, ())).issubset(remaining | completed) for node_id in remaining)


class RecoveryProposalEngine:
    VERSION = "recovery-proposal-v1"

    def __init__(self, contract_resolver: Callable[[str], NodeContract]) -> None:
        self.contract_resolver = contract_resolver

    def propose(
        self,
        *,
        diagnosis: DiagnosisRecord,
        plan: dict[str, object],
        ticket: ExecutionTicket,
        project_policy: RecoveryQuotaLimits | dict[str, int | None] | None,
        usage: RecoveryQuotaUsage,
        changes: RecoveryChangeRequest | None = None,
        checkpoint: CheckpointEvidence | None = None,
        parent_recovery_proposal_id: str | None = None,
        created_at: datetime | None = None,
    ) -> RecoveryProposal:
        if (
            diagnosis.bindings.execution_ticket_id != ticket.execution_ticket_id
            or diagnosis.bindings.plan_hash != ticket.plan_hash
            or diagnosis.bindings.project_id != ticket.project_id
        ):
            raise SafetyError(
                "RECOVERY_PROPOSAL_BINDING_MISMATCH", code="RECOVERY_PROPOSAL_BINDING_MISMATCH"
            )
        node_ids = tuple(
            sorted(
                {fact.node_id for fact in diagnosis.facts if fact.node_id}
                or set(ticket.approved_node_ids)
            )
        )
        change_request = changes or RecoveryChangeRequest()
        proposed_node_ids = set(change_request.replacement_node_ids or ())
        proposed_node_ids.update(change_request.parameter_patch)
        proposed_node_ids.update(change_request.backend_patch)
        contracts: dict[str, NodeContract] = {}
        unknown_contracts = []
        for node_id in sorted(set(ticket.approved_node_ids) | set(node_ids) | proposed_node_ids):
            try:
                contracts[node_id] = self.contract_resolver(node_id)
            except (KeyError, SafetyError):
                unknown_contracts.append(node_id)
        original = build_execution_snapshot(plan=plan, ticket=ticket, contracts=contracts)
        same_diff = canonical_recovery_diff(original, original)
        quota = decide_recovery_quota(
            ticket=ticket,
            node_contracts=(contracts[node_id] for node_id in node_ids if node_id in contracts),
            project_policy=project_policy,
            usage=usage,
        )
        global_blocks = list(diagnosis.blocking_safety_issues)
        if diagnosis.root_cause_status == "unknown":
            global_blocks.append("ROOT_CAUSE_UNKNOWN")
        if unknown_contracts:
            global_blocks.append("NODE_CONTRACT_UNKNOWN:" + ",".join(sorted(unknown_contracts)))
        if not quota.executable:
            global_blocks.extend(quota.reason_codes)
        if _contains_rawdata_output(original):
            global_blocks.append("RAWDATA_OUTPUT_SCOPE_FORBIDDEN")
        if global_blocks:
            return self._handoff_only(
                diagnosis=diagnosis,
                quota=quota,
                diff=same_diff,
                reasons=tuple(sorted(set(global_blocks))),
                parent_recovery_proposal_id=parent_recovery_proposal_id,
                created_at=created_at,
            )

        changed = apply_change_request(original, change_request, contracts)
        changed_diff = canonical_recovery_diff(original, changed)
        candidates: list[RecoveryCandidate] = []
        facts = [fact for fact in diagnosis.facts if fact.node_id in node_ids]
        retryable = bool(facts) and all(fact.retryability == "retryable" for fact in facts)
        contracts_retryable = bool(node_ids) and all(
            contracts[node_id].retry_policy.retryable for node_id in node_ids
        )
        isolation_safe = all(
            contracts[node_id].idempotency.idempotent
            or contracts[node_id].idempotency.attempt_output_strategy == "isolated_subdirectory"
            for node_id in node_ids
        )
        failed_subjects = tuple(sorted({fact.subject_id for fact in facts if fact.subject_id}))
        safe_blocks = tuple(
            reason
            for reason, condition in (
                ("ERROR_NOT_CONTRACTED_RETRYABLE", not retryable),
                ("NODE_RETRY_NOT_SUPPORTED", not contracts_retryable),
                ("OUTPUT_COLLISION_POLICY_UNSAFE", not isolation_safe),
                ("REVIEWED_CONTRACT_CHANGED", changed_diff.changes_reviewed_contract),
                ("FAILED_SUBJECT_SCOPE_REQUIRES_SUBSET_ACTION", bool(failed_subjects)),
            )
            if condition
        )
        if facts:
            candidates.append(
                self._candidate(
                    action="SAFE_RETRY",
                    scope="nodes",
                    node_ids=node_ids,
                    subjects=(),
                    diff=same_diff,
                    risk="low",
                    idempotency="idempotent"
                    if all(contracts[node].idempotency.idempotent for node in node_ids)
                    else "isolated_output",
                    approval="explicit_retry_approval",
                    reasons=("SAFE_RETRY_EVALUATED", "QUOTA_AVAILABLE"),
                    blocked=safe_blocks,
                    eligible=not safe_blocks,
                    executable=not safe_blocks,
                )
            )
        if failed_subjects:
            subset_supported = all(
                contracts[node].retry_policy.supports_subject_subset for node in node_ids
            )
            subset_in_scope = set(failed_subjects).issubset(
                original.subject_scope or failed_subjects
            )
            subset_blocks = tuple(
                reason
                for reason, condition in (
                    ("ERROR_NOT_CONTRACTED_RETRYABLE", not retryable),
                    ("SUBJECT_SUBSET_CONTRACT_NOT_SUPPORTED", not subset_supported),
                    ("FAILED_SUBJECT_SCOPE_NOT_EXACT_SUBSET", not subset_in_scope),
                    ("SUCCESSFUL_OUTPUT_COLLISION_RISK", not isolation_safe),
                    ("REVIEWED_CONTRACT_CHANGED", changed_diff.changes_reviewed_contract),
                )
                if condition
            )
            candidates.append(
                self._candidate(
                    action="RETRY_FAILED_SUBJECTS",
                    scope="subjects",
                    node_ids=node_ids,
                    subjects=failed_subjects,
                    diff=same_diff,
                    risk="low",
                    idempotency="isolated_output",
                    approval="explicit_retry_approval",
                    reasons=("FAILED_SUBJECT_RETRY_EVALUATED",),
                    blocked=subset_blocks,
                    eligible=not subset_blocks,
                    executable=not subset_blocks,
                )
            )
        if checkpoint is not None:
            resume_supported = (
                not changed_diff.changes_reviewed_contract
                and all(
                    contracts[node].retry_policy.supports_resume
                    and contracts[node].retry_policy.checkpoint_schema == checkpoint.schema_id
                    for node in checkpoint.remaining_node_ids
                    if node in contracts
                )
                and set(checkpoint.remaining_node_ids).issubset(contracts)
            )
            checkpoint_ok = _checkpoint_valid(checkpoint, original, ticket)
            candidates.append(
                self._candidate(
                    action="RESUME",
                    scope="checkpoint",
                    node_ids=checkpoint.remaining_node_ids,
                    subjects=(),
                    checkpoint_id=checkpoint.checkpoint_id,
                    checkpoint_evidence=checkpoint,
                    diff=same_diff,
                    risk="low" if resume_supported and checkpoint_ok else "unknown",
                    idempotency="isolated_output",
                    approval="explicit_resume_approval",
                    reasons=("CHECKPOINT_PRESENT",),
                    blocked=tuple(
                        reason
                        for reason, condition in (
                            ("CHECKPOINT_NOT_VERIFIED_OR_BOUND", not checkpoint_ok),
                            ("RESUME_CONTRACT_NOT_SUPPORTED", not resume_supported),
                        )
                        if condition
                    ),
                    eligible=resume_supported and checkpoint_ok,
                    executable=resume_supported and checkpoint_ok,
                )
            )
        if change_request.parameter_patch:
            parameter_blocks = []
            for node_id, patch in change_request.parameter_patch.items():
                contract = contracts.get(node_id)
                if contract is None:
                    parameter_blocks.append(f"NODE_CONTRACT_UNKNOWN:{node_id}")
                    continue
                if not set(patch).issubset(contract.retry_policy.mutable_parameters_for_recovery):
                    parameter_blocks.append(f"PARAMETER_NOT_MUTABLE_FOR_RECOVERY:{node_id}")
                    continue
                _, _, errors = validate_and_normalize_parameters(
                    contract,
                    dict(changed.normalized_params.get(node_id, {})),
                )
                parameter_blocks.extend(
                    f"PARAMETER_CONTRACT_INVALID:{node_id}:{error}" for error in errors
                )
            parameter_cause = any(
                fact.category in {"PARAMETER_INVALID", "PARAMETER_CAUSED_GAP"}
                for fact in diagnosis.facts
            )
            if not parameter_cause:
                parameter_blocks.append("PARAMETER_CAUSE_NOT_ESTABLISHED")
            candidates.append(
                self._candidate(
                    action="PARAMETER_CHANGE",
                    scope="reviewed_plan",
                    node_ids=tuple(sorted(change_request.parameter_patch)),
                    subjects=(),
                    diff=changed_diff,
                    risk="medium",
                    idempotency="not_applicable",
                    approval="new_reviewed_plan_and_approval",
                    reasons=("PARAMETER_PATCH_REQUIRES_NEW_PLAN",),
                    blocked=tuple(sorted(parameter_blocks)),
                    eligible=not parameter_blocks,
                    executable=False,
                    parameter_patch=change_request.parameter_patch,
                    changes_reviewed_plan=True,
                    change_request=change_request,
                )
            )
        if change_request.backend_patch:
            backend_blocks = []
            for node_id, backend in change_request.backend_patch.items():
                contract = contracts.get(node_id)
                if contract is None or backend not in contract.retry_policy.backend_switch_targets:
                    backend_blocks.append(f"BACKEND_TARGET_NOT_CONTRACTED:{node_id}:{backend}")
                elif not contract.retry_policy.backend_scientific_equivalence.get(backend):
                    backend_blocks.append(f"BACKEND_EQUIVALENCE_UNPROVEN:{node_id}:{backend}")
            if not any(fact.category == "BACKEND_UNAVAILABLE" for fact in diagnosis.facts):
                backend_blocks.append("BACKEND_FAILURE_NOT_ESTABLISHED")
            backend_blocks.append("HIGH_RISK_REQUIRES_HUMAN_HANDOFF")
            candidates.append(
                self._candidate(
                    action="BACKEND_SWITCH",
                    scope="reviewed_plan",
                    node_ids=tuple(sorted(change_request.backend_patch)),
                    subjects=(),
                    diff=changed_diff,
                    risk="high",
                    idempotency="not_applicable",
                    approval="new_reviewed_plan_and_approval",
                    reasons=("BACKEND_SWITCH_REQUIRES_NEW_PLAN",),
                    blocked=tuple(sorted(set(backend_blocks))),
                    eligible=False,
                    executable=False,
                    backend_patch=change_request.backend_patch,
                    changes_reviewed_plan=True,
                    change_request=change_request,
                )
            )
        replan_dimensions = {entry.dimension for entry in changed_diff.entries if entry.changed} - {
            "normalized_params",
            "backend_ids",
            "execution_backend_policy",
        }
        if replan_dimensions:
            replan_blocks = []
            if _contains_rawdata_output(changed):
                replan_blocks.append("RAWDATA_OUTPUT_SCOPE_FORBIDDEN")
            candidates.append(
                self._candidate(
                    action="REPLAN",
                    scope="reviewed_plan",
                    node_ids=changed.node_ids,
                    subjects=changed.subject_scope,
                    diff=changed_diff,
                    risk="medium",
                    idempotency="not_applicable",
                    approval="new_reviewed_plan_and_approval",
                    reasons=(
                        "REVIEWED_CONTRACT_CHANGE",
                        *tuple(sorted(f"DIFF_{item.upper()}" for item in replan_dimensions)),
                    ),
                    blocked=tuple(replan_blocks),
                    eligible=not replan_blocks,
                    executable=False,
                    changes_reviewed_plan=True,
                    change_request=change_request,
                )
            )
        eligible = [candidate for candidate in candidates if candidate.eligible]
        if not eligible:
            candidates.append(
                self._handoff_candidate(
                    same_diff,
                    reasons=tuple(
                        sorted(
                            {
                                reason
                                for candidate in candidates
                                for reason in candidate.blocked_reasons
                            }
                            or {"NO_SAFE_RECOVERY_CANDIDATE"}
                        )
                    ),
                )
            )
            eligible = [candidates[-1]]
        candidates = sorted(candidates, key=lambda item: (item.rank_key, item.candidate_id))
        recommended = min(
            (candidate for candidate in candidates if candidate.eligible),
            key=lambda item: (item.rank_key, item.candidate_id),
        )
        return self._proposal(
            diagnosis=diagnosis,
            quota=quota,
            candidates=tuple(candidates),
            recommended_id=recommended.candidate_id,
            parent_recovery_proposal_id=parent_recovery_proposal_id,
            created_at=created_at,
        )

    def _candidate(
        self,
        *,
        action: str,
        scope: str,
        node_ids: Iterable[str],
        subjects: Iterable[str],
        diff: CanonicalRecoveryDiff,
        risk: str,
        idempotency: str,
        approval: str,
        reasons: tuple[str, ...],
        eligible: bool,
        executable: bool,
        blocked: tuple[str, ...] = (),
        checkpoint_id: str | None = None,
        checkpoint_evidence: CheckpointEvidence | None = None,
        parameter_patch: dict[str, dict[str, object]] | None = None,
        backend_patch: dict[str, str] | None = None,
        changes_reviewed_plan: bool = False,
        change_request: RecoveryChangeRequest | None = None,
        safe_human_actions: tuple[str, ...] = (),
    ) -> RecoveryCandidate:
        identity = stable_hash(
            {
                "action": action,
                "nodes": sorted(node_ids),
                "subjects": sorted(subjects),
                "diff": diff.canonical_diff_hash,
                "reasons": reasons,
                "blocked": blocked,
            }
        )
        candidate = RecoveryCandidate(
            candidate_id=f"candidate_{identity[:20]}",
            candidate_hash="pending",
            action=action,
            scope=scope,
            target_node_ids=tuple(sorted(set(node_ids))),
            target_subject_ids=tuple(sorted(set(subjects))),
            checkpoint_id=checkpoint_id,
            checkpoint_evidence=checkpoint_evidence,
            parameter_patch=parameter_patch or {},
            backend_patch=backend_patch or {},
            change_request=change_request,
            canonical_diff=diff,
            risk=risk,
            idempotency=idempotency,
            expected_evidence=("new_observation", "new_goal_evaluation"),
            approval_class=approval,
            reason_codes=reasons,
            blocked_reasons=blocked,
            safe_human_actions=safe_human_actions,
            eligible=eligible,
            executable=executable,
            changes_reviewed_plan=changes_reviewed_plan,
            rank_key=(0 if eligible else 1, _ACTION_PRIORITY[action]),
        )
        return candidate.model_copy(
            update={"candidate_hash": calculate_recovery_candidate_hash(candidate)}
        )

    def _handoff_candidate(
        self,
        diff: CanonicalRecoveryDiff,
        *,
        reasons: tuple[str, ...],
    ) -> RecoveryCandidate:
        return self._candidate(
            action="HUMAN_HANDOFF",
            scope="human",
            node_ids=(),
            subjects=(),
            diff=diff,
            risk="unknown",
            idempotency="not_applicable",
            approval="human_handoff",
            reasons=reasons,
            eligible=True,
            executable=False,
            safe_human_actions=(
                "inspect_recovery_evidence_and_audit_timeline",
                "review_remaining_goal_gaps",
                "verify_quota_or_create_a_new_reviewed_plan",
            ),
        )

    def _handoff_only(
        self,
        *,
        diagnosis: DiagnosisRecord,
        quota: RecoveryQuotaDecision,
        diff: CanonicalRecoveryDiff,
        reasons: tuple[str, ...],
        parent_recovery_proposal_id: str | None,
        created_at: datetime | None,
    ) -> RecoveryProposal:
        candidate = self._handoff_candidate(diff, reasons=reasons)
        return self._proposal(
            diagnosis=diagnosis,
            quota=quota,
            candidates=(candidate,),
            recommended_id=candidate.candidate_id,
            parent_recovery_proposal_id=parent_recovery_proposal_id,
            created_at=created_at,
        )

    def _proposal(
        self,
        *,
        diagnosis: DiagnosisRecord,
        quota: RecoveryQuotaDecision,
        candidates: tuple[RecoveryCandidate, ...],
        recommended_id: str,
        parent_recovery_proposal_id: str | None,
        created_at: datetime | None,
    ) -> RecoveryProposal:
        identity = stable_hash(
            {
                "bindings": diagnosis.bindings.model_dump(mode="json"),
                "diagnosis_hash": diagnosis.diagnosis_hash,
                "quota": quota.model_dump(mode="json"),
                "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
                "parent": parent_recovery_proposal_id,
            }
        )
        proposal = RecoveryProposal(
            recovery_proposal_id=f"recovery_proposal_{identity[:20]}",
            engine_version=self.VERSION,
            bindings=diagnosis.bindings,
            diagnosis_id=diagnosis.diagnosis_id,
            diagnosis_hash=diagnosis.diagnosis_hash,
            created_at=created_at or datetime.now(UTC),
            parent_recovery_proposal_id=parent_recovery_proposal_id,
            quota=quota,
            candidates=candidates,
            recommended_candidate_id=recommended_id,
            recovery_proposal_hash="pending",
        )
        return proposal.model_copy(
            update={"recovery_proposal_hash": calculate_recovery_proposal_hash(proposal)}
        )
