"""Persist stable reviewed plans and link each real-project execution."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.planner.goal_contract_builder import (
    build_goal_contract_semantics,
    finalize_goal_contract,
    goal_contract_identity_payload,
)
from src.backend.app.planner.plan_validator import (
    validate_goal_contract_reachability,
    validate_plan,
)
from src.backend.app.planner.project_context import (
    ProjectContext,
    ProjectContextError,
    load_project_context,
    validate_plan_project_context,
)
from src.backend.app.schemas.desktop import ReviewedPlanRecord, RunLinkRecord
from src.backend.app.schemas.goal_contract import GoalContract, GoalContractCandidate
from src.backend.app.schemas.memory import MemoryContext
from src.backend.app.schemas.planner_provenance import PlannerEvidence, PlannerInvocation
from src.backend.app.schemas.planning import PlanningRequest
from src.backend.app.runtime.atomic_file import atomic_write_json
from src.backend.app.services.mock_store import mock_store, utc_now_iso


class ReviewedPlanStoreError(ValueError):
    """Raised when a reviewed plan cannot be persisted or linked safely."""


def normalize_reviewed_plan(plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the contract-normalized plan and its validation evidence."""
    result = validate_plan(plan)
    normalized = result.normalized_plan if result.ok and result.normalized_plan else plan
    return normalized, result.to_dict()


def reviewed_plan_identity(
    project_id: str,
    plan: dict[str, Any],
    goal_contract_semantics: dict[str, Any] | None = None,
    memory_context: MemoryContext | dict[str, Any] | None = None,
    planner_invocation: PlannerInvocation | None = None,
    planner_evidence: PlannerEvidence | None = None,
    planning_inputs_hash: str | None = None,
) -> tuple[str, str]:
    normalized, _ = normalize_reviewed_plan(plan)
    identity_payload: dict[str, Any] = {"plan": normalized}
    if goal_contract_semantics is not None:
        identity_payload["goal_contract"] = goal_contract_identity_payload(
            goal_contract_semantics
        )
    if memory_context is not None:
        identity_payload["memory_context"] = (
            memory_context.model_dump(mode="json")
            if isinstance(memory_context, MemoryContext)
            else dict(memory_context)
        )
    if planner_invocation is not None and planner_evidence is not None:
        identity_payload["planner_provenance"] = {
            "provider_id": planner_invocation.provider_id,
            "model_id": planner_invocation.model_id,
            "prompt_template_version": planner_invocation.prompt_template_version,
            "prompt_template_hash": planner_invocation.prompt_template_hash,
            "input_schema_version": planner_invocation.input_schema_version,
            "input_hash": planner_invocation.input_hash,
            "output_hash": planner_evidence.output_hash,
            "validation_codes": list(planner_evidence.validation_codes),
            "fallback_used": planner_evidence.fallback_used,
        }
    if planning_inputs_hash is not None:
        identity_payload["planning_inputs_hash"] = planning_inputs_hash
    plan_hash = stable_hash(
        identity_payload
        if (
            goal_contract_semantics is not None
            or memory_context is not None
            or planner_invocation is not None
        )
        else normalized
    )
    identity_hash = stable_hash({"project_id": project_id, "plan_hash": plan_hash})
    return f"reviewed_{identity_hash[:20]}", plan_hash


def new_run_identity() -> tuple[str, str]:
    return f"runlink_{uuid4().hex[:20]}", f"run_{uuid4().hex[:20]}"


def _project_dir(project_id: str, store=None) -> Path:
    project_store = store or mock_store
    project = project_store.get_project(project_id)
    if project is None:
        raise ReviewedPlanStoreError(f"PROJECT_NOT_FOUND: {project_id}")
    value = project.metadata.get("project_dir")
    if not isinstance(value, str) or not value.strip():
        raise ReviewedPlanStoreError(
            "PROJECT_DIR_REQUIRED: persisted project metadata has no project_dir"
        )
    path = Path(value).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise ReviewedPlanStoreError(f"PROJECT_DIR_INVALID: {path}")
    return path


def _snapshot_path(project_dir: Path, reviewed_plan_id: str) -> Path:
    plans_dir = (project_dir / "plans").resolve()
    try:
        plans_dir.relative_to(project_dir)
    except ValueError as exc:
        raise ReviewedPlanStoreError("PLAN_PATH_INVALID: plans directory escapes project") from exc
    return plans_dir / f"{reviewed_plan_id}.json"


def write_reviewed_plan_snapshot(record: ReviewedPlanRecord, project_dir: Path) -> Path:
    """Write the first immutable project-local snapshot for a reviewed plan."""
    target = _snapshot_path(project_dir, record.reviewed_plan_id)
    if target.exists():
        return target
    atomic_write_json(target, record.model_dump(mode="json"), schema_version=1)
    return target


def save_reviewed_plan(
    *,
    project_id: str,
    project_config_path: str | None,
    plan: dict[str, Any],
    validation: dict[str, Any] | None = None,
    goal: str | None = None,
    provider: str | None = None,
    status: str = "REVIEWED",
    warnings: list[str] | None = None,
    goal_contract_candidate: GoalContractCandidate | dict[str, Any] | None = None,
    reviewed_actor: str | None = None,
    lineage: dict[str, Any] | None = None,
    memory_context: MemoryContext | dict[str, Any] | None = None,
    planner_invocation: PlannerInvocation | dict[str, Any] | None = None,
    planner_evidence: PlannerEvidence | dict[str, Any] | None = None,
    planning_request: PlanningRequest | None = None,
    store=None,
) -> ReviewedPlanRecord:
    """Upsert a stable SQLite plan index and write its immutable snapshot."""
    try:
        context = load_project_context(project_id, project_config_path, store=store)
    except ProjectContextError as exc:
        raise ReviewedPlanStoreError(str(exc)) from exc
    context_errors = validate_plan_project_context(plan, context)
    if context_errors:
        raise ReviewedPlanStoreError("; ".join(context_errors))

    normalized_plan, contract_validation = normalize_reviewed_plan(plan)
    project_store = store or mock_store
    project_dir = _project_dir(project_id, store=project_store)
    candidate: GoalContractCandidate | None = None
    if goal_contract_candidate is not None:
        try:
            candidate = (
                goal_contract_candidate
                if isinstance(goal_contract_candidate, GoalContractCandidate)
                else GoalContractCandidate(**goal_contract_candidate)
            )
        except Exception as exc:
            raise ReviewedPlanStoreError(
                "GOAL_CONTRACT_CANDIDATE_INVALID: review-time contract is invalid"
            ) from exc
        if goal and goal.strip() != candidate.goal_text.strip():
            raise ReviewedPlanStoreError(
                "GOAL_TEXT_MISMATCH: goal and reviewed Goal Contract disagree"
            )
        goal = candidate.goal_text
    goal_build = build_goal_contract_semantics(normalized_plan, goal)
    # The deterministic builder produces a review candidate only.  A caller
    # must explicitly return that candidate as reviewed input before it can be
    # frozen into plan identity or authorize execution.
    goal_semantics = candidate.semantics() if candidate is not None else None
    identity_semantics = goal_semantics or {
        "schema_version": 1,
        "goal_text": str(goal or ""),
        "goal_kind": "needs_goal_review",
        "scope": {"subject_ids": [], "session_ids": [], "include": [], "exclude": [], "completeness_required": True},
        "criteria": [],
        "minimum_capability_level": "unavailable",
        "allowed_limitation_flags": [],
        "forbidden_limitation_flags": ["simplified", "preview_only", "partial"],
        "evaluation_policy_version": "goal-evaluator-v1",
    }
    typed_invocation = (
        planner_invocation
        if isinstance(planner_invocation, PlannerInvocation)
        else PlannerInvocation.model_validate(planner_invocation)
        if planner_invocation is not None
        else None
    )
    typed_evidence = (
        planner_evidence
        if isinstance(planner_evidence, PlannerEvidence)
        else PlannerEvidence.model_validate(planner_evidence)
        if planner_evidence is not None
        else None
    )
    if (typed_invocation is None) != (typed_evidence is None) or (
        typed_invocation is not None
        and typed_evidence is not None
        and typed_evidence.invocation_id != typed_invocation.invocation_id
    ):
        raise ReviewedPlanStoreError("PLANNER_PROVENANCE_BINDING_INVALID")
    if planning_request is not None:
        if planning_request.project_id != project_id:
            raise ReviewedPlanStoreError("PLANNING_REQUEST_PROJECT_MISMATCH")
        if Path(planning_request.project_config_path).resolve() != context.project_config_path:
            raise ReviewedPlanStoreError("PLANNING_REQUEST_CONTEXT_MISMATCH")
        planning_inputs_hash = stable_hash(planning_request.identity_payload())
        parent_reviewed_plan_id = planning_request.parent_reviewed_plan_id
        parent_plan_hash = planning_request.parent_plan_hash
        revision_reason = planning_request.revision_reason
    else:
        planning_inputs_hash = stable_hash(
            {
                "schema_version": 1,
                "project_id": project_id,
                "goal": str(goal or ""),
                "provider": str(provider or ""),
                "plan": normalized_plan,
            }
        )
        parent_reviewed_plan_id = None
        parent_plan_hash = None
        revision_reason = "initial"
    revision_no = 1
    if parent_reviewed_plan_id is not None or parent_plan_hash is not None:
        if not parent_reviewed_plan_id or not parent_plan_hash:
            raise ReviewedPlanStoreError("PLANNING_REQUEST_PARENT_BINDING_INVALID")
        parent = project_store.get_reviewed_plan(parent_reviewed_plan_id)
        if (
            parent is None
            or parent.project_id != project_id
            or parent.plan_hash != parent_plan_hash
        ):
            raise ReviewedPlanStoreError("PLANNING_REQUEST_PARENT_BINDING_INVALID")
        revision_no = parent.revision_no + 1
    reviewed_plan_id, plan_hash = reviewed_plan_identity(
        project_id,
        normalized_plan,
        identity_semantics,
        memory_context,
        typed_invocation,
        typed_evidence,
        planning_inputs_hash,
    )
    existing = project_store.get_reviewed_plan(reviewed_plan_id)
    if existing is not None:
        return existing
    goal_contract: GoalContract | None = None
    goal_contract_issues: list[str] = []
    if goal_semantics is not None:
        goal_contract = finalize_goal_contract(
            semantics=goal_semantics,
            project_id=project_id,
            reviewed_plan_id=reviewed_plan_id,
            plan_hash=plan_hash,
            reviewed_actor=reviewed_actor,
            reviewed_at=datetime.now(UTC) if reviewed_actor else None,
        )
        goal_contract_issues = list(
            dict.fromkeys(
                issue.message
                for issue in validate_goal_contract_reachability(
                    normalized_plan,
                    goal_contract,
                )
            )
        )
        if goal_contract_issues:
            raise ReviewedPlanStoreError("; ".join(goal_contract_issues))
    effective_status = status
    if goal_contract is None and status == "REVIEWED":
        effective_status = "NEEDS_GOAL_REVIEW"
    now = utc_now_iso()
    plan_path = _snapshot_path(project_dir, reviewed_plan_id)
    record = ReviewedPlanRecord(
        reviewed_plan_id=reviewed_plan_id,
        project_id=project_id,
        project_config_path=str(context.project_config_path),
        dataset_index_path=(
            str(context.dataset_index_path) if context.dataset_index_path else None
        ),
        rawdata_dir=str(context.rawdata_dir) if context.rawdata_dir else None,
        plan_hash=plan_hash,
        revision_no=revision_no,
        parent_reviewed_plan_id=parent_reviewed_plan_id,
        parent_plan_hash=parent_plan_hash,
        revision_reason=revision_reason,
        planning_inputs_hash=planning_inputs_hash,
        evidence_snapshot_hash=(
            planning_request.evidence_snapshot_hash if planning_request is not None else None
        ),
        memory_context_hash=(
            memory_context.context_hash
            if isinstance(memory_context, MemoryContext)
            else str((memory_context or {}).get("context_hash") or "") or None
        ),
        memory_context_refs=(
            [
                item.model_dump(mode="json")
                for item in memory_context.evidence_refs
            ]
            if isinstance(memory_context, MemoryContext)
            else list((memory_context or {}).get("evidence_refs") or [])
        ),
        memory_retrieval_policy_version=(
            memory_context.retrieval_policy_version
            if isinstance(memory_context, MemoryContext)
            else str((memory_context or {}).get("retrieval_policy_version") or "") or None
        ),
        planner_invocation=typed_invocation,
        planner_evidence=typed_evidence,
        plan_path=str(plan_path),
        status=effective_status,
        created_at=now,
        updated_at=now,
        warnings=list(warnings or []),
        payload={
            "plan": normalized_plan,
            "normalized_plan_hash": stable_hash(normalized_plan),
            "validation": {
                **contract_validation,
                **dict(validation or {}),
                "normalized_params_hash": contract_validation.get("normalized_params_hash", ""),
                "contract_versions": contract_validation.get("contract_versions", {}),
                "validation_evidence": contract_validation.get("validation_evidence", []),
            },
            "goal": goal,
            "goal_contract": goal_contract.model_dump(mode="json") if goal_contract else None,
            "goal_contract_status": "reviewed" if goal_contract else "needs_goal_review",
            "goal_contract_candidate": (
                goal_build.semantics if candidate is None and goal_build.ok else None
            ),
            "goal_contract_issue": (
                None
                if candidate is not None
                else goal_build.reason or "GOAL_CONTRACT_REVIEW_REQUIRED"
            ),
            "provider": provider,
            "planning_request": (
                planning_request.model_dump(mode="json")
                if planning_request is not None
                else None
            ),
            "planning_inputs_hash": planning_inputs_hash,
            "planner_invocation": (
                typed_invocation.model_dump(mode="json") if typed_invocation else None
            ),
            "planner_evidence": (
                typed_evidence.model_dump(mode="json") if typed_evidence else None
            ),
            "lineage": dict(lineage or {}),
            "memory_context": (
                memory_context.model_dump(mode="json")
                if isinstance(memory_context, MemoryContext)
                else dict(memory_context or {})
            ),
        },
    )
    if record.status in {"REVIEWED", "NEEDS_APPROVAL"}:
        from src.backend.app.services.approval_summary_service import (
            ApprovalSummaryService,
        )

        project = project_store.get_project(project_id)
        if project is None:
            raise ReviewedPlanStoreError(f"PROJECT_NOT_FOUND: {project_id}")
        summary = ApprovalSummaryService().build(
            project=project,
            reviewed_plan=record,
        )
        public_summary = {
            key: value
            for key, value in summary.model_dump(mode="json").items()
            if key
            in {
                "summary_hash",
                "goal",
                "dataset_summary",
                "execution_summary",
                "write_roots",
                "rawdata_read_only",
                "external_tools",
                "limitations",
                "science_changes",
                "sections",
                "expires_at",
                "memory_context_hash",
                "memory_refs",
                "memory_influence_summary",
                "planning_inputs_hash",
                "revision_no",
                "parent_reviewed_plan_id",
                "parent_plan_hash",
                "revision_reason",
            }
        }
        record = record.model_copy(
            update={
                "payload": {
                    **record.payload,
                    "approval_summary": public_summary,
                    "approval_envelope": summary.model_dump(mode="json"),
                }
            }
        )
    stored = project_store.add_reviewed_plan(record)
    try:
        write_reviewed_plan_snapshot(stored, project_dir)
    except Exception as exc:
        snapshot_warning = f"PLAN_SNAPSHOT_WRITE_FAILED: {exc}"
        stored = project_store.update_reviewed_plan(
            stored.reviewed_plan_id,
            warnings=list(dict.fromkeys([*stored.warnings, snapshot_warning])),
        ) or stored
    return stored


def snapshot_warnings(record: ReviewedPlanRecord) -> list[str]:
    warnings = list(record.warnings)
    if not record.plan_path or not Path(record.plan_path).is_file():
        warnings.append("PLAN_SNAPSHOT_MISSING")
    return list(dict.fromkeys(warnings))


def artifact_warnings(record: RunLinkRecord) -> list[str]:
    warnings = list(record.warnings)
    if not record.pipeline_path or not Path(record.pipeline_path).is_file():
        warnings.append("PIPELINE_YAML_MISSING")
    if record.summary_path and not Path(record.summary_path).is_file():
        warnings.append("SUMMARY_MISSING")
    return list(dict.fromkeys(warnings))


def resolve_reviewed_plan_for_execution(
    context: ProjectContext,
    plan: dict[str, Any],
    reviewed_plan_id: str | None,
) -> ReviewedPlanRecord:
    if not context.project_id:
        raise ReviewedPlanStoreError("PROJECT_ID_REQUIRED: real execution needs a project id")
    normalized_plan, _ = normalize_reviewed_plan(plan)
    normalized_plan_hash = stable_hash(normalized_plan)
    record = mock_store.get_reviewed_plan(reviewed_plan_id) if reviewed_plan_id else None
    if record is None and reviewed_plan_id:
        raise ReviewedPlanStoreError("REVIEWED_PLAN_NOT_FOUND: save this reviewed plan before execution")
    if record is None:
        matches = [
            item
            for item in mock_store.list_reviewed_plans(context.project_id)
            if item.payload.get("normalized_plan_hash") == normalized_plan_hash
        ]
        if len(matches) == 1:
            record = matches[0]
        elif len(matches) > 1:
            raise ReviewedPlanStoreError(
                "REVIEWED_PLAN_ID_REQUIRED: multiple reviewed Goal Contracts exist for this plan"
            )
    if record is None:
        raise ReviewedPlanStoreError(
            "REVIEWED_PLAN_NOT_FOUND: save this reviewed plan before execution"
        )
    if record.project_id != context.project_id:
        raise ReviewedPlanStoreError(
            "REVIEWED_PLAN_MISMATCH: persisted reviewed plan does not match execution"
        )
    stored_normalized_hash = record.payload.get("normalized_plan_hash")
    if stored_normalized_hash != normalized_plan_hash:
        raise ReviewedPlanStoreError(
            "REVIEWED_PLAN_MISMATCH: normalized plan changed after review"
        )
    goal_contract_payload = record.payload.get("goal_contract")
    if record.payload.get("goal_contract_status") != "reviewed" or not isinstance(goal_contract_payload, dict):
        raise ReviewedPlanStoreError(
            "REVIEWED_PLAN_NEEDS_GOAL_REVIEW: plan has no reviewed Goal Contract"
        )
    try:
        goal_contract = GoalContract(**goal_contract_payload)
    except Exception as exc:
        raise ReviewedPlanStoreError("GOAL_CONTRACT_INVALID: persisted contract cannot be loaded") from exc
    canonical_goal = goal_contract.model_dump(mode="json")
    expected_goal_hash = canonical_goal.pop("goal_contract_hash", None)
    if stable_hash(canonical_goal) != expected_goal_hash:
        raise ReviewedPlanStoreError("GOAL_CONTRACT_TAMPERED: canonical hash mismatch")
    semantics = goal_contract_identity_payload(goal_contract.model_dump(mode="json"))
    expected_id, expected_plan_hash = reviewed_plan_identity(
        context.project_id,
        normalized_plan,
        semantics,
        record.payload.get("memory_context") or None,
        record.planner_invocation,
        record.planner_evidence,
        record.planning_inputs_hash,
    )
    if record.reviewed_plan_id != expected_id or record.plan_hash != expected_plan_hash:
        raise ReviewedPlanStoreError("REVIEWED_PLAN_GOAL_BINDING_MISMATCH")
    if Path(record.project_config_path).resolve() != context.project_config_path:
        raise ReviewedPlanStoreError(
            "PROJECT_CONFIG_MISMATCH: reviewed plan uses a different project config"
        )
    return record


def build_run_link(
    *,
    project_id: str,
    reviewed_plan_id: str,
    run_link_id: str,
    run_id: str,
    project_config_path: str,
    pipeline_path: str,
    task_id: str | None = None,
) -> RunLinkRecord:
    now = utc_now_iso()
    return RunLinkRecord(
        run_link_id=run_link_id,
        project_id=project_id,
        reviewed_plan_id=reviewed_plan_id,
        task_id=task_id,
        run_id=run_id,
        pipeline_path=pipeline_path,
        project_config_path=project_config_path,
        created_at=now,
        updated_at=now,
    )
