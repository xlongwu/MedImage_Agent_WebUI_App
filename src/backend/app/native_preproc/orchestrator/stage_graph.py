"""Stage graph for the native full preprocessing orchestrator."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NativeFullStageSpec:
    stage_id: str
    display_name: str
    node_id: str
    depends_on: tuple[str, ...] = ()
    required_inputs: tuple[str, ...] = ()
    produced_outputs: tuple[str, ...] = ()
    capability_level: str = "metadata_only"
    enabled_by_default: bool = True
    notes: tuple[str, ...] = ()


NATIVE_FULL_STAGE_GRAPH: tuple[NativeFullStageSpec, ...] = (
    NativeFullStageSpec(
        "input_validation",
        "Input validation",
        "native_preproc_input_validation",
        required_inputs=("input_bold",),
        produced_outputs=("input_inventory",),
    ),
    NativeFullStageSpec(
        "auto_acpc_align",
        "Automatic ACPC alignment",
        "native_auto_acpc_align",
        depends_on=("input_validation",),
        required_inputs=("t1w",),
        produced_outputs=("acpc_t1w", "transform_matrix", "acpc_landmarks", "qc_json"),
        capability_level="computed",
        enabled_by_default=False,
        notes=(
            "Template-rigid estimated landmarks only; independent manual-landmark validation is pending.",
        ),
    ),
    NativeFullStageSpec(
        "dicom_to_nifti",
        "DICOM to NIfTI",
        "native_preproc_dicom_to_nifti",
        capability_level="unavailable",
        enabled_by_default=False,
        notes=("DICOM conversion remains owned by the conversion domain.",),
    ),
    NativeFullStageSpec(
        "bids_sidecar_validation",
        "BIDS sidecar validation",
        "native_preproc_bids_sidecar_validation",
        depends_on=("input_validation",),
        required_inputs=("sidecar_json_or_explicit_tr",),
    ),
    NativeFullStageSpec(
        "dummy_scan_removal",
        "Dummy scan removal",
        "native_preproc_dummy_scan_removal",
        depends_on=("input_validation",),
        required_inputs=("input_bold",),
        produced_outputs=("bold_4d",),
        capability_level="numerically_implemented",
    ),
    NativeFullStageSpec(
        "slice_timing",
        "Slice timing correction",
        "native_preproc_slice_timing",
        depends_on=("dummy_scan_removal",),
        required_inputs=("bold_4d", "sidecar_json_with_slice_timing"),
        produced_outputs=("bold_4d",),
        capability_level="numerically_implemented",
    ),
    NativeFullStageSpec(
        "realignment",
        "Realignment",
        "native_preproc_realignment",
        depends_on=("slice_timing",),
        required_inputs=("bold_4d",),
        produced_outputs=("bold_4d", "mean_functional", "motion_parameters", "transform_matrix"),
        capability_level="simplified",
        notes=("Translation-only native baseline; not SPM 6DOF equivalent.",),
    ),
    NativeFullStageSpec(
        "motion_qc",
        "Motion QC",
        "native_preproc_motion_qc",
        depends_on=("realignment",),
        required_inputs=("motion_parameters",),
        produced_outputs=("fd_timeseries", "confound_matrix", "qc_json"),
        capability_level="numerically_implemented",
    ),
    NativeFullStageSpec(
        "coregistration",
        "Coregistration",
        "native_preproc_coregistration",
        depends_on=("realignment",),
        required_inputs=("mean_functional", "t1w"),
        produced_outputs=("t1w", "transform_matrix"),
        capability_level="simplified",
    ),
    NativeFullStageSpec(
        "segmentation",
        "Segmentation",
        "native_preproc_segmentation",
        depends_on=("coregistration",),
        required_inputs=("t1w",),
        produced_outputs=("brain_mask", "gm_map", "wm_map", "csf_map"),
        capability_level="simplified",
    ),
    NativeFullStageSpec(
        "normalization",
        "Affine normalization",
        "native_preproc_normalization",
        depends_on=("segmentation",),
        required_inputs=("t1w", "bold_4d", "template"),
        produced_outputs=("normalized_bold", "transform_matrix", "wm_map", "csf_map"),
        capability_level="affine_only",
    ),
    NativeFullStageSpec(
        "smoothing",
        "Spatial smoothing",
        "native_preproc_smoothing",
        depends_on=("normalization",),
        required_inputs=("bold_4d",),
        produced_outputs=("smoothed_bold",),
        capability_level="numerically_implemented",
    ),
    NativeFullStageSpec(
        "nuisance_regression",
        "Nuisance regression",
        "native_preproc_nuisance_regression",
        depends_on=("motion_qc",),
        required_inputs=("bold_4d", "motion_parameters"),
        produced_outputs=("residual_bold", "confound_matrix"),
        capability_level="numerically_implemented",
    ),
    NativeFullStageSpec(
        "detrending",
        "Detrending",
        "native_preproc_detrending",
        depends_on=("nuisance_regression",),
        required_inputs=("bold_4d",),
        produced_outputs=("detrended_bold",),
        capability_level="numerically_implemented",
    ),
    NativeFullStageSpec(
        "temporal_filtering",
        "Temporal filtering",
        "native_preproc_temporal_filtering",
        depends_on=("detrending",),
        required_inputs=("bold_4d", "tr"),
        produced_outputs=("filtered_bold",),
        capability_level="numerically_implemented",
    ),
    NativeFullStageSpec(
        "alff",
        "ALFF",
        "native_preproc_alff",
        depends_on=("temporal_filtering",),
        required_inputs=("filtered_bold", "tr"),
        produced_outputs=("alff_map",),
        capability_level="numerically_implemented",
    ),
    NativeFullStageSpec(
        "falff",
        "fALFF",
        "native_preproc_falff",
        depends_on=("temporal_filtering",),
        required_inputs=("filtered_bold", "tr"),
        produced_outputs=("falff_map",),
        capability_level="numerically_implemented",
    ),
    NativeFullStageSpec(
        "reho",
        "ReHo",
        "native_preproc_reho",
        depends_on=("temporal_filtering",),
        required_inputs=("filtered_bold",),
        produced_outputs=("reho_map",),
        capability_level="numerically_implemented",
    ),
    NativeFullStageSpec(
        "atlas_resampling",
        "Atlas resampling",
        "native_preproc_atlas_resampling",
        depends_on=("temporal_filtering",),
        required_inputs=("atlas", "reference_image"),
        produced_outputs=("atlas_resampled",),
        capability_level="numerically_implemented",
    ),
    NativeFullStageSpec(
        "roi_timeseries",
        "ROI time series",
        "native_preproc_roi_timeseries",
        depends_on=("atlas_resampling",),
        required_inputs=("filtered_bold", "atlas_resampled"),
        produced_outputs=("roi_timeseries", "roi_labels"),
        capability_level="numerically_implemented",
    ),
    NativeFullStageSpec(
        "functional_connectivity",
        "Functional connectivity",
        "native_preproc_functional_connectivity",
        depends_on=("roi_timeseries",),
        required_inputs=("roi_timeseries",),
        produced_outputs=("fc_matrix", "fisher_z_matrix"),
        capability_level="numerically_implemented",
    ),
    NativeFullStageSpec(
        "subject_qc",
        "Subject QC",
        "native_preproc_subject_qc",
        depends_on=("functional_connectivity",),
        produced_outputs=("qc_json",),
    ),
    NativeFullStageSpec(
        "group_summary",
        "Group summary",
        "native_preproc_group_summary",
        depends_on=("subject_qc",),
        produced_outputs=("group_summary",),
    ),
    NativeFullStageSpec(
        "validation_report",
        "Validation report",
        "native_preproc_validation_report",
        depends_on=("group_summary",),
        produced_outputs=("validation_report",),
    ),
    NativeFullStageSpec(
        "final_report",
        "Final report",
        "native_preproc_final_report",
        depends_on=("validation_report",),
        produced_outputs=("final_report",),
    ),
)


def iter_native_full_stage_specs() -> tuple[NativeFullStageSpec, ...]:
    return NATIVE_FULL_STAGE_GRAPH


def native_full_stage_graph_payload() -> list[dict[str, object]]:
    return [
        {
            "stage_id": spec.stage_id,
            "display_name": spec.display_name,
            "node_id": spec.node_id,
            "depends_on": list(spec.depends_on),
            "required_inputs": list(spec.required_inputs),
            "produced_outputs": list(spec.produced_outputs),
            "capability_level": spec.capability_level,
            "enabled_by_default": spec.enabled_by_default,
            "notes": list(spec.notes),
        }
        for spec in NATIVE_FULL_STAGE_GRAPH
    ]


__all__ = [
    "NATIVE_FULL_STAGE_GRAPH",
    "NativeFullStageSpec",
    "iter_native_full_stage_specs",
    "native_full_stage_graph_payload",
]
