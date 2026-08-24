"""Rsfmri Nodes registry plugin."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.backend.app.runtime.node_registry_plugins.base import NodeExecutionContext, NodeRunner
from src.backend.app.schemas.pipeline_schema import PipelineNode
from src.backend.app.tools.alff_falff_runner import run_alff_falff_subject
from src.backend.app.tools.functional_connectivity_runner import run_functional_connectivity_subject
from src.backend.app.tools.group_dataset_summary import build_group_dataset_summary
from src.backend.app.tools.nuisance_regression_runner import run_nuisance_regression_subject
from src.backend.app.tools.reho_runner import run_reho_subject
from src.backend.app.tools.rsfmri_plan_tool import write_rsfmri_preprocessing_plan
from src.backend.app.tools.temporal_filtering_runner import run_temporal_filtering_subject


def run_rsfmri_preprocessing_plan_node(
    context: NodeExecutionContext,
    node: PipelineNode,
) -> dict[str, Any]:
    result = write_rsfmri_preprocessing_plan(
        work_dir=context.work_dir,
        report_dir=context.project_config.get("runtime", {}).get("report_dir", "./reports"),
    )
    result["node_id"] = node.id
    return result


def run_nuisance_regression_subject_node(
    context: NodeExecutionContext,
    node: PipelineNode,
    subject_record: dict[str, Any] | None = None,
    subject_id: str | None = None,
) -> dict[str, Any]:
    if not subject_id:
        return {
            "ok": False,
            "node_id": node.id,
            "backend": "python",
            "outputs": [],
            "errors": ["Missing subject_id"],
        }
    result = run_nuisance_regression_subject(
        subject_id=subject_id,
        derivatives_dir=context.derivatives_dir,
        backend=node.params.get("backend", "python"),
        model=node.params.get("model", "friston24"),
        include_intercept=bool(node.params.get("include_intercept", True)),
        include_linear_trend=bool(node.params.get("include_linear_trend", True)),
        include_global_signal=bool(node.params.get("include_global_signal", False)),
        input_nii=node.params.get("input_nii"),
        motion_parameter_file=node.params.get("motion_parameter_file"),
    )
    result["node_id"] = node.id
    return result


def run_temporal_filtering_subject_node(
    context: NodeExecutionContext,
    node: PipelineNode,
    subject_record: dict[str, Any] | None = None,
    subject_id: str | None = None,
) -> dict[str, Any]:
    if not subject_id:
        return {
            "ok": False,
            "node_id": node.id,
            "backend": "python",
            "outputs": [],
            "errors": ["Missing subject_id"],
        }
    result = run_temporal_filtering_subject(
        subject_id=subject_id,
        derivatives_dir=context.derivatives_dir,
        backend=node.params.get("backend", "python"),
        low_hz=float(node.params.get("low_hz", 0.01)),
        high_hz=float(node.params.get("high_hz", 0.08)),
        tr=node.params.get("tr"),
        fallback_tr=node.params.get("fallback_tr"),
    )
    result["node_id"] = node.id
    return result


def run_alff_falff_subject_node(
    context: NodeExecutionContext,
    node: PipelineNode,
    subject_record: dict[str, Any] | None = None,
    subject_id: str | None = None,
) -> dict[str, Any]:
    if not subject_id:
        return {
            "ok": False,
            "node_id": node.id,
            "backend": "python",
            "outputs": [],
            "errors": ["Missing subject_id"],
        }
    r = run_alff_falff_subject(
        subject_id=subject_id,
        derivatives_dir=context.derivatives_dir,
        backend=node.params.get("backend", "python"),
        low_hz=node.params.get("low_hz"),
        high_hz=node.params.get("high_hz"),
        tr=node.params.get("tr"),
        fallback_tr=node.params.get("fallback_tr"),
    )
    r["node_id"] = node.id
    return r


def run_reho_subject_node(
    context: NodeExecutionContext, node: PipelineNode, subject_record=None, subject_id=None
) -> dict[str, Any]:
    if not subject_id:
        return {
            "ok": False,
            "node_id": node.id,
            "backend": "python",
            "outputs": [],
            "errors": ["Missing subject_id"],
        }
    r = run_reho_subject(
        subject_id=subject_id,
        derivatives_dir=context.derivatives_dir,
        backend=node.params.get("backend", "python"),
        neighborhood=int(node.params.get("neighborhood", 27)),
        use_gm_mask=bool(node.params.get("use_gm_mask", False)),
    )
    r["node_id"] = node.id
    return r


def run_functional_connectivity_subject_node(context, node, subject_record=None, subject_id=None):
    """Compute ROI-based functional connectivity for a subject."""
    if not subject_id:
        return {
            "ok": False,
            "node_id": node.id,
            "backend": "python",
            "outputs": [],
            "errors": ["Missing subject_id"],
        }
    input_nii = node.params.get("input_nii")
    if not input_nii and isinstance(subject_record, dict):
        for session in subject_record.get("sessions", []):
            if not isinstance(session, dict):
                continue
            for functional in session.get("func", []):
                if isinstance(functional, dict) and functional.get("bold"):
                    input_nii = functional["bold"]
                    break
            if input_nii:
                break
    if input_nii and not Path(str(input_nii)).is_file():
        raise RuntimeError(
            "TRANSIENT_IO: registered functional input is temporarily unavailable"
        )
    result = run_functional_connectivity_subject(
        subject_id=subject_id,
        derivatives_dir=context.derivatives_dir,
        backend=node.params.get("backend", "python"),
        roi_count=int(node.params.get("roi_count", 4)),
        atlas_path=node.params.get("atlas_path"),
        labels_path=node.params.get("labels_path"),
        generate_seed_map=bool(node.params.get("generate_seed_map", False)),
        input_nii=input_nii,
        allowed_input_roots=tuple(
            str(root)
            for root in (
                context.tool_execution_context.input_roots
                if getattr(context, "tool_execution_context", None) is not None
                else ()
            )
        ),
    )
    result["node_id"] = node.id
    return result


def run_group_dataset_summary_node(context, node):
    """Build group-level dataset summary report."""
    result = build_group_dataset_summary(
        derivatives_dir=context.derivatives_dir,
        reports_dir=context.project_config.get("runtime", {}).get("report_dir", "./reports"),
        work_dir=context.work_dir,
    )
    result["node_id"] = node.id
    return result


REGISTRY: dict[str, NodeRunner] = {
    "rsfmri_preprocessing_plan": run_rsfmri_preprocessing_plan_node,
    "nuisance_regression_subject": run_nuisance_regression_subject_node,
    "temporal_filtering_subject": run_temporal_filtering_subject_node,
    "alff_falff_subject": run_alff_falff_subject_node,
    "reho_subject": run_reho_subject_node,
    "functional_connectivity_subject": run_functional_connectivity_subject_node,
    "group_dataset_summary": run_group_dataset_summary_node,
}
