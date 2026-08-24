from __future__ import annotations

from src.backend.app.planner.llm_planner import generate_plan_from_goal
import pytest


def test_converted_bids_context_uses_native_full_preprocessing() -> None:
    resp = generate_plan_from_goal(
        (
            "rs-fMRI preprocessing with slice timing, realignment, motion QC, "
            "nuisance regression, detrending, temporal filtering, ROI time series, "
            "and functional connectivity"
        ),
        constraints={
            "project_context": {
                "project_id": "demodata-5",
                "project_dir": "work/projects/demodata-5",
                "diagnostics": {
                    "status": "CONVERTED_BIDS",
                    "nifti_file_count": 6,
                    "preprocessing_conversion_run_id": "conv-001",
                    "registered_atlas_resources": [
                        {
                            "name": "Registered test atlas",
                            "path": "work/projects/demodata-5/resources/atlases/test.nii.gz",
                            "license": "CC0-1.0",
                            "checksum": "sha256:test",
                        }
                    ],
                },
            }
        },
    )

    assert resp.ok is True
    assert resp.plan["pipeline_id"] == "native_functional_connectivity"
    node_ids = [node["id"] for node in resp.plan["nodes"]]
    assert node_ids == ["native_preproc_full_execute"]
    assert "spm_realign_subject" not in node_ids
    params = resp.plan["nodes"][0]["params"]
    assert params["project_id"] == "demodata-5"
    assert params["conversion_run_id"] == "conv-001"
    assert resp.plan["metadata"]["capability_level"] == "computed"
    assert resp.validation["approval_required_nodes"] == ["native_preproc_full_execute"]
    assert resp.validation["high_risk_nodes"] == []


@pytest.mark.parametrize(
    "goal",
    ["Generate FC", "Compute functional connectivity", "生成 FC"],
)
def test_short_fc_goals_use_native_reviewed_chain(goal: str) -> None:
    resp = generate_plan_from_goal(
        goal,
        constraints={
            "project_context": {
                "project_id": "fc-project",
                "project_dir": "work/projects/fc-project",
                "diagnostics": {
                    "status": "BIDS",
                    "nifti_file_count": 2,
                    "preprocessing_input_dir": "work/projects/fc-project/rawdata",
                    "registered_atlas_resources": [
                        {
                            "name": "Schaefer 200",
                            "path": "work/projects/fc-project/resources/atlases/schaefer200.nii.gz",
                            "license": "MIT",
                            "checksum": "sha256:test",
                        }
                    ],
                },
            }
        },
    )

    assert resp.ok is True
    assert resp.plan["pipeline_id"] == "native_functional_connectivity"
    node = resp.plan["nodes"][0]
    assert node["id"] == "native_preproc_full_execute"
    assert node["params"]["stage_overrides"]["functional_connectivity"] is True
    assert node["params"]["cpu_policy"] == {"mode": "auto"}
    assert node["params"]["compute_policy"]["backend"] == "auto"
    assert resp.plan["metadata"]["science_decisions"]["atlas_required"] is False
    assert node["params"]["atlas"].endswith("schaefer200.nii.gz")


def test_fc_goal_stops_before_plan_without_registered_atlas() -> None:
    resp = generate_plan_from_goal(
        "Generate FC",
        constraints={
            "project_context": {
                "project_id": "fc-project",
                "project_dir": "work/projects/fc-project",
                "diagnostics": {
                    "status": "BIDS",
                    "nifti_file_count": 2,
                    "preprocessing_input_dir": "work/projects/fc-project/rawdata",
                },
            }
        },
    )

    assert resp.ok is False
    assert resp.plan == {}
    assert resp.clarification_required is True
    assert resp.errors[0].startswith("REGISTERED_ATLAS_REQUIRED")
    assert "Arbitrary atlas paths" in resp.warnings[0]


@pytest.mark.parametrize("goal", ["Generate FC", "生成 FC"])
def test_fc_goal_without_registered_input_requests_input_instead_of_goal_revision(
    goal: str,
) -> None:
    resp = generate_plan_from_goal(
        goal,
        constraints={
            "project_context": {
                "project_id": "empty-project",
                "project_dir": "work/projects/empty-project",
                "diagnostics": {"status": "EMPTY"},
            }
        },
    )

    assert resp.ok is False
    assert resp.plan == {}
    assert resp.clarification_required is True
    assert resp.errors[0].startswith("REGISTERED_FUNCTIONAL_INPUT_REQUIRED")
    assert all("UNSUPPORTED_GOAL" not in error for error in resp.errors)


def test_prepared_dicom_fc_goal_keeps_fc_stage_contract_and_registered_atlas() -> None:
    resp = generate_plan_from_goal(
        "Convert registered DICOM to NIfTI, run rs-fMRI preprocessing, and generate FC",
        constraints={
            "project_context": {
                "project_id": "dicom-fc-project",
                "project_dir": "work/projects/dicom-fc-project",
                "rawdata_dir": "data/DemoData",
                "diagnostics": {
                    "status": "RAW_DICOM",
                    "dicom_file_count": 36,
                    "agent_conversion_execution_ready": True,
                    "agent_conversion_run_id": "conversion-001",
                    "registered_atlas_resources": [
                        {
                            "name": "Registered test atlas",
                            "path": "work/projects/dicom-fc-project/resources/atlases/test.nii.gz",
                            "license": "CC0-1.0",
                            "checksum": "sha256:test",
                        }
                    ],
                },
            }
        },
    )

    assert resp.ok is True
    assert resp.plan["pipeline_id"] == "native_functional_connectivity"
    assert [node["id"] for node in resp.plan["nodes"]] == [
        "native_dicom_conversion_execute",
        "native_preproc_full_execute",
    ]
    node = resp.plan["nodes"][1]
    assert node["params"]["stage_overrides"]["functional_connectivity"] is True
    assert node["params"]["stage_overrides"]["slice_timing"] is False
    assert node["params"]["stage_overrides"]["normalization"] is False
    assert node["params"]["atlas"].endswith("test.nii.gz")
