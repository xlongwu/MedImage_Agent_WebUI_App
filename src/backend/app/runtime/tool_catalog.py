"""Tool Catalog — read-only registry of pipeline node metadata.

Purpose:
  Describe every valid pipeline node so that LLM Planners, Plan Validators,
  and frontend Plan Review Consoles can reason about tools without executing
  them.  The Tool Catalog is NOT a substitute for Node Registry — Node
  Registry maps node_id → runner function; Tool Catalog maps node_id →
  metadata for planning / validation / display.

Safety metadata is derived exclusively from the versioned NodeContract
registry.  TOOL_METADATA supplies presentation-only name, description, and
tags; it cannot override backend, approval, risk, write, input, or output
contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ── Dataclass ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ToolCatalogItem:
    """Metadata for a single pipeline node / tool."""

    id: str
    name: str
    backend: str
    parallel_level: str
    description: str
    requires_approval: bool
    manual_required: bool
    risk_level: str
    inputs: list[str] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


# ── Core metadata (30 nodes) ─────────────────────────────────────────────────

TOOL_METADATA: dict[str, dict[str, Any]] = {
    # ── Data / Env ──
    "create_synthetic_bids": {
        "name": "Create Synthetic BIDS",
        "backend": "python",
        "parallel_level": "project",
        "description": "Generate synthetic BIDS dataset for testing.",
        "requires_approval": False,
        "manual_required": False,
        "risk_level": "low",
        "inputs": [],
        "outputs": ["synthetic_bids/rawdata/"],
        "tags": ["data", "synthetic"],
    },
    "contract_smoke": {
        "name": "Contract Smoke",
        "backend": "python",
        "parallel_level": "project",
        "description": "Minimal executor node contract smoke validator.",
        "requires_approval": False,
        "manual_required": False,
        "risk_level": "low",
        "inputs": [],
        "outputs": ["contract_smoke_report.json", "contract_smoke.log"],
        "tags": ["contract", "test", "validation"],
    },
    "data_inspection": {
        "name": "Data Inspection",
        "backend": "python",
        "parallel_level": "project",
        "description": "Scan rawdata and build dataset index.",
        "requires_approval": False,
        "manual_required": False,
        "risk_level": "low",
        "inputs": ["rawdata_dir"],
        "outputs": ["dataset_index.json"],
        "tags": ["data", "bids"],
    },
    "environment_check": {
        "name": "Environment Check",
        "backend": "python",
        "parallel_level": "project",
        "description": "Verify MATLAB/SPM/DPABI environment.",
        "requires_approval": False,
        "manual_required": False,
        "risk_level": "low",
        "inputs": [],
        "outputs": ["environment_check.json"],
        "tags": ["env", "matlab"],
    },

    # ── SPM preprocessing ──
    "spm_slice_timing_subject": {
        "name": "SPM Slice Timing",
        "backend": "matlab-spm",
        "parallel_level": "subject",
        "description": "Correct slice timing differences in BOLD fMRI.",
        "requires_approval": True,
        "manual_required": False,
        "risk_level": "high",
        "inputs": ["BOLD NIfTI", "TR", "slice_order"],
        "outputs": ["a<subject>_bold.nii"],
        "tags": ["spm", "matlab", "rsfmri", "preprocessing"],
    },
    "spm_realign_subject": {
        "name": "SPM Realign (future — not executable)",
        "backend": "matlab-spm",
        "parallel_level": "subject",
        "description": (
            "Future SPM realignment wrapper.  NOT CURRENTLY EXECUTABLE — "
            "no MATLAB/SPM execution is available in this release.  "
            "Requires MATLAB, SPM, explicit approval gate, persisted audit, "
            "environment checks, and safe-allowlist opt-in before real "
            "execution is enabled.  Currently preparation-only: MedImage "
            "Agent can inspect data, validate parameters, check environment "
            "readiness, generate dry-run output manifests, and preview "
            "MATLAB batch templates, but does not perform realignment, "
            "does not write derivatives, and does not modify rawdata."
        ),
        "requires_approval": True,
        "manual_required": True,
        "risk_level": "high",
        "inputs": [
            "BOLD NIfTI (discovered from project metadata)",
            "BOLD sidecar JSON (optional)",
        ],
        "outputs": [
            "r<subject>_bold.nii (realigned BOLD)",
            "mean<subject>_bold.nii (mean/reference BOLD)",
            "rp_*.txt (motion parameters)",
            "stdout.log / stderr.log",
            "provenance.json",
            "node_state.json",
        ],
        "tags": ["spm", "matlab", "rsfmri", "realign", "motion", "high-risk", "not-executable"],
    },
    "spm_coregister_subject": {
        "name": "SPM Coregister",
        "backend": "matlab-spm",
        "parallel_level": "subject",
        "description": "Coregister functional to structural images.",
        "requires_approval": True,
        "manual_required": False,
        "risk_level": "high",
        "inputs": ["BOLD NIfTI", "T1w NIfTI"],
        "outputs": ["coregistered BOLD"],
        "tags": ["spm", "matlab", "rsfmri", "registration"],
    },
    "spm_segment_subject": {
        "name": "SPM Segment",
        "backend": "matlab-spm",
        "parallel_level": "subject",
        "description": "Segment T1w into GM/WM/CSF tissue maps.",
        "requires_approval": True,
        "manual_required": False,
        "risk_level": "high",
        "inputs": ["T1w NIfTI"],
        "outputs": ["c1/c2/c3 tissue maps"],
        "tags": ["spm", "matlab", "segmentation"],
    },
    "spm_normalize_subject": {
        "name": "SPM Normalize",
        "backend": "matlab-spm",
        "parallel_level": "subject",
        "description": "Spatial normalisation to MNI template.",
        "requires_approval": True,
        "manual_required": False,
        "risk_level": "high",
        "inputs": ["BOLD NIfTI", "deformation fields"],
        "outputs": ["w<subject>_bold.nii"],
        "tags": ["spm", "matlab", "rsfmri", "normalization"],
    },
    "spm_smooth_subject": {
        "name": "SPM Smooth",
        "backend": "matlab-spm",
        "parallel_level": "subject",
        "description": "Gaussian spatial smoothing of BOLD images.",
        "requires_approval": True,
        "manual_required": False,
        "risk_level": "high",
        "inputs": ["BOLD NIfTI", "FWHM"],
        "outputs": ["s<subject>_bold.nii"],
        "tags": ["spm", "matlab", "rsfmri", "smoothing"],
    },
    "spm_smoke_test": {
        "name": "SPM Smoke Test",
        "backend": "matlab-spm",
        "parallel_level": "project",
        "description": "Quick smoke test to verify SPM/MATLAB setup.",
        "requires_approval": True,
        "manual_required": False,
        "risk_level": "medium",
        "inputs": [],
        "outputs": [],
        "tags": ["spm", "matlab", "test"],
    },

    # ── Motion QC ──
    "motion_qc_subject": {
        "name": "Motion QC (per subject)",
        "backend": "python",
        "parallel_level": "subject",
        "description": "Compute FD/DVARS motion metrics.",
        "requires_approval": False,
        "manual_required": False,
        "risk_level": "low",
        "inputs": ["rp_*.txt"],
        "outputs": ["motion_qc.json"],
        "tags": ["qc", "motion", "rsfmri"],
    },
    "motion_qc_dataset_report": {
        "name": "Motion QC Dataset Report",
        "backend": "python",
        "parallel_level": "project",
        "description": "Aggregate motion QC across all subjects.",
        "requires_approval": False,
        "manual_required": False,
        "risk_level": "low",
        "inputs": ["motion_qc per subject"],
        "outputs": ["motion_qc_dataset_report"],
        "tags": ["qc", "motion", "report"],
    },

    # ── QC reports ──
    "slice_timing_qc_dataset_report": {
        "name": "Slice Timing QC Report",
        "backend": "python", "parallel_level": "project",
        "description": "Aggregate slice timing QC.",
        "requires_approval": False, "manual_required": False, "risk_level": "low",
        "inputs": [], "outputs": [],
        "tags": ["qc", "report", "slice_timing"],
    },
    "registration_qc_dataset_report": {
        "name": "Registration QC Report",
        "backend": "python", "parallel_level": "project",
        "description": "Aggregate coregistration QC.",
        "requires_approval": False, "manual_required": False, "risk_level": "low",
        "inputs": [], "outputs": [],
        "tags": ["qc", "report", "registration"],
    },
    "tissue_qc_dataset_report": {
        "name": "Tissue QC Report",
        "backend": "python", "parallel_level": "project",
        "description": "Aggregate segmentation QC.",
        "requires_approval": False, "manual_required": False, "risk_level": "low",
        "inputs": [], "outputs": [],
        "tags": ["qc", "report", "segmentation"],
    },
    "normalization_qc_dataset_report": {
        "name": "Normalization QC Report",
        "backend": "python", "parallel_level": "project",
        "description": "Aggregate normalisation QC.",
        "requires_approval": False, "manual_required": False, "risk_level": "low",
        "inputs": [], "outputs": [],
        "tags": ["qc", "report", "normalization"],
    },
    "smoothing_qc_dataset_report": {
        "name": "Smoothing QC Report",
        "backend": "python", "parallel_level": "project",
        "description": "Aggregate smoothing QC.",
        "requires_approval": False, "manual_required": False, "risk_level": "low",
        "inputs": [], "outputs": [],
        "tags": ["qc", "report", "smoothing"],
    },

    # ── Python post-processing ──
    "nuisance_regression_subject": {
        "name": "Nuisance Regression",
        "backend": "python", "parallel_level": "subject",
        "description": "Remove nuisance signals via linear regression.",
        "requires_approval": False, "manual_required": False, "risk_level": "medium",
        "inputs": ["BOLD NIfTI", "confound matrix"],
        "outputs": ["residual BOLD"],
        "tags": ["rsfmri", "denoising"],
    },
    "temporal_filtering_subject": {
        "name": "Temporal Filtering",
        "backend": "python", "parallel_level": "subject",
        "description": "Band-pass temporal filtering.",
        "requires_approval": False, "manual_required": False, "risk_level": "medium",
        "inputs": ["BOLD NIfTI", "TR", "freq_band"],
        "outputs": ["filtered BOLD"],
        "tags": ["rsfmri", "filtering"],
    },
    "alff_falff_subject": {
        "name": "ALFF/fALFF",
        "backend": "python", "parallel_level": "subject",
        "description": "Amplitude of Low Frequency Fluctuations.",
        "requires_approval": False, "manual_required": False, "risk_level": "medium",
        "inputs": ["BOLD NIfTI", "TR", "freq_band"],
        "outputs": ["ALFF/fALFF maps"],
        "tags": ["rsfmri", "metric", "alff"],
    },
    "reho_subject": {
        "name": "ReHo",
        "backend": "python", "parallel_level": "subject",
        "description": "Regional Homogeneity (Kendall's W).",
        "requires_approval": False, "manual_required": False, "risk_level": "medium",
        "inputs": ["BOLD NIfTI"],
        "outputs": ["ReHo map"],
        "tags": ["rsfmri", "metric", "reho"],
    },
    "functional_connectivity_subject": {
        "name": "Functional Connectivity",
        "backend": "python", "parallel_level": "subject",
        "description": "ROI-based functional connectivity matrix.",
        "requires_approval": False, "manual_required": False, "risk_level": "medium",
        "inputs": ["BOLD NIfTI", "ROI atlas"],
        "outputs": ["FC matrix"],
        "tags": ["rsfmri", "metric", "connectivity"],
    },

    # ── GPU-accelerated ──
    "gpu_alff_subject": {
        "name": "GPU ALFF/fALFF",
        "backend": "gpu", "parallel_level": "subject",
        "description": "GPU-accelerated ALFF/fALFF (CuPy).",
        "requires_approval": False, "manual_required": False, "risk_level": "medium",
        "inputs": ["BOLD NIfTI"],
        "outputs": ["ALFF/fALFF maps"],
        "tags": ["rsfmri", "metric", "alff", "gpu"],
    },
    "gpu_reho_subject": {
        "name": "GPU ReHo",
        "backend": "gpu", "parallel_level": "subject",
        "description": "GPU-accelerated ReHo (CuPy).",
        "requires_approval": False, "manual_required": False, "risk_level": "medium",
        "inputs": ["BOLD NIfTI"],
        "outputs": ["ReHo map"],
        "tags": ["rsfmri", "metric", "reho", "gpu"],
    },
    "gpu_nuisance_regression_subject": {
        "name": "GPU Nuisance Regression",
        "backend": "gpu", "parallel_level": "subject",
        "description": "GPU-accelerated nuisance regression.",
        "requires_approval": False, "manual_required": False, "risk_level": "medium",
        "inputs": ["BOLD NIfTI"],
        "outputs": ["residual BOLD"],
        "tags": ["rsfmri", "gpu", "denoising"],
    },
    "gpu_temporal_filtering_subject": {
        "name": "GPU Temporal Filtering",
        "backend": "gpu", "parallel_level": "subject",
        "description": "GPU-accelerated temporal filtering.",
        "requires_approval": False, "manual_required": False, "risk_level": "medium",
        "inputs": ["BOLD NIfTI"],
        "outputs": ["filtered BOLD"],
        "tags": ["rsfmri", "gpu", "filtering"],
    },
    "gpu_functional_connectivity_subject": {
        "name": "GPU Functional Connectivity",
        "backend": "gpu", "parallel_level": "subject",
        "description": "GPU-accelerated functional connectivity.",
        "requires_approval": False, "manual_required": False, "risk_level": "medium",
        "inputs": ["BOLD NIfTI"],
        "outputs": ["FC matrix"],
        "tags": ["rsfmri", "gpu", "connectivity"],
    },

    # ── QC dataset reports (Python) ──
    "nuisance_regression_qc_dataset_report": {
        "name": "Nuisance Regression QC Report",
        "backend": "python", "parallel_level": "project",
        "description": "Aggregate nuisance regression QC.",
        "requires_approval": False, "manual_required": False, "risk_level": "low",
        "inputs": [], "outputs": [],
        "tags": ["qc", "report"],
    },
    "temporal_filtering_qc_dataset_report": {
        "name": "Temporal Filtering QC Report",
        "backend": "python", "parallel_level": "project",
        "description": "Aggregate temporal filtering QC.",
        "requires_approval": False, "manual_required": False, "risk_level": "low",
        "inputs": [], "outputs": [],
        "tags": ["qc", "report"],
    },
    "alff_falff_qc_dataset_report": {
        "name": "ALFF/fALFF QC Report",
        "backend": "python", "parallel_level": "project",
        "description": "Aggregate ALFF/fALFF QC.",
        "requires_approval": False, "manual_required": False, "risk_level": "low",
        "inputs": [], "outputs": [],
        "tags": ["qc", "report", "alff"],
    },
    "reho_qc_dataset_report": {
        "name": "ReHo QC Report",
        "backend": "python", "parallel_level": "project",
        "description": "Aggregate ReHo QC.",
        "requires_approval": False, "manual_required": False, "risk_level": "low",
        "inputs": [], "outputs": [],
        "tags": ["qc", "report", "reho"],
    },
    "functional_connectivity_qc_dataset_report": {
        "name": "FC QC Report",
        "backend": "python", "parallel_level": "project",
        "description": "Aggregate functional connectivity QC.",
        "requires_approval": False, "manual_required": False, "risk_level": "low",
        "inputs": [], "outputs": [],
        "tags": ["qc", "report", "connectivity"],
    },

    # ── Group / Report / Release ──
    "dataset_evaluation": {
        "name": "Dataset Evaluation",
        "backend": "python", "parallel_level": "project",
        "description": "Evaluate dataset completeness and consistency.",
        "requires_approval": False, "manual_required": False, "risk_level": "low",
        "inputs": ["dataset_index"],
        "outputs": ["dataset_evaluation report"],
        "tags": ["data", "qc", "report"],
    },
    "group_dataset_summary": {
        "name": "Group Dataset Summary",
        "backend": "python", "parallel_level": "project",
        "description": "Cross-subject aggregate summary and dashboard data.",
        "requires_approval": False, "manual_required": False, "risk_level": "low",
        "inputs": ["per-subject QC/metrics"],
        "outputs": ["group_summary.json", "dashboard_data.csv"],
        "tags": ["report", "summary"],
    },
    "rsfmri_report_exporter": {
        "name": "rs-fMRI Report Exporter",
        "backend": "python", "parallel_level": "project",
        "description": "Export ZIP report package with SHA256 checksums.",
        "requires_approval": False, "manual_required": False, "risk_level": "low",
        "inputs": ["reports/"],
        "outputs": ["ZIP package", "SHA256 manifest"],
        "tags": ["report", "export"],
    },
    "rsfmri_report_package_validator": {
        "name": "Report Package Validator",
        "backend": "python", "parallel_level": "project",
        "description": "Validate exported report package integrity and safety.",
        "requires_approval": False, "manual_required": False, "risk_level": "low",
        "inputs": ["ZIP package"],
        "outputs": ["validation_report"],
        "tags": ["report", "validation"],
    },

    # ── Preset contract nodes (rs-fMRI preprocessing MVP) ──
    "data_readiness_check": {
        "name": "Data Readiness Check",
        "backend": "contract", "parallel_level": "project",
        "description": "Validate project data readiness before preprocessing.",
        "requires_approval": False, "manual_required": False, "risk_level": "low",
        "inputs": ["project_config_path"],
        "outputs": ["readiness_summary"],
        "tags": ["contract", "readiness", "rsfmri"],
    },
    "bids_validation_check": {
        "name": "BIDS Validation Check",
        "backend": "contract", "parallel_level": "project",
        "description": "Validate BIDS-like structure of rawdata.",
        "requires_approval": False, "manual_required": False, "risk_level": "low",
        "inputs": ["rawdata_dir"],
        "outputs": ["bids_validation_summary"],
        "tags": ["contract", "bids", "rsfmri"],
    },
    "rsfmri_bold_reference_check": {
        "name": "BOLD Reference Check",
        "backend": "contract", "parallel_level": "project",
        "description": "Verify BOLD NIfTI availability and sidecar metadata.",
        "requires_approval": False, "manual_required": False, "risk_level": "low",
        "inputs": ["dataset_index_path"],
        "outputs": ["bold_reference_summary"],
        "tags": ["contract", "bold", "rsfmri"],
    },
    "rsfmri_motion_qc_plan": {
        "name": "Motion QC Plan",
        "backend": "contract", "parallel_level": "project",
        "description": "Plan motion QC steps (realign parameters, FD, DVARS).",
        "requires_approval": False, "manual_required": False, "risk_level": "low",
        "inputs": ["bold_reference_summary"],
        "outputs": ["motion_qc_plan"],
        "tags": ["contract", "motion", "rsfmri"],
    },
    "rsfmri_preprocessing_plan_stub": {
        "name": "Preprocessing Plan Stub",
        "backend": "contract", "parallel_level": "project",
        "description": "Stub for future SPM slice-timing, realign, normalize, smooth.",
        "requires_approval": False, "manual_required": False, "risk_level": "medium",
        "inputs": ["motion_qc_plan"],
        "outputs": ["preprocessing_plan_stub"],
        "tags": ["contract", "stub", "rsfmri", "preprocessing"],
    },
    "rsfmri_report_plan_stub": {
        "name": "Report Plan Stub",
        "backend": "contract", "parallel_level": "project",
        "description": "Stub for future QC report generation.",
        "requires_approval": False, "manual_required": False, "risk_level": "low",
        "inputs": ["preprocessing_plan_stub"],
        "outputs": ["report_plan_stub"],
        "tags": ["contract", "stub", "report", "rsfmri", "validation"],
    },
    "project_release_readiness": {
        "name": "Project Release Readiness",
        "backend": "python", "parallel_level": "project",
        "description": "Check project release readiness against quality gates.",
        "requires_approval": False, "manual_required": False, "risk_level": "low",
        "inputs": [],
        "outputs": ["release_readiness report"],
        "tags": ["release", "report"],
    },
    "docs_inventory": {
        "name": "Docs Inventory",
        "backend": "python", "parallel_level": "project",
        "description": "Build documentation inventory for the project.",
        "requires_approval": False, "manual_required": False, "risk_level": "low",
        "inputs": [],
        "outputs": ["docs_inventory"],
        "tags": ["docs", "report"],
    },

    # ── Misc ──
    "subject_qc": {
        "name": "Subject QC",
        "backend": "python", "parallel_level": "subject",
        "description": "Per-subject quality control on smoothed output.",
        "requires_approval": False, "manual_required": False, "risk_level": "low",
        "inputs": ["smoothed BOLD"],
        "outputs": ["qc_metrics"],
        "tags": ["qc"],
    },
    "st_realign_motion_chain_report": {
        "name": "Slice Timing + Realign + Motion Chain Report",
        "backend": "python", "parallel_level": "project",
        "description": "End-to-end report for ST → Realign → Motion QC chain.",
        "requires_approval": False, "manual_required": False, "risk_level": "low",
        "inputs": [], "outputs": [],
        "tags": ["report", "motion"],
    },
    "rsfmri_preprocessing_plan": {
        "name": "rs-fMRI Preprocessing Plan",
        "backend": "python", "parallel_level": "project",
        "description": "Generate and write the rs-fMRI preprocessing plan.",
        "requires_approval": False, "manual_required": False, "risk_level": "low",
        "inputs": [], "outputs": [],
        "tags": ["plan", "rsfmri"],
    },
}


TOOL_METADATA.update(
    {
        "native_dicom_conversion_execute": {
            "name": "Native DICOM Conversion Execute",
            "backend": "medimage-native",
            "parallel_level": "project",
            "description": (
                "Execute an already prepared and release-approved native DICOM conversion "
                "through the reviewed ticket and gateway boundary. Rawdata remains read-only."
            ),
            "requires_approval": True,
            "manual_required": False,
            "risk_level": "medium",
            "inputs": ["approved DICOM mapping package", "rawdata checksum snapshot"],
            "outputs": ["converted NIfTI", "BIDS sidecars", "manifest", "provenance"],
            "tags": ["dicom", "conversion", "execute", "requires-approval", "no-external-tools"],
        },
        "native_preproc_full_dry_run": {
            "name": "Native Full Preprocessing Dry Run",
            "backend": "native_python",
            "parallel_level": "project",
            "description": (
                "Plan the Python-native full rs-fMRI preprocessing workflow without "
                "creating numerical artifacts or invoking MATLAB, SPM, or DPABI."
            ),
            "requires_approval": False,
            "manual_required": False,
            "risk_level": "low",
            "inputs": ["BOLD NIfTI", "BIDS sidecar JSON", "optional T1w/template/atlas"],
            "outputs": ["planned native stage graph"],
            "tags": ["native-preproc", "dry-run", "rsfmri", "no-external-tools"],
        },
        "native_preproc_full_execute": {
            "name": "Native Full Preprocessing Execute",
            "backend": "native_python",
            "parallel_level": "project",
            "description": (
                "Execute the reviewed Python-native full preprocessing orchestrator. "
                "The runner writes manifest, provenance, validation, report, and "
                "registered numerical artifacts while keeping rawdata read-only and "
                "external MATLAB/SPM/DPABI disabled."
            ),
            "requires_approval": True,
            "manual_required": False,
            "risk_level": "medium",
            "inputs": [
                "BOLD NIfTI",
                "BIDS sidecar JSON or explicit TR",
                "optional T1w/template/atlas",
                "native safety confirmations",
            ],
            "outputs": [
                "native_full_run_manifest.json",
                "native_preproc_validation_report.json",
                "native_preproc_final_report.json",
                "stage artifacts",
            ],
            "tags": [
                "native-preproc",
                "execute",
                "rsfmri",
                "requires-approval",
                "audit",
                "no-external-tools",
                "rawdata-readonly",
            ],
        },
        "native_auto_acpc_align": {
            "name": "Automatic ACPC alignment",
            "backend": "native_python",
            "parallel_level": "project",
            "description": (
                "Deterministic template-rigid T1w ACPC alignment. It writes estimated, "
                "not manually detected, AC/PC landmarks; any QC failure stops without final artifacts."
            ),
            "requires_approval": True,
            "manual_required": False,
            "risk_level": "medium",
            "inputs": ["registered T1w artifact ID", "approved SPM avg152 T1 template"],
            "outputs": ["ACPC T1w", "rigid transform", "estimated landmarks JSON", "QC JSON"],
            "tags": ["native-preproc", "acpc", "t1w", "requires-approval", "no-external-tools", "estimated-landmarks"],
        },
    }
)


def _install_native_preproc_stage_metadata() -> None:
    from src.backend.app.native_preproc.orchestrator.stage_graph import (  # noqa: E402
        iter_native_full_stage_specs,
    )

    for spec in iter_native_full_stage_specs():
        TOOL_METADATA.setdefault(
            spec.node_id,
            {
                "name": f"Native Preprocessing Stage Boundary: {spec.display_name}",
                "backend": "native_python",
                "parallel_level": "project",
                "description": (
                    f"Reviewed-plan boundary for native stage '{spec.stage_id}'. "
                    "Direct execution is blocked; use native_preproc_full_execute "
                    "so artifacts, provenance, validation, and reports stay coordinated."
                ),
                "requires_approval": False,
                "manual_required": False,
                "risk_level": "low",
                "inputs": list(spec.required_inputs),
                "outputs": list(spec.produced_outputs),
                "tags": [
                    "native-preproc",
                    "stage-boundary",
                    "rsfmri",
                    "coordinated-execution-only",
                ],
            },
        )


_install_native_preproc_stage_metadata()


# ── Public API ────────────────────────────────────────────────────────────────

def build_tool_catalog() -> list[ToolCatalogItem]:
    """Build one read-only presentation item from each authoritative contract."""
    from src.backend.app.runtime.node_contract_registry import NODE_CONTRACTS  # noqa: E402

    items: list[ToolCatalogItem] = []
    for node_id, contract in sorted(NODE_CONTRACTS.items()):
        presentation = TOOL_METADATA.get(node_id, {})
        items.append(
            ToolCatalogItem(
                id=node_id,
                name=str(presentation.get("name") or node_id.replace("_", " ").title()),
                backend=contract.backend,
                parallel_level=contract.parallel_level,
                description=str(
                    presentation.get("description")
                    or f"Versioned NodeContract for '{node_id}'."
                ),
                requires_approval=contract.requires_approval,
                manual_required=contract.manual_required,
                risk_level=contract.risk_level,
                inputs=[item.artifact_type for item in contract.input_schema],
                outputs=[item.artifact_type for item in contract.output_schema],
                tags=list(presentation.get("tags") or ["contract-derived"]),
            )
        )
    return items


def get_tool_catalog_item(node_id: str) -> ToolCatalogItem:
    """Look up one contract-derived catalog item and reject unknown ids."""
    for item in build_tool_catalog():
        if item.id == node_id:
            return item
    raise KeyError(f"Unknown node id: {node_id}")


def catalog_as_dicts() -> list[dict[str, Any]]:
    """Return the full catalog as a list of plain dicts (JSON-serializable)."""
    return [
        {
            "id": item.id,
            "name": item.name,
            "backend": item.backend,
            "parallel_level": item.parallel_level,
            "description": item.description,
            "requires_approval": item.requires_approval,
            "manual_required": item.manual_required,
            "risk_level": item.risk_level,
            "inputs": item.inputs,
            "outputs": item.outputs,
            "tags": item.tags,
        }
        for item in build_tool_catalog()
    ]
