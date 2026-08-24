from __future__ import annotations

from src.backend.app.planner.llm_planner import (
    _build_native_full_preprocessing_plan,
    generate_plan_from_goal,
)
from src.backend.app.services.approval_summary_service import ApprovalSummaryService


def test_dicom_goal_stage_graph_places_conversion_before_preprocessing(tmp_path) -> None:
    plan = _build_native_full_preprocessing_plan(
        goal="Convert DICOM, preprocess, and compute FC",
        provider="rule_based",
        project_context={
            "project_id": "project-1",
            "rawdata_dir": str(tmp_path / "rawdata"),
            "diagnostics": {
                "project_dir": str(tmp_path),
                "rawdata_dir": str(tmp_path / "rawdata"),
                "agent_conversion_execution_ready": True,
                "agent_conversion_run_id": "conversion-1",
                "converted_bids_dir": str(tmp_path / "converted_bids"),
            },
        },
    )
    nodes = {node["id"]: node for node in plan["nodes"]}
    assert "native_dicom_conversion_execute" in nodes
    assert nodes["native_preproc_full_execute"]["depends_on"] == ["native_dicom_conversion_execute"]
    confirmations = ApprovalSummaryService._confirmations(
        plan=plan,
        node_ids=tuple(nodes),
        backend_ids=tuple(node["backend"] for node in plan["nodes"]),
    )
    assert confirmations["conversion_scope_confirmed"] is True


def test_prepared_conversion_evidence_selects_conversion_dependency() -> None:
    response = generate_plan_from_goal(
        "Run full preprocessing and functional connectivity",
        provider="rule_based",
        constraints={
            "project_context": {
                "project_id": "project-1",
                "rawdata_dir": "C:/project/rawdata",
                "diagnostics": {
                    "project_dir": "C:/project",
                    "rawdata_dir": "C:/project/rawdata",
                    "agent_conversion_execution_ready": True,
                    "agent_conversion_run_id": "conv-reviewed-1",
                    "conversion_run_id": "conv-reviewed-1",
                    "agent_conversion_output_root": "C:/project/converted_bids",
                    "converted_bids_dir": "C:/project/converted_bids",
                    "registered_atlas_resources": [
                        {
                            "name": "Reviewed atlas",
                            "path": "C:/project/resources/atlases/reviewed.nii.gz",
                            "license": "CC-BY-4.0",
                            "checksum": "a" * 64,
                        }
                    ],
                },
            }
        },
    )

    assert response.ok is True
    assert [node["id"] for node in response.plan["nodes"]] == [
        "native_dicom_conversion_execute",
        "native_preproc_full_execute",
    ]
