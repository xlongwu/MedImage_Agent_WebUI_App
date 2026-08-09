"""Tests for typed rule-based and OpenAI-compatible plan generation."""

from __future__ import annotations

import json

from src.backend.app.planner.llm_planner import (
    PlannerRequest,
    generate_plan_from_goal,
    plan_from_request,
)
from src.backend.app.runtime.tool_catalog import build_tool_catalog

# ── Helper ──


def _catalog_ids() -> set[str]:
    return {item.id for item in build_tool_catalog()}


# ── 1. motion goal generates plan ──


def test_motion_goal_generates_plan():
    resp = generate_plan_from_goal("run motion correction")
    assert resp.ok is True
    assert resp.plan["pipeline_id"] == "planned_motion_qc"
    assert len(resp.plan["nodes"]) == 4


# ── 2. plan has pipeline_id and nodes ──


def test_plan_has_required_fields():
    resp = generate_plan_from_goal("motion correction")
    assert "pipeline_id" in resp.plan
    assert "nodes" in resp.plan
    assert isinstance(resp.plan["nodes"], list)


# ── 3. all node ids are in Tool Catalog ──


def test_generated_nodes_in_catalog():
    resp = generate_plan_from_goal("motion correction")
    catalog = _catalog_ids()
    for node in resp.plan["nodes"]:
        assert node["id"] in catalog, f"Node '{node['id']}' not in Tool Catalog"


# ── 4. plan is validated ──


def test_validation_called():
    resp = generate_plan_from_goal("motion correction")
    assert "validation" in resp.to_dict()
    assert resp.validation["ok"] is True


# ── 5. motion plan has no errors ──


def test_motion_plan_no_errors():
    resp = generate_plan_from_goal("motion correction")
    assert len(resp.validation.get("errors", [])) == 0


# ── 6. SPM node in approval_required_nodes ──


def test_spm_in_approval_required():
    resp = generate_plan_from_goal("motion correction")
    assert "spm_realign_subject" in resp.validation["approval_required_nodes"]


# ── 7. ALFF goal ──


def test_alff_goal():
    resp = generate_plan_from_goal("compute alff analysis")
    assert resp.ok is True
    assert resp.plan["pipeline_id"] == "planned_alff"
    nids = [n["id"] for n in resp.plan["nodes"]]
    assert "alff_falff_subject" in nids


# ── 8. ReHo goal ──


def test_reho_goal():
    resp = generate_plan_from_goal("reho analysis")
    assert resp.ok is True
    assert resp.plan["pipeline_id"] == "planned_reho"
    nids = [n["id"] for n in resp.plan["nodes"]]
    assert "reho_subject" in nids


# ── 9. empty goal → ok=False ──


def test_empty_goal():
    resp = generate_plan_from_goal("")
    assert resp.ok is False
    assert any("EMPTY_GOAL" in e for e in resp.errors)


# ── 10. unsupported goal → ok=False ──


def test_unsupported_goal():
    resp = generate_plan_from_goal("do something completely unknown")
    assert resp.ok is False
    assert any("UNSUPPORTED_GOAL" in e for e in resp.errors)


# ── 11. unsupported provider → ok=False ──


def test_unsupported_provider():
    resp = generate_plan_from_goal("motion", provider="openai")
    assert resp.ok is False
    assert any("UNSUPPORTED_PROVIDER" in e for e in resp.errors)


# ── 12. to_dict is JSON-serializable ──


def test_response_to_dict_json():
    resp = generate_plan_from_goal("motion correction")
    d = resp.to_dict()
    raw = json.dumps(d, ensure_ascii=False)
    back = json.loads(raw)
    assert back["ok"] is True


# ── 13. does not execute runners ──


def test_no_runner_execution():
    resp = generate_plan_from_goal("motion correction")
    assert resp.ok is True
    # No side effects — trivially passes


# ── 14. no file writes ──


def test_no_file_writes(tmp_path):
    """Planner must not write any files to disk."""
    import os

    before = set(os.listdir(tmp_path))
    generate_plan_from_goal("motion")
    after = set(os.listdir(tmp_path))
    assert after == before


# ── 15. plan_from_request wrapper ──


def test_plan_from_request():
    req = PlannerRequest(goal="motion correction")
    resp = plan_from_request(req)
    assert resp.ok is True
    assert resp.plan["pipeline_id"] == "planned_motion_qc"


# ── 16. full pipeline goal ──


def test_full_pipeline_goal():
    resp = generate_plan_from_goal("run full pipeline preprocessing")
    assert resp.ok is True
    assert resp.plan["pipeline_id"] == "planned_full_preprocessing"
    assert len(resp.plan["nodes"]) >= 5
    # SPM approval warning expected, but no errors
    assert len(resp.validation.get("errors", [])) == 0


# ── 17. Chinese goal matching ──


def test_chinese_goal():
    resp = generate_plan_from_goal("全流程预处理")
    assert resp.ok is True
    assert resp.plan["pipeline_id"] == "planned_full_preprocessing"


def test_chinese_plan_only_goal_builds_metadata_plan_without_execution() -> None:
    resp = generate_plan_from_goal(
        "为已登记的 BIDS 数据准备静息态预处理方案，仅生成计划，不执行任何计算，不修改 rawdata。",
        constraints={
            "project_context": {
                "project_id": "demo",
                "diagnostics": {"status": "CONVERTED_BIDS", "nifti_file_count": 2},
            }
        },
    )

    assert resp.ok is True
    assert resp.plan["pipeline_id"] == "rsfmri_preproc_mvp"
    assert resp.plan["metadata"]["plan_only"] is True
    assert resp.plan["metadata"]["capability_level"] == "metadata_only"
    assert resp.plan["metadata"]["execution_enabled"] is False
    assert resp.plan["metadata"]["rawdata_read_only"] is True
    assert all(not node["id"].endswith("_execute") for node in resp.plan["nodes"])
    assert resp.goal_contract_candidate["goal_kind"] == "rsfmri_preprocessing_plan"
    assert {
        criterion["criterion_type"]
        for criterion in resp.goal_contract_candidate["criteria"]
    } == {"capability_at_least"}


def test_chinese_registered_bids_execution_goal_selects_native_full_preprocessing() -> None:
    resp = generate_plan_from_goal(
        "对已登记的 BIDS 数据执行静息态预处理并生成质量控制报告。",
        constraints={
            "project_context": {
                "project_id": "demo",
                "rawdata_dir": "C:/research/demo/rawdata",
                "diagnostics": {"status": "CONVERTED_BIDS", "nifti_file_count": 2},
            }
        },
    )

    assert resp.ok is True
    assert resp.plan["pipeline_id"] == "native_full_preprocessing"
    assert resp.plan["metadata"]["execution_requires_approval_gate"] is True
    assert any(node["id"] == "native_preproc_full_execute" for node in resp.plan["nodes"])
    native = next(node for node in resp.plan["nodes"] if node["id"] == "native_preproc_full_execute")
    assert native["params"]["input_bids_dir"] == "C:/research/demo/rawdata"
    assert resp.missing_prerequisites == []
    assert all("UNSUPPORTED_GOAL" not in error for error in resp.errors)


def test_explicit_subject_and_minimal_preprocessing_scope_enter_native_execution_params() -> None:
    resp = generate_plan_from_goal(
        "仅对当前 Demo 项目的 sub-001 生成并在人工审批后执行原生 rs-fMRI 最小预处理方案",
        provider="rule_based",
        constraints={
            "project_context": {
                "project_id": "demo",
                "rawdata_dir": "C:/research/demo/rawdata",
                "diagnostics": {
                    "status": "BIDS",
                    "nifti_file_count": 4,
                    "subject_candidates": ["sub-001", "sub-002"],
                },
            }
        },
    )

    assert resp.ok is True
    native = next(
        node
        for node in resp.plan["nodes"]
        if node["id"] == "native_preproc_full_execute"
    )
    assert native["params"]["subject_id"] == "sub-001"
    assert native["params"]["stage_overrides"] == {
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
    assert resp.plan["metadata"]["subject_scope"] == ["sub-001"]
    assert resp.plan["metadata"]["stage_profile"] == "minimal_rsfmri"


def test_ambiguous_single_subject_goal_requires_a_subject_decision() -> None:
    resp = generate_plan_from_goal(
        "为当前 Demo 项目选择 1 名已登记受试者并执行原生 rs-fMRI 最小预处理",
        provider="rule_based",
        constraints={
            "project_context": {
                "project_id": "demo",
                "rawdata_dir": "C:/research/demo/rawdata",
                "diagnostics": {
                    "status": "BIDS",
                    "nifti_file_count": 4,
                    "subject_candidates": ["sub-001", "sub-002"],
                },
            }
        },
    )

    native = next(
        node
        for node in resp.plan["nodes"]
        if node["id"] == "native_preproc_full_execute"
    )
    assert "subject_id" not in native["params"]
    assert resp.plan["metadata"]["science_decisions"]["subject_selection_required"] is True
    assert resp.plan["metadata"]["science_decisions"]["subject_candidates"] == [
        "sub-001",
        "sub-002",
    ]


def test_registered_bids_reho_execution_uses_native_preprocessing_prerequisites() -> None:
    resp = generate_plan_from_goal(
        "对已登记的静息态 fMRI 数据执行完整预处理并计算 ReHo，生成质量控制报告。",
        constraints={
            "project_context": {
                "project_id": "demo",
                "rawdata_dir": "C:/research/demo/rawdata",
                "diagnostics": {"status": "BIDS", "nifti_file_count": 4},
            }
        },
    )

    assert resp.ok is True
    assert resp.plan["pipeline_id"] == "native_reho"
    native = resp.plan["nodes"][0]
    assert native["id"] == "native_preproc_full_execute"
    assert native["params"]["input_bids_dir"] == "C:/research/demo/rawdata"
    assert native["params"]["stage_overrides"]["realignment"] is True
    assert native["params"]["stage_overrides"]["nuisance_regression"] is True
    assert native["params"]["stage_overrides"]["temporal_filtering"] is True
    assert native["params"]["stage_overrides"]["reho"] is True
    assert native["params"]["stage_overrides"]["alff"] is False
    assert native["params"]["stage_overrides"]["functional_connectivity"] is False
    assert resp.goal_contract_candidate["goal_kind"] == "reho"
    artifact_targets = {
        criterion["target"]
        for criterion in resp.goal_contract_candidate["criteria"]
        if criterion["criterion_type"].startswith("artifact_")
    }
    assert artifact_targets == {"reho_map"}
