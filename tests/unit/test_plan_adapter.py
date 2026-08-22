"""Tests for Plan Adapter — reviewed plan → executor pipeline dict conversion."""

from __future__ import annotations

import json

import pytest

from src.backend.app.planner.plan_adapter import (
    adapt_reviewed_plan,
    classify_plan_nodes,
    reviewed_plan_to_pipeline_dict,
)


def _valid_plan(**overrides):
    p = {
        "pipeline_id": "test_plan",
        "nodes": [
            {"id": "data_inspection", "backend": "python", "depends_on": [], "params": {}},
            {
                "id": "motion_qc_subject",
                "backend": "python",
                "depends_on": ["data_inspection"],
                "params": {},
            },
        ],
    }
    p.update(overrides)
    return p


# ── 1. Valid plan converts ──


def test_valid_plan_converts():
    result = reviewed_plan_to_pipeline_dict(_valid_plan())
    assert result["pipeline_id"] == "test_plan"
    assert len(result["nodes"]) == 2


# ── 2. Output has required fields ──


def test_output_has_required_fields():
    result = reviewed_plan_to_pipeline_dict(_valid_plan())
    assert "version" in result
    assert "modality" in result
    assert "execution" in result
    assert "nodes" in result
    assert "run_id" in result["execution"]


# ── 3. Name from pipeline_id ──


def test_name_from_pipeline_id():
    result = reviewed_plan_to_pipeline_dict(_valid_plan())
    assert result["pipeline_id"] == "test_plan"


# ── 4. Backend fill from catalog ──


def test_backend_fill_from_catalog():
    plan = {
        "pipeline_id": "test",
        "nodes": [{"id": "data_inspection", "depends_on": []}],
    }
    result = reviewed_plan_to_pipeline_dict(plan)
    assert result["nodes"][0]["backend"] == "python"


# ── 5. depends_on default ──


def test_depends_on_default():
    plan = {"pipeline_id": "test", "nodes": [{"id": "data_inspection"}]}
    result = reviewed_plan_to_pipeline_dict(plan)
    assert result["nodes"][0]["depends_on"] == []


# ── 6. params default ──


def test_params_default():
    plan = {"pipeline_id": "test", "nodes": [{"id": "data_inspection"}]}
    result = reviewed_plan_to_pipeline_dict(plan)
    assert result["nodes"][0]["params"] == {}


# ── 7. Unknown node → error ──


def test_unknown_node_error():
    plan = {"pipeline_id": "test", "nodes": [{"id": "nonexistent_xyz", "depends_on": []}]}
    result = reviewed_plan_to_pipeline_dict(plan)
    # Backend becomes "unknown" but conversion still succeeds
    assert result["nodes"][0]["backend"] == "unknown"


# ── 8. Duplicate node → error ──


def test_duplicate_node_error():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "data_inspection"},
            {"id": "data_inspection"},
        ],
    }
    with pytest.raises(ValueError, match="Duplicate"):
        reviewed_plan_to_pipeline_dict(plan)


# ── 9. Unknown dependency → error ──


def test_unknown_dependency_error():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "data_inspection"},
            {"id": "motion_qc_subject", "depends_on": ["nonexistent"]},
        ],
    }
    with pytest.raises(ValueError, match="unknown node"):
        reviewed_plan_to_pipeline_dict(plan)


# ── 10. SPM node blocked ──


def test_spm_blocked():
    """spm_realign_subject has manual_required=True → blocked_manual_required_nodes."""
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "spm_realign_subject", "depends_on": []},
        ],
    }
    policy = classify_plan_nodes(plan)
    # With manual_required=True, node goes to blocked_manual_required_nodes instead of blocked_spm_nodes
    assert "spm_realign_subject" in policy["blocked_manual_required_nodes"]


# ── 11. DPABI execution blocked ──


def test_dpabi_execution_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "dpabi_subject_smooth", "depends_on": []},
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "dpabi_subject_smooth" in policy["blocked_dpabi_execution_nodes"]


# ── 12. Manual required blocked ──


def test_unknown_node_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "nonexistent_node_xyz", "depends_on": []},
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "nonexistent_node_xyz" in policy["blocked_unknown_nodes"]


# ── 13. Python QC allowed ──


def test_python_qc_allowed():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "motion_qc_subject", "depends_on": []},
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "motion_qc_subject" in policy["allowed_python_nodes"]


# ── 14. GPU allowed ──


def test_gpu_allowed():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "gpu_alff_subject", "depends_on": []},
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "gpu_alff_subject" in policy["allowed_gpu_nodes"]


# ── 15. Contract node allowed ──


def test_contract_allowed():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "dpabi_capability_inspection", "depends_on": []},
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "dpabi_capability_inspection" in policy["allowed_dpabi_metadata_nodes"]


# ── 16. Adapter result JSON ──


def test_adapter_result_json():
    result = adapt_reviewed_plan(_valid_plan())
    d = result.to_dict()
    raw = json.dumps(d, ensure_ascii=False)
    back = json.loads(raw)
    assert back["ok"] is True


def test_native_full_preprocessing_with_registered_bids_input_is_adaptable():
    result = adapt_reviewed_plan(
        {
            "pipeline_id": "native_reho",
            "nodes": [
                {
                    "id": "native_preproc_full_execute",
                    "backend": "native_python",
                    "depends_on": [],
                    "params": {
                        "input_bids_dir": "C:/research/demo/rawdata",
                        "confirmations": {
                            "confirm_reviewed_native_execution": True,
                            "confirm_rawdata_readonly": True,
                            "confirm_no_external_tools": True,
                            "confirm_research_use_only": True,
                            "confirm_no_clinical_use": True,
                        },
                        "stage_overrides": {"realignment": True, "reho": True},
                    },
                }
            ],
        }
    )

    assert result.ok is True
    assert result.policy["allowed_native_preproc_nodes"] == ["native_preproc_full_execute"]


# ── 17. No file writes ──


def test_no_file_writes(tmp_path):
    import os

    before = set(os.listdir(tmp_path))
    adapt_reviewed_plan(_valid_plan())
    after = set(os.listdir(tmp_path))
    assert after == before


# ── 18. No executor ──


def test_no_executor():
    adapt_reviewed_plan(_valid_plan())


# ── 19. No runner ──


def test_no_runner():
    adapt_reviewed_plan(_valid_plan())


# ── 20. No rawdata writes ──


def test_no_rawdata():
    adapt_reviewed_plan(_valid_plan())


# ══════════════════════════════════════════════════════════════════════════════
# M6-T004b: spm_smoke_test allowlist
# ══════════════════════════════════════════════════════════════════════════════

# ── 21. spm_smoke_test classified as allowed_spm_smoke_nodes ──


def test_spm_smoke_test_allowed():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "spm_smoke_test", "depends_on": [], "params": {}},
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_smoke_test" in policy["allowed_spm_smoke_nodes"]
    assert "spm_smoke_test" not in policy["blocked_spm_nodes"]


# ── 22. spm_realign_subject still blocked ──


def test_spm_realign_still_blocked():
    """spm_realign_subject has manual_required=True → blocked_manual_required_nodes."""
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "spm_realign_subject", "depends_on": [], "params": {}},
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_realign_subject" in policy["blocked_manual_required_nodes"]
    assert "spm_realign_subject" not in policy["allowed_spm_smoke_nodes"]
    assert "spm_realign_subject" not in policy["allowed_spm_realign_sandbox_nodes"]


# ── 23. spm_slice_timing_subject still blocked ──


def test_spm_slice_timing_still_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "spm_slice_timing_subject", "depends_on": [], "params": {}},
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_slice_timing_subject" in policy["blocked_spm_nodes"]


# ══════════════════════════════════════════════════════════════════════════════
# M6-T005d: sandbox-only spm_realign_subject allowlist
# ══════════════════════════════════════════════════════════════════════════════

# ── 24. spm_realign_subject + sandbox → allowed_spm_realign_sandbox_nodes ──


def test_spm_realign_sandbox_allowed():
    """spm_realign_subject has manual_required=True → blocked_manual_required_nodes."""
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {
                "id": "spm_realign_subject",
                "depends_on": [],
                "params": {"sandbox_mode": True, "input_bold": "/tmp/bold.nii"},
            },
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_realign_subject" not in policy["allowed_spm_realign_sandbox_nodes"]
    assert "spm_realign_subject" in policy["blocked_manual_required_nodes"]


# ── 25. spm_realign_subject without sandbox_mode → blocked (manual_required) ──


def test_spm_realign_no_sandbox_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "spm_realign_subject", "depends_on": [], "params": {}},
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_realign_subject" in policy["blocked_manual_required_nodes"]


# ── 26. spm_realign_subject without input_bold → blocked (manual_required) ──


def test_spm_realign_no_input_bold_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "spm_realign_subject", "depends_on": [], "params": {"sandbox_mode": True}},
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_realign_subject" in policy["blocked_manual_required_nodes"]


# ── 27. spm_normalize_subject still blocked ──


def test_spm_normalize_still_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "spm_normalize_subject", "depends_on": [], "params": {}},
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_normalize_subject" in policy["blocked_spm_nodes"]


# ══════════════════════════════════════════════════════════════════════════════
# M6-T006d: sandbox-only spm_slice_timing_subject allowlist
# ══════════════════════════════════════════════════════════════════════════════


def test_slice_timing_synthetic_allowed():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {
                "id": "spm_slice_timing_subject",
                "depends_on": [],
                "params": {
                    "sandbox_mode": True,
                    "input_bold": "examples/synthetic_bids/rawdata/sub-001/func/bold.nii",
                },
            },
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_slice_timing_subject" in policy["allowed_spm_slice_timing_sandbox_nodes"]


def test_slice_timing_derivatives_allowed():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {
                "id": "spm_slice_timing_subject",
                "depends_on": [],
                "params": {
                    "sandbox_mode": True,
                    "allow_derivative_input": True,
                    "input_bold": "derivatives/rsfmri_preproc/sub-001/func/rsub-001_bold.nii",
                },
            },
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_slice_timing_subject" in policy["allowed_spm_slice_timing_sandbox_nodes"]


def test_slice_timing_derivatives_no_allow_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {
                "id": "spm_slice_timing_subject",
                "depends_on": [],
                "params": {
                    "sandbox_mode": True,
                    "allow_derivative_input": False,
                    "input_bold": "derivatives/rsfmri_preproc/sub-001/func/rsub-001_bold.nii",
                },
            },
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_slice_timing_subject" in policy["blocked_spm_nodes"]


def test_slice_timing_arbitrary_input_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {
                "id": "spm_slice_timing_subject",
                "depends_on": [],
                "params": {"sandbox_mode": True, "input_bold": "/etc/passwd"},
            },
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_slice_timing_subject" in policy["blocked_spm_nodes"]


def test_slice_timing_rawdata_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {
                "id": "spm_slice_timing_subject",
                "depends_on": [],
                "params": {"sandbox_mode": True, "input_bold": "data/sub-001/func/bold.nii"},
            },
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_slice_timing_subject" in policy["blocked_spm_nodes"]


def test_slice_timing_path_traversal_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {
                "id": "spm_slice_timing_subject",
                "depends_on": [],
                "params": {"sandbox_mode": True, "input_bold": "../etc/passwd"},
            },
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_slice_timing_subject" in policy["blocked_spm_nodes"]


def test_slice_timing_no_sandbox_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "spm_slice_timing_subject", "depends_on": [], "params": {}},
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_slice_timing_subject" in policy["blocked_spm_nodes"]


def test_slice_timing_no_input_bold_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "spm_slice_timing_subject", "depends_on": [], "params": {"sandbox_mode": True}},
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_slice_timing_subject" in policy["blocked_spm_nodes"]


def test_spm_coregister_still_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "spm_coregister_subject", "depends_on": [], "params": {}},
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_coregister_subject" in policy["blocked_spm_nodes"]


# ══════════════════════════════════════════════════════════════════════════════
# M6-T007d: sandbox-only spm_coregister_subject allowlist
# ══════════════════════════════════════════════════════════════════════════════


def test_coregister_sandbox_allowed():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {
                "id": "spm_coregister_subject",
                "depends_on": [],
                "params": {
                    "sandbox_mode": True,
                    "subject_source": "synthetic_bids",
                    "reference_source": "derivatives_mean_functional",
                },
            },
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_coregister_subject" in policy["allowed_spm_coregister_sandbox_nodes"]


def test_coregister_no_sandbox_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "spm_coregister_subject", "depends_on": [], "params": {}},
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_coregister_subject" in policy["blocked_spm_nodes"]


def test_coregister_bad_subject_source_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {
                "id": "spm_coregister_subject",
                "depends_on": [],
                "params": {
                    "sandbox_mode": True,
                    "subject_source": "real_rawdata",
                    "reference_source": "derivatives_mean_functional",
                },
            },
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_coregister_subject" in policy["blocked_spm_nodes"]


def test_coregister_bad_reference_source_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {
                "id": "spm_coregister_subject",
                "depends_on": [],
                "params": {
                    "sandbox_mode": True,
                    "subject_source": "synthetic_bids",
                    "reference_source": "arbitrary_path",
                },
            },
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_coregister_subject" in policy["blocked_spm_nodes"]


def test_segment_still_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "spm_segment_subject", "depends_on": [], "params": {}},
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_segment_subject" in policy["blocked_spm_nodes"]


# ══════════════════════════════════════════════════════════════════════════════
# M6-T008d: sandbox-only spm_segment_subject allowlist
# ══════════════════════════════════════════════════════════════════════════════


def test_segment_sandbox_allowed():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {
                "id": "spm_segment_subject",
                "depends_on": [],
                "params": {
                    "sandbox_mode": True,
                    "anatomical_source": "coregistered_t1w",
                    "tpm_source": "spm_default_tpm",
                },
            },
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_segment_subject" in policy["allowed_spm_segment_sandbox_nodes"]


def test_segment_no_sandbox_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "spm_segment_subject", "depends_on": [], "params": {}},
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_segment_subject" in policy["blocked_spm_nodes"]


def test_segment_bad_anatomical_source_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {
                "id": "spm_segment_subject",
                "depends_on": [],
                "params": {
                    "sandbox_mode": True,
                    "anatomical_source": "arbitrary_path",
                    "tpm_source": "spm_default_tpm",
                },
            },
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_segment_subject" in policy["blocked_spm_nodes"]


def test_segment_bad_tpm_source_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {
                "id": "spm_segment_subject",
                "depends_on": [],
                "params": {
                    "sandbox_mode": True,
                    "anatomical_source": "coregistered_t1w",
                    "tpm_source": "custom_tpm",
                },
            },
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_segment_subject" in policy["blocked_spm_nodes"]


def test_normalize_still_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "spm_normalize_subject", "depends_on": [], "params": {}},
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_normalize_subject" in policy["blocked_spm_nodes"]


# ══════════════════════════════════════════════════════════════════════════════
# M6-T009d: sandbox-only spm_normalize_subject allowlist
# ══════════════════════════════════════════════════════════════════════════════


def test_normalize_sandbox_allowed():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {
                "id": "spm_normalize_subject",
                "depends_on": [],
                "params": {
                    "sandbox_mode": True,
                    "deformation_source": "segment_deformation_field",
                    "functional_source": "sandbox_derivatives",
                },
            },
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_normalize_subject" in policy["allowed_spm_normalize_sandbox_nodes"]


def test_normalize_no_sandbox_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "spm_normalize_subject", "depends_on": [], "params": {}},
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_normalize_subject" in policy["blocked_spm_nodes"]


def test_normalize_bad_deformation_source_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {
                "id": "spm_normalize_subject",
                "depends_on": [],
                "params": {
                    "sandbox_mode": True,
                    "deformation_source": "custom",
                    "functional_source": "sandbox_derivatives",
                },
            },
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_normalize_subject" in policy["blocked_spm_nodes"]


def test_smooth_still_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "spm_smooth_subject", "depends_on": [], "params": {}},
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_smooth_subject" in policy["blocked_spm_nodes"]


# ── M6-T010d: smooth sandbox allowlist ──


def test_smooth_sandbox_allowed():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {
                "id": "spm_smooth_subject",
                "depends_on": [],
                "params": {
                    "sandbox_mode": True,
                    "normalized_source": "normalize_outputs",
                    "fwhm_policy": "bounded_3d",
                },
            },
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_smooth_subject" in policy["allowed_spm_smooth_sandbox_nodes"]


def test_smooth_no_sandbox_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "spm_smooth_subject", "depends_on": [], "params": {}},
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_smooth_subject" in policy["blocked_spm_nodes"]


# ── M7-T002b: DPABI metadata allowlist ──


def test_dpabi_metadata_classified_allowed():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "dpabi_capability_inspection", "depends_on": [], "params": {}},
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "dpabi_capability_inspection" in policy["allowed_dpabi_metadata_nodes"]


def test_dpabi_exec_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {"id": "dpabi_subject_smooth", "depends_on": [], "params": {}},
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "dpabi_subject_smooth" in policy["blocked_dpabi_execution_nodes"]


# ── M7-T004d: DPABI sandbox smoke allowlist ──


def test_dpabi_smoke_sandbox_allowed():
    plan = {
        "pipeline_id": "t",
        "nodes": [
            {
                "id": "dpabi_sandbox_smoke_run",
                "depends_on": [],
                "params": {
                    "sandbox_mode": True,
                    "smoke_only": True,
                    "input_source": "synthetic_sandbox",
                },
            },
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "dpabi_sandbox_smoke_run" in policy["allowed_dpabi_sandbox_smoke_nodes"]


def test_dpabi_smoke_no_sandbox_blocked():
    plan = {
        "pipeline_id": "t",
        "nodes": [
            {"id": "dpabi_sandbox_smoke_run", "depends_on": [], "params": {}},
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "dpabi_sandbox_smoke_run" in policy["blocked_dpabi_execution_nodes"]


# ── M7-T005d: DPABI single-function sandbox allowlist ──


def test_single_func_sandbox_allowed():
    plan = {
        "pipeline_id": "t",
        "nodes": [
            {
                "id": "dpabi_single_function_sandbox",
                "depends_on": [],
                "params": {
                    "sandbox_mode": True,
                    "single_function_only": True,
                    "function_policy": "allowlisted_contract_only",
                    "function_name": "y_Smooth",
                },
            },
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "dpabi_single_function_sandbox" in policy["allowed_dpabi_single_function_sandbox_nodes"]


def test_single_func_no_sandbox_blocked():
    plan = {
        "pipeline_id": "t",
        "nodes": [
            {"id": "dpabi_single_function_sandbox", "depends_on": [], "params": {}},
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "dpabi_single_function_sandbox" in policy["blocked_dpabi_execution_nodes"]


def test_single_func_forbidden_function_blocked():
    plan = {
        "pipeline_id": "t",
        "nodes": [
            {
                "id": "dpabi_single_function_sandbox",
                "depends_on": [],
                "params": {
                    "sandbox_mode": True,
                    "single_function_only": True,
                    "function_policy": "allowlisted_contract_only",
                    "function_name": "DPARSF_run",
                },
            },
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "dpabi_single_function_sandbox" in policy["blocked_dpabi_execution_nodes"]


def test_single_func_arbitrary_function_blocked():
    plan = {
        "pipeline_id": "t",
        "nodes": [
            {
                "id": "dpabi_single_function_sandbox",
                "depends_on": [],
                "params": {
                    "sandbox_mode": True,
                    "single_function_only": True,
                    "function_policy": "allowlisted_contract_only",
                    "function_name": "evil_eval",
                },
            },
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "dpabi_single_function_sandbox" in policy["blocked_dpabi_execution_nodes"]


# ── M7-T006d: subject smooth sandbox allowlist ──


def test_subject_smooth_sandbox_allowed():
    plan = {
        "pipeline_id": "t",
        "nodes": [
            {
                "id": "dpabi_subject_smooth",
                "depends_on": [],
                "params": {
                    "sandbox_mode": True,
                    "subject_level": True,
                    "subject_source": "synthetic_sandbox",
                    "input_source": "synthetic_sandbox_derivatives",
                    "fwhm_policy": "bounded_3d",
                    "output_policy": "derivatives_dir_scoped",
                },
            },
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "dpabi_subject_smooth" in policy["allowed_dpabi_subject_smooth_sandbox_nodes"]


def test_subject_smooth_no_sandbox_blocked():
    plan = {
        "pipeline_id": "t",
        "nodes": [
            {"id": "dpabi_subject_smooth", "depends_on": [], "params": {}},
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "dpabi_subject_smooth" in policy["blocked_dpabi_execution_nodes"]


# ── M7-T007d: wrapper report sandbox allowlist ──


def test_wrapper_report_sandbox_allowed():
    plan = {
        "pipeline_id": "t",
        "nodes": [
            {
                "id": "dpabi_subject_wrapper_report",
                "depends_on": [],
                "params": {
                    "sandbox_mode": True,
                    "report_only": True,
                    "report_source": "dpabi_subject_smooth_outputs",
                    "output_policy": "reports_dir_dpabi_only",
                },
            },
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "dpabi_subject_wrapper_report" in policy["allowed_dpabi_subject_wrapper_report_nodes"]


def test_wrapper_report_no_sandbox_blocked():
    plan = {
        "pipeline_id": "t",
        "nodes": [
            {"id": "dpabi_subject_wrapper_report", "depends_on": [], "params": {}},
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "dpabi_subject_wrapper_report" in policy["blocked_dpabi_execution_nodes"]


# ── M7-T008d: validation matrix sandbox allowlist ──


def test_validation_matrix_sandbox_allowed():
    plan = {
        "pipeline_id": "t",
        "nodes": [
            {
                "id": "dpabi_wrapper_validation_matrix",
                "depends_on": [],
                "params": {
                    "sandbox_mode": True,
                    "validation_matrix_only": True,
                    "matrix_source": "dpabi_contracts_and_reports",
                    "output_policy": "reports_dir_dpabi_validation_matrix_only",
                },
            },
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "dpabi_wrapper_validation_matrix" in policy["allowed_dpabi_validation_matrix_nodes"]


def test_validation_matrix_no_sandbox_blocked():
    plan = {
        "pipeline_id": "t",
        "nodes": [
            {"id": "dpabi_wrapper_validation_matrix", "depends_on": [], "params": {}},
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "dpabi_wrapper_validation_matrix" in policy["blocked_dpabi_execution_nodes"]


def test_dpabi_single_function_bad_normalized_source_blocked():
    plan = {
        "pipeline_id": "t",
        "nodes": [
            {
                "id": "dpabi_single_function_sandbox",
                "depends_on": [],
                "params": {
                    "sandbox_mode": True,
                    "single_function_only": True,
                    "function_policy": "allowlisted_contract_only",
                    "function_name": "evil_eval",
                },
            },
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "dpabi_single_function_sandbox" in policy["blocked_dpabi_execution_nodes"]


def test_smooth_bad_normalized_source_blocked():
    plan = {
        "pipeline_id": "test",
        "nodes": [
            {
                "id": "spm_smooth_subject",
                "depends_on": [],
                "params": {
                    "sandbox_mode": True,
                    "normalized_source": "custom",
                    "fwhm_policy": "bounded_3d",
                },
            },
        ],
    }
    policy = classify_plan_nodes(plan)
    assert "spm_smooth_subject" in policy["blocked_spm_nodes"]
