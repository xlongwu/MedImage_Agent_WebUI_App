"""Deterministic candidate builder for review-time Goal Contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.schemas.goal_contract import (
    GoalContract,
    GoalCriterion,
    GoalScope,
)
from src.backend.app.schemas.node_contract import CapabilityLevel

_METADATA_GOAL_NODES = {
    "contract_smoke",
    "create_synthetic_bids",
    "data_inspection",
    "dataset_evaluation",
    "environment_check",
    "native_preproc_full_dry_run",
}

_NATIVE_STAGE_ARTIFACT_TYPES = {
    "nuisance_regression": "residual_bold",
    "temporal_filtering": "filtered_bold",
    "alff": "alff_map",
    "falff": "falff_map",
    "reho": "reho_map",
    "functional_connectivity": "fc_matrix",
}


@dataclass(frozen=True)
class GoalContractBuildResult:
    ok: bool
    clarification_required: bool
    reason: str | None = None
    semantics: dict[str, Any] | None = None


def _node_ids(plan: dict[str, Any]) -> tuple[str, ...]:
    nodes = plan.get("nodes")
    if not isinstance(nodes, list):
        return ()
    return tuple(
        str(node.get("id"))
        for node in nodes
        if isinstance(node, dict) and node.get("id")
    )


def _scope(plan: dict[str, Any]) -> GoalScope:
    metadata = plan.get("metadata") if isinstance(plan.get("metadata"), dict) else {}
    subjects: list[str] = []
    sessions: list[str] = []
    for key, target in (
        ("subject_ids", subjects),
        ("subjects", subjects),
        ("subject_scope", subjects),
        ("session_ids", sessions),
        ("sessions", sessions),
        ("session_scope", sessions),
    ):
        value = metadata.get(key)
        if isinstance(value, list):
            target.extend(str(item) for item in value if str(item))
    for key, target in (("subject_id", subjects), ("session_id", sessions)):
        value = metadata.get(key)
        if isinstance(value, str) and value:
            target.append(value)
    for node in plan.get("nodes", []):
        if not isinstance(node, dict) or not isinstance(node.get("params"), dict):
            continue
        params = node["params"]
        for key, target in (
            ("subject_ids", subjects),
            ("subjects", subjects),
            ("session_ids", sessions),
            ("sessions", sessions),
        ):
            value = params.get(key)
            if isinstance(value, list):
                target.extend(str(item) for item in value if str(item))
        for key, target in (("subject_id", subjects), ("session_id", sessions)):
            value = params.get(key)
            if isinstance(value, str) and value:
                target.append(value)
    return GoalScope(
        subject_ids=tuple(sorted(set(subjects))),
        session_ids=tuple(sorted(set(sessions))),
        completeness_required=True,
    )


def _criterion(
    *,
    kind: str,
    target: str,
    index: int,
    required_evidence: tuple[str, ...],
    expected: dict[str, Any] | None = None,
    failure_semantics: str = "strict",
) -> GoalCriterion:
    identity = stable_hash({"kind": kind, "target": target, "index": index})[:16]
    return GoalCriterion(
        criterion_id=f"criterion_{identity}",
        criterion_type=kind,
        target=target,
        quantifier="all",
        severity="required",
        required_evidence=required_evidence,
        expected=expected or {},
        failure_semantics=failure_semantics,
    )


def build_goal_contract_semantics(
    plan: dict[str, Any],
    goal_text: str | None,
) -> GoalContractBuildResult:
    goal = str(goal_text or "").strip()
    nodes = _node_ids(plan)
    if not goal:
        return GoalContractBuildResult(
            ok=False,
            clarification_required=True,
            reason="GOAL_TEXT_REQUIRED",
        )
    if not nodes:
        return GoalContractBuildResult(
            ok=False,
            clarification_required=True,
            reason="GOAL_PLAN_NODES_REQUIRED",
        )

    node_set = set(nodes)
    plan_metadata = plan.get("metadata") if isinstance(plan.get("metadata"), dict) else {}
    plan_only = (
        plan_metadata.get("plan_only") is True
        and plan_metadata.get("execution_enabled") is False
        and plan_metadata.get("capability_level") == "metadata_only"
    )
    artifact_types: tuple[str, ...] = ()
    minimum: CapabilityLevel
    requested_goal_kind = str(plan_metadata.get("goal_kind") or "")
    requested_artifacts = plan_metadata.get("goal_artifact_types")
    native_goal_artifacts = {
        "acpc_alignment": {"acpc_t1w", "transform_matrix", "acpc_landmarks", "qc_json"},
        "reho": {"reho_map"},
        "alff_falff": {"alff_map", "falff_map"},
        "functional_connectivity": {"fc_matrix"},
    }
    requested_artifact_set = (
        {str(item) for item in requested_artifacts}
        if isinstance(requested_artifacts, list)
        else set()
    )
    trusted_native_goal = (
        ("native_preproc_full_execute" in node_set or "native_auto_acpc_align" in node_set)
        and requested_goal_kind in native_goal_artifacts
        and requested_artifact_set == native_goal_artifacts.get(requested_goal_kind)
    )
    if plan_only:
        goal_kind = "rsfmri_preprocessing_plan"
        minimum = "metadata_only"
    elif trusted_native_goal:
        goal_kind = requested_goal_kind
        artifact_types = tuple(str(item) for item in requested_artifacts)
        minimum = "computed"
    elif "functional_connectivity_subject" in node_set:
        goal_kind = "functional_connectivity"
        artifact_types = ("fc_matrix",)
        minimum = "computed"
    elif "alff_falff_subject" in node_set:
        goal_kind = "alff_falff"
        artifact_types = ("alff_map", "falff_map")
        minimum = "computed"
    elif "reho_subject" in node_set:
        goal_kind = "reho"
        artifact_types = ("reho_map",)
        minimum = "computed"
    elif "native_preproc_full_execute" in node_set:
        goal_kind = "native_full_preprocessing"
        native_node = next(
            (
                node
                for node in plan.get("nodes", [])
                if isinstance(node, dict)
                and node.get("id") == "native_preproc_full_execute"
            ),
            None,
        )
        native_params = (
            native_node.get("params")
            if isinstance(native_node, dict)
            and isinstance(native_node.get("params"), dict)
            else {}
        )
        stage_overrides = native_params.get("stage_overrides")
        if isinstance(stage_overrides, dict) and any(
            stage_id in stage_overrides
            for stage_id in _NATIVE_STAGE_ARTIFACT_TYPES
        ):
            artifact_types = tuple(
                artifact_type
                for stage_id, artifact_type in _NATIVE_STAGE_ARTIFACT_TYPES.items()
                if stage_overrides.get(stage_id) is True
            )
        else:
            # Plans created before reviewed stage scoping did not persist a
            # complete override map. Preserve their historical contract rather
            # than silently weakening already-reviewed expectations.
            artifact_types = tuple(_NATIVE_STAGE_ARTIFACT_TYPES.values())
        minimum = "computed" if artifact_types else "metadata_only"
    elif node_set.issubset(_METADATA_GOAL_NODES):
        goal_kind = "contract_smoke"
        minimum = "metadata_only"
    else:
        return GoalContractBuildResult(
            ok=False,
            clarification_required=True,
            reason="GOAL_KIND_UNSUPPORTED_OR_AMBIGUOUS",
        )

    criteria: list[GoalCriterion] = []
    allowed_limitation_flags = (
        ["simplified"]
        if not plan_only and ("native_preproc_full_execute" in node_set or "native_auto_acpc_align" in node_set)
        else []
    )
    forbidden_limitation_flags = [
        flag
        for flag in ("simplified", "preview_only", "partial")
        if flag not in allowed_limitation_flags
    ]
    if not plan_only:
        criteria.extend(
            [
                _criterion(
                    kind="pipeline_terminal",
                    target="pipeline",
                    index=0,
                    required_evidence=("pipeline_summary", "node_states"),
                    expected={"statuses": ["SUCCESS", "COMPLETED"], "active_nodes": 0},
                    failure_semantics="indeterminate_if_source_incomplete",
                ),
                _criterion(
                    kind="node_status",
                    target="required_nodes",
                    index=1,
                    required_evidence=("node_states",),
                    expected={"node_ids": list(nodes), "statuses": ["SUCCESS", "COMPLETED"]},
                    failure_semantics="indeterminate_if_source_incomplete",
                ),
            ]
        )
    index = len(criteria)
    for artifact_type in artifact_types:
        for criterion_type in (
            "artifact_present",
            "artifact_reloadable",
            "artifact_registered",
        ):
            criteria.append(
                _criterion(
                    kind=criterion_type,
                    target=artifact_type,
                    index=index,
                    required_evidence=("artifact_discovery", "artifact_registry"),
                    expected={"artifact_type": artifact_type},
                    failure_semantics="indeterminate_if_source_incomplete",
                )
            )
            index += 1
    if artifact_types and not plan_only:
        criteria.extend(
            [
                _criterion(
                    kind="validation_passed",
                    target="scientific_validation",
                    index=index,
                    required_evidence=("validation",),
                    expected={"blocking_issues": 0},
                    failure_semantics="indeterminate_if_source_incomplete",
                ),
                _criterion(
                    kind="scope_complete",
                    target="reviewed_scope",
                    index=index + 1,
                    required_evidence=("node_states", "artifact_registry"),
                    expected={"artifact_types": list(artifact_types)},
                    failure_semantics="indeterminate_if_source_incomplete",
                ),
            ]
        )
        index += 2
    criteria.append(
        _criterion(
            kind="capability_at_least",
            target=minimum,
            index=index,
            required_evidence=(("reviewed_plan",) if plan_only else ("node_contract", "artifact_discovery")),
            expected={"minimum": minimum},
            failure_semantics="indeterminate_if_source_incomplete",
        )
    )
    if not plan_only:
        criteria.extend(
            [
                _criterion(
                    kind="scientific_status_allowed",
                    target="scientific_status",
                    index=index + 1,
                    required_evidence=("artifact_registry", "validation"),
                    expected={
                        "minimum": minimum,
                        "forbidden_limitation_flags": forbidden_limitation_flags,
                    },
                    failure_semantics="indeterminate_if_source_incomplete",
                ),
                _criterion(
                    kind="no_blocking_issue",
                    target="observation",
                    index=index + 2,
                    required_evidence=("pipeline_summary", "node_states", "artifact_discovery"),
                    expected={"blocking_facts": 0},
                ),
            ]
        )
    semantics = {
        "schema_version": 1,
        "goal_text": goal,
        "goal_kind": goal_kind,
        "scope": _scope(plan).model_dump(mode="json"),
        "criteria": [criterion.model_dump(mode="json") for criterion in criteria],
        "minimum_capability_level": minimum,
        "allowed_limitation_flags": allowed_limitation_flags,
        "forbidden_limitation_flags": forbidden_limitation_flags,
        "evaluation_policy_version": "goal-evaluator-v1",
        "builder_source": "deterministic_contract_builder",
        "warnings": [],
    }
    return GoalContractBuildResult(
        ok=True,
        clarification_required=False,
        semantics=semantics,
    )


def goal_contract_identity_payload(semantics: dict[str, Any]) -> dict[str, Any]:
    """Return the semantic fields that participate in reviewed-plan identity."""
    return {
        key: semantics[key]
        for key in (
            "schema_version",
            "goal_text",
            "goal_kind",
            "scope",
            "criteria",
            "minimum_capability_level",
            "allowed_limitation_flags",
            "forbidden_limitation_flags",
            "evaluation_policy_version",
        )
    }


def finalize_goal_contract(
    *,
    semantics: dict[str, Any],
    project_id: str,
    reviewed_plan_id: str,
    plan_hash: str,
    reviewed_actor: str | None = None,
    reviewed_at: datetime | None = None,
) -> GoalContract:
    semantic_payload = goal_contract_identity_payload(semantics)
    identity = stable_hash(
        {
            "project_id": project_id,
            "reviewed_plan_id": reviewed_plan_id,
            "plan_hash": plan_hash,
            "semantics": semantic_payload,
        }
    )
    payload = {
        **semantics,
        "goal_contract_id": f"goal_contract_{identity[:20]}",
        "project_id": project_id,
        "reviewed_plan_id": reviewed_plan_id,
        "plan_hash": plan_hash,
        "reviewed_actor": reviewed_actor,
        "reviewed_at": reviewed_at,
        "goal_contract_hash": "pending",
    }
    contract = GoalContract(**payload)
    canonical = contract.model_dump(mode="json")
    canonical.pop("goal_contract_hash", None)
    return contract.model_copy(update={"goal_contract_hash": stable_hash(canonical)})
