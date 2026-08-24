from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.backend.app.native_preproc.orchestrator.stage_graph import iter_native_full_stage_specs
from src.backend.app.runtime import node_registry
from src.backend.app.runtime.node_registry_plugins.base import merge_registries
from src.backend.app.runtime.node_registry_plugins import rsfmri_nodes
from src.backend.app.runtime.tool_catalog import build_tool_catalog

EXPECTED_NATIVE_NODE_IDS = {
    "native_preproc_full_dry_run",
    "native_preproc_full_execute",
} | {spec.node_id for spec in iter_native_full_stage_specs()}


EXPECTED_NODE_IDS = {
    "alff_falff_gpu_candidate_contract",
    "alff_falff_qc_dataset_report",
    "alff_falff_subject",
    "contract_smoke",
    "create_synthetic_bids",
    "data_inspection",
    "dataset_evaluation",
    "docs_inventory",
    "dpabi_alff_falff_contract",
    "dpabi_capability_inspection",
    "dpabi_functional_connectivity_contract",
    "dpabi_input_manifest",
    "dpabi_nuisance_regression_contract",
    "dpabi_preflight",
    "dpabi_reho_contract",
    "dpabi_run_plan",
    "dpabi_sandbox_smoke_run",
    "dpabi_signature_probe",
    "dpabi_single_function_sandbox",
    "dpabi_subject_smooth",
    "dpabi_subject_wrapper_report",
    "dpabi_template_execute",
    "dpabi_template_instantiate",
    "dpabi_template_library",
    "dpabi_temporal_filtering_contract",
    "dpabi_wrapper_contracts",
    "dpabi_wrapper_scaffold",
    "dpabi_wrapper_validation_matrix",
    "environment_check",
    "functional_connectivity_gpu_candidate_contract",
    "functional_connectivity_qc_dataset_report",
    "functional_connectivity_subject",
    "gpu_alff_subject",
    "gpu_functional_connectivity_subject",
    "gpu_nuisance_regression_subject",
    "gpu_reho_subject",
    "gpu_synthetic_smoke",
    "gpu_temporal_filtering_subject",
    "group_dataset_summary",
    "motion_qc_dataset_report",
    "native_dicom_conversion_execute",
    "motion_qc_subject",
    "normalization_qc_dataset_report",
    "nuisance_regression_qc_dataset_report",
    "nuisance_regression_subject",
    "project_release_readiness",
    "registration_qc_dataset_report",
    "reho_gpu_candidate_contract",
    "reho_qc_dataset_report",
    "reho_subject",
    "rsfmri_preprocessing_plan",
    "rsfmri_report_exporter",
    "rsfmri_report_package_validator",
    "slice_timing_qc_dataset_report",
    "smoothing_qc_dataset_report",
    "spm_coregister_subject",
    "spm_normalize_subject",
    "spm_realign_subject",
    "spm_segment_subject",
    "spm_slice_timing_subject",
    "spm_smoke_test",
    "spm_smooth_subject",
    "st_realign_motion_chain_report",
    "subject_qc",
    "temporal_filtering_qc_dataset_report",
    "temporal_filtering_subject",
    "tissue_qc_dataset_report",
} | EXPECTED_NATIVE_NODE_IDS


def test_plugin_registry_preserves_exact_node_ids():
    assert set(node_registry.NODE_REGISTRY) == EXPECTED_NODE_IDS
    assert len(node_registry.NODE_REGISTRY) == 94


def test_get_node_runner_returns_registered_runner_identity():
    for node_id, runner in node_registry.NODE_REGISTRY.items():
        assert node_registry.get_node_runner(node_id) is runner


def test_duplicate_registry_merge_raises_value_error():
    def _runner():
        return {"ok": True}

    with pytest.raises(ValueError, match="Duplicate node id registered: duplicate"):
        merge_registries({"duplicate": _runner}, {"duplicate": _runner})


def test_tool_catalog_covers_plugin_registry_nodes():
    catalog_ids = {item.id for item in build_tool_catalog()}
    assert set(node_registry.NODE_REGISTRY) <= catalog_ids


def test_external_helper_aliases_are_not_exposed_by_runtime_registry():
    assert not hasattr(node_registry, "run_matlab_check")
    assert not hasattr(node_registry, "run_spm_smoke_test")
    assert not hasattr(node_registry, "run_dpabi_capability_inspection")
    assert (
        node_registry.NODE_REGISTRY["spm_realign_subject"].__name__
        == "_external_legacy_node_blocker"
    )


def test_functional_connectivity_node_uses_the_current_subject_bold(monkeypatch, tmp_path):
    captured = {}
    bold_path = tmp_path / "sub-003_task-rest_bold.nii.gz"
    bold_path.write_bytes(b"fixture")

    def fake_runner(**kwargs):
        captured.update(kwargs)
        return {"ok": True}

    monkeypatch.setattr(rsfmri_nodes, "run_functional_connectivity_subject", fake_runner)
    result = rsfmri_nodes.run_functional_connectivity_subject_node(
        SimpleNamespace(derivatives_dir="derivatives"),
        SimpleNamespace(
            id="functional_connectivity_subject",
            params={"atlas_path": "atlas.nii.gz"},
        ),
        {
            "subject_id": "sub-003",
            "sessions": [
                {
                    "func": [
                        {
                            "bold": str(bold_path),
                            "exists": True,
                        }
                    ]
                }
            ],
        },
        "sub-003",
    )

    assert result["ok"] is True
    assert captured["subject_id"] == "sub-003"
    assert captured["input_nii"].endswith("sub-003_task-rest_bold.nii.gz")


def test_functional_connectivity_node_classifies_missing_registered_bold_as_transient_io():
    with pytest.raises(RuntimeError, match="TRANSIENT_IO"):
        rsfmri_nodes.run_functional_connectivity_subject_node(
            SimpleNamespace(derivatives_dir="derivatives"),
            SimpleNamespace(id="functional_connectivity_subject", params={}),
            {
                "subject_id": "sub-003",
                "sessions": [{"func": [{"bold": "missing-bold.nii.gz", "exists": True}]}],
            },
            "sub-003",
        )
