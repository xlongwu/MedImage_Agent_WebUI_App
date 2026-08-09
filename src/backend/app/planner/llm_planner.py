"""Typed rule-based and OpenAI-compatible reviewed-plan generation.

The Planner converts a natural-language goal into a candidate pipeline
plan dict. Both providers return the same canonical Pydantic plan and
every generated plan passes through Plan Validator before it is returned.
"""

from __future__ import annotations

import re
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from src.backend.app.planner.audit_record import stable_hash
from src.backend.app.planner.goal_contract_builder import build_goal_contract_semantics
from src.backend.app.planner.plan_validator import validate_plan
from src.backend.app.schemas.planner_plan import canonical_plan_payload
from src.backend.app.schemas.planner_provenance import PlannerEvidence, PlannerInvocation


PLANNER_PROMPT_TEMPLATE_VERSION = "planner-plan-v1"
PLANNER_INPUT_SCHEMA_VERSION = "planner-request-v1"
PLANNER_PROMPT_TEMPLATE_SIGNATURE = {
    "system_policy": "strict canonical reviewed-plan JSON; no execution or approval",
    "input_fields": ["goal", "constraints", "tool_catalog"],
    "output_schema": "PlannerPlan",
}

# ── Dataclasses ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PlannerRequest:
    """Input to the LLM Planner."""

    goal: str
    provider: str = "rule_based"
    project_config_path: str | None = None
    constraints: dict[str, Any] | None = None


@dataclass(frozen=True)
class PlannerResponse:
    """Output from the LLM Planner."""

    ok: bool
    provider: str
    goal: str
    plan: dict[str, Any]
    validation: dict[str, Any]
    messages: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    confidence: float | None = None
    missing_prerequisites: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    clarification_required: bool = False
    goal_contract_candidate: dict[str, Any] | None = None
    planner_invocation: PlannerInvocation | None = None
    planner_evidence: PlannerEvidence | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "provider": self.provider,
            "goal": self.goal,
            "plan": self.plan,
            "validation": self.validation,
            "messages": self.messages,
            "warnings": self.warnings,
            "errors": self.errors,
            "confidence": self.confidence,
            "missing_prerequisites": self.missing_prerequisites,
            "risks": self.risks,
            "clarification_required": self.clarification_required,
            "goal_contract_candidate": self.goal_contract_candidate,
            "planner_invocation": (
                self.planner_invocation.model_dump(mode="json")
                if self.planner_invocation else None
            ),
            "planner_evidence": (
                self.planner_evidence.model_dump(mode="json")
                if self.planner_evidence else None
            ),
        }


# ── Rule-based goal → node sequence ──────────────────────────────────────────

# Native full preprocessing is only selected when a real project context already
# exposes registered NIfTI/BIDS evidence. Generic motion-planning behavior stays
# backward compatible.
_NATIVE_FULL_GOAL_TERMS = (
    "rs-fmri",
    "preprocessing",
    "preprocess",
    "slice timing",
    "realignment",
    "realign",
    "motion qc",
    "nuisance regression",
    "detrending",
    "temporal filtering",
    "roi time series",
    "functional connectivity",
    "预处理",
    "全流程",
)

_NATIVE_FULL_CONFIRMATIONS: dict[str, bool] = {
    "confirm_reviewed_native_execution": True,
    "confirm_rawdata_readonly": True,
    "confirm_no_external_tools": True,
    "confirm_research_use_only": True,
    "confirm_no_clinical_use": True,
}

_ACPC_GOAL_TERMS = (
    "acpc", "ac-pc", "anterior commissure", "posterior commissure", "前后连合", "前后联合", "前连合", "后连合",
)

_PLAN_ONLY_TERMS = (
    "plan only",
    "planning only",
    "do not execute",
    "don't execute",
    "no execution",
    "no computation",
    "without execution",
    "仅生成计划",
    "只生成计划",
    "仅生成方案",
    "只生成方案",
    "不执行任何计算",
    "不执行计算",
    "不运行任何计算",
)

_PREPROCESSING_PLAN_TERMS = (
    "bids",
    "rs-fmri",
    "resting-state",
    "preprocessing",
    "preprocess",
    "静息态",
    "预处理",
)

_BIDS_SUBJECT_RE = re.compile(r"(?<![A-Za-z0-9])sub[-_]?([A-Za-z0-9]+)", re.IGNORECASE)
_SINGLE_SUBJECT_TERMS = (
    "single subject",
    "one subject",
    "select 1 subject",
    "选择 1 名",
    "选择1名",
    "选择一名",
    "仅对一名",
    "只对一名",
)
_MINIMAL_PREPROCESSING_TERMS = (
    "minimal preprocessing",
    "minimum preprocessing",
    "minimal rs-fmri",
    "最小预处理",
    "最小化预处理",
)
_MINIMAL_RSFMRI_STAGE_OVERRIDES: dict[str, bool] = {
    "dicom_to_nifti": False,
    "input_validation": True,
    "bids_sidecar_validation": True,
    "dummy_scan_removal": False,
    "slice_timing": False,
    "realignment": True,
    "motion_qc": True,
    "coregistration": False,
    "segmentation": False,
    "normalization": False,
    "smoothing": False,
    "nuisance_regression": True,
    "detrending": True,
    "temporal_filtering": True,
    "alff": False,
    "falff": False,
    "reho": False,
    "atlas_resampling": False,
    "roi_timeseries": False,
    "functional_connectivity": False,
    "subject_qc": True,
    "group_summary": False,
    "validation_report": True,
    "final_report": True,
}

# Each entry: set of trigger keywords → (pipeline_id, list of node ids)
_RULES: list[tuple[set[str], str, list[str]]] = [
    (
        {"realign", "头动", "运动校正", "motion correction"},
        "planned_motion_qc",
        [
            "data_inspection",
            "spm_realign_subject",
            "motion_qc_subject",
            "motion_qc_dataset_report",
        ],
    ),
    (
        {"alff", "falff", "amplitude"},
        "planned_alff",
        [
            "data_inspection",
            "nuisance_regression_subject",
            "alff_falff_subject",
            "alff_falff_qc_dataset_report",
        ],
    ),
    (
        {"reho", "regional homogeneity"},
        "planned_reho",
        [
            "data_inspection",
            "nuisance_regression_subject",
            "reho_subject",
            "reho_qc_dataset_report",
        ],
    ),
    (
        {"smooth", "smoothing", "平滑", "spatial smoothing"},
        "planned_smooth",
        [
            "data_inspection",
            "spm_smooth_subject",
            "smoothing_qc_dataset_report",
        ],
    ),
    (
        {"rs-fmri preprocessing", "resting-state preprocessing", "fMRI preprocessing", "motion QC", "静息态预处理"},
        "rsfmri_preproc_mvp",
        [
            "data_readiness_check",
            "bids_validation_check",
            "rsfmri_bold_reference_check",
            "rsfmri_motion_qc_plan",
            "rsfmri_preprocessing_plan_stub",
            "rsfmri_report_plan_stub",
        ],
    ),
    (
        {"full pipeline", "全流程", "complete preprocessing", "完整预处理", "full preprocessing"},
        "planned_full_preprocessing",
        [
            "data_inspection",
            "spm_slice_timing_subject",
            "spm_realign_subject",
            "motion_qc_subject",
            "spm_smooth_subject",
            "smoothing_qc_dataset_report",
        ],
    ),
]


def _contract_backend(node_id: str) -> str:
    """Return the backend declared by the canonical Node Contract registry."""
    from src.backend.app.runtime.node_contract_registry import get_node_contract

    return get_node_contract(node_id).backend


def _build_plan(
    pipeline_id: str,
    node_ids: list[str],
    *,
    goal: str,
    provider: str,
) -> dict[str, Any]:
    """Build a minimal reviewed-plan-shaped dict from a node sequence."""
    nodes: list[dict[str, Any]] = []
    for nid in node_ids:
        node: dict[str, Any] = {
            "id": nid,
            "backend": _contract_backend(nid),
            "depends_on": [],
            "params": {},
        }
        if nid.startswith("spm_") or nid.startswith("dpabi_"):
            node["params"]["approved"] = False
        nodes.append(node)

    # Chain dependencies sequentially
    for i in range(1, len(nodes)):
        nodes[i]["depends_on"] = [nodes[i - 1]["id"]]

    return {
        "pipeline_id": pipeline_id,
        "project_context": {
            "project_id": None,
            "project_config_path": None,
            "rawdata_dir": None,
            "dataset_index_path": None,
            "source": "planner_rule_based_policy",
            "diagnostics": {},
        },
        "goal": goal,
        "nodes": nodes,
        "metadata": {
            "planner": "deterministic_stage_policy",
            "provider": provider,
            "capability_level": "metadata_only",
            "external_api_used": False,
            "execution_enabled": False,
        },
    }


# ── Public API ────────────────────────────────────────────────────────────────

def _project_context_from_constraints(
    constraints: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(constraints, dict):
        return {}
    context = constraints.get("project_context")
    return dict(context) if isinstance(context, dict) else {}


def _diagnostics_from_context(context: dict[str, Any]) -> dict[str, Any]:
    diagnostics = context.get("diagnostics")
    return dict(diagnostics) if isinstance(diagnostics, dict) else {}


def _has_registered_nifti_evidence(context: dict[str, Any]) -> bool:
    diagnostics = _diagnostics_from_context(context)
    status = str(diagnostics.get("status") or "").upper()
    if status in {"CONVERTED_BIDS", "MIXED", "NIFTI", "BIDS"}:
        return True
    if diagnostics.get("converted_bids_available") is True:
        return True
    count = diagnostics.get("nifti_file_count") or diagnostics.get("nifti_files")
    try:
        return int(count) > 0
    except (TypeError, ValueError):
        return False


def _has_prepared_conversion_evidence(context: dict[str, Any]) -> bool:
    diagnostics = _diagnostics_from_context(context)
    return bool(
        diagnostics.get("agent_conversion_execution_ready") is True
        and (
            diagnostics.get("agent_conversion_run_id")
            or diagnostics.get("conversion_run_id")
        )
    )


def _matches_native_full_goal(goal_lower: str) -> bool:
    score = sum(1 for term in _NATIVE_FULL_GOAL_TERMS if term.lower() in goal_lower)
    has_preproc_intent = (
        "preprocessing" in goal_lower
        or "preprocess" in goal_lower
        or "预处理" in goal_lower
        or "全流程" in goal_lower
    )
    has_downstream_intent = any(
        term in goal_lower
        for term in (
            "functional connectivity",
            "roi time series",
            "temporal filtering",
            "nuisance regression",
            "detrending",
        )
    )
    has_explicit_execution_intent = any(
        term in goal_lower
        for term in (
            "execute",
            "run ",
            "执行",
            "运行",
        )
    )
    has_registered_rsfmri_scope = any(
        term in goal_lower
        for term in (
            "bids",
            "registered",
            "已登记",
            "rs-fmri",
            "静息态",
        )
    )
    return (
        score >= 2 and (has_preproc_intent or has_downstream_intent)
    ) or (
        has_explicit_execution_intent
        and has_registered_rsfmri_scope
        and has_preproc_intent
    )


def _matches_plan_only_preprocessing_goal(goal_lower: str) -> bool:
    return any(term in goal_lower for term in _PLAN_ONLY_TERMS) and any(
        term in goal_lower for term in _PREPROCESSING_PLAN_TERMS
    )


def _matches_native_reho_goal(goal_lower: str) -> bool:
    return any(term in goal_lower for term in ("reho", "regional homogeneity")) and any(
        term in goal_lower
        for term in ("compute", "calculate", "execute", "run ", "计算", "执行", "生成")
    )


def _matches_acpc_goal(goal_lower: str) -> bool:
    return any(term in goal_lower for term in _ACPC_GOAL_TERMS) and any(
        term in goal_lower
        for term in ("align", "alignment", "locat", "reorient", "对齐", "定位", "校正", "重定向")
    )


def _build_acpc_plan(*, goal: str, provider: str, project_context: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    diagnostics = _diagnostics_from_context(project_context)
    artifact_ids = [str(value) for value in diagnostics.get("registered_t1_artifact_ids") or [] if str(value)]
    if len(artifact_ids) != 1:
        return {}, [
            "REGISTERED_T1_ARTIFACT_REQUIRED: ACPC alignment requires exactly one selected registered T1w artifact."
        ]
    project_dir = str(diagnostics.get("project_dir") or "")
    return {
        "pipeline_id": "native_auto_acpc_alignment",
        "project_context": {"project_id": None, "project_config_path": None, "rawdata_dir": None, "dataset_index_path": None, "source": "planner_rule_based_policy", "diagnostics": {}},
        "goal": goal,
        "nodes": [{
            "id": "native_auto_acpc_align",
            "backend": "native_python",
            "depends_on": [],
            "params": {
                "project_id": str(project_context.get("project_id") or ""),
                "project_dir": project_dir,
                "source_t1_artifact_id": artifact_ids[0],
                "output_root": str(Path(project_dir) / "derivatives") if project_dir else "",
                "template_id": "spm12_avg152_t1_ras",
                "interpolation": "linear",
            },
        }],
        "metadata": {
            "planner": "deterministic_acpc_policy",
            "provider": provider,
            "capability_level": "computed",
            "goal_kind": "acpc_alignment",
            "goal_artifact_types": ["acpc_t1w", "transform_matrix", "acpc_landmarks", "qc_json"],
            "execution_enabled": False,
            "execution_requires_approval_gate": True,
            "rawdata_read_only": True,
            "estimated_landmarks": True,
            "review_required_on_qc_failure": True,
        },
    }, []


def _normalize_subject_id(value: object) -> str:
    match = _BIDS_SUBJECT_RE.search(str(value or "").strip())
    return f"sub-{match.group(1).lower()}" if match else ""


def _subject_candidates(diagnostics: dict[str, Any]) -> list[str]:
    raw = diagnostics.get("subject_candidates")
    if not isinstance(raw, list):
        return []
    return sorted(
        {
            normalized
            for item in raw
            if (normalized := _normalize_subject_id(item))
        }
    )


def _explicit_subject_id(goal: str) -> str:
    return _normalize_subject_id(goal)


def _requests_single_subject(goal_lower: str) -> bool:
    return any(term in goal_lower for term in _SINGLE_SUBJECT_TERMS)


def _matches_minimal_preprocessing_goal(goal_lower: str) -> bool:
    return any(term in goal_lower for term in _MINIMAL_PREPROCESSING_TERMS)


def _build_plan_only_preprocessing_plan(*, goal: str, provider: str) -> dict[str, Any]:
    plan = _build_plan(
        "rsfmri_preproc_mvp",
        [
            "data_readiness_check",
            "bids_validation_check",
            "rsfmri_bold_reference_check",
            "rsfmri_motion_qc_plan",
            "rsfmri_preprocessing_plan_stub",
            "rsfmri_report_plan_stub",
        ],
        goal=goal,
        provider=provider,
    )
    plan["metadata"].update(
        {
            "plan_only": True,
            "execution_enabled": False,
            "execution_requires_approval_gate": False,
            "rawdata_read_only": True,
        }
    )
    return plan


def _build_native_full_preprocessing_plan(
    *,
    goal: str,
    provider: str,
    project_context: dict[str, Any],
) -> dict[str, Any]:
    diagnostics = _diagnostics_from_context(project_context)
    goal_lower = goal.lower()
    candidates = _subject_candidates(diagnostics)
    requested_subject = _explicit_subject_id(goal)
    selected_subject = (
        requested_subject
        if requested_subject and (not candidates or requested_subject in candidates)
        else ""
    )
    subject_selection_required = bool(
        candidates
        and not selected_subject
        and (_requests_single_subject(goal_lower) or bool(requested_subject))
    )
    prepared_conversion_run_id = (
        str(diagnostics.get("agent_conversion_run_id") or "")
        if diagnostics.get("agent_conversion_execution_ready") is True
        else ""
    )
    conversion_run_id = str(
        diagnostics.get("preprocessing_conversion_run_id")
        or prepared_conversion_run_id
        or ""
    )
    native_params: dict[str, Any] = {
        "project_id": str(project_context.get("project_id") or ""),
        "project_dir": str(diagnostics.get("project_dir") or ""),
        "conversion_run_id": conversion_run_id,
        "confirmations": dict(_NATIVE_FULL_CONFIRMATIONS),
        "stage_overrides": {},
    }
    if selected_subject:
        native_params["subject_id"] = selected_subject
    minimal_preprocessing = _matches_minimal_preprocessing_goal(goal_lower)
    if minimal_preprocessing:
        native_params["stage_overrides"] = dict(_MINIMAL_RSFMRI_STAGE_OVERRIDES)
    registered_bids_dir = str(
        diagnostics.get("preprocessing_input_dir")
        or diagnostics.get("converted_bids_dir")
        or project_context.get("rawdata_dir")
        or ""
    )
    if registered_bids_dir:
        native_params["input_bids_dir"] = registered_bids_dir
    conversion_nodes: list[dict[str, Any]] = []
    if prepared_conversion_run_id:
        conversion_nodes.append(
            {
                "id": "native_dicom_conversion_execute",
                "backend": "medimage-native",
                "depends_on": [],
                "params": {
                    "project_id": str(project_context.get("project_id") or ""),
                    "project_dir": str(diagnostics.get("project_dir") or ""),
                    "rawdata_dir": str(
                        diagnostics.get("rawdata_dir")
                        or project_context.get("rawdata_dir")
                        or ""
                    ),
                    "conversion_run_id": prepared_conversion_run_id,
                    "output_dir": str(diagnostics.get("converted_bids_dir") or ""),
                },
            }
        )
    metadata: dict[str, Any] = {
        "planner": "deterministic_stage_policy",
        "provider": provider,
        "capability_level": "computed",
        "external_api_used": False,
        "execution_enabled": False,
        "execution_requires_approval_gate": True,
        "native_preprocessing": True,
        "native_dicom_conversion": bool(conversion_nodes),
    }
    if selected_subject:
        metadata["subject_scope"] = [selected_subject]
    if subject_selection_required:
        metadata["science_decisions"] = {
            "subject_selection_required": True,
            "subject_candidates": candidates,
            **(
                {"requested_subject_id": requested_subject}
                if requested_subject
                else {}
            ),
        }
    if minimal_preprocessing:
        metadata.update(
            {
                "stage_profile": "minimal_rsfmri",
                "required_preprocessing_stages": [
                    "input_validation",
                    "bids_sidecar_validation",
                    "realignment",
                    "motion_qc",
                    "nuisance_regression",
                    "detrending",
                    "temporal_filtering",
                    "subject_qc",
                    "validation_report",
                    "final_report",
                ],
            }
        )
    return {
        "pipeline_id": "native_full_preprocessing",
        "project_context": {
            "project_id": None,
            "project_config_path": None,
            "rawdata_dir": None,
            "dataset_index_path": None,
            "source": "planner_rule_based_policy",
            "diagnostics": {},
        },
        "goal": goal,
        "nodes": [
            *conversion_nodes,
            {
                "id": "native_preproc_full_execute",
                "backend": "native_python",
                "depends_on": ["native_dicom_conversion_execute"] if conversion_nodes else [],
                "params": native_params,
            }
        ],
        "metadata": metadata,
    }


def _build_native_reho_plan(
    *,
    goal: str,
    provider: str,
    project_context: dict[str, Any],
) -> dict[str, Any]:
    plan = _build_native_full_preprocessing_plan(
        goal=goal,
        provider=provider,
        project_context=project_context,
    )
    plan["pipeline_id"] = "native_reho"
    native_node = next(
        node for node in plan["nodes"] if node["id"] == "native_preproc_full_execute"
    )
    native_node["params"]["stage_overrides"] = {
        "dicom_to_nifti": False,
        "input_validation": True,
        "bids_sidecar_validation": True,
        "dummy_scan_removal": True,
        "slice_timing": False,
        "realignment": True,
        "motion_qc": True,
        "coregistration": False,
        "segmentation": False,
        "normalization": False,
        "smoothing": False,
        "nuisance_regression": True,
        "detrending": True,
        "temporal_filtering": True,
        "alff": False,
        "falff": False,
        "reho": True,
        "atlas_resampling": False,
        "roi_timeseries": False,
        "functional_connectivity": False,
        "subject_qc": True,
        "group_summary": True,
        "validation_report": True,
        "final_report": True,
    }
    plan["metadata"].update(
        {
            "goal_kind": "reho",
            "goal_artifact_types": ["reho_map"],
            "required_preprocessing_stages": [
                "realignment",
                "motion_qc",
                "nuisance_regression",
                "detrending",
                "temporal_filtering",
            ],
        }
    )
    return plan


def generate_plan_from_goal(
    goal: str,
    provider: str = "rule_based",
    constraints: dict[str, Any] | None = None,
    project_config_path: str | None = None,
) -> PlannerResponse:
    """Generate a candidate pipeline plan from a natural-language goal.

    The caller explicitly selects either deterministic rules or the remote provider.
    """
    errors: list[str] = []
    warnings: list[str] = []
    messages: list[str] = []
    invocation = PlannerInvocation(
        invocation_id=f"planner_invocation_{uuid4().hex}",
        provider_id=provider,
        model_id=(
            os.environ.get("MEDIMAGE_LLM_MODEL", "gpt-4.1-mini")
            if provider == "openai_compatible"
            else "deterministic-rules-v1"
        ),
        prompt_template_version=PLANNER_PROMPT_TEMPLATE_VERSION,
        prompt_template_hash=stable_hash(
            {
                "version": PLANNER_PROMPT_TEMPLATE_VERSION,
                "signature": PLANNER_PROMPT_TEMPLATE_SIGNATURE,
            }
        ),
        input_schema_version=PLANNER_INPUT_SCHEMA_VERSION,
        input_hash=stable_hash(
            {
                "goal": goal,
                "provider": provider,
                "constraints": constraints or {},
                "project_config_path": project_config_path,
            }
        ),
        started_at=datetime.now(UTC),
        timeout_ms=60_000 if provider == "openai_compatible" else 1_000,
    )

    def response(**values: Any) -> PlannerResponse:
        plan = values.get("plan")
        if isinstance(plan, dict) and plan:
            try:
                plan = canonical_plan_payload(plan)
                values["plan"] = plan
            except Exception as exc:
                values["ok"] = False
                values["plan"] = {}
                values["validation"] = {}
                values["errors"] = [
                    *list(values.get("errors") or []),
                    f"PLANNER_OUTPUT_INVALID: {type(exc).__name__}",
                ]
                plan = {}
        validation = values.get("validation") if isinstance(values.get("validation"), dict) else {}
        validation_codes = tuple(
            dict.fromkeys(
                str(item.get("code"))
                for bucket in (validation.get("errors", []), validation.get("warnings", []))
                for item in bucket
                if isinstance(item, dict) and item.get("code")
            )
        )
        result_errors = [str(item) for item in values.get("errors", [])]
        failure_code = result_errors[0].split(":", 1)[0] if result_errors else None
        evidence = PlannerEvidence(
            invocation_id=invocation.invocation_id,
            output_hash=stable_hash(plan) if plan else None,
            validation_codes=validation_codes,
            failure_code=failure_code,
            redacted_summary=(
                f"Planner {provider} produced {len(plan.get('nodes', []))} typed nodes."
                if plan
                else f"Planner {provider} stopped without a plan."
            ),
        )
        return PlannerResponse(
            **values,
            planner_invocation=invocation,
            planner_evidence=evidence,
        )

    # ── Provider check ──
    supported = {"rule_based", "openai_compatible"}
    if provider not in supported:
        return response(
            ok=False,
            provider=provider,
            goal=goal,
            plan={},
            validation={},
            errors=[f"UNSUPPORTED_PROVIDER: '{provider}' not supported. Use: {sorted(supported)}"],
        )

    # ── Goal check ──
    stripped = goal.strip()
    if not stripped:
        return response(
            ok=False,
            provider=provider,
            goal=goal,
            plan={},
            validation={},
            errors=["EMPTY_GOAL: goal must be a non-empty string."],
        )

    # ── OpenAI-compatible provider ──
    if provider == "openai_compatible":
        from src.backend.app.planner.llm_provider import (  # noqa: E402
            call_openai_compatible_provider,
            parse_llm_plan_json,
        )

        pr = call_openai_compatible_provider(goal, constraints=constraints)
        if not pr.ok:
            return response(
                ok=False,
                provider=provider,
                goal=goal,
                plan={},
                validation={},
                errors=pr.errors,
            )

        try:
            plan = parse_llm_plan_json(pr.content)
        except ValueError as exc:
            return response(
                ok=False,
                provider=provider,
                goal=goal,
                plan={},
                validation={},
                errors=[str(exc)],
            )

        validation = validate_plan(plan)
        goal_contract = build_goal_contract_semantics(plan, goal)
        missing_prerequisites = list(plan.get("missing_prerequisites") or [])
        risks = list(plan.get("risks") or [])
        return response(
            ok=validation.ok and not missing_prerequisites,
            provider=provider,
            goal=goal,
            plan=plan,
            validation=validation.to_dict(),
            messages=[f"Generated plan via {provider} ({len(plan.get('nodes', []))} nodes)."],
            confidence=(float(plan["confidence"]) if "confidence" in plan else None),
            missing_prerequisites=missing_prerequisites,
            risks=risks,
            clarification_required=bool(missing_prerequisites) or goal_contract.clarification_required,
            goal_contract_candidate=goal_contract.semantics,
        )

    # ── Rule matching ──
    goal_lower = stripped.lower()
    project_context = _project_context_from_constraints(constraints)
    if _matches_acpc_goal(goal_lower):
        plan, missing = _build_acpc_plan(goal=stripped, provider=provider, project_context=project_context)
        if missing:
            return response(
                ok=False, provider=provider, goal=goal, plan={}, validation={},
                missing_prerequisites=missing, clarification_required=True,
                errors=missing, warnings=["ACPC planning did not create an executable node without a selected registered T1w artifact."],
            )
        validation = validate_plan(plan)
        goal_contract = build_goal_contract_semantics(plan, goal)
        return response(
            ok=validation.ok and goal_contract.ok, provider=provider, goal=goal, plan=plan,
            validation=validation.to_dict(), messages=["Prepared a reviewed ACPC alignment plan using a registered T1w artifact."],
            warnings=["AC/PC outputs are template-back-projected estimates and require independent manual-reference validation."],
            errors=[] if goal_contract.ok else [goal_contract.reason or "GOAL_CONTRACT_INVALID"],
            clarification_required=goal_contract.clarification_required, goal_contract_candidate=goal_contract.semantics,
        )
    if _matches_plan_only_preprocessing_goal(goal_lower):
        plan = _build_plan_only_preprocessing_plan(goal=stripped, provider=provider)
        validation = validate_plan(plan)
        goal_contract = build_goal_contract_semantics(plan, goal)
        return response(
            ok=validation.ok and goal_contract.ok,
            provider=provider,
            goal=goal,
            plan=plan,
            validation=validation.to_dict(),
            messages=[
                "Prepared a metadata-only preprocessing plan; execution remains disabled."
            ],
            warnings=[
                "Plan-only task: no numerical computation or rawdata modification is authorized."
            ],
            errors=([] if goal_contract.ok else [goal_contract.reason or "GOAL_CONTRACT_INVALID"]),
            confidence=1.0,
            clarification_required=goal_contract.clarification_required,
            goal_contract_candidate=goal_contract.semantics,
        )
    if _has_registered_nifti_evidence(project_context) and _matches_native_reho_goal(goal_lower):
        plan = _build_native_reho_plan(
            goal=stripped,
            provider=provider,
            project_context=project_context,
        )
        validation = validate_plan(plan)
        goal_contract = build_goal_contract_semantics(plan, goal)
        return response(
            ok=validation.ok and goal_contract.ok,
            provider=provider,
            goal=goal,
            plan=plan,
            validation=validation.to_dict(),
            messages=[
                "Matched ReHo execution to the reviewed native preprocessing chain."
            ],
            warnings=[
                "ReHo execution includes realignment, motion QC, nuisance regression, detrending, and temporal filtering."
            ],
            errors=([] if goal_contract.ok else [goal_contract.reason or "GOAL_CONTRACT_INVALID"]),
            confidence=1.0,
            clarification_required=goal_contract.clarification_required,
            goal_contract_candidate=goal_contract.semantics,
        )
    if (
        (
            _has_registered_nifti_evidence(project_context)
            or _has_prepared_conversion_evidence(project_context)
        )
        and _matches_native_full_goal(goal_lower)
    ):
        plan = _build_native_full_preprocessing_plan(
            goal=stripped,
            provider=provider,
            project_context=project_context,
        )
        validation = validate_plan(plan)
        validation_errors = [f"[{issue.code}] {issue.message}" for issue in validation.errors]
        missing_prerequisites = (
            ["Provide a registered conversion_run_id or one explicit input_bold before review."]
            if any(
                issue.code == "NODE_PARAMETER_INVALID"
                and "input_bold or conversion_run_id" in issue.message
                for issue in validation.errors
            )
            else []
        )
        messages.append(
            "Matched goal to native full preprocessing using registered NIfTI/BIDS evidence."
        )
        if validation.warnings:
            for w in validation.warnings:
                warnings.append(f"[{w.code}] {w.message}")
        goal_contract = build_goal_contract_semantics(plan, goal)
        return response(
            ok=validation.ok,
            provider=provider,
            goal=goal,
            plan=plan,
            validation=validation.to_dict(),
            messages=messages,
            warnings=warnings,
            errors=validation_errors,
            confidence=1.0,
            missing_prerequisites=missing_prerequisites,
            risks=[f"high-risk node: {node_id}" for node_id in validation.high_risk_nodes],
            clarification_required=not validation.ok or goal_contract.clarification_required,
            goal_contract_candidate=goal_contract.semantics,
        )

    best_match: tuple[int, str, list[str]] | None = None

    for keywords, pipeline_id, node_ids in _RULES:
        score = sum(1 for kw in keywords if kw.lower() in goal_lower)
        if score > 0 and (best_match is None or score > best_match[0]):
            best_match = (score, pipeline_id, node_ids)

    if best_match is None:
        return response(
            ok=False,
            provider=provider,
            goal=goal,
            plan={},
            validation={},
            errors=[f"UNSUPPORTED_GOAL: could not match goal '{goal}' to any known pipeline."],
            messages=["Supported keywords: motion/realign, alff/falff, reho, smooth, full pipeline"],
        )

    _, pipeline_id, node_ids = best_match
    plan = _build_plan(
        pipeline_id,
        node_ids,
        goal=stripped,
        provider=provider,
    )
    validation = validate_plan(plan)

    # ── Build response ──
    messages.append(f"Matched goal to pipeline '{pipeline_id}' ({len(node_ids)} nodes).")
    if validation.warnings:
        for w in validation.warnings:
            warnings.append(f"[{w.code}] {w.message}")
    goal_contract = build_goal_contract_semantics(plan, goal)

    return response(
        ok=validation.ok and len(errors) == 0,
        provider=provider,
        goal=goal,
        plan=plan,
        validation=validation.to_dict(),
        messages=messages,
        warnings=warnings,
        errors=errors,
        confidence=1.0,
        risks=[f"high-risk node: {node_id}" for node_id in validation.high_risk_nodes],
        clarification_required=goal_contract.clarification_required,
        goal_contract_candidate=goal_contract.semantics,
    )


def plan_from_request(request: PlannerRequest) -> PlannerResponse:
    """Convenience wrapper around generate_plan_from_goal."""
    return generate_plan_from_goal(
        goal=request.goal,
        provider=request.provider,
        constraints=request.constraints,
        project_config_path=request.project_config_path,
    )
